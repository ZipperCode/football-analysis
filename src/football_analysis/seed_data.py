from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from football_analysis.models import AgentFinding, EvidenceSource, Match, OddsSnapshot


def build_seed_dataset(timezone: str) -> tuple[list[Match], list[OddsSnapshot], list[AgentFinding]]:
    tz = ZoneInfo(timezone)
    now = datetime.now(tz)
    kickoff_one = now.replace(hour=22, minute=0, second=0, microsecond=0)
    kickoff_two = now.replace(hour=23, minute=0, second=0, microsecond=0)

    matches = [
        Match(
            id="SAMPLE-001",
            league="Premier League",
            home_team="North London FC",
            away_team="Manchester Blue",
            kickoff_at=kickoff_one,
            data_completeness=0.86,
        ),
        Match(
            id="SAMPLE-002",
            league="La Liga",
            home_team="Madrid White",
            away_team="Catalonia Red",
            kickoff_at=kickoff_two,
            data_completeness=0.58,
        ),
    ]

    odds = [
        OddsSnapshot(
            id="SAMPLE-001-1x2-api-football",
            match_id="SAMPLE-001",
            market_type="1x2",
            source="api_football",
            bookmaker="Market average",
            outcome_odds={"HOME": 2.24, "DRAW": 3.42, "AWAY": 3.18},
            market_average={"HOME": 2.08, "DRAW": 3.39, "AWAY": 3.10},
            best_price={"HOME": 2.24, "DRAW": 3.48, "AWAY": 3.22},
            movement=0.035,
        ),
        OddsSnapshot(
            id="SAMPLE-002-1x2-odds-api",
            match_id="SAMPLE-002",
            market_type="1x2",
            source="odds_api_io",
            bookmaker="Market average",
            outcome_odds={"HOME": 2.52, "DRAW": 3.30, "AWAY": 2.80},
            market_average={"HOME": 2.50, "DRAW": 3.28, "AWAY": 2.78},
            best_price={"HOME": 2.54, "DRAW": 3.35, "AWAY": 2.84},
            movement=0.12,
        ),
    ]

    findings = [
        AgentFinding(
            id="SAMPLE-001-news",
            match_id="SAMPLE-001",
            agent_name="News Agent",
            summary="主队轮换压力低，客队锋线一名主力缺阵，消息面对主队略有正向。",
            confidence=0.72,
            score_delta=4.2,
            evidence_sources=[
                EvidenceSource(title="Fixture note fixture", publisher="seed"),
            ],
        ),
        AgentFinding(
            id="SAMPLE-001-history",
            match_id="SAMPLE-001",
            agent_name="History Agent",
            summary="主队近五个主场稳定，直接交锋只给低权重参考。",
            confidence=0.68,
            score_delta=2.4,
        ),
        AgentFinding(
            id="SAMPLE-002-risk",
            match_id="SAMPLE-002",
            agent_name="Risk Agent",
            summary="赔率波动偏高且数据完整度不足，降级为仅分析。",
            confidence=0.80,
            score_delta=-6.0,
            risk_tags=["low_data_quality", "odds_volatility"],
        ),
    ]

    return matches, odds, findings
