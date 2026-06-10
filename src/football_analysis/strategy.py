from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from itertools import product
from statistics import mean

from pydantic import BaseModel, Field

from football_analysis.contracts import HistoricalMatchRow
from football_analysis.db import StructuredRepository
from football_analysis.settings import StrategyProfileSettings


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
    season_phase: str = "all"


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


class StrategyPortfolioItem(BaseModel):
    name: str
    league: str
    strategy_type: str
    season_phases: list[str]
    seasons: list[str]
    settled_bets: int
    profit_units: float
    roi: float | None
    positive_folds: int
    fold_count: int
    average_clv: float | None
    worst_fold_roi: float | None
    stability_label: str
    fallback_folds: int
    selected_by: list[str]
    params: dict
    folds: list[StrategyResult]


class StrategyPortfolioReport(BaseModel):
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    seasons: list[str]
    leagues: list[str]
    season_phases: list[str]
    scan_phases: bool = False
    candidates: list[StrategyPortfolioItem]


class StrategyProfileAuditItem(BaseModel):
    profile_id: str
    status: str
    message: str
    configured: dict | None = None
    portfolio: dict | None = None


class StrategyProfileAuditReport(BaseModel):
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    seasons: list[str]
    passed: bool
    items: list[StrategyProfileAuditItem]


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
    season_phases: list[str] | None = None,
) -> StrategyResult:
    all_rows = _load_rows(repository, league, sorted(set(train_seasons + test_seasons)))
    phases = _normalize_season_phases(season_phases)
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
            season_phase=season_phase,
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
            season_phase,
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
            phases,
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
        fallback = StrategyParams(season_phase=phases[0])
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
    season_phases: list[str] | None = None,
) -> WalkForwardResult:
    folds: list[StrategyResult] = []
    for index in range(min_train_seasons, len(seasons)):
        train = seasons[:index]
        test = [seasons[index]]
        folds.append(
            optimize_strategy(
                repository,
                league,
                train,
                test,
                min_test_bets=min_test_bets,
                season_phases=season_phases,
            )
        )
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


def build_strategy_portfolio(
    repository: StructuredRepository,
    seasons: list[str],
    leagues: list[str] | None = None,
    season_phases: list[str] | None = None,
    scan_phases: bool = False,
) -> StrategyPortfolioReport:
    selected_leagues = _normalize_leagues(leagues)
    selected_phases = _normalize_season_phases(season_phases)
    if scan_phases:
        candidates = _scan_phase_candidates(repository, seasons, selected_leagues, selected_phases)
        candidates.sort(key=_portfolio_sort_key, reverse=True)
        return StrategyPortfolioReport(
            seasons=seasons,
            leagues=selected_leagues,
            season_phases=selected_phases,
            scan_phases=True,
            candidates=candidates,
        )

    e0_all = walk_forward_optimize(
        repository,
        league="E0",
        seasons=seasons,
        min_train_seasons=2,
        min_test_bets=30,
        season_phases=["all"],
    )
    i1_all = walk_forward_optimize(
        repository,
        league="I1",
        seasons=seasons,
        min_train_seasons=2,
        min_test_bets=30,
        season_phases=["all"],
    )
    i1_middle_overlay = _phase_overlay_walk_forward(repository, i1_all, seasons, "middle")
    i1_late_overlay = _phase_overlay_walk_forward(repository, i1_all, seasons, "late")
    candidates = [
        _portfolio_item("E0 robust all-season home value", "optimized_walk_forward", ["all"], seasons, e0_all),
        _portfolio_item("I1 high-yield AH away value", "optimized_walk_forward", ["all"], seasons, i1_all),
        _portfolio_item(
            "I1 middle-season AH away overlay",
            "phase_overlay_from_all",
            ["middle"],
            seasons,
            i1_middle_overlay,
        ),
        _portfolio_item(
            "I1 late-season AH away overlay",
            "phase_overlay_from_all",
            ["late"],
            seasons,
            i1_late_overlay,
        ),
    ]
    candidates.sort(key=_portfolio_sort_key, reverse=True)
    return StrategyPortfolioReport(
        seasons=seasons,
        leagues=["E0", "I1"],
        season_phases=["all", "middle", "late"],
        scan_phases=False,
        candidates=candidates,
    )


def audit_strategy_profiles(
    repository: StructuredRepository,
    configured_profiles: list[StrategyProfileSettings],
    seasons: list[str],
    roi_tolerance: float = 0.002,
    clv_tolerance: float = 0.002,
) -> StrategyProfileAuditReport:
    portfolio = build_strategy_portfolio(repository, seasons=seasons)
    portfolio_by_id = {_profile_id_for_candidate(candidate): candidate for candidate in portfolio.candidates}
    configured_by_id = {profile.id: profile for profile in configured_profiles if profile.active}
    items: list[StrategyProfileAuditItem] = []

    for profile_id, profile in configured_by_id.items():
        candidate = portfolio_by_id.get(profile_id)
        if candidate is None:
            items.append(
                StrategyProfileAuditItem(
                    profile_id=profile_id,
                    status="missing_from_portfolio",
                    message="configured profile is active but not present in the current portfolio",
                    configured=_profile_config_payload(profile),
                )
            )
            continue
        drift = _profile_drift(profile, candidate, roi_tolerance, clv_tolerance)
        items.append(
            StrategyProfileAuditItem(
                profile_id=profile_id,
                status="matched" if not drift else "stale",
                message="profile matches current portfolio" if not drift else "; ".join(drift),
                configured=_profile_config_payload(profile),
                portfolio=_profile_candidate_payload(candidate),
            )
        )

    for profile_id, candidate in portfolio_by_id.items():
        if candidate.stability_label == "reject_unstable" or profile_id in configured_by_id:
            continue
        items.append(
            StrategyProfileAuditItem(
                profile_id=profile_id,
                status="missing_from_config",
                message="current portfolio candidate is not configured as an active strategy profile",
                portfolio=_profile_candidate_payload(candidate),
            )
        )

    status_order = {"stale": 0, "missing_from_portfolio": 1, "missing_from_config": 2, "matched": 3}
    items.sort(key=lambda item: (status_order.get(item.status, 9), item.profile_id))
    passed = all(item.status == "matched" for item in items)
    return StrategyProfileAuditReport(seasons=seasons, passed=passed, items=items)


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
    season_totals: dict[str, int] = {}
    season_seen: dict[str, int] = {}
    for row in rows:
        season_totals[row.season] = season_totals.get(row.season, 0) + 1

    for row in rows:
        season_seen[row.season] = season_seen.get(row.season, 0) + 1
        home_state = state.get(row.home_team, TeamState())
        away_state = state.get(row.away_team, TeamState())
        if row.season not in decisions_by_season:
            decisions_by_season[row.season] = []
        phase = _season_phase(season_seen[row.season] - 1, season_totals[row.season])
        if _phase_allowed(params.season_phase, phase) and _eligible_state(home_state, away_state, params):
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


def _normalize_season_phases(season_phases: list[str] | None) -> list[str]:
    if not season_phases:
        return ["all"]
    normalized = [phase.strip().lower() for phase in season_phases if phase.strip()]
    if not normalized:
        return ["all"]
    invalid = sorted(set(normalized) - {"all", "early", "middle", "late"})
    if invalid:
        raise ValueError(f"unsupported_season_phase:{','.join(invalid)}")
    return normalized


def _normalize_leagues(leagues: list[str] | None) -> list[str]:
    if not leagues:
        return ["E0", "SP1", "D1", "I1", "F1"]
    normalized = [league.strip().upper() for league in leagues if league.strip()]
    return normalized or ["E0", "SP1", "D1", "I1", "F1"]


def _phase_allowed(requested_phase: str, current_phase: str) -> bool:
    normalized = requested_phase.strip().lower()
    if normalized not in {"all", "early", "middle", "late"}:
        raise ValueError(f"unsupported_season_phase:{requested_phase}")
    return normalized == "all" or normalized == current_phase


def _season_phase(index: int, total: int) -> str:
    # Phase filters only control bet eligibility; team state still accrues full season history.
    if total <= 0:
        return "early"
    ratio = index / total
    if ratio < 1 / 3:
        return "early"
    if ratio < 2 / 3:
        return "middle"
    return "late"


def _selection_score(result: StrategyResult) -> float:
    positive_seasons = sum(1 for item in result.season_breakdown if (item.get("roi") or 0) > 0)
    consistency = positive_seasons / len(result.season_breakdown) if result.season_breakdown else 0.0
    volume_penalty = 0.0 if result.bets >= 180 else (180 - result.bets) / 1000
    clv_bonus = min(result.average_clv or 0.0, 0.03)
    return (result.roi or -1.0) + consistency * 0.06 + clv_bonus - volume_penalty


def _scan_phase_candidates(
    repository: StructuredRepository,
    seasons: list[str],
    leagues: list[str],
    season_phases: list[str],
) -> list[StrategyPortfolioItem]:
    candidates: list[StrategyPortfolioItem] = []
    for league in leagues:
        base_result = walk_forward_optimize(
            repository,
            league=league,
            seasons=seasons,
            min_train_seasons=2,
            min_test_bets=30,
            season_phases=["all"],
        )
        for phase in season_phases:
            if phase == "all":
                result = base_result
                strategy_type = "phase_scan_base"
            else:
                result = _phase_overlay_walk_forward(repository, base_result, seasons, phase)
                strategy_type = "phase_scan_overlay"
            candidates.append(
                _portfolio_item(
                    f"{league} {phase}-season value scan",
                    strategy_type,
                    [phase],
                    seasons,
                    result,
                )
            )
    return candidates


def _phase_overlay_walk_forward(
    repository: StructuredRepository,
    source: WalkForwardResult,
    seasons: list[str],
    season_phase: str,
) -> WalkForwardResult:
    rows = _load_rows(repository, source.league, seasons)
    folds: list[StrategyResult] = []
    for source_fold in source.folds:
        params = _params_from_result(source_fold, season_phase)
        fold = run_strategy_on_rows(rows, source.league, source_fold.test_seasons, params)
        folds.append(
            fold.model_copy(
                update={
                    "train_seasons": source_fold.train_seasons,
                    "test_seasons": source_fold.test_seasons,
                    "params": {
                        **params.__dict__,
                        "source_strategy_phase": source_fold.params.get("season_phase", "all"),
                        "source_train_roi": source_fold.params.get("train_roi"),
                        "source_train_bets": source_fold.params.get("train_bets"),
                    },
                    "selected_by": "phase_overlay_from_all",
                }
            )
        )
    return _summarize_folds(source.league, folds)


def _params_from_result(result: StrategyResult, season_phase: str) -> StrategyParams:
    names = {field.name for field in fields(StrategyParams)}
    values = {name: result.params[name] for name in names if name in result.params}
    values["season_phase"] = season_phase
    return StrategyParams(**values)


def _summarize_folds(league: str, folds: list[StrategyResult]) -> WalkForwardResult:
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


def _portfolio_item(
    name: str,
    strategy_type: str,
    season_phases: list[str],
    seasons: list[str],
    result: WalkForwardResult,
) -> StrategyPortfolioItem:
    fold_rois = [fold.roi for fold in result.folds if fold.roi is not None]
    worst_fold_roi = min(fold_rois) if fold_rois else None
    selected_by = [fold.selected_by for fold in result.folds]
    return StrategyPortfolioItem(
        name=name,
        league=result.league,
        strategy_type=strategy_type,
        season_phases=season_phases,
        seasons=seasons,
        settled_bets=result.settled_bets,
        profit_units=result.profit_units,
        roi=result.roi,
        positive_folds=result.positive_folds,
        fold_count=result.fold_count,
        average_clv=result.average_clv,
        worst_fold_roi=round(worst_fold_roi, 4) if worst_fold_roi is not None else None,
        stability_label=_stability_label(result),
        fallback_folds=sum(1 for value in selected_by if value == "fallback_no_training_candidate"),
        selected_by=selected_by,
        params=result.folds[-1].params if result.folds else {},
        folds=result.folds,
    )


def _stability_label(result: WalkForwardResult) -> str:
    if any(fold.selected_by == "fallback_no_training_candidate" for fold in result.folds):
        return "reject_unstable"
    has_positive_clv = (result.average_clv or 0.0) > 0.0
    has_two_thirds_positive = result.positive_folds * 3 >= result.fold_count * 2 if result.fold_count else False
    if result.settled_bets >= 100 and result.positive_folds == result.fold_count and has_positive_clv:
        return "robust"
    if result.settled_bets >= 150 and (result.roi or 0.0) >= 0.10 and has_two_thirds_positive and has_positive_clv:
        return "high_yield"
    if result.settled_bets >= 60 and (result.roi or 0.0) >= 0.08 and has_two_thirds_positive and has_positive_clv:
        return "supplemental"
    return "reject_unstable"


def _portfolio_sort_key(item: StrategyPortfolioItem) -> tuple[int, float, float, int]:
    rank = {"robust": 4, "high_yield": 3, "supplemental": 2, "reject_unstable": 1}.get(item.stability_label, 0)
    return (rank, item.roi or -1.0, item.average_clv or -1.0, item.settled_bets)


def _profile_id_for_candidate(candidate: StrategyPortfolioItem) -> str:
    phase = candidate.season_phases[0] if candidate.season_phases else "all"
    if candidate.params.get("mode") == "asian_value":
        selection = _profile_selection_from_params(candidate.params)
        return f"{candidate.league.lower()}_{phase}_ah_{selection}_{candidate.stability_label}"
    selection = _profile_selection_from_params(candidate.params)
    return f"{candidate.league.lower()}_{phase}_{selection}_{candidate.stability_label}"


def _profile_selection_from_params(params: dict) -> str:
    selection_bias = str(params.get("selection_bias", "")).lower()
    if selection_bias == "ah_away":
        return "away"
    if selection_bias == "ah_home":
        return "home"
    return selection_bias or "all"


def _profile_config_payload(profile: StrategyProfileSettings) -> dict:
    return {
        "id": profile.id,
        "league_code": profile.league_code,
        "market_type": profile.market_type,
        "selections": profile.selections,
        "season_phases": profile.season_phases,
        "stability_label": profile.stability_label,
        "roi": profile.roi,
        "settled_bets": profile.settled_bets,
        "positive_folds": profile.positive_folds,
        "fold_count": profile.fold_count,
        "average_clv": profile.average_clv,
    }


def _profile_candidate_payload(candidate: StrategyPortfolioItem) -> dict:
    return {
        "id": _profile_id_for_candidate(candidate),
        "league_code": candidate.league,
        "season_phases": candidate.season_phases,
        "stability_label": candidate.stability_label,
        "roi": candidate.roi,
        "settled_bets": candidate.settled_bets,
        "positive_folds": candidate.positive_folds,
        "fold_count": candidate.fold_count,
        "average_clv": candidate.average_clv,
    }


def _profile_drift(
    profile: StrategyProfileSettings,
    candidate: StrategyPortfolioItem,
    roi_tolerance: float,
    clv_tolerance: float,
) -> list[str]:
    drift: list[str] = []
    if profile.league_code.upper() != candidate.league:
        drift.append(f"league_code changed:{profile.league_code}->{candidate.league}")
    if profile.season_phases != candidate.season_phases:
        drift.append(f"season_phases changed:{profile.season_phases}->{candidate.season_phases}")
    if profile.stability_label != candidate.stability_label:
        drift.append(f"stability_label changed:{profile.stability_label}->{candidate.stability_label}")
    if profile.settled_bets != candidate.settled_bets:
        drift.append(f"settled_bets changed:{profile.settled_bets}->{candidate.settled_bets}")
    if profile.positive_folds != candidate.positive_folds:
        drift.append(f"positive_folds changed:{profile.positive_folds}->{candidate.positive_folds}")
    if profile.fold_count != candidate.fold_count:
        drift.append(f"fold_count changed:{profile.fold_count}->{candidate.fold_count}")
    if _float_drifted(profile.roi, candidate.roi, roi_tolerance):
        drift.append(f"roi changed:{profile.roi}->{candidate.roi}")
    if _float_drifted(profile.average_clv, candidate.average_clv, clv_tolerance):
        drift.append(f"average_clv changed:{profile.average_clv}->{candidate.average_clv}")
    return drift


def _float_drifted(left: float | None, right: float | None, tolerance: float) -> bool:
    if left is None or right is None:
        return left != right
    return abs(left - right) > tolerance


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
