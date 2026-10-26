"""MCP server exposing the evidence tools, so any MCP client (Claude Desktop, an agent framework) can query the controls."""
from __future__ import annotations

import os
import sys

from mcp.server.mcpserver import MCPServer

from .tools import Toolbox


def build_server(toolbox: Toolbox) -> MCPServer:
    server = MCPServer("fundlake-controls", instructions="Evidence tools over a synthetic asset-management data platform. Answer only from tool output.")

    @server.tool()
    def explain_break(fund_id: str, security_id: str, date: str) -> dict:
        """Explain a custodian-vs-OMS position break for a fund, security and date (YYYY-MM-DD)."""
        return toolbox.explain_break(fund_id, security_id, date)

    @server.tool()
    def check_price_staleness(security_id: str, date: str) -> dict:
        """Say whether a security's price on a date is vendor-sourced, carried forward (missing) or stale."""
        return toolbox.check_price_staleness(security_id, date)

    @server.tool()
    def summarise_nav_move(fund_id: str, date: str) -> dict:
        """Decompose a fund's one-day NAV change into price, FX, trade and cash effects and list data faults touching that day."""
        return toolbox.summarise_nav_move(fund_id, date)

    @server.tool()
    def trace_lineage(entity: str, trade_id: str | None = None, fund_id: str | None = None, date: str | None = None) -> dict:
        """Trace a gold trade (entity='trade', trade_id) or a fund NAV (entity='nav', fund_id, date) to the raw source rows."""
        return toolbox.trace_lineage(entity, trade_id, fund_id, date)

    @server.tool()
    def summarise_private_fund(investor_id: str, pm_fund_id: str) -> dict:
        """Performance (paid-in, distributions, NAV, DPI, TVPI, IRR), NAV age, reconciliation status and findings for one private-fund commitment."""
        return toolbox.summarise_private_fund(investor_id, pm_fund_id)

    @server.tool()
    def list_findings(finding_type: str, limit: int = 10) -> dict:
        """List data-quality findings of one type, e.g. duplicate_trade, missing_price, stale_price, stale_fx, identifier_change."""
        return toolbox.list_findings(finding_type, limit)

    return server


def main() -> None:
    db = os.environ.get("FUNDLAKE_DB", "data/fundlake.duckdb")
    raw = os.environ.get("FUNDLAKE_RAW", "data/raw")
    build_server(Toolbox(db, raw)).run("stdio")


if __name__ == "__main__":
    sys.exit(main())
