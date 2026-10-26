"""Evidence tools over the gold layer. Every tool returns plain JSON-able dicts with status 'ok' or 'no_data', so an LLM
(or a test stub) can only state what a tool returned. The same functions back the PydanticAI agent and the MCP server."""
from __future__ import annotations

import re
from pathlib import Path

import duckdb

from .. import lineage as L

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _r(x, n=4):
    if x is None or (isinstance(x, float) and x != x):
        return None
    return round(float(x), n) if isinstance(x, (float, int)) and not isinstance(x, bool) else x


def _clean(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            v = v.isoformat()[:10]
        elif isinstance(v, float):
            v = _r(v)
        elif hasattr(v, "item"):
            v = v.item()
            v = _r(v) if isinstance(v, float) else v
        out[k] = v
    return out


class Toolbox:
    def __init__(self, db_path: str | Path, raw_dir: str | Path):
        self.db_path, self.raw_dir = str(db_path), Path(raw_dir)
        self.con = duckdb.connect(self.db_path, read_only=True)
        self.first_date, self.last_date = [str(x) for x in self.con.execute("select min(position_date), max(position_date) from gold.fact_nav").fetchone()]

    # -- helpers -------------------------------------------------------------------------------------------------------
    def _bad_date(self, date: str):
        if not DATE.match(date or ""):
            return {"status": "no_data", "detail": f"date {date!r} is not YYYY-MM-DD"}
        if not (self.first_date <= date <= self.last_date):
            return {"status": "no_data", "detail": f"date {date} is outside the data range {self.first_date} to {self.last_date}"}
        return None

    def _fund_ok(self, fund_id: str):
        n = self.con.execute("select count(*) from gold.dim_fund where fund_id=?", [fund_id]).fetchone()[0]
        return None if n else {"status": "no_data", "detail": f"unknown fund {fund_id!r}"}

    # -- tools ---------------------------------------------------------------------------------------------------------
    def explain_break(self, fund_id: str, security_id: str, date: str) -> dict:
        """Explain a custodian-vs-OMS position break for one fund, security and date: both quantities, the difference, the rule-based
        cause hint, the trade that explains it (if any), and how long the break has lasted."""
        for chk in (self._fund_ok(fund_id), self._bad_date(date)):
            if chk:
                return chk
        pos = self.con.execute("select oms_qty, custodian_qty, qty_diff, is_break from gold.fact_position where fund_id=? and security_id=? and position_date=?",
                               [fund_id, security_id, date]).fetchdf()
        if pos.empty:
            return {"status": "no_data", "detail": f"no position for {fund_id}/{security_id} on {date}"}
        p = pos.iloc[0]
        if not p.is_break:
            return {"status": "ok", "is_break": False, "fund_id": fund_id, "security_id": security_id, "date": date,
                    "oms_qty": _r(p.oms_qty), "custodian_qty": _r(p.custodian_qty), "qty_diff": 0.0}
        b = self.con.execute("select * from gold.recon_position_breaks where fund_id=? and security_id=? and position_date=?", [fund_id, security_id, date]).fetchdf().iloc[0]
        ep = self.con.execute("select min(position_date), max(position_date), count(*) from gold.recon_position_breaks where fund_id=? and security_id=? and episode=?",
                              [fund_id, security_id, int(b.episode)]).fetchone()
        out = dict(status="ok", is_break=True, fund_id=fund_id, security_id=security_id, date=date, oms_qty=_r(b.oms_qty), custodian_qty=_r(b.custodian_qty),
                   qty_diff=_r(b.qty_diff), cause_hint=b.cause_hint, break_first_date=str(ep[0]), break_last_date=str(ep[1]), break_business_days=int(ep[2]),
                   still_open=str(ep[1]) == self.last_date)
        if b.candidate_trade_id:
            t = self.con.execute("select trade_id, side, quantity, settle_date, src_row_id from gold.fact_trade where trade_id=?", [b.candidate_trade_id]).fetchdf().iloc[0]
            out["candidate_trade"] = _clean(t.to_dict())
        dups = self.con.execute("select count(*) from gold.fact_trade where fund_id=? and security_id=? and n_source_copies>1", [fund_id, security_id]).fetchone()[0]
        out["replayed_trades_removed_for_this_position"] = int(dups)
        idc = self.con.execute("select detail from silver.dq_findings where finding_type='identifier_change' and security_id=?", [security_id]).fetchall()
        out["identifier_change_on_record"] = bool(idc)
        return out

    def check_price_staleness(self, security_id: str, date: str) -> dict:
        """State whether a security's price on a date came from the vendor, was carried forward because it was missing, or is stale
        (unchanged for several days), with the source row."""
        chk = self._bad_date(date)
        if chk:
            return chk
        r = self.con.execute("select close_price, is_carried, is_stale, src_row_id from gold.fact_price where security_id=? and date_day=?", [security_id, date]).fetchdf()
        if r.empty:
            return {"status": "no_data", "detail": f"no price record for {security_id} on {date}"}
        p = r.iloc[0]
        out = dict(status="ok", security_id=security_id, date=date, close_price=_r(p.close_price), price_status="carried" if p.is_carried else "stale" if p.is_stale else "ok",
                   source_row=p.src_row_id)
        if p.is_carried:
            out["note"] = "vendor sent no price for this date; last available close carried forward"
        if p.is_stale:
            d = self.con.execute("select detail from silver.dq_findings where finding_type='stale_price' and security_id=? and as_of_date=?", [security_id, date]).fetchone()
            out["stale_detail"] = d[0] if d else None
            n = self.con.execute("select count(*) from silver.dq_findings where finding_type='stale_price' and security_id=?", [security_id]).fetchone()[0]
            out["stale_run_days_on_record"] = int(n)
        return out

    def summarise_nav_move(self, fund_id: str, date: str) -> dict:
        """Decompose a fund's NAV change from the previous business day into price, FX, trade and cash effects, state the
        difference to the administrator NAV, and list any data faults touching that day."""
        for chk in (self._fund_ok(fund_id), self._bad_date(date)):
            if chk:
                return chk
        days = [str(x[0]) for x in self.con.execute("select distinct position_date from gold.fact_nav order by 1").fetchall()]
        i = days.index(date) if date in days else -1
        if i <= 0:
            return {"status": "no_data", "detail": f"no previous business day for {date}"}
        d0 = days[i - 1]
        q = "select security_id, oms_qty, price, fx_rate, mv_gbp, price_is_carried, price_is_stale, fx_is_carried from gold.fact_nav_component where fund_id=? and position_date=?"
        a = self.con.execute(q, [fund_id, d0]).fetchdf().set_index("security_id")
        b = self.con.execute(q, [fund_id, date]).fetchdf().set_index("security_id")
        div = self.con.execute("select security_id, price_divisor from gold.dim_security where is_current").fetchdf().set_index("security_id").price_divisor
        j = a.join(b, how="outer", lsuffix="0", rsuffix="1")
        j["price0"] = j.price0.fillna(j.price1); j["price1"] = j.price1.fillna(j.price0)
        j["fx_rate0"] = j.fx_rate0.fillna(j.fx_rate1); j["fx_rate1"] = j.fx_rate1.fillna(j.fx_rate0)
        j["oms_qty0"] = j.oms_qty0.fillna(0); j["oms_qty1"] = j.oms_qty1.fillna(0)
        j["div"] = div.reindex(j.index).values
        price_eff = (j.oms_qty0 * (j.price1 - j.price0) * j.fx_rate0 / j["div"])
        fx_eff = (j.oms_qty0 * j.price1 * (j.fx_rate1 - j.fx_rate0) / j["div"])
        trade_eff = ((j.oms_qty1 - j.oms_qty0) * j.price1 * j.fx_rate1 / j["div"])
        n0 = self.con.execute("select * from gold.fact_nav where fund_id=? and position_date=?", [fund_id, d0]).fetchdf().iloc[0]
        n1 = self.con.execute("select * from gold.fact_nav where fund_id=? and position_date=?", [fund_id, date]).fetchdf().iloc[0]
        cash_eff = n1.cash_gbp - n0.cash_gbp
        top = (price_eff + fx_eff).abs().sort_values(ascending=False).head(3).index
        issues = []
        if int(n1.n_carried_prices) > 0: issues.append("carried_price")
        if int(n1.n_stale_prices) > 0: issues.append("stale_price")
        if bool(n1.stale_fx_day): issues.append("stale_fx")
        return dict(status="ok", fund_id=fund_id, date=date, previous_date=d0, nav_gbp=_r(n1.nav_gbp, 2), previous_nav_gbp=_r(n0.nav_gbp, 2),
                    nav_change_gbp=_r(n1.nav_gbp - n0.nav_gbp, 2), price_effect_gbp=_r(price_eff.sum(), 2), fx_effect_gbp=_r(fx_eff.sum(), 2),
                    trade_effect_gbp=_r(trade_eff.sum(), 2), cash_effect_gbp=_r(cash_eff, 2),
                    residual_gbp=_r((n1.nav_gbp - n0.nav_gbp) - (price_eff.sum() + fx_eff.sum() + trade_eff.sum() + cash_eff), 2),
                    top_price_fx_movers=[dict(security_id=s, effect_gbp=_r(price_eff[s] + fx_eff[s], 2)) for s in top],
                    administrator_nav_gbp=_r(n1.admin_nav_gbp, 2), difference_to_administrator_bps=_r(n1.diff_bps, 3), breaches_tolerance=bool(n1.is_breach),
                    n_carried_prices=int(n1.n_carried_prices), n_stale_prices=int(n1.n_stale_prices), data_issues=issues)

    def trace_lineage(self, entity: str, trade_id: str | None = None, fund_id: str | None = None, date: str | None = None) -> dict:
        """Trace a gold record back to the raw source rows. entity='trade' needs trade_id; entity='nav' needs fund_id and date."""
        if entity == "trade" and trade_id:
            return L.trace_trade(self.con, self.raw_dir, trade_id)
        if entity == "nav" and fund_id and date:
            for chk in (self._fund_ok(fund_id), self._bad_date(date)):
                if chk:
                    return chk
            return L.trace_nav(self.con, self.raw_dir, fund_id, date)
        return {"status": "no_data", "detail": "entity must be 'trade' (trade_id) or 'nav' (fund_id, date)"}

    def summarise_private_fund(self, investor_id: str, pm_fund_id: str) -> dict:
        """Performance of one private-fund commitment: paid-in, distributions, NAV at the reporting date and how old the
        statement behind it is, unfunded, DPI, RVPI, TVPI and IRR; for a secondary interest, the GP-reported basis as well;
        whether the GP capital account reconciles to our notices; and the data-quality findings on it."""
        chk = self._fund_ok(investor_id)
        if chk:
            return chk
        r = self.con.execute("select * from gold.mart_pm_performance where investor_id=? and pm_fund_id=?", [investor_id, pm_fund_id]).fetchdf()
        if r.empty:
            return {"status": "no_data", "detail": f"no commitment by {investor_id} to private fund {pm_fund_id!r}"}
        m = r.iloc[0]
        out = dict(status="ok", investor_id=investor_id, pm_fund_id=pm_fund_id, fund_name=m.fund_name, strategy=m.strategy, vintage=int(m.vintage),
                   currency=m.currency, origin=m.origin, commitment=_r(m.commitment, 2), paid_in=_r(m.paid_in, 2), distributions=_r(m.distributions, 2),
                   nav_asof=_r(m.nav_asof, 2), last_statement_date=str(m.last_statement_date)[:10], statement_lag_days=int(m.statement_lag_days),
                   nav_is_rolled_forward=bool(m.is_rolled_forward), unfunded=_r(m.unfunded, 2), dpi=_r(m.dpi), rvpi=_r(m.rvpi), tvpi=_r(m.tvpi),
                   irr=_r(m.irr, 6), irr_pct=_r(float(m.irr) * 100, 2), is_reconciled=bool(m.is_reconciled), nav_gbp=_r(m.nav_gbp, 2))
        if m.origin == "secondary":
            tr = self.con.execute("select purchase_price, transfer_date from silver.silver_pm_transfers where buyer_investor_id=? and pm_fund_id=?",
                                  [investor_id, pm_fund_id]).fetchone()
            out.update(gp_basis_tvpi=_r(m.gp_basis_tvpi), purchase_price=_r(tr[0], 2), transfer_date=str(tr[1]), basis="purchase_price")
        if not m.is_reconciled:
            b = self.con.execute("select period_end, contributions_diff, distributions_diff, cause_hint from gold.pm_recon_capital_account "
                                 "where investor_id=? and pm_fund_id=? and is_break order by period_end", [investor_id, pm_fund_id]).fetchdf()
            out["capital_account_breaks"] = [_clean(x) for x in b.to_dict("records")]
        f = self.con.execute("select finding_type, entity_key from silver.dq_findings where fund_id=? and security_id=? order by 1, 2",
                             [investor_id, pm_fund_id]).fetchall()
        out["findings"] = [dict(finding_type=a, entity_key=k) for a, k in f]
        return out

    def list_findings(self, finding_type: str, limit: int = 10) -> dict:
        """List data-quality findings of one type (duplicate_trade, missing_price, stale_price, stale_fx, identifier_change, identifier_conflict, orphan_claim, ...)."""
        df = self.con.execute("select finding_type, entity_key, security_id, as_of_date, detail from silver.dq_findings where finding_type=? order by entity_key limit ?",
                              [finding_type, int(limit)]).fetchdf()
        n = self.con.execute("select count(*) from silver.dq_findings where finding_type=?", [finding_type]).fetchone()[0]
        if not n:
            return {"status": "no_data", "detail": f"no findings of type {finding_type!r}"}
        return {"status": "ok", "finding_type": finding_type, "total": int(n), "examples": [_clean(r) for r in df.to_dict("records")]}


TOOL_NAMES = ["explain_break", "check_price_staleness", "summarise_nav_move", "trace_lineage", "summarise_private_fund", "list_findings"]
