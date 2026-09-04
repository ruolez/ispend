"""Merchant normalization: raw statement description -> (key, display name, clean text). Pure."""
import re
from dataclasses import dataclass

PREFIXES = (
    "SQ", "SQU", "TST", "PP", "PY", "PAYPAL", "APLPAY", "APPLE PAY", "GOOGLE", "GOOGLE PAY", "DD", "DOORDASH",
    "IC", "INSTACART", "WWW", "POS", "POS DEBIT", "POS PURCHASE", "PURCHASE", "DEBIT CARD PURCHASE",
    "DEBIT PURCHASE", "CHECKCARD", "CHECK CARD", "VISA", "MC", "MASTERCARD", "ACH DEBIT", "ACH CREDIT", "ACH",
    "ONLINE PAYMENT", "RECURRING PAYMENT", "RECURRING", "PAYMENT TO", "ELECTRONIC WITHDRAWAL",
    "DIRECT DEBIT", "DIRECT DEPOSIT", "PREAUTHORIZED", "PRE-AUTHORIZED", "AUTOPAY", "AUTOMATIC PAYMENT",
    "INTERAC", "E-TRANSFER", "INTERAC E-TRANSFER", "WITHDRAWAL", "DEPOSIT", "BILL PAYMENT", "PAYMENT",
    "CARD PURCHASE", "TFR", "PURCHASE AUTHORIZED ON", "BPS",
)
_PREFIX_RE = re.compile(
    r"^(?:(?:" + "|".join(re.escape(p) for p in sorted(PREFIXES, key=len, reverse=True)) + r")\b\s*[\*\-:#/]*\s*)+",
    re.I,
)
_STAR_CODE = re.compile(r"\*\s*(?=[A-Z0-9\-]*\d)[A-Z0-9][A-Z0-9\-]{1,}\b")
NOISE = [
    re.compile(r"\b(?:CARD|CRD)\s*(?:#|NO\.?|ENDING(?: IN)?)?\s*\d{4}\b"),
    re.compile(r"\bX{2,}\d{2,4}\b"),
    re.compile(r"\*{2,}\d{2,4}\b"),
    re.compile(r"\b(?:STORE|STR|LOC|UNIT|BRANCH|TERMINAL|TERM|REF|REFERENCE|TRACE|CONF|CONFIRMATION|ID|INV|INVOICE|ORDER|ORD|SEQ|AUTH|APPROVAL)\s*(?:#|NO\.?|:)?\s*[A-Z0-9\-]*\d[A-Z0-9\-]*\b"),
    re.compile(r"#\s?[A-Z0-9\-]*\d[A-Z0-9\-]*\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
    re.compile(r"\b\d{2}:\d{2}(?::\d{2})?\b"),
    re.compile(r"\b(?=[A-Z0-9]{10,}\b)(?=[A-Z0-9]*\d)[A-Z0-9]+\b"),
    re.compile(r"(?<![A-Z0-9])(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b"),
    re.compile(r"\b\d{6,}\b"),
    re.compile(r"\b(?:AUTHORIZED ON|AUTHORISED ON|VIA|FROM|TO|THE)\s*$"),
    re.compile(r"\b[A-Z]{1,2}\d{4,}\b"),
    re.compile(r"\b(?:DEBIT|CREDIT|PURCHASE|PAYMENT|ONLINE|MOBILE|RECURRING|WITHDRAWAL|DEPOSIT|POS|EFT|ACH|CHK|CK|WEB|PMT|TXN|TRANS|TRANSACTION|ELECTRONIC|MERCHANT|RECEIVED|RCVD|SENT)\b"),
    re.compile(r"\b(?:HTTPS?://)?(?:WWW\.)?([A-Z0-9\-]+)\.(?:COM|CA|NET|ORG|CO|IO|US)\b(?:/\S*)?"),
    re.compile(r"\b\d+(?:\.\d+)?\s?(?:USD|CAD)\b"),
    re.compile(r"\b(?:USD|CAD)\s?\d+(?:\.\d+)?\b"),
]
_DOMAIN = NOISE[-3]
_GENERIC_WORDS = NOISE[-4]
LEADING_PREPOSITIONS = {"TO", "FROM", "AT", "FOR", "BY", "VIA", "WITH"}
GENERIC_RESIDUE = {"THANK", "YOU", "SENT", "RECEIVED", "RCVD", "FROM", "TO", "IN", "OUT", "FEE", "BY", "AT", "AND", "THE", "OF"}
STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR",
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
}
COUNTRY_SUFFIX = {"US", "USA", "CAN", "CA"}
CITY_PREFIX_WORDS = {"NORTH", "SOUTH", "EAST", "WEST", "NEW", "SAN", "LOS", "LAS", "ST", "ST.", "FORT", "FT", "MOUNT", "MT",
                     "SAINT", "PORT", "LAKE", "OLD", "N", "S", "E", "W", "DOWNTOWN", "SANTA", "EL", "LA", "DES"}
ALIASES = [
    ("PAYMENT THANK YOU", "Card Payment"), ("AUTOPAY PAYMENT THANK YOU", "Card Payment"),
    ("AUTOPAY PAYMENT", "Card Payment"), ("AUTOMATIC PAYMENT", "Card Payment"),
    ("ONLINE PAYMENT THANK YOU", "Card Payment"), ("MOBILE PAYMENT THANK YOU", "Card Payment"),
    ("INTERNET PAYMENT THANK YOU", "Card Payment"), ("PAYMENT RECEIVED THANK YOU", "Card Payment"),
    ("INTERAC E-TRANSFER", "Interac e-Transfer"), ("E-TRANSFER", "Interac e-Transfer"), ("AT&T", "AT&T"), ("EBAY", "eBay"),
    ("AMAZON", "Amazon"), ("AMZN", "Amazon"), ("AMAZON PRIME", "Amazon Prime"), ("PRIME VIDEO", "Amazon Prime Video"),
    ("WAL-MART", "Walmart"), ("WAL MART", "Walmart"), ("WALMART", "Walmart"), ("WM SUPERCENTER", "Walmart"),
    ("WM SUPERC", "Walmart"), ("MCDONALD", "McDonald's"), ("MCDONALDS", "McDonald's"), ("STARBUCKS", "Starbucks"),
    ("TIM HORTONS", "Tim Hortons"), ("TIMHORTONS", "Tim Hortons"), ("COSTCO", "Costco"), ("COSTCO WHSE", "Costco"),
    ("TARGET", "Target"), ("UBER EATS", "Uber Eats"), ("UBER", "Uber"), ("LYFT", "Lyft"), ("NETFLIX", "Netflix"),
    ("SPOTIFY", "Spotify"), ("APPLE.COM/BILL", "Apple"), ("APPLE COM BILL", "Apple"), ("APPLE", "Apple"),
    ("GOOGLE", "Google"), ("YOUTUBE", "YouTube"), ("KROGER", "Kroger"), ("WHOLEFDS", "Whole Foods"),
    ("WHOLE FOODS", "Whole Foods"), ("TRADER JOE", "Trader Joe's"), ("HOME DEPOT", "Home Depot"),
    ("THE HOME DEPOT", "Home Depot"), ("LOWES", "Lowe's"), ("SHELL OIL", "Shell"), ("SHELL", "Shell"),
    ("CHEVRON", "Chevron"), ("EXXON", "Exxon"), ("EXXONMOBIL", "Exxon"), ("BP", "BP"), ("7-ELEVEN", "7-Eleven"),
    ("CVS", "CVS"), ("CVS/PHARMACY", "CVS"), ("WALGREENS", "Walgreens"), ("CHIPOTLE", "Chipotle"),
    ("DOORDASH", "DoorDash"), ("GRUBHUB", "Grubhub"), ("INSTACART", "Instacart"), ("VENMO", "Venmo"),
    ("ZELLE", "Zelle"), ("PAYPAL", "PayPal"), ("SHOPPERS DRUG", "Shoppers Drug Mart"), ("LOBLAWS", "Loblaws"),
    ("SOBEYS", "Sobeys"), ("METRO", "Metro"), ("CANADIAN TIRE", "Canadian Tire"), ("PETRO-CANADA", "Petro-Canada"),
    ("PETRO CANADA", "Petro-Canada"), ("ESSO", "Esso"), ("HULU", "Hulu"), ("DISNEY PLUS", "Disney+"),
    ("DISNEYPLUS", "Disney+"), ("HBO MAX", "HBO Max"), ("AUDIBLE", "Audible"), ("MICROSOFT", "Microsoft"),
    ("ADOBE", "Adobe"), ("DROPBOX", "Dropbox"), ("GITHUB", "GitHub"), ("OPENAI", "OpenAI"), ("CHATGPT", "OpenAI"),
    ("SAFEWAY", "Safeway"), ("ALDI", "Aldi"), ("PUBLIX", "Publix"), ("H-E-B", "H-E-B"), ("HEB", "H-E-B"),
    ("WENDYS", "Wendy's"), ("WENDY'S", "Wendy's"), ("BURGER KING", "Burger King"), ("SUBWAY", "Subway"),
    ("DUNKIN", "Dunkin'"), ("IKEA", "IKEA"), ("BEST BUY", "Best Buy"), ("USPS", "USPS"), ("UPS", "UPS"),
    ("FEDEX", "FedEx"), ("AIRBNB", "Airbnb"), ("EXPEDIA", "Expedia"), ("DELTA AIR", "Delta Air Lines"),
    ("UNITED AIR", "United Airlines"), ("AMERICAN AIR", "American Airlines"), ("AIR CANADA", "Air Canada"),
    ("WESTJET", "WestJet"), ("SOUTHWEST", "Southwest Airlines"), ("NETFLIX.COM", "Netflix"),
]
_ALIASES_SORTED = sorted(ALIASES, key=lambda a: len(a[0]), reverse=True)
_SMALL_WORDS = {"OF", "THE", "AND", "DE", "LA", "LE", "DU", "DES", "A", "AN", "IN", "ON", "AT", "BY", "FOR", "TO"}


@dataclass(frozen=True)
class Merchant:
    key: str
    name: str
    clean: str


def _strip_location(tokens):
    """Drop a trailing '<CITY> <ST>' / country marker, keeping at least one token."""
    if len(tokens) >= 2 and tokens[-1] in COUNTRY_SUFFIX and tokens[-2] in STATES:
        tokens = tokens[:-1]
    if len(tokens) >= 2 and tokens[-1] in STATES:
        tokens = tokens[:-1]
        if len(tokens) >= 2:
            tokens = tokens[:-1]
            if len(tokens) >= 2 and tokens[-1] in CITY_PREFIX_WORDS:
                tokens = tokens[:-1]
    elif len(tokens) >= 2 and tokens[-1] in COUNTRY_SUFFIX - {"CA"}:
        tokens = tokens[:-1]
    return tokens



_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9&'.\-]*")


def _flat(text):
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def split_descriptor(description):
    """Some exports (Bilt, Apple Card, ...) write "Pretty Merchant Cardholder Name RAW*DESCRIPTOR".
    When a later part of the text restates the merchant's first word, split into
    (pretty merchant words, raw descriptor tail). Returns None when the shape is not present."""
    text = (description or "").strip()
    words = text.split()
    if len(words) < 2:
        return None
    head = words[0].strip("*#.,")
    if len(_flat(head)) < 2:
        return None
    restart = None
    for m in re.finditer(r"(?<![A-Za-z0-9])" + re.escape(head) + r"(?![A-Za-z])", text, re.I):
        if m.start() > 0:
            restart = m.start()
    if restart is not None:
        # include a processor prefix glued to the restatement: "... BPS*BILT HOUSING"
        m = re.search(r"(?:" + "|".join(re.escape(p) for p in PREFIXES) + r")\s*[\*\-:#/]?\s*$", text[:restart], re.I)
        if m and m.start() > 0:
            restart = m.start()
    if restart is None:
        return None
    pretty, tail = text[:restart].strip(), text[restart:].strip()
    pwords = pretty.split()
    if not pwords:
        return None
    flat_tail = _flat(_PREFIX_RE.sub("", tail))
    best, concat = 0, ""
    for k, w in enumerate(pwords, start=1):
        prev, concat = concat, concat + _flat(w)
        if flat_tail.startswith(concat):
            best = k
            continue
        if len(flat_tail) > len(prev) and concat.startswith(flat_tail):  # truncated inside this word
            best = k
        break
    if best == 0:
        return None
    return " ".join(pwords[:best]), tail


def raw_descriptor(description):
    parts = split_descriptor(description)
    return parts[1] if parts else (description or "").strip()


PHRASE_STOP = {"PAYMENT", "THANK", "YOU", "ONLINE", "MOBILE", "PURCHASE", "DEBIT", "CREDIT", "CARD", "POS", "THE",
               "AND", "INC", "LLC", "LTD", "COM", "WWW", "STORE", "MARKET", "AUTOPAY", "TRANSFER", "INTERAC"}


def frequent_phrases(descriptions, min_share=0.35, min_rows=15):
    """Word pairs that recur across most descriptions of a statement (a cardholder's name in
    exports like Bilt's). Returns the dominant pair plus any pair sharing its last word
    (a second cardholder), uppercased, e.g. ["EUGENE BRAVERMAN", "MARIA BRAVERMAN"]."""
    docs = [d for d in descriptions if d]
    if len(docs) < min_rows:
        return []
    counts = {}
    for d in docs:
        toks = [t.upper() for t in _WORD.findall(d)]
        seen = set()
        for a, b in zip(toks, toks[1:]):
            if not (a.isalpha() and b.isalpha() and len(a) >= 2 and len(b) >= 2):
                continue
            if a in PHRASE_STOP or b in PHRASE_STOP or b in STATES or b in COUNTRY_SUFFIX:
                continue  # "CHICAGO IL" is a location, not a cardholder
            seen.add(f"{a} {b}")
        for ph in seen:
            counts[ph] = counts.get(ph, 0) + 1
    if not counts:
        return []
    top, n = max(counts.items(), key=lambda kv: kv[1])
    if n / len(docs) < min_share:
        return []
    surname = top.split()[1]
    out = [top]
    for ph, c in counts.items():
        if ph != top and ph.split()[1] == surname and c / len(docs) >= 0.03:
            out.append(ph)
    return out


def strip_phrases(description, phrases):
    text = description or ""
    for ph in phrases or ():
        text = re.sub(r"(?<![A-Za-z])" + re.escape(ph).replace(r"\ ", r"\s+") + r"(?![A-Za-z])", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def clean_description(description, light=False):
    text = (description or "").upper().replace("’", "'").replace("’", "'")
    text = re.sub(r"[ \t]+", " ", text)
    text = text.strip()
    had_prefix = False
    for _ in range(3):
        if light:
            break
        stripped = _PREFIX_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
        had_prefix = True
    if light:
        text = text.replace("-", " ")
    text = _DOMAIN.sub(lambda m: m.group(1) + " ", text)
    text = _STAR_CODE.sub(" ", text)
    text = text.replace("*", " ")
    for pat in NOISE:
        if light and pat is _GENERIC_WORDS:
            continue
        text = pat.sub(" ", text)
    text = re.sub(r"[^A-Z0-9&'\-./ ]+", " ", text)
    tokens = [t.strip("-./'") for t in text.split()]
    tokens = [t for t in tokens if t]
    tokens = _strip_location(tokens)
    while had_prefix and len(tokens) > 1 and tokens[0] in LEADING_PREPOSITIONS:
        tokens = tokens[1:]
    tokens = [t for i, t in enumerate(tokens) if not re.fullmatch(r"\d+", t) or len(tokens) == 1
              or (i == 0 and len(tokens) > 1 and len(t) <= 3)]
    clean = " ".join(tokens).strip()
    if not light and (not tokens or all(t in GENERIC_RESIDUE for t in tokens)):
        # Everything meaningful was "noise" (e.g. "Payment Thank You-Mobile"):
        # keep the generic words so the row still reads as a payment/transfer.
        return clean_description(description, light=True)
    return clean


def _alias_key(text):
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]+", " ", text.upper())).strip()


def _alias_for(clean):
    flat = _alias_key(clean)
    for pattern, display in _ALIASES_SORTED:
        p = _alias_key(pattern)
        if flat == p or flat.startswith(p + " "):
            return display
    return None


def _title(key):
    words = []
    for i, w in enumerate(key.split()):
        if w in _SMALL_WORDS and i > 0:
            words.append(w.lower())
        elif len(w) <= 3 and w.isalpha() and w not in ("THE", "AND"):
            words.append(w)
        else:
            words.append("-".join(p[:1] + p[1:].lower() for p in w.split("-")))
    return " ".join(words)


def normalize(description, phrases=()):
    original = strip_phrases((description or "").strip(), phrases)
    parts = split_descriptor(original)
    if parts:
        pretty_clean, tail_clean = clean_description(parts[0]), clean_description(parts[1])
        # the raw descriptor usually carries more detail (AMAZON MKTP, SPECTRUM MOBILE); fall back to
        # the pretty name when the descriptor was truncated or cleans down to less than the name
        if len(_flat(tail_clean)) > len(_flat(pretty_clean)):
            original, clean = parts[1], tail_clean
        else:
            original, clean = parts[0], pretty_clean
    else:
        clean = clean_description(original)
    if not clean:
        clean = re.sub(r"\s+", " ", original.upper()).strip() or "UNKNOWN"
    alias = _alias_for(clean)
    if alias:
        key = alias.upper()
        name = alias
    else:
        tokens = [t for t in clean.split() if not re.fullmatch(r"\d+", t)] or clean.split()
        key = " ".join(tokens[:3])
        name = _title(key)
        if "MCDONALD" in key:
            name = "McDonald's"
    return Merchant(key=key, name=name, clean=clean)
