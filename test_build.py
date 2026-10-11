from datetime import date
import build as b

D = date.fromisoformat

def test_frequency_quarterly():
    ex = [D("2025-02-10"), D("2025-05-12"), D("2025-08-11"), D("2025-11-10"), D("2026-02-09")]
    assert b.infer_frequency(ex) == ("quarterly", 91)

def test_frequency_monthly_and_annual_bounds():
    monthly = [D("2026-06-30"), D("2026-07-31"), D("2026-08-31"), D("2026-09-30")]
    assert b.infer_frequency(monthly) == ("monthly", 30)
    annual = [D("2023-12-22"), D("2024-12-20"), D("2025-12-22")]
    assert b.infer_frequency(annual)[0] == "annual"
    semi = [D("2025-02-10"), D("2025-07-28"), D("2026-02-10"), D("2026-07-28")]
    assert b.infer_frequency(semi)[0] == "semiannual"

def test_frequency_irregular_or_unknown():
    assert b.infer_frequency([D("2026-01-01")]) == (None, None)
    assert b.infer_frequency([D("2025-01-01"), D("2025-03-01"), D("2025-04-15"), D("2026-01-01")])[0] == "irregular"

def test_frequency_uses_recent_intervals_only():
    # old annual pattern then switched to monthly: last 6 gaps decide
    ex = [D("2023-01-01"), D("2024-01-01")] + [D(f"2026-0{m}-15") for m in range(1, 8)]
    assert b.infer_frequency(ex)[0] == "monthly"

def test_median_pay_offset():
    hist = [{"ex": "2026-08-10", "pay": "2026-08-13"}, {"ex": "2026-05-11", "pay": "2026-05-14"}, {"ex": "2026-02-09", "pay": None}]
    assert b.median_pay_offset(hist) == 3
    assert b.median_pay_offset([{"ex": "2026-02-09", "pay": None}]) is None

def test_merge_declared_dedupes_and_drops_past():
    today = D("2026-10-11")
    nasdaq = [{"ex": "2026-11-19", "pay": "2026-12-10", "amount": 0.98, "source": "nasdaq"},
              {"ex": "2026-08-20", "pay": "2026-09-10", "amount": 0.91, "source": "nasdaq"}]
    yahoo = {"ex": "2026-11-19", "pay": "2026-12-10", "amount": None, "source": "yahoo"}
    out = b.merge_declared(nasdaq, yahoo, today)
    assert [e["ex"] for e in out] == ["2026-11-19"]
    assert out[0]["source"] == "nasdaq"
    # yahoo-only future date is kept, amount filled from fallback
    out = b.merge_declared([], {"ex": "2026-12-10", "pay": "2027-01-07", "amount": None, "source": "yahoo"}, today, fallback_amount=3.77)
    assert out == [{"ex": "2026-12-10", "pay": "2027-01-07", "amount": 3.77, "source": "yahoo", "status": "declared"}]

def test_project_from_last_known_until_horizon():
    today = D("2026-10-11")
    declared = [{"ex": "2026-11-19", "pay": "2026-12-10", "amount": 0.98, "source": "nasdaq", "status": "declared"}]
    est = b.project(last_ex=D("2026-08-20"), interval=91, amount=0.98, pay_offset=21, today=today, declared=declared)
    exs = [e["ex"] for e in est]
    assert exs[0] == "2027-02-18"          # 2026-11-19 + 91
    assert all(D(x) <= today.replace(year=2027) for x in exs)
    assert len(exs) == 3                   # Feb, May, Aug 2027 within 365 days
    assert est[0] == {"ex": "2027-02-18", "pay": "2027-03-11", "amount": 0.98, "source": "estimate", "status": "estimated"}

def test_project_skips_when_irregular_or_no_history():
    today = D("2026-10-11")
    assert b.project(last_ex=None, interval=91, amount=1, pay_offset=None, today=today, declared=[]) == []
    assert b.project(last_ex=D("2026-01-01"), interval=None, amount=1, pay_offset=None, today=today, declared=[]) == []

def test_project_catches_up_when_history_stale():
    # last ex long ago: skip dates already in the past, start from the first future one
    today = D("2026-10-11")
    est = b.project(last_ex=D("2025-12-22"), interval=365, amount=1.1, pay_offset=None, today=today, declared=[])
    assert [e["ex"] for e in est] == ["2026-12-22"]
    assert est[0]["pay"] is None

def test_assemble_market_classifies_status():
    today = D("2026-10-11")
    m = {"hl": "TSLA", "type": "stock", "yahoo": "TSLA", "name": "Tesla", "exchange": "Nasdaq", "ticker": "TSLA"}
    out = b.assemble(m, history=[], price=400.0, currency="USD", declared=[], today=today)
    assert out["status"] == "none" and out["events"] == [] and out["next"] is None
    hist = [{"ex": "2026-08-10", "pay": "2026-08-13", "amount": 0.27}, {"ex": "2026-05-11", "pay": "2026-05-14", "amount": 0.27},
            {"ex": "2026-02-09", "pay": "2026-02-12", "amount": 0.26}, {"ex": "2025-11-10", "pay": "2025-11-13", "amount": 0.26}]
    out = b.assemble({**m, "hl": "AAPL"}, history=hist, price=270.0, currency="USD", declared=[], today=today)
    assert out["status"] == "estimated" and out["frequency"] == "quarterly"
    assert out["next"]["ex"] == "2026-11-09" and out["next"]["status"] == "estimated"
    assert abs(out["next_gap_pct"] - 0.27 / 270.0 * 100) < 1e-9
    assert abs(out["ttm_yield_pct"] - (0.27 + 0.27 + 0.26 + 0.26) / 270.0 * 100) < 1e-9

def test_project_rolls_weekend_dates_to_monday():
    today = D("2026-10-11")
    # 2026-10-11 is a Sunday; +91 lands on Sunday 2027-01-10 -> Monday 2027-01-11
    est = b.project(last_ex=D("2026-10-11"), interval=91, amount=1.0, pay_offset=None, today=today, declared=[])
    assert est[0]["ex"] == "2027-01-11"
    # anchor keeps stepping from the unrolled date so drift does not accumulate
    assert est[1]["ex"] == "2027-04-12"   # 2027-04-11 is a Sunday -> Monday


def test_frequency_semimonthly_like_strc():
    ex = [D("2026-07-31"), D("2026-08-14"), D("2026-08-31"), D("2026-09-15"), D("2026-09-30")]
    assert b.infer_frequency(ex) == ("semimonthly", 15)
