# hl-dividends

Upcoming dividends (declared + estimated) for every stock, ADR, ETF and preferred
listed as a trade.xyz perpetual on Hyperliquid, rendered as a static ex-dividend calendar.

- `build.py` — fetches the live xyz universe, Yahoo history/calendar and Nasdaq declared rows, writes `site/data.json`
- `markets.json` — xyz symbol → underlying mapping (regenerate with `gen_markets.py`, hand edits survive if `"manual": true`)
- `site/index.html` — the page; reads `data.json` next to it
- `.github/workflows/pages.yml` — daily build + GitHub Pages deploy

Local run:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q test_build.py
python3 build.py
python3 -m http.server -d site 8080
```

Deploy: push to `main`, then Settings → Pages → Source: GitHub Actions.
