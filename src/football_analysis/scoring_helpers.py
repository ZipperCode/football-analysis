from __future__ import annotations

from dataclasses import dataclass

from football_analysis.models import Match, OddsSnapshot
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


def clamp_score(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def best_market_edge(
    odds_snapshots: list[OddsSnapshot],
    *,
    min_odds: float,
    max_odds: float,
) -> MarketEdge | None:
    best: MarketEdge | None = None
    for snapshot in odds_snapshots:
        prices = snapshot.best_price or snapshot.outcome_odds
        for selection, price in prices.items():
            if price < min_odds or price > max_odds:
                continue
            average = snapshot.market_average.get(selection)
            if not average or average <= 1.0:
                continue
            if average < min_odds or average > max_odds:
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


def strategy_profile_for_edge(
    match: Match,
    edge: MarketEdge,
    settings: Settings,
) -> StrategyProfileSettings | None:
    league_code = football_data_uk_code(match, settings)
    if not league_code:
        return None
    selection = normalized_strategy_selection(edge.selection, edge.market_type)
    profiles = sorted(settings.strategy_profiles, key=lambda item: (not item.live_enabled, item.id))
    for profile in profiles:
        if not profile.active:
            continue
        if profile.league_code.upper() != league_code:
            continue
        if profile.market_type != edge.market_type:
            continue
        if selection in {normalized_strategy_selection(item, profile.market_type) for item in profile.selections}:
            return profile
    return None


def league_settings_for_match(match: Match, settings: Settings) -> LeagueSettings | None:
    normalized_league = match.league.strip().lower()
    for league in settings.leagues:
        if normalized_league in {value.strip().lower() for value in league_match_values(league) if value}:
            return league
    return None


def league_match_values(league: LeagueSettings) -> list[str]:
    values = [league.code, league.name, league.football_data_uk_code, league.football_data_org_code]
    if league.country and league.name:
        values.append(f"{league.country} - {league.name}")
    values.extend(league.aliases)
    return [value for value in values if value]


def football_data_uk_code(match: Match, settings: Settings) -> str | None:
    league = league_settings_for_match(match, settings)
    if not league:
        return None
    return (league.football_data_uk_code or league.code).upper()


def normalized_strategy_selection(selection: str, market_type: str | None = None) -> str:
    upper = selection.upper()
    market = (market_type or "").lower()
    if upper.startswith("AH_AWAY") or (market == "asian_handicap" and is_away_selection(upper)):
        return "AH_AWAY"
    if upper.startswith("AH_HOME") or (market == "asian_handicap" and is_home_selection(upper)):
        return "AH_HOME"
    return upper


def is_away_selection(selection: str) -> bool:
    return selection == "AWAY" or selection.startswith("AWAY_") or selection.startswith("AWAY:")


def is_home_selection(selection: str) -> bool:
    return selection == "HOME" or selection.startswith("HOME_") or selection.startswith("HOME:")


def strategy_profile_payload(profile: StrategyProfileSettings | None) -> dict:
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


def league_profile_payload(league: LeagueSettings | None) -> dict:
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


def tier_policy_for_league(league: LeagueSettings | None, settings: Settings) -> TierPolicySettings | None:
    if league is None:
        return None
    return settings.tier_policies.get(league.tier)


def bookmaker_count(odds_snapshots: list[OddsSnapshot]) -> int:
    return len(
        {
            snapshot.bookmaker.strip()
            for snapshot in odds_snapshots
            if snapshot.bookmaker and snapshot.bookmaker.strip().lower() != "market average"
        }
    )


def tier_policy_gates_failed(
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


def tier_policy_payload(
    policy: TierPolicySettings | None,
    bookmaker_count_value: int,
    gates_failed: list[str],
) -> dict:
    if policy is None:
        return {"matched": False, "bookmaker_count": bookmaker_count_value}
    return {
        "matched": True,
        "label": policy.label,
        "min_data_quality": policy.min_data_quality,
        "min_value_score": policy.min_value_score,
        "max_risk_score": policy.max_risk_score,
        "min_confidence": policy.min_confidence,
        "max_stake_units": policy.max_stake_units,
        "min_bookmakers": policy.min_bookmakers,
        "bookmaker_count": bookmaker_count_value,
        "passed": not gates_failed,
        "gates_failed": gates_failed,
    }


def strategy_confidence_class(
    league: LeagueSettings | None,
    strategy_profile: StrategyProfileSettings | None,
) -> str:
    if strategy_profile is not None:
        return "validated_strategy"
    if league is None or league.paper_only or league.strategy_mode != "live":
        return "paper_candidate"
    return "live_scoring"
