"""Private markets: every reported figure checked against the answer key, which computes it independently in Python
(including the IRR, by bisection) from the cashflows a correct pipeline should see."""
import pandas as pd
import pytest

from fundlake.synth_pm import xirr
from conftest import RAW


@pytest.fixture(scope="module")
def perf(con):
    return con.execute("select * from gold.mart_pm_performance").fetchdf()


def test_every_commitment_matches_the_answer_key(perf, key):
    exp = key["pm_expected"]
    assert len(perf) == len(exp) == 10
    for _, r in perf.iterrows():
        e = exp[f"{r.investor_id}:{r.pm_fund_id}"]
        assert r.paid_in == pytest.approx(e["paid_in"], abs=0.01)
        assert r.distributions == pytest.approx(e["distributions"], abs=0.01)
        assert r.nav_asof == pytest.approx(e["nav_asof"], abs=0.01)
        for m in ("dpi", "rvpi", "tvpi"):
            assert r[m] == pytest.approx(e[m], abs=1e-9)
        assert r.irr == pytest.approx(e["irr"], abs=1e-6)
        assert bool(r.is_reconciled) == e["is_reconciled"]


def test_sql_irr_agrees_with_python_xirr_on_the_same_flows(con):
    cf = con.execute("select investor_id, pm_fund_id, value_date, signed_amount from gold.fact_pm_cashflow").fetchdf()
    nav = con.execute("select investor_id, pm_fund_id, as_of_date, nav_asof from gold.pm_nav_asof").fetchdf()
    irr = con.execute("select investor_id, pm_fund_id, irr from gold.pm_irr").fetchdf().set_index(["investor_id", "pm_fund_id"]).irr
    for (inv, fid), g in cf.groupby(["investor_id", "pm_fund_id"]):
        n = nav[(nav.investor_id == inv) & (nav.pm_fund_id == fid)].iloc[0]
        flows = [(pd.Timestamp(d).date(), float(a)) for d, a in zip(g.value_date, g.signed_amount)] + [(pd.Timestamp(n.as_of_date).date(), float(n.nav_asof))]
        assert irr[(inv, fid)] == pytest.approx(xirr(flows), abs=1e-6)


def test_secondary_is_measured_on_purchase_price_with_gp_basis_alongside(perf, key, con):
    r = perf[perf.origin == "secondary"].iloc[0]
    t = key["pm_transfer"]
    calls = con.execute("select sum(amount) from gold.fact_pm_cashflow where investor_id=? and pm_fund_id=? and flow_type='contribution'",
                        [r.investor_id, r.pm_fund_id]).fetchone()[0]
    assert r.paid_in == pytest.approx(t["purchase_price"] + calls, abs=0.01)
    assert r.gp_basis_tvpi == pytest.approx(key["pm_expected"]["F04:PM01"]["gp_basis_tvpi"], abs=1e-9)
    assert r.tvpi > r.gp_basis_tvpi                      # bought at a discount: the buyer's multiple is higher
    pre = con.execute("select count(*) from gold.fact_pm_cashflow where pm_fund_id=? and value_date <= ? and flow_type <> 'secondary_purchase'",
                      [r.pm_fund_id, t["transfer_date"]]).fetchone()[0]
    assert pre == 0                                      # none of the seller's cashflows are counted as the buyer's


def test_missing_notice_is_found_by_capital_account_reconciliation(con, key):
    m = key["pm_missing_notice"]
    b = con.execute("select period_end, contributions_diff, distributions_diff, cause_hint from gold.pm_recon_capital_account where is_break order by 1").fetchdf()
    assert len(b) == 4 and (b.cause_hint == "call_notice_missing_from_our_feed").all()
    assert b.contributions_diff.round(2).eq(round(m["amount"], 2)).all() and (b.distributions_diff.abs() < 0.01).all()
    assert str(b.period_end.min())[:10] >= m["value_date"]


def test_resent_and_corrected_notices_create_no_false_breaks(con, key):
    pairs = con.execute("select distinct investor_id || ':' || pm_fund_id from gold.pm_recon_capital_account where is_break").fetchall()
    assert pairs == [("F04:PM05",)]
    dups = [x["notice_id"] for x in key["faults"]["pm_duplicate_notice"]]
    assert con.execute(f"select count(*) from gold.fact_pm_cashflow where source_id in ({','.join('?' * len(dups))})", dups).fetchone()[0] == len(dups)
    raw = pd.read_csv(RAW / "pm_notice_lines.csv", dtype=str)
    assert raw.duplicated(["notice_id", "line_no"]).sum() > 0     # the resent lines really are in the feed


def test_nav_is_rolled_forward_from_a_lagged_statement(con, key):
    n = con.execute("select * from gold.pm_nav_asof").fetchdf()
    assert (n.statement_lag_days == 92).all() and n.is_rolled_forward.all()
    assert ((n.last_reported_nav + n.calls_since - n.distributions_since - n.nav_asof).abs() < 0.01).all()


def test_cost_of_the_lag_is_measured_against_the_hidden_true_nav(con, key):
    n = con.execute("select investor_id || ':' || pm_fund_id as k, nav_asof from gold.pm_nav_asof").fetchdf().set_index("k").nav_asof
    err = pd.Series({k: n[k] / v - 1 for k, v in key["pm_true_nav_asof"].items()})
    assert err.abs().max() > 0.01                        # a quarter of unobserved valuation change is material
    assert err.abs().mean() < 0.10


def test_missing_statement_rolled_forward_and_stale_marks_flagged(con, key):
    m = key["faults"]["pm_missing_nav_statement"][0]
    r = con.execute("select nav_source from gold.fact_pm_nav where investor_id=? and pm_fund_id=? and period_end=?",
                    [m["investor_id"], m["pm_fund_id"], m["period_end"]]).fetchone()
    assert r == ("rolled_forward",)
    stale = {str(d)[:10] for (d,) in con.execute("select period_end from gold.fact_pm_nav where is_stale").fetchall()}
    assert stale == {x["period_end"] for x in key["faults"]["pm_stale_nav"]}


def test_gbp_values_use_the_reporting_date_rate(con, perf):
    fx = dict(con.execute("select pair, rate from gold.fact_fx where date_day = date '2026-09-30'").fetchall())
    for _, r in perf.iterrows():
        pair = {"EUR": "EURGBP", "USD": "USDGBP", "GBP": "GBPGBP"}[r.currency]
        assert r.nav_gbp == pytest.approx(r.nav_asof * fx[pair], rel=1e-12)


def test_over_call_shows_as_negative_unfunded(perf, key):
    oc = key["faults"]["pm_over_call"][0]
    r = perf[(perf.investor_id == oc["investor_id"]) & (perf.pm_fund_id == oc["pm_fund_id"])].iloc[0]
    assert r.unfunded < 0 and (perf.drop(r.name).unfunded > 0).all()
