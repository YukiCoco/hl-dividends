#!/usr/bin/env python3
"""Build site/data.json: upcoming dividends for Hyperliquid trade.xyz stock perps.

Sources (all free, no keys):
  Hyperliquid info API  -> live xyz universe
  Yahoo chart           -> 3y dividend history + last price (all tickers incl. ETFs)
  Yahoo quoteSummary    -> latest declared ex/pay date (needs crumb)
  Nasdaq quote API      -> declared future rows, Nasdaq-listed tickers only
Pure computation lives in infer_frequency / median_pay_offset / merge_declared /
project / assemble so it can be unit-tested without network.
"""
from __future__ import annotations

import json, re, statistics, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path

HERE = Path(__file__).parent
# Yahoo returns 429 to a full Chrome UA + Accept header combo from a non-browser client;
# the bare UA below is what works. Nasdaq additionally needs Accept: application/json.
UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
NASDAQ_HDR = {"Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}
HORIZON_DAYS = 365
DIVIDEND_TYPES = {"stock", "adr", "etf", "preferred"}
FREQ_BANDS = [("semimonthly", 12, 18), ("monthly", 25, 38), ("quarterly", 80, 100), ("semiannual", 160, 205), ("annual", 350, 380)]
FREQ_STEP = {"semimonthly": 15, "monthly": 30, "quarterly": 91, "semiannual": 182, "annual": 365}

# ----------------------------------------------------------------------------- pure logic

def infer_frequency(ex_dates):
    """Return (label, step_days). Uses the median of the last 6 gaps."""
    ds = sorted(set(ex_dates))
    if len(ds) < 2:
        return (None, None)
    gaps = [(b - a).days for a, b in zip(ds, ds[1:])][-6:]
    med = statistics.median(gaps)
    for label, lo, hi in FREQ_BANDS:
        if lo <= med <= hi:
            return (label, FREQ_STEP[label])
    return ("irregular", None)


def median_pay_offset(history):
    offs = [(date.fromisoformat(h["pay"]) - date.fromisoformat(h["ex"])).days for h in history if h.get("pay") and h.get("ex")]
    offs = [o for o in offs if 0 <= o <= 120]
    return int(statistics.median(offs)) if offs else None


def merge_declared(nasdaq_rows, yahoo_event, today, fallback_amount=None):
    """Union of Nasdaq future rows and Yahoo's latest calendar event, deduped on ex-date."""
    out = {}
    for r in nasdaq_rows:
        if r.get("ex") and date.fromisoformat(r["ex"]) >= today:
            out[r["ex"]] = {"ex": r["ex"], "pay": r.get("pay"), "amount": r.get("amount"), "source": "nasdaq", "status": "declared"}
    y = yahoo_event
    if y and y.get("ex") and date.fromisoformat(y["ex"]) >= today and y["ex"] not in out:
        out[y["ex"]] = {"ex": y["ex"], "pay": y.get("pay"), "amount": y.get("amount") if y.get("amount") is not None else fallback_amount,
                        "source": "yahoo", "status": "declared"}
    return [out[k] for k in sorted(out)]


def project(last_ex, interval, amount, pay_offset, today, declared, horizon_days=HORIZON_DAYS):
    """Extrapolate estimated events after the last known (historical or declared) ex-date."""
    if not interval or amount is None:
        return []
    anchors = [last_ex] if last_ex else []
    anchors += [date.fromisoformat(d["ex"]) for d in declared]
    if not anchors:
        return []
    cur = max(anchors)
    taken = {d["ex"] for d in declared}
    end = today + timedelta(days=horizon_days)
    out = []
    while True:
        cur = cur + timedelta(days=interval)
        if cur > end:
            break
        ex = cur + timedelta(days=(7 - cur.weekday()) if cur.weekday() >= 5 else 0)  # weekend -> next Monday
        if ex < today or ex.isoformat() in taken:
            continue
        out.append({"ex": ex.isoformat(), "pay": (ex + timedelta(days=pay_offset)).isoformat() if pay_offset is not None else None,
                    "amount": amount, "source": "estimate", "status": "estimated"})
    return out


def assemble(m, history, price, currency, declared, today):
    hist = sorted((h for h in history if h.get("ex")), key=lambda h: h["ex"])
    ex_dates = [date.fromisoformat(h["ex"]) for h in hist]
    freq, step = infer_frequency(ex_dates)
    last = hist[-1] if hist else None
    last_amount = (declared[-1]["amount"] if declared and declared[-1].get("amount") is not None else (last["amount"] if last else None))
    for d in declared:
        if d.get("amount") is None:
            d["amount"] = last_amount
    est = project(ex_dates[-1] if ex_dates else None, step, last_amount, median_pay_offset(hist), today, declared)
    events = declared + est
    nxt = events[0] if events else None
    one_year_ago = today - timedelta(days=365)
    ttm = sum(h["amount"] for h in hist if date.fromisoformat(h["ex"]) > one_year_ago and h.get("amount") is not None)
    status = "declared" if declared else ("estimated" if est else ("irregular" if hist else "none"))
    return {
        "hl": m["hl"], "name": m.get("name"), "type": m["type"], "exchange": m.get("exchange"), "ticker": m.get("ticker"),
        "yahoo": m.get("yahoo"), "currency": currency, "price": price, "frequency": freq, "status": status,
        "last": {"ex": last["ex"], "amount": last["amount"]} if last else None,
        "next": nxt,
        "next_gap_pct": (nxt["amount"] / price * 100) if (nxt and nxt.get("amount") is not None and price) else None,
        "ttm_yield_pct": (ttm / price * 100) if (price and hist) else None,
        "events": events,
        "history": hist[-8:],
        "errors": [],
    }

# ----------------------------------------------------------------------------- fetchers

def _get(url, headers=None, data=None, opener=None, retries=3):
    import gzip
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    last = None
    for i in range(retries):
        try:
            resp = (opener.open if opener else urllib.request.urlopen)(req, timeout=30)
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode()
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (404, 400):
                raise
            time.sleep(3 * (i + 1))
        except Exception as e:
            last = repr(e)
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"failed {url}: {last}")


def hl_universe():
    body = json.dumps({"type": "meta", "dex": "xyz"}).encode()
    j = json.loads(_get("https://api.hyperliquid.xyz/info", {"Content-Type": "application/json"}, body))
    return [u["name"].split(":", 1)[1] for u in j["universe"] if not u.get("isDelisted")]


class Yahoo:
    def __init__(self):
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.crumb = None
        self.crumb_failed = False

    def _crumb(self):
        """Fetch the crumb once per run; if Yahoo refuses, disable the calendar module instead of retrying per ticker."""
        if self.crumb:
            return self.crumb
        if self.crumb_failed:
            raise RuntimeError("crumb unavailable this run")
        try:
            try:
                self.opener.open(urllib.request.Request("https://fc.yahoo.com", headers=UA), timeout=20)
            except urllib.error.HTTPError:
                pass  # fc.yahoo.com answers 404 but sets the A3 cookie
            self.crumb = _get("https://query2.finance.yahoo.com/v1/test/getcrumb", opener=self.opener, retries=2).strip()
        except Exception:
            self.crumb_failed = True
            raise
        return self.crumb

    def chart(self, sym):
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=3y&interval=1d&events=div"
        r = json.loads(_get(url))["chart"]["result"][0]
        meta = r.get("meta", {})
        divs = r.get("events", {}).get("dividends", {})
        hist = [{"ex": datetime.fromtimestamp(int(k), timezone.utc).date().isoformat(), "pay": None, "amount": float(v["amount"])}
                for k, v in divs.items()]
        return hist, meta.get("regularMarketPrice"), meta.get("currency")

    def calendar(self, sym):
        url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(sym)}"
               f"?modules=calendarEvents,summaryDetail&crumb={urllib.parse.quote(self._crumb())}")
        j = json.loads(_get(url, opener=self.opener))["quoteSummary"]
        res = (j.get("result") or [None])[0]
        if not res:
            return None
        ce = res.get("calendarEvents", {}) or {}
        ex = (ce.get("exDividendDate") or {}).get("fmt")
        pay = (ce.get("dividendDate") or {}).get("fmt")
        return {"ex": ex, "pay": pay, "amount": None, "source": "yahoo"} if ex else None


def nasdaq_rows(ticker, assetclass):
    url = f"https://api.nasdaq.com/api/quote/{ticker}/dividends?assetclass={assetclass}"
    j = json.loads(_get(url, NASDAQ_HDR))
    rows = ((j.get("data") or {}).get("dividends") or {}).get("rows") or []
    out = []
    for r in rows:
        ex = _us(r.get("exOrEffDate")); pay = _us(r.get("paymentDate"))
        amt = r.get("amount", "")
        amt = float(amt.replace("$", "").replace(",", "")) if amt and amt not in ("N/A", "") else None
        if ex:
            out.append({"ex": ex, "pay": pay, "amount": amt, "source": "nasdaq"})
    return out


def _us(s):
    if not s or s == "N/A":
        return None
    try:
        return datetime.strptime(s, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None

# ----------------------------------------------------------------------------- main

def main(out_path=HERE / "site" / "data.json"):
    today = datetime.now(timezone.utc).date()
    markets = {m["hl"]: m for m in json.load(open(HERE / "markets.json"))}
    live = hl_universe()
    y = Yahoo()
    result, excluded, unmapped = [], [], []
    for sym in live:
        m = markets.get(sym)
        if not m:
            unmapped.append(sym); continue
        if m["type"] not in DIVIDEND_TYPES or not m.get("yahoo"):
            excluded.append({"hl": sym, "type": m["type"]}); continue
        errors = []
        hist, price, currency = [], None, None
        try:
            hist, price, currency = y.chart(m["yahoo"])
        except Exception as e:
            errors.append(f"yahoo chart: {e}")
        cal = None
        try:
            cal = y.calendar(m["yahoo"])
        except Exception as e:
            errors.append(f"yahoo calendar: {e}")
        nrows = []
        if m.get("nasdaq"):
            try:
                nrows = nasdaq_rows(m["ticker"], m["nasdaq"])
            except Exception as e:
                errors.append(f"nasdaq: {e}")
        # Nasdaq rows (and Yahoo's latest ex/pay pair) carry pay dates; enrich history so the pay offset can be learned.
        by_ex = {r["ex"]: r.get("pay") for r in nrows}
        if cal and cal.get("ex") and cal.get("pay"):
            by_ex.setdefault(cal["ex"], cal["pay"])
        for h in hist:
            if h["ex"] in by_ex and by_ex[h["ex"]]:
                h["pay"] = by_ex[h["ex"]]
        fallback = hist[-1]["amount"] if hist else None
        declared = merge_declared(nrows, cal, today, fallback_amount=fallback)
        rec = assemble(m, hist, price, currency, declared, today)
        rec["errors"] = errors
        result.append(rec)
        print(f"{sym:9s} {rec['status']:9s} next={rec['next']['ex'] if rec['next'] else '-':10s} freq={rec['frequency']} {('ERR ' + '; '.join(errors)) if errors else ''}", file=sys.stderr)
        time.sleep(0.3)
    result.sort(key=lambda r: (r["next"] is None, r["next"]["ex"] if r["next"] else "", r["hl"]))
    data = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "today": today.isoformat(),
            "dex": "xyz", "markets": result, "excluded": excluded, "unmapped": unmapped,
            "counts": {"live": len(live), "dividend_tracked": len(result), "excluded": len(excluded), "unmapped": len(unmapped)}}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=0))
    print(f"wrote {out_path} ({len(result)} markets, {len(excluded)} excluded, {len(unmapped)} unmapped)", file=sys.stderr)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "site" / "data.json")
