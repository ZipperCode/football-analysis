from __future__ import annotations

from dataclasses import dataclass

from football_analysis.contracts import ScoreBreakdown
from football_analysis.models import AgentFinding, Match, OddsSnapshot, Recommendation, RecommendationStatus
from football_analysis.settings import Settings


@dataclass(frozen=True)
class MarketEdge:
    market_type: str
    selection: str
    best_price: float
    market_average: float
    edge: float
    source: str
    bookmaker: str
    movement: float


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _best_market_edge(odds_snapshots: list[OddsSnapshot]) -> MarketEdge | None:
    best: MarketEdge | None = None
    for snapshot in odds_snapshots:
        prices = snapshot.best_price or snapshot.outcome_odds
        for selection, price in prices.items():
            average = snapshot.market_average.get(selection)
            if not average or average <= 1.0 or price <= 1.0:
                continue
            edge = (price / average) - 1.0
            candidate = MarketEdge(
                market_type=snapshot.market_type.value,
                selection=selection,
                best_price=price,
                market_average=average,
                edge=edge,
                source=snapshot.source,
                bookmaker=snapshot.bookmaker,
                movement=snapshot.movement,
            )
            if best is None or candidate.edge > best.edge:
                best = candidate
    return best


def score_match(
    match: Match,
    odds_snapshots: list[OddsSnapshot],
    findings: list[AgentFinding],
    settings: Settings,
) -> Recommendation:
    edge = _best_market_edge(odds_snapshots)
    risk_tags = [tag for finding in findings for tag in finding.risk_tags]
    weighted_signal = sum(finding.score_delta * finding.confidence for finding in findings)
    average_finding_confidence = (
        sum(finding.confidence for finding in findings) / len(findings) if findings else 0.35
    )

    if edge is None:
        breakdown = ScoreBreakdown(
            data_quality=match.data_completeness,
            final_value_score=0.0,
            final_risk_score=75.0,
            gates_failed=["missing_comparable_odds"],
        )
        return Recommendation(
            id=f"{match.id}-analysis-v1",
            match_id=match.id,
            status=RecommendationStatus.analysis_only,
            value_score=0.0,
            risk_score=75.0,
            confidence=0.20,
            score_breakdown=breakdown.model_dump(mode="json"),
            risk_tags=["missing_comparable_odds"],
            reason="未取得可比较赔率，按风控规则只输出赛前分析，不给投注建议。",
            risk_notice=settings.app.risk_notice,
        )

    data_penalty = max(0.0, settings.thresholds.min_data_quality - match.data_completeness) * 85.0
    tag_penalty = min(32.0, len(set(risk_tags)) * 8.0)
    movement_penalty = 18.0 if abs(edge.movement) >= 0.10 else abs(edge.movement) * 90.0
    risk_score = _clamp(18.0 + data_penalty + tag_penalty + movement_penalty, 0.0, 100.0)

    value_score = _clamp(50.0 + edge.edge * 180.0 + weighted_signal, 0.0, 100.0)
    confidence = _clamp(
        0.22
        + match.data_completeness * 0.42
        + average_finding_confidence * 0.18
        + edge.edge * 1.10
        - risk_score / 220.0,
        0.05,
        0.93,
    )

    status = RecommendationStatus.recommended
    reasons: list[str] = []
    gates_failed: list[str] = []
    if match.data_completeness < settings.thresholds.min_data_quality:
        status = RecommendationStatus.analysis_only
        reasons.append("数据完整度低于阈值")
        gates_failed.append("low_data_quality")
    if value_score < settings.thresholds.min_value_score:
        status = RecommendationStatus.analysis_only
        reasons.append("价值分未过阈值")
        gates_failed.append("low_value_score")
    if risk_score > settings.thresholds.max_risk_score:
        status = RecommendationStatus.rejected
        reasons.append("风险分超过阈值")
        gates_failed.append("high_risk_score")

    if status is RecommendationStatus.recommended:
        if value_score >= 78 and confidence >= 0.62 and risk_score <= 38:
            stake_units = settings.thresholds.max_stake_units
        elif value_score >= 68 and confidence >= 0.52:
            stake_units = min(1.0, settings.thresholds.max_stake_units)
        else:
            stake_units = 0.5
        reason = (
            f"{edge.market_type} {edge.selection} 当前最高价 {edge.best_price:.2f} "
            f"高于市场均值 {edge.market_average:.2f}，模型价值分 {value_score:.1f}，"
            f"风险分 {risk_score:.1f}。"
        )
    else:
        stake_units = 0.0
        reason = "；".join(reasons) + "，按风控规则不进入主推。"

    breakdown = ScoreBreakdown(
        odds_edge=round(edge.edge, 4),
        data_quality=match.data_completeness,
        history_signal=round(
            sum(finding.score_delta * finding.confidence for finding in findings if "history" in finding.agent_name.lower()),
            4,
        ),
        news_signal=round(
            sum(finding.score_delta * finding.confidence for finding in findings if "news" in finding.agent_name.lower()),
            4,
        ),
        risk_penalty=round(data_penalty + tag_penalty, 4),
        movement_penalty=round(movement_penalty, 4),
        final_value_score=round(value_score, 2),
        final_risk_score=round(risk_score, 2),
        gates_failed=gates_failed,
    )

    return Recommendation(
        id=f"{match.id}-{edge.market_type}-{edge.selection}-v1",
        match_id=match.id,
        market_type=edge.market_type,
        selection=edge.selection,
        status=status,
        value_score=round(value_score, 2),
        risk_score=round(risk_score, 2),
        confidence=round(confidence, 3),
        stake_units=stake_units,
        odds_basis={
            "best_price": edge.best_price,
            "market_average": edge.market_average,
            "edge": round(edge.edge, 4),
            "source": edge.source,
            "bookmaker": edge.bookmaker,
            "movement": edge.movement,
        },
        score_breakdown=breakdown.model_dump(mode="json"),
        risk_tags=sorted(set(risk_tags + gates_failed)),
        reason=reason,
        risk_notice=settings.app.risk_notice,
    )
