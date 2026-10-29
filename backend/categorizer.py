"""Categorization cascade: rules -> learned merchant memory -> fuzzy memory ->
builtin hints -> nothing (AI runs later in a batch for the remainder)."""
from dataclasses import dataclass, field
from decimal import Decimal

from rapidfuzz import fuzz, process

import builtin_hints
import db
import rules as rules_mod
from util import record_events, drop_splits

FUZZY_CUTOFF = 90  # plain ratio: near-identical keys only, never token subsets (UBER vs UBER EATS)


@dataclass
class Decision:
    category_id: int | None = None
    status: str = "none"            # none|suggested|confirmed
    source: str | None = None       # rule|merchant|builtin|ai|manual
    rule_id: int | None = None
    confidence: float | None = None
    is_transfer: bool = False
    is_excluded: bool = False


@dataclass
class Context:
    user_id: int
    rules: list = field(default_factory=list)
    memory: dict = field(default_factory=dict)       # direction -> merchant_key -> memory row
    slug_to_id: dict = field(default_factory=dict)
    ai_enabled: bool = False
    memory_keys: dict = field(default_factory=dict)  # direction -> merchant keys


def direction(amount):
    """Merchant memory is kept per direction: the same source can pay you and charge you."""
    return "in" if amount is not None and amount > 0 else "out"


def direction_sql(amount_col="amount"):
    return f"(CASE WHEN {amount_col} > 0 THEN 'in' ELSE 'out' END)"


def deactivate_timed_out(compiled_rules):
    """A regex that hit MATCH_TIMEOUT is switched off so it cannot stall the next import."""
    ids = rules_mod.timed_out_ids(compiled_rules)
    if ids:
        db.execute("UPDATE rules SET is_active = FALSE, updated_at = now() WHERE id = ANY(%s)", (ids,))
    return ids


def load_context(user_id):
    rule_rows = db.query(
        "SELECT * FROM rules WHERE user_id = %s AND is_active ORDER BY priority, id", (user_id,)
    ) or []
    mem_rows = db.query(
        "SELECT merchant_key, direction, category_id, is_transfer, display_name FROM merchant_memory WHERE user_id = %s",
        (user_id,),
    ) or []
    cat_rows = db.query("SELECT id, slug FROM categories WHERE user_id = %s", (user_id,)) or []
    try:
        import openrouter
        ai_enabled = bool(openrouter.enabled("categorize", user_id))
    except Exception:
        ai_enabled = False
    memory = {}
    for r in mem_rows:
        memory.setdefault(r["direction"], {})[r["merchant_key"]] = dict(r)
    return Context(
        user_id=user_id,
        rules=[rules_mod.compile_rule(dict(r)) for r in rule_rows],
        memory=memory,
        slug_to_id={r["slug"]: r["id"] for r in cat_rows},
        ai_enabled=ai_enabled,
        memory_keys={d: list(keys) for d, keys in memory.items()},
    )


def _transfer_category(ctx, hint_slug=None):
    for slug in (hint_slug, "transfers.internal", "transfers"):
        if slug and slug in ctx.slug_to_id:
            return ctx.slug_to_id[slug]
    return None


def categorize(txn, ctx):
    rule = rules_mod.first_match(ctx.rules, txn)
    if rule:
        category_id = rule.get("category_id")
        is_transfer = bool(rule.get("set_transfer"))
        if category_id is None and is_transfer:
            category_id = _transfer_category(ctx)
        return Decision(
            category_id=category_id,
            status="confirmed" if category_id is not None else "none",
            source="rule",
            rule_id=rule.get("id"),
            confidence=1.0,
            is_transfer=is_transfer,
            is_excluded=is_transfer or bool(rule.get("set_excluded")),
        )

    key = txn.get("merchant_key") or ""
    way = direction(txn.get("amount"))
    memory = ctx.memory.get(way, {})
    mem = memory.get(key) if key else None
    if mem:
        return Decision(
            category_id=mem["category_id"], status="confirmed", source="merchant", confidence=0.95,
            is_transfer=bool(mem.get("is_transfer")), is_excluded=bool(mem.get("is_transfer")),
        )
    keys = ctx.memory_keys.get(way)
    if key and keys:
        best = process.extractOne(key, keys, scorer=fuzz.ratio, score_cutoff=FUZZY_CUTOFF)
        if best:
            mem = memory[best[0]]
            # a suggestion never moves money out of the totals; confirming it in Review does
            return Decision(category_id=mem["category_id"], status="suggested", source="merchant", confidence=0.8)

    slug = builtin_hints.lookup(key, txn.get("description_clean") or "", txn.get("description_raw") or "")
    if slug:
        category_id = ctx.slug_to_id.get(slug)
        if category_id is None and "." in slug:
            category_id = ctx.slug_to_id.get(slug.split(".")[0])
        if category_id is not None:
            return Decision(category_id=category_id, status="suggested", source="builtin", confidence=0.6)
    return Decision()


def learn(user_id, merchant_key, category_id, direction, display_name=None, is_transfer=False):
    if not merchant_key or category_id is None:
        return
    db.execute(
        """INSERT INTO merchant_memory (user_id, merchant_key, direction, display_name, category_id, is_transfer)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (user_id, merchant_key, direction) DO UPDATE SET
             category_id = EXCLUDED.category_id,
             is_transfer = EXCLUDED.is_transfer,
             display_name = COALESCE(EXCLUDED.display_name, merchant_memory.display_name),
             times_used = merchant_memory.times_used + 1,
             last_used_at = now()""",
        (user_id, merchant_key, direction, display_name, category_id, bool(is_transfer)),
    )


def forget(user_id, merchant_key, direction=None):
    if direction is None:
        db.execute("DELETE FROM merchant_memory WHERE user_id = %s AND merchant_key = %s", (user_id, merchant_key))
    else:
        db.execute("DELETE FROM merchant_memory WHERE user_id = %s AND merchant_key = %s AND direction = %s",
                   (user_id, merchant_key, direction))


def apply_manual(user_id, txn_ids, category_id, learn_memory=True, source="manual"):
    """Set (or clear, when category_id is None) the category on the user's
    transactions, record events, and remember the choice per merchant."""
    ids = [int(i) for i in (txn_ids or [])]
    if not ids:
        return 0
    rows = db.query(
        "SELECT id, merchant_key, merchant_name, amount FROM transactions WHERE user_id = %s AND id = ANY(%s)",
        (user_id, ids),
    ) or []
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    if category_id is None:
        db.execute(
            """UPDATE transactions SET category_id = NULL, category_status = 'none', category_source = NULL,
                   category_rule_id = NULL, category_confidence = NULL, updated_at = now()
               WHERE id = ANY(%s)""",
            (ids,),
        )
        record_events([(i, "manual", {"category_id": None}, user_id) for i in ids])
        drop_splits(ids, user_id, "recategorized")
        return len(ids)
    db.execute(
        """UPDATE transactions SET category_id = %s, category_status = 'confirmed', category_source = %s,
               category_rule_id = NULL, category_confidence = 1.0, updated_at = now()
           WHERE id = ANY(%s)""",
        (category_id, source, ids),
    )
    record_events([(r["id"], source if source in ("manual", "merchant", "ai", "rule") else "manual",
                    {"category_id": category_id}, user_id) for r in rows])
    drop_splits(ids, user_id, "recategorized")
    if learn_memory:
        seen = {}
        for r in rows:
            if r["merchant_key"]:
                seen.setdefault((r["merchant_key"], direction(r["amount"])), r["merchant_name"])
        for (key, way), name in seen.items():
            learn(user_id, key, category_id, way, display_name=name)
    return len(ids)


def to_decimal(value):
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))
