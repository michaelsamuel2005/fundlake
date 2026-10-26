"""Lineage: from a gold row back to the raw source rows it was built from, with the raw values re-read from the landed file."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def raw_row(raw_dir: Path, src_row_id: str) -> dict:
    """src_row_id is '<file>:<1-based data row>'. Re-read that row from the landed CSV."""
    if src_row_id is None or (isinstance(src_row_id, float) and src_row_id != src_row_id):
        return {"file": None, "row": None, "note": "no source row: base-currency rate fixed at 1.0"}
    fname, n = src_row_id.rsplit(":", 1)
    df = pd.read_csv(Path(raw_dir) / fname, dtype=str, skiprows=range(1, int(n)), nrows=1)
    return {"file": fname, "row": int(n), **df.iloc[0].where(df.iloc[0].notna(), None).to_dict()}


def trace_trade(con, raw_dir, trade_id: str) -> dict:
    r = con.execute("select trade_id, fund_id, security_id, side, quantity, price, trade_date, settle_date, n_source_copies, src_row_id "
                    "from gold.fact_trade where trade_id = ?", [trade_id]).fetchdf()
    if r.empty:
        return {"status": "no_data", "detail": f"trade {trade_id} not in gold.fact_trade"}
    row = r.iloc[0].to_dict()
    return {"status": "ok", "gold": row, "source": raw_row(raw_dir, row["src_row_id"])}


def trace_nav(con, raw_dir, fund_id: str, date: str, max_components: int = 5) -> dict:
    nav = con.execute("select * from gold.fact_nav where fund_id=? and position_date=?", [fund_id, date]).fetchdf()
    if nav.empty:
        return {"status": "no_data", "detail": f"no NAV for {fund_id} on {date}"}
    comp = con.execute("select * from gold.fact_nav_component where fund_id=? and position_date=? order by abs(mv_gbp) desc", [fund_id, date]).fetchdf()
    top = comp.head(max_components)
    items = []
    for _, c in top.iterrows():
        lp = con.execute("select trade_ids, trade_src_row_ids from gold.lineage_position where fund_id=? and security_id=? and position_date=?",
                         [fund_id, c.security_id, date]).fetchdf()
        items.append(dict(security_id=c.security_id, mv_gbp=float(c.mv_gbp), oms_qty=float(c.oms_qty),
                          price=raw_row(raw_dir, c.price_src_row_id), fx=raw_row(raw_dir, c.fx_src_row_id),
                          price_is_carried=bool(c.price_is_carried), trade_ids=lp.trade_ids.iloc[0].split(",") if len(lp) else []))
    admin = raw_row(raw_dir, nav.iloc[0].admin_src_row_id)
    return {"status": "ok", "nav_gbp": float(nav.iloc[0].nav_gbp), "n_components": int(len(comp)), "top_components": items, "administrator_row": admin}
