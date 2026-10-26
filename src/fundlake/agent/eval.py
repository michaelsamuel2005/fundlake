"""Evaluate the agent against the planted answer key. Expected answers come from answer_key.json (and, for NAV holders, the
position table), never from the tools under test.

Metrics: accuracy on answerable questions, abstention precision/recall on unanswerable ones, numeric faithfulness (share of answers with
every number grounded in a tool result), hallucinated-number count, and mean tool calls."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .agent import build_agent, run_question
from .faithfulness import ungrounded
from .schemas import Answer
from .tools import Toolbox


@dataclass
class Q:
    qid: str
    text: str
    kind: str                       # answerable | abstain
    check: Callable[[Answer], bool] = lambda a: True
    why: str = ""


def build_questions(key: dict, tb: Toolbox) -> list[Q]:
    f = key["faults"]
    qs: list[Q] = []
    cause_for = {"missed_settlement": "trade_not_reflected_at_custodian", "custodian_extra": "unexplained_difference", "missing_position": "missing_at_custodian"}
    for i, b in enumerate(f["position_break"]):
        d = b["dates"][len(b["dates"]) // 2]
        want = cause_for[b["kind"]]
        qs.append(Q(f"break{i}", f"Why does the custodian differ from the OMS for {b['fund_id']}/{b['security_id']} on {d}?", "answerable",
                    lambda a, want=want: (not a.abstain) and a.key_facts.get("cause") == want and a.root_cause_established == (want != "unexplained_difference"),
                    f"{b['kind']} -> {want}"))
    for i, m in enumerate(f["missing_price"]):
        qs.append(Q(f"missing{i}", f"Is the vendor price for {m['security_id']} on {m['date']} genuine or was it filled in?", "answerable",
                    lambda a: (not a.abstain) and a.key_facts.get("price_status") == "carried", "missing price -> carried"))
    sp = f["stale_price"][0]
    qs.append(Q("stale0", f"Is the price for {sp['security_id']} on {sp['dates'][2]} stale?", "answerable", lambda a: a.key_facts.get("price_status") == "stale", "stale"))
    planted = {(x["security_id"], x["date"]) for x in f["missing_price"]} | {(sp["security_id"], d) for d in sp["dates"]}
    clean = tb.con.execute("select security_id, date_day from gold.fact_price where not is_carried and not is_stale and date_day = '2026-07-20' limit 1").fetchone()
    assert (clean[0], str(clean[1])) not in planted
    qs.append(Q("clean0", f"Is the price for {clean[0]} on {clean[1]} stale or filled in?", "answerable", lambda a: a.key_facts.get("price_status") == "ok", "control: clean"))
    fx = f["stale_fx"][0]
    qs.append(Q("navfx", f"What drove F03's NAV move on {fx['dates'][3]}?", "answerable",
                lambda a: "stale_fx" in a.key_facts.get("data_issues", []), "stale FX day must be flagged"))
    qs.append(Q("navclean", "What drove F01's NAV move on 2026-07-20?", "answerable", lambda a: (not a.abstain) and a.key_facts.get("data_issues") == [] and a.key_facts.get("breaches_tolerance") is False,
                "control: clean day"))
    m0 = f["missing_price"][0]
    holder = tb.con.execute("select fund_id from gold.fact_position where security_id=? and position_date=? and oms_qty>0 limit 1", [m0["security_id"], m0["date"]]).fetchone()[0]
    qs.append(Q("navmiss", f"What drove {holder}'s NAV move on {m0['date']}?", "answerable", lambda a: "carried_price" in a.key_facts.get("data_issues", []), "missing price day must be flagged"))
    qs.append(Q("lin_trade", f"Where did trade {f['duplicate_trade'][0]['trade_id']} come from?", "answerable",
                lambda a: a.key_facts.get("source_file") == "oms_trades.csv", "lineage"))
    qs.append(Q("lin_nav", "Where did the NAV for F02 on 2026-08-03 come from?", "answerable", lambda a: a.key_facts.get("administrator_file") == "custodian_cash_nav.csv", "lineage"))
    pe = key["pm_expected"]
    qs.append(Q("pm_secondary", "What is F04's TVPI on its PM01 interest?", "answerable",
                lambda a: a.key_facts.get("basis") == "purchase_price" and abs(float(a.key_facts.get("tvpi", 0)) - pe["F04:PM01"]["tvpi"]) < 1e-3
                and abs(float(a.key_facts.get("gp_basis_tvpi", 0)) - pe["F04:PM01"]["gp_basis_tvpi"]) < 1e-3, "secondary on purchase price, GP basis shown"))
    unrec = [p for p, e in pe.items() if not e["is_reconciled"]][0].split(":")
    qs.append(Q("pm_unreconciled", f"Can we rely on {unrec[0]}'s performance figures for {unrec[1]}?", "answerable",
                lambda a: a.key_facts.get("is_reconciled") is False and not a.abstain, "capital account does not reconcile"))
    st = f["pm_stale_nav"][0]
    qs.append(Q("pm_stale", f"How has {st['investor_id']}'s {st['pm_fund_id']} private fund performed, and is its NAV current?", "answerable",
                lambda a, n=len(f["pm_stale_nav"]): a.key_facts.get("stale_nav_quarters") == float(n), "stale marks flagged"))
    oc = f["pm_over_call"][0]
    qs.append(Q("pm_overcall", f"What is {oc['investor_id']}'s TVPI in {oc['pm_fund_id']}?", "answerable",
                lambda a: "pm_over_call" in a.key_facts.get("findings", []), "over-call flagged"))
    for qid, text, why in [
        ("abs_pm", "What is F03's IRR in PM99?", "unknown private fund"),
        ("abs_fund", "Why does the custodian differ from the OMS for F99/S001 on 2026-08-03?", "unknown fund"),
        ("abs_date", "What drove F01's NAV move on 2027-01-05?", "date outside data"),
        ("abs_market", "Why did the FTSE fall on 12 August?", "no market-news tool"),
        ("abs_future", "What will F01's NAV be tomorrow?", "forecast"),
        ("abs_trade", "Where did trade T9999999 come from?", "unknown trade"),
    ]:
        qs.append(Q(qid, text, "abstain", lambda a: a.abstain, why))
    return qs


def evaluate(db: str | Path, raw: str | Path, key_path: str | Path, model, label: str = "scripted") -> dict:
    tb = Toolbox(db, raw)
    key = json.loads(Path(key_path).read_text())
    agent = build_agent(model)
    rows, halluc = [], 0
    for q in build_questions(key, tb):
        ans, calls, results = run_question(agent, tb, q.text)
        bad = ungrounded(ans.summary, results)
        halluc += len(bad)
        rows.append(dict(qid=q.qid, kind=q.kind, correct=bool(q.check(ans)), faithful=not bad, ungrounded=bad, n_calls=len(calls), why=q.why, abstained=ans.abstain))
    A = [r for r in rows if r["kind"] == "answerable"]
    B = [r for r in rows if r["kind"] == "abstain"]
    abst_pred = [r for r in rows if r["abstained"]]
    return dict(model=label, n_questions=len(rows), answerable=len(A), accuracy_answerable=sum(r["correct"] for r in A) / len(A),
                abstain_questions=len(B), abstain_recall=sum(r["abstained"] for r in B) / len(B),
                abstain_precision=(sum(r["kind"] == "abstain" for r in abst_pred) / len(abst_pred)) if abst_pred else None,
                faithful_answers=sum(r["faithful"] for r in rows) / len(rows), hallucinated_numbers=halluc,
                mean_tool_calls=sum(r["n_calls"] for r in rows) / len(rows), rows=rows)
