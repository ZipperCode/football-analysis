from __future__ import annotations

from datetime import datetime, timezone

from football_analysis.models import BetLog, Match, OddsSnapshot, Recommendation, RecommendationStatus
from football_analysis.settings import Settings, StrategyProfileSettings


def allocate_live_stakes(
    recommendations: list[Recommendation],
    matches_by_id: dict[str, Match],
    settings: Settings,
) -> list[Recommendation]:
    """Enforce the daily planned-stake cap across already-gated recommendations."""
    allocated: dict[str, Recommendation] = {item.id: item for item in recommendations}
    planned_by_date: dict[object, float] = {}
    candidates = sorted(
        [
            item
            for item in recommendations
            if item.status is RecommendationStatus.recommended
            and item.score_breakdown.get("live_gate", {}).get("passed") is True
        ],
        key=lambda item: (item.value_score, item.confidence),
        reverse=True,
    )
    for item in candidates:
        match = matches_by_id.get(item.match_id)
        if match is None:
            continue
        local_date = match.kickoff_at.astimezone(settings.app.tzinfo).date()
        live_gate = dict(item.score_breakdown.get("live_gate", {}))
        base_stake = float(live_gate.get("daily_stake_units") or 0.0)
        planned = planned_by_date.setdefault(local_date, base_stake)
        next_planned = round(planned + item.stake_units, 3)
        if next_planned > settings.live_trading.max_daily_stake_units:
            gate = (
                f"live_daily_planned_stake_limit:"
                f"{next_planned:.2f}/{settings.live_trading.max_daily_stake_units:.2f}"
            )
            allocated[item.id] = _downgrade_for_live_gate(
                item,
                gate,
                live_gate_updates={
                    "passed": False,
                    "planned_daily_stake_units": next_planned,
                    "max_daily_stake_units": settings.live_trading.max_daily_stake_units,
                    "applied_stake_units": 0.0,
                },
                reason_suffix=" 超过今日计划总仓位上限，降级为纸面候选。",
            )
            continue
        planned_by_date[local_date] = next_planned
        allocated[item.id] = _with_live_gate_updates(
            item,
            {
                "planned_daily_stake_units": next_planned,
                "max_daily_stake_units": settings.live_trading.max_daily_stake_units,
            },
        )
    return [allocated[item.id] for item in recommendations]


def apply_live_gate(
    recommendation: Recommendation,
    match: Match,
    odds_snapshots: list[OddsSnapshot],
    bet_logs: list[BetLog],
    settings: Settings,
    profile_review_actions: dict[str, str] | None = None,
) -> Recommendation:
    """Apply account and market gates before a recommendation can use real stake."""
    profile_id = _profile_id(recommendation)
    profile = _profile_by_id(settings, profile_id)
    profileless_live_allowed = _profileless_live_allowed(recommendation)
    review_actions = profile_review_actions or {}
    gates_failed = _live_gate_failures(
        recommendation,
        match,
        odds_snapshots,
        bet_logs,
        settings,
        profile,
        review_actions,
        profileless_live_allowed,
    )
    passed = recommendation.status is RecommendationStatus.recommended and not gates_failed
    stake_cap = _stake_cap(settings, profile)
    stake_units = min(recommendation.stake_units, stake_cap) if passed else 0.0
    rolling = _rolling_performance(bet_logs, settings)
    odds_freshness = _odds_freshness(odds_snapshots, recommendation, settings)
    effective_min_bookmakers = int(
        _effective_tier_threshold(
            recommendation,
            field_name="min_bookmakers",
            default=settings.live_trading.min_bookmakers,
            profileless_live_allowed=profileless_live_allowed,
        )
    )

    payload = {
        "passed": passed,
        "profile_id": profile_id,
        "profile_live_enabled": bool(profile.live_enabled) if profile else False,
        "profileless_live_allowed": profileless_live_allowed,
        "profile_review_action": review_actions.get(profile_id) if profile_id else None,
        "bookmaker_count": _bookmaker_count(odds_snapshots, recommendation),
        "min_bookmakers": effective_min_bookmakers,
        "freshest_odds_collected_at": odds_freshness["freshest_odds_collected_at"],
        "odds_age_minutes": odds_freshness["odds_age_minutes"],
        "max_odds_age_minutes": settings.live_trading.max_odds_age_minutes,
        "recent_consecutive_losses": _recent_consecutive_losses(bet_logs),
        "max_recent_consecutive_losses": settings.live_trading.max_recent_consecutive_losses,
        "rolling_settled_bets": rolling["settled_bets"],
        "rolling_window_settled_bets": settings.live_trading.rolling_window_settled_bets,
        "rolling_profit_units": rolling["profit_units"],
        "rolling_loss_units": rolling["loss_units"],
        "rolling_roi": rolling["roi"],
        "daily_stake_units": _daily_stake_units(bet_logs, match.kickoff_at),
        "max_daily_stake_units": settings.live_trading.max_daily_stake_units,
        "season_phase": _live_season_phase(match.kickoff_at),
        "stake_cap_units": stake_cap,
        "applied_stake_units": round(stake_units, 3),
        "gates_failed": gates_failed,
    }

    score_breakdown = dict(recommendation.score_breakdown)
    score_breakdown["live_gate"] = payload
    odds_basis = dict(recommendation.odds_basis)
    odds_basis["live_gate"] = payload
    risk_tags = sorted(set(recommendation.risk_tags + gates_failed))

    if recommendation.status is not RecommendationStatus.recommended:
        return recommendation.model_copy(
            update={
                "score_breakdown": score_breakdown,
                "odds_basis": odds_basis,
                "risk_tags": risk_tags,
                "stake_units": 0.0,
            }
        )
    if passed:
        return recommendation.model_copy(
            update={
                "score_breakdown": score_breakdown,
                "odds_basis": odds_basis,
                "risk_tags": risk_tags,
                "stake_units": round(stake_units, 3),
                "reason": recommendation.reason
                + f" 实盘闸门通过，单注仓位按 profile/全局上限限制为 {stake_units:.2f}u。",
            }
        )
    return recommendation.model_copy(
        update={
            "status": RecommendationStatus.paper_candidate,
            "stake_units": 0.0,
            "score_breakdown": score_breakdown,
            "odds_basis": odds_basis,
            "risk_tags": risk_tags,
            "reason": recommendation.reason + " 未通过实盘闸门，保留为纸面候选，仓位置为 0。",
        }
    )


def _downgrade_for_live_gate(
    recommendation: Recommendation,
    gate: str,
    live_gate_updates: dict,
    reason_suffix: str,
) -> Recommendation:
    live_gate = dict(recommendation.score_breakdown.get("live_gate", {}))
    gates_failed = list(live_gate.get("gates_failed", []))
    if gate not in gates_failed:
        gates_failed.append(gate)
    live_gate.update(live_gate_updates)
    live_gate["gates_failed"] = gates_failed
    score_breakdown = dict(recommendation.score_breakdown)
    score_breakdown["live_gate"] = live_gate
    odds_basis = dict(recommendation.odds_basis)
    odds_basis["live_gate"] = live_gate
    risk_tags = sorted(set(recommendation.risk_tags + [gate]))
    return recommendation.model_copy(
        update={
            "status": RecommendationStatus.paper_candidate,
            "stake_units": 0.0,
            "score_breakdown": score_breakdown,
            "odds_basis": odds_basis,
            "risk_tags": risk_tags,
            "reason": recommendation.reason + reason_suffix,
        }
    )


def _with_live_gate_updates(recommendation: Recommendation, live_gate_updates: dict) -> Recommendation:
    live_gate = dict(recommendation.score_breakdown.get("live_gate", {}))
    live_gate.update(live_gate_updates)
    score_breakdown = dict(recommendation.score_breakdown)
    score_breakdown["live_gate"] = live_gate
    odds_basis = dict(recommendation.odds_basis)
    odds_basis["live_gate"] = live_gate
    return recommendation.model_copy(update={"score_breakdown": score_breakdown, "odds_basis": odds_basis})


def _live_gate_failures(
    recommendation: Recommendation,
    match: Match,
    odds_snapshots: list[OddsSnapshot],
    bet_logs: list[BetLog],
    settings: Settings,
    profile: StrategyProfileSettings | None,
    profile_review_actions: dict[str, str],
    profileless_live_allowed: bool,
) -> list[str]:
    gates_failed: list[str] = []
    live = settings.live_trading
    min_data_quality = float(
        _effective_tier_threshold(
            recommendation,
            field_name="min_data_quality",
            default=live.min_data_quality,
            profileless_live_allowed=profileless_live_allowed,
        )
    )
    min_value_score = float(
        _effective_tier_threshold(
            recommendation,
            field_name="min_value_score",
            default=live.min_value_score,
            profileless_live_allowed=profileless_live_allowed,
        )
    )
    max_risk_score = float(
        _effective_tier_threshold(
            recommendation,
            field_name="max_risk_score",
            default=live.max_risk_score,
            profileless_live_allowed=profileless_live_allowed,
        )
    )
    min_confidence = float(
        _effective_tier_threshold(
            recommendation,
            field_name="min_confidence",
            default=live.min_confidence,
            profileless_live_allowed=profileless_live_allowed,
        )
    )
    min_bookmakers = int(
        _effective_tier_threshold(
            recommendation,
            field_name="min_bookmakers",
            default=live.min_bookmakers,
            profileless_live_allowed=profileless_live_allowed,
        )
    )
    if not live.enabled:
        gates_failed.append("live_trading_disabled")
    if recommendation.status is not RecommendationStatus.recommended:
        gates_failed.append(f"live_status_not_recommended:{recommendation.status.value}")
    gates_failed.extend(_world_cup_final_gate_failures(recommendation, match, settings))
    if profile is None and not profileless_live_allowed:
        gates_failed.append("live_missing_strategy_profile")
    elif profile is not None and not profile.live_enabled:
        gates_failed.append(f"live_profile_not_enabled:{profile.id}")
    elif profile is not None and profile_review_actions.get(profile.id) in {"pause_live", "demote_to_paper"}:
        gates_failed.append(f"live_profile_review_action:{profile_review_actions[profile.id]}")
    if match.data_completeness < min_data_quality:
        gates_failed.append(f"live_min_data_quality:{match.data_completeness:.2f}/{min_data_quality:.2f}")
    if recommendation.value_score < min_value_score:
        gates_failed.append(f"live_min_value_score:{recommendation.value_score:.2f}/{min_value_score:.2f}")
    if recommendation.risk_score > max_risk_score:
        gates_failed.append(f"live_max_risk_score:{recommendation.risk_score:.2f}/{max_risk_score:.2f}")
    if recommendation.confidence < min_confidence:
        gates_failed.append(f"live_min_confidence:{recommendation.confidence:.3f}/{min_confidence:.3f}")
    edge = _recommendation_edge(recommendation)
    if edge < live.min_edge:
        gates_failed.append(f"live_min_edge:{edge:.4f}/{live.min_edge:.4f}")
    bookmaker_count = _bookmaker_count(odds_snapshots, recommendation)
    if bookmaker_count < min_bookmakers:
        gates_failed.append(f"live_min_bookmakers:{bookmaker_count}/{min_bookmakers}")
    odds_age_minutes = _odds_age_minutes(odds_snapshots, recommendation)
    if odds_age_minutes is not None and odds_age_minutes > live.max_odds_age_minutes:
        gates_failed.append(f"live_max_odds_age_minutes:{odds_age_minutes}/{live.max_odds_age_minutes}")
    daily_stake = _daily_stake_units(bet_logs, match.kickoff_at)
    if daily_stake >= live.max_daily_stake_units:
        gates_failed.append(f"live_daily_stake_limit:{daily_stake:.2f}/{live.max_daily_stake_units:.2f}")
    recent_losses = _recent_consecutive_losses(bet_logs)
    if recent_losses >= live.max_recent_consecutive_losses:
        gates_failed.append(f"live_recent_consecutive_losses:{recent_losses}/{live.max_recent_consecutive_losses}")
    gates_failed.extend(_rolling_performance_failures(bet_logs, settings))
    if profile is not None:
        gates_failed.extend(_phase_failures(profile, match))
        gates_failed.extend(_profile_failures(profile, settings))
    return gates_failed


def _profileless_live_allowed(recommendation: Recommendation) -> bool:
    confidence_class = str(
        recommendation.score_breakdown.get("strategy_confidence_class")
        or recommendation.odds_basis.get("strategy_confidence_class")
        or ""
    )
    if confidence_class not in {
        "elite_live_small_stake",
        "secondary_live_small_stake",
        "tournament_live_small_stake",
    }:
        return False
    tier_policy = recommendation.score_breakdown.get("tier_policy") or recommendation.odds_basis.get("tier_policy")
    return isinstance(tier_policy, dict) and tier_policy.get("passed") is True


def _world_cup_final_gate_failures(
    recommendation: Recommendation,
    match: Match,
    settings: Settings,
) -> list[str]:
    league = next(
        (
            item
            for item in settings.leagues
            if item.code.upper() == "WORLD_CUP"
            and match.league.strip().lower()
            in {str(value).strip().lower() for value in [item.code, item.name, *(item.aliases or [])] if value}
        ),
        None,
    )
    if league is None:
        return []
    if league.strategy_mode != "live" or league.paper_only:
        return []
    market_type = recommendation.market_type.value if recommendation.market_type else None
    if market_type != "1x2":
        return ["world_cup_market_not_1x2"]
    gate = (
        recommendation.score_breakdown.get("world_cup_high_winrate")
        or recommendation.odds_basis.get("world_cup_high_winrate")
        or {}
    )
    if isinstance(gate, dict) and gate.get("stage") == "final" and gate.get("passed") is True:
        return []
    return ["world_cup_final_gate_required"]


def _effective_tier_threshold(
    recommendation: Recommendation,
    field_name: str,
    default: float | int,
    profileless_live_allowed: bool,
) -> float | int:
    if not profileless_live_allowed:
        return default
    tier_policy = recommendation.score_breakdown.get("tier_policy") or recommendation.odds_basis.get("tier_policy")
    if not isinstance(tier_policy, dict):
        return default
    value = tier_policy.get(field_name)
    if value is None:
        return default
    try:
        return int(value) if isinstance(default, int) else float(value)
    except (TypeError, ValueError):
        return default


def _phase_failures(profile: StrategyProfileSettings, match: Match) -> list[str]:
    phases = {phase.strip().lower() for phase in profile.season_phases if phase.strip()}
    if not phases or "all" in phases:
        return []
    observed = _live_season_phase(match.kickoff_at)
    if observed in phases:
        return []
    return [f"live_profile_season_phase:{observed}_not_in:{','.join(sorted(phases))}"]


def _profile_failures(profile: StrategyProfileSettings, settings: Settings) -> list[str]:
    live = settings.live_trading
    gates_failed: list[str] = []
    long_bets = profile.long_horizon_settled_bets or profile.settled_bets
    long_roi = profile.long_horizon_roi if profile.long_horizon_roi is not None else profile.roi
    if long_bets < live.min_long_horizon_bets:
        gates_failed.append(f"live_min_long_horizon_bets:{long_bets}/{live.min_long_horizon_bets}")
    if long_roi is None or long_roi < live.min_long_horizon_roi:
        observed = "none" if long_roi is None else f"{long_roi:.4f}"
        gates_failed.append(f"live_min_long_horizon_roi:{observed}/{live.min_long_horizon_roi:.4f}")
    if profile.holdout_settled_bets < live.min_holdout_bets:
        gates_failed.append(f"live_min_holdout_bets:{profile.holdout_settled_bets}/{live.min_holdout_bets}")
    if profile.holdout_roi is None or profile.holdout_roi < live.min_holdout_roi:
        observed = "none" if profile.holdout_roi is None else f"{profile.holdout_roi:.4f}"
        gates_failed.append(f"live_min_holdout_roi:{observed}/{live.min_holdout_roi:.4f}")
    positive_rate = (
        profile.holdout_positive_seasons / profile.holdout_season_count
        if profile.holdout_season_count
        else 0.0
    )
    if positive_rate < live.min_holdout_positive_rate:
        gates_failed.append(f"live_min_holdout_positive_rate:{positive_rate:.3f}/{live.min_holdout_positive_rate:.3f}")
    if profile.average_clv is None or profile.average_clv < live.min_average_clv:
        observed = "none" if profile.average_clv is None else f"{profile.average_clv:.4f}"
        gates_failed.append(f"live_min_average_clv:{observed}/{live.min_average_clv:.4f}")
    if profile.worst_season_roi is not None and profile.worst_season_roi < live.max_worst_season_roi:
        gates_failed.append(f"live_worst_season_roi:{profile.worst_season_roi:.4f}/{live.max_worst_season_roi:.4f}")
    return gates_failed


def _profile_id(recommendation: Recommendation) -> str | None:
    profile = recommendation.score_breakdown.get("strategy_profile") or recommendation.odds_basis.get("strategy_profile")
    if isinstance(profile, dict) and profile.get("matched"):
        value = profile.get("id")
        return str(value) if value else None
    return None


def _profile_by_id(settings: Settings, profile_id: str | None) -> StrategyProfileSettings | None:
    if profile_id is None:
        return None
    for profile in settings.strategy_profiles:
        if profile.id == profile_id:
            return profile
    return None


def _bookmaker_count(odds_snapshots: list[OddsSnapshot], recommendation: Recommendation) -> int:
    return len(
        {
            snapshot.bookmaker.strip()
            for snapshot in _relevant_odds_snapshots(odds_snapshots, recommendation)
            if snapshot.bookmaker
            and snapshot.bookmaker.strip().lower() != "market average"
        }
    )


def _relevant_odds_snapshots(
    odds_snapshots: list[OddsSnapshot],
    recommendation: Recommendation,
) -> list[OddsSnapshot]:
    market_type = recommendation.market_type.value if recommendation.market_type else None
    return [snapshot for snapshot in odds_snapshots if snapshot.market_type.value == market_type]


def _odds_freshness(
    odds_snapshots: list[OddsSnapshot],
    recommendation: Recommendation,
    settings: Settings,
) -> dict[str, int | str | None]:
    freshest = _freshest_odds_collected_at(odds_snapshots, recommendation)
    if freshest is None:
        return {"freshest_odds_collected_at": None, "odds_age_minutes": None}
    freshest_utc = _as_utc(freshest)
    return {
        "freshest_odds_collected_at": freshest_utc.astimezone(settings.app.tzinfo).isoformat(),
        "odds_age_minutes": _age_minutes_since(freshest_utc),
    }


def _odds_age_minutes(odds_snapshots: list[OddsSnapshot], recommendation: Recommendation) -> int | None:
    freshest = _freshest_odds_collected_at(odds_snapshots, recommendation)
    if freshest is None:
        return None
    return _age_minutes_since(_as_utc(freshest))


def _freshest_odds_collected_at(
    odds_snapshots: list[OddsSnapshot],
    recommendation: Recommendation,
) -> datetime | None:
    relevant = _relevant_odds_snapshots(odds_snapshots, recommendation)
    if not relevant:
        return None
    return max((snapshot.collected_at for snapshot in relevant), key=_as_utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_minutes_since(value: datetime) -> int:
    age_seconds = max(0.0, (datetime.now(timezone.utc) - _as_utc(value)).total_seconds())
    return int(age_seconds // 60)


def _recommendation_edge(recommendation: Recommendation) -> float:
    value = recommendation.odds_basis.get("edge")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _stake_cap(settings: Settings, profile: StrategyProfileSettings | None) -> float:
    caps = [settings.live_trading.max_stake_units_per_pick, settings.thresholds.max_stake_units]
    if profile is not None and profile.max_stake_units is not None:
        caps.append(profile.max_stake_units)
    return max(0.0, min(caps))


def _daily_stake_units(bet_logs: list[BetLog], kickoff_at: datetime) -> float:
    target_date = kickoff_at.date()
    return round(sum(bet.stake_units for bet in bet_logs if bet.placed_at.date() == target_date), 3)


def _live_season_phase(kickoff_at: datetime) -> str:
    # European domestic seasons generally run August-May; live gates use a coarse calendar proxy.
    if kickoff_at.month in {8, 9, 10, 11}:
        return "early"
    if kickoff_at.month in {12, 1, 2}:
        return "middle"
    return "late"


def _recent_consecutive_losses(bet_logs: list[BetLog]) -> int:
    settled = sorted(
        [bet for bet in bet_logs if bet.profit_units is not None],
        key=lambda item: item.placed_at,
        reverse=True,
    )
    losses = 0
    for bet in settled:
        if (bet.profit_units or 0.0) < 0:
            losses += 1
            continue
        break
    return losses


def _rolling_performance_failures(bet_logs: list[BetLog], settings: Settings) -> list[str]:
    live = settings.live_trading
    stats = _rolling_performance(bet_logs, settings)
    if stats["settled_bets"] < live.min_rolling_settled_bets:
        return []
    gates_failed: list[str] = []
    loss_units = float(stats["loss_units"])
    if live.max_rolling_loss_units > 0 and loss_units >= live.max_rolling_loss_units:
        gates_failed.append(f"live_rolling_loss_units:{loss_units:.2f}/{live.max_rolling_loss_units:.2f}")
    roi = stats["roi"]
    if roi is not None and float(roi) <= live.min_rolling_roi:
        gates_failed.append(f"live_rolling_roi:{float(roi):.3f}/{live.min_rolling_roi:.3f}")
    return gates_failed


def _rolling_performance(bet_logs: list[BetLog], settings: Settings) -> dict[str, float | int | None]:
    settled = sorted(
        [bet for bet in bet_logs if bet.profit_units is not None],
        key=lambda item: item.placed_at,
        reverse=True,
    )[: settings.live_trading.rolling_window_settled_bets]
    stake = sum(bet.stake_units for bet in settled)
    profit = sum(bet.profit_units or 0.0 for bet in settled)
    roi = profit / stake if stake else None
    return {
        "settled_bets": len(settled),
        "profit_units": round(profit, 3),
        "loss_units": round(max(0.0, -profit), 3),
        "roi": round(roi, 4) if roi is not None else None,
    }
