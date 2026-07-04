from __future__ import annotations

import re
from datetime import datetime, timedelta
from uuid import uuid4

from football_analysis.models import (
    AgentFinding,
    BetLog,
    BetSettlementReport,
    Match,
    MatchAnalysis,
    OddsSnapshot,
    PaperBankrollReport,
    PerformanceByLeagueReport,
    PerformanceGroupSummary,
    PerformanceSummary,
    PickList,
    Recommendation,
    RecommendationStatus,
    SourceHealth,
    StrategySnapshot,
)
from football_analysis.db import StructuredRepository
from football_analysis.ingestion import IngestionService
from football_analysis.live_gate import allocate_live_stakes, apply_live_gate
from football_analysis.portfolio import apply_portfolio_constraints
from football_analysis.ai_analysis import build_ai_signal
from football_analysis.scoring import _normalized_strategy_selection, score_match
from football_analysis.seed_data import build_seed_dataset
from football_analysis.settings import Settings, load_settings
from football_analysis.sources import SourceHealthChecker


class AnalysisService:
    def __init__(self, settings: Settings, repository: StructuredRepository):
        self.settings = settings
        self.repository = repository
        self.health_checker = SourceHealthChecker(settings, repository)
        self.ingestion = IngestionService(settings, repository)
        self._profile_review_actions_cache: dict[str, str] | None = None

    @classmethod
    def from_config(cls) -> "AnalysisService":
        settings = load_settings()
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        service = cls(settings, repository)
        service.ensure_seed_data()
        return service

    def ensure_seed_data(self) -> None:
        if not self.settings.app.fixture_mode:
            return
        existing = self.repository.list_models("matches", Match)
        today = datetime.now(self.settings.app.tzinfo).date()
        has_today = any(match.kickoff_at.astimezone(self.settings.app.tzinfo).date() == today for match in existing)
        has_real_data = any(not match.id.startswith("SAMPLE-") for match in existing)
        if has_today or has_real_data:
            return
        matches, odds, findings = build_seed_dataset(self.settings.app.timezone)
        for match in matches:
            self.repository.upsert_model("matches", match.id, match)
        for snapshot in odds:
            self.repository.upsert_model("odds", snapshot.id, snapshot)
        for finding in findings:
            self.repository.upsert_model("findings", finding.id, finding)

    def picks_today(self) -> PickList:
        today = datetime.now(self.settings.app.tzinfo).date()
        analyses = [
            self._score_analysis(match.id)
            for match in self.repository.list_models("matches", Match)
            if match.kickoff_at.astimezone(self.settings.app.tzinfo).date() == today
        ]
        analyses = self._allocate_analysis_recommendations(analyses)
        pick_statuses = {RecommendationStatus.recommended, RecommendationStatus.advisory_recommended}
        recommended = [
            analysis.recommendation
            for analysis in analyses
            if analysis.recommendation.status in pick_statuses
        ]
        recommended = self._dedupe_recommendations_by_event(recommended, analyses)
        recommended.sort(key=lambda item: (item.value_score, item.confidence), reverse=True)
        picks = recommended[: self.settings.app.daily_pick_limit]
        message = f"今日分析建议 {len(picks)} 场。" if picks else "今日无满足阈值的分析建议，建议只复盘观察。"
        return PickList(picks=picks, analyses=analyses, message=message)

    def picks_upcoming(self, hours: int = 24) -> PickList:
        now = datetime.now(self.settings.app.tzinfo)
        window_end = now + timedelta(hours=max(1, hours))
        analyses = [
            self._score_analysis(match.id)
            for match in self.repository.list_models("matches", Match)
            if now <= match.kickoff_at.astimezone(self.settings.app.tzinfo) <= window_end
        ]
        analyses = self._allocate_analysis_recommendations(analyses)
        pick_statuses = {RecommendationStatus.recommended, RecommendationStatus.advisory_recommended}
        recommended = [
            analysis.recommendation
            for analysis in analyses
            if analysis.recommendation.status in pick_statuses
        ]
        recommended = self._dedupe_recommendations_by_event(recommended, analyses)
        recommended.sort(key=lambda item: (item.value_score, item.confidence), reverse=True)
        picks = recommended[: self.settings.app.daily_pick_limit]
        message = f"未来 {hours} 小时分析建议 {len(picks)} 场。" if picks else f"未来 {hours} 小时无满足阈值的分析建议，建议只复盘观察。"
        return PickList(picks=picks, analyses=analyses, message=message)

    def _dedupe_recommendations_by_event(
        self,
        recommendations: list[Recommendation],
        analyses: list[MatchAnalysis],
    ) -> list[Recommendation]:
        analysis_by_match_id = {analysis.match.id: analysis for analysis in analyses}
        best_by_event: dict[tuple[str, str, str], Recommendation] = {}
        for recommendation in recommendations:
            analysis = analysis_by_match_id.get(recommendation.match_id)
            if analysis is None:
                continue
            key = self._event_key(analysis.match)
            current = best_by_event.get(key)
            if current is None or self._recommendation_source_rank(recommendation) > self._recommendation_source_rank(current):
                best_by_event[key] = recommendation
        return list(best_by_event.values())

    def _event_key(self, match: Match) -> tuple[str, str, str]:
        kickoff_key = match.kickoff_at.astimezone(self.settings.app.tzinfo).strftime("%Y-%m-%dT%H:%M")
        return (kickoff_key, self._team_key(match.home_team), self._team_key(match.away_team))

    def _team_key(self, value: str) -> str:
        normalized = value.casefold().replace("turkiye", "turkey")
        return re.sub(r"[^a-z0-9]+", "", normalized)

    def _recommendation_source_rank(self, recommendation: Recommendation) -> tuple[int, int, int, float, float]:
        source = str(recommendation.odds_basis.get("source") or "").lower()
        source_rank = {"the_odds_api": 30, "odds_api_io": 20}.get(source, 10)
        live_gate = recommendation.score_breakdown.get("live_gate") or {}
        research = recommendation.score_breakdown.get("research_advisory") or {}
        return (
            source_rank,
            int(live_gate.get("bookmaker_count") or 0),
            int(research.get("evidence_count") or 0),
            recommendation.value_score,
            recommendation.confidence,
        )

    def analyze_match(self, match_id: str) -> MatchAnalysis:
        analysis = self._score_analysis(match_id)
        return self._allocate_analysis_recommendations([analysis], include_existing=True)[0]

    def _score_analysis(self, match_id: str) -> MatchAnalysis:
        match = self.repository.get_model("matches", match_id, Match)
        if match is None:
            raise KeyError(f"Match not found: {match_id}")
        odds = [
            snapshot
            for snapshot in self.repository.list_models("odds", OddsSnapshot)
            if snapshot.match_id == match_id
        ]
        findings = [
            finding
            for finding in self.repository.list_models("findings", AgentFinding)
            if finding.match_id == match_id
        ]
        ai_signal = build_ai_signal(match, odds, findings, self.settings)
        recommendation = score_match(match, odds, findings, self.settings, ai_signal=ai_signal)
        stored = self.repository.get_model("recommendations", recommendation.id, Recommendation)
        if stored is not None and _is_world_cup_final_recommendation(stored, match, self.settings):
            recommendation = stored
        recommendation = apply_live_gate(
            recommendation,
            match=match,
            odds_snapshots=odds,
            bet_logs=self.repository.list_models("bets", BetLog),
            settings=self.settings,
            profile_review_actions=self._profile_review_actions(),
        )
        return MatchAnalysis(match=match, odds_snapshots=odds, findings=findings, recommendation=recommendation)

    def _profile_review_actions(self) -> dict[str, str]:
        if self._profile_review_actions_cache is None:
            from football_analysis.live_review import run_live_review

            report = run_live_review(self.repository, self.settings)
            self._profile_review_actions_cache = {
                profile.profile_id: profile.action
                for profile in report.profiles
                if profile.action in {"pause_live", "demote_to_paper"}
            }
        return self._profile_review_actions_cache

    def _invalidate_profile_review_actions(self) -> None:
        self._profile_review_actions_cache = None

    def _allocate_analysis_recommendations(
        self,
        analyses: list[MatchAnalysis],
        include_existing: bool = False,
    ) -> list[MatchAnalysis]:
        if not analyses:
            return []
        matches_by_id = {analysis.match.id: analysis.match for analysis in analyses}
        recommendations = [analysis.recommendation for analysis in analyses]
        if include_existing:
            current_ids = {recommendation.id for recommendation in recommendations}
            target_dates = {
                analysis.match.kickoff_at.astimezone(self.settings.app.tzinfo).date()
                for analysis in analyses
            }
            stored_matches = {match.id: match for match in self.repository.list_models("matches", Match)}
            for recommendation in self.repository.list_models("recommendations", Recommendation):
                if recommendation.id in current_ids:
                    continue
                match = stored_matches.get(recommendation.match_id)
                if match is None:
                    continue
                if match.kickoff_at.astimezone(self.settings.app.tzinfo).date() not in target_dates:
                    continue
                matches_by_id[match.id] = match
                recommendations.append(recommendation)
        allocated = allocate_live_stakes(recommendations, matches_by_id, self.settings)
        allocated = apply_portfolio_constraints(
            allocated,
            matches_by_id,
            self.settings.portfolio,
            bankroll_units=self.settings.bankroll.initial_units,
        )
        allocated_by_id = {recommendation.id: recommendation for recommendation in allocated}
        for recommendation in allocated:
            self.repository.upsert_model("recommendations", recommendation.id, recommendation)
            match = matches_by_id.get(recommendation.match_id)
            if match is not None:
                self._record_strategy_snapshot(recommendation, match)
        return [
            analysis.model_copy(
                update={
                    "recommendation": allocated_by_id.get(
                        analysis.recommendation.id,
                        analysis.recommendation,
                    )
                }
            )
            for analysis in analyses
        ]

    def _record_strategy_snapshot(self, recommendation: Recommendation, match: Match) -> None:
        snapshot = self._build_strategy_snapshot(recommendation, match)
        self.repository.upsert_model("strategy_snapshots", snapshot.id, snapshot)

    def _build_strategy_snapshot(self, recommendation: Recommendation, match: Match) -> StrategySnapshot:
        decision_time = datetime.now(self.settings.app.tzinfo)
        odds_basis = recommendation.odds_basis or {}
        score_breakdown = recommendation.score_breakdown or {}
        strategy_profile = odds_basis.get("strategy_profile") or score_breakdown.get("strategy_profile") or {}
        strategy_name = _strategy_snapshot_name(strategy_profile, odds_basis, score_breakdown)
        market_odds = self._strategy_snapshot_market_odds(recommendation)
        expected_value = _strategy_snapshot_expected_value(odds_basis)
        return StrategySnapshot(
            id=f"snapshot:{uuid4()}",
            recommendation_id=recommendation.id,
            match_id=recommendation.match_id,
            strategy_name=strategy_name,
            strategy_version=recommendation.version,
            strategy_profile=dict(strategy_profile) if isinstance(strategy_profile, dict) else {},
            decision_stage="allocated_recommendation",
            decision_time=decision_time,
            market_type=recommendation.market_type,
            selection=recommendation.selection,
            recommendation_status=recommendation.status,
            model_prediction={
                "confidence": recommendation.confidence,
                "value_score": recommendation.value_score,
                "risk_score": recommendation.risk_score,
                "implied_probability": _implied_probability(odds_basis.get("best_price")),
                "market_average_implied_probability": _implied_probability(odds_basis.get("market_average")),
            },
            market_odds=market_odds,
            expected_value=expected_value,
            time_to_kickoff_hours=_hours_to_kickoff(match, decision_time, self.settings.app.tzinfo),
            stake_units=recommendation.stake_units,
            gates_failed=list(score_breakdown.get("gates_failed") or []),
            reasoning=recommendation.reason,
            source_recommendation=recommendation.model_dump(mode="json"),
            audit_payload={
                "match": match.model_dump(mode="json"),
                "score_breakdown": score_breakdown,
                "risk_tags": recommendation.risk_tags,
            },
        )

    def _strategy_snapshot_market_odds(self, recommendation: Recommendation) -> dict:
        odds_basis = recommendation.odds_basis or {}
        relevant_odds = [
            snapshot.model_dump(mode="json")
            for snapshot in self.repository.list_models("odds", OddsSnapshot)
            if snapshot.match_id == recommendation.match_id
            and (recommendation.market_type is None or snapshot.market_type == recommendation.market_type)
        ]
        return {
            "selected": {
                "best_price": odds_basis.get("best_price"),
                "market_average": odds_basis.get("market_average"),
                "edge": odds_basis.get("edge"),
                "source": odds_basis.get("source"),
                "bookmaker": odds_basis.get("bookmaker"),
                "line": odds_basis.get("line"),
                "movement": odds_basis.get("movement"),
            },
            "snapshots": relevant_odds,
        }

    def record_bet(self, bet: BetLog) -> BetLog:
        self._validate_recordable_bet(bet)
        if not bet.id:
            bet.id = str(uuid4())
        self.repository.upsert_model("bets", bet.id, bet)
        self._invalidate_profile_review_actions()
        return bet

    def _validate_recordable_bet(self, bet: BetLog) -> None:
        if bet.stake_units <= 0 or _is_paper_platform(bet.platform):
            return
        match = self.repository.get_model("matches", bet.match_id, Match)
        if match is not None and _placed_after_kickoff(bet, match):
            raise ValueError(f"live_bet_after_kickoff:{bet.match_id}")
        recommendations = [
            recommendation
            for recommendation in self.repository.list_models("recommendations", Recommendation)
            if recommendation.match_id == bet.match_id
        ]
        normalized_bet_selection = _normalized_strategy_selection(bet.selection, bet.market_type.value)
        for recommendation in recommendations:
            if recommendation.status is not RecommendationStatus.recommended:
                continue
            if recommendation.market_type != bet.market_type:
                continue
            if _normalized_strategy_selection(recommendation.selection or "", recommendation.market_type.value) != normalized_bet_selection:
                continue
            live_gate = recommendation.score_breakdown.get("live_gate", {})
            if live_gate.get("passed") is not True:
                continue
            _validate_execution_odds(bet, recommendation, self.settings)
            existing_stake = self._existing_real_stake_units(bet, recommendation)
            if existing_stake + bet.stake_units > recommendation.stake_units + 1e-9:
                raise ValueError(
                    f"stake_exceeds_recommendation:{existing_stake + bet.stake_units:.3f}/"
                    f"{recommendation.stake_units:.3f}"
                )
            return
        raise ValueError(f"live_recommendation_required:{bet.match_id}:{bet.market_type.value}:{bet.selection}")

    def _existing_real_stake_units(self, bet: BetLog, recommendation: Recommendation) -> float:
        normalized_bet_selection = _normalized_strategy_selection(bet.selection, bet.market_type.value)
        total = 0.0
        for existing in self.repository.list_models("bets", BetLog):
            if existing.id and bet.id and existing.id == bet.id:
                continue
            if existing.match_id != bet.match_id:
                continue
            if existing.market_type != bet.market_type:
                continue
            if _is_paper_platform(existing.platform):
                continue
            if _normalized_strategy_selection(existing.selection, recommendation.market_type.value) != normalized_bet_selection:
                continue
            total += existing.stake_units
        return round(total, 3)

    def settle_bet(
        self,
        bet_id: str,
        result: str | None = None,
        closing_odds: float | None = None,
    ) -> BetLog:
        bet = self.repository.get_model("bets", bet_id, BetLog)
        if bet is None:
            raise KeyError(f"Bet not found: {bet_id}")

        settled_result = _normalize_bet_result(result) if result else self._infer_bet_result(bet)
        profit_units = _profit_for_result(settled_result, bet.odds, bet.stake_units)
        updates = {
            "result": settled_result,
            "profit_units": round(profit_units, 4),
        }
        if closing_odds is not None:
            updates["closing_odds"] = closing_odds
        settled = bet.model_copy(update=updates)
        self.repository.upsert_model("bets", settled.id, settled)
        self._backfill_strategy_snapshots_from_bet(settled)
        self._invalidate_profile_review_actions()
        return settled

    def _backfill_strategy_snapshots_from_bet(self, bet: BetLog) -> None:
        normalized_bet_selection = _normalized_strategy_selection(bet.selection, bet.market_type.value)
        for snapshot in self.repository.list_models("strategy_snapshots", StrategySnapshot):
            if snapshot.match_id != bet.match_id:
                continue
            if snapshot.market_type != bet.market_type:
                continue
            if _normalized_strategy_selection(snapshot.selection or "", bet.market_type.value) != normalized_bet_selection:
                continue
            closing_odds = dict(snapshot.closing_odds)
            if bet.closing_odds is not None:
                closing_odds.update({"odds": bet.closing_odds, "source": "settlement"})
            updated = snapshot.model_copy(
                update={
                    "closing_odds": closing_odds,
                    "clv": _bet_clv(bet),
                    "settlement_result": bet.result,
                    "profit_units": bet.profit_units,
                }
            )
            self.repository.upsert_model("strategy_snapshots", updated.id, updated)

    def settle_open_bets(self) -> BetSettlementReport:
        open_bets = [bet for bet in self.repository.list_models("bets", BetLog) if bet.profit_units is None]
        settled_bets: list[BetLog] = []
        skipped: list[str] = []
        errors: list[str] = []
        for bet in open_bets:
            try:
                settled_bets.append(self.settle_bet(bet.id))
            except ValueError as exc:
                message = str(exc)
                if message.startswith("missing_final_score:"):
                    skipped.append(f"{bet.id}:{message}")
                    continue
                errors.append(f"{bet.id}:{message}")
            except KeyError as exc:
                errors.append(f"{bet.id}:{exc}")
        return BetSettlementReport(
            scanned_count=len(open_bets),
            settled_count=len(settled_bets),
            skipped_count=len(skipped),
            error_count=len(errors),
            settled_bets=settled_bets,
            skipped=skipped,
            errors=errors,
        )

    def performance(self) -> PerformanceSummary:
        bets = self.repository.list_models("bets", BetLog)
        return _performance_summary(bets)

    def paper_bankroll(
        self,
        profile_id: str,
        initial_units: float = 1000.0,
        min_promotion_bets: int = 300,
        min_positive_clv_rate: float = 0.60,
        min_roi: float = 0.02,
        early_stop_bets: int = 100,
        stop_roi: float = -0.05,
        stop_positive_clv_rate: float = 0.40,
    ) -> PaperBankrollReport:
        bets = _paper_bets_for_profile(self.repository.list_models("bets", BetLog), profile_id)
        settled = [bet for bet in bets if bet.profit_units is not None]
        summary = _performance_summary(bets)
        positive_clv_rate = _positive_clv_rate(settled)
        max_drawdown = _max_drawdown_units(settled)
        consecutive_losses = _consecutive_losses(settled)
        issues: list[str] = []
        action = "continue_paper"
        status = "active"
        if summary.settled_bets >= early_stop_bets and (
            (summary.roi is not None and summary.roi < stop_roi)
            or (positive_clv_rate is not None and positive_clv_rate < stop_positive_clv_rate)
        ):
            status = "failed"
            action = "stop_strategy"
            issues.append("paper_stop_threshold")
        elif summary.settled_bets >= min_promotion_bets:
            if summary.roi is None or summary.roi <= min_roi:
                issues.append(f"roi_below_promotion:{summary.roi}/{min_roi}")
            if positive_clv_rate is None or positive_clv_rate < min_positive_clv_rate:
                issues.append(f"positive_clv_below_promotion:{positive_clv_rate}/{min_positive_clv_rate}")
            if not issues:
                status = "qualified"
                action = "promote_to_simulated_live"
            else:
                status = "needs_more_evidence"
        return PaperBankrollReport(
            profile_id=profile_id,
            initial_units=round(initial_units, 3),
            current_units=round(initial_units + summary.profit_units, 3),
            bets=summary.bets,
            settled_bets=summary.settled_bets,
            total_stake_units=summary.total_stake_units,
            profit_units=summary.profit_units,
            roi=summary.roi,
            average_clv=summary.average_clv,
            positive_clv_rate=positive_clv_rate,
            max_drawdown_units=max_drawdown,
            consecutive_losses=consecutive_losses,
            status=status,
            action=action,
            issues=issues,
        )

    def performance_by_league(self) -> PerformanceByLeagueReport:
        matches = {match.id: match for match in self.repository.list_models("matches", Match)}
        groups: dict[tuple[str, str, str], list[BetLog]] = {}
        for bet in self.repository.list_models("bets", BetLog):
            match = matches.get(bet.match_id)
            league_code, league_name, tier = self._performance_league_key(match)
            groups.setdefault((league_code, league_name, tier), []).append(bet)

        summaries = []
        for (league_code, league_name, tier), bets in sorted(groups.items()):
            summary = _performance_summary(bets)
            summaries.append(
                PerformanceGroupSummary(
                    league_code=league_code,
                    league_name=league_name,
                    tier=tier,
                    **summary.model_dump(),
                )
            )
        return PerformanceByLeagueReport(groups=summaries)

    async def sources_health(self) -> list[SourceHealth]:
        return await self.health_checker.check_all()

    def _infer_bet_result(self, bet: BetLog) -> str:
        match = self.repository.get_model("matches", bet.match_id, Match)
        if match is None:
            raise KeyError(f"Match not found: {bet.match_id}")
        market_type = bet.market_type.value
        if market_type == "1x2":
            winning_selection = _winning_1x2_selection(match)
            return "win" if bet.selection.upper() == winning_selection else "loss"
        if market_type == "asian_handicap":
            return _infer_asian_handicap_result(bet, match)
        if market_type == "over_under":
            return _infer_over_under_result(bet, match)
        raise ValueError(f"explicit_result_required:{market_type}")

    def _performance_league_key(self, match: Match | None) -> tuple[str, str, str]:
        if match is None:
            return ("UNKNOWN", "Unknown", "unknown")
        normalized_league = match.league.strip().lower()
        for league in self.settings.leagues:
            values = [league.code, league.name, league.football_data_uk_code, league.football_data_org_code]
            if league.country and league.name:
                values.append(f"{league.country} - {league.name}")
            values.extend(league.aliases)
            if normalized_league in {value.strip().lower() for value in values if value}:
                return (league.code, league.name, league.tier)
        return (match.league.upper().replace(" ", "_"), match.league, "unknown")


def _winning_1x2_selection(match: Match) -> str:
    if match.home_score is None or match.away_score is None:
        raise ValueError(f"missing_final_score:{match.id}")
    if match.home_score > match.away_score:
        return "HOME"
    if match.home_score < match.away_score:
        return "AWAY"
    return "DRAW"


def _normalize_bet_result(result: str) -> str:
    normalized = result.strip().lower()
    if normalized not in {"win", "loss", "void", "half_win", "half_loss"}:
        raise ValueError(f"unsupported_bet_result:{result}")
    return normalized


def _profit_for_result(result: str, odds: float, stake_units: float) -> float:
    if result == "win":
        return (odds - 1.0) * stake_units
    if result == "half_win":
        return (odds - 1.0) * stake_units / 2
    if result == "half_loss":
        return -stake_units / 2
    if result == "loss":
        return -stake_units
    return 0.0


def _infer_asian_handicap_result(bet: BetLog, match: Match) -> str:
    _require_final_score(match)
    side, line = _parse_asian_handicap_selection(bet.selection)
    margin = (match.home_score or 0) - (match.away_score or 0)
    side_margin = margin if side == "home" else -margin
    return _aggregate_line_results([_settle_margin(side_margin + sub_line) for sub_line in _split_quarter_line(line)])


def _infer_over_under_result(bet: BetLog, match: Match) -> str:
    _require_final_score(match)
    side, line = _parse_total_selection(bet.selection)
    total_goals = (match.home_score or 0) + (match.away_score or 0)
    results = []
    for sub_line in _split_quarter_line(line):
        if side == "over":
            results.append(_settle_margin(total_goals - sub_line))
        else:
            results.append(_settle_margin(sub_line - total_goals))
    return _aggregate_line_results(results)


def _parse_asian_handicap_selection(selection: str) -> tuple[str, float]:
    upper = selection.strip().upper()
    if "AH_HOME" in upper or upper.startswith("HOME"):
        side = "home"
    elif "AH_AWAY" in upper or upper.startswith("AWAY"):
        side = "away"
    else:
        raise ValueError(f"unsupported_asian_handicap_selection:{selection}")
    return side, _parse_selection_line(selection)


def _parse_total_selection(selection: str) -> tuple[str, float]:
    upper = selection.strip().upper()
    if upper.startswith("OVER"):
        side = "over"
    elif upper.startswith("UNDER"):
        side = "under"
    else:
        raise ValueError(f"unsupported_total_selection:{selection}")
    return side, _parse_selection_line(selection)


def _parse_selection_line(selection: str) -> float:
    special = selection.strip().upper().replace(" ", "")
    if special in {"OVER25", "UNDER25"}:
        return 2.5
    match = re.search(r"[-+]?\d+(?:\.\d+)?", selection)
    if match is None:
        raise ValueError(f"selection_line_required:{selection}")
    return float(match.group(0))


def _split_quarter_line(line: float) -> list[float]:
    if abs(line * 4 - round(line * 4)) < 1e-9 and abs(line * 2 - round(line * 2)) > 1e-9:
        return [line - 0.25, line + 0.25]
    return [line]


def _settle_margin(adjusted_margin: float) -> str:
    if adjusted_margin > 1e-9:
        return "win"
    if adjusted_margin < -1e-9:
        return "loss"
    return "void"


def _aggregate_line_results(results: list[str]) -> str:
    score = sum({"win": 1.0, "void": 0.0, "loss": -1.0}[result] for result in results) / len(results)
    if score >= 1.0:
        return "win"
    if score <= -1.0:
        return "loss"
    if score > 0:
        return "half_win"
    if score < 0:
        return "half_loss"
    return "void"


def _require_final_score(match: Match) -> None:
    if match.home_score is None or match.away_score is None:
        raise ValueError(f"missing_final_score:{match.id}")


def _is_paper_platform(platform: str) -> bool:
    return platform.strip().lower() in {"paper", "paper_trading", "simulation"}


def _validate_execution_odds(bet: BetLog, recommendation: Recommendation, settings: Settings) -> None:
    approved_odds = _approved_odds(recommendation)
    if approved_odds is None:
        raise ValueError(f"execution_odds_reference_missing:{recommendation.id}")
    slippage = settings.live_trading.max_execution_odds_slippage
    minimum_odds = approved_odds * (1.0 - slippage)
    if bet.odds + 1e-9 < minimum_odds:
        raise ValueError(
            f"execution_odds_below_minimum:{bet.odds:.3f}/{minimum_odds:.3f}:"
            f"approved={approved_odds:.3f}:slippage={slippage:.3f}"
        )


def _approved_odds(recommendation: Recommendation) -> float | None:
    value = recommendation.odds_basis.get("best_price")
    try:
        odds = float(value)
    except (TypeError, ValueError):
        return None
    return odds if odds > 1.0 else None


def _bet_clv(bet: BetLog) -> float | None:
    if bet.closing_odds is None or bet.odds <= 0 or bet.closing_odds <= 0:
        return None
    return round((bet.odds / bet.closing_odds) - 1.0, 6)


def _is_world_cup_final_recommendation(
    recommendation: Recommendation,
    match: Match,
    settings: Settings,
) -> bool:
    if match.league.strip().lower() not in {
        str(value).strip().lower()
        for league in settings.leagues
        if league.code.upper() == "WORLD_CUP"
        for value in [league.code, league.name, *(league.aliases or [])]
        if value
    }:
        return False
    gate = (
        recommendation.score_breakdown.get("world_cup_high_winrate")
        or recommendation.odds_basis.get("world_cup_high_winrate")
        or {}
    )
    return isinstance(gate, dict) and gate.get("stage") == "final" and gate.get("passed") is True


def _placed_after_kickoff(bet: BetLog, match: Match) -> bool:
    placed_at = bet.placed_at
    kickoff_at = match.kickoff_at
    if placed_at.tzinfo is None and kickoff_at.tzinfo is not None:
        placed_at = placed_at.replace(tzinfo=kickoff_at.tzinfo)
    elif placed_at.tzinfo is not None and kickoff_at.tzinfo is None:
        kickoff_at = kickoff_at.replace(tzinfo=placed_at.tzinfo)
    return placed_at >= kickoff_at


def _performance_summary(bets: list[BetLog]) -> PerformanceSummary:
    settled = [bet for bet in bets if bet.profit_units is not None]
    total_stake = sum(bet.stake_units for bet in settled)
    profit = sum(bet.profit_units or 0.0 for bet in settled)
    clv_values = [
        (bet.odds / bet.closing_odds) - 1.0
        for bet in settled
        if bet.closing_odds is not None and bet.odds > 0 and bet.closing_odds > 0
    ]
    roi = profit / total_stake if total_stake else None
    average_clv = sum(clv_values) / len(clv_values) if clv_values else None
    return PerformanceSummary(
        bets=len(bets),
        settled_bets=len(settled),
        total_stake_units=round(total_stake, 3),
        profit_units=round(profit, 3),
        roi=round(roi, 4) if roi is not None else None,
        average_clv=round(average_clv, 4) if average_clv is not None else None,
    )


def _paper_bets_for_profile(bets: list[BetLog], profile_id: str) -> list[BetLog]:
    marker = f"profile_id={profile_id}"
    return [
        bet
        for bet in bets
        if _is_paper_platform(bet.platform)
        and marker in (bet.notes or "")
    ]


def _positive_clv_rate(bets: list[BetLog]) -> float | None:
    values = [
        _bet_clv(bet)
        for bet in bets
        if bet.closing_odds is not None and bet.odds > 0 and bet.closing_odds > 0
    ]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return round(sum(1 for value in values if value > 0) / len(values), 4)


def _max_drawdown_units(bets: list[BetLog]) -> float | None:
    if not bets:
        return None
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for bet in sorted(bets, key=lambda item: item.placed_at):
        equity += bet.profit_units or 0.0
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return round(max_drawdown, 3)


def _consecutive_losses(bets: list[BetLog]) -> int:
    streak = 0
    for bet in sorted(bets, key=lambda item: item.placed_at, reverse=True):
        if (bet.profit_units or 0.0) < 0:
            streak += 1
            continue
        break
    return streak


def _strategy_snapshot_name(
    strategy_profile: object,
    odds_basis: dict,
    score_breakdown: dict,
) -> str:
    if isinstance(strategy_profile, dict) and strategy_profile.get("id"):
        return str(strategy_profile["id"])
    confidence_class = odds_basis.get("strategy_confidence_class") or score_breakdown.get("strategy_confidence_class")
    if confidence_class:
        return str(confidence_class)
    return "score_match_v1"


def _strategy_snapshot_expected_value(odds_basis: dict) -> float | None:
    value = odds_basis.get("edge")
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _implied_probability(value: object) -> float | None:
    try:
        odds = float(value)
    except (TypeError, ValueError):
        return None
    if odds <= 1.0:
        return None
    return round(1.0 / odds, 6)


def _hours_to_kickoff(match: Match, decision_time: datetime, tzinfo: object) -> float:
    kickoff_at = match.kickoff_at
    if kickoff_at.tzinfo is None:
        kickoff_at = kickoff_at.replace(tzinfo=tzinfo)
    if decision_time.tzinfo is None:
        decision_time = decision_time.replace(tzinfo=tzinfo)
    return round((kickoff_at - decision_time).total_seconds() / 3600.0, 4)


def get_service() -> AnalysisService:
    return AnalysisService.from_config()


def get_api_service():
    service = AnalysisService.from_config()
    try:
        yield service
    finally:
        service.repository.close()
