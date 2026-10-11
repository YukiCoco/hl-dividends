"""Regenerate markets.json from trade.xyz spec index + live Hyperliquid xyz universe.

Run occasionally when xyz lists new markets; review the diff by hand. Aliases below
cover cases where the on-chain name differs from the docs instrument name.
"""
import html, json, re, sys, urllib.request

SPEC = "https://docs.trade.xyz/perpetuals/specifications-and-schedules/specification-index.md"
HL = "https://api.hyperliquid.xyz/info"
ALIASES = {"SKHX": "SKHYNIX", "SMSN": "SAMSUNG", "CL": "WTIOIL", "HO": "DIESEL"}
INDEX = {"SP500", "XYZ100", "JP225", "KR200"}
COMMODITY = {"BRENTOIL", "WTIOIL", "NATGAS", "GOLD", "SILVER", "PLATINUM", "PALLADIUM", "COPPER", "DIESEL"}
FX = {"JPY", "EUR", "GBP"}
EXCH = re.compile(r"\((Nasdaq|NYSE Arca|NYSE|Cboe BZX|KRX|TSE|HKEX|SSE STAR|SSE):\s*([A-Z0-9.]+)\)")

def get(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=30).read().decode()

spec = get(SPEC)
rows = {}
for r in re.findall(r"<tr>(.*?)</tr>", spec, re.S):
    cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
    if len(cells) >= 3:
        rows[cells[0]] = {"description": cells[1], "underlying": cells[2]}

universe = json.loads(get(HL, json.dumps({"type": "meta", "dex": "xyz"}).encode()))["universe"]
live = [u["name"].split(":", 1)[1] for u in universe if not u.get("isDelisted")]

old = {}
try:
    old = {m["hl"]: m for m in json.load(open("markets.json"))}
except FileNotFoundError:
    pass

out, missing = [], []
MANUAL_PREIPO = {"OURA": "Oura Health Oy (pre-IPO perpetual)"}
for sym in live:
    doc = ALIASES.get(sym, sym)
    if doc not in rows:
        if sym in MANUAL_PREIPO:
            out.append({"hl": sym, "doc": sym, "name": MANUAL_PREIPO[sym], "type": "preipo", "exchange": None, "ticker": None, "yahoo": None, "nasdaq": None}); continue
        missing.append(sym); continue
    d = rows[doc]["description"]
    name = d.split(" references ", 1)[-1].split(", quoted")[0].split(". The")[0].strip()
    m = EXCH.search(d)
    if doc in INDEX: t = "index"
    elif doc in COMMODITY: t = "commodity"
    elif doc in FX: t = "fx"
    elif not m: t = "preipo"
    elif re.search(r"\bETF\b|\bFund\b|Direxion|Daily .*Shares", d): t = "etf"
    elif "Preferred" in d: t = "preferred"
    elif re.search(r"\bAD[SR]\b", d): t = "adr"
    else: t = "stock"
    exch, code = (m.group(1), m.group(2)) if m else (None, None)
    yahoo = None
    if exch in ("Nasdaq", "NYSE", "NYSE Arca", "Cboe BZX"): yahoo = code
    elif exch == "KRX": yahoo = f"{code}.KS"
    elif exch == "TSE": yahoo = f"{code}.T"
    elif exch == "HKEX": yahoo = f"{code.zfill(4)}.HK"
    elif exch in ("SSE", "SSE STAR"): yahoo = f"{code}.SS"
    nasdaq = ("etf" if t == "etf" else "stocks") if exch == "Nasdaq" else None
    entry = {"hl": sym, "doc": doc, "name": name, "type": t, "exchange": exch, "ticker": code, "yahoo": yahoo, "nasdaq": nasdaq}
    if sym in old and old[sym].get("manual"):
        entry = old[sym]  # keep hand edits
    out.append(entry)

json.dump(out, open("markets.json", "w"), indent=1, ensure_ascii=False)
print(f"{len(out)} mapped, {len(missing)} missing: {missing}")
from collections import Counter
print(Counter(e["type"] for e in out))
