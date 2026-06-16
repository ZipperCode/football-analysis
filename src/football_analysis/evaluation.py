from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from football_analysis.live_gate import apply_live_gate
from football_analysis.models import (
    AgentFinding,
    BetLog,
    MarketType,
    Match,
    MatchAnalysis,
    MatchStatus,
    OddsSnapshot,
    Recommendation,
    RecommendationStatus,
)
from football_analysis.scoring import score_match
from football_analysis.service import (
    AnalysisService,
    _infer_asian_handicap_result,
    _infer_over_under_result,
    _profit_for_result,
    _winning_1x2_selection,
)


class FinishedMatchEvaluation(BaseModel):
    match_id: str
    league: str
    home_team: str
    away_team: str
    kickoff_at: datetime
    final_score: str | None
    recommendation_status: str
    market_type: str | None = None
    selection: str | None = None
    normalized_selection: str | None = None
    stake_units: float = 0.0
    odds: float | None = None
    result: str | None = None
    profit_units: float | None = None
    evaluable: bool = False
    excluded_reason: str | None = None
    odds_collected_at: datetime | None = None
    odds_age_minutes: int | None = None
    gates_failed: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    value_score: float = 0.0
    risk_score: float = 0.0
    confidence: float = 0.0


class FinishedMatchEvaluationReport(BaseModel):
    league: str | None
    date: date
    timezone: str
    included_statuses: list[str]
    scanned_matches: int
    finished_matches: int
    analyses: int
    recommended_count: int
    sample_count: int
    wins: int
    losses: int
    voids: int
    half_wins: int
    half_losses: int
    hit_rate: float | None
    staked_units: float
    profit_units: float
    roi_units: float | None
    excluded_count: int
    excluded_by_reason: dict[str, int]
    results: list[FinishedMatchEvaluation]


def evaluate_finished_matches(
    service: AnalysisService,
    *,
    target_date: date,
    league: str | None = None,
    included_statuses: set[RecommendationStatus] | None = None,
    result_overrides: list[str] | None = None,
    save_results: bool = False,
) -> FinishedMatchEvaluationReport:
    included_statuses = included_statuses or {RecommendationStatus.recommended}
    tzinfo = service.settings.app.tzinfo
    all_matches = service.repository.list_models("matches", Match)
    matches = [
        match
        for match in all_matches
        if match.kickoff_at.astimezone(tzinfo).date() == target_date
        and _league_matches(match, league, service.settings.leagues)
    ]
    override_scores = _parse_result_overrides(result_overrides or [])
    matches = [_apply_score_override(match, override_scores, save_results=save_results, service=service) for match in matches]
    finished = [match for match in matches if _has_final_score(match)]
    analyses = _score_finished_matches(service, finished)
    results = [
        _evaluate_analysis(analysis, included_statuses=included_statuses)
        for analysis in analyses
    ]
    counters = Counter(item.result for item in results if item.evaluable)
    excluded = Counter(item.excluded_reason or "unknown" for item in results if not item.evaluable)
    sample_count = sum(counters.values())
    staked_units = round(sum(item.stake_units for item in results if item.evaluable), 4)
    profit_units = round(sum(item.profit_units or 0.0 for item in results if item.evaluable), 4)
    wins = counters["win"]
    half_wins = counters["half_win"]
    losses = counters["loss"]
    half_losses = counters["half_loss"]
    hit_numerator = wins + (half_wins * 0.5)
    hit_denominator = wins + losses + half_wins + half_losses
    return FinishedMatchEvaluationReport(
        league=league,
        date=target_date,
        timezone=str(service.settings.app.timezone),
        included_statuses=sorted(status.value for status in included_statuses),
        scanned_matches=len(matches),
        finished_matches=len(finished),
        analyses=len(analyses),
        recommended_count=sum(
            1 for analysis in analyses if analysis.recommendation.status is RecommendationStatus.recommended
        ),
        sample_count=sample_count,
        wins=wins,
        losses=losses,
        voids=counters["void"],
        half_wins=half_wins,
        half_losses=half_losses,
        hit_rate=round(hit_numerator / hit_denominator, 4) if hit_denominator else None,
        staked_units=staked_units,
        profit_units=profit_units,
        roi_units=round(profit_units / staked_units, 4) if staked_units else None,
        excluded_count=sum(excluded.values()),
        excluded_by_reason=dict(sorted(excluded.items())),
        results=results,
    )


def _score_finished_matches(service: AnalysisService, matches: list[Match]) -> list[MatchAnalysis]:
    odds = service.repository.list_models("odds", OddsSnapshot)
    findings = service.repository.list_models("findings", AgentFinding)
    bet_logs = service.repository.list_models("bets", BetLog)
    profile_review_actions = service._profile_review_actions()
    analyses: list[MatchAnalysis] = []
    for match in matches:
        match_odds = [
            snapshot
            for snapshot in odds
            if snapshot.match_id == match.id and _is_pre_kickoff(snapshot.collected_at, match.kickoff_at)
        ]
        match_findings = [finding for finding in findings if finding.match_id == match.id]
        recommendation = score_match(match, match_odds, match_findings, service.settings)
        recommendation = apply_live_gate(
            recommendation,
            match=match,
            odds_snapshots=match_odds,
            bet_logs=bet_logs,
            settings=service.settings,
            profile_review_actions=profile_review_actions,
        )
        analyses.append(
            MatchAnalysis(
                match=match,
                odds_snapshots=match_odds,
                findings=match_findings,
                recommendation=recommendation,
            )
        )
    return service._allocate_analysis_recommendations(analyses)


def _evaluate_analysis(
    analysis: MatchAnalysis,
    *,
    included_statuses: set[RecommendationStatus],
) -> FinishedMatchEvaluation:
    recommendation = analysis.recommendation
    final_score = _final_score(analysis.match)
    base = {
        "match_id": analysis.match.id,
        "league": analysis.match.league,
        "home_team": analysis.match.home_team,
        "away_team": analysis.match.away_team,
        "kickoff_at": analysis.match.kickoff_at,
        "final_score": final_score,
        "recommendation_status": recommendation.status.value,
        "market_type": recommendation.market_type.value if recommendation.market_type else None,
        "selection": recommendation.selection,
        "gates_failed": list(recommendation.score_breakdown.get("gates_failed") or []),
        "risk_tags": recommendation.risk_tags,
        "value_score": recommendation.value_score,
        "risk_score": recommendation.risk_score,
        "confidence": recommendation.confidence,
    }
    excluded_reason = _excluded_reason(recommendation, included_statuses)
    if excluded_reason is not None:
        return FinishedMatchEvaluation(**base, excluded_reason=excluded_reason)
    try:
        normalized_selection = _normalized_selection(recommendation, analysis.odds_snapshots)
        odds_snapshot, odds = _evaluation_odds(recommendation, normalized_selection, analysis.odds_snapshots)
        if odds is None:
            return FinishedMatchEvaluation(**base, normalized_selection=normalized_selection, excluded_reason="missing_odds")
        if odds_snapshot is None:
            odds_collected_at = None
            odds_age_minutes = None
        else:
            odds_collected_at = odds_snapshot.collected_at
            odds_age_minutes = int(
                (analysis.match.kickoff_at - _aware_datetime(odds_snapshot.collected_at)).total_seconds() / 60
            )
        stake_units = recommendation.stake_units or 1.0
        result = _settle_recommendation(recommendation, normalized_selection, analysis.match, odds, stake_units)
        profit_units = _profit_for_result(result, odds, stake_units)
        return FinishedMatchEvaluation(
            **base,
            normalized_selection=normalized_selection,
            odds=odds,
            stake_units=stake_units,
            result=result,
            profit_units=round(profit_units, 4),
            evaluable=True,
            odds_collected_at=odds_collected_at,
            odds_age_minutes=odds_age_minutes,
        )
    except ValueError as exc:
        return FinishedMatchEvaluation(**base, excluded_reason=str(exc))


def _excluded_reason(
    recommendation: Recommendation,
    included_statuses: set[RecommendationStatus],
) -> str | None:
    if recommendation.status not in included_statuses:
        return f"status:{recommendation.status.value}"
    if recommendation.market_type is None or recommendation.selection is None:
        return "missing_market_or_selection"
    return None


def _normalized_selection(recommendation: Recommendation, odds_snapshots: list[OddsSnapshot]) -> str:
    selection = (recommendation.selection or "").strip().upper()
    if recommendation.market_type == MarketType.over_under and selection in {"OVER", "UNDER"}:
        line = _matching_line(recommendation, odds_snapshots, selection)
        if line is None:
            raise ValueError("missing_line")
        return f"{selection} {line}"
    return selection


def _matching_line(
    recommendation: Recommendation,
    odds_snapshots: list[OddsSnapshot],
    selection: str,
) -> str | None:
    for snapshot in odds_snapshots:
        if snapshot.market_type != recommendation.market_type:
            continue
        if selection in snapshot.outcome_odds or selection in snapshot.best_price:
            return snapshot.line
    line = recommendation.odds_basis.get("line")
    return str(line) if line is not None else None


def _evaluation_odds(
    recommendation: Recommendation,
    normalized_selection: str,
    odds_snapshots: list[OddsSnapshot],
) -> tuple[OddsSnapshot | None, float | None]:
    selection_key = normalized_selection.split()[0] if recommendation.market_type == MarketType.over_under else normalized_selection
    for snapshot in odds_snapshots:
        if snapshot.market_type != recommendation.market_type:
            continue
        if recommendation.odds_basis.get("source") and snapshot.source != recommendation.odds_basis.get("source"):
            continue
        if recommendation.odds_basis.get("bookmaker") and snapshot.bookmaker != recommendation.odds_basis.get("bookmaker"):
            continue
        price = (snapshot.best_price or snapshot.outcome_odds).get(selection_key)
        if price:
            return snapshot, price
    odds = recommendation.odds_basis.get("best_price")
    return None, float(odds) if odds is not None else None


def _settle_recommendation(
    recommendation: Recommendation,
    normalized_selection: str,
    match: Match,
    odds: float,
    stake_units: float,
) -> str:
    if recommendation.market_type == MarketType.one_x_two:
        winning_selection = _winning_1x2_selection(match)
        return "win" if normalized_selection == winning_selection else "loss"
    bet = BetLog(
        id=f"evaluation:{recommendation.id}",
        match_id=match.id,
        market_type=recommendation.market_type,
        selection=normalized_selection,
        odds=odds,
        stake_units=stake_units,
        platform="evaluation",
    )
    if recommendation.market_type == MarketType.asian_handicap:
        return _infer_asian_handicap_result(bet, match)
    if recommendation.market_type == MarketType.over_under:
        return _infer_over_under_result(bet, match)
    raise ValueError(f"unsupported_market:{recommendation.market_type}")


def _parse_result_overrides(items: list[str]) -> dict[str, tuple[int, int]]:
    parsed: dict[str, tuple[int, int]] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"invalid_result_override:{item}")
        raw_key, raw_score = item.split("=", 1)
        if "-" not in raw_score:
            raise ValueError(f"invalid_result_score:{item}")
        raw_home, raw_away = raw_score.split("-", 1)
        parsed[_normalize_key(raw_key)] = (int(raw_home.strip()), int(raw_away.strip()))
    return parsed


def _apply_score_override(
    match: Match,
    override_scores: dict[str, tuple[int, int]],
    *,
    save_results: bool,
    service: AnalysisService,
) -> Match:
    key_candidates = {
        _normalize_key(match.id),
        _normalize_key(f"{match.home_team} vs {match.away_team}"),
        _normalize_key(f"{match.home_team}-{match.away_team}"),
    }
    score = next((override_scores[key] for key in key_candidates if key in override_scores), None)
    if score is None:
        return match
    updated = match.model_copy(
        update={
            "status": MatchStatus.finished,
            "home_score": score[0],
            "away_score": score[1],
        }
    )
    if save_results:
        service.repository.upsert_model("matches", updated.id, updated)
    return updated


def _has_final_score(match: Match) -> bool:
    return match.home_score is not None and match.away_score is not None


def _final_score(match: Match) -> str | None:
    if not _has_final_score(match):
        return None
    return f"{match.home_score}-{match.away_score}"


def _normalize_key(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").replace(":", " ").split())


def _league_matches(match: Match, league: str | None, league_settings: list[Any]) -> bool:
    if league is None:
        return True
    wanted = _normalize_key(league)
    match_league = _normalize_key(match.league)
    for item in league_settings:
        keys = {_normalize_key(item.code), _normalize_key(item.name), *{_normalize_key(alias) for alias in item.aliases}}
        if wanted in keys:
            return match_league in keys
    return match_league == wanted


def _is_pre_kickoff(collected_at: datetime, kickoff_at: datetime) -> bool:
    return _aware_datetime(collected_at) <= _aware_datetime(kickoff_at)


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
