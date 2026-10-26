"""PydanticAI agent over the evidence tools, plus a deterministic scripted model for CI.

The scripted model (`ScriptedModel`) is NOT an LLM. It is a rule-based planner that calls the same tools and returns the same
structured `Answer`, so CI exercises tool wiring, structured outputs, the faithfulness checker and the evaluation harness without
an API key. It says nothing about how an LLM would perform: for that run scripts/run_agent_live.py with a real model."""
from __future__ import annotations

import os
import re
from typing import Callable

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .schemas import Answer
from .tools import Toolbox

SYSTEM_PROMPT = """You are a data-controls analyst for a simulated asset manager (all data is synthetic).
Answer only from tool results. Copy figures exactly as returned; do not estimate, round differently, or compute new figures.
If a tool returns status 'no_data', or no tool can answer the question, set abstain=true and say why.
If a break has cause_hint 'unexplained_difference', say the cause is not established (root_cause_established=false) and name what to check;
never assert a cause the tools did not return. Fill key_facts with the machine-readable values you used."""


def build_agent(model, toolbox_cls=Toolbox) -> Agent:
    agent = Agent(model, deps_type=toolbox_cls, output_type=Answer, system_prompt=SYSTEM_PROMPT, retries=1)

    @agent.tool
    def explain_break(ctx: RunContext[Toolbox], fund_id: str, security_id: str, date: str) -> dict:
        """Explain a custodian-vs-OMS position break for a fund, security and date (YYYY-MM-DD)."""
        return ctx.deps.explain_break(fund_id, security_id, date)

    @agent.tool
    def check_price_staleness(ctx: RunContext[Toolbox], security_id: str, date: str) -> dict:
        """Say whether a security's price on a date is vendor-sourced, carried forward (missing) or stale."""
        return ctx.deps.check_price_staleness(security_id, date)

    @agent.tool
    def summarise_nav_move(ctx: RunContext[Toolbox], fund_id: str, date: str) -> dict:
        """Decompose a fund's one-day NAV change and list data faults touching that day."""
        return ctx.deps.summarise_nav_move(fund_id, date)

    @agent.tool
    def trace_lineage(ctx: RunContext[Toolbox], entity: str, trade_id: str | None = None, fund_id: str | None = None, date: str | None = None) -> dict:
        """Trace a gold trade (entity='trade', trade_id) or fund NAV (entity='nav', fund_id, date) to raw source rows."""
        return ctx.deps.trace_lineage(entity, trade_id, fund_id, date)

    @agent.tool
    def summarise_private_fund(ctx: RunContext[Toolbox], investor_id: str, pm_fund_id: str) -> dict:
        """Performance, NAV age, reconciliation status and findings for one private-fund commitment (e.g. F04, PM01)."""
        return ctx.deps.summarise_private_fund(investor_id, pm_fund_id)

    @agent.tool
    def list_findings(ctx: RunContext[Toolbox], finding_type: str, limit: int = 10) -> dict:
        """List data-quality findings of one type."""
        return ctx.deps.list_findings(finding_type, limit)

    return agent


# ------------------------------------------------------------------------------------------------------------------------------
# Scripted model
# ------------------------------------------------------------------------------------------------------------------------------
_F, _S, _D, _T = r"(F\d{2})", r"(S\d{3})", r"(\d{4}-\d{2}-\d{2})", r"(T\d{7})"


def plan(question: str) -> tuple[str, dict] | None:
    q = question
    if (p := re.search(r"(PM\d{2})", q)) and (m := re.search(_F, q)):
        return "summarise_private_fund", dict(investor_id=m.group(1), pm_fund_id=p.group(1))
    if re.search(r"differ|break|reconcil", q, re.I) and (m := re.search(_F, q)) and (s := re.search(_S, q)) and (d := re.search(_D, q)):
        return "explain_break", dict(fund_id=m.group(1), security_id=s.group(1), date=d.group(1))
    if re.search(r"price|close|stale|vendor", q, re.I) and (s := re.search(_S, q)) and (d := re.search(_D, q)):
        return "check_price_staleness", dict(security_id=s.group(1), date=d.group(1))
    if re.search(r"where.*(come|from)|lineage|trace|source", q, re.I):
        if t := re.search(_T, q):
            return "trace_lineage", dict(entity="trade", trade_id=t.group(1))
        if (m := re.search(_F, q)) and (d := re.search(_D, q)):
            return "trace_lineage", dict(entity="nav", fund_id=m.group(1), date=d.group(1))
    if re.search(r"\bnav\b", q, re.I) and not re.search(r"tomorrow|next|forecast|will", q, re.I) and (m := re.search(_F, q)) and (d := re.search(_D, q)):
        return "summarise_nav_move", dict(fund_id=m.group(1), date=d.group(1))
    if t := re.search(_T, q):
        return "trace_lineage", dict(entity="trade", trade_id=t.group(1))
    return None


def _abstain(reason: str) -> Answer:
    return Answer(summary=f"I cannot answer this from the available data: {reason}", abstain=True, abstain_reason=reason)


def compose(tool: str, res: dict) -> Answer:
    if res.get("status") != "ok":
        return _abstain(res.get("detail", "no data"))
    if tool == "explain_break":
        if not res["is_break"]:
            return Answer(summary=f"There is no break for {res['fund_id']}/{res['security_id']} on {res['date']}: both sides hold {res['oms_qty']:,.0f}.",
                          key_facts={"is_break": False}, root_cause_established=None)
        c = res["cause_hint"]
        s = (f"On {res['date']} the custodian holds {res['custodian_qty']:,.0f} of {res['security_id']} in {res['fund_id']} against {res['oms_qty']:,.0f} in the OMS, "
             f"a difference of {res['qty_diff']:,.0f}. ")
        established = True
        if c == "trade_not_reflected_at_custodian":
            t = res["candidate_trade"]
            s += f"The difference equals trade {t['trade_id']} ({t['side']} {t['quantity']:,.0f}), which settled on {t['settle_date']} and is not reflected at the custodian. "
        elif c == "missing_at_custodian":
            s += "The custodian reports no position at all while the OMS shows one. "
        elif c == "missing_at_oms":
            s += "The OMS shows no position while the custodian reports one. "
        else:
            established = False
            s += "No trade explains the difference, so the cause is not established; check for a corporate action or a custodian booking not yet mirrored in the OMS. "
        s += ("The break is still open." if res["still_open"] else f"The break began on {res['break_first_date']} and lasted {res['break_business_days']} business days.")
        return Answer(summary=s, key_facts={"cause": c, "qty_diff": res["qty_diff"], "is_break": True}, root_cause_established=established)
    if tool == "check_price_staleness":
        st = res["price_status"]
        s = f"The {res['date']} price for {res['security_id']} is {res['close_price']}, status {st}. "
        if st == "carried":
            s += "The vendor sent no price for that date, so the last available close was carried forward."
        elif st == "stale":
            s += f"The close has not changed for several days ({res.get('stale_detail')})."
        else:
            s += "It came from the vendor feed (source row " + str(res["source_row"]) + ")."
        return Answer(summary=s, key_facts={"price_status": st, "close_price": res["close_price"]})
    if tool == "summarise_nav_move":
        s = (f"{res['fund_id']} NAV moved from {res['previous_nav_gbp']:,.2f} to {res['nav_gbp']:,.2f} GBP ({res['nav_change_gbp']:,.2f}) between {res['previous_date']} and {res['date']}: "
             f"price effect {res['price_effect_gbp']:,.2f}, FX effect {res['fx_effect_gbp']:,.2f}, trades {res['trade_effect_gbp']:,.2f}, cash {res['cash_effect_gbp']:,.2f}. "
             f"The difference to the administrator NAV is {res['difference_to_administrator_bps']} bps")
        s += (", outside tolerance. " if res["breaches_tolerance"] else ", within tolerance. ")
        if res["data_issues"]:
            s += "Data issues on the day: " + ", ".join(res["data_issues"]) + "."
        return Answer(summary=s, key_facts={"data_issues": list(res["data_issues"]), "breaches_tolerance": res["breaches_tolerance"], "nav_change_gbp": res["nav_change_gbp"]})
    if tool == "summarise_private_fund":
        s = (f"{res['investor_id']}'s commitment of {res['commitment']:,.2f} {res['currency']} to {res['pm_fund_id']} ({res['strategy']}, vintage {res['vintage']}): "
             f"paid-in {res['paid_in']:,.2f}, distributions {res['distributions']:,.2f}, NAV {res['nav_asof']:,.2f} "
             f"(from the {res['last_statement_date']} statement, {res['statement_lag_days']} days old, rolled forward with later cashflows); "
             f"DPI {res['dpi']:.2f}, TVPI {res['tvpi']:.2f}, IRR {res['irr_pct']:.1f}%. ")
        facts = {"tvpi": res["tvpi"], "is_reconciled": res["is_reconciled"], "findings": [f["finding_type"] for f in res["findings"]],
                 "stale_nav_quarters": float(sum(1 for f in res["findings"] if f["finding_type"] == "pm_stale_nav"))}
        if res.get("basis") == "purchase_price":
            s += (f"This interest was bought on the secondary market on {res['transfer_date']} for {res['purchase_price']:,.2f}, so the multiples are on that price; "
                  f"on the GP-reported basis, which inherits the seller's history, TVPI is {res['gp_basis_tvpi']:.2f}. ")
            facts.update(basis="purchase_price", gp_basis_tvpi=res["gp_basis_tvpi"])
        if not res["is_reconciled"]:
            b = res["capital_account_breaks"][0]
            s += (f"These figures are not reliable yet: from {b['period_end']} the GP's capital account shows {b['contributions_diff']:,.2f} more contributed "
                  f"than our notices ({b['cause_hint']}); obtain the missing notice before reporting. ")
        if facts["stale_nav_quarters"]:
            s += f"The GP's NAV was not revalued for {int(facts['stale_nav_quarters'])} quarters running (stale marks), so treat its history with care. "
        if "pm_over_call" in facts["findings"]:
            s += "Cumulative calls exceed the commitment: query the over-call with the GP. "
        return Answer(summary=s.strip(), key_facts=facts, root_cause_established=None)
    if tool == "trace_lineage":
        if "gold" in res:
            g, src = res["gold"], res["source"]
            return Answer(summary=f"Trade {g['trade_id']} comes from {src['file']} row {src['row']} (batch {src['batch_id']}); the feed carried {g['n_source_copies']} copies of it.",
                          key_facts={"source_file": src["file"], "source_row": src["row"]})
        a = res["administrator_row"]
        return Answer(summary=f"NAV of {res['nav_gbp']:,.2f} is built from {res['n_components']} positions priced from vendor_prices.csv and vendor_fx.csv, plus cash from {a['file']} row {a['row']}.",
                      key_facts={"administrator_file": a["file"], "n_components": res["n_components"]})
    return _abstain("no composer for tool " + tool)


class ScriptedModel:
    """Factory for a PydanticAI FunctionModel following `plan` then `compose`."""

    def __init__(self, composer: Callable[[str, dict], Answer] = compose):
        self.composer = composer

    def build(self) -> FunctionModel:
        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            out_tool = info.output_tools[0].name
            question = next(p.content for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart))
            returns = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
            if not returns:
                step = plan(str(question))
                if step is None:
                    ans = _abstain("none of the available tools covers this question")
                    return ModelResponse(parts=[ToolCallPart(out_tool, ans.model_dump())])
                return ModelResponse(parts=[ToolCallPart(step[0], step[1])])
            r = returns[-1]
            ans = self.composer(r.tool_name, r.content if isinstance(r.content, dict) else {})
            return ModelResponse(parts=[ToolCallPart(out_tool, ans.model_dump())])
        return FunctionModel(fn)


def run_question(agent: Agent, toolbox: Toolbox, question: str):
    """Run one question; return (Answer, tool_calls, tool_results)."""
    res = agent.run_sync(question, deps=toolbox)
    calls, results = [], []
    for m in res.all_messages():
        for p in getattr(m, "parts", []):
            if isinstance(p, ToolCallPart) and p.tool_name != "final_result":
                calls.append((p.tool_name, p.args))
            if isinstance(p, ToolReturnPart) and p.tool_name != "final_result":
                results.append(p.content)
    return res.output, calls, results
