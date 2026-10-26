"""Score fundlake's controls against the planted answer key: precision and recall per fault type, nothing hand-picked."""
from __future__ import annotations

import json
from pathlib import Path


def expected_sets(key: dict) -> dict[str, set]:
    f = key["faults"]
    e: dict[str, set] = {
        "duplicate_trade": {x["trade_id"] for x in f["duplicate_trade"]},
        "missing_price": {f"{x['security_id']}:{x['date']}" for x in f["missing_price"]},
        "stale_price": {f"{x['security_id']}:{d}" for x in f["stale_price"] for d in x["dates"]},
        "stale_fx": {f"{x['pair']}:{d}" for x in f["stale_fx"] for d in x["dates"]},
        "identifier_change": {x["security_id"] for x in f["identifier_change"]},
        "identifier_conflict": {f"{x['id_type']}:{x['id_value']}" for x in f["identifier_conflict"]},
        "position_break": {f"{b['fund_id']}:{b['security_id']}:{d}" for b in f["position_break"] for d in b["dates"]},
    }
    for k in ("claim_before_inception", "orphan_claim", "reported_before_loss", "duplicate_claim"):
        e[k] = {x["claim_id"] for x in f[k]}
    for k in ("pm_duplicate_notice", "pm_notice_lines_mismatch", "pm_sign_corrected", "pm_unknown_investor"):
        e[k] = {x["notice_id"] for x in f.get(k, [])}
    e["pm_over_call"] = {f"{x['investor_id']}:{x['pm_fund_id']}" for x in f.get("pm_over_call", [])}
    for k in ("pm_missing_nav_statement", "pm_stale_nav", "pm_capital_account_break"):
        e[k] = {f"{x['investor_id']}:{x['pm_fund_id']}:{x['period_end']}" for x in f.get(k, [])}
    return e


def detected_sets(con) -> dict[str, set]:
    d: dict[str, set] = {}
    for t, k in con.execute("select finding_type, entity_key from silver.dq_findings").fetchall():
        d.setdefault(t, set()).add(k)
    d["position_break"] = {f"{a}:{b}:{c}" for a, b, c in con.execute("select fund_id, security_id, position_date from gold.recon_position_breaks").fetchall()}
    d["pm_capital_account_break"] = {f"{a}:{b}:{c}" for a, b, c in con.execute(
        "select investor_id, pm_fund_id, period_end from gold.pm_recon_capital_account where is_break").fetchall()}
    return d


def score(con, answer_key_path) -> dict:
    key = json.loads(Path(answer_key_path).read_text())
    exp, det = expected_sets(key), detected_sets(con)
    out = {}
    for t, e in exp.items():
        g = det.get(t, set())
        tp = len(e & g)
        out[t] = dict(planted=len(e), detected=len(g), true_positive=tp, missed=sorted(e - g)[:5], false_positive=sorted(g - e)[:5],
                      recall=tp / len(e) if e else 1.0, precision=tp / len(g) if g else (1.0 if not e else 0.0))
    out["_overall"] = dict(planted=sum(v["planted"] for v in out.values()), true_positive=sum(v["true_positive"] for v in out.values()),
                           false_positive=sum(len(v["false_positive"]) for v in out.values()))
    return out
