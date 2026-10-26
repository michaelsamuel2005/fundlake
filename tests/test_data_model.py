

def q(con, sql, *a):
    return con.execute(sql, list(a)).fetchall()


def test_star_schema_foreign_keys(con):
    assert q(con, "select count(*) from gold.fact_trade t left join gold.dim_security s using (security_sk) where s.security_sk is null")[0][0] == 0
    assert q(con, "select count(*) from gold.fact_trade t left join gold.dim_fund f using (fund_id) where f.fund_id is null")[0][0] == 0
    assert q(con, "select count(*) from gold.fact_trade t left join gold.dim_counterparty c using (counterparty_id) where c.counterparty_id is null")[0][0] == 0
    assert q(con, "select count(*) from gold.fact_claim c left join gold.dim_policy p using (policy_id) where p.policy_id is null")[0][0] == 0


def test_security_master_is_type_2(con, key):
    rows = q(con, "select security_id, count(*) from gold.dim_security group by 1 having count(*) > 1 order by 1")
    assert {r[0] for r in rows} == {h["security_id"] for h in key["attribute_history"]}
    sid = key["attribute_history"][0]["security_id"]
    old, new = key["attribute_history"][0]["old"], key["attribute_history"][0]["new"]
    r = q(con, "select rating, valid_from, valid_to, is_current from gold.dim_security where security_id=? order by valid_from", sid)
    assert [x[0] for x in r] == [old, new] and [x[3] for x in r] == [False, True]
    assert str(r[0][2]) == "2026-08-19" and str(r[1][1]) == "2026-08-20"


def test_asset_listing_uses_rating_as_of_the_listing_date(con, key):
    # the listing date is the last day, so any downgraded bond held by the insurance account must show the NEW rating
    new = {h["security_id"]: h["new"] for h in key["attribute_history"]}
    for sid, rating in q(con, "select asset_id, rating from gold.si_asset_listing where asset_id in ({})".format(",".join("?" * len(new))), *new):
        assert rating == new[sid]


def test_as_of_join_matters_for_identifier_change(con, key):
    """A join on the CURRENT identifier loses the custodian rows reported under the retired ISIN; the as-of join loses none."""
    c = key["faults"]["identifier_change"][0]
    naive_unmatched = q(con, """select count(*) from silver.silver_custodian_positions p
                                left join silver.silver_identifiers x on x.id_type='ISIN' and x.id_value=p.isin and x.valid_to = date '9999-12-31'
                                where x.security_id is null""")[0][0]
    asof_unmatched = q(con, "select count(*) from silver.silver_custodian_positions where security_id is null")[0][0]
    assert naive_unmatched > 0 and asof_unmatched == 0
    assert q(con, "select count(distinct isin) from silver.silver_custodian_positions where security_id=?", c["security_id"])[0][0] == 2


def test_replayed_trades_do_not_create_false_breaks_or_double_count(con, key):
    dup_ids = [x["trade_id"] for x in key["faults"]["duplicate_trade"]]
    assert q(con, f"select count(*) from gold.fact_trade where trade_id in ({','.join('?' * len(dup_ids))})", *dup_ids)[0][0] == len(dup_ids)
    assert q(con, "select max(n_source_copies) from gold.fact_trade")[0][0] == 2
    # clean (non-broken) positions agree exactly with the custodian although 32 trades were replayed
    assert q(con, "select count(*) from gold.fact_position where not is_break and abs(qty_diff) > 1e-6")[0][0] == 0


def test_nav_equals_components_plus_cash(con):
    bad = q(con, """select count(*) from gold.fact_nav n join (select fund_id, position_date, sum(mv_gbp) s from gold.fact_nav_component group by 1,2) c
                    using (fund_id, position_date) where abs(n.nav_gbp - (c.s + n.cash_gbp)) > 0.01""")[0][0]
    assert bad == 0


def test_every_nav_breach_is_explained_by_a_flagged_fault(con):
    assert q(con, "select count(*) from gold.fact_nav where is_breach and n_carried_prices=0 and n_stale_prices=0 and not stale_fx_day")[0][0] == 0
    assert q(con, "select count(*) from gold.fact_nav where is_breach")[0][0] > 0


def test_clean_days_agree_with_the_administrator_to_the_penny(con):
    worst = q(con, "select max(abs(diff_gbp)) from gold.fact_nav where n_carried_prices=0 and n_stale_prices=0 and not stale_fx_day")[0][0]
    assert worst < 1.0


def test_price_basis_applied_to_bonds(con):
    r = q(con, """select c.mv_gbp, c.oms_qty*c.price/100*c.fx_rate from gold.fact_nav_component c join gold.dim_security s
                  on s.security_id=c.security_id and c.position_date between s.valid_from and s.valid_to where s.asset_class='bond' limit 20""")
    assert r and all(abs(a - b) < 1e-6 for a, b in r)


def test_claims_marts_exclude_invalid_claims(con, key):
    bad = {x["claim_id"] for k in ("claim_before_inception", "orphan_claim", "reported_before_loss") for x in key["faults"][k]}
    ids = {r[0] for r in q(con, "select claim_id from gold.fact_claim")}
    assert not (bad & ids) and q(con, "select count(*) from gold.fact_claim")[0][0] == 266
    assert q(con, "select count(*) from silver.silver_claims where invalid_reason is not null")[0][0] == 11


def test_insurance_listing_is_labelled_illustrative(con):
    r = q(con, "select count(*), sum(weight), min(disclaimer) from gold.si_asset_listing")[0]
    assert r[0] == 20 and abs(r[1] - 1) < 1e-9 and "illustrative" in r[2]
