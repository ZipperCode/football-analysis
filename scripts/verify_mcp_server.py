from __future__ import annotations

import asyncio

from football_analysis.mcp_server import mcp
from football_analysis.production import AnalysisAdviceReport, format_analysis_advice_alert


def main() -> None:
    tools = {tool.name for tool in asyncio.run(mcp.list_tools())}
    expected = {
        "production_status",
        "production_health",
        "get_picks_today",
        "get_live_decision",
        "get_odds_readiness",
        "evaluate_finished_matches",
        "refresh_live_data",
        "run_analysis_cycle",
        "push_analysis_report",
    }
    assert expected <= tools, f"missing MCP tools: {sorted(expected - tools)}"
    forbidden_fragments = {"broker", "betfair", "order", "execute_broker"}
    forbidden = [
        name
        for name in tools
        if any(fragment in name for fragment in forbidden_fragments)
    ]
    assert not forbidden, f"MCP must not expose broker/order tools by default: {forbidden}"

    message = format_analysis_advice_alert(
        AnalysisAdviceReport(
            status="no_recommendation",
            message="今日无满足阈值的主推，建议只复盘观察。",
            pick_count=0,
            analysis_count=3,
            items=[],
            risk_notice="Test risk notice.",
        )
    )
    assert "football-analysis advice" in message
    assert "execution=analysis_only_no_broker_orders" in message


if __name__ == "__main__":
    main()
