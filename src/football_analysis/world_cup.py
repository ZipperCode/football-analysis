from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from pydantic import BaseModel, Field

from football_analysis.models import (
    AgentFinding,
    BetLog,
    EvidenceSource,
    Match,
    OddsSnapshot,
    Recommendation,
    RecommendationStatus,
)
from football_analysis.live_gate import apply_live_gate
from football_analysis.research import research_and_store_match
from football_analysis.service import AnalysisService
from football_analysis.settings import Settings

WORLD_CUP_LEAGUE_CODE = "WORLD_CUP"
WORLD_CUP_PROFILE_ID = "world_cup_high_winrate"
WORLD_CUP_RESEARCH_PROVIDERS = ("exa", "firecrawl", "tavily")
ADVISORY_WINDOW_MIN_MINUTES = 360
ADVISORY_WINDOW_MAX_MINUTES = 720
FINAL_WINDOW_MIN_MINUTES = 60
FINAL_WINDOW_MAX_MINUTES = 90


class WorldCupHistoricalBacktestReport(BaseModel):
    profile_id: str = WORLD_CUP_PROFILE_ID
    status: str
    passed: bool
    sample_scope: list[str]
    minimum_hit_rate: float = 0.65
    hit_rate: float | None = None
    roi: float | None = None
    max_drawdown_units: float | None = None
    settled_bets: int = 0
    issues: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class _MatchDataState:
    match: Match
    odds: list[OddsSnapshot]
    findings: list[AgentFinding]
    issues: list[str]


def refresh_world_cup_data(
    service: AnalysisService,
    match_date: str,
    include_research: bool = True,
    research_provider: str = "auto",
) -> dict[str, Any]:
    """Refresh World Cup fixtures, odds and QQSD context with explicit blockers."""
    issues: list[str] = []
    operations: list[dict[str, Any]] = []

    if not _credential_present(service.settings, "qqsd"):
        issues.append("missing_required_env:QQSD_C_CK")

    previous_live_context = service.settings.ingestion.qqsd_live_context_enabled
    previous_timeline = service.settings.ingestion.qqsd_odds_timeline_enabled
    service.settings.ingestion.qqsd_live_context_enabled = True
    service.settings.ingestion.qqsd_odds_timeline_enabled = True
    try:
        fixture_result = service.ingestion.ingest_fixtures(
            date=match_date,
            source="qqsd",
            league_code=WORLD_CUP_LEAGUE_CODE,
        )
        operations.append({"operation": "qqsd_fixtures", "result": fixture_result.model_dump(mode="json")})
        issues.extend(f"qqsd_fixtures:{error}" for error in fixture_result.errors)

        odds_result = service.ingestion.ingest_odds(
            date=match_date,
            source="qqsd",
            league_code=WORLD_CUP_LEAGUE_CODE,
        )
        operations.append({"operation": "qqsd_odds", "result": odds_result.model_dump(mode="json")})
        issues.extend(f"qqsd_odds:{error}" for error in odds_result.errors)

        try:
            standings_result = service.ingestion.ingest_standings(
                league_code=WORLD_CUP_LEAGUE_CODE,
                source="qqsd",
            )
            operations.append({"operation": "qqsd_standings", "result": standings_result.model_dump(mode="json")})
            issues.extend(f"qqsd_standings:{error}" for error in standings_result.errors)
        except Exception as exc:
            issues.append(f"qqsd_standings:{type(exc).__name__}:{exc}")
    finally:
        service.settings.ingestion.qqsd_live_context_enabled = previous_live_context
        service.settings.ingestion.qqsd_odds_timeline_enabled = previous_timeline

    if include_research:
        research_report = research_world_cup(service, hours=48, provider=research_provider)
        operations.append({"operation": "research", "result": research_report})
        issues.extend(f"research:{issue}" for issue in research_report.get("issues", []))

    matches = _world_cup_matches_on_date(service, match_date)
    match_ids = {match.id for match in matches}
    odds = [
        snapshot
        for snapshot in service.repository.list_models("odds", OddsSnapshot)
        if snapshot.match_id in match_ids
    ]
    findings = [
        finding
        for finding in service.repository.list_models("findings", AgentFinding)
        if finding.match_id in match_ids
    ]
    data_states = [_match_data_state(service, match) for match in matches]
    issues.extend(issue for state in data_states for issue in state.issues if issue.startswith("world_cup_qqsd_"))
    if not matches:
        issues.append(f"no_world_cup_fixtures:{match_date}")
    if matches and not odds:
        issues.append(f"no_world_cup_odds:{match_date}")

    return {
        "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
        "date": match_date,
        "status": "blocked" if issues else "ready",
        "league": WORLD_CUP_LEAGUE_CODE,
        "matches": len(matches),
        "odds_snapshots": len(odds),
        "findings": len(findings),
        "qqsd_data_states": [_qqsd_data_summary(state.findings) for state in data_states],
        "issues": _dedupe(issues),
        "operations": operations,
    }


def research_world_cup(
    service: AnalysisService,
    hours: int = 48,
    provider: str = "auto",
    limit: int = 5,
) -> dict[str, Any]:
    issues: list[str] = []
    provider_status = _research_provider_status(provider)
    if not provider_status["available"]:
        issues.append(provider_status["issue"])

    now = datetime.now(service.settings.app.tzinfo)
    window_end = now + timedelta(hours=max(1, hours))
    matches = [
        match
        for match in service.repository.list_models("matches", Match)
        if _is_world_cup_match(match, service.settings)
        and now <= match.kickoff_at.astimezone(service.settings.app.tzinfo) <= window_end
    ]
    findings: list[AgentFinding] = []
    errors: list[str] = []
    if provider_status["available"]:
        for match in matches:
            try:
                finding = research_and_store_match(match, service.repository, provider=provider, limit=limit)
            except Exception as exc:
                errors.append(f"{match.id}:{type(exc).__name__}:{exc}")
                continue
            if finding is not None:
                findings.append(finding)
    issues.extend(f"research_error:{error}" for error in errors)
    if matches and provider_status["available"] and not findings:
        issues.append("research_no_actionable_findings")

    return {
        "checked_at": now.isoformat(),
        "status": "blocked" if issues else "ready",
        "provider": provider_status,
        "hours": hours,
        "matches": len(matches),
        "findings": len(findings),
        "issues": _dedupe(issues),
        "finding_ids": [finding.id for finding in findings],
    }


def backtest_world_cup_high_winrate(service: AnalysisService) -> WorldCupHistoricalBacktestReport:
    profile = _world_cup_profile(service.settings)
    if profile is not None:
        evidence = profile.model_dump(mode="json")
        hit_rate = _profile_hit_rate(evidence)
        settled_bets = int(evidence.get("settled_bets") or 0)
        roi = evidence.get("roi")
        max_drawdown_units = evidence.get("max_drawdown_units")
        issues = _world_cup_profile_issues(evidence, service.settings)
        return WorldCupHistoricalBacktestReport(
            status="passed" if not issues else "blocked",
            passed=not issues,
            sample_scope=_sample_scope(evidence),
            hit_rate=hit_rate,
            roi=roi,
            max_drawdown_units=max_drawdown_units,
            settled_bets=settled_bets,
            issues=issues,
            evidence=evidence,
        )

    stored = service.repository.get_cached_payload(
        "world_cup",
        "historical_backtest",
        WORLD_CUP_PROFILE_ID,
    )
    if isinstance(stored, dict):
        hit_rate = _safe_float(stored.get("hit_rate"))
        settled_bets = int(stored.get("settled_bets") or 0)
        issues = _world_cup_backtest_payload_issues(stored)
        return WorldCupHistoricalBacktestReport(
            status="passed" if not issues else "blocked",
            passed=not issues,
            sample_scope=_sample_scope(stored),
            hit_rate=hit_rate,
            roi=_safe_float(stored.get("roi")),
            max_drawdown_units=_safe_float(stored.get("max_drawdown_units")),
            settled_bets=settled_bets,
            issues=issues,
            evidence=stored,
        )

    return WorldCupHistoricalBacktestReport(
        status="blocked",
        passed=False,
        sample_scope=_default_sample_scope(),
        issues=["missing_world_cup_high_winrate_backtest_evidence"],
    )


def recommend_world_cup(
    service: AnalysisService,
    match_date: str,
    stage: str = "advisory",
    ignore_final_window: bool = False,
) -> dict[str, Any]:
    normalized_stage = stage.lower().strip()
    if normalized_stage not in {"advisory", "final"}:
        raise ValueError(f"unsupported_world_cup_stage:{stage}")

    now = datetime.now(service.settings.app.tzinfo)
    matches = _world_cup_matches_on_date(service, match_date)
    analyses = [service._score_analysis(match.id) for match in matches]
    recommendations = [analysis.recommendation for analysis in analyses]
    issues: list[str] = []
    upgraded: list[Recommendation] = []

    backtest = backtest_world_cup_high_winrate(service)
    provider_status = _research_provider_status("auto")

    for analysis in analyses:
        state = _match_data_state(service, analysis.match)
        if normalized_stage == "advisory":
            recommendation = _advisory_recommendation(analysis.recommendation)
            issues.extend(_advisory_window_issues(state.match, now, service.settings))
        else:
            recommendation, item_issues = _final_recommendation(
                service,
                analysis.recommendation,
                state,
                backtest,
                provider_status,
                now,
                ignore_final_window=ignore_final_window,
            )
            if not item_issues and recommendation.status is RecommendationStatus.recommended:
                recommendation = apply_live_gate(
                    recommendation,
                    match=analysis.match,
                    odds_snapshots=state.odds,
                    bet_logs=service.repository.list_models("bets", BetLog),
                    settings=service.settings,
                    profile_review_actions=service._profile_review_actions(),
                )
            issues.extend(item_issues)
        service.repository.upsert_model("recommendations", recommendation.id, recommendation)
        upgraded.append(recommendation)

    if not matches:
        issues.append(f"no_world_cup_fixtures:{match_date}")

    status = "ready" if any(item.status is RecommendationStatus.recommended for item in upgraded) else "blocked"
    if normalized_stage == "advisory" and any(
        item.status is RecommendationStatus.advisory_recommended for item in upgraded
    ):
        status = "advisory"

    return {
        "checked_at": now.isoformat(),
        "date": match_date,
        "stage": normalized_stage,
        "status": status,
        "league": WORLD_CUP_LEAGUE_CODE,
        "matches": len(matches),
        "recommendations": [item.model_dump(mode="json") for item in upgraded],
        "backtest": backtest.model_dump(mode="json"),
        "research_provider": provider_status,
        "ignore_final_window": ignore_final_window,
        "issues": _dedupe(issues),
    }


def execution_queue_world_cup(service: AnalysisService, include_past: bool = False, platform: str = "real") -> dict[str, Any]:
    from football_analysis.production import build_production_execution_queue

    return build_production_execution_queue(
        service,
        include_past=include_past,
        platform=platform,
        league_codes={WORLD_CUP_LEAGUE_CODE},
    )


def is_world_cup_final_ready_recommendation(
    recommendation: Recommendation,
    match: Match,
    settings: Settings,
) -> bool:
    if not _is_world_cup_match(match, settings):
        return False
    payload = _world_cup_gate_payload(recommendation)
    return payload.get("stage") == "final" and payload.get("passed") is True


def world_cup_strategy_profile_payload(settings: Settings) -> dict[str, Any]:
    report = _world_cup_profile_report(settings)
    payload = dict(report.evidence)
    payload.setdefault("matched", report.passed)
    payload.setdefault("id", WORLD_CUP_PROFILE_ID)
    payload.setdefault("name", "World Cup high win-rate 1x2")
    payload.setdefault("league_code", WORLD_CUP_LEAGUE_CODE)
    payload.setdefault("market_type", "1x2")
    return payload


def _final_recommendation(
    service: AnalysisService,
    recommendation: Recommendation,
    state: _MatchDataState,
    backtest: WorldCupHistoricalBacktestReport,
    provider_status: dict[str, Any],
    now: datetime,
    ignore_final_window: bool = False,
) -> tuple[Recommendation, list[str]]:
    issues = list(state.issues)
    has_advisory_signal = (
        recommendation.status is RecommendationStatus.advisory_recommended
        or isinstance(recommendation.score_breakdown.get("research_advisory"), dict)
        or isinstance(recommendation.odds_basis.get("research_advisory"), dict)
    )
    if not has_advisory_signal:
        issues.append(f"world_cup_no_advisory_signal:{state.match.id}")
    if recommendation.market_type is None or recommendation.market_type.value != "1x2":
        issues.append(f"world_cup_market_not_1x2:{state.match.id}")
    if not backtest.passed:
        issues.extend(f"world_cup_backtest:{issue}" for issue in backtest.issues)
    if not provider_status["available"]:
        issues.append(provider_status["issue"])
    kickoff_minutes = _minutes_to_kickoff(state.match, now, service.settings)
    if (
        service.settings.live_trading.world_cup_final_window_gate_enabled
        and not ignore_final_window
        and (kickoff_minutes is None or kickoff_minutes < FINAL_WINDOW_MIN_MINUTES or kickoff_minutes > FINAL_WINDOW_MAX_MINUTES)
    ):
        issues.append(
            f"world_cup_final_window:{state.match.id}:"
            f"{'unknown' if kickoff_minutes is None else int(kickoff_minutes)}m"
        )
    if _fresh_1x2_bookmaker_count(state.odds, recommendation, service.settings, now) < 2:
        issues.append(f"world_cup_fresh_1x2_odds_insufficient:{state.match.id}")
    if _cross_checked_research_sources(state.findings) < 2:
        issues.append(f"world_cup_research_sources_insufficient:{state.match.id}")
    if not _has_lineup_or_injury_context(state.findings):
        issues.append(f"world_cup_lineup_or_injury_context_missing:{state.match.id}")

    if issues:
        payload = _world_cup_gate(
            stage="final",
            passed=False,
            backtest=backtest,
            provider_status=provider_status,
            issues=issues,
            ignore_final_window=ignore_final_window,
            qqsd_data=_qqsd_data_summary(state.findings),
        )
        return _with_world_cup_gate(recommendation, payload, stake_units=0.0), issues

    tier = _world_cup_tier(recommendation)
    stake_units = 0.5 if tier == "A" else 0.25
    stake_units = min(stake_units, service.settings.live_trading.max_stake_units_per_pick)
    if _planned_world_cup_stake_for_day(service, state.match) + stake_units > 1.0:
        issues.append(f"world_cup_daily_stake_limit:{state.match.id}")
        payload = _world_cup_gate(
            stage="final",
            passed=False,
            backtest=backtest,
            provider_status=provider_status,
            issues=issues,
            tier=tier,
            ignore_final_window=ignore_final_window,
            qqsd_data=_qqsd_data_summary(state.findings),
        )
        return _with_world_cup_gate(recommendation, payload, stake_units=0.0), issues

    payload = _world_cup_gate(
        stage="final",
        passed=True,
        backtest=backtest,
        provider_status=provider_status,
        issues=[],
        tier=tier,
        ignore_final_window=ignore_final_window,
        qqsd_data=_qqsd_data_summary(state.findings),
    )
    score_breakdown = dict(recommendation.score_breakdown)
    odds_basis = dict(recommendation.odds_basis)
    score_breakdown["world_cup_high_winrate"] = payload
    score_breakdown["strategy_profile"] = world_cup_strategy_profile_payload(service.settings)
    score_breakdown["strategy_confidence_class"] = "validated_strategy"
    odds_basis["world_cup_high_winrate"] = payload
    odds_basis["strategy_profile"] = score_breakdown["strategy_profile"]
    odds_basis["strategy_confidence_class"] = "validated_strategy"
    cleared_prefixes = (
        "live_status_not_recommended:",
        "live_min_edge:",
        "live_missing_strategy_profile",
        "world_cup_final_gate_required",
    )
    risk_tags = sorted(
        tag
        for tag in set(recommendation.risk_tags + ["world_cup_high_winrate", f"world_cup_tier_{tier.lower()}"])
        if tag != "advisory_no_real_stake" and not any(tag.startswith(prefix) for prefix in cleared_prefixes)
    )
    return recommendation.model_copy(
        update={
            "status": RecommendationStatus.recommended,
            "stake_units": round(stake_units, 3),
            "score_breakdown": score_breakdown,
            "odds_basis": odds_basis,
            "risk_tags": risk_tags,
            "reason": (
                recommendation.reason
                + f" 世界杯高胜率 profile 通过，final 阶段升级为人工执行候选，评级 {tier}，仓位 {stake_units:.2f}u。"
            ),
        }
    ), []


def _advisory_recommendation(recommendation: Recommendation) -> Recommendation:
    if recommendation.status is RecommendationStatus.advisory_recommended:
        return recommendation
    if recommendation.status is RecommendationStatus.recommended:
        return recommendation.model_copy(
            update={
                "status": RecommendationStatus.advisory_recommended,
                "stake_units": 0.0,
                "risk_tags": sorted(set(recommendation.risk_tags + ["advisory_no_real_stake"])),
            }
        )
    return recommendation


def _with_world_cup_gate(
    recommendation: Recommendation,
    payload: dict[str, Any],
    stake_units: float,
) -> Recommendation:
    score_breakdown = dict(recommendation.score_breakdown)
    odds_basis = dict(recommendation.odds_basis)
    score_breakdown["world_cup_high_winrate"] = payload
    odds_basis["world_cup_high_winrate"] = payload
    return recommendation.model_copy(
        update={
            "stake_units": stake_units,
            "score_breakdown": score_breakdown,
            "odds_basis": odds_basis,
            "risk_tags": sorted(set(recommendation.risk_tags + payload.get("issues", []))),
        }
    )


def _match_data_state(service: AnalysisService, match: Match) -> _MatchDataState:
    odds = [
        snapshot
        for snapshot in service.repository.list_models("odds", OddsSnapshot)
        if snapshot.match_id == match.id
    ]
    findings = [
        finding
        for finding in service.repository.list_models("findings", AgentFinding)
        if finding.match_id == match.id
    ]
    issues: list[str] = []
    if match.data_completeness < 0.82:
        issues.append(f"world_cup_data_completeness:{match.id}:{match.data_completeness:.2f}/0.82")
    if not odds:
        issues.append(f"world_cup_missing_odds:{match.id}")
    if not findings:
        issues.append(f"world_cup_missing_research:{match.id}")
    issues.extend(_qqsd_context_issues(match, findings))
    return _MatchDataState(match=match, odds=odds, findings=findings, issues=issues)


def _qqsd_context_issues(match: Match, findings: list[AgentFinding]) -> list[str]:
    context = _qqsd_context_payload(findings)
    if not context:
        return [f"world_cup_qqsd_context_missing:{match.id}"]
    issues: list[str] = []
    if context.get("detail") in (None, {}, []):
        issues.append(f"world_cup_qqsd_detail_missing:{match.id}")
    if context.get("standings") in (None, {}, []):
        issues.append(f"world_cup_qqsd_standings_missing:{match.id}")
    odds_context = context.get("odds_context")
    if not isinstance(odds_context, dict) or not odds_context:
        issues.append(f"world_cup_qqsd_odds_context_missing:{match.id}")
    timeline = context.get("odds_timeline")
    if not isinstance(timeline, dict):
        issues.append(f"world_cup_qqsd_timeline_missing:{match.id}")
        return issues
    markets = timeline.get("markets")
    if not isinstance(markets, dict):
        issues.append(f"world_cup_qqsd_timeline_markets_missing:{match.id}")
        return issues
    for market in ("1x2", "asian_handicap", "over_under"):
        item = markets.get(market)
        if not isinstance(item, dict):
            issues.append(f"world_cup_qqsd_timeline_market_missing:{match.id}:{market}")
            continue
        if item.get("current_available") is not True:
            issues.append(f"world_cup_qqsd_current_odds_missing:{match.id}:{market}")
        if int(item.get("history_row_count") or 0) <= 0:
            availability = str(item.get("history_availability") or "unknown")
            issues.append(f"world_cup_qqsd_timeline_history_missing:{match.id}:{market}:{availability}")
    qqsd_errors = context.get("qqsd_errors")
    if isinstance(qqsd_errors, list):
        hard_keys = {"detail", "standings", "odds_timeline"}
        for row in qqsd_errors:
            if isinstance(row, dict) and str(row.get("key") or "") in hard_keys:
                issues.append(f"world_cup_qqsd_error:{match.id}:{row.get('key')}:{row.get('error')}")
    return _dedupe(issues)


def _qqsd_context_payload(findings: list[AgentFinding]) -> dict[str, Any]:
    for finding in findings:
        if finding.agent_name == "qqsd_full_context" and isinstance(finding.payload, dict):
            return finding.payload
    return {}


def _qqsd_data_summary(findings: list[AgentFinding]) -> dict[str, Any]:
    payload = _qqsd_context_payload(findings)
    if not payload:
        return {"provider": "qqsd", "available": False}
    timeline = payload.get("odds_timeline") if isinstance(payload.get("odds_timeline"), dict) else {}
    markets = timeline.get("markets") if isinstance(timeline, dict) else {}
    market_summary: dict[str, Any] = {}
    if isinstance(markets, dict):
        for market, item in markets.items():
            if isinstance(item, dict):
                market_summary[str(market)] = {
                    "company": item.get("company"),
                    "current_available": item.get("current_available"),
                    "history_row_count": item.get("history_row_count"),
                    "history_availability": item.get("history_availability"),
                }
    return {
        "provider": "qqsd",
        "available": True,
        "fid": payload.get("fid"),
        "detail": payload.get("detail") not in (None, {}, []),
        "standings": payload.get("standings") not in (None, {}, []),
        "odds_context": payload.get("odds_context") not in (None, {}, []),
        "timeline_summary": timeline.get("summary") if isinstance(timeline, dict) else None,
        "markets": market_summary,
        "errors": payload.get("qqsd_errors") or [],
    }
def _world_cup_matches_on_date(service: AnalysisService, match_date: str) -> list[Match]:
    parsed = date.fromisoformat(match_date)
    start = datetime.combine(parsed, time.min, tzinfo=service.settings.app.tzinfo)
    end = datetime.combine(parsed, time.max, tzinfo=service.settings.app.tzinfo)
    matches = [
        match
        for match in service.repository.list_models("matches", Match)
        if _is_world_cup_match(match, service.settings)
        and start <= match.kickoff_at.astimezone(service.settings.app.tzinfo) <= end
    ]
    matches.sort(key=lambda item: item.kickoff_at)
    return matches


def _is_world_cup_match(match: Match, settings: Settings) -> bool:
    values = {match.league.strip().upper()}
    for league in settings.leagues:
        if league.code.upper() != WORLD_CUP_LEAGUE_CODE:
            continue
        values.update(
            str(item).strip().upper()
            for item in [league.code, league.name, *(league.aliases or [])]
            if item
        )
    return WORLD_CUP_LEAGUE_CODE in values or match.league.strip().upper() in values


def _world_cup_profile(settings: Settings):
    for profile in settings.strategy_profiles:
        if profile.id == WORLD_CUP_PROFILE_ID:
            return profile
    return None


def _world_cup_profile_report(settings: Settings) -> WorldCupHistoricalBacktestReport:
    profile = _world_cup_profile(settings)
    evidence = profile.model_dump(mode="json") if profile else {}
    issues = _world_cup_profile_issues(evidence, settings) if evidence else ["missing_world_cup_high_winrate_profile"]
    return WorldCupHistoricalBacktestReport(
        status="passed" if not issues else "blocked",
        passed=not issues,
        sample_scope=_sample_scope(evidence),
        hit_rate=_profile_hit_rate(evidence),
        roi=_safe_float(evidence.get("roi")),
        max_drawdown_units=_safe_float(evidence.get("max_drawdown_units")),
        settled_bets=int(evidence.get("settled_bets") or 0),
        issues=issues,
        evidence=evidence,
    )


def _world_cup_profile_issues(evidence: dict[str, Any], settings: Settings) -> list[str]:
    issues = _world_cup_backtest_payload_issues(evidence)
    if evidence.get("live_enabled") is not True:
        issues.append("world_cup_profile_not_live_enabled")
    if str(evidence.get("league_code") or "").upper() != WORLD_CUP_LEAGUE_CODE:
        issues.append("world_cup_profile_league_mismatch")
    if str(evidence.get("market_type") or "") != "1x2":
        issues.append("world_cup_profile_market_not_1x2")
    if float(evidence.get("max_stake_units") or 0.0) > settings.live_trading.max_stake_units_per_pick:
        issues.append("world_cup_profile_stake_exceeds_live_cap")
    return _dedupe(issues)


def _world_cup_backtest_payload_issues(evidence: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    hit_rate = _profile_hit_rate(evidence)
    settled_bets = int(evidence.get("settled_bets") or 0)
    if hit_rate is None:
        issues.append("world_cup_backtest_missing_hit_rate")
    elif hit_rate < 0.65:
        issues.append(f"world_cup_backtest_hit_rate:{hit_rate:.3f}/0.650")
    if settled_bets < 80:
        issues.append(f"world_cup_backtest_sample:{settled_bets}/80")
    if str(evidence.get("sample_scope") or ""):
        scope = _sample_scope(evidence)
        if "WORLD_CUP" not in {item.upper() for item in scope}:
            issues.append("world_cup_backtest_scope_missing_world_cup")
    return issues


def _profile_hit_rate(evidence: dict[str, Any]) -> float | None:
    hit_rate = _safe_float(evidence.get("hit_rate"))
    if hit_rate is not None:
        return hit_rate
    win_rate = _safe_float(evidence.get("win_rate"))
    if win_rate is not None:
        return win_rate
    return _safe_float(evidence.get("holdout_positive_rate"))


def _world_cup_gate(
    stage: str,
    passed: bool,
    backtest: WorldCupHistoricalBacktestReport,
    provider_status: dict[str, Any],
    issues: list[str],
    tier: str | None = None,
    ignore_final_window: bool = False,
    qqsd_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "profile_id": WORLD_CUP_PROFILE_ID,
        "stage": stage,
        "passed": passed,
        "tier": tier,
        "ignore_final_window": ignore_final_window,
        "minimum_hit_rate": backtest.minimum_hit_rate,
        "hit_rate": backtest.hit_rate,
        "settled_bets": backtest.settled_bets,
        "research_provider": provider_status,
        "qqsd_data": qqsd_data or {},
        "issues": _dedupe(issues),
    }


def _world_cup_gate_payload(recommendation: Recommendation) -> dict[str, Any]:
    payload = (
        recommendation.score_breakdown.get("world_cup_high_winrate")
        or recommendation.odds_basis.get("world_cup_high_winrate")
        or {}
    )
    return payload if isinstance(payload, dict) else {}


def _research_provider_status(provider: str) -> dict[str, Any]:
    normalized = provider.lower().strip()
    checked = list(WORLD_CUP_RESEARCH_PROVIDERS) if normalized == "auto" else [normalized]
    for name in checked:
        env_name = _research_provider_env(name)
        if env_name and os.getenv(env_name):
            return {
                "requested": provider,
                "selected": name,
                "available": True,
                "required_env": env_name,
            }
    expected = [_research_provider_env(name) for name in checked if _research_provider_env(name)]
    return {
        "requested": provider,
        "selected": None,
        "available": False,
        "required_env": expected,
        "issue": "missing_research_provider_env:" + "|".join(expected or ["EXA_API_KEY", "FIRECRAWL_API_KEY"]),
    }


def _research_provider_env(provider: str) -> str | None:
    return {
        "exa": "EXA_API_KEY",
        "firecrawl": "FIRECRAWL_API_KEY",
        "tavily": "TAVILY_API_KEY",
    }.get(provider)


def _credential_present(settings: Settings, source_id: str) -> bool:
    source = settings.data_sources.get(source_id)
    if source is None or not source.api_key_env:
        return True
    return bool(os.getenv(source.api_key_env))


def _minutes_to_kickoff(match: Match, now: datetime, settings: Settings) -> float | None:
    kickoff = match.kickoff_at.astimezone(settings.app.tzinfo)
    return (kickoff - now.astimezone(settings.app.tzinfo)).total_seconds() / 60.0


def _advisory_window_issues(match: Match, now: datetime, settings: Settings) -> list[str]:
    kickoff_minutes = _minutes_to_kickoff(match, now, settings)
    if (
        kickoff_minutes is not None
        and ADVISORY_WINDOW_MIN_MINUTES <= kickoff_minutes <= ADVISORY_WINDOW_MAX_MINUTES
    ):
        return []
    return [
        f"world_cup_advisory_window:{match.id}:"
        f"{'unknown' if kickoff_minutes is None else int(kickoff_minutes)}m"
    ]


def _fresh_1x2_bookmaker_count(
    odds: list[OddsSnapshot],
    recommendation: Recommendation,
    settings: Settings,
    now: datetime,
) -> int:
    selection = recommendation.selection or ""
    bookmakers = {
        snapshot.bookmaker
        for snapshot in odds
        if snapshot.market_type.value == "1x2"
        and selection in snapshot.outcome_odds
        and _odds_age_minutes(snapshot, now) <= settings.live_trading.max_odds_age_minutes
    }
    return len(bookmakers)


def _odds_age_minutes(snapshot: OddsSnapshot, now: datetime) -> float:
    collected = snapshot.collected_at
    if collected.tzinfo is None:
        collected = collected.replace(tzinfo=now.tzinfo)
    return max(0.0, (now - collected.astimezone(now.tzinfo)).total_seconds() / 60.0)


def _cross_checked_research_sources(findings: list[AgentFinding]) -> int:
    publishers: set[str] = set()
    for finding in findings:
        for source in finding.evidence_sources:
            publishers.add((source.publisher or source.url or source.title).strip().lower())
    return len({publisher for publisher in publishers if publisher})


def _has_lineup_or_injury_context(findings: list[AgentFinding]) -> bool:
    needles = ("lineup", "starting", "injury", "injuries", "team news", "suspended", "阵容", "伤停", "首发")
    for finding in findings:
        payload_text = " ".join(str(value) for value in finding.payload.values()).lower()
        text = f"{finding.summary} {payload_text}".lower()
        if any(needle in text for needle in needles):
            return True
    return False


def _planned_world_cup_stake_for_day(service: AnalysisService, match: Match) -> float:
    local_date = match.kickoff_at.astimezone(service.settings.app.tzinfo).date()
    total = 0.0
    for recommendation in service.repository.list_models("recommendations", Recommendation):
        if recommendation.id == "":
            continue
        stored_match = service.repository.get_model("matches", recommendation.match_id, Match)
        if stored_match is None or not _is_world_cup_match(stored_match, service.settings):
            continue
        if stored_match.kickoff_at.astimezone(service.settings.app.tzinfo).date() != local_date:
            continue
        if recommendation.status is RecommendationStatus.recommended:
            total += float(recommendation.stake_units)
    return total


def _world_cup_tier(recommendation: Recommendation) -> str:
    return "A" if recommendation.confidence >= 0.76 and recommendation.value_score >= 78 else "B"


def _sample_scope(evidence: dict[str, Any]) -> list[str]:
    value = evidence.get("sample_scope") or evidence.get("sample_leagues")
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return _default_sample_scope()


def _default_sample_scope() -> list[str]:
    return ["WORLD_CUP", "EURO", "COPA_AMERICA", "ASIAN_CUP", "AFCON"]


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
