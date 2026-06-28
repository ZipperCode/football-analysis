"""Portfolio exposure controls for already gated betting recommendations."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Protocol

from football_analysis.models import Match, Recommendation, RecommendationStatus


class PortfolioConfig(Protocol):
    max_daily_exposure_fraction: float
    correlation_penalty_same_league: float
    correlation_penalty_same_match: float
    max_correlated_stakes_per_day: int


def apply_portfolio_constraints(
    recommendations: list[Recommendation],
    matches_by_id: dict[str, Match],
    config: PortfolioConfig,
    *,
    bankroll_units: float,
) -> list[Recommendation]:
    """Apply daily exposure, same-match exclusion, and same-league stake penalties."""
    allocated: dict[str, Recommendation] = {item.id: item for item in recommendations}
    candidates = sorted(
        [
            item
            for item in recommendations
            if item.status is RecommendationStatus.recommended and item.stake_units > 0
        ],
        key=lambda item: (item.value_score, item.confidence),
        reverse=True,
    )
    budget_by_date: dict[date, float] = defaultdict(float)
    accepted_by_date: dict[date, list[Recommendation]] = defaultdict(list)
    daily_budget = max(0.0, bankroll_units * config.max_daily_exposure_fraction)

    for item in candidates:
        match = matches_by_id.get(item.match_id)
        if match is None:
            continue
        local_date = match.kickoff_at.date()
        accepted = accepted_by_date[local_date]
        portfolio_payload = _portfolio_payload(item)
        portfolio_payload["daily_budget_units"] = round(daily_budget, 3)

        same_match = [_item for _item in accepted if _same_match(_item, item)]
        if same_match:
            allocated[item.id] = _zero_for_portfolio(
                item,
                portfolio_payload,
                "portfolio_same_match_exclusion",
            )
            continue

        same_league_count = sum(1 for accepted_item in accepted if _same_league(accepted_item, item, matches_by_id))
        stake = float(item.stake_units)
        if same_league_count:
            stake = round(stake * (1.0 - config.correlation_penalty_same_league), 3)
            portfolio_payload["same_league_count"] = same_league_count
            portfolio_payload["same_league_penalty"] = config.correlation_penalty_same_league
            portfolio_payload["portfolio_adjusted"] = True

        if same_league_count >= config.max_correlated_stakes_per_day:
            allocated[item.id] = _zero_for_portfolio(
                item,
                portfolio_payload,
                "portfolio_correlated_stake_limit",
            )
            continue

        if budget_by_date[local_date] + stake > daily_budget:
            remaining = max(0.0, daily_budget - budget_by_date[local_date])
            if remaining <= 0:
                allocated[item.id] = _zero_for_portfolio(
                    item,
                    portfolio_payload,
                    "portfolio_daily_exposure_limit",
                )
                continue
            stake = round(min(stake, remaining), 3)
            portfolio_payload["portfolio_adjusted"] = True
            portfolio_payload["daily_exposure_limited"] = True

        budget_by_date[local_date] = round(budget_by_date[local_date] + stake, 3)
        portfolio_payload["applied_stake_units"] = stake
        portfolio_payload["planned_daily_stake_units"] = budget_by_date[local_date]
        portfolio_payload["correlation_group"] = _correlation_group(item, match)
        accepted_item = _with_portfolio_payload(item, portfolio_payload, stake)
        allocated[item.id] = accepted_item
        accepted.append(accepted_item)

    return [allocated[item.id] for item in recommendations]


def _portfolio_payload(item: Recommendation) -> dict[str, object]:
    live_gate = item.score_breakdown.get("live_gate", {})
    payload = dict(live_gate if isinstance(live_gate, dict) else {})
    payload.setdefault("portfolio_adjusted", False)
    return payload


def _with_portfolio_payload(item: Recommendation, payload: dict[str, object], stake_units: float) -> Recommendation:
    score_breakdown = dict(item.score_breakdown)
    score_breakdown["live_gate"] = payload
    odds_basis = dict(item.odds_basis)
    odds_basis["live_gate"] = payload
    return item.model_copy(
        update={
            "stake_units": round(stake_units, 3),
            "score_breakdown": score_breakdown,
            "odds_basis": odds_basis,
        }
    )


def _zero_for_portfolio(item: Recommendation, payload: dict[str, object], reason: str) -> Recommendation:
    payload["passed"] = False
    payload["portfolio_adjusted"] = True
    payload["applied_stake_units"] = 0.0
    payload["portfolio_reason"] = reason
    updated = _with_portfolio_payload(item, payload, 0.0)
    risk_tags = sorted(set(updated.risk_tags + [reason]))
    return updated.model_copy(
        update={
            "status": RecommendationStatus.paper_candidate,
            "risk_tags": risk_tags,
            "reason": updated.reason + " 投注组合约束未通过，降级为纸面候选。",
        }
    )


def _same_match(left: Recommendation, right: Recommendation) -> bool:
    return left.match_id == right.match_id


def _same_league(left: Recommendation, right: Recommendation, matches_by_id: dict[str, Match]) -> bool:
    left_match = matches_by_id.get(left.match_id)
    right_match = matches_by_id.get(right.match_id)
    return bool(left_match and right_match and left_match.league == right_match.league)


def _correlation_group(item: Recommendation, match: Match) -> str:
    market = item.market_type.value if hasattr(item.market_type, "value") else str(item.market_type)
    return f"{match.kickoff_at.date()}:{match.league}:{market}"
