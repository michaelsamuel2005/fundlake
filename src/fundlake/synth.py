"""Synthetic source systems with a planted answer key.

Six sources, all LABELLED SYNTHETIC (names are invented; identifiers carry valid check digits but are not issued):
  oms        trades from the order-management system (daily batches, one replayed feed)
  custodian  daily positions, cash and administrator NAV
  vendor     daily prices (keyed by ISIN) and FX rates
  secmaster  security master with identifier cross-reference and attribute history
  insurer    policy and claims extract (contains personal data fields)
  gp         private-markets notices and capital-account statements from general partners (see synth_pm.py)
Planted faults are recorded in answer_key.json so detection can be scored, not just demonstrated.
"""
from __future__ import annotations

import hashlib
import json
import string
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20261026
START, END = "2026-07-01", "2026-09-30"
HOLIDAYS = ["2026-08-31"]  # UK summer bank holiday
FUNDS = {
    "F01": "UK Equity Income (synthetic)",
    "F02": "Global Bond (synthetic)",
    "F03": "Multi-Asset (synthetic)",
    "F04": "Insurance General Account (synthetic)",
}
CCY_FX = {"GBP": None, "EUR": "EURGBP", "USD": "USDGBP"}
ADJ = ["Northfield", "Harbour", "Alder", "Kestrel", "Meridian", "Silverbeck", "Oakmoor", "Tidewater", "Brightwell", "Corvane",
       "Lindholm", "Ashgrove", "Redmere", "Stoneleigh", "Vantor", "Penhallow", "Quillon", "Rowan", "Sablecross", "Thornby"]
NOUN = ["Holdings", "Energy", "Foods", "Logistics", "Insurance", "Materials", "Networks", "Pharma", "Utilities", "Retail", "Capital", "Systems"]
SECTORS = ["Energy", "Financials", "Consumer", "Industrials", "Health", "Utilities", "Technology", "Materials"]
RATINGS = ["AAA", "AA", "A", "BBB", "BB"]


def _luhn_isin(body: str) -> str:
    digits = "".join(str(int(c, 36)) for c in body)
    total, dbl = 0, True
    for ch in reversed(digits):
        d = int(ch)
        if dbl:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
        dbl = not dbl
    return body + str((10 - total % 10) % 10)


def isin_valid(s: str) -> bool:
    return len(s) == 12 and s[:2].isalpha() and s == _luhn_isin(s[:11])


def sedol_check(body: str) -> str:
    w = [1, 3, 1, 7, 3, 9]
    tot = sum(w[i] * int(c, 36) for i, c in enumerate(body))
    return body + str((10 - tot % 10) % 10)


def sedol_valid(s: str) -> bool:
    return len(s) == 7 and s == sedol_check(s[:6])


def cusip_check(body: str) -> str:
    tot = 0
    for i, c in enumerate(body):
        v = int(c, 36) if c.isalnum() else 0
        if i % 2 == 1:
            v *= 2
        tot += v // 10 + v % 10
    return body + str((10 - tot % 10) % 10)


def cusip_valid(s: str) -> bool:
    return len(s) == 9 and s == cusip_check(s[:8])


def _alnum(rng, n, alphabet=string.digits + "BCDFGHJKLMNPQRSTVWXYZ"):
    return "".join(rng.choice(list(alphabet), n))


def business_days() -> pd.DatetimeIndex:
    d = pd.bdate_range(START, END)
    return d[~d.isin(pd.to_datetime(HOLIDAYS))]


def shift_bdays(days: pd.DatetimeIndex, d: pd.Timestamp, n: int):
    i = days.get_loc(d) + n
    return days[min(i, len(days) - 1)]


def build_securities(rng) -> pd.DataFrame:
    rows = []
    for i in range(60):
        kind = "equity" if i < 36 else "bond"
        if kind == "equity":
            ccy = ["GBP"] * 17 + ["USD"] * 10 + ["EUR"] * 9
            ccy = ccy[i]
            country = {"GBP": "GB", "USD": "US", "EUR": rng.choice(["DE", "FR", "NL"])}[ccy]
        else:
            j = i - 36
            ccy = ["GBP"] * 8 + ["EUR"] * 8 + ["USD"] * 8
            ccy = ccy[j]
            country = {"GBP": "GB", "USD": "US", "EUR": rng.choice(["DE", "FR", "NL"])}[ccy]
        name = f"{ADJ[i % len(ADJ)]} {NOUN[(i * 7) % len(NOUN)]}" + (" plc" if country == "GB" else " SA" if country in ("FR", "NL") else " AG" if country == "DE" else " Inc")
        if kind == "bond":
            name += f" {rng.choice([3.25, 4.0, 4.5, 5.125])}% {2028 + (i % 9)}"
        isin = _luhn_isin(country + _alnum(rng, 9))
        sedol = sedol_check(_alnum(rng, 6)) if country != "US" or rng.random() < 0.5 else None
        cusip = cusip_check(_alnum(rng, 8)) if country == "US" else None
        rows.append(dict(
            security_id=f"S{i + 1:03d}", name=name, asset_class=kind, currency=ccy, country=country,
            isin=isin, sedol=sedol, cusip=cusip, sector=SECTORS[i % len(SECTORS)] if kind == "equity" else "Fixed income",
            rating=None if kind == "equity" else str(rng.choice(RATINGS, p=[.3, .25, .25, .15, .05])),
            price_basis="per100" if kind == "bond" else "unit", settle_lag=1 if (kind == "bond" and ccy == "GBP") else 2,
        ))
    df = pd.DataFrame(rows)
    # US securities without a SEDOL still need one for the OMS: give every security a SEDOL
    for k in df.index[df.sedol.isna()]:
        df.at[k, "sedol"] = sedol_check(_alnum(rng, 6))
    return df


def price_paths(rng, sec: pd.DataFrame, days) -> pd.DataFrame:
    n = len(days)
    out = {}
    for _, s in sec.iterrows():
        if s.asset_class == "equity":
            p0, vol = rng.uniform(5, 60), 0.014
        else:
            p0, vol = rng.uniform(94, 106), 0.002
        r = rng.normal(0.0002, vol, n)
        out[s.security_id] = np.round(p0 * np.exp(np.cumsum(r)), 4)
    return pd.DataFrame(out, index=days)


def fx_paths(rng, days) -> pd.DataFrame:
    n = len(days)
    eur = 0.86 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    usd = 0.75 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    return pd.DataFrame({"EURGBP": np.round(eur, 5), "USDGBP": np.round(usd, 5), "GBPGBP": 1.0}, index=days)


def generate(out_dir: str | Path, seed: int = SEED) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    days = business_days()
    sec = build_securities(rng)
    px = price_paths(rng, sec, days)
    fx = fx_paths(rng, days)
    key: dict = {"seed": seed, "label": "SYNTHETIC; faults planted by fundlake.synth", "faults": {}}

    # ---- counterparties -------------------------------------------------------------------------------------------------
    cps = pd.DataFrame({"counterparty_id": [f"CP{i}" for i in range(1, 7)],
                        "name": [f"Broker {c} (synthetic)" for c in "ABCDEF"],
                        "lei": [_luhn_isin("XX" + _alnum(rng, 9))[:2] + _alnum(rng, 18) for _ in range(6)]})

    # ---- trades (true) ---------------------------------------------------------------------------------------------------
    universe = {f: list(rng.choice(sec.security_id, 20, replace=False)) for f in FUNDS}
    secx = sec.set_index("security_id")
    trades, tid = [], 0
    held = {(f, s): 0.0 for f in FUNDS for s in universe[f]}

    def mk_trade(f, s, side, qty, d):
        nonlocal tid
        tid += 1
        row = secx.loc[s]
        px_ = float(px.loc[d, s]) * (1 + rng.normal(0, 0.001))
        sd = shift_bdays(days, d, int(row.settle_lag))
        held[(f, s)] += qty if side == "BUY" else -qty
        trades.append(dict(trade_id=f"T{tid:07d}", fund_id=f, sedol=row.sedol, side=side, quantity=float(qty), price=round(px_, 4),
                           currency=row.currency, trade_date=d, settle_date=sd, counterparty_id=str(rng.choice(cps.counterparty_id)),
                           batch_id=f"B{d:%Y%m%d}"))
    for f in FUNDS:
        for s in universe[f]:
            row = secx.loc[s]
            target = rng.uniform(0.6e6, 2.2e6)
            unit = float(px.loc[days[0], s]) / (100 if row.asset_class == "bond" else 1)
            fxr = float(fx.loc[days[0], CCY_FX[row.currency] or "GBPGBP"])
            mk_trade(f, s, "BUY", int(round(target / (unit * fxr), -2)), days[0])
    for d in days[1:]:
        for f in FUNDS:
            for s in rng.choice(universe[f], 5, replace=False):
                if rng.random() < 0.5:
                    continue
                row = secx.loc[s]
                unit = float(px.loc[d, s]) / (100 if row.asset_class == "bond" else 1)
                fxr = float(fx.loc[d, CCY_FX[row.currency] or "GBPGBP"])
                qty = int(round(rng.uniform(0.03e6, 0.15e6) / (unit * fxr), -2))
                if qty <= 0:
                    continue
                side = "SELL" if (rng.random() < 0.4 and held[(f, s)] > qty * 2) else "BUY"
                mk_trade(f, s, side, qty, d)
    tr = pd.DataFrame(trades)
    tr["received_at"] = pd.to_datetime(tr.trade_date) + pd.Timedelta(hours=18)

    # ---- custodian positions (true), before planted faults --------------------------------------------------------------
    pos_rows = []
    for f in FUNDS:
        t = tr[tr.fund_id == f]
        for d in days:
            q = t[pd.to_datetime(t.settle_date) <= d].assign(sq=lambda x: np.where(x.side == "BUY", x.quantity, -x.quantity)).groupby("sedol").sq.sum()
            for sedol, qty in q.items():
                if abs(qty) > 1e-9:
                    r = sec[sec.sedol == sedol].iloc[0]
                    pos_rows.append(dict(position_date=d, fund_id=f, isin=r["isin"], quantity=float(qty), _sid=r.security_id))
    pos = pd.DataFrame(pos_rows)

    # ---- planted fault: identifier change (corporate action) -------------------------------------------------------------
    held_any = pos.groupby("_sid").size().sort_values(ascending=False)
    idc = held_any.index[3]
    chg_date = pd.Timestamp("2026-08-14")
    old_isin = secx.loc[idc, "isin"]
    new_isin = _luhn_isin(old_isin[:2] + _alnum(rng, 9))
    key["faults"]["identifier_change"] = [dict(security_id=idc, old_isin=old_isin, new_isin=new_isin, effective=str(chg_date.date()))]

    # ---- planted fault: identifier conflict (SEDOL of one security also attached to another in the xref) -------------------
    a, b = sec.security_id.iloc[38], sec.security_id.iloc[41]
    key["faults"]["identifier_conflict"] = [dict(security_id=a, other_security_id=b, id_type="SEDOL", id_value=secx.loc[a, "sedol"])]

    # ---- planted fault: custodian-vs-OMS breaks --------------------------------------------------------------------------
    breaks, cands = [], tr[(tr.trade_date > days[8]) & (tr.trade_date < days[45]) & (tr.quantity > 0) & (tr.trade_id != "")].copy()
    cands = cands[cands.sedol.map(secx.reset_index().set_index("sedol").security_id) != idc]
    cands = cands.sample(frac=1, random_state=seed)
    used = set()
    def pick():
        for _, t in cands.iterrows():
            k = (t.fund_id, t.sedol)
            if k not in used:
                used.add(k)
                return t
    custodian_adjust = []  # (fund, isin, from, to, delta)
    for kind in ["missed_settlement"] * 3 + ["custodian_extra", "missing_position"]:
        t = pick()
        r = sec[sec.sedol == t.sedol].iloc[0]
        sd = pd.Timestamp(t.settle_date)
        if kind == "missed_settlement":
            delta = -(t.quantity if t.side == "BUY" else -t.quantity)
            end = shift_bdays(days, sd, 6)
        elif kind == "custodian_extra":
            base = pos[(pos.fund_id == t.fund_id) & (pos._sid == r.security_id) & (pos.position_date == sd)].quantity
            delta = round(float(base.iloc[0]) * 0.05, 0) if len(base) else 1000.0
            end = days[-1]
        else:
            delta, end = None, shift_bdays(days, sd, 1)
        custodian_adjust.append((kind, t.fund_id, r.security_id, r["isin"], sd, end, delta))
    for kind, f, s, isin, sd, end, delta in custodian_adjust:
        m = (pos.fund_id == f) & (pos._sid == s) & (pos.position_date >= sd) & (pos.position_date <= end)
        if kind == "missing_position":
            pos = pos[~m]
        else:
            pos.loc[m, "quantity"] = pos.loc[m, "quantity"] + delta
        dts = [str(x.date()) for x in days[(days >= sd) & (days <= end)] if ((pos.fund_id == f) & (pos._sid == s) & (pos.position_date == x)).any() or kind == "missing_position"]
        breaks.append(dict(kind=kind, fund_id=f, security_id=s, dates=dts, quantity_diff=delta))
    key["faults"]["position_break"] = breaks

    # ---- apply identifier change to custodian + vendor feeds (they send the NEW isin from the effective date) -------------
    pos["isin"] = np.where((pos._sid == idc) & (pos.position_date >= chg_date), new_isin, pos["isin"])

    # ---- true NAV (administrator) and cash ----------------------------------------------------------------------------------
    cash_rows, nav_rows = [], []
    start_cash = 20e6
    for f in FUNDS:
        t = tr[tr.fund_id == f].copy()
        for d in days:
            tt = t[pd.to_datetime(t.settle_date) <= d]
            flow = 0.0
            for _, x in tt.iterrows():
                r = secx.loc[sec[sec.sedol == x.sedol].security_id.iloc[0]]
                basis = 100 if r.asset_class == "bond" else 1
                rate = float(fx.loc[pd.Timestamp(x.trade_date), CCY_FX[x.currency] or "GBPGBP"])
                v = x.quantity * x.price / basis * rate
                flow += -v if x.side == "BUY" else v
            cash = start_cash + flow
            cash_rows.append(dict(position_date=d, fund_id=f, cash_gbp=round(cash, 2)))
    cash = pd.DataFrame(cash_rows)
    # administrator NAV uses true custodian quantities (before the planted breaks) and true prices/FX
    true_pos = pd.DataFrame(pos_rows)
    true_pos["isin"] = true_pos["isin"]
    for (f, d), g in true_pos.groupby(["fund_id", "position_date"]):
        mv = 0.0
        for _, p in g.iterrows():
            r = secx.loc[p._sid]
            basis = 100 if r.asset_class == "bond" else 1
            mv += p.quantity * float(px.loc[d, p._sid]) / basis * float(fx.loc[d, CCY_FX[r.currency] or "GBPGBP"])
        c = float(cash[(cash.fund_id == f) & (cash.position_date == d)].cash_gbp.iloc[0])
        nav_rows.append(dict(position_date=d, fund_id=f, admin_nav_gbp=round(mv + c, 2)))
    nav = pd.DataFrame(nav_rows)
    cust_cash = cash.merge(nav, on=["position_date", "fund_id"], how="left")
    cust_cash["admin_nav_gbp"] = cust_cash.admin_nav_gbp.fillna(cust_cash.cash_gbp)  # before first settlement NAV is cash

    # ---- vendor prices (by isin) with planted faults ---------------------------------------------------------------------
    vp = []
    for s in sec.security_id:
        for d in days:
            isin = secx.loc[s, "isin"]
            if s == idc and d >= chg_date:
                isin = new_isin
            vp.append(dict(price_date=d, isin=isin, close_price=float(px.loc[d, s]), currency=secx.loc[s, "currency"], _sid=s))
    vp = pd.DataFrame(vp)
    heldkeys = pos.groupby(["_sid", "position_date"]).quantity.sum().reset_index()
    heldkeys = heldkeys[heldkeys.position_date > days[10]]
    rets = vp.sort_values(["_sid", "price_date"]).assign(ret=lambda x: x.groupby("_sid").close_price.pct_change().abs())
    cand = rets.merge(heldkeys, left_on=["_sid", "price_date"], right_on=["_sid", "position_date"]).sort_values("ret", ascending=False)
    miss, seen = [], set()
    for _, r in cand.iterrows():
        if r._sid in seen or r._sid == idc:
            continue
        seen.add(r._sid)
        miss.append((r._sid, r.price_date))
        if len(miss) == 8:
            break
    for s, d in miss:
        vp = vp[~((vp._sid == s) & (vp.price_date == d))]
    key["faults"]["missing_price"] = [dict(security_id=s, date=str(d.date())) for s, d in miss]
    stale_sid = [s for s in pos._sid.unique() if s not in seen and s != idc][0]
    sd0 = days[30]
    sdates = days[(days >= sd0)][:6]
    base_px = float(px.loc[sd0, stale_sid])
    vp.loc[(vp._sid == stale_sid) & (vp.price_date.isin(sdates)), "close_price"] = base_px
    key["faults"]["stale_price"] = [dict(security_id=stale_sid, dates=[str(x.date()) for x in sdates], price=base_px)]
    vp["vendor_ts"] = pd.to_datetime(vp.price_date) + pd.Timedelta(hours=20)

    # ---- vendor FX with planted stale window ------------------------------------------------------------------------------
    fxd = fx.copy()
    w = days[50:56]
    fxd.loc[w, "EURGBP"] = fx.loc[w[0], "EURGBP"]
    key["faults"]["stale_fx"] = [dict(pair="EURGBP", dates=[str(x.date()) for x in w], rate=float(fx.loc[w[0], "EURGBP"]))]
    fxl = fxd[["EURGBP", "USDGBP"]].reset_index(names="rate_date").melt(id_vars="rate_date", var_name="pair", value_name="rate")
    fxl["vendor_ts"] = pd.to_datetime(fxl.rate_date) + pd.Timedelta(hours=20)

    # ---- duplicated trades from feed replay ------------------------------------------------------------------------------
    rb = [days[12], days[27], days[40]]
    dup = tr[tr.batch_id.isin([f"B{d:%Y%m%d}" for d in rb])].copy()
    dup["batch_id"] = dup.batch_id + "R"
    dup["received_at"] = dup.received_at + pd.Timedelta(hours=14)
    tr_out = pd.concat([tr, dup], ignore_index=True).sort_values(["received_at", "trade_id"]).reset_index(drop=True)
    key["faults"]["duplicate_trade"] = [dict(trade_id=t) for t in sorted(dup.trade_id)]

    # ---- security master with attribute history (type 2) -------------------------------------------------------------------
    sm = []
    for _, s in sec.iterrows():
        sm.append(dict(security_id=s.security_id, name=s["name"], asset_class=s.asset_class, currency=s.currency, country=s.country,
                       sector=s.sector, rating=s.rating, price_basis=s.price_basis, settle_lag=s.settle_lag,
                       valid_from="2026-01-01", valid_to=None))
    sm = pd.DataFrame(sm)
    hist = []
    for sid in sec[sec.asset_class == "bond"].security_id.iloc[[2, 7, 11]]:
        i = sm.index[sm.security_id == sid][0]
        old = sm.loc[i].copy()
        newr = {"AAA": "AA", "AA": "A", "A": "BBB", "BBB": "BB", "BB": "B"}[old.rating]
        sm.at[i, "valid_to"] = "2026-08-19"
        n = old.copy(); n["rating"] = newr; n["valid_from"] = "2026-08-20"; n["valid_to"] = None
        sm = pd.concat([sm, n.to_frame().T], ignore_index=True)
        hist.append(dict(security_id=sid, field="rating", old=old.rating, new=newr, effective="2026-08-20"))
    key["attribute_history"] = hist
    xr = []
    for _, s in sec.iterrows():
        for typ, val in (("ISIN", s["isin"]), ("SEDOL", s.sedol), ("CUSIP", s.cusip)):
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            vf, vt = "2026-01-01", None
            if s.security_id == idc and typ == "ISIN":
                xr.append(dict(security_id=s.security_id, id_type=typ, id_value=val, valid_from=vf, valid_to="2026-08-13"))
                xr.append(dict(security_id=s.security_id, id_type=typ, id_value=new_isin, valid_from="2026-08-14", valid_to=None))
                continue
            xr.append(dict(security_id=s.security_id, id_type=typ, id_value=val, valid_from=vf, valid_to=vt))
    xr.append(dict(security_id=b, id_type="SEDOL", id_value=secx.loc[a, "sedol"], valid_from="2026-07-15", valid_to=None))
    xref = pd.DataFrame(xr)

    # ---- insurer extract --------------------------------------------------------------------------------------------------
    n_pol = 400
    regions = ["London", "South East", "North West", "Scotland", "Wales", "Midlands"]
    products = ["Motor", "Home", "Commercial Property", "Liability"]
    start = pd.to_datetime("2025-10-01") + pd.to_timedelta(rng.integers(0, 365, n_pol), unit="D")
    pol = pd.DataFrame(dict(
        policy_id=[f"P{i + 1:06d}" for i in range(n_pol)], client_id=[f"C{rng.integers(1, 301):04d}" for _ in range(n_pol)],
        product=rng.choice(products, n_pol, p=[.4, .3, .15, .15]), start_date=start.date, end_date=(start + pd.Timedelta(days=364)).date,
        annual_premium=np.round(rng.gamma(4, 180, n_pol), 2), sum_insured=np.round(rng.uniform(20e3, 2e6, n_pol), -3),
        region=rng.choice(regions, n_pol)))
    cl = pd.DataFrame(dict(client_id=[f"C{i:04d}" for i in range(1, 301)]))
    cl["client_name"] = [f"Client {i:04d} (synthetic)" for i in range(1, 301)]
    cl["email"] = [f"client{i:04d}@example.invalid" for i in range(1, 301)]
    cl["date_of_birth"] = (pd.to_datetime("1950-01-01") + pd.to_timedelta(rng.integers(0, 365 * 50, 300), unit="D")).date
    cl["postcode"] = [f"{rng.choice(['AB', 'CD', 'EF', 'GH'])}{rng.integers(1, 20)} {rng.integers(1, 9)}{_alnum(rng, 2, 'ABDEFG')}" for _ in range(300)]
    pol = pol.merge(cl, on="client_id", how="left")
    nc = 450
    pi = rng.integers(0, n_pol, nc)
    base = pd.to_datetime(pol.start_date.iloc[pi].values)
    loss = base + pd.to_timedelta(rng.integers(5, 300, nc), unit="D")
    claims = pd.DataFrame(dict(
        claim_id=[f"CL{i + 1:06d}" for i in range(nc)], policy_id=pol.policy_id.iloc[pi].values, loss_date=loss.date,
        reported_date=(loss + pd.to_timedelta(rng.integers(0, 30, nc), unit="D")).date,
        claim_type=rng.choice(["Damage", "Theft", "Liability", "Water"], nc), paid_amount=np.round(rng.gamma(2, 130, nc), 2),
        reserve_amount=np.round(rng.gamma(1.5, 50, nc), 2), status=rng.choice(["Open", "Closed"], nc, p=[.3, .7])))
    claims = claims[pd.to_datetime(claims.loss_date) <= pd.Timestamp(END)].reset_index(drop=True)
    idx = list(rng.choice(len(claims), 20, replace=False))
    before, orphan, repbl, dupl = idx[:5], idx[5:9], idx[9:11], idx[11:14]
    pst = pol.set_index("policy_id").start_date
    for i in before:
        claims.at[i, "loss_date"] = (pd.Timestamp(pst[claims.at[i, "policy_id"]]) - pd.Timedelta(days=20)).date()
        claims.at[i, "reported_date"] = (pd.Timestamp(claims.at[i, "loss_date"]) + pd.Timedelta(days=3)).date()
    for i in orphan:
        claims.at[i, "policy_id"] = f"P9{i:05d}"
    for i in repbl:
        claims.at[i, "reported_date"] = (pd.Timestamp(claims.at[i, "loss_date"]) - pd.Timedelta(days=4)).date()
    dupdf = claims.iloc[dupl].copy()
    claims = pd.concat([claims, dupdf], ignore_index=True)
    cid = lambda ix: [claims.at[i, "claim_id"] for i in ix]
    key["faults"]["claim_before_inception"] = [dict(claim_id=c) for c in cid(before)]
    key["faults"]["orphan_claim"] = [dict(claim_id=c) for c in cid(orphan)]
    key["faults"]["reported_before_loss"] = [dict(claim_id=c) for c in cid(repbl)]
    key["faults"]["duplicate_claim"] = [dict(claim_id=c) for c in cid(dupl)]
    claims["extract_ts"] = pd.Timestamp(END) + pd.Timedelta(hours=23)

    # ---- write sources --------------------------------------------------------------------------------------------------------
    pos_out = pos.drop(columns="_sid").sort_values(["position_date", "fund_id", "isin"])
    tr_out.to_csv(out / "oms_trades.csv", index=False)
    pos_out.to_csv(out / "custodian_positions.csv", index=False)
    cust_cash.to_csv(out / "custodian_cash_nav.csv", index=False)
    vp.drop(columns="_sid").sort_values(["price_date", "isin"]).to_csv(out / "vendor_prices.csv", index=False)
    fxl.to_csv(out / "vendor_fx.csv", index=False)
    sm.to_csv(out / "secmaster_securities.csv", index=False)
    xref.to_csv(out / "secmaster_identifiers.csv", index=False)
    cps.to_csv(out / "oms_counterparties.csv", index=False)
    pd.DataFrame({"fund_id": list(FUNDS), "fund_name": list(FUNDS.values()), "base_currency": "GBP",
                  "is_insurance_account": [f == "F04" for f in FUNDS]}).to_csv(out / "oms_funds.csv", index=False)
    pol.to_csv(out / "insurer_policies.csv", index=False)
    claims.to_csv(out / "insurer_claims.csv", index=False)
    from .synth_pm import generate_pm   # private-markets extracts (own random stream, so the files above are unchanged)
    generate_pm(out, key, {"EURGBP": float(fxd.loc[days[-1], "EURGBP"]), "USDGBP": float(fxd.loc[days[-1], "USDGBP"]), "GBPGBP": 1.0}, seed)
    key["counts"] = {k: int(len(v)) for k, v in key["faults"].items()}
    (out / "answer_key.json").write_text(json.dumps(key, indent=1, default=str))
    key["sha"] = hashlib.sha256((out / "oms_trades.csv").read_bytes()).hexdigest()
    return key


if __name__ == "__main__":
    import sys
    k = generate(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
    print(json.dumps(k["counts"]))
