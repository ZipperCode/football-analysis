from __future__ import annotations

from football_analysis.contracts import HistoricalMatchRow
from football_analysis.db import StructuredRepository
from football_analysis.models import BacktestSummary


CALIBRATION_BUCKETS = [(0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 1.01)]


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
    wins = 0
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    brier_values: list[float] = []
    calibration_rows: list[tuple[float, bool]] = []
    segments: dict[str, dict[str, float]] = {}

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
        wins += 1 if won else 0
        bet_profit = odds - 1.0 if won else -1.0
        profit += bet_profit
        equity += bet_profit
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        implied_probability = 1.0 / odds
        brier_values.append((implied_probability - (1.0 if won else 0.0)) ** 2)
        calibration_rows.append((implied_probability, won))
        _add_segment(segments, f"odds:{_odds_bucket(odds)}", bet_profit, won, clv)

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
        hit_rate=round(wins / settled, 4) if settled else None,
        positive_clv_rate=round(sum(1 for value in clv_values if value > 0) / len(clv_values), 4) if clv_values else None,
        max_drawdown_units=round(max_drawdown, 3) if settled else None,
        brier_score=round(sum(brier_values) / len(brier_values), 4) if brier_values else None,
        calibration_buckets=_calibration_buckets(calibration_rows),
        segment_breakdown=_segment_breakdown(segments),
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


def _odds_bucket(odds: float) -> str:
    if odds < 1.5:
        return "<1.50"
    if odds < 2.0:
        return "1.50-1.99"
    if odds < 3.0:
        return "2.00-2.99"
    if odds < 5.0:
        return "3.00-4.99"
    return "5.00+"


def _add_segment(
    segments: dict[str, dict[str, float]],
    name: str,
    profit: float,
    won: bool,
    clv: float,
) -> None:
    segment = segments.setdefault(
        name,
        {"bets": 0.0, "profit": 0.0, "wins": 0.0, "positive_clv": 0.0},
    )
    segment["bets"] += 1.0
    segment["profit"] += profit
    segment["wins"] += 1.0 if won else 0.0
    segment["positive_clv"] += 1.0 if clv > 0 else 0.0


def _segment_breakdown(segments: dict[str, dict[str, float]]) -> list[dict]:
    rows = []
    for name, values in sorted(segments.items()):
        bets = values["bets"]
        rows.append(
            {
                "segment": name,
                "bets": int(bets),
                "profit_units": round(values["profit"], 3),
                "roi": round(values["profit"] / bets, 4) if bets else None,
                "hit_rate": round(values["wins"] / bets, 4) if bets else None,
                "positive_clv_rate": round(values["positive_clv"] / bets, 4) if bets else None,
            }
        )
    return rows


def _calibration_buckets(rows: list[tuple[float, bool]]) -> list[dict]:
    buckets: list[dict] = []
    for low, high in CALIBRATION_BUCKETS:
        items = [(probability, won) for probability, won in rows if low <= probability < high]
        if not items:
            continue
        predicted = sum(probability for probability, _ in items) / len(items)
        observed = sum(1.0 for _, won in items if won) / len(items)
        buckets.append(
            {
                "probability_range": f"{low:.2f}-{min(high, 1.0):.2f}",
                "count": len(items),
                "predicted_rate": round(predicted, 4),
                "observed_rate": round(observed, 4),
                "calibration_error": round(observed - predicted, 4),
            }
        )
    return buckets
