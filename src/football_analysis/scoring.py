from __future__ import annotations

from football_analysis.contracts import ScoreBreakdown
from football_analysis.models import AgentFinding, Match, OddsSnapshot, Recommendation, RecommendationStatus
from football_analysis.settings import Settings
from football_analysis.scoring_helpers import (
    best_market_edge,
    bookmaker_count,
    clamp_score,
    league_profile_payload,
    league_settings_for_match,
    normalized_strategy_selection,
    strategy_confidence_class,
    strategy_profile_for_edge,
    strategy_profile_payload,
    tier_policy_for_league,
    tier_policy_gates_failed,
    tier_policy_payload,
)

_best_market_edge = best_market_edge
_clamp = clamp_score
_normalized_strategy_selection = normalized_strategy_selection
_league_settings_for_match = league_settings_for_match
_league_profile_payload = league_profile_payload
_strategy_confidence_class = strategy_confidence_class
_strategy_profile_for_edge = strategy_profile_for_edge
_strategy_profile_payload = strategy_profile_payload
_bookmaker_count = bookmaker_count
_tier_policy_for_league = tier_policy_for_league
_tier_policy_gates_failed = tier_policy_gates_failed
_tier_policy_payload = tier_policy_payload


def score_match(
    match: Match,
    odds_snapshots: list[OddsSnapshot],
    findings: list[AgentFinding],
    settings: Settings,
) -> Recommendation:
    league_settings = _league_settings_for_match(match, settings)
    league_profile_payload = _league_profile_payload(league_settings)
    bookmaker_count = _bookmaker_count(odds_snapshots)
    tier_policy = _tier_policy_for_league(league_settings, settings)
    edge = _best_market_edge(
        odds_snapshots,
        min_odds=settings.live_trading.min_recommendation_odds,
        max_odds=settings.live_trading.max_recommendation_odds,
    )
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
        score_breakdown = breakdown.model_dump(mode="json")
        score_breakdown["league_profile"] = league_profile_payload
        score_breakdown["tier_policy"] = _tier_policy_payload(tier_policy, bookmaker_count, [])
        score_breakdown["strategy_confidence_class"] = "analysis_only"
        return Recommendation(
            id=f"{match.id}-analysis-v1",
            match_id=match.id,
            status=RecommendationStatus.analysis_only,
            value_score=0.0,
            risk_score=75.0,
            confidence=0.20,
            score_breakdown=score_breakdown,
            risk_tags=["missing_comparable_odds"],
            reason="未取得可比较赔率，按风控规则只输出赛前分析，不给投注建议。",
            risk_notice=settings.app.risk_notice,
        )

    strategy_profile = _strategy_profile_for_edge(match, edge, settings)
    strategy_profile_payload = _strategy_profile_payload(strategy_profile)
    strategy_confidence_class = _strategy_confidence_class(league_settings, strategy_profile)
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

    tier_policy_gates_failed: list[str] = (
        _tier_policy_gates_failed(
            tier_policy,
            match=match,
            value_score=value_score,
            risk_score=risk_score,
            confidence=confidence,
            bookmaker_count=bookmaker_count,
        )
        if tier_policy is not None
        else []
    )
    if (
        status is RecommendationStatus.recommended
        and strategy_profile is None
        and strategy_confidence_class == "live_scoring"
        and tier_policy is not None
    ):
        if tier_policy_gates_failed:
            status = RecommendationStatus.paper_candidate
            stake_units = 0.0
            gates_failed.extend(tier_policy_gates_failed)
            strategy_confidence_class = "paper_candidate"
        else:
            strategy_confidence_class = tier_policy.label
            if tier_policy.max_stake_units is not None:
                stake_units = min(stake_units, tier_policy.max_stake_units)

    paper_candidate = status is RecommendationStatus.recommended and strategy_confidence_class == "paper_candidate"
    if paper_candidate:
        status = RecommendationStatus.paper_candidate
        stake_units = 0.0
        gates_failed.append("paper_candidate")

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
    score_breakdown = breakdown.model_dump(mode="json")
    score_breakdown["league_profile"] = league_profile_payload
    score_breakdown["tier_policy"] = _tier_policy_payload(tier_policy, bookmaker_count, tier_policy_gates_failed)
    score_breakdown["strategy_profile"] = strategy_profile_payload
    score_breakdown["strategy_confidence_class"] = strategy_confidence_class
    if strategy_profile is not None:
        reason += f" 命中回测策略池：{strategy_profile.name}（{strategy_profile.stability_label}）。"
    elif status is RecommendationStatus.paper_candidate:
        reason += " 未命中已验证回测策略池，且未通过当前联赛分层实盘门槛，仅进入纸面观察。"
    else:
        reason += " 未命中已验证回测策略池，仅按实时评分输出。"
    if status is RecommendationStatus.paper_candidate:
        reason += " 当前联赛或策略处于纸面观察模式，仓位置为 0，不进入今日主推。"
    elif strategy_confidence_class == "secondary_live_small_stake":
        reason += f" 命中二级职业联赛小仓实盘门槛，最高仓位 {stake_units:.1f}u。"

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
            "league_profile": league_profile_payload,
            "tier_policy": _tier_policy_payload(tier_policy, bookmaker_count, tier_policy_gates_failed),
            "strategy_profile": strategy_profile_payload,
            "strategy_confidence_class": strategy_confidence_class,
        },
        score_breakdown=score_breakdown,
        risk_tags=sorted(set(risk_tags + gates_failed)),
        reason=reason,
        risk_notice=settings.app.risk_notice,
    )
