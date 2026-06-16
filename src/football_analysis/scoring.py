from __future__ import annotations

from dataclasses import dataclass

from football_analysis.contracts import ScoreBreakdown
from football_analysis.models import AgentFinding, MarketType, Match, OddsSnapshot, Recommendation, RecommendationStatus
from football_analysis.settings import LeagueSettings, Settings, StrategyProfileSettings, TierPolicySettings


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
    line: str | None = None


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
            if _is_price_outlier(price, average):
                price = snapshot.outcome_odds.get(selection, 0.0)
                if price <= 1.0 or _is_price_outlier(price, average):
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
                line=snapshot.line,
            )
            if best is None or candidate.edge > best.edge:
                best = candidate
    return best


def _is_price_outlier(price: float, average: float) -> bool:
    if average <= 1.0 or price <= 1.0:
        return True
    ratio = price / average
    return ratio >= 1.75 or ratio <= 0.45


def _strategy_profile_for_edge(
    match: Match,
    edge: MarketEdge,
    settings: Settings,
) -> StrategyProfileSettings | None:
    if _is_world_cup_live_league(_league_settings_for_match(match, settings)):
        selection = _normalized_strategy_selection(edge.selection, edge.market_type)
        for profile in sorted(settings.strategy_profiles, key=lambda item: (not item.live_enabled, item.id)):
            if not profile.active:
                continue
            if profile.id != "world_cup_high_winrate":
                continue
            if profile.league_code.upper() != "WORLD_CUP":
                continue
            if profile.market_type != edge.market_type:
                continue
            if selection in {_normalized_strategy_selection(item, profile.market_type) for item in profile.selections}:
                return profile
    league_code = _football_data_uk_code(match, settings)
    if not league_code:
        return None
    selection = _normalized_strategy_selection(edge.selection, edge.market_type)
    profiles = sorted(settings.strategy_profiles, key=lambda item: (not item.live_enabled, item.id))
    for profile in profiles:
        if not profile.active:
            continue
        if profile.league_code.upper() != league_code:
            continue
        if profile.market_type != edge.market_type:
            continue
        if selection in {_normalized_strategy_selection(item, profile.market_type) for item in profile.selections}:
            return profile
    return None


def _league_settings_for_match(match: Match, settings: Settings) -> LeagueSettings | None:
    """Return configured league metadata for a match, including provider aliases."""
    normalized_league = match.league.strip().lower()
    for league in settings.leagues:
        if normalized_league in {value.strip().lower() for value in _league_match_values(league) if value}:
            return league
    return None


def _league_match_values(league: LeagueSettings) -> list[str]:
    """Build every stable league label used by providers and historical datasets."""
    values = [league.code, league.name, league.football_data_uk_code, league.football_data_org_code]
    if league.country and league.name:
        values.append(f"{league.country} - {league.name}")
    values.extend(league.aliases)
    return [value for value in values if value]


def _football_data_uk_code(match: Match, settings: Settings) -> str | None:
    league = _league_settings_for_match(match, settings)
    if not league:
        return None
    return (league.football_data_uk_code or league.code).upper()


def _normalized_strategy_selection(selection: str, market_type: str | None = None) -> str:
    upper = selection.upper()
    market = (market_type or "").lower()
    if upper.startswith("AH_AWAY") or (market == "asian_handicap" and _is_away_selection(upper)):
        return "AH_AWAY"
    if upper.startswith("AH_HOME") or (market == "asian_handicap" and _is_home_selection(upper)):
        return "AH_HOME"
    return upper


def _is_away_selection(selection: str) -> bool:
    return selection == "AWAY" or selection.startswith("AWAY_") or selection.startswith("AWAY:")


def _is_home_selection(selection: str) -> bool:
    return selection == "HOME" or selection.startswith("HOME_") or selection.startswith("HOME:")


def _strategy_profile_payload(profile: StrategyProfileSettings | None) -> dict:
    if profile is None:
        return {"matched": False}
    return {
        "matched": True,
        "id": profile.id,
        "name": profile.name,
        "league_code": profile.league_code,
        "season_phases": profile.season_phases,
        "stability_label": profile.stability_label,
        "roi": profile.roi,
        "settled_bets": profile.settled_bets,
        "positive_folds": profile.positive_folds,
        "fold_count": profile.fold_count,
        "average_clv": profile.average_clv,
        "live_enabled": profile.live_enabled,
        "long_horizon_roi": profile.long_horizon_roi,
        "long_horizon_settled_bets": profile.long_horizon_settled_bets,
        "holdout_roi": profile.holdout_roi,
        "holdout_settled_bets": profile.holdout_settled_bets,
    }


def _league_profile_payload(league: LeagueSettings | None) -> dict:
    """Expose the league tier that decided scoring confidence and staking mode."""
    if league is None:
        return {"matched": False}
    return {
        "matched": True,
        "code": league.code,
        "name": league.name,
        "country": league.country,
        "tier": league.tier,
        "analysis_depth": league.analysis_depth,
        "strategy_mode": league.strategy_mode,
        "paper_only": league.paper_only,
        "min_bookmakers": league.min_bookmakers,
        "max_events": league.max_events,
    }


def _tier_policy_for_league(league: LeagueSettings | None, settings: Settings) -> TierPolicySettings | None:
    if league is None:
        return None
    return settings.tier_policies.get(league.tier)


def _bookmaker_count(odds_snapshots: list[OddsSnapshot]) -> int:
    """Count real bookmakers; synthetic market-average rows are not enough for live staking."""
    return len(
        {
            snapshot.bookmaker.strip()
            for snapshot in odds_snapshots
            if snapshot.bookmaker and snapshot.bookmaker.strip().lower() != "market average"
        }
    )


def _research_advisory_signal(match: Match, findings: list[AgentFinding]) -> dict | None:
    candidates: list[dict] = []
    for finding in findings:
        payload = finding.payload or {}
        if not bool(payload.get("advisory_recommendation")):
            continue
        market_type = str(payload.get("market_type") or "1x2")
        if market_type != "1x2":
            continue
        selection = _normalize_research_selection(payload.get("selection"), match)
        if not selection:
            continue
        evidence_count = len(finding.evidence_sources)
        if evidence_count < 2 or finding.confidence < 0.58:
            continue
        candidates.append(
            {
                "selection": selection,
                "confidence": finding.confidence,
                "score_delta": finding.score_delta,
                "evidence_count": evidence_count,
                "summary": finding.summary,
                "finding_id": finding.id,
                "agent_name": finding.agent_name,
            }
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item["confidence"], item["evidence_count"], item["score_delta"]), reverse=True)
    return candidates[0]


def _normalize_research_selection(value: object, match: Match) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    normalized = raw.upper()
    if normalized in {"HOME", "AWAY", "DRAW"}:
        return normalized
    if raw.casefold() == match.home_team.casefold():
        return "HOME"
    if raw.casefold() == match.away_team.casefold():
        return "AWAY"
    return None


def _selection_market_edge(odds_snapshots: list[OddsSnapshot], selection: str) -> MarketEdge | None:
    valid: list[tuple[float, OddsSnapshot]] = []
    for snapshot in odds_snapshots:
        if snapshot.market_type.value != "1x2":
            continue
        price = snapshot.outcome_odds.get(selection)
        average = snapshot.market_average.get(selection)
        if price is None or average is None or price <= 1.0 or average <= 1.0:
            continue
        if _is_price_outlier(price, average):
            continue
        valid.append((price, snapshot))
    if not valid:
        return None
    best_price, best_snapshot = max(valid, key=lambda item: item[0])
    market_average = sum(price for price, _ in valid) / len(valid)
    edge = max(0.0, (best_price / market_average) - 1.0)
    return MarketEdge(
        market_type="1x2",
        selection=selection,
        best_price=round(best_price, 4),
        market_average=round(market_average, 4),
        edge=round(edge, 4),
        source=best_snapshot.source,
        bookmaker=best_snapshot.bookmaker,
        movement=best_snapshot.movement,
    )


def _is_world_cup_advisory_league(league: LeagueSettings | None) -> bool:
    if league is None:
        return False
    return league.code.upper() == "WORLD_CUP"


def _is_world_cup_live_league(league: LeagueSettings | None) -> bool:
    if league is None:
        return False
    return league.code.upper() == "WORLD_CUP" and league.strategy_mode == "live" and not league.paper_only


def _is_coverage_advisory_league(league: LeagueSettings | None) -> bool:
    if league is None:
        return False
    return league.paper_only and league.tier in {"elite_club", "major_tournament"}


def _apply_research_advisory(
    recommendation: Recommendation,
    match: Match,
    odds_snapshots: list[OddsSnapshot],
    findings: list[AgentFinding],
    league_settings: LeagueSettings | None,
    bookmaker_count: int,
) -> Recommendation:
    signal = _research_advisory_signal(match, findings)
    if signal is None or not _is_world_cup_advisory_league(league_settings):
        return recommendation
    if bookmaker_count < max(2, league_settings.min_bookmakers if league_settings else 2):
        return recommendation
    edge = _selection_market_edge(odds_snapshots, signal["selection"])
    if edge is None:
        return recommendation

    evidence_count = int(signal["evidence_count"])
    confidence = _clamp(max(recommendation.confidence, float(signal["confidence"])), 0.05, 0.93)
    value_score = _clamp(max(recommendation.value_score, 62.0 + confidence * 22.0 + min(8.0, evidence_count * 1.5)), 0.0, 100.0)
    risk_score = _clamp(max(18.0, min(recommendation.risk_score, 34.0)), 0.0, 100.0)
    score_breakdown = dict(recommendation.score_breakdown)
    score_breakdown["research_advisory"] = {
        "matched": True,
        "finding_id": signal["finding_id"],
        "agent_name": signal["agent_name"],
        "selection": signal["selection"],
        "confidence": round(confidence, 3),
        "evidence_count": evidence_count,
        "summary": signal["summary"],
    }
    score_breakdown["strategy_confidence_class"] = "research_advisory"
    odds_basis = dict(recommendation.odds_basis)
    odds_basis.update(
        {
            "best_price": edge.best_price,
            "market_average": edge.market_average,
            "edge": edge.edge,
            "source": edge.source,
            "bookmaker": edge.bookmaker,
            "movement": edge.movement,
            "research_advisory": score_breakdown["research_advisory"],
            "strategy_confidence_class": "research_advisory",
        }
    )
    risk_tags = sorted(set(recommendation.risk_tags + ["advisory_no_real_stake", "world_cup_research_advisory"]))
    selection_label = match.home_team if signal["selection"] == "HOME" else match.away_team if signal["selection"] == "AWAY" else "Draw"
    reason = (
        f"研究增强建议：外部证据 {evidence_count} 条支持 {selection_label}，"
        f"健康赔率最高价 {edge.best_price:.2f}，市场均价 {edge.market_average:.2f}，"
        f"建议置信度 {confidence:.2f}。世界杯策略仍为非下单建议，仓位置为 0。"
    )
    return recommendation.model_copy(
        update={
            "id": f"{match.id}-research-{edge.market_type}-{edge.selection}-v1",
            "market_type": MarketType(edge.market_type),
            "selection": edge.selection,
            "status": RecommendationStatus.advisory_recommended,
            "value_score": round(value_score, 2),
            "risk_score": round(risk_score, 2),
            "confidence": round(confidence, 3),
            "stake_units": 0.0,
            "odds_basis": odds_basis,
            "score_breakdown": score_breakdown,
            "risk_tags": risk_tags,
            "reason": reason,
        }
    )


def _apply_coverage_advisory(
    recommendation: Recommendation,
    match: Match,
    league_settings: LeagueSettings | None,
    bookmaker_count: int,
) -> Recommendation:
    if recommendation.status is not RecommendationStatus.paper_candidate:
        return recommendation
    if not _is_coverage_advisory_league(league_settings):
        return recommendation
    if recommendation.market_type is None or not recommendation.selection:
        return recommendation
    if bookmaker_count < max(2, league_settings.min_bookmakers if league_settings else 2):
        return recommendation
    tier_policy = recommendation.score_breakdown.get("tier_policy") or recommendation.odds_basis.get("tier_policy")
    if not isinstance(tier_policy, dict) or tier_policy.get("passed") is not True:
        return recommendation

    score_breakdown = dict(recommendation.score_breakdown)
    score_breakdown["coverage_advisory"] = {
        "matched": True,
        "tier": league_settings.tier if league_settings else None,
        "league_code": league_settings.code if league_settings else None,
        "reason": "paper_only_high_coverage_league",
    }
    score_breakdown["strategy_confidence_class"] = "coverage_advisory"
    odds_basis = dict(recommendation.odds_basis)
    odds_basis["coverage_advisory"] = score_breakdown["coverage_advisory"]
    odds_basis["strategy_confidence_class"] = "coverage_advisory"
    risk_tags = sorted(set(recommendation.risk_tags + ["advisory_no_real_stake", "coverage_advisory"]))
    reason = (
        recommendation.reason
        + " 大联赛/大赛覆盖建议已通过分层数据与赔率门槛，但当前联赛仍为纸面模式，仓位置为 0。"
    )
    return recommendation.model_copy(
        update={
            "status": RecommendationStatus.advisory_recommended,
            "stake_units": 0.0,
            "score_breakdown": score_breakdown,
            "odds_basis": odds_basis,
            "risk_tags": risk_tags,
            "reason": reason,
        }
    )


def _tier_policy_gates_failed(
    policy: TierPolicySettings,
    match: Match,
    value_score: float,
    risk_score: float,
    confidence: float,
    bookmaker_count: int,
) -> list[str]:
    gates_failed: list[str] = []
    if policy.min_data_quality is not None and match.data_completeness < policy.min_data_quality:
        gates_failed.append(f"tier_min_data_quality:{match.data_completeness:.2f}/{policy.min_data_quality:.2f}")
    if policy.min_value_score is not None and value_score < policy.min_value_score:
        gates_failed.append(f"tier_min_value_score:{value_score:.2f}/{policy.min_value_score:.2f}")
    if policy.max_risk_score is not None and risk_score > policy.max_risk_score:
        gates_failed.append(f"tier_max_risk_score:{risk_score:.2f}/{policy.max_risk_score:.2f}")
    if policy.min_confidence is not None and confidence < policy.min_confidence:
        gates_failed.append(f"tier_min_confidence:{confidence:.3f}/{policy.min_confidence:.3f}")
    if policy.min_bookmakers is not None and bookmaker_count < policy.min_bookmakers:
        gates_failed.append(f"tier_min_bookmakers:{bookmaker_count}/{policy.min_bookmakers}")
    return gates_failed


def _tier_policy_payload(
    policy: TierPolicySettings | None,
    bookmaker_count: int,
    gates_failed: list[str],
) -> dict:
    if policy is None:
        return {"matched": False, "bookmaker_count": bookmaker_count}
    return {
        "matched": True,
        "label": policy.label,
        "min_data_quality": policy.min_data_quality,
        "min_value_score": policy.min_value_score,
        "max_risk_score": policy.max_risk_score,
        "min_confidence": policy.min_confidence,
        "max_stake_units": policy.max_stake_units,
        "min_bookmakers": policy.min_bookmakers,
        "bookmaker_count": bookmaker_count,
        "passed": not gates_failed,
        "gates_failed": gates_failed,
    }


def _strategy_confidence_class(
    league: LeagueSettings | None,
    strategy_profile: StrategyProfileSettings | None,
) -> str:
    """Classify whether a score can become a live pick or must remain paper-only."""
    if strategy_profile is not None:
        return "validated_strategy"
    if league is None or league.paper_only or league.strategy_mode != "live":
        return "paper_candidate"
    return "live_scoring"


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
    elif strategy_confidence_class in {"elite_live_small_stake", "secondary_live_small_stake", "tournament_live_small_stake"}:
        reason += f" 命中分层小仓实盘门槛，最高仓位 {stake_units:.1f}u。"

    recommendation = Recommendation(
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
            "line": edge.line,
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
    recommendation = _apply_coverage_advisory(
        recommendation,
        match=match,
        league_settings=league_settings,
        bookmaker_count=bookmaker_count,
    )
    return _apply_research_advisory(
        recommendation,
        match=match,
        odds_snapshots=odds_snapshots,
        findings=findings,
        league_settings=league_settings,
        bookmaker_count=bookmaker_count,
    )
