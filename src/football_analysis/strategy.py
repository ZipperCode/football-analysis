from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from statistics import mean

from pydantic import BaseModel, Field

from football_analysis.contracts import HistoricalMatchRow
from football_analysis.db import StructuredRepository


@dataclass(frozen=True)
class StrategyParams:
    mode: str = "form_edge"
    min_edge: float = 0.06
    min_odds: float = 1.80
    max_odds: float = 4.50
    min_matches: int = 8
    max_bets_per_season: int = 140
    allow_draw: bool = False
    require_positive_recent: bool = True
    min_strength: float = 0.0
    min_prob_gap: float = 0.04
    selection_bias: str = "all"


class StrategyResult(BaseModel):
    league: str
    train_seasons: list[str] = Field(default_factory=list)
    test_seasons: list[str] = Field(default_factory=list)
    params: dict
    matches: int
    bets: int
    settled_bets: int
    profit_units: float
    roi: float | None
    average_odds: float | None
    hit_rate: float | None
    average_clv: float | None
    selected_by: str = "direct"
    season_breakdown: list[dict] = Field(default_factory=list)


class WalkForwardResult(BaseModel):
    league: str
    folds: list[StrategyResult]
    bets: int
    settled_bets: int
    profit_units: float
    roi: float | None
    positive_folds: int
    fold_count: int
    average_clv: float | None


@dataclass
class TeamState:
    matches: int = 0
    points: float = 0.0
    goals_for: int = 0
    goals_against: int = 0
    home_matches: int = 0
    home_points: float = 0.0
    away_matches: int = 0
    away_points: float = 0.0

    @property
    def ppg(self) -> float:
        return self.points / self.matches if self.matches else 0.0

    @property
    def home_ppg(self) -> float:
        return self.home_points / self.home_matches if self.home_matches else self.ppg

    @property
    def away_ppg(self) -> float:
        return self.away_points / self.away_matches if self.away_matches else self.ppg

    @property
    def goal_diff_per_match(self) -> float:
        return (self.goals_for - self.goals_against) / self.matches if self.matches else 0.0


@dataclass(frozen=True)
class BetDecision:
    row: HistoricalMatchRow
    selection: str
    odds: float
    closing_odds: float | None
    model_probability: float
    market_probability: float
    edge: float
    profit_units: float | None = None


def optimize_strategy(
    repository: StructuredRepository,
    league: str,
    train_seasons: list[str],
    test_seasons: list[str],
    min_test_bets: int = 80,
) -> StrategyResult:
    all_rows = _load_rows(repository, league, sorted(set(train_seasons + test_seasons)))
    grid = [
        StrategyParams(
            mode=mode,
            min_edge=min_edge,
            min_odds=min_odds,
            max_odds=max_odds,
            min_matches=min_matches,
            max_bets_per_season=max_bets_per_season,
            allow_draw=allow_draw,
            require_positive_recent=require_positive_recent,
            min_strength=min_strength,
            min_prob_gap=min_prob_gap,
            selection_bias=selection_bias,
        )
        for (
            mode,
            min_edge,
            min_odds,
            max_odds,
            min_matches,
            max_bets_per_season,
            allow_draw,
            require_positive_recent,
            min_strength,
            min_prob_gap,
            selection_bias,
        ) in product(
            ["market_value", "asian_value"],
            [0.015, 0.025, 0.04],
            [1.55, 1.75, 2.0],
            [3.0, 3.25, 4.5],
            [5, 8],
            [60, 80, 140],
            [False],
            [True],
            [0.0, 0.3],
            [0.02],
            ["home", "away", "ah_home", "ah_away"],
        )
        if (
            (mode == "asian_value" and selection_bias in {"ah_home", "ah_away"})
            or (mode == "market_value" and selection_bias in {"home", "away"})
        )
    ]

    best: tuple[float, StrategyParams, StrategyResult] | None = None
    for params in grid:
        train = run_strategy_on_rows(all_rows, league, train_seasons, params)
        if train.bets < 120 or train.roi is None:
            continue
        train_seasons_positive = sum(1 for item in train.season_breakdown if (item.get("roi") or 0) > 0)
        if train.roi < 0.03 or train_seasons_positive < max(1, len(train_seasons) // 2):
            continue
        score = _selection_score(train)
        if best is None or score > best[0]:
            best = (score, params, train)

    if best is None:
        fallback = StrategyParams()
        return run_strategy_on_rows(all_rows, league, test_seasons, fallback).model_copy(
            update={
                "train_seasons": train_seasons,
                "test_seasons": test_seasons,
                "selected_by": "fallback_no_training_candidate",
            }
        )

    _, params, train = best
    test = run_strategy_on_rows(all_rows, league, test_seasons, params)
    if test.bets < min_test_bets:
        return test.model_copy(
            update={
                "train_seasons": train_seasons,
                "test_seasons": test_seasons,
                "params": {**params.__dict__, "warning": f"test_bets_below_minimum:{test.bets}/{min_test_bets}"},
                "selected_by": "train_only_below_test_volume",
            }
        )
    return test.model_copy(
        update={
            "train_seasons": train_seasons,
            "test_seasons": test_seasons,
            "params": {**params.__dict__, "train_roi": train.roi, "train_bets": train.bets},
            "selected_by": "train_only",
        }
    )


def walk_forward_optimize(
    repository: StructuredRepository,
    league: str,
    seasons: list[str],
    min_train_seasons: int = 2,
    min_test_bets: int = 30,
) -> WalkForwardResult:
    folds: list[StrategyResult] = []
    for index in range(min_train_seasons, len(seasons)):
        train = seasons[:index]
        test = [seasons[index]]
        folds.append(optimize_strategy(repository, league, train, test, min_test_bets=min_test_bets))
    settled = sum(fold.settled_bets for fold in folds)
    profit = sum(fold.profit_units for fold in folds)
    roi = profit / settled if settled else None
    clv_values = [fold.average_clv for fold in folds if fold.average_clv is not None]
    average_clv = mean(clv_values) if clv_values else None
    return WalkForwardResult(
        league=league,
        folds=folds,
        bets=sum(fold.bets for fold in folds),
        settled_bets=settled,
        profit_units=round(profit, 3),
        roi=round(roi, 4) if roi is not None else None,
        positive_folds=sum(1 for fold in folds if (fold.roi or 0) > 0),
        fold_count=len(folds),
        average_clv=round(average_clv, 4) if average_clv is not None else None,
    )


def run_strategy(
    repository: StructuredRepository,
    league: str,
    seasons: list[str],
    params: StrategyParams,
) -> StrategyResult:
    return run_strategy_on_rows(_load_rows(repository, league, seasons), league, seasons, params)


def run_strategy_on_rows(
    all_rows: list[HistoricalMatchRow],
    league: str,
    seasons: list[str],
    params: StrategyParams,
) -> StrategyResult:
    rows = sorted(
        [
            row
            for row in all_rows
            if row.league == league and row.season in set(seasons)
        ],
        key=lambda item: (item.season, item.date, item.home_team, item.away_team),
    )
    state: dict[str, TeamState] = {}
    decisions_by_season: dict[str, list[BetDecision]] = {season: [] for season in seasons}

    for row in rows:
        home_state = state.get(row.home_team, TeamState())
        away_state = state.get(row.away_team, TeamState())
        if row.season not in decisions_by_season:
            decisions_by_season[row.season] = []
        if _eligible_state(home_state, away_state, params):
            decision = _decision(row, home_state, away_state, params)
            if decision is not None:
                decisions_by_season[row.season].append(decision)
        _update_state(state, row)

    trimmed_decisions: list[BetDecision] = []
    for season, decisions in decisions_by_season.items():
        ranked = sorted(decisions, key=lambda item: item.edge, reverse=True)[: params.max_bets_per_season]
        trimmed_decisions.extend(ranked)

    season_breakdown = [
        _summarize_decisions(
            league,
            [season],
            sorted(decisions_by_season.get(season, []), key=lambda item: item.edge, reverse=True)[
                : params.max_bets_per_season
            ],
        )
        for season in seasons
    ]
    summary = _summarize_decisions(league, seasons, trimmed_decisions)
    return StrategyResult(
        league=league,
        train_seasons=[],
        test_seasons=seasons,
        params=params.__dict__,
        matches=len(rows),
        bets=summary["bets"],
        settled_bets=summary["settled_bets"],
        profit_units=summary["profit_units"],
        roi=summary["roi"],
        average_odds=summary["average_odds"],
        hit_rate=summary["hit_rate"],
        average_clv=summary["average_clv"],
        selected_by="direct",
        season_breakdown=season_breakdown,
    )


def _load_rows(repository: StructuredRepository, league: str, seasons: list[str]) -> list[HistoricalMatchRow]:
    season_set = set(seasons)
    return [
        row
        for row in repository.list_models("historical_matches", HistoricalMatchRow)
        if row.league == league and row.season in season_set
    ]


def _eligible_state(home_state: TeamState, away_state: TeamState, params: StrategyParams) -> bool:
    return home_state.matches >= params.min_matches and away_state.matches >= params.min_matches


def _selection_score(result: StrategyResult) -> float:
    positive_seasons = sum(1 for item in result.season_breakdown if (item.get("roi") or 0) > 0)
    consistency = positive_seasons / len(result.season_breakdown) if result.season_breakdown else 0.0
    volume_penalty = 0.0 if result.bets >= 180 else (180 - result.bets) / 1000
    clv_bonus = min(result.average_clv or 0.0, 0.03)
    return (result.roi or -1.0) + consistency * 0.06 + clv_bonus - volume_penalty


def _decision(row: HistoricalMatchRow, home_state: TeamState, away_state: TeamState, params: StrategyParams) -> BetDecision | None:
    candidates: list[BetDecision] = []
    strength_gap = (home_state.home_ppg - away_state.away_ppg) + 0.45 * (home_state.goal_diff_per_match - away_state.goal_diff_per_match)
    if params.mode == "asian_value":
        return _asian_decision(row, params, strength_gap)

    home_probability = _clamp(0.46 + strength_gap * 0.13, 0.18, 0.74)
    away_probability = _clamp(0.28 - strength_gap * 0.10, 0.12, 0.58)
    draw_probability = _clamp(1.0 - home_probability - away_probability, 0.12, 0.36)
    total = home_probability + draw_probability + away_probability
    probabilities = {
        "HOME": home_probability / total,
        "DRAW": draw_probability / total,
        "AWAY": away_probability / total,
    }
    odds_map = {
        "HOME": (_stake_odds(row.home_odds, row.max_home_odds), row.closing_home_odds, row.avg_home_odds),
        "DRAW": (_stake_odds(row.draw_odds, row.max_draw_odds), row.closing_draw_odds, row.avg_draw_odds),
        "AWAY": (_stake_odds(row.away_odds, row.max_away_odds), row.closing_away_odds, row.avg_away_odds),
    }
    for selection, probability in probabilities.items():
        if selection == "DRAW" and not params.allow_draw:
            continue
        if params.selection_bias == "home" and selection != "HOME":
            continue
        if params.selection_bias == "away" and selection != "AWAY":
            continue
        if params.selection_bias == "non_draw" and selection == "DRAW":
            continue
        odds, closing, average_odds = odds_map[selection]
        if not odds or odds < params.min_odds or odds > params.max_odds:
            continue
        market_probability = 1.0 / odds
        edge = _edge(params.mode, selection, probability, market_probability, odds, average_odds)
        if edge < params.min_edge or probability - max(p for key, p in probabilities.items() if key != selection) < params.min_prob_gap:
            continue
        if params.require_positive_recent and selection == "HOME" and strength_gap <= 0:
            continue
        if params.require_positive_recent and selection == "AWAY" and strength_gap >= 0:
            continue
        candidates.append(
            BetDecision(
                row=row,
                selection=selection,
                odds=odds,
                closing_odds=closing,
                model_probability=probability,
                market_probability=market_probability,
                edge=edge,
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.edge)


def _asian_decision(row: HistoricalMatchRow, params: StrategyParams, strength_gap: float) -> BetDecision | None:
    if row.ah_line is None:
        return None
    sides: list[str]
    if params.selection_bias == "ah_home":
        sides = ["home"]
    elif params.selection_bias == "ah_away":
        sides = ["away"]
    else:
        sides = ["home", "away"]

    candidates: list[BetDecision] = []
    for side in sides:
        if side == "home":
            if strength_gap < params.min_strength:
                continue
            odds = row.ah_home_odds
            average_odds = row.avg_ah_home_odds
            closing_odds = row.closing_ah_home_odds
            line = row.ah_line
            selection = f"AH_HOME({line:+g})"
        else:
            if -strength_gap < params.min_strength:
                continue
            odds = row.ah_away_odds
            average_odds = row.avg_ah_away_odds
            closing_odds = row.closing_ah_away_odds
            line = -row.ah_line
            selection = f"AH_AWAY({line:+g})"
        if not odds or not average_odds or odds < params.min_odds or odds > params.max_odds:
            continue
        edge = (odds / average_odds) - 1.0
        if edge < params.min_edge:
            continue
        profit = _settle_ah(row.home_goals, row.away_goals, row.ah_line, odds, side)
        candidates.append(
            BetDecision(
                row=row,
                selection=selection,
                odds=odds,
                closing_odds=closing_odds,
                model_probability=0.0,
                market_probability=1.0 / odds,
                edge=edge,
                profit_units=profit,
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.edge)


def _stake_odds(open_odds: float | None, max_odds: float | None) -> float | None:
    return max_odds or open_odds


def _edge(
    mode: str,
    selection: str,
    model_probability: float,
    market_probability: float,
    odds: float,
    average_odds: float | None,
) -> float:
    if mode == "market_value":
        if not average_odds or average_odds <= 1.0:
            return -1.0
        return (odds / average_odds) - 1.0
    if mode == "market_drift":
        fair_probability = {"HOME": 0.43, "DRAW": 0.27, "AWAY": 0.30}.get(selection, 0.33)
        return fair_probability - market_probability
    return model_probability - market_probability


def _update_state(state: dict[str, TeamState], row: HistoricalMatchRow) -> None:
    if row.home_goals is None or row.away_goals is None:
        return
    home = state.setdefault(row.home_team, TeamState())
    away = state.setdefault(row.away_team, TeamState())
    home_points, away_points = _points(row.home_goals, row.away_goals)
    home.matches += 1
    home.points += home_points
    home.goals_for += row.home_goals
    home.goals_against += row.away_goals
    home.home_matches += 1
    home.home_points += home_points
    away.matches += 1
    away.points += away_points
    away.goals_for += row.away_goals
    away.goals_against += row.home_goals
    away.away_matches += 1
    away.away_points += away_points


def _points(home_goals: int, away_goals: int) -> tuple[float, float]:
    if home_goals > away_goals:
        return 3.0, 0.0
    if away_goals > home_goals:
        return 0.0, 3.0
    return 1.0, 1.0


def _summarize_decisions(league: str, seasons: list[str], decisions: list[BetDecision]) -> dict:
    settled = [decision for decision in decisions if decision.row.home_goals is not None and decision.row.away_goals is not None]
    profit = 0.0
    wins = 0
    clv_values: list[float] = []
    for decision in settled:
        if decision.profit_units is not None:
            profit += decision.profit_units
            if decision.profit_units > 0:
                wins += 1
        else:
            won = _won(decision.selection, decision.row.home_goals or 0, decision.row.away_goals or 0)
            if won:
                wins += 1
                profit += decision.odds - 1.0
            else:
                profit -= 1.0
        if decision.closing_odds and decision.closing_odds > 1.0:
            clv_values.append((decision.odds / decision.closing_odds) - 1.0)
    roi = profit / len(settled) if settled else None
    average_odds = mean([decision.odds for decision in settled]) if settled else None
    hit_rate = wins / len(settled) if settled else None
    average_clv = mean(clv_values) if clv_values else None
    return {
        "league": league,
        "seasons": seasons,
        "bets": len(decisions),
        "settled_bets": len(settled),
        "profit_units": round(profit, 3),
        "roi": round(roi, 4) if roi is not None else None,
        "average_odds": round(average_odds, 4) if average_odds is not None else None,
        "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
        "average_clv": round(average_clv, 4) if average_clv is not None else None,
    }


def _won(selection: str, home_goals: int, away_goals: int) -> bool:
    if selection == "HOME":
        return home_goals > away_goals
    if selection == "AWAY":
        return away_goals > home_goals
    return home_goals == away_goals


def _settle_ah(home_goals: int | None, away_goals: int | None, home_line: float, odds: float, side: str) -> float:
    if home_goals is None or away_goals is None:
        return 0.0
    lines = [home_line]
    if abs(home_line * 4 - round(home_line * 4)) < 1e-9 and abs(home_line * 2 - round(home_line * 2)) > 1e-9:
        lines = [home_line - 0.25, home_line + 0.25]
    stake_part = 1.0 / len(lines)
    profit = 0.0
    for line in lines:
        adjusted = home_goals + line - away_goals if side == "home" else away_goals - home_goals - line
        if adjusted > 0:
            profit += stake_part * (odds - 1.0)
        elif adjusted < 0:
            profit -= stake_part
    return profit


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
