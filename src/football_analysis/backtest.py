from __future__ import annotations

from football_analysis.contracts import HistoricalMatchRow
from football_analysis.db import StructuredRepository
from football_analysis.models import BacktestSummary


def run_historical_backtest(
    repository: StructuredRepository,
    league: str,
    season: str,
    min_clv_edge: float = 0.025,
) -> BacktestSummary:
    rows = [
        row
        for row in repository.list_models("historical_matches", HistoricalMatchRow)
        if row.league == league and row.season == season
    ]
    bets = 0
    settled = 0
    total_stake = 0.0
    profit = 0.0
    clv_values: list[float] = []

    for row in rows:
        candidate = _best_candidate(row)
        if candidate is None:
            continue
        selection, odds, closing_odds = candidate
        clv = (odds / closing_odds) - 1.0
        if clv < min_clv_edge:
            continue
        bets += 1
        if row.home_goals is None or row.away_goals is None:
            continue
        settled += 1
        total_stake += 1.0
        clv_values.append(clv)
        won = _won(selection, row.home_goals, row.away_goals)
        profit += odds - 1.0 if won else -1.0

    roi = profit / total_stake if total_stake else None
    average_clv = sum(clv_values) / len(clv_values) if clv_values else None
    return BacktestSummary(
        league=league,
        season=season,
        matches=len(rows),
        bets=bets,
        settled_bets=settled,
        total_stake_units=round(total_stake, 3),
        profit_units=round(profit, 3),
        roi=round(roi, 4) if roi is not None else None,
        average_clv=round(average_clv, 4) if average_clv is not None else None,
    )


def _best_candidate(row: HistoricalMatchRow) -> tuple[str, float, float] | None:
    candidates = [
        ("HOME", row.home_odds, row.closing_home_odds),
        ("DRAW", row.draw_odds, row.closing_draw_odds),
        ("AWAY", row.away_odds, row.closing_away_odds),
    ]
    valid = [(selection, odds, closing) for selection, odds, closing in candidates if odds and closing and odds > 1.0 and closing > 1.0]
    if not valid:
        return None
    return max(valid, key=lambda item: item[1] / item[2])


def _won(selection: str, home_goals: int, away_goals: int) -> bool:
    if selection == "HOME":
        return home_goals > away_goals
    if selection == "AWAY":
        return away_goals > home_goals
    return home_goals == away_goals
