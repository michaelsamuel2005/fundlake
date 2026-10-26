import pytest

from fundlake import lineage as L
from fundlake.privacy import AccessDenied, PERSONAL_COLUMNS, client_view
from conftest import RAW


def test_every_gold_trade_traces_to_the_raw_row_it_came_from(con):
    rows = con.execute("select trade_id, src_row_id, quantity, price from gold.fact_trade order by trade_id").fetchall()
    for tid, src, qty, px in rows[::7]:
        r = L.raw_row(RAW, src)
        assert r["trade_id"] == tid and float(r["quantity"]) == qty and float(r["price"]) == px


def test_price_used_in_nav_equals_the_vendor_row(con):
    rows = con.execute("select security_id, position_date, price, price_src_row_id from gold.fact_nav_component where not price_is_carried order by 1,2").fetchall()
    for sid, d, price, src in rows[::250]:
        r = L.raw_row(RAW, src)
        assert r["price_date"] == str(d) and float(r["close_price"]) == pytest.approx(price)


def test_carried_price_points_to_an_earlier_vendor_row(con, key):
    m = key["faults"]["missing_price"][0]
    src, price, d = con.execute("select src_row_id, close_price, date_day from gold.fact_price where security_id=? and date_day=?", [m["security_id"], m["date"]]).fetchone()
    r = L.raw_row(RAW, src)
    assert r["price_date"] < m["date"] and float(r["close_price"]) == pytest.approx(price)


def test_nav_trace_reaches_administrator_row(con):
    t = L.trace_nav(con, RAW, "F02", "2026-08-03")
    assert t["status"] == "ok" and t["administrator_row"]["file"] == "custodian_cash_nav.csv" and t["administrator_row"]["fund_id"] == "F02"
    assert t["top_components"][0]["trade_ids"]


def test_client_view_is_minimised(con):
    df = con.execute("select * from gold.client_policy_view").fetchdf()
    assert not (PERSONAL_COLUMNS & set(df.columns))
    text = df.astype(str).to_csv()
    assert "example.invalid" not in text and "(synthetic)" not in text
    assert set(df.age_band) <= {"under 40", "40-59", "60+"} and df.postcode_district.str.len().max() <= 4


def test_client_key_is_stable_and_not_the_client_id(con):
    k = con.execute("select p.client_key, s.client_id from gold.dim_policy p join silver.silver_policies s using (policy_id)").fetchdf()
    assert not (k.client_key == k.client_id).any()
    assert (k.groupby("client_id").client_key.nunique() == 1).all()


def test_row_level_security_by_role(con):
    full = client_view(con, "data_steward")
    ldn = client_view(con, "analyst_london")
    north = client_view(con, "analyst_north")
    assert len(full) == 400 and set(ldn.region) <= {"London", "South East"} and set(north.region) <= {"North West", "Scotland"}
    assert not (set(ldn.policy_id) & set(north.policy_id))
    with pytest.raises(AccessDenied):
        client_view(con, "intern")
