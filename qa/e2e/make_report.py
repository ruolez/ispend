"""Render the pass/fail matrix, console/network table, performance table and probe issues
from qa/reports/smoke-results.jsonl as Markdown fragments (stdout)."""
import json
import pathlib
import sys
from collections import Counter

RESULTS = pathlib.Path(__file__).resolve().parents[1] / "reports" / "smoke-results.jsonl"


def load():
    rows = []
    for line in RESULTS.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def matrix(rows):
    m = [r for r in rows if r["kind"] == "matrix"]
    pages = ["login", "index", "transactions", "review", "import", "statements", "categories", "rules", "reports", "budgets", "insights", "settings"]
    cols = [("anon", "light", "1440"), ("anon", "dark", "1440"), ("anon", "light", "390"), ("anon", "dark", "390")]
    cols += [(p, t, v) for p in ("qa_tester", "admin") for t in ("light", "dark") for v in ("1440", "390")]
    idx = {(r["page"], r["persona"], r["theme"], r["vp"]): r for r in m}
    out = ["| page | " + " | ".join(f"{p}<br>{t}/{v}" for p, t, v in cols) + " |", "|---|" + "---|" * len(cols)]
    for pg in pages:
        cells = []
        for c in cols:
            r = idx.get((pg, *c))
            if not r:
                cells.append("—")
            else:
                cells.append("PASS" if r["status"] == "pass" else f"**FAIL** ({len(r['issues'])})")
        out.append(f"| {pg} | " + " | ".join(cells) + " |")
    n_pass = sum(1 for r in m if r["status"] == "pass")
    out.append(f"\n{n_pass}/{len(m)} page loads clean.")
    return "\n".join(out)


def errors(rows):
    m = [r for r in rows if r["kind"] in ("matrix", "probe")]
    out = ["| page | persona | theme | vp | phase | kind | message | source |", "|---|---|---|---|---|---|---|---|"]
    seen = Counter()
    for r in m:
        for c in r.get("console", []):
            key = (r["page"], c["type"], c["text"][:80])
            seen[key] += 1
            if seen[key] > 2:
                continue
            out.append(f"| {r['page']} | {r['persona']} | {r['theme']} | {r['vp']} | {c['label']} | console.{c['type']} | {c['text'][:160].replace('|', '\\|')} | {c['source'][-60:]} |")
        for e in r.get("pageerrors", []):
            out.append(f"| {r['page']} | {r['persona']} | {r['theme']} | {r['vp']} | {e['label']} | pageerror | {e['text'][:160].replace('|', '\\|')} | |")
        for f in r.get("failed", []):
            out.append(f"| {r['page']} | {r['persona']} | {r['theme']} | {r['vp']} | {f['label']} | requestfailed | {f['method']} {f['url'][-80:]} {f['failure']} | |")
        for h in r.get("http_errors", []):
            out.append(f"| {r['page']} | {r['persona']} | {r['theme']} | {r['vp']} | {h['label']} | http {h['status']} | {h['method']} {h['url'][-90:]} | |")
    if len(out) == 2:
        out.append("| — | | | | | | none | |")
    return "\n".join(out)


def slow(rows):
    out = ["| page | persona | theme | vp | phase | ms | request |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["kind"] not in ("matrix", "probe"):
            continue
        for s in r.get("slow", []):
            out.append(f"| {r['page']} | {r['persona']} | {r['theme']} | {r['vp']} | {s['label']} | {s['ms']} | {s['method']} {s['url'].split('5559', 1)[-1][:110]} |")
    if len(out) == 2:
        out.append("| — | | | | | | none |")
    return "\n".join(out)


def perf(rows):
    m = [r for r in rows if r["kind"] == "matrix" and r["theme"] == "light" and r["vp"] == "1440"]
    out = ["| page | persona | DCL ms | load ms | skeletons clear ms | requests | KB | API calls | dup API | largest JS | largest CSS | Chart.js | canvases |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(m, key=lambda r: (r["page"], r["persona"])):
        p = r["perf"]
        lj = p.get("largestJs") or {}
        lc = p.get("largestCss") or {}
        out.append(f"| {r['page']} | {r['persona']} | {p['dcl']} | {p['load']} | {r['loaded']['loaded_ms']} | {p['requests']} | {round(p['bytes'] / 1024)} | {p['apiCalls']} | {', '.join(p['apiDups']) or '—'} | {lj.get('name', '—').split('/')[-1]} {round(lj.get('enc', 0) / 1024)}KB | {lc.get('name', '—').split('/')[-1]} {round(lc.get('enc', 0) / 1024)}KB | {'yes' if p['chartLoaded'] else 'no'} | {p['canvases']} |")
    return "\n".join(out)


def api_calls(rows):
    m = [r for r in rows if r["kind"] == "matrix" and r["theme"] == "light" and r["vp"] == "1440" and r["persona"] == "admin"]
    out = []
    for r in sorted(m, key=lambda r: r["page"]):
        calls = [c["url"].split("5559", 1)[-1] for c in r.get("api_calls", [])]
        out.append(f"- **{r['page']}** ({len(calls)}): " + ", ".join(f"`{c}`" for c in calls))
    return "\n".join(out)


def probes(rows):
    out = []
    for r in [r for r in rows if r["kind"] == "probe"]:
        bad = [s for s in r["steps"] if not s["ok"]]
        summ = next((s for s in r["steps"] if s["label"] == "buttons:summary"), {})
        out.append(f"### {r['page']} · {r['theme']}/{r['vp']} — {'PASS' if r['status'] == 'pass' else 'FAIL'} ({len(r['steps'])} steps, {len(bad)} failed)")
        out.append(f"- {summ.get('detail', '')}")
        for s in r["steps"]:
            if s["label"] == "buttons:summary":
                continue
            flag = "OK" if s["ok"] else "**FAIL**"
            probs = json.dumps(s["problems"], default=str)[:300] if s["problems"] else ""
            slow_ = f" slow: {[f'{x['ms']}ms {x['url'].split('5559', 1)[-1][:60]}' for x in s['slow']]}" if s["slow"] else ""
            out.append(f"  - {flag} `{s['label']}` — {str(s['detail'])[:220]} {probs}{slow_}")
    return "\n".join(out)


def others(rows):
    out = []
    for r in rows:
        if r["kind"] in ("auth", "theme"):
            out.append(f"- **{r['kind']}:{r['name']}** — {'PASS' if r['ok'] else '**FAIL**'} — `{json.dumps(r['detail'], default=str)[:600]}`")
    return "\n".join(out)


def text_hits(rows):
    out = []
    for r in rows:
        if r["kind"] == "matrix" and r.get("text_hits"):
            for t in r["text_hits"]:
                out.append(f"- {r['page']} {r['persona']} {r['theme']}/{r['vp']}: [{t['kind']}] `{t['text']}` in `{t['el']}`")
    return "\n".join(out) or "- none"


if __name__ == "__main__":
    rows = load()
    section = sys.argv[1] if len(sys.argv) > 1 else "all"
    fns = {"matrix": matrix, "errors": errors, "slow": slow, "perf": perf, "api": api_calls, "probes": probes, "others": others, "text": text_hits}
    if section == "all":
        for k, f in fns.items():
            print(f"\n## {k}\n")
            print(f(rows))
    else:
        print(fns[section](rows))
