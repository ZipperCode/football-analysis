from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from football_analysis.ai_analysis import build_ai_signal, selection_label
from football_analysis.models import AgentFinding, BetLog, Match, MarketType, OddsSnapshot
from football_analysis.service import (
    AnalysisService,
    _infer_asian_handicap_result,
    _infer_over_under_result,
    _winning_1x2_selection,
)

_MARKET_ENUM = {
    "1x2": MarketType.one_x_two,
    "asian_handicap": MarketType.asian_handicap,
    "over_under": MarketType.over_under,
}


@dataclass(frozen=True)
class AIEvalRow:
    match_id: str
    home_team: str
    away_team: str
    final_score: str
    market_type: str
    selection: str
    selection_label: str
    ai_probability: float
    market_probability: float
    ai_confidence: float
    outcome: float
    ai_brier: float
    market_brier: float
    ai_analysis: str


@dataclass
class AIEvalReport:
    date: date
    league: str | None
    scanned_matches: int
    finished_matches: int
    signal_count: int
    ai_brier: float | None
    market_brier: float | None
    brier_improvement: float | None
    ai_hit_rate: float | None
    market_hit_rate: float | None
    rows: list[AIEvalRow] = field(default_factory=list)


def evaluate_ai_quality(
    service: AnalysisService,
    *,
    target_date: date,
    league: str | None = None,
) -> AIEvalReport:
    from football_analysis.evaluation import _has_final_score, _is_pre_kickoff, _league_matches

    tzinfo = service.settings.app.tzinfo
    matches = [
        match
        for match in service.repository.list_models("matches", Match)
        if match.kickoff_at.astimezone(tzinfo).date() == target_date
        and _league_matches(match, league, service.settings.leagues)
    ]
    finished = [match for match in matches if _has_final_score(match)]

    all_odds = service.repository.list_models("odds", OddsSnapshot)
    all_findings = service.repository.list_models("findings", AgentFinding)

    rows: list[AIEvalRow] = []
    for match in finished:
        match_odds = [
            snapshot
            for snapshot in all_odds
            if snapshot.match_id == match.id and _is_pre_kickoff(snapshot.collected_at, match.kickoff_at)
        ]
        match_findings = [finding for finding in all_findings if finding.match_id == match.id]
        signal = build_ai_signal(match, match_odds, match_findings, service.settings)
        if signal is None:
            continue
        row = _row_for_signal(match, signal)
        if row is not None:
            rows.append(row)

    return _summarize(target_date, league, len(matches), len(finished), rows)


def _row_for_signal(match: Match, signal: Any) -> AIEvalRow | None:
    ai_prob = signal.probabilities.get(signal.selection)
    if ai_prob is None:
        return None
    outcome = _selection_outcome(match, signal.market_type, signal.selection)
    if outcome is None:
        return None
    best_price = float(signal.raw.get("best_price") or 0.0)
    market_prob = 1.0 / best_price if best_price > 1.0 else 0.0
    return AIEvalRow(
        match_id=match.id,
        home_team=match.home_team,
        away_team=match.away_team,
        final_score=f"{match.home_score}-{match.away_score}",
        market_type=signal.market_type,
        selection=signal.selection,
        selection_label=selection_label(signal.selection),
        ai_probability=round(ai_prob, 4),
        market_probability=round(market_prob, 4),
        ai_confidence=round(signal.confidence, 4),
        outcome=outcome,
        ai_brier=round((ai_prob - outcome) ** 2, 4),
        market_brier=round((market_prob - outcome) ** 2, 4),
        ai_analysis=signal.analysis,
    )


def _selection_outcome(match: Match, market_type: str, selection: str) -> float | None:
    market_enum = _MARKET_ENUM.get(market_type)
    if market_enum is None:
        return None
    if market_type == "1x2":
        return 1.0 if _winning_1x2_selection(match) == selection else 0.0
    bet = BetLog(
        id=f"ai-eval:{match.id}:{selection}",
        match_id=match.id,
        market_type=market_enum,
        selection=selection,
        odds=2.0,
        stake_units=1.0,
        platform="simulation",
    )
    if market_type == "asian_handicap":
        result = _infer_asian_handicap_result(bet, match)
    else:
        result = _infer_over_under_result(bet, match)
    return _result_to_outcome(result)


def _result_to_outcome(result: str) -> float | None:
    mapping = {"win": 1.0, "half_win": 0.75, "void": 0.5, "half_loss": 0.25, "loss": 0.0}
    return mapping.get(result)


def _summarize(
    target_date: date,
    league: str | None,
    scanned: int,
    finished: int,
    rows: list[AIEvalRow],
) -> AIEvalReport:
    count = len(rows)
    if count == 0:
        return AIEvalReport(
            date=target_date,
            league=league,
            scanned_matches=scanned,
            finished_matches=finished,
            signal_count=0,
            ai_brier=None,
            market_brier=None,
            brier_improvement=None,
            ai_hit_rate=None,
            market_hit_rate=None,
            rows=[],
        )
    ai_brier = round(sum(row.ai_brier for row in rows) / count, 4)
    market_brier = round(sum(row.market_brier for row in rows) / count, 4)
    ai_hits = sum(1 for row in rows if (row.ai_probability >= 0.5) == (row.outcome >= 0.5))
    market_hits = sum(1 for row in rows if (row.market_probability >= 0.5) == (row.outcome >= 0.5))
    return AIEvalReport(
        date=target_date,
        league=league,
        scanned_matches=scanned,
        finished_matches=finished,
        signal_count=count,
        ai_brier=ai_brier,
        market_brier=market_brier,
        brier_improvement=round(market_brier - ai_brier, 4),
        ai_hit_rate=round(ai_hits / count, 4),
        market_hit_rate=round(market_hits / count, 4),
        rows=rows,
    )
