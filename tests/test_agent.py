import asyncio
import json

import pytest

from fundlake.agent import agent as A
from fundlake.agent.eval import evaluate
from fundlake.agent.faithfulness import ungrounded
from fundlake.agent.mcp_server import build_server
from fundlake.agent.schemas import Answer
from fundlake.agent.tools import Toolbox
from conftest import RAW


@pytest.fixture(scope="module")
def tb(built):
    return Toolbox(built["db"], RAW)


def test_explain_break_for_each_planted_kind(tb, key):
    want = {"missed_settlement": "trade_not_reflected_at_custodian", "custodian_extra": "unexplained_difference", "missing_position": "missing_at_custodian"}
    for b in key["faults"]["position_break"]:
        r = tb.explain_break(b["fund_id"], b["security_id"], b["dates"][0])
        assert r["status"] == "ok" and r["is_break"] and r["cause_hint"] == want[b["kind"]]
        if b["kind"] == "missed_settlement":
            assert r["candidate_trade"]["trade_id"] and r["qty_diff"] == b["quantity_diff"]


def test_no_break_and_no_data_are_distinguished(tb):
    ok = tb.explain_break("F01", "S015", "2026-07-20")
    assert ok["status"] == "ok" and ok["is_break"] is False
    assert tb.explain_break("F99", "S001", "2026-07-20")["status"] == "no_data"
    assert tb.explain_break("F01", "S015", "2027-02-01")["status"] == "no_data"
    assert tb.explain_break("F01", "S015", "not-a-date")["status"] == "no_data"


def test_nav_decomposition_is_exact(tb):
    for fund, d in [("F01", "2026-07-20"), ("F02", "2026-08-17"), ("F03", "2026-09-15"), ("F04", "2026-09-30")]:
        r = tb.summarise_nav_move(fund, d)
        assert r["status"] == "ok" and abs(r["residual_gbp"]) < 0.05
        parts = r["price_effect_gbp"] + r["fx_effect_gbp"] + r["trade_effect_gbp"] + r["cash_effect_gbp"]
        assert abs(parts - r["nav_change_gbp"]) < 0.1


def test_price_status_tool(tb, key):
    m = key["faults"]["missing_price"][0]
    assert tb.check_price_staleness(m["security_id"], m["date"])["price_status"] == "carried"
    s = key["faults"]["stale_price"][0]
    assert tb.check_price_staleness(s["security_id"], s["dates"][1])["price_status"] == "stale"


def test_mcp_server_exposes_tools_and_matches_direct_calls(tb, key):
    server = build_server(tb)
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"explain_break", "check_price_staleness", "summarise_nav_move", "trace_lineage", "summarise_private_fund", "list_findings"}
    m = key["faults"]["missing_price"][0]
    res = asyncio.run(server.call_tool("check_price_staleness", {"security_id": m["security_id"], "date": m["date"]}))
    got = json.loads(res.content[0].text)
    assert got == json.loads(json.dumps(tb.check_price_staleness(m["security_id"], m["date"])))


def test_faithfulness_checker_catches_invented_numbers():
    results = [{"qty_diff": 131000.0, "date": "2026-09-03", "trade": {"trade_id": "T0000502"}}]
    assert ungrounded("The difference is 131,000 on 2026-09-03 (trade T0000502).", results) == []
    assert ungrounded("The difference is 131,000 and the loss was 12,345.67.", results) == ["12,345.67"]


def test_scripted_agent_end_to_end_evaluation(built, tmp_path):
    r = evaluate(built["db"], RAW, RAW / "answer_key.json", A.ScriptedModel().build())
    assert r["n_questions"] == 30 and r["answerable"] == 24 and r["accuracy_answerable"] == 1.0
    assert r["abstain_recall"] == 1.0 and r["abstain_precision"] == 1.0 and r["faithful_answers"] == 1.0 and r["hallucinated_numbers"] == 0
    (tmp_path / "e.json").write_text(json.dumps(r))


def test_evaluation_has_teeth_hallucinating_model_is_caught(built):
    def halluc(tool, res):
        a = A.compose(tool, res)
        if not a.abstain:
            a.summary += " Estimated exposure was 4,321.99."
        return a
    r = evaluate(built["db"], RAW, RAW / "answer_key.json", A.ScriptedModel(halluc).build(), "hallucinating")
    assert r["hallucinated_numbers"] >= 15 and r["faithful_answers"] < 0.4


def test_evaluation_has_teeth_overconfident_model_is_caught(built):
    def overconfident(tool, res):
        a = A.compose(tool, res)
        if tool == "explain_break" and res.get("cause_hint") == "unexplained_difference":
            a.root_cause_established = True
            a.key_facts["cause"] = "corporate_action"
        return a
    r = evaluate(built["db"], RAW, RAW / "answer_key.json", A.ScriptedModel(overconfident).build(), "overconfident")
    assert r["accuracy_answerable"] < 1.0
    assert not [x for x in r["rows"] if x["qid"].startswith("break") and x["correct"] is False and x["qid"] != "break3"]


def test_answer_schema_defaults():
    a = Answer(summary="x")
    assert a.abstain is False and a.root_cause_established is None
