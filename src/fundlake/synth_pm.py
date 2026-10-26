"""Private-markets extracts for fundlake (SYNTHETIC), with a planted answer key.

Two of fundlake's funds invest as limited partners (LPs) in eight private funds: buyout, growth equity, real estate,
infrastructure and a secondaries fund, in GBP, EUR and USD. One interest was bought on the secondary market. The general
partners (GPs) send what GPs send: capital-call and distribution notices with line items, and quarterly capital-account
statements that arrive about 75 days after the quarter ends, so the latest NAV is always a quarter old.

Six extracts: pm_funds, pm_commitments, pm_secondary_transfers, pm_notices, pm_notice_lines, pm_nav_statements.

Planted faults (recorded under `faults`, prefixed pm_):
  pm_duplicate_notice       a notice (and its lines) delivered twice
  pm_notice_lines_mismatch  header total differs from the sum of its lines (header keying error; lines are right)
  pm_sign_corrected         distribution header keyed negative; lines positive and sum to the absolute header
  pm_unknown_investor       a post-transfer notice also sent to the seller of the secondary interest
  pm_over_call              cumulative calls exceed the commitment
  pm_missing_nav_statement  a quarterly statement never delivered
  pm_stale_nav              three quarters where the GP rolled NAV forward by cashflows only (no revaluation)
  pm_capital_account_break  a call notice never delivered, so the GP's contributions-to-date exceed ours
The key also holds the metrics a correct pipeline should report (`pm_expected`) and the hidden true NAV at the as-of date
(`pm_true_nav_asof`), so the cost of reporting a lagged NAV can be measured.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

AS_OF = date(2026, 9, 30)
STATEMENT_LAG_DAYS = 75          # statements for a quarter arrive about 75 days after it ends
TRANSFER_DATE = date(2025, 6, 30)
REFERENCE_DATE = date(2025, 3, 31)
SECONDARY_DISCOUNT = 0.88        # purchase price = 88% of reference NAV, adjusted for cashflows after the reference date
SELLER_ID = "EXT-SELLER-01"

PM_FUNDS = [
    ("PM01", "Corvane Capital Partners IV (synthetic)", "buyout", 2018, "USD"),
    ("PM02", "Quillon Growth Equity II (synthetic)", "growth", 2019, "EUR"),
    ("PM03", "Penhallow European Buyout V (synthetic)", "buyout", 2020, "EUR"),
    ("PM04", "Ashgrove UK Property Income (synthetic)", "real_estate", 2019, "GBP"),
    ("PM05", "Redmere Infrastructure III (synthetic)", "infrastructure", 2021, "GBP"),
    ("PM06", "Sablecross Secondaries VII (synthetic)", "secondaries", 2022, "USD"),
    ("PM07", "Lindholm Real Estate Value-Add II (synthetic)", "real_estate", 2022, "EUR"),
    ("PM08", "Thornby Buyout VI (synthetic)", "buyout", 2023, "GBP"),
]
# investor (a fundlake fund), private fund, commitment in fund currency
COMMITMENTS = [
    ("F04", "PM01", 40_000_000.0),   # bought on the secondary market on TRANSFER_DATE
    ("F04", "PM03", 25_000_000.0), ("F04", "PM04", 30_000_000.0), ("F04", "PM05", 35_000_000.0),
    ("F04", "PM07", 15_000_000.0), ("F04", "PM08", 20_000_000.0),
    ("F03", "PM02", 12_000_000.0), ("F03", "PM03", 10_000_000.0), ("F03", "PM06", 15_000_000.0), ("F03", "PM08", 8_000_000.0),
]
# quarterly return mean and sd, drawdown pace (quarters), management fee (annual, on commitment), first capital distribution
# quarter, income yield per quarter, cap on the share of NAV distributed in a quarter. Reported NAVs are smoothed, so the
# volatility is lower than for listed assets.
STRATEGY = {
    "buyout":         (0.035, 0.035, 16, 0.0175, 12, 0.0, 0.12),
    "growth":         (0.040, 0.050, 14, 0.0200, 14, 0.0, 0.12),
    "real_estate":    (0.022, 0.020, 10, 0.0125, 16, 0.011, 0.10),
    "infrastructure": (0.024, 0.015, 12, 0.0125, 16, 0.010, 0.08),
    "secondaries":    (0.030, 0.025, 8, 0.0100, 4, 0.0, 0.12),
}


def quarter_end(y: int, q: int) -> date:
    return date(y, 3 * q, [31, 30, 30, 31][q - 1])


def quarter_ends(first: date, last: date) -> list[date]:
    out, y, q = [], first.year, (first.month - 1) // 3 + 1
    while True:
        d = quarter_end(y, q)
        if d > last:
            return out
        if d >= first:
            out.append(d)
        q += 1
        if q == 5:
            y, q = y + 1, 1


def xirr(flows: list[tuple[date, float]]) -> float | None:
    """Annualised internal rate of return on dated cashflows (Excel XIRR convention, 365-day year), by bisection."""
    d0 = min(d for d, _ in flows)
    npv = lambda r: sum(a / (1.0 + r) ** ((d - d0).days / 365.0) for d, a in flows)
    lo, hi = -0.95, 2.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


def _simulate(rng, strategy: str, vintage: int, commitment: float) -> tuple[list, list, list]:
    """One LP interest from first close to the as-of quarter: calls, distributions, true NAV at each quarter end."""
    mean, sd, pace, fee, dist_start, yld, cap = STRATEGY[strategy]
    first = date(vintage, 6, 30)
    n_inv = int(round(pace * 1.5))
    w = np.exp(-np.arange(n_inv) / pace)
    w = w / w.sum()
    nav, prev = 0.0, first - timedelta(days=91)
    calls, dists, navs = [], [], []
    for k, qe in enumerate(quarter_ends(first, AS_OF)):
        inv = round(0.80 * commitment * w[k], 2) if k < n_inv else 0.0
        mfee = round(commitment * fee / 4, 2) if k < 20 else round(max(nav, 0.0) * fee / 8, 2)
        expenses = round(commitment * 0.0002, 2) if k % 4 == 3 else 0.0
        r = rng.normal(mean - (0.02 if k < 4 else 0.0), sd)
        nav = max(nav * (1 + r) + inv, 0.0)
        income = round(nav * yld, 2) if (yld > 0 and k >= 2) else 0.0
        dist = nav * min(0.03 + 0.01 * (k - dist_start), cap) if k >= dist_start else 0.0
        roc, gain = round(dist * 0.6, 2), round(dist * 0.4, 2)
        nav = max(nav - roc - gain - income, 0.0)
        calls.append(dict(k=k, qe=qe, value_date=prev + timedelta(days=40), investment=inv, management_fee=mfee, fund_expenses=expenses))
        dists.append(dict(k=k, qe=qe, value_date=qe - timedelta(days=12), income=income, return_of_capital=roc, realised_gain=gain))
        navs.append(dict(period_end=qe, nav=round(nav, 2)))
        prev = qe
    return calls, dists, navs


def _lines(row: dict, comps: list[str]) -> list[tuple[str, float]]:
    return [(c, row[c]) for c in comps if row[c] > 0]


def generate_pm(out, key: dict, fx_asof: dict[str, float], seed: int) -> dict:
    rng = np.random.default_rng(seed + 7)
    funds = {f[0]: f for f in PM_FUNDS}
    notices, lines, statements, commits = [], [], [], []
    truth: dict = {}
    transfer = None
    for i, (inv_id, fid, c) in enumerate(COMMITMENTS):
        _, _, strat, vint, ccy = funds[fid]
        calls, dists, navs = _simulate(rng, strat, vint, c)
        if (inv_id, fid) == ("F03", "PM02"):            # planted over-call: an extra investment call in quarter 18
            calls[18]["investment"] = round(calls[18]["investment"] + 0.14 * c, 2)
        secondary = fid == "PM01"
        holder = SELLER_ID if secondary else inv_id
        flows_all = []                                   # (value_date, kind, total, row)
        for row in calls:
            tot = round(sum(v for _, v in _lines(row, ["investment", "management_fee", "fund_expenses"])), 2)
            if tot > 0:
                flows_all.append((row["value_date"], "capital_call", tot, row))
        for row in dists:
            tot = round(sum(v for _, v in _lines(row, ["income", "return_of_capital", "realised_gain"])), 2)
            if tot > 0:
                flows_all.append((row["value_date"], "distribution", tot, row))
        flows_all.sort(key=lambda x: (x[0], x[1]))
        cum = lambda kind, upto: round(sum(t for d, k, t, _ in flows_all if k == kind and d <= upto), 2)
        if secondary:
            ref_nav = next(n["nav"] for n in navs if n["period_end"] == REFERENCE_DATE)
            between = lambda kind: round(sum(t for d, k, t, _ in flows_all if k == kind and REFERENCE_DATE < d <= TRANSFER_DATE), 2)
            price = round(SECONDARY_DISCOUNT * ref_nav + between("capital_call") - between("distribution"), 2)
            transfer = dict(transfer_id="TR-0001", pm_fund_id=fid, buyer_investor_id=inv_id, seller_name="Seller LP (synthetic)",
                            reference_date=REFERENCE_DATE, transfer_date=TRANSFER_DATE, reference_nav=ref_nav, purchase_price=price,
                            transferred_commitment=c, inherited_paid_in=cum("capital_call", TRANSFER_DATE),
                            inherited_distributions=cum("distribution", TRANSFER_DATE),
                            unfunded_at_transfer=round(c - cum("capital_call", TRANSFER_DATE), 2))
        commits.append(dict(commitment_id=f"CM{i + 1:03d}", investor_id=inv_id, pm_fund_id=fid,
                            commitment_date=TRANSFER_DATE if secondary else date(vint, 6, 30), commitment_amount=c,
                            origin="secondary" if secondary else "primary", transfer_id="TR-0001" if secondary else None))
        # notices: a secondary buyer only receives notices for cashflows after the transfer
        for d, kind, tot, row in flows_all:
            if secondary and d <= TRANSFER_DATE:
                continue
            nid = f"{'CN' if kind == 'capital_call' else 'DN'}-{fid}-{inv_id}-{row['k']:03d}"
            notices.append(dict(notice_id=nid, pm_fund_id=fid, investor_id=inv_id, notice_type=kind,
                                notice_date=d - timedelta(days=10 if kind == "capital_call" else 7), value_date=d, currency=ccy,
                                total_amount=tot, received_at=pd.Timestamp(d - timedelta(days=9 if kind == "capital_call" else 6)) + pd.Timedelta(hours=9)))
            comps = ["investment", "management_fee", "fund_expenses"] if kind == "capital_call" else ["income", "return_of_capital", "realised_gain"]
            for j, (comp, amt) in enumerate(_lines(row, comps), 1):
                lines.append(dict(notice_id=nid, line_no=j, component=comp, amount=amt))
        # capital-account statements: delivered for quarter ends up to the last one received by the as-of date
        for n in navs:
            pe = n["period_end"]
            if pe + timedelta(days=STATEMENT_LAG_DAYS) > AS_OF or (secondary and pe < TRANSFER_DATE):
                continue
            statements.append(dict(statement_id=f"ST-{fid}-{inv_id}-{pe:%Y%m}", pm_fund_id=fid, investor_id=inv_id, period_end=pe,
                                   nav=n["nav"], contributions_to_date=cum("capital_call", pe), distributions_to_date=cum("distribution", pe),
                                   received_date=pe + timedelta(days=STATEMENT_LAG_DAYS)))
        truth[(inv_id, fid)] = dict(flows=flows_all, navs=navs, holder=holder, ccy=ccy, commitment=c)

    notices_df, lines_df, st_df = pd.DataFrame(notices), pd.DataFrame(lines), pd.DataFrame(statements)
    faults: dict = {}

    # ---- planted: a call notice never delivered (GP statement still counts it) -> capital-account breaks --------------
    miss = notices_df[(notices_df.investor_id == "F04") & (notices_df.pm_fund_id == "PM05") & (notices_df.notice_type == "capital_call")
                      & (notices_df.value_date > date(2025, 6, 30)) & (notices_df.value_date <= date(2025, 9, 30))].iloc[0]
    notices_df = notices_df[notices_df.notice_id != miss.notice_id]
    lines_df = lines_df[lines_df.notice_id != miss.notice_id]
    brk = st_df[(st_df.investor_id == "F04") & (st_df.pm_fund_id == "PM05") & (st_df.period_end >= miss.value_date)]
    faults["pm_capital_account_break"] = [dict(investor_id="F04", pm_fund_id="PM05", period_end=str(p)) for p in sorted(brk.period_end)]
    missing_notice = dict(notice_id=miss.notice_id, value_date=str(miss.value_date), amount=float(miss.total_amount))

    # ---- planted: a statement never delivered ---------------------------------------------------------------------------
    ms = (st_df.investor_id == "F04") & (st_df.pm_fund_id == "PM07") & (st_df.period_end == date(2026, 3, 31))
    st_df = st_df[~ms]
    faults["pm_missing_nav_statement"] = [dict(investor_id="F04", pm_fund_id="PM07", period_end="2026-03-31")]

    # ---- planted: stale NAV (rolled forward by cashflows only) for three quarters --------------------------------------
    st_df = st_df.sort_values(["investor_id", "pm_fund_id", "period_end"]).reset_index(drop=True)
    stale_q = [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31)]
    idx = st_df.index[(st_df.investor_id == "F03") & (st_df.pm_fund_id == "PM06")].tolist()
    for i in idx:
        if st_df.at[i, "period_end"] in stale_q:
            p = i - 1
            st_df.at[i, "nav"] = round(st_df.at[p, "nav"] + (st_df.at[i, "contributions_to_date"] - st_df.at[p, "contributions_to_date"])
                                       - (st_df.at[i, "distributions_to_date"] - st_df.at[p, "distributions_to_date"]), 2)
    faults["pm_stale_nav"] = [dict(investor_id="F03", pm_fund_id="PM06", period_end=str(q)) for q in stale_q]

    # ---- planted: over-call (already in the cashflows; record it) -------------------------------------------------------
    faults["pm_over_call"] = [dict(investor_id="F03", pm_fund_id="PM02")]

    # ---- planted: post-transfer notice also sent to the seller ---------------------------------------------------------
    first_post = notices_df[(notices_df.pm_fund_id == "PM01") & (notices_df.notice_type == "capital_call")].sort_values("value_date").iloc[0]
    orphan = first_post.copy()
    orphan["notice_id"] = first_post.notice_id + "-S"
    orphan["investor_id"] = SELLER_ID
    notices_df = pd.concat([notices_df, orphan.to_frame().T], ignore_index=True)
    lines_df = pd.concat([lines_df, lines_df[lines_df.notice_id == first_post.notice_id].assign(notice_id=orphan.notice_id)], ignore_index=True)
    faults["pm_unknown_investor"] = [dict(notice_id=orphan.notice_id)]

    # ---- planted: header keying errors ----------------------------------------------------------------------------------
    protected = {miss.notice_id, first_post.notice_id, orphan.notice_id}
    calls_ok = notices_df[(notices_df.notice_type == "capital_call") & ~notices_df.notice_id.isin(protected) & (notices_df.investor_id != SELLER_ID)]
    mism = calls_ok.sample(2, random_state=seed).notice_id.tolist()
    notices_df.loc[notices_df.notice_id.isin(mism), "total_amount"] = notices_df.loc[notices_df.notice_id.isin(mism), "total_amount"].astype(float) + 1000.0
    faults["pm_notice_lines_mismatch"] = [dict(notice_id=n) for n in sorted(mism)]
    dists_ok = notices_df[(notices_df.notice_type == "distribution") & ~notices_df.notice_id.isin(protected)]
    sign = dists_ok.sample(2, random_state=seed + 1).notice_id.tolist()
    notices_df.loc[notices_df.notice_id.isin(sign), "total_amount"] = -notices_df.loc[notices_df.notice_id.isin(sign), "total_amount"].astype(float)
    faults["pm_sign_corrected"] = [dict(notice_id=n) for n in sorted(sign)]

    # ---- planted: duplicated deliveries (header and lines resent three days later) --------------------------------------
    pool = notices_df[~notices_df.notice_id.isin(protected | set(mism) | set(sign)) & (notices_df.investor_id != SELLER_ID)]
    dups = pool.sample(3, random_state=seed + 2).notice_id.tolist()
    rep = notices_df[notices_df.notice_id.isin(dups)].copy()
    rep["received_at"] = pd.to_datetime(rep.received_at) + pd.Timedelta(days=3)
    notices_df = pd.concat([notices_df, rep], ignore_index=True)
    lines_df = pd.concat([lines_df, lines_df[lines_df.notice_id.isin(dups)]], ignore_index=True)
    faults["pm_duplicate_notice"] = [dict(notice_id=n) for n in sorted(dups)]

    # ---- expected metrics: what a correct pipeline reports from the delivered data ---------------------------------------
    expected, true_nav = {}, {}
    st_by = st_df.groupby(["investor_id", "pm_fund_id"])
    for (inv_id, fid), t in truth.items():
        visible = [(d, k, tot) for d, k, tot, row in t["flows"]
                   if not (fid == "PM01" and d <= TRANSFER_DATE)
                   and not (inv_id == "F04" and fid == "PM05" and d == pd.Timestamp(missing_notice["value_date"]).date() and k == "capital_call")]
        last = st_by.get_group((inv_id, fid)).sort_values("period_end").iloc[-1]
        since = [(d, k, tot) for d, k, tot in visible if last.period_end < d <= AS_OF]
        nav_asof = round(float(last.nav) + sum(tot for _, k, tot in since if k == "capital_call") - sum(tot for _, k, tot in since if k == "distribution"), 2)
        calls_sum = sum(tot for _, k, tot in visible if k == "capital_call")
        dists_sum = sum(tot for _, k, tot in visible if k == "distribution")
        flows = [(d, -tot if k == "capital_call" else tot) for d, k, tot in visible]
        paid_in = calls_sum
        if fid == "PM01":
            flows.append((TRANSFER_DATE, -transfer["purchase_price"]))
            paid_in += transfer["purchase_price"]
        flows.append((AS_OF, nav_asof))
        expected[f"{inv_id}:{fid}"] = dict(paid_in=round(paid_in, 2), distributions=round(dists_sum, 2), nav_asof=nav_asof,
                                           last_statement=str(last.period_end), dpi=dists_sum / paid_in, rvpi=nav_asof / paid_in,
                                           tvpi=(dists_sum + nav_asof) / paid_in, irr=xirr(flows),
                                           is_reconciled=not (inv_id == "F04" and fid == "PM05"))
        true_nav[f"{inv_id}:{fid}"] = next(n["nav"] for n in t["navs"] if n["period_end"] == AS_OF)
    if transfer:
        e = expected["F04:PM01"]
        inh_p, inh_d = transfer["inherited_paid_in"], transfer["inherited_distributions"]
        post_calls = e["paid_in"] - transfer["purchase_price"]
        e["gp_basis_tvpi"] = (inh_d + e["distributions"] + e["nav_asof"]) / (inh_p + post_calls)

    # ---- write -------------------------------------------------------------------------------------------------------------
    pd.DataFrame([dict(pm_fund_id=f[0], fund_name=f[1], strategy=f[2], vintage=f[3], currency=f[4]) for f in PM_FUNDS]).to_csv(out / "pm_funds.csv", index=False)
    pd.DataFrame(commits).to_csv(out / "pm_commitments.csv", index=False)
    pd.DataFrame([transfer]).to_csv(out / "pm_secondary_transfers.csv", index=False)
    notices_df = notices_df.sort_values(["received_at", "notice_id"]).reset_index(drop=True)
    notices_df["total_amount"] = notices_df.total_amount.astype(float).round(2)
    notices_df.to_csv(out / "pm_notices.csv", index=False)
    lines_df.to_csv(out / "pm_notice_lines.csv", index=False)
    st_df.sort_values(["received_date", "statement_id"]).to_csv(out / "pm_nav_statements.csv", index=False)

    key["faults"].update(faults)
    key["pm_expected"] = expected
    key["pm_true_nav_asof"] = true_nav
    key["pm_missing_notice"] = missing_notice
    key["pm_transfer"] = {k: (str(v) if isinstance(v, date) else v) for k, v in transfer.items()}
    key["pm_fx_asof"] = fx_asof
    return key
