from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

import yaml
from dotenv import load_dotenv
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import Field

from football_analysis.contracts import HistoricalMatchRow
from football_analysis.datasources.base import ClientContext
from football_analysis.datasources.the_odds_api import TheOddsApiClient, sport_key_for_league
from football_analysis.daily_ops import run_daily_ops
from football_analysis.datasources.football_data_uk import FootballDataUkClient
from football_analysis.http_client import ProviderHttpClient
from football_analysis.live_audit import audit_live_trading
from football_analysis.live_decision import run_live_decision
from football_analysis.live_refresh import run_live_refresh
from football_analysis.models import (
    AppModel,
    BetLog,
    IngestionResult,
    JobRun,
    JobStatus,
    Match,
    OddsSnapshot,
    Recommendation,
    RecommendationStatus,
)
from football_analysis.odds_readiness import audit_odds_readiness
from football_analysis.scoring import _normalized_strategy_selection
from football_analysis.service import AnalysisService, _approved_odds, _is_paper_platform


class ProductionCycleReport(AppModel):
    checked_at: datetime = Field(default_factory=datetime.utcnow)
    date: str
    status: str
    action: str
    ready_to_bet: bool
    leagues: list[str]
    fixture_source: str
    odds_source: str
    result_source: str
    refresh_mode: str = "fixed"
    refresh_dry_run: bool = False
    fixture_results: list[IngestionResult] = Field(default_factory=list)
    odds_results: list[IngestionResult] = Field(default_factory=list)
    result_results: list[IngestionResult] = Field(default_factory=list)
    refresh: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    daily_ops: Any | None = None
    analysis_advice: dict[str, Any] | None = None
    data_apply: dict[str, Any] | None = None
    execution_queue: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    broker_discovery: dict[str, Any] | None = None
    broker_execution: dict[str, Any] | None = None
    issues: list[str] = Field(default_factory=list)


class AnalysisAdviceReport(AppModel):
    checked_at: datetime = Field(default_factory=datetime.utcnow)
    status: str
    message: str
    pick_count: int
    analysis_count: int
    items: list[dict[str, Any]] = Field(default_factory=list)
    risk_notice: str | None = None


class TelegramAlertResult(AppModel):
    enabled: bool
    sent: bool
    status_code: int | None = None
    skipped_reason: str | None = None
    error: str | None = None

class ProductionStatusReport(AppModel):
    checked_at: datetime = Field(default_factory=datetime.utcnow)
    overall_status: str
    ready_to_bet: bool
    action: str
    decision: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    recent_jobs: list[dict[str, Any]] = Field(default_factory=list)
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)


DecisionRunner = Callable[..., Any]
DailyOpsRunner = Callable[[AnalysisService, date], Any]
RefreshRunner = Callable[..., Any]
ExecutionRunner = Callable[..., dict[str, Any]]
DataApplyRunner = Callable[..., dict[str, Any]]
BrokerDiscoveryRunner = Callable[..., dict[str, Any]]
BrokerExecutionRunner = Callable[..., dict[str, Any]]


def run_production_cycle(
    service: AnalysisService,
    run_date: date,
    leagues: Iterable[str],
    fixture_source: str = "api_football",
    odds_source: str = "odds_api_io",
    result_source: str = "api_football",
    max_events: int | None = None,
    include_results: bool = True,
    include_daily_ops: bool = True,
    include_past: bool = False,
    decision_runner: DecisionRunner | None = None,
    daily_ops_runner: DailyOpsRunner | None = None,
    auto_refresh: bool = False,
    refresh_scope: str = "active-profiles",
    allow_odds_fallback: bool = False,
    expand_live_leagues_on_empty: bool = False,
    refresh_runner: RefreshRunner | None = None,
    refresh_dry_run: bool = False,
    execution_mode: str = "dry-run",
    execution_platform: str = "real",
    execution_max_items: int | None = None,
    execution_fills: dict[str, Any] | None = None,
    require_execution_fills: bool = False,
    execution_runner: ExecutionRunner | None = None,
    data_apply_mode: str = "off",
    data_apply_include_backtests: bool = True,
    data_apply_include_blocked_prerequisites: bool = False,
    data_apply_max_commands: int | None = None,
    data_apply_timeout_seconds: int = 1800,
    data_apply_historical_odds_start_time: str | None = None,
    data_apply_historical_odds_end_time: str | None = None,
    data_apply_historical_odds_interval_minutes: int = 10,
    data_apply_historical_odds_max_snapshots: int = 24,
    data_apply_historical_odds_max_events: int | None = None,
    data_apply_runner: DataApplyRunner | None = None,
    broker_id: str = "betfair_exchange",
    broker_discovery_mode: str = "off",
    broker_discovery_max_items: int | None = None,
    broker_discovery_max_results: int = 20,
    broker_discovery_match_window_hours: int = 36,
    broker_discovery_runner: BrokerDiscoveryRunner | None = None,
    broker_execution_mode: str = "off",
    broker_execution_max_items: int | None = None,
    broker_execution_runner: BrokerExecutionRunner | None = None,
) -> ProductionCycleReport:
    day = run_date.isoformat()
    target_leagues = list(leagues)
    fixture_results: list[IngestionResult] = []
    odds_results: list[IngestionResult] = []
    result_results: list[IngestionResult] = []
    issues: list[str] = []
    refresh_reports: list[Any] = []

    if refresh_dry_run:
        issues.append("refresh_dry_run_enabled")

    if auto_refresh:
        refresh_fn = refresh_runner or _default_refresh_runner
        refresh_reports = _run_auto_refresh_reports(
            refresh_fn=refresh_fn,
            service=service,
            day=day,
            leagues=target_leagues,
            fixture_source=fixture_source,
            odds_source=odds_source,
            refresh_scope=refresh_scope,
            max_events=max_events,
            include_past=include_past,
            allow_odds_fallback=allow_odds_fallback,
            expand_live_leagues_on_empty=expand_live_leagues_on_empty,
            refresh_dry_run=refresh_dry_run,
        )
        target_leagues = _refresh_target_leagues(refresh_reports)
        for refresh_report in refresh_reports:
            fixture_results.extend(getattr(refresh_report, "fixture_results", []) or [])
            odds_results.extend(getattr(refresh_report, "odds_results", []) or [])
            issues.extend(f"refresh:{issue}" for issue in getattr(refresh_report, "issues", []) or [])

    if not auto_refresh and not refresh_dry_run:
        for league in target_leagues:
            fixture_result = service.ingestion.ingest_fixtures(
                date=day,
                source=fixture_source,
                league_code=league,
            )
            fixture_results.append(fixture_result)
            issues.extend(f"fixtures:{league}:{error}" for error in fixture_result.errors)

            odds_result = service.ingestion.ingest_odds(
                date=day,
                source=odds_source,
                league_code=league,
                max_events=max_events,
            )
            odds_results.append(odds_result)
            issues.extend(f"odds:{league}:{error}" for error in odds_result.errors)

            if include_results:
                result = service.ingestion.ingest_results(
                    date=day,
                    source=result_source,
                    league_code=league,
                )
                result_results.append(result)
                issues.extend(f"results:{league}:{error}" for error in result.errors)

    if auto_refresh and include_results and not refresh_dry_run:
        for league in target_leagues:
            result = service.ingestion.ingest_results(
                date=day,
                source=result_source,
                league_code=league,
            )
            result_results.append(result)
            issues.extend(f"results:{league}:{error}" for error in result.errors)

    decision_fn = decision_runner or _default_decision_runner
    decision = decision_fn(service, include_past=include_past)
    status = getattr(decision, "status", "unknown")
    action = getattr(decision, "action", "unknown")
    ready_to_bet = bool(getattr(decision, "ready_to_bet", False))
    issues.extend(f"decision:{issue}" for issue in getattr(decision, "issues", []))
    if refresh_dry_run:
        status = "planned"
        action = "refresh_dry_run"
        ready_to_bet = False

    daily_ops_result = None
    if include_daily_ops:
        ops_fn = daily_ops_runner or _default_daily_ops_runner
        daily_ops_result = ops_fn(service, run_date)
        issues.extend(f"daily_ops:{issue}" for issue in _extract_issues(daily_ops_result))

    analysis_advice = None
    if hasattr(service, "picks_today"):
        analysis_advice = build_analysis_advice_report(service).model_dump(mode="json")

    if any(result.errors for result in [*fixture_results, *odds_results, *result_results]):
        status = "error"

    execution_queue = None
    execution = None
    data_apply = None
    broker_discovery = None
    broker_execution = None
    normalized_execution_mode = _normalize_execution_mode(execution_mode)
    normalized_data_apply_mode = _normalize_data_apply_mode(data_apply_mode)
    normalized_broker_discovery_mode = _normalize_broker_discovery_mode(broker_discovery_mode)
    normalized_broker_execution_mode = _normalize_broker_execution_mode(broker_execution_mode)
    not_ready_reason = "refresh_dry_run" if refresh_dry_run else "decision_not_ready"
    if hasattr(service, "repository") and hasattr(service, "settings"):
        if normalized_data_apply_mode != "off":
            data_apply_fn = data_apply_runner or build_production_data_apply
            data_apply = data_apply_fn(
                service,
                include_past=include_past,
                execute=normalized_data_apply_mode in {"safe", "remote"},
                allow_remote=normalized_data_apply_mode == "remote",
                include_backtests=data_apply_include_backtests,
                include_blocked_prerequisites=data_apply_include_blocked_prerequisites,
                max_commands=data_apply_max_commands,
                timeout_seconds=data_apply_timeout_seconds,
                historical_odds_start_time=data_apply_historical_odds_start_time,
                historical_odds_end_time=data_apply_historical_odds_end_time,
                historical_odds_interval_minutes=data_apply_historical_odds_interval_minutes,
                historical_odds_max_snapshots=data_apply_historical_odds_max_snapshots,
                historical_odds_max_events=data_apply_historical_odds_max_events,
            )
        if ready_to_bet and normalized_execution_mode == "off":
            execution_queue = build_production_execution_queue(
                service,
                include_past=include_past,
                platform=execution_platform,
            )
        elif ready_to_bet:
            execution_fn = execution_runner or run_production_execution
            execution = execution_fn(
                service,
                include_past=include_past,
                platform=execution_platform,
                execute_records=normalized_execution_mode == "record-only",
                max_items=execution_max_items,
                fills=execution_fills,
                require_fills=require_execution_fills,
            )
            execution_queue = execution.get("queue")
        elif normalized_execution_mode != "off":
            execution = {
                "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
                "status": "skipped",
                "mode": normalized_execution_mode,
                "execute_records": False,
                "platform": execution_platform,
                "reason": not_ready_reason,
                "queue_count": 0,
                "selected_count": 0,
                "recorded_count": 0,
                "dry_run_count": 0,
                "error_count": 0,
                "issues": [],
                "records": [],
            }
        if ready_to_bet and normalized_broker_discovery_mode != "off":
            broker_discovery_fn = broker_discovery_runner or run_production_broker_discovery
            broker_discovery = broker_discovery_fn(
                service,
                broker_id=broker_id,
                include_past=include_past,
                platform=execution_platform,
                fetch_remote=normalized_broker_discovery_mode in {"remote", "apply"},
                apply_mappings=normalized_broker_discovery_mode == "apply",
                max_items=broker_discovery_max_items,
                max_results=broker_discovery_max_results,
                match_window_hours=broker_discovery_match_window_hours,
            )
        elif normalized_broker_discovery_mode != "off":
            broker_discovery = {
                "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
                "status": "skipped",
                "mode": normalized_broker_discovery_mode,
                "fetch_remote": False,
                "broker_id": broker_id,
                "reason": not_ready_reason,
                "selected_count": 0,
                "discovered_count": 0,
                "dry_run_count": 0,
                "error_count": 0,
                "issues": [],
                "records": [],
                "applied_mappings": [],
            }
        if ready_to_bet and normalized_broker_execution_mode != "off":
            broker_execution_fn = broker_execution_runner or run_production_broker_execution
            broker_execution = broker_execution_fn(
                service,
                broker_id=broker_id,
                include_past=include_past,
                platform=execution_platform,
                execute_broker_orders=normalized_broker_execution_mode == "live",
                max_items=broker_execution_max_items,
            )
        elif normalized_broker_execution_mode != "off":
            broker_execution = {
                "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
                "status": "skipped",
                "mode": normalized_broker_execution_mode,
                "execute_broker_orders": False,
                "broker_id": broker_id,
                "reason": not_ready_reason,
                "selected_count": 0,
                "sent_count": 0,
                "dry_run_count": 0,
                "error_count": 0,
                "issues": [],
                "records": [],
            }

    status, action, ready_to_bet = _finalize_production_cycle_gate(
        status=status,
        action=action,
        ready_to_bet=ready_to_bet,
        issues=issues,
        execution_queue=execution_queue,
        execution=execution,
        broker_discovery=broker_discovery,
        broker_execution=broker_execution,
    )

    report = ProductionCycleReport(
        date=day,
        status=status,
        action=action,
        ready_to_bet=ready_to_bet,
        leagues=target_leagues,
        fixture_source=fixture_source,
        odds_source=odds_source,
        result_source=result_source,
        refresh_mode="auto" if auto_refresh else "fixed",
        refresh_dry_run=refresh_dry_run,
        fixture_results=fixture_results,
        odds_results=odds_results,
        result_results=result_results,
        refresh=_refresh_reports_summary(refresh_reports) if auto_refresh else None,
        decision=_decision_summary(decision),
        daily_ops=_daily_ops_summary(daily_ops_result),
        analysis_advice=analysis_advice,
        data_apply=data_apply,
        execution_queue=execution_queue,
        execution=execution,
        broker_discovery=broker_discovery,
        broker_execution=broker_execution,
        issues=issues,
    )
    _record_production_cycle_job(service, report)
    return report


def _finalize_production_cycle_gate(
    status: str,
    action: str,
    ready_to_bet: bool,
    issues: list[str],
    execution_queue: dict[str, Any] | None,
    execution: dict[str, Any] | None,
    broker_discovery: dict[str, Any] | None,
    broker_execution: dict[str, Any] | None,
) -> tuple[str, str, bool]:
    if status in {"error", "planned"} or ready_to_bet is not True:
        return status, action, ready_to_bet

    queue = execution_queue or {}
    if queue and queue.get("ready_to_execute") is False:
        queue_status = queue.get("status") or "unknown"
        issues.append(f"execution_queue_not_ready:{queue_status}")
        issues.extend(f"execution_queue:{issue}" for issue in queue.get("issues", []) or [])
        return "blocked", "review_execution_queue", False

    execution_status = (execution or {}).get("status")
    if execution_status in {"blocked", "failed", "error"}:
        issues.extend(f"execution:{issue}" for issue in (execution or {}).get("issues", []) or [])
        return "blocked", "review_execution", False

    broker_discovery_status = (broker_discovery or {}).get("status")
    if broker_discovery_status in {"blocked", "failed", "error"}:
        issues.extend(
            f"broker_discovery:{issue}" for issue in (broker_discovery or {}).get("issues", []) or []
        )
        return "blocked", "review_broker_discovery", False

    broker_execution_status = (broker_execution or {}).get("status")
    if broker_execution_status in {"blocked", "failed", "error"}:
        issues.extend(
            f"broker_execution:{issue}" for issue in (broker_execution or {}).get("issues", []) or []
        )
        return "blocked", "review_broker_execution", False

    return status, action, ready_to_bet


def run_production_worker(
    service_factory: Callable[[], AnalysisService],
    leagues: Iterable[str],
    fixture_source: str = "api_football",
    odds_source: str = "odds_api_io",
    result_source: str = "api_football",
    max_events: int | None = None,
    interval_seconds: int = 900,
    once: bool = False,
    include_results: bool = True,
    include_daily_ops: bool = True,
    include_past: bool = False,
    on_report: Callable[[ProductionCycleReport], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    decision_runner: DecisionRunner | None = None,
    daily_ops_runner: DailyOpsRunner | None = None,
    auto_refresh: bool = False,
    refresh_scope: str = "active-profiles",
    allow_odds_fallback: bool = False,
    expand_live_leagues_on_empty: bool = False,
    refresh_runner: RefreshRunner | None = None,
    refresh_dry_run: bool = False,
    execution_mode: str = "dry-run",
    execution_platform: str = "real",
    execution_max_items: int | None = None,
    execution_fills: dict[str, Any] | None = None,
    require_execution_fills: bool = False,
    execution_runner: ExecutionRunner | None = None,
    data_apply_mode: str = "off",
    data_apply_include_backtests: bool = True,
    data_apply_include_blocked_prerequisites: bool = False,
    data_apply_max_commands: int | None = None,
    data_apply_timeout_seconds: int = 1800,
    data_apply_historical_odds_start_time: str | None = None,
    data_apply_historical_odds_end_time: str | None = None,
    data_apply_historical_odds_interval_minutes: int = 10,
    data_apply_historical_odds_max_snapshots: int = 24,
    data_apply_historical_odds_max_events: int | None = None,
    data_apply_runner: DataApplyRunner | None = None,
    broker_id: str = "betfair_exchange",
    broker_discovery_mode: str = "off",
    broker_discovery_max_items: int | None = None,
    broker_discovery_max_results: int = 20,
    broker_discovery_match_window_hours: int = 36,
    broker_discovery_runner: BrokerDiscoveryRunner | None = None,
    broker_execution_mode: str = "off",
    broker_execution_max_items: int | None = None,
    broker_execution_runner: BrokerExecutionRunner | None = None,
) -> list[ProductionCycleReport]:
    reports: list[ProductionCycleReport] = []
    while True:
        service = service_factory()
        report = run_production_cycle(
            service,
            run_date=datetime.now(service.settings.app.tzinfo).date(),
            leagues=leagues,
            fixture_source=fixture_source,
            odds_source=odds_source,
            result_source=result_source,
            max_events=max_events,
            include_results=include_results,
            include_daily_ops=include_daily_ops,
            include_past=include_past,
            decision_runner=decision_runner,
            daily_ops_runner=daily_ops_runner,
            auto_refresh=auto_refresh,
            refresh_scope=refresh_scope,
            allow_odds_fallback=allow_odds_fallback,
            expand_live_leagues_on_empty=expand_live_leagues_on_empty,
            refresh_runner=refresh_runner,
            refresh_dry_run=refresh_dry_run,
            execution_mode=execution_mode,
            execution_platform=execution_platform,
            execution_max_items=execution_max_items,
            execution_fills=execution_fills,
            require_execution_fills=require_execution_fills,
            execution_runner=execution_runner,
            data_apply_mode=data_apply_mode,
            data_apply_include_backtests=data_apply_include_backtests,
            data_apply_include_blocked_prerequisites=data_apply_include_blocked_prerequisites,
            data_apply_max_commands=data_apply_max_commands,
            data_apply_timeout_seconds=data_apply_timeout_seconds,
            data_apply_historical_odds_start_time=data_apply_historical_odds_start_time,
            data_apply_historical_odds_end_time=data_apply_historical_odds_end_time,
            data_apply_historical_odds_interval_minutes=data_apply_historical_odds_interval_minutes,
            data_apply_historical_odds_max_snapshots=data_apply_historical_odds_max_snapshots,
            data_apply_historical_odds_max_events=data_apply_historical_odds_max_events,
            data_apply_runner=data_apply_runner,
            broker_id=broker_id,
            broker_discovery_mode=broker_discovery_mode,
            broker_discovery_max_items=broker_discovery_max_items,
            broker_discovery_max_results=broker_discovery_max_results,
            broker_discovery_match_window_hours=broker_discovery_match_window_hours,
            broker_discovery_runner=broker_discovery_runner,
            broker_execution_mode=broker_execution_mode,
            broker_execution_max_items=broker_execution_max_items,
            broker_execution_runner=broker_execution_runner,
        )
        reports.append(report)
        if on_report is not None:
            on_report(report)
        if once:
            return reports
        sleep_fn(interval_seconds)


def _record_production_cycle_job(service: AnalysisService, report: ProductionCycleReport) -> None:
    repository = getattr(service, "repository", None)
    if repository is None or not hasattr(repository, "upsert_model"):
        return
    try:
        job = JobRun(
            id=f"production_cycle:{report.checked_at.isoformat()}",
            job_type="production_cycle",
            status=_production_cycle_job_status(report),
            source="production",
            started_at=report.checked_at,
            finished_at=datetime.utcnow(),
            summary=_production_cycle_job_summary(report),
            error=";".join(report.issues) if report.status == "error" and report.issues else None,
        )
        repository.upsert_model("jobs", job.id, job)
    except Exception:
        return


def _production_cycle_job_status(report: ProductionCycleReport) -> JobStatus:
    execution = report.execution or {}
    if report.status == "error":
        return JobStatus.failed
    if execution.get("error_count", 0):
        return JobStatus.partial
    if report.status == "ready" or report.ready_to_bet is True:
        return JobStatus.succeeded
    if report.issues or report.ready_to_bet is not True:
        return JobStatus.partial
    return JobStatus.succeeded


def _production_cycle_partial_is_valid_heartbeat(job: JobRun) -> bool:
    if job.job_type != "production_cycle":
        return False
    status = getattr(job.status, "value", str(job.status))
    if status != JobStatus.partial.value:
        return False
    summary = _dict_payload(job.summary)
    return summary.get("status") in {"blocked", "planned"} and summary.get("action") in {
        "review_execution_queue",
        "review_execution",
        "review_broker_discovery",
        "review_broker_execution",
        "refresh_dry_run",
    }


def _production_cycle_job_summary(report: ProductionCycleReport) -> dict[str, Any]:
    queue = report.execution_queue or {}
    execution = report.execution or {}
    data_apply = report.data_apply or {}
    analysis_advice = report.analysis_advice or {}
    broker_discovery = report.broker_discovery or {}
    broker_execution = report.broker_execution or {}
    return {
        "date": report.date,
        "status": report.status,
        "action": report.action,
        "ready_to_bet": report.ready_to_bet,
        "refresh_mode": report.refresh_mode,
        "refresh_dry_run": report.refresh_dry_run,
        "leagues": list(report.leagues),
        "fixture_count": _result_count(report.fixture_results),
        "odds_count": _result_count(report.odds_results),
        "result_count": _result_count(report.result_results),
        "decision_status": (report.decision or {}).get("status"),
        "analysis_advice_status": analysis_advice.get("status"),
        "analysis_advice_pick_count": analysis_advice.get("pick_count", 0),
        "analysis_advice_analysis_count": analysis_advice.get("analysis_count", 0),
        "data_apply_status": data_apply.get("status"),
        "data_apply_execute": data_apply.get("execute"),
        "data_apply_allow_remote": data_apply.get("allow_remote"),
        "data_apply_selected_count": data_apply.get("selected_count", 0),
        "data_apply_succeeded_count": data_apply.get("succeeded_count", 0),
        "data_apply_failed_count": data_apply.get("failed_count", 0),
        "execution_mode": execution.get("mode"),
        "execution_status": execution.get("status"),
        "execution_selected_count": execution.get("selected_count", 0),
        "execution_recorded_count": execution.get("recorded_count", 0),
        "execution_dry_run_count": execution.get("dry_run_count", 0),
        "execution_error_count": execution.get("error_count", 0),
        "broker_discovery_mode": broker_discovery.get("mode"),
        "broker_discovery_status": broker_discovery.get("status"),
        "broker_discovery_selected_count": broker_discovery.get("selected_count", 0),
        "broker_discovery_discovered_count": broker_discovery.get("discovered_count", 0),
        "broker_discovery_error_count": broker_discovery.get("error_count", 0),
        "broker_execution_mode": broker_execution.get("mode"),
        "broker_execution_status": broker_execution.get("status"),
        "broker_execution_selected_count": broker_execution.get("selected_count", 0),
        "broker_execution_sent_count": broker_execution.get("sent_count", 0),
        "broker_execution_dry_run_count": broker_execution.get("dry_run_count", 0),
        "broker_execution_error_count": broker_execution.get("error_count", 0),
        "queue_status": queue.get("status"),
        "queue_count": queue.get("queue_count", 0),
        "queue_stake_units": queue.get("queue_stake_units", 0.0),
        "issue_count": len(report.issues),
    }


def build_production_cycle_log(report: ProductionCycleReport, max_issues: int = 40) -> dict[str, Any]:
    """Return a compact, line-log-safe production cycle payload."""
    summary = _production_cycle_job_summary(report)
    payload: dict[str, Any] = {
        "type": "production_cycle",
        "checked_at": report.checked_at.isoformat(),
        "summary": summary,
        "decision": _compact_stage(report.decision, ["status", "ready_to_bet", "action", "components"]),
        "analysis_advice": _compact_stage(
            report.analysis_advice,
            ["status", "message", "pick_count", "analysis_count", "items"],
        ),
        "refresh": _compact_stage(
            report.refresh,
            ["status", "scope", "target_leagues", "fixture_result_count", "odds_result_count", "issue_count"],
        ),
        "daily_ops": _compact_stage(report.daily_ops, ["status", "action", "issue_count"]),
        "data_apply": _compact_stage(
            report.data_apply,
            [
                "status",
                "execute",
                "allow_remote",
                "selected_count",
                "succeeded_count",
                "failed_count",
                "skipped_count",
            ],
        ),
        "execution_queue": _compact_stage(
            report.execution_queue,
            [
                "status",
                "ready_to_execute",
                "queue_count",
                "queue_stake_units",
                "candidate_count",
                "profileless_candidate_count",
                "profileless_candidates",
                "issues",
            ],
        ),
        "execution": _compact_stage(
            report.execution,
            [
                "status",
                "mode",
                "queue_status",
                "selected_count",
                "recorded_count",
                "dry_run_count",
                "error_count",
                "issues",
            ],
        ),
        "broker_discovery": _compact_stage(
            report.broker_discovery,
            ["status", "mode", "selected_count", "discovered_count", "error_count", "issues"],
        ),
        "broker_execution": _compact_stage(
            report.broker_execution,
            ["status", "mode", "selected_count", "sent_count", "dry_run_count", "error_count", "issues"],
        ),
        "issue_count": len(report.issues),
        "issues": list(report.issues[:max_issues]),
    }
    if len(report.issues) > max_issues:
        payload["issues_truncated"] = len(report.issues) - max_issues
    return payload


def build_analysis_advice_report(
    service: AnalysisService,
    limit: int | None = None,
    hours: int = 24,
) -> AnalysisAdviceReport:
    pick_list = service.picks_upcoming(hours=hours)
    analyses_by_match = {analysis.match.id: analysis for analysis in pick_list.analyses}
    max_items = service.settings.app.daily_pick_limit if limit is None else max(0, limit)
    items = [
        _analysis_advice_item(recommendation, analyses_by_match)
        for recommendation in pick_list.picks[:max_items]
    ]
    return AnalysisAdviceReport(
        status="ready" if items else "no_recommendation",
        message=pick_list.message,
        pick_count=len(pick_list.picks),
        analysis_count=len(pick_list.analyses),
        items=items,
        risk_notice=service.settings.app.risk_notice,
    )


def _analysis_advice_item(
    recommendation: Recommendation,
    analyses_by_match: dict[str, Any],
) -> dict[str, Any]:
    analysis = analyses_by_match.get(recommendation.match_id)
    match = getattr(analysis, "match", None)
    odds = _approved_odds(recommendation)
    research_advisory = (
        recommendation.score_breakdown.get("research_advisory")
        or recommendation.odds_basis.get("research_advisory")
        or {}
    )
    evidence_sources: list[dict[str, Any]] = []
    for finding in getattr(analysis, "findings", []) or []:
        if research_advisory.get("finding_id") and finding.id != research_advisory.get("finding_id"):
            continue
        for source in finding.evidence_sources:
            evidence_sources.append(source.model_dump(mode="json"))
    return {
        "recommendation_id": recommendation.id,
        "match_id": recommendation.match_id,
        "league": getattr(match, "league", None),
        "home_team": getattr(match, "home_team", None),
        "away_team": getattr(match, "away_team", None),
        "kickoff_at": getattr(getattr(match, "kickoff_at", None), "isoformat", lambda: None)(),
        "market_type": _market_type_value(recommendation.market_type),
        "selection": recommendation.selection,
        "status": _status_value(recommendation.status),
        "odds": round(odds, 3) if odds is not None else None,
        "bookmaker": recommendation.odds_basis.get("bookmaker"),
        "source": recommendation.odds_basis.get("source"),
        "edge": recommendation.odds_basis.get("edge"),
        "value_score": recommendation.value_score,
        "risk_score": recommendation.risk_score,
        "confidence": recommendation.confidence,
        "stake_units": recommendation.stake_units,
        "strategy_confidence_class": (
            recommendation.score_breakdown.get("strategy_confidence_class")
            or recommendation.odds_basis.get("strategy_confidence_class")
        ),
        "research_advisory": research_advisory,
        "evidence_sources": evidence_sources,
        "risk_tags": list(recommendation.risk_tags),
        "reason": recommendation.reason,
    }


def format_analysis_advice_alert(report: AnalysisAdviceReport | dict[str, Any], max_items: int = 5) -> str:
    payload = report.model_dump(mode="json") if hasattr(report, "model_dump") else dict(report)
    items = list(payload.get("items") or [])[:max_items]
    lines = [
        "football-analysis advice",
        f"status={payload.get('status')}",
        f"message={payload.get('message')}",
        f"pick_count={payload.get('pick_count', 0)}",
        f"analysis_count={payload.get('analysis_count', 0)}",
    ]
    if not items:
        lines.append("advice=none")
    for index, item in enumerate(items, start=1):
        home = item.get("home_team") or "Unknown Home"
        away = item.get("away_team") or "Unknown Away"
        market = item.get("market_type") or "-"
        selection = item.get("selection") or "-"
        odds = item.get("odds")
        stake = item.get("stake_units", 0)
        confidence = item.get("confidence", 0)
        value_score = item.get("value_score", 0)
        risk_score = item.get("risk_score", 0)
        lines.append(
            f"{index}. {home} vs {away} | {market} {selection} | "
            f"odds={odds if odds is not None else '-'} | stake={stake}u | "
            f"confidence={confidence} | value={value_score} | risk={risk_score}"
        )
        reason = str(item.get("reason") or "").strip()
        if reason:
            lines.append(f"   reason={reason[:220]}")
    risk_notice = payload.get("risk_notice")
    if risk_notice:
        lines.append(f"risk_notice={risk_notice}")
    lines.append("execution=analysis_only_no_broker_orders")
    return "\n".join(lines)


def _compact_stage(stage: Any, keys: list[str]) -> dict[str, Any] | None:
    if stage is None:
        return None
    if hasattr(stage, "model_dump"):
        stage = stage.model_dump(mode="json")
    if not isinstance(stage, dict):
        return None
    return {key: stage[key] for key in keys if key in stage}


def build_production_status(
    service: AnalysisService,
    recent_limit: int = 10,
    include_past: bool = False,
    decision_runner: DecisionRunner | None = None,
    required_job_types: Iterable[str] = ("production_cycle", "ingest_fixtures", "ingest_odds"),
    execution_queue_runner: Callable[..., Any] | None = None,
    league_codes: set[str] | None = None,
) -> dict[str, Any]:
    repository = service.repository
    decision_fn = decision_runner or _default_decision_runner
    decision_kwargs: dict[str, Any] = {"include_past": include_past}
    if league_codes and _callable_accepts_keyword(decision_fn, "league_codes"):
        decision_kwargs["league_codes"] = league_codes
    if league_codes and _callable_accepts_keyword(decision_fn, "require_strategy_profiles"):
        decision_kwargs["require_strategy_profiles"] = False
    decision = decision_fn(service, **decision_kwargs)
    decision_summary = _decision_summary(decision)
    execution_queue_summary = _production_execution_queue_summary(
        service,
        include_past=include_past,
        execution_queue_runner=execution_queue_runner,
        league_codes=league_codes,
    )
    all_jobs = list(repository.list_models("jobs", JobRun))
    jobs = _recent_jobs(all_jobs, limit=recent_limit)
    monitoring_jobs = _monitoring_jobs(all_jobs)
    issues = _production_status_issues(monitoring_jobs, required_job_types)
    overall_status = decision_summary["status"]
    ready_to_bet = decision_summary["ready_to_bet"]
    action = decision_summary["action"]
    if execution_queue_summary["ready_to_execute"] is False:
        queue_status = execution_queue_summary["status"] or "unknown"
        issues.append(f"execution_queue_not_ready:{queue_status}")
        issues.extend(
            f"execution_queue:{issue}" for issue in execution_queue_summary["issues"]
        )
        if ready_to_bet is True:
            overall_status = "blocked"
            ready_to_bet = False
            action = "review_execution_queue"

    return {
        "checked_at": datetime.utcnow().isoformat(),
        "overall_status": overall_status,
        "ready_to_bet": ready_to_bet,
        "action": action,
        "league_codes": sorted(league_codes or []),
        "decision": decision_summary,
        "execution_queue": execution_queue_summary,
        "counts": {
            bucket: _safe_count(repository, bucket)
            for bucket in ("matches", "odds", "bets", "recommendations", "jobs")
        },
        "recent_jobs": [_job_summary(job) for job in jobs],
        "providers": {
            provider_id: _provider_status(repository, provider_id, source)
            for provider_id, source in service.settings.data_sources.items()
        },
        "odds_readiness": _odds_readiness_status(
            service,
            include_past=include_past,
            league_codes=league_codes,
        ),
        "production_readiness": build_production_readiness(
            service,
            include_past=include_past,
            league_codes=league_codes,
        ),
        "issues": sorted(set(issues)),
    }


def _production_execution_queue_summary(
    service: AnalysisService,
    include_past: bool = False,
    execution_queue_runner: Callable[..., Any] | None = None,
    league_codes: set[str] | None = None,
) -> dict[str, Any]:
    settings = getattr(service, "settings", None)
    if not hasattr(settings, "app"):
        return {
            "status": "not_checked",
            "ready_to_execute": None,
            "queue_count": 0,
            "queue_stake_units": 0,
            "candidate_count": 0,
            "profileless_candidate_count": 0,
            "league_codes": sorted(league_codes or []),
            "issues": [],
        }
    if execution_queue_runner is not None:
        queue_kwargs: dict[str, Any] = {"include_past": include_past}
        if league_codes and _callable_accepts_keyword(execution_queue_runner, "league_codes"):
            queue_kwargs["league_codes"] = league_codes
        execution_queue = execution_queue_runner(service, **queue_kwargs)
    else:
        execution_queue = build_production_execution_queue(
            service,
            include_past=include_past,
            league_codes=league_codes,
        )
    return {
        "status": execution_queue.get("status"),
        "ready_to_execute": execution_queue.get("ready_to_execute") is True,
        "queue_count": execution_queue.get("queue_count", 0),
        "queue_stake_units": execution_queue.get("queue_stake_units", 0),
        "candidate_count": execution_queue.get("candidate_count", 0),
        "profileless_candidate_count": execution_queue.get("profileless_candidate_count", 0),
        "profileless_candidates": execution_queue.get("profileless_candidates", []),
        "league_codes": execution_queue.get("league_codes", sorted(league_codes or [])),
        "issues": execution_queue.get("issues", []),
    }


def build_production_health(
    service: AnalysisService,
    include_past: bool = False,
    recent_limit: int = 10,
    max_cycle_age_minutes: int = 90,
    max_data_job_age_minutes: int = 180,
    decision_runner: DecisionRunner | None = None,
    execution_queue_runner: Callable[..., Any] | None = None,
    league_codes: set[str] | None = None,
) -> dict[str, Any]:
    status = build_production_status(
        service,
        recent_limit=recent_limit,
        include_past=include_past,
        decision_runner=decision_runner,
        execution_queue_runner=execution_queue_runner,
        league_codes=league_codes,
    )
    checked_at = datetime.utcnow()
    jobs = _monitoring_jobs(service.repository.list_models("jobs", JobRun))
    job_health = {
        "production_cycle": _job_health(
            jobs,
            "production_cycle",
            checked_at=checked_at,
            max_age_minutes=max_cycle_age_minutes,
        ),
        "ingest_fixtures": _job_health(
            jobs,
            "ingest_fixtures",
            checked_at=checked_at,
            max_age_minutes=max_data_job_age_minutes,
        ),
        "ingest_odds": _job_health(
            jobs,
            "ingest_odds",
            checked_at=checked_at,
            max_age_minutes=max_data_job_age_minutes,
        ),
    }
    counts = status.get("counts", {})
    issues = []
    warnings = []
    for issue in status.get("issues", []):
        if any(
            issue == f"latest_job_not_succeeded:{job_type}:started"
            and health["status"] == "running"
            for job_type, health in job_health.items()
        ):
            continue
        if issue.startswith("empty_recent_job:"):
            job_type = issue.removeprefix("empty_recent_job:")
            if _job_data_available(job_type, counts):
                warnings.append(issue)
                continue
        if issue.startswith("execution_queue"):
            warnings.append(issue)
            continue
        issues.append(issue)
    for job_type, health in job_health.items():
        if health["status"] not in {"ok", "running"}:
            if health["status"] == "empty" and _job_data_available(job_type, counts):
                health["status"] = "ok"
                health["empty_recent_jobs"] = True
                health["warning"] = "empty_recent_jobs_with_data_available"
                warnings.append(f"empty_recent_job:{job_type}")
                continue
            issues.append(f"{health['status']}_job:{job_type}")
    health_status = _production_health_status(status, job_health, issues)
    return {
        "checked_at": checked_at.isoformat(),
        "status": health_status,
        "ready_to_bet": status["ready_to_bet"],
        "action": status["action"],
        "overall_status": status["overall_status"],
        "league_codes": sorted(league_codes or []),
        "max_cycle_age_minutes": max_cycle_age_minutes,
        "max_data_job_age_minutes": max_data_job_age_minutes,
        "job_health": job_health,
        "issues": sorted(set(issues)),
        "warnings": sorted(set(warnings)),
        "production_status": {
            "overall_status": status["overall_status"],
            "ready_to_bet": status["ready_to_bet"],
            "action": status["action"],
            "league_codes": status.get("league_codes", []),
            "execution_queue": status.get("execution_queue", {}),
            "counts": status["counts"],
            "issues": status["issues"],
            "warnings": sorted(set(warnings)),
        },
    }


def _job_health(
    jobs: Iterable[JobRun],
    job_type: str,
    checked_at: datetime,
    max_age_minutes: int,
) -> dict[str, Any]:
    latest = _latest_jobs_by_type(jobs).get(job_type)
    if latest is None:
        return {
            "status": "missing",
            "job_type": job_type,
            "latest_job": None,
            "age_minutes": None,
            "max_age_minutes": max_age_minutes,
        }
    status = getattr(latest.status, "value", str(latest.status))
    age_minutes = _job_age_minutes(latest, checked_at)
    health_status = "ok"
    if status == "started":
        if age_minutes is not None and age_minutes > max_age_minutes:
            health_status = "stale"
        else:
            health_status = "running"
    elif status == "partial":
        if _production_cycle_partial_is_valid_heartbeat(latest):
            health_status = "ok"
        else:
            health_status = "degraded"
    elif status != "succeeded":
        health_status = "failed"
    elif age_minutes is not None and age_minutes > max_age_minutes:
        health_status = "stale"
    elif _recent_jobs_are_empty(jobs, job_type):
        health_status = "empty"
    return {
        "status": health_status,
        "job_type": job_type,
        "latest_job": _job_summary(latest),
        "age_minutes": age_minutes,
        "max_age_minutes": max_age_minutes,
    }


def _job_age_minutes(job: JobRun, checked_at: datetime) -> float | None:
    timestamp = job.finished_at or job.started_at
    if timestamp is None:
        return None
    current = _naive_utc(checked_at)
    job_time = _naive_utc(timestamp)
    return round(max((current - job_time).total_seconds(), 0.0) / 60.0, 3)


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _production_health_status(
    production_status: dict[str, Any],
    job_health: dict[str, dict[str, Any]],
    issues: list[str],
) -> str:
    hard_failures = {"missing", "failed", "stale"}
    if any(item["status"] in hard_failures for item in job_health.values()):
        return "unhealthy"
    if issues:
        return "degraded"
    if production_status.get("ready_to_bet") is not True:
        return "degraded"
    return "healthy"


def _job_data_available(job_type: str, counts: dict[str, Any]) -> bool:
    bucket = {
        "ingest_fixtures": "matches",
        "ingest_odds": "odds",
        "ingest_results": "matches",
    }.get(job_type)
    if bucket is None:
        return False
    try:
        return int(counts.get(bucket, 0)) > 0
    except (TypeError, ValueError):
        return False


def _production_worker_startup_health_ready(health: dict[str, Any]) -> bool:
    job_health = health.get("job_health", {})
    if not isinstance(job_health, dict) or not job_health:
        return False

    tolerated_statuses = {"ok", "running", "missing", "empty"}
    job_types = {str(job_type) for job_type in job_health}
    for item in job_health.values():
        if not isinstance(item, dict):
            return False
        if str(item.get("status")) not in tolerated_statuses:
            return False

    tolerated_issues = {
        f"{prefix}{job_type}"
        for job_type in job_types
        for prefix in (
            "missing_job:",
            "missing_recent_job:",
            "empty_job:",
            "empty_recent_job:",
        )
    }
    issues = {str(issue) for issue in health.get("issues", [])}
    return issues.issubset(tolerated_issues)


def build_production_readiness(
    service: AnalysisService,
    include_past: bool = False,
    league_codes: set[str] | None = None,
) -> dict[str, Any]:
    try:
        return _build_production_readiness(
            service,
            include_past=include_past,
            league_codes=league_codes,
        )
    except Exception as exc:
        return {
            "checked_at": datetime.utcnow().isoformat(),
            "status": "unavailable",
            "include_past": include_past,
            "league_codes": sorted(league_codes or []),
            "error": type(exc).__name__,
            "issues": ["production_readiness_unavailable"],
            "leagues": [],
        }


def build_production_data_plan(
    service: AnalysisService,
    include_past: bool = False,
    historical_odds_start_time: str | None = None,
    historical_odds_end_time: str | None = None,
    historical_odds_interval_minutes: int = 10,
    historical_odds_max_snapshots: int = 24,
    historical_odds_max_events: int | None = None,
    league_codes: set[str] | None = None,
) -> dict[str, Any]:
    readiness = build_production_readiness(
        service,
        include_past=include_past,
        league_codes=league_codes,
    )
    tasks: list[dict[str, Any]] = []
    historical_odds_options: dict[str, Any] | None = None
    if historical_odds_start_time or historical_odds_end_time:
        historical_odds_options = {
            "start_time": historical_odds_start_time or "",
            "end_time": historical_odds_end_time or "",
            "interval_minutes": historical_odds_interval_minutes,
            "max_snapshots": historical_odds_max_snapshots,
            "max_events": historical_odds_max_events,
        }
    for row in readiness.get("leagues", []):
        if row.get("status") not in {"blocked", "production_ready"}:
            continue
        tasks.extend(_data_plan_tasks_for_league(row, service.settings, historical_odds_options))

    user_actions = sorted(
        {
            action
            for task in tasks
            for action in task.get("user_actions", [])
            if action
        }
    )
    local_commands = [
        command
        for task in tasks
        for command in task.get("local_commands", [])
        if command
    ]
    return {
        "checked_at": datetime.utcnow().isoformat(),
        "status": "ready" if not tasks else "action_required",
        "include_past": include_past,
        "league_codes": sorted(league_codes or []),
        "readiness_status": readiness.get("status"),
        "task_count": len(tasks),
        "user_action_count": len(user_actions),
        "local_command_count": len(local_commands),
        "user_actions": user_actions,
        "local_commands": local_commands,
        "tasks": tasks,
    }


def build_production_data_apply(
    service: AnalysisService,
    include_past: bool = False,
    execute: bool = False,
    allow_remote: bool = False,
    include_backtests: bool = True,
    include_blocked_prerequisites: bool = False,
    max_commands: int | None = None,
    timeout_seconds: int = 1800,
    command_runner: Callable[[list[str], int], Any] | None = None,
    historical_odds_start_time: str | None = None,
    historical_odds_end_time: str | None = None,
    historical_odds_interval_minutes: int = 10,
    historical_odds_max_snapshots: int = 24,
    historical_odds_max_events: int | None = None,
) -> dict[str, Any]:
    plan = build_production_data_plan(
        service,
        include_past=include_past,
        historical_odds_start_time=historical_odds_start_time,
        historical_odds_end_time=historical_odds_end_time,
        historical_odds_interval_minutes=historical_odds_interval_minutes,
        historical_odds_max_snapshots=historical_odds_max_snapshots,
        historical_odds_max_events=historical_odds_max_events,
    )
    commands = _data_apply_commands_from_plan(
        plan,
        allow_remote=allow_remote,
        include_backtests=include_backtests,
        include_blocked_prerequisites=include_blocked_prerequisites,
        max_commands=max_commands,
    )
    selected = [command for command in commands if command["selected"]]
    command_summary = _data_apply_command_summary(commands)
    if not execute:
        return {
            "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
            "status": "dry_run",
            "execute": False,
            "allow_remote": allow_remote,
            "include_backtests": include_backtests,
            "include_blocked_prerequisites": include_blocked_prerequisites,
            "max_commands": max_commands,
            "timeout_seconds": timeout_seconds,
            "selected_count": len(selected),
            "skipped_count": len(commands) - len(selected),
            "succeeded_count": 0,
            "failed_count": 0,
            "plan_status": plan.get("status"),
            "command_summary": command_summary,
            "commands": commands,
        }

    runner = command_runner or _run_data_apply_command
    results: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0
    for command in commands:
        if not command["selected"]:
            results.append({**command, "status": "skipped"})
            continue
        argv = _data_apply_command_argv(command["command"])
        if argv is None:
            failed += 1
            results.append({**command, "status": "failed", "error": "unsupported_command"})
            continue
        try:
            completed = runner(argv, timeout_seconds)
        except Exception as exc:  # pragma: no cover - defensive for production runners
            failed += 1
            results.append({**command, "status": "failed", "argv": argv, "error": str(exc)})
            continue
        return_code = int(getattr(completed, "returncode", 1))
        stdout = getattr(completed, "stdout", "") or ""
        stderr = getattr(completed, "stderr", "") or ""
        status = "succeeded" if return_code == 0 else "failed"
        if return_code == 0:
            succeeded += 1
        else:
            failed += 1
        results.append(
            {
                **command,
                "status": status,
                "argv": argv,
                "return_code": return_code,
                "stdout_tail": _text_tail(stdout),
                "stderr_tail": _text_tail(stderr),
            }
        )

    if failed:
        status = "failed" if succeeded == 0 else "partial_failed"
    elif selected:
        status = "succeeded"
    else:
        status = "no_selected_commands"
    return {
        "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
        "status": status,
        "execute": True,
        "allow_remote": allow_remote,
        "include_backtests": include_backtests,
        "include_blocked_prerequisites": include_blocked_prerequisites,
        "max_commands": max_commands,
        "timeout_seconds": timeout_seconds,
        "selected_count": len(selected),
        "skipped_count": len(commands) - len(selected),
        "succeeded_count": succeeded,
        "failed_count": failed,
        "plan_status": plan.get("status"),
        "command_summary": _data_apply_command_summary(results),
        "commands": results,
    }


def build_the_odds_api_sports_report(
    service: AnalysisService,
    fetch_remote: bool = False,
) -> dict[str, Any]:
    source_id = "the_odds_api"
    source = service.settings.data_sources.get(source_id)
    configured_keys = dict(getattr(source, "sport_keys", {}) or {}) if source is not None else {}
    league_keys = []
    for league in service.settings.leagues:
        sport_key = (
            sport_key_for_league(league.code, getattr(league, "odds_api_slug", None), configured_keys)
            if source is not None
            else None
        )
        league_keys.append(
            {
                "league": league.code,
                "name": league.name,
                "configured_sport_key": sport_key,
                "configured_explicitly": league.code in configured_keys,
                "odds_api_slug": league.odds_api_slug,
                "live_candidate": league.strategy_mode == "live" and not league.paper_only,
            }
        )

    credential_env = getattr(source, "api_key_env", None) if source is not None else None
    credential_present = bool(credential_env and os.getenv(str(credential_env)))
    issues: list[str] = []
    sports: list[dict[str, Any]] = []
    missing_configured_keys: list[str] = []
    if source is None:
        issues.append("source_not_configured:the_odds_api")
    if source is not None and not getattr(source, "enabled", False):
        issues.append("source_disabled:the_odds_api")
    if source is not None and credential_env and not credential_present:
        issues.append(f"missing_credentials:{credential_env}")
    if not fetch_remote:
        status = "dry_run" if not issues else "action_required"
    elif issues:
        status = "blocked"
    else:
        client = TheOddsApiClient(
            ClientContext(
                provider=source_id,
                source=source,
                settings=service.settings,
                repository=service.repository,
                http=ProviderHttpClient(service.settings, service.repository),
            )
        )
        try:
            sports = client.sports(all_sports=True)
            available_keys = {item["key"] for item in sports}
            configured_nonempty = sorted({key for key in configured_keys.values() if key})
            missing_configured_keys = [key for key in configured_nonempty if key not in available_keys]
            issues.extend(f"sport_key_not_available:{key}" for key in missing_configured_keys)
            status = "ready" if not issues else "coverage_mismatch"
        except Exception as exc:
            issues.append(f"{type(exc).__name__}: {exc}")
            status = "error"

    available_keys = {item["key"] for item in sports}
    resolved_league_keys = [
        {
            **item,
            "available": item["configured_sport_key"] in available_keys if fetch_remote and sports else None,
        }
        for item in league_keys
    ]
    return {
        "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
        "status": status,
        "fetch_remote": fetch_remote,
        "source_id": source_id,
        "enabled": bool(getattr(source, "enabled", False)) if source is not None else False,
        "credential_env": credential_env,
        "credential_present": credential_present,
        "configured_sport_key_count": len([key for key in configured_keys.values() if key]),
        "remote_sport_count": len(sports),
        "missing_configured_keys": missing_configured_keys,
        "issues": sorted(set(issues)),
        "league_sport_keys": resolved_league_keys,
        "sports": sports,
    }


def build_production_historical_odds_plan(
    service: AnalysisService,
    leagues: Iterable[str],
    start_time: str,
    end_time: str,
    interval_minutes: int = 10,
    max_snapshots: int = 24,
    max_events: int | None = None,
    source_id: str = "the_odds_api",
) -> dict[str, Any]:
    if source_id != "the_odds_api":
        return {
            "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
            "status": "invalid",
            "source_id": source_id,
            "issues": [f"unsupported_historical_odds_source:{source_id}"],
            "leagues": [],
            "commands": [],
            "command_count": 0,
            "estimated_request_count": 0,
            "estimated_usage_credits": 0,
        }
    return _build_the_odds_api_historical_odds_plan(
        service.settings,
        leagues=leagues,
        start_time=start_time,
        end_time=end_time,
        interval_minutes=interval_minutes,
        max_snapshots=max_snapshots,
        max_events=max_events,
    )


def _build_the_odds_api_historical_odds_plan(
    settings: Any,
    leagues: Iterable[str],
    start_time: str,
    end_time: str,
    interval_minutes: int,
    max_snapshots: int,
    max_events: int | None,
) -> dict[str, Any]:
    source_id = "the_odds_api"
    source = settings.data_sources.get(source_id)
    credential_env = getattr(source, "api_key_env", None) if source is not None else None
    credential_present = bool(credential_env and os.getenv(str(credential_env)))
    source_issues: list[str] = []
    if source is None:
        source_issues.append("source_not_configured:the_odds_api")
    elif not getattr(source, "enabled", False):
        source_issues.append("source_disabled:the_odds_api")
    if source is not None and credential_env and not credential_present:
        source_issues.append(f"missing_credentials:{credential_env}")

    validation_issues: list[str] = []
    if interval_minutes <= 0:
        validation_issues.append("invalid_interval_minutes")
    if max_snapshots <= 0:
        validation_issues.append("invalid_max_snapshots")

    start_dt = _parse_historical_snapshot_time(start_time, "start_time", validation_issues)
    end_dt = _parse_historical_snapshot_time(end_time, "end_time", validation_issues)
    snapshot_times: list[datetime] = []
    truncated = False
    if start_dt is not None and end_dt is not None and interval_minutes > 0 and max_snapshots > 0:
        if start_dt > end_dt:
            validation_issues.append("start_time_after_end_time")
        else:
            snapshot_times, truncated = _historical_snapshot_times(
                start_dt,
                end_dt,
                interval_minutes=interval_minutes,
                max_snapshots=max_snapshots,
            )
            if not snapshot_times:
                validation_issues.append("empty_snapshot_window")

    regions = list(getattr(source, "regions", []) or ["uk", "eu"]) if source is not None else []
    markets = list(getattr(source, "markets", []) or ["h2h", "spreads", "totals"]) if source is not None else []
    per_snapshot_credit_cost = 10 * max(1, len(regions)) * max(1, len(markets))
    league_codes = [item.strip().upper() for item in leagues if item and item.strip()]
    if not league_codes:
        validation_issues.append("missing_league")

    plan_items: list[dict[str, Any]] = []
    commands: list[str] = []
    sport_keys = dict(getattr(source, "sport_keys", {}) or {}) if source is not None else {}
    for code in league_codes:
        league = _league_by_code(settings, code)
        item_issues: list[str] = []
        if league is None:
            item_issues.append(f"unknown_league:{code}")
            sport_key = None
            name = code
        else:
            sport_key = sport_key_for_league(code, getattr(league, "odds_api_slug", None), sport_keys)
            name = getattr(league, "name", code)
            if not sport_key:
                item_issues.append(f"missing_sport_key:{code}")

        item_commands: list[str] = []
        if sport_key and snapshot_times and not validation_issues:
            for snapshot_time in snapshot_times:
                command = (
                    "footballctl ingest historical-odds "
                    f"--source the_odds_api --league {code} "
                    f"--snapshot-time {_format_historical_snapshot_time(snapshot_time)}"
                )
                if max_events is not None:
                    command += f" --max-events {max_events}"
                command += " --json"
                item_commands.append(command)
            commands.extend(item_commands)

        if item_issues:
            item_status = "blocked"
        elif source_issues or validation_issues:
            item_status = "action_required"
        elif item_commands:
            item_status = "ready"
        else:
            item_status = "empty"
        plan_items.append(
            {
                "league": code,
                "name": name,
                "sport_key": sport_key,
                "status": item_status,
                "issues": item_issues,
                "snapshot_count": len(snapshot_times),
                "estimated_request_count": len(item_commands),
                "estimated_usage_credits": len(item_commands) * per_snapshot_credit_cost,
                "commands": item_commands,
            }
        )

    issues = sorted(set(source_issues + validation_issues + [issue for item in plan_items for issue in item["issues"]]))
    command_count = len(commands)
    if validation_issues:
        status = "invalid"
    elif not command_count:
        status = "blocked"
    elif source_issues or any(item["issues"] for item in plan_items):
        status = "action_required"
    else:
        status = "ready"

    return {
        "checked_at": datetime.now(settings.app.tzinfo).isoformat(),
        "status": status,
        "source_id": source_id,
        "enabled": bool(getattr(source, "enabled", False)) if source is not None else False,
        "credential_env": credential_env,
        "credential_present": credential_present,
        "start_time": start_time,
        "end_time": end_time,
        "interval_minutes": interval_minutes,
        "max_snapshots": max_snapshots,
        "max_events": max_events,
        "snapshot_times": [_format_historical_snapshot_time(item) for item in snapshot_times],
        "snapshot_count": len(snapshot_times),
        "truncated": truncated,
        "regions": regions,
        "markets": markets,
        "usage_model": "10 credits per region per market per historical odds snapshot",
        "per_snapshot_credit_cost": per_snapshot_credit_cost,
        "estimated_request_count": command_count,
        "estimated_usage_credits": command_count * per_snapshot_credit_cost,
        "command_count": command_count,
        "issues": issues,
        "leagues": plan_items,
        "commands": commands,
    }


def _parse_historical_snapshot_time(value: str, field_name: str, issues: list[str]) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        issues.append(f"missing_{field_name}")
        return None
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        issues.append(f"invalid_{field_name}")
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_historical_snapshot_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _historical_snapshot_times(
    start_time: datetime,
    end_time: datetime,
    interval_minutes: int,
    max_snapshots: int,
) -> tuple[list[datetime], bool]:
    values: list[datetime] = []
    cursor = start_time
    interval = timedelta(minutes=interval_minutes)
    truncated = False
    while cursor <= end_time:
        if len(values) >= max_snapshots:
            truncated = True
            break
        values.append(cursor)
        cursor += interval
    return values, truncated


def build_production_onboarding(
    service: AnalysisService,
    broker_id: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    profile_promotion_audit: bool = False,
    audit_runner: Callable[..., Any] | None = None,
    profile_audit_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    data_plan = build_production_data_plan(service, include_past=include_past)
    broker_plan = build_production_broker_plan(
        service,
        broker_id=broker_id,
        include_past=include_past,
        platform=platform,
        audit_runner=audit_runner,
    )
    profile_promotion = _build_preflight_profile_promotion(
        service,
        data_plan,
        require_audit=profile_promotion_audit,
        audit_runner=profile_audit_runner,
    )

    data_sources = _production_onboarding_data_sources(data_plan)
    broker = _production_onboarding_broker(broker_plan)
    profile = _production_onboarding_profile_promotion(profile_promotion)

    actions: list[dict[str, Any]] = []
    for item in data_sources:
        actions.extend(item["actions"])
    actions.extend(broker["actions"])
    actions.extend(profile["actions"])
    actions = _dedupe_onboarding_actions(actions)

    status = "ready" if not actions else "action_required"
    return {
        "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
        "status": status,
        "action_count": len(actions),
        "broker_id": broker_id,
        "include_past": include_past,
        "platform": platform,
        "profile_promotion_audit": profile_promotion_audit,
        "data_plan_status": data_plan.get("status"),
        "broker_plan_status": broker_plan.get("status"),
        "profile_promotion_status": profile_promotion.get("status"),
        "actions": actions,
        "data_sources": data_sources,
        "broker": broker,
        "profile_promotion": profile,
    }


def build_production_onboarding_checklist(
    service: AnalysisService,
    target: str = "worker",
    broker_id: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    profile_promotion_audit: bool = False,
    config_path: str | Path | None = None,
    broker_stake_currency_per_unit: float | None = None,
    audit_runner: Callable[..., Any] | None = None,
    profile_audit_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    onboarding = build_production_onboarding(
        service,
        broker_id=broker_id,
        include_past=include_past,
        platform=platform,
        profile_promotion_audit=profile_promotion_audit,
        audit_runner=audit_runner,
        profile_audit_runner=profile_audit_runner,
    )
    resolved_config_path = str(Path(config_path or os.getenv("FOOTBALL_CONFIG", "config/default.yaml")))
    include_config_path = config_path is not None
    runtime_security = build_production_runtime_security(target=target)
    items = [
        _production_onboarding_checklist_item(
            action,
            _onboarding_apply_item(
                action,
                config_path=resolved_config_path,
                include_config_path=include_config_path,
                service=service,
                broker_stake_currency_per_unit=broker_stake_currency_per_unit,
            ),
            index,
        )
        for index, action in enumerate(onboarding.get("actions", []), start=1)
    ]
    runtime_items = [
        _production_runtime_security_checklist_item(item, len(items) + index)
        for index, item in enumerate(runtime_security.get("checks", []), start=1)
    ]
    items.extend(runtime_items)
    items = sorted(items, key=lambda item: (item["sort_order"], item["code"]))
    sections = _production_onboarding_checklist_sections(items)
    required_envs = sorted(
        {
            str(item["target"])
            for item in items
            if item.get("category") == "secrets" and item.get("target")
        }
        | {
            str(env_name)
            for item in items
            for env_name in item.get("required_envs", [])
            if env_name
        }
    )
    provider_urls = _production_onboarding_official_urls(items)
    ready_commands = [item["apply_command"] for item in items if item.get("apply_command") and item["status"] == "ready"]
    verification_commands = [
        "footballctl production-candidate-check --target record-only --json",
        "footballctl production-deploy-check --target full --fail-on-blocked --json",
    ]
    status = "ready" if not items else "action_required"
    return {
        "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
        "status": status,
        "action": "run_full_deploy_check" if status == "ready" else "complete_operator_checklist",
        "broker_id": broker_id,
        "target": target.strip().lower().replace("_", "-"),
        "include_past": include_past,
        "platform": platform,
        "profile_promotion_audit": profile_promotion_audit,
        "config_path": resolved_config_path,
        "broker_stake_currency_per_unit": broker_stake_currency_per_unit,
        "item_count": len(items),
        "ready_count": sum(1 for item in items if item["status"] == "ready"),
        "blocked_count": sum(1 for item in items if item["status"] == "blocked"),
        "manual_required_count": sum(1 for item in items if item["status"] == "manual_required"),
        "required_envs": required_envs,
        "provider_urls": provider_urls,
        "ready_commands": ready_commands,
        "verification_commands": verification_commands,
        "operator_inputs": list(
            dict.fromkeys(item["operator_input"] for item in items if item.get("operator_input"))
        ),
        "sections": sections,
        "items": items,
        "runtime_security": runtime_security,
        "onboarding": {
            "status": onboarding.get("status"),
            "action_count": onboarding.get("action_count", 0),
            "data_plan_status": onboarding.get("data_plan_status"),
            "broker_plan_status": onboarding.get("broker_plan_status"),
            "profile_promotion_status": onboarding.get("profile_promotion_status"),
        },
    }


def format_production_onboarding_checklist_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Production Onboarding Checklist",
        "",
        f"- Checked at: `{report.get('checked_at')}`",
        f"- Status: `{report.get('status')}`",
        f"- Action: `{report.get('action')}`",
        f"- Broker: `{report.get('broker_id')}`",
        f"- Config path: `{report.get('config_path')}`",
        f"- Items: `{report.get('item_count', 0)}`",
        "",
    ]
    required_envs = report.get("required_envs", []) or []
    if required_envs:
        lines.extend(["## Required Environment Variables", ""])
        for env_name in required_envs:
            lines.append(f"- [ ] `{env_name}`")
        lines.append("")
    provider_urls = report.get("provider_urls", []) or []
    if provider_urls:
        lines.extend(["## Official Provider Links", ""])
        for entry in provider_urls:
            lines.append(f"- `{entry.get('target')}`: {entry.get('official_url')}")
        lines.append("")
    for section in report.get("sections", []) or []:
        section_items = section.get("items", []) or []
        if not section_items:
            continue
        lines.extend([f"## {section.get('title')}", ""])
        for item in section_items:
            lines.append(f"- [ ] **{item.get('title')}**")
            lines.append(f"  - Code: `{item.get('code')}`")
            lines.append(f"  - Status: `{item.get('status')}`")
            if item.get("operator_input"):
                lines.append(f"  - Need: {item['operator_input']}")
            if item.get("official_url"):
                lines.append(f"  - Official URL: {item['official_url']}")
            if item.get("required_for_leagues"):
                leagues = ", ".join(str(value) for value in item["required_for_leagues"])
                lines.append(f"  - Leagues: `{leagues}`")
            if item.get("dry_run_command"):
                lines.append(f"  - Dry run: `{item['dry_run_command']}`")
            if item.get("apply_command"):
                lines.append(f"  - Apply: `{item['apply_command']}`")
            elif item.get("command"):
                lines.append(f"  - Command: `{item['command']}`")
            if item.get("blocking_reasons"):
                reasons = ", ".join(str(value) for value in item["blocking_reasons"])
                lines.append(f"  - Blocking reasons: `{reasons}`")
        lines.append("")
    ready_commands = report.get("ready_commands", []) or []
    if ready_commands:
        lines.extend(["## Ready Local Commands", ""])
        for command in ready_commands:
            lines.append(f"- `{command}`")
        lines.append("")
    verification_commands = report.get("verification_commands", []) or []
    if verification_commands:
        lines.extend(["## Verification Commands", ""])
        for command in verification_commands:
            lines.append(f"- `{command}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_production_onboarding_apply_plan(
    service: AnalysisService,
    broker_id: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    profile_promotion_audit: bool = False,
    config_path: str | Path | None = None,
    broker_stake_currency_per_unit: float | None = None,
    execute_ready: bool = False,
    timeout_seconds: int = 1800,
    command_runner: Callable[[list[str], int], Any] | None = None,
    audit_runner: Callable[..., Any] | None = None,
    profile_audit_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    onboarding = build_production_onboarding(
        service,
        broker_id=broker_id,
        include_past=include_past,
        platform=platform,
        profile_promotion_audit=profile_promotion_audit,
        audit_runner=audit_runner,
        profile_audit_runner=profile_audit_runner,
    )
    resolved_config_path = str(Path(config_path or os.getenv("FOOTBALL_CONFIG", "config/default.yaml")))
    include_config_path = config_path is not None
    items = [
        _onboarding_apply_item(
            action,
            config_path=resolved_config_path,
            include_config_path=include_config_path,
            service=service,
            broker_stake_currency_per_unit=broker_stake_currency_per_unit,
        )
        for action in onboarding.get("actions", [])
    ]
    ready_items = [item for item in items if item["status"] == "ready"]
    blocked_items = [item for item in items if item["status"] == "blocked"]
    manual_items = [item for item in items if item["status"] == "manual_required"]
    execution_results: list[dict[str, Any]] = []
    succeeded_count = 0
    failed_count = 0
    if execute_ready:
        runner = command_runner or _run_data_apply_command
        for item in ready_items:
            command = item.get("apply_command")
            if not command:
                failed_count += 1
                execution_results.append(
                    {
                        "id": item.get("id"),
                        "code": item.get("code"),
                        "status": "failed",
                        "error": "missing_apply_command",
                    }
                )
                continue
            argv = _data_apply_command_argv(str(command))
            if argv is None:
                failed_count += 1
                execution_results.append(
                    {
                        "id": item.get("id"),
                        "code": item.get("code"),
                        "command": command,
                        "status": "failed",
                        "error": "unsupported_command",
                    }
                )
                continue
            try:
                completed = runner(argv, timeout_seconds)
            except Exception as exc:  # pragma: no cover - defensive for operator runs
                failed_count += 1
                execution_results.append(
                    {
                        "id": item.get("id"),
                        "code": item.get("code"),
                        "command": command,
                        "argv": argv,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                continue
            return_code = int(getattr(completed, "returncode", 1))
            stdout = getattr(completed, "stdout", "") or ""
            stderr = getattr(completed, "stderr", "") or ""
            payload_status = _command_payload_status(stdout)
            payload_failed = payload_status in {"blocked", "failed", "unavailable"}
            result_status = "succeeded" if return_code == 0 and not payload_failed else "failed"
            if result_status == "succeeded":
                succeeded_count += 1
            else:
                failed_count += 1
            execution_result = {
                "id": item.get("id"),
                "code": item.get("code"),
                "command": command,
                "argv": argv,
                "status": result_status,
                "return_code": return_code,
                "stdout_tail": _text_tail(stdout),
                "stderr_tail": _text_tail(stderr),
            }
            if payload_status is not None:
                execution_result["payload_status"] = payload_status
            if payload_failed:
                execution_result["error"] = f"command_payload_status:{payload_status}"
            execution_results.append(execution_result)
    if ready_items and (blocked_items or manual_items):
        status = "partial_applied" if execute_ready and failed_count == 0 and succeeded_count else "partial_ready"
        action = "resolve_remaining_blockers" if status == "partial_applied" else "apply_ready_items_then_resolve_blockers"
    elif ready_items:
        if execute_ready:
            status = "applied" if failed_count == 0 else "failed"
            action = "deploy_check" if status == "applied" else "fix_apply_errors"
        else:
            status = "ready"
            action = "apply_ready_items"
    elif items:
        status = "blocked"
        action = "resolve_blockers"
    else:
        status = "no_actions"
        action = "deploy_check"
    return {
        "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
        "status": status,
        "action": action,
        "config_path": resolved_config_path,
        "broker_id": broker_id,
        "include_past": include_past,
        "platform": platform,
        "profile_promotion_audit": profile_promotion_audit,
        "broker_stake_currency_per_unit": broker_stake_currency_per_unit,
        "execute_ready": execute_ready,
        "timeout_seconds": timeout_seconds,
        "ready_count": len(ready_items),
        "blocked_count": len(blocked_items),
        "manual_required_count": len(manual_items),
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
        "item_count": len(items),
        "ready_commands": [item["apply_command"] for item in ready_items if item.get("apply_command")],
        "blocked_reasons": sorted(
            {
                reason
                for item in [*blocked_items, *manual_items]
                for reason in item.get("blocking_reasons", [])
            }
        ),
        "items": items,
        "executions": execution_results,
        "onboarding": {
            "status": onboarding.get("status"),
            "action_count": onboarding.get("action_count", 0),
            "actions": onboarding.get("actions", []),
        },
    }


def _onboarding_apply_item(
    action: dict[str, Any],
    config_path: str,
    include_config_path: bool = False,
    service: AnalysisService | None = None,
    broker_stake_currency_per_unit: float | None = None,
) -> dict[str, Any]:
    code = str(action.get("code") or "")
    command = str(action.get("command") or "")
    item = {
        "id": action.get("id") or code,
        "code": code,
        "kind": action.get("kind"),
        "target": action.get("target"),
        "title": action.get("title"),
        "reason": action.get("reason"),
        "dry_run_command": command or None,
        "apply_command": None,
        "writes_config": False,
        "requires_remote": False,
        "requires_operator_approval": False,
        "status": "manual_required",
        "blocking_reasons": [],
    }
    if code == "apply_profile_promotion":
        dry_run_command = _remove_cli_flag(command, "--apply")
        apply_command = _add_cli_flag(command, "--apply")
        if include_config_path and "--config-path" not in dry_run_command:
            dry_run_command = _add_cli_option(dry_run_command, "--config-path", config_path)
        if include_config_path and "--config-path" not in apply_command:
            apply_command = _add_cli_option(apply_command, "--config-path", config_path)
        item.update(
            {
                "dry_run_command": dry_run_command,
                "apply_command": apply_command,
                "writes_config": True,
                "requires_operator_approval": True,
                "status": "ready",
            }
        )
        return item
    if code.startswith(("enable_data_source:", "enable_broker:", "set_broker_stake_currency_per_unit:")):
        item.update(
            _onboarding_config_apply_state(
                action,
                item,
                config_path=config_path,
                include_config_path=include_config_path,
                service=service,
                broker_stake_currency_per_unit=broker_stake_currency_per_unit,
            )
        )
        return item
    if code.startswith("set_env:"):
        item.update(
            {
                "status": "manual_required",
                "blocking_reasons": ["set_secret_environment_variable"],
            }
        )
        return item
    if code.startswith("apply_or_confirm_provider:"):
        item.update(
            {
                "status": "manual_required",
                "blocking_reasons": ["provider_account_or_plan_required"],
            }
        )
        return item
    if code.startswith("apply_broker_mappings:"):
        item.update(
            {
                "requires_remote": True,
                "status": "blocked",
                "blocking_reasons": ["broker_credentials_and_remote_mapping_required"],
            }
        )
        return item
    if code == "run_profile_audit":
        item.update({"status": "ready", "requires_operator_approval": False})
        return item
    item["blocking_reasons"] = ["manual_review_required"]
    return item


def _add_cli_flag(command: str, flag: str) -> str:
    parts = command.split()
    if flag in parts:
        return command
    insert_at = parts.index("--json") if "--json" in parts else len(parts)
    return " ".join([*parts[:insert_at], flag, *parts[insert_at:]])


def _remove_cli_flag(command: str, flag: str) -> str:
    return " ".join(part for part in command.split() if part != flag)


def _add_cli_option(command: str, option: str, value: str) -> str:
    parts = command.split()
    if option in parts:
        return command
    insert_at = parts.index("--json") if "--json" in parts else len(parts)
    return " ".join([*parts[:insert_at], option, value, *parts[insert_at:]])


def _onboarding_config_apply_state(
    action: dict[str, Any],
    item: dict[str, Any],
    config_path: str,
    include_config_path: bool,
    service: AnalysisService | None,
    broker_stake_currency_per_unit: float | None,
) -> dict[str, Any]:
    command = str(action.get("command") or "")
    code = str(action.get("code") or "")
    apply_command = _add_cli_flag(command, "--apply") if command else None
    dry_run_command = command or None
    if code.startswith("set_broker_stake_currency_per_unit:") and broker_stake_currency_per_unit is not None:
        amount = f"{broker_stake_currency_per_unit:g}"
        apply_command = apply_command.replace("<AMOUNT>", amount) if apply_command else None
        dry_run_command = dry_run_command.replace("<AMOUNT>", amount) if dry_run_command else None
    if include_config_path and apply_command and "--config-path" not in apply_command:
        apply_command = _add_cli_option(apply_command, "--config-path", config_path)
    if include_config_path and dry_run_command and "--config-path" not in dry_run_command:
        dry_run_command = _add_cli_option(dry_run_command, "--config-path", config_path)

    readiness = _onboarding_config_readiness(
        service,
        code,
        action.get("target"),
        broker_stake_currency_per_unit=broker_stake_currency_per_unit,
    )
    status = readiness.get("status", "blocked")
    issues = list(readiness.get("issues", []))
    if status == "ready":
        blocking_reasons: list[str] = []
    elif status == "already_configured":
        blocking_reasons = []
    elif issues:
        blocking_reasons = issues
    else:
        blocking_reasons = ["credential_or_value_prerequisite_required"]
    return {
        "dry_run_command": dry_run_command,
        "apply_command": apply_command,
        "writes_config": True,
        "requires_operator_approval": True,
        "status": "ready" if status == "ready" else "blocked",
        "blocking_reasons": blocking_reasons,
        "config_plan_status": status,
        "config_plan_issues": issues,
    }


def _onboarding_config_readiness(
    service: AnalysisService | None,
    code: str,
    target: Any,
    broker_stake_currency_per_unit: float | None,
) -> dict[str, Any]:
    if service is None:
        return {"status": "blocked", "issues": ["config_readiness_unavailable"]}
    target_id = str(target or "")
    if code.startswith("enable_data_source:"):
        plan = build_production_config_plan(service, source_ids=[target_id])
    elif code.startswith("enable_broker:"):
        plan = build_production_config_plan(service, broker_ids=[target_id])
    elif code.startswith("set_broker_stake_currency_per_unit:"):
        if broker_stake_currency_per_unit is None:
            return {"status": "blocked", "issues": ["stake_currency_per_unit_required"]}
        plan = build_production_config_plan(
            service,
            broker_ids=[target_id],
            stake_currency_per_unit=broker_stake_currency_per_unit,
        )
    else:
        return {"status": "blocked", "issues": ["unsupported_config_action"]}
    matching = next((item for item in plan.get("items", []) if item.get("id") == target_id), None)
    if not matching:
        return {"status": "blocked", "issues": ["config_plan_item_missing"]}
    return {"status": matching.get("status"), "issues": matching.get("issues", [])}


def build_production_deploy_check(
    service: AnalysisService,
    target: str = "worker",
    broker_id: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    require_execution_queue: bool = False,
    profile_promotion_audit: bool = False,
    decision_runner: DecisionRunner | None = None,
    audit_runner: Callable[..., Any] | None = None,
    profile_audit_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    normalized_target = target.strip().lower().replace("_", "-")
    valid_targets = {"worker", "record-only", "broker-live", "full"}
    preflight = build_production_preflight(
        service,
        broker_id=broker_id,
        include_past=include_past,
        platform=platform,
        require_broker=normalized_target in {"broker-live", "full"},
        require_execution_queue=require_execution_queue,
        require_health_history=normalized_target != "worker",
        profile_promotion_audit=profile_promotion_audit,
        decision_runner=decision_runner,
        audit_runner=audit_runner,
        profile_audit_runner=profile_audit_runner,
    )
    onboarding = build_production_onboarding(
        service,
        broker_id=broker_id,
        include_past=include_past,
        platform=platform,
        profile_promotion_audit=profile_promotion_audit,
        audit_runner=audit_runner,
        profile_audit_runner=profile_audit_runner,
    )

    issues: list[str] = []
    warnings: list[str] = []
    if normalized_target not in valid_targets:
        issues.append(f"invalid_deploy_target:{target}")
    requires_worker_runtime = normalized_target in {"worker", "broker-live", "full"}
    if requires_worker_runtime and preflight.get("ready_for_worker") is not True:
        issues.append("worker_not_ready")
    elif not requires_worker_runtime and preflight.get("ready_for_worker") is not True:
        warnings.extend(f"preflight:{issue}" for issue in preflight.get("issues", []))
    if normalized_target in {"record-only", "broker-live", "full"}:
        if preflight.get("ready_for_record_execution") is not True:
            if normalized_target == "record-only" and not require_execution_queue:
                warnings.append("record_execution_not_ready")
            else:
                issues.append("record_execution_not_ready")
    if normalized_target in {"broker-live", "full"}:
        if preflight.get("ready_for_broker_execution") is not True:
            issues.append("broker_execution_not_ready")
        for action in onboarding.get("broker", {}).get("actions", []):
            code = action.get("code")
            if code:
                issues.append(f"broker_onboarding_action_required:{code}")
    if normalized_target == "full":
        for action in onboarding.get("actions", []):
            code = action.get("code")
            if code:
                issues.append(f"onboarding_action_required:{code}")
        if preflight.get("status") != "ready":
            issues.append(f"preflight_not_ready:{preflight.get('status')}")

    warnings.extend(f"preflight:{warning}" for warning in preflight.get("warnings", []))
    if onboarding.get("action_count", 0):
        warnings.append(f"onboarding_action_required:{onboarding.get('action_count', 0)}")
    if profile_promotion_audit is False:
        warnings.append("profile_promotion_audit_not_checked")

    if issues:
        status = "blocked"
        action = "fix_deploy_blockers"
    elif warnings:
        status = "ready_with_warnings"
        action = _production_deploy_action(normalized_target)
    else:
        status = "ready"
        action = _production_deploy_action(normalized_target)
    return {
        "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
        "status": status,
        "target": normalized_target,
        "action": action,
        "broker_id": broker_id,
        "include_past": include_past,
        "platform": platform,
        "require_execution_queue": require_execution_queue,
        "profile_promotion_audit": profile_promotion_audit,
        "ready_for_worker": preflight.get("ready_for_worker") is True,
        "ready_for_record_execution": preflight.get("ready_for_record_execution") is True,
        "ready_for_broker_execution": preflight.get("ready_for_broker_execution") is True,
        "issues": sorted(set(issues)),
        "warnings": sorted(set(warnings)),
        "preflight": {
            "status": preflight.get("status"),
            "action": preflight.get("action"),
            "warnings": preflight.get("warnings", []),
            "issues": preflight.get("issues", []),
            "execution_queue": preflight.get("execution_queue", {}),
            "record_execution_history": preflight.get("record_execution_history", {}),
            "broker_plan": preflight.get("broker_plan", {}),
            "profile_promotion": preflight.get("profile_promotion", {}),
        },
        "onboarding": {
            "status": onboarding.get("status"),
            "action_count": onboarding.get("action_count", 0),
            "actions": onboarding.get("actions", []),
            "broker": onboarding.get("broker", {}),
            "profile_promotion": onboarding.get("profile_promotion", {}),
        },
    }


def _production_deploy_action(target: str) -> str:
    if target == "worker":
        return "deploy_worker_observe_or_dry_run"
    if target == "record-only":
        return "deploy_worker_record_only"
    if target == "broker-live":
        return "deploy_worker_broker_live"
    if target == "full":
        return "deploy_full_automation"
    return "fix_deploy_blockers"


def build_production_runtime_security(target: str = "worker") -> dict[str, Any]:
    load_dotenv(override=False)
    normalized_target = target.strip().lower().replace("_", "-")
    bootstrap_command = f"footballctl production-runtime-secrets --target {normalized_target} --json"
    strict_target = normalized_target in {"broker-live", "full"}
    admin_token = os.getenv("FOOTBALL_ADMIN_TOKEN", "").strip()
    api_bind_host = (os.getenv("API_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1")
    postgres_bind_host = (os.getenv("POSTGRES_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1")
    postgres_password = os.getenv("POSTGRES_PASSWORD", "football")

    admin_token_present = bool(admin_token)
    admin_token_min_length_ok = not admin_token_present or len(admin_token) >= 16
    api_public_bind = not _runtime_bind_host_is_local(api_bind_host)
    postgres_public_bind = not _runtime_bind_host_is_local(postgres_bind_host)
    postgres_password_is_default = postgres_password in {"", "football"}

    checks: list[dict[str, Any]] = []
    if not admin_token_present:
        severity = "issue" if strict_target or api_public_bind else "warning"
        checks.append(
            {
                "code": "runtime_admin_token_missing",
                "severity": severity,
                "status": "blocked" if severity == "issue" else "manual_required",
                "title": "Set FOOTBALL_ADMIN_TOKEN for production API operations",
                "operator_input": "Set FOOTBALL_ADMIN_TOKEN before exposing write, execution, remote-fetch, or broker API endpoints.",
                "required_envs": ["FOOTBALL_ADMIN_TOKEN"],
                "bootstrap_command": bootstrap_command,
                "show_values_command": f"{bootstrap_command} --show-secret-values",
            }
        )
    elif not admin_token_min_length_ok:
        severity = "issue" if api_public_bind else "warning"
        checks.append(
            {
                "code": "runtime_admin_token_too_short",
                "severity": severity,
                "status": "blocked" if severity == "issue" else "manual_required",
                "title": "Use a stronger FOOTBALL_ADMIN_TOKEN",
                "operator_input": "Set FOOTBALL_ADMIN_TOKEN to at least 16 characters.",
                "required_envs": ["FOOTBALL_ADMIN_TOKEN"],
                "bootstrap_command": bootstrap_command,
                "show_values_command": f"{bootstrap_command} --show-secret-values",
            }
        )
    if api_public_bind:
        severity = "issue" if not admin_token_present else "warning"
        checks.append(
            {
                "code": "runtime_api_public_bind",
                "severity": severity,
                "status": "blocked" if severity == "issue" else "manual_required",
                "title": "Review public API bind host",
                "operator_input": "Prefer API_BIND_HOST=127.0.0.1 behind a reverse proxy; if API_BIND_HOST is public, keep FOOTBALL_ADMIN_TOKEN set.",
            }
        )
    if postgres_password_is_default:
        severity = "issue" if strict_target else "warning"
        checks.append(
            {
                "code": "runtime_postgres_default_password",
                "severity": severity,
                "status": "blocked" if severity == "issue" else "manual_required",
                "title": "Replace default Postgres password",
                "operator_input": "Set POSTGRES_PASSWORD to an environment-specific strong secret before production rollout.",
                "required_envs": ["POSTGRES_PASSWORD"],
                "bootstrap_command": bootstrap_command,
                "show_values_command": f"{bootstrap_command} --show-secret-values",
            }
        )
    if postgres_public_bind:
        checks.append(
            {
                "code": "runtime_postgres_public_bind",
                "severity": "issue",
                "status": "blocked",
                "title": "Do not expose Postgres publicly",
                "operator_input": "Keep POSTGRES_BIND_HOST on 127.0.0.1 or remove host publication; never expose Postgres directly to the public internet.",
            }
        )

    issues = [item["code"] for item in checks if item.get("severity") == "issue"]
    warnings = [item["code"] for item in checks if item.get("severity") == "warning"]
    status = "blocked" if issues else "ready_with_warnings" if warnings else "ready"
    return {
        "checked_at": datetime.utcnow().isoformat(),
        "status": status,
        "target": normalized_target,
        "strict_target": strict_target,
        "admin_token_present": admin_token_present,
        "admin_token_min_length_ok": admin_token_min_length_ok,
        "api_bind_host": api_bind_host,
        "api_public_bind": api_public_bind,
        "postgres_bind_host": postgres_bind_host,
        "postgres_public_bind": postgres_public_bind,
        "postgres_password_is_default": postgres_password_is_default,
        "issues": issues,
        "warnings": warnings,
        "checks": checks,
    }


def build_production_runtime_secret_bootstrap(
    target: str = "worker",
    show_secret_values: bool = False,
    admin_token_bytes: int = 32,
    postgres_password_bytes: int = 24,
    value_factory: Callable[[str, int], str] | None = None,
) -> dict[str, Any]:
    normalized_target = target.strip().lower().replace("_", "-")
    runtime_security = build_production_runtime_security(target=normalized_target)
    required_envs = sorted(
        {
            env_name
            for check in runtime_security.get("checks", [])
            for env_name in check.get("required_envs", [])
            if env_name in {"FOOTBALL_ADMIN_TOKEN", "POSTGRES_PASSWORD"}
        }
    )

    def generated_value(env_name: str) -> str:
        byte_count = admin_token_bytes if env_name == "FOOTBALL_ADMIN_TOKEN" else postgres_password_bytes
        if value_factory is not None:
            return value_factory(env_name, byte_count)
        return secrets.token_urlsafe(byte_count)

    generated: dict[str, str] = {env_name: generated_value(env_name) for env_name in required_envs}
    items: list[dict[str, Any]] = []
    for env_name in required_envs:
        value = generated[env_name]
        visible_value = value if show_secret_values else None
        redacted_value = f"<generated:{len(value)} chars>"
        items.append(
            {
                "env": env_name,
                "status": "generated",
                "secret_value": visible_value,
                "redacted_value": redacted_value,
                "env_line": f"{env_name}={value if show_secret_values else redacted_value}",
                "powershell_set": f'$env:{env_name}="{value if show_secret_values else redacted_value}"',
                "notes": _runtime_secret_notes(env_name),
            }
        )

    env_lines = [str(item["env_line"]) for item in items]
    postgres_rotation_command = None
    if "POSTGRES_PASSWORD" in required_envs:
        postgres_user = os.getenv("POSTGRES_USER", "football").replace('"', '""')
        postgres_db = os.getenv("POSTGRES_DB", "football_analysis")
        postgres_password = generated["POSTGRES_PASSWORD"] if show_secret_values else "<generated POSTGRES_PASSWORD>"
        postgres_rotation_command = (
            "docker compose exec -T postgres psql "
            f"-U {postgres_user} -d {postgres_db} "
            f"-c \"ALTER USER \\\"{postgres_user}\\\" WITH PASSWORD '{postgres_password}';\""
        )

    apply_steps = [
        {
            "order": 1,
            "title": "Generate runtime secrets",
            "status": "ready" if items else "not_needed",
            "operator_action": (
                "Run with --show-secret-values in a private terminal or CI secret job to reveal the generated values."
                if items and not show_secret_values
                else "Generated runtime secret values are included in this response; store them in the production secret store."
            ),
        },
        {
            "order": 2,
            "title": "Set production environment",
            "status": "manual_required" if items else "not_needed",
            "operator_action": "Write the env lines to the production secret store or .env used by Docker Compose.",
            "env_lines": env_lines,
        },
        {
            "order": 3,
            "title": "Rotate existing Postgres password",
            "status": "manual_required" if postgres_rotation_command else "not_needed",
            "operator_action": (
                "For an existing postgres-data volume, changing POSTGRES_PASSWORD in .env does not update the existing database role. Run the rotation command before restarting API/worker."
                if postgres_rotation_command
                else "No Postgres password rotation is needed by this bootstrap plan."
            ),
            "command": postgres_rotation_command,
        },
        {
            "order": 4,
            "title": "Restart production services",
            "status": "manual_required" if items else "not_needed",
            "commands": [
                "docker compose up -d postgres api worker",
                "footballctl production-ops-check --api-url http://127.0.0.1:18000 --target worker --json",
                f"footballctl production-runtime-security --target {normalized_target} --json",
            ],
        },
    ]

    return {
        "checked_at": datetime.utcnow().isoformat(),
        "status": "manual_required" if items else "ready",
        "target": normalized_target,
        "writes_files": False,
        "calls_remote_providers": False,
        "secret_values_visible": show_secret_values,
        "required_envs": required_envs,
        "items": items,
        "apply_steps": apply_steps,
        "runtime_security": runtime_security,
        "warnings": [
            "secret_values_redacted"
        ]
        if items and not show_secret_values
        else [],
        "next_commands": [
            f"footballctl production-runtime-secrets --target {normalized_target} --show-secret-values --json",
            "docker compose up -d postgres api worker",
            "footballctl production-ops-check --api-url http://127.0.0.1:18000 --target worker --json",
        ]
        if items
        else [
            f"footballctl production-runtime-security --target {normalized_target} --json",
        ],
    }


def _runtime_secret_notes(env_name: str) -> list[str]:
    if env_name == "FOOTBALL_ADMIN_TOKEN":
        return [
            "Use this token only in the production API environment.",
            "Clients call protected endpoints with X-Football-Admin-Token or Authorization: Bearer.",
        ]
    if env_name == "POSTGRES_PASSWORD":
        return [
            "The generated value uses URL-safe characters for the Compose DATABASE_URL template.",
            "On an existing postgres-data volume, rotate the database role password as well as updating .env.",
        ]
    return []


def _runtime_bind_host_is_local(bind_host: str) -> bool:
    value = bind_host.strip().lower().strip("[]")
    if not value:
        return True
    if value in {"localhost", "::1"}:
        return True
    return value.startswith("127.")


def build_production_candidate_check(
    service: AnalysisService,
    source_config_path: str | Path | None = None,
    candidate_config_path: str | Path | None = None,
    target: str = "record-only",
    broker_id: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    require_execution_queue: bool = False,
    profile_promotion_audit: bool = False,
    broker_stake_currency_per_unit: float | None = None,
    execute_ready: bool = True,
    refresh_candidate: bool = True,
    max_apply_passes: int = 3,
    timeout_seconds: int = 1800,
    command_runner: Callable[[list[str], int], Any] | None = None,
    decision_runner: DecisionRunner | None = None,
    audit_runner: Callable[..., Any] | None = None,
    profile_audit_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    source_path = Path(source_config_path or os.getenv("FOOTBALL_CONFIG", "config/default.yaml"))
    candidate_path = Path(candidate_config_path) if candidate_config_path else _default_candidate_config_path(service)
    checked_at = datetime.now(service.settings.app.tzinfo).isoformat()
    issues: list[str] = []
    warnings: list[str] = []
    copy_status = "not_run"
    source_hash_before: str | None = None
    source_hash_after: str | None = None
    apply_plan: dict[str, Any] | None = None
    apply_passes: list[dict[str, Any]] = []
    deploy_check: dict[str, Any] | None = None
    config_diff: dict[str, Any] | None = None

    if _same_path(source_path, candidate_path):
        issues.append("candidate_config_must_differ_from_source_config")
    elif not source_path.exists():
        issues.append(f"source_config_missing:{source_path}")
    else:
        try:
            source_hash_before = _file_sha256(source_path)
            if refresh_candidate:
                candidate_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, candidate_path)
                copy_status = "copied"
            elif candidate_path.exists():
                copy_status = "reused"
            else:
                copy_status = "missing"
                issues.append(f"candidate_config_missing:{candidate_path}")
        except Exception as exc:  # pragma: no cover - defensive for operator runs
            copy_status = "failed"
            issues.append(f"candidate_config_prepare_failed:{exc}")

    if not issues:
        apply_passes = _run_candidate_apply_passes(
            service,
            candidate_path,
            broker_id=broker_id,
            include_past=include_past,
            platform=platform,
            profile_promotion_audit=profile_promotion_audit,
            broker_stake_currency_per_unit=broker_stake_currency_per_unit,
            execute_ready=execute_ready,
            max_apply_passes=max_apply_passes,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
            audit_runner=audit_runner,
            profile_audit_runner=profile_audit_runner,
        )
        apply_plan = _candidate_apply_plan_summary(apply_passes, execute_ready=execute_ready)
        if (
            execute_ready
            and apply_passes
            and len(apply_passes) >= max(1, max_apply_passes)
            and int(apply_passes[-1].get("succeeded_count", 0)) > 0
        ):
            warnings.append(f"candidate_apply_pass_limit_reached:{max_apply_passes}")
        try:
            source_hash_after = _file_sha256(source_path)
        except Exception as exc:  # pragma: no cover - defensive for operator runs
            issues.append(f"source_config_hash_failed:{exc}")
        if source_hash_before and source_hash_after and source_hash_before != source_hash_after:
            issues.append("source_config_changed")
        try:
            config_diff = _production_candidate_config_diff(source_path, candidate_path)
        except Exception as exc:  # pragma: no cover - defensive for operator runs
            warnings.append(f"candidate_config_diff_failed:{exc}")

    apply_failed = bool(apply_plan and int(apply_plan.get("failed_count", 0)) > 0)
    if not issues and not apply_failed:
        try:
            from football_analysis.settings import load_settings

            candidate_settings = load_settings(candidate_path)
            candidate_service = AnalysisService(candidate_settings, service.repository)
            old_config = os.environ.get("FOOTBALL_CONFIG")
            os.environ["FOOTBALL_CONFIG"] = str(candidate_path)
            try:
                deploy_check = build_production_deploy_check(
                    candidate_service,
                    target=target,
                    broker_id=broker_id,
                    include_past=include_past,
                    platform=platform,
                    require_execution_queue=require_execution_queue,
                    profile_promotion_audit=profile_promotion_audit,
                    decision_runner=decision_runner,
                    audit_runner=audit_runner,
                    profile_audit_runner=profile_audit_runner,
                )
            finally:
                if old_config is None:
                    os.environ.pop("FOOTBALL_CONFIG", None)
                else:
                    os.environ["FOOTBALL_CONFIG"] = old_config
        except Exception as exc:  # pragma: no cover - defensive for operator runs
            issues.append(f"candidate_deploy_check_failed:{exc}")

    if issues:
        status = "failed"
        action = "fix_candidate_check_errors"
    elif apply_failed:
        status = "failed"
        action = "fix_candidate_apply_errors"
    elif deploy_check is None:
        status = "failed"
        action = "fix_candidate_deploy_check"
    elif deploy_check.get("status") == "blocked":
        status = "blocked"
        action = "resolve_candidate_blockers"
    elif deploy_check.get("status") == "ready":
        status = "ready"
        action = "promote_candidate_after_operator_review"
    else:
        status = "ready_with_warnings"
        action = "review_candidate_warnings"

    deploy_warnings = deploy_check.get("warnings", []) if deploy_check else []
    if deploy_warnings:
        warnings.extend(f"deploy:{warning}" for warning in deploy_warnings)
    return {
        "checked_at": checked_at,
        "status": status,
        "action": action,
        "target": target.strip().lower().replace("_", "-"),
        "broker_id": broker_id,
        "include_past": include_past,
        "platform": platform,
        "require_execution_queue": require_execution_queue,
        "profile_promotion_audit": profile_promotion_audit,
        "broker_stake_currency_per_unit": broker_stake_currency_per_unit,
        "execute_ready": execute_ready,
        "refresh_candidate": refresh_candidate,
        "max_apply_passes": max_apply_passes,
        "timeout_seconds": timeout_seconds,
        "source_config_path": str(source_path),
        "candidate_config_path": str(candidate_path),
        "copy_status": copy_status,
        "source_config_changed": bool(
            source_hash_before and source_hash_after and source_hash_before != source_hash_after
        ),
        "ready_for_target": status in {"ready", "ready_with_warnings"},
        "issues": sorted(set(issues)),
        "warnings": sorted(set(warnings)),
        "apply_plan": apply_plan,
        "apply_passes": apply_passes,
        "deploy_check": deploy_check,
        "config_diff": config_diff,
    }


def build_production_deployment_doctor(
    service: AnalysisService,
    target: str = "worker",
    broker_id: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    require_execution_queue: bool = False,
    profile_promotion_audit: bool = False,
    broker_stake_currency_per_unit: float | None = None,
    source_config_path: str | Path | None = None,
    candidate_config_path: str | Path | None = None,
    execute_candidate_ready: bool = False,
    refresh_candidate: bool = True,
    max_apply_passes: int = 3,
    timeout_seconds: int = 1800,
    decision_runner: DecisionRunner | None = None,
    audit_runner: Callable[..., Any] | None = None,
    profile_audit_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    normalized_target = target.strip().lower().replace("_", "-")
    execution_queue_runner = lambda svc, include_past=False: build_production_execution_queue(
        svc,
        include_past=include_past,
        platform=platform,
        audit_runner=audit_runner,
    )
    status_report = build_production_status(
        service,
        include_past=include_past,
        decision_runner=decision_runner,
        execution_queue_runner=execution_queue_runner,
    )
    health = build_production_health(
        service,
        include_past=include_past,
        decision_runner=decision_runner,
        execution_queue_runner=execution_queue_runner,
    )
    runtime_security = build_production_runtime_security(target=normalized_target)
    worker_startup_health_ready = (
        normalized_target == "worker"
        and health.get("status") == "unhealthy"
        and _production_worker_startup_health_ready(health)
    )
    deploy_check = build_production_deploy_check(
        service,
        target=normalized_target,
        broker_id=broker_id,
        include_past=include_past,
        platform=platform,
        require_execution_queue=require_execution_queue,
        profile_promotion_audit=profile_promotion_audit,
        decision_runner=decision_runner,
        audit_runner=audit_runner,
        profile_audit_runner=profile_audit_runner,
    )
    checklist = build_production_onboarding_checklist(
        service,
        target=normalized_target,
        broker_id=broker_id,
        include_past=include_past,
        platform=platform,
        profile_promotion_audit=profile_promotion_audit,
        broker_stake_currency_per_unit=broker_stake_currency_per_unit,
        audit_runner=audit_runner,
        profile_audit_runner=profile_audit_runner,
    )
    candidate_check = build_production_candidate_check(
        service,
        source_config_path=source_config_path,
        candidate_config_path=candidate_config_path,
        target=normalized_target,
        broker_id=broker_id,
        include_past=include_past,
        platform=platform,
        require_execution_queue=require_execution_queue,
        profile_promotion_audit=profile_promotion_audit,
        broker_stake_currency_per_unit=broker_stake_currency_per_unit,
        execute_ready=execute_candidate_ready,
        refresh_candidate=refresh_candidate,
        max_apply_passes=max_apply_passes,
        timeout_seconds=timeout_seconds,
        decision_runner=decision_runner,
        audit_runner=audit_runner,
        profile_audit_runner=profile_audit_runner,
    )

    issues: list[str] = []
    warnings: list[str] = []
    if health.get("status") == "unhealthy":
        if worker_startup_health_ready:
            warnings.append("production_health_startup_history_missing")
        else:
            issues.append("production_health_unhealthy")
    elif health.get("status") == "degraded":
        warnings.append("production_health_degraded")
    if deploy_check.get("status") == "blocked":
        issues.append(f"deploy_check_blocked:{normalized_target}")
    elif deploy_check.get("status") == "ready_with_warnings":
        warnings.append(f"deploy_check_ready_with_warnings:{normalized_target}")
    if candidate_check.get("status") in {"failed", "blocked"}:
        issues.append(f"candidate_check_{candidate_check.get('status')}:{normalized_target}")
    elif candidate_check.get("status") == "ready_with_warnings":
        warnings.append(f"candidate_check_ready_with_warnings:{normalized_target}")
    if checklist.get("status") != "ready":
        warnings.append(f"onboarding_action_required:{checklist.get('item_count', 0)}")
    if runtime_security.get("status") == "blocked":
        issues.append("runtime_security_blocked")
        issues.extend(f"runtime_security:{issue}" for issue in runtime_security.get("issues", []))
    elif runtime_security.get("status") == "ready_with_warnings":
        warnings.append("runtime_security_ready_with_warnings")
        warnings.extend(f"runtime_security:{warning}" for warning in runtime_security.get("warnings", []))

    status = "blocked" if issues else "ready_with_warnings" if warnings else "ready"
    action = (
        "resolve_deployment_blockers"
        if issues
        else "review_deployment_warnings"
        if warnings
        else "deploy_production_worker"
    )
    next_actions = _production_deployment_next_actions(
        target=normalized_target,
        issues=issues,
        warnings=warnings,
        status_report=status_report,
        runtime_security=runtime_security,
        deploy_check=deploy_check,
        candidate_check=candidate_check,
        checklist=checklist,
    )
    return {
        "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
        "status": status,
        "action": action,
        "target": normalized_target,
        "broker_id": broker_id,
        "include_past": include_past,
        "platform": platform,
        "require_execution_queue": require_execution_queue,
        "profile_promotion_audit": profile_promotion_audit,
        "broker_stake_currency_per_unit": broker_stake_currency_per_unit,
        "execute_candidate_ready": execute_candidate_ready,
        "refresh_candidate": refresh_candidate,
        "worker_startup_health_ready": worker_startup_health_ready,
        "ready_for_target": deploy_check.get("status") in {"ready", "ready_with_warnings"}
        and candidate_check.get("status") in {"ready", "ready_with_warnings"}
        and runtime_security.get("status") in {"ready", "ready_with_warnings"},
        "runtime_security_ready": runtime_security.get("status") in {"ready", "ready_with_warnings"},
        "issues": sorted(set(issues)),
        "warnings": sorted(set(warnings)),
        "next_actions": next_actions,
        "summary": {
            "production_status": status_report.get("status"),
            "production_ready": status_report.get("ready"),
            "health_status": health.get("status"),
            "health_ready_to_bet": health.get("ready_to_bet"),
            "runtime_security_status": runtime_security.get("status"),
            "deploy_check_status": deploy_check.get("status"),
            "candidate_check_status": candidate_check.get("status"),
            "onboarding_status": checklist.get("status"),
            "onboarding_item_count": checklist.get("item_count", 0),
            "onboarding_blocked_count": checklist.get("blocked_count", 0),
            "onboarding_manual_required_count": checklist.get("manual_required_count", 0),
            "required_envs": checklist.get("required_envs", []),
            "provider_urls": checklist.get("provider_urls", []),
        },
        "deploy_check": deploy_check,
        "candidate_check": candidate_check,
        "runtime_security": runtime_security,
        "onboarding_checklist": {
            "status": checklist.get("status"),
            "item_count": checklist.get("item_count", 0),
            "ready_count": checklist.get("ready_count", 0),
            "blocked_count": checklist.get("blocked_count", 0),
            "manual_required_count": checklist.get("manual_required_count", 0),
            "required_envs": checklist.get("required_envs", []),
            "provider_urls": checklist.get("provider_urls", []),
            "ready_commands": checklist.get("ready_commands", []),
            "verification_commands": checklist.get("verification_commands", []),
            "operator_inputs": checklist.get("operator_inputs", []),
            "sections": checklist.get("sections", []),
            "items": checklist.get("items", []),
        },
        "health": {
            "status": health.get("status"),
            "ready_to_bet": health.get("ready_to_bet"),
            "issues": health.get("issues", []),
            "job_health": health.get("job_health", {}),
        },
        "production_status": {
            "status": status_report.get("status"),
            "ready": status_report.get("ready"),
            "issues": status_report.get("issues", []),
            "provider_status": status_report.get("provider_status", {}),
            "table_counts": status_report.get("table_counts", {}),
            "production_readiness": status_report.get("production_readiness", {}),
        },
    }


def _production_deployment_next_actions(
    target: str,
    issues: list[str],
    warnings: list[str],
    status_report: dict[str, Any],
    runtime_security: dict[str, Any],
    deploy_check: dict[str, Any],
    candidate_check: dict[str, Any],
    checklist: dict[str, Any],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []

    def add(action: dict[str, Any]) -> None:
        action_id = str(action.get("id") or "")
        if action_id and any(existing.get("id") == action_id for existing in actions):
            return
        actions.append(action)

    runtime_codes = [
        *(runtime_security.get("issues", []) or []),
        *(runtime_security.get("warnings", []) or []),
    ]
    if any(code in {"runtime_admin_token_missing", "runtime_postgres_default_password"} for code in runtime_codes):
        add(
            {
                "id": "bootstrap_runtime_secrets",
                "status": "manual_required",
                "reason": "runtime_security_requires_secret_rotation",
                "command": f"footballctl production-runtime-secrets --target {target} --show-secret-values --json",
                "verify_command": f"footballctl production-runtime-security --target {target} --json",
                "requires_operator_approval": True,
                "writes_files": False,
                "calls_remote_providers": False,
                "secret_values_visible_when_run": True,
                "required_envs": [
                    env
                    for env in ["FOOTBALL_ADMIN_TOKEN", "POSTGRES_PASSWORD"]
                    if env in {
                        env_name
                        for check in runtime_security.get("checks", []) or []
                        for env_name in check.get("required_envs", [])
                    }
                ],
            }
        )

    execution_queue = status_report.get("execution_queue") or {}
    profileless_count = int(execution_queue.get("profileless_candidate_count") or 0)
    if profileless_count:
        add(
            {
                "id": "review_profileless_execution_candidates",
                "status": "manual_required",
                "reason": f"strategy_profile_required:{profileless_count}",
                "command": "footballctl production-execution-queue --json",
                "verify_command": "footballctl production-status --json",
                "candidate_count": profileless_count,
                "requires_operator_approval": True,
                "writes_files": False,
                "calls_remote_providers": False,
                "candidate_summaries": list(execution_queue.get("profileless_candidates", [])[:5]),
            }
        )

    if any("profile_promotion" in warning for warning in warnings):
        add(
            {
                "id": "run_profile_promotion_audit",
                "status": "ready",
                "reason": "profile_promotion_warning_present",
                "command": "footballctl production-profile-promote --json",
                "verify_command": f"footballctl production-deploy-check --target {target} --json",
                "requires_operator_approval": False,
                "writes_files": False,
                "calls_remote_providers": False,
            }
        )

    if any("production_data_plan_action_required" in warning for warning in warnings):
        add(
            {
                "id": "review_production_data_plan",
                "status": "manual_required",
                "reason": "production_data_plan_action_required",
                "command": "footballctl production-data-plan --json",
                "verify_command": "footballctl production-readiness --json",
                "requires_operator_approval": True,
                "writes_files": False,
                "calls_remote_providers": False,
            }
        )

    checklist_items = [item for item in checklist.get("items", []) or [] if isinstance(item, dict)]
    manual_items = [item for item in checklist_items if item.get("status") == "manual_required"]
    if manual_items:
        add(
            {
                "id": "complete_operator_checklist",
                "status": "manual_required",
                "reason": f"manual_required:{len(manual_items)}",
                "command": f"footballctl production-onboarding-checklist --target {target} --json",
                "verify_command": f"footballctl production-deployment-doctor --target {target} --json",
                "requires_operator_approval": True,
                "writes_files": False,
                "calls_remote_providers": False,
                "items": [
                    {
                        "code": item.get("code"),
                        "title": item.get("title"),
                        "category": item.get("category"),
                        "required_envs": item.get("required_envs", []),
                        "operator_input": item.get("operator_input"),
                    }
                    for item in manual_items[:10]
                ],
            }
        )

    if any(issue.startswith("deploy_check_blocked") for issue in issues):
        add(
            {
                "id": "fix_deploy_check_blockers",
                "status": "blocked",
                "reason": "deploy_check_blocked",
                "command": f"footballctl production-deploy-check --target {target} --json",
                "requires_operator_approval": False,
                "writes_files": False,
                "calls_remote_providers": False,
                "issues": deploy_check.get("issues", []),
            }
        )
    if any(issue.startswith("candidate_check_") for issue in issues):
        add(
            {
                "id": "fix_candidate_config_blockers",
                "status": "blocked",
                "reason": "candidate_check_blocked",
                "command": f"footballctl production-candidate-check --target {target} --json",
                "requires_operator_approval": False,
                "writes_files": False,
                "calls_remote_providers": False,
                "issues": candidate_check.get("issues", []),
            }
        )

    if not actions:
        actions.append(
            {
                "id": "verify_production_target",
                "status": "ready",
                "reason": "no_blocking_next_action",
                "command": f"footballctl production-deployment-doctor --target {target} --json",
                "requires_operator_approval": False,
                "writes_files": False,
                "calls_remote_providers": False,
            }
        )
    return actions


def _run_candidate_apply_passes(
    service: AnalysisService,
    candidate_path: Path,
    broker_id: str,
    include_past: bool,
    platform: str,
    profile_promotion_audit: bool,
    broker_stake_currency_per_unit: float | None,
    execute_ready: bool,
    max_apply_passes: int,
    timeout_seconds: int,
    command_runner: Callable[[list[str], int], Any] | None,
    audit_runner: Callable[..., Any] | None,
    profile_audit_runner: Callable[..., Any] | None,
) -> list[dict[str, Any]]:
    pass_limit = max(1, max_apply_passes) if execute_ready else 1
    passes: list[dict[str, Any]] = []
    for pass_index in range(1, pass_limit + 1):
        candidate_service = _candidate_service_from_config(service, candidate_path)
        before_hash = _file_sha256(candidate_path) if candidate_path.exists() else None
        plan = build_production_onboarding_apply_plan(
            candidate_service,
            broker_id=broker_id,
            include_past=include_past,
            platform=platform,
            profile_promotion_audit=profile_promotion_audit,
            config_path=candidate_path,
            broker_stake_currency_per_unit=broker_stake_currency_per_unit,
            execute_ready=execute_ready,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
            audit_runner=audit_runner,
            profile_audit_runner=profile_audit_runner,
        )
        after_hash = _file_sha256(candidate_path) if candidate_path.exists() else None
        plan = {
            **plan,
            "pass_index": pass_index,
            "candidate_config_changed": bool(before_hash and after_hash and before_hash != after_hash),
        }
        passes.append(plan)
        if not execute_ready:
            break
        if int(plan.get("failed_count", 0)) > 0:
            break
        if int(plan.get("ready_count", 0)) == 0:
            break
        if int(plan.get("succeeded_count", 0)) == 0:
            break
        if before_hash and after_hash and before_hash == after_hash:
            break
    return passes


def _candidate_service_from_config(service: AnalysisService, candidate_path: Path) -> AnalysisService:
    from football_analysis.settings import load_settings

    candidate_settings = load_settings(candidate_path)
    return AnalysisService(candidate_settings, service.repository)


def _candidate_apply_plan_summary(
    apply_passes: list[dict[str, Any]],
    execute_ready: bool,
) -> dict[str, Any] | None:
    if not apply_passes:
        return None
    final = dict(apply_passes[-1])
    succeeded_count = sum(int(plan.get("succeeded_count", 0)) for plan in apply_passes)
    failed_count = sum(int(plan.get("failed_count", 0)) for plan in apply_passes)
    executions = [
        execution
        for plan in apply_passes
        for execution in plan.get("executions", []) or []
    ]
    final.update(
        {
            "pass_count": len(apply_passes),
            "succeeded_count": succeeded_count,
            "failed_count": failed_count,
            "executions": executions,
        }
    )
    if execute_ready and failed_count:
        final["status"] = "failed" if succeeded_count == 0 else "partial_failed"
        final["action"] = "fix_candidate_apply_errors"
    elif execute_ready and succeeded_count:
        final["status"] = "partial_applied" if final.get("item_count", 0) else "applied"
        final["action"] = "resolve_remaining_blockers"
    return final


def _default_candidate_config_path(service: AnalysisService) -> Path:
    stamp = datetime.now(service.settings.app.tzinfo).strftime("%Y%m%dT%H%M%S%f")
    return Path("build") / "production-candidates" / f"candidate-{stamp}.yaml"


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve(strict=False) == right.resolve(strict=False)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _yaml_mapping(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


def _production_candidate_config_diff(source_path: Path, candidate_path: Path) -> dict[str, Any]:
    source = _yaml_mapping(source_path)
    candidate = _yaml_mapping(candidate_path)
    changed_top_level_keys = sorted(
        key for key in set(source) | set(candidate) if source.get(key) != candidate.get(key)
    )
    profile_changes = _list_item_field_changes(
        source.get("strategy_profiles", []),
        candidate.get("strategy_profiles", []),
        id_field="id",
        tracked_fields=("active", "live_enabled", "max_stake_units"),
    )
    data_source_changes = _mapping_item_field_changes(
        source.get("data_sources", {}),
        candidate.get("data_sources", {}),
        tracked_fields=("enabled", "api_key_env"),
    )
    broker_changes = _mapping_item_field_changes(
        source.get("execution_brokers", {}),
        candidate.get("execution_brokers", {}),
        tracked_fields=("enabled", "stake_currency_per_unit"),
    )
    return {
        "changed": bool(changed_top_level_keys),
        "changed_top_level_keys": changed_top_level_keys,
        "strategy_profile_changes": profile_changes,
        "data_source_changes": data_source_changes,
        "execution_broker_changes": broker_changes,
        "change_count": len(profile_changes) + len(data_source_changes) + len(broker_changes),
    }


def _list_item_field_changes(
    before_items: Any,
    after_items: Any,
    id_field: str,
    tracked_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    before = {
        str(item.get(id_field)): item
        for item in before_items
        if isinstance(item, dict) and item.get(id_field) is not None
    }
    after = {
        str(item.get(id_field)): item
        for item in after_items
        if isinstance(item, dict) and item.get(id_field) is not None
    }
    changes: list[dict[str, Any]] = []
    for item_id in sorted(set(before) | set(after)):
        field_changes = _field_changes(before.get(item_id, {}), after.get(item_id, {}), tracked_fields)
        if field_changes:
            changes.append({"id": item_id, "fields": field_changes})
    return changes


def _mapping_item_field_changes(
    before_items: Any,
    after_items: Any,
    tracked_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    before = before_items if isinstance(before_items, dict) else {}
    after = after_items if isinstance(after_items, dict) else {}
    changes: list[dict[str, Any]] = []
    for item_id in sorted(set(before) | set(after)):
        before_item = before.get(item_id, {}) if isinstance(before.get(item_id, {}), dict) else {}
        after_item = after.get(item_id, {}) if isinstance(after.get(item_id, {}), dict) else {}
        field_changes = _field_changes(before_item, after_item, tracked_fields)
        if field_changes:
            changes.append({"id": item_id, "fields": field_changes})
    return changes


def _field_changes(
    before: dict[str, Any],
    after: dict[str, Any],
    tracked_fields: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    for field in tracked_fields:
        if before.get(field) != after.get(field):
            changes[field] = {"before": before.get(field), "after": after.get(field)}
    return changes


def _production_onboarding_data_sources(data_plan: dict[str, Any]) -> list[dict[str, Any]]:
    providers: dict[str, dict[str, Any]] = {}
    for task in data_plan.get("tasks", []):
        league = task.get("league")
        task_type = task.get("task_type")
        task_status = task.get("status")
        for candidate in task.get("provider_candidates", []):
            source_id = str(candidate.get("source_id") or "").strip()
            if not source_id:
                continue
            entry = providers.setdefault(
                source_id,
                {
                    "source_id": source_id,
                    "name": candidate.get("name") or source_id,
                    "official_url": candidate.get("official_url"),
                    "configured": candidate.get("configured") is True,
                    "enabled": candidate.get("enabled") is True,
                    "credential_env": candidate.get("credential_env"),
                    "credential_present": candidate.get("credential_present") is True,
                    "requires_user_application": candidate.get("requires_user_application")
                    is True,
                    "purposes": set(),
                    "leagues": set(),
                    "task_types": set(),
                    "task_statuses": set(),
                    "coverage": set(),
                    "notes": set(),
                },
            )
            entry["configured"] = entry["configured"] or candidate.get("configured") is True
            entry["enabled"] = entry["enabled"] or candidate.get("enabled") is True
            entry["credential_present"] = (
                entry["credential_present"] or candidate.get("credential_present") is True
            )
            entry["requires_user_application"] = (
                entry["requires_user_application"]
                or candidate.get("requires_user_application") is True
            )
            if candidate.get("official_url") and not entry.get("official_url"):
                entry["official_url"] = candidate.get("official_url")
            if candidate.get("purpose"):
                entry["purposes"].add(str(candidate["purpose"]))
            if league:
                entry["leagues"].add(str(league))
            if task_type:
                entry["task_types"].add(str(task_type))
            if task_status:
                entry["task_statuses"].add(str(task_status))
            if candidate.get("coverage"):
                entry["coverage"].add(str(candidate["coverage"]))
            for note in candidate.get("notes", []) or []:
                entry["notes"].add(str(note))

    result: list[dict[str, Any]] = []
    for source_id, entry in sorted(providers.items()):
        actions = _production_onboarding_provider_actions(entry)
        result.append(
            {
                "source_id": source_id,
                "name": entry["name"],
                "status": "ready" if not actions else "action_required",
                "official_url": entry.get("official_url"),
                "configured": entry["configured"],
                "enabled": entry["enabled"],
                "credential_env": entry.get("credential_env"),
                "credential_present": entry["credential_present"],
                "requires_user_application": entry["requires_user_application"],
                "purposes": sorted(entry["purposes"]),
                "leagues": sorted(entry["leagues"]),
                "task_types": sorted(entry["task_types"]),
                "task_statuses": sorted(entry["task_statuses"]),
                "coverage": sorted(entry["coverage"]),
                "notes": sorted(entry["notes"]),
                "actions": actions,
            }
        )
    return result


def _production_onboarding_provider_actions(entry: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    source_id = str(entry["source_id"])
    official_url = entry.get("official_url")
    leagues = sorted(entry.get("leagues", []))
    if entry.get("requires_user_application") and not (
        entry.get("enabled") and entry.get("credential_present")
    ):
        actions.append(
            {
                "code": f"apply_or_confirm_provider:{source_id}",
                "kind": "provider_application",
                "target": source_id,
                "official_url": official_url,
                "required_for_leagues": leagues,
                "reason": "paid_or_plan_gated_provider_required_for_production_data",
            }
        )
    credential_env = entry.get("credential_env")
    if credential_env and not entry.get("credential_present"):
        actions.append(
            {
                "code": f"set_env:{credential_env}",
                "kind": "credential",
                "target": credential_env,
                "source_id": source_id,
                "official_url": official_url,
                "required_for_leagues": leagues,
                "reason": "provider_credential_missing",
            }
        )
    if entry.get("configured") and not entry.get("enabled"):
        actions.append(
            {
                "code": f"enable_data_source:{source_id}",
                "kind": "config",
                "target": source_id,
                "official_url": official_url,
                "required_for_leagues": leagues,
                "command": f"footballctl production-config-plan --source {source_id} --json",
                "reason": "provider_configured_but_disabled",
            }
        )
    if not entry.get("configured"):
        actions.append(
            {
                "code": f"configure_data_source:{source_id}",
                "kind": "config",
                "target": source_id,
                "official_url": official_url,
                "required_for_leagues": leagues,
                "reason": "provider_not_configured",
            }
        )
    return actions


def _production_onboarding_broker(broker_plan: dict[str, Any]) -> dict[str, Any]:
    broker = broker_plan.get("broker") or {}
    credential_status = broker.get("credential_status") or {}
    missing_credentials = [
        env_name for env_name, present in sorted(credential_status.items()) if present is not True
    ]
    actions: list[dict[str, Any]] = []
    broker_id = str(broker_plan.get("broker_id") or "")
    official_url = broker.get("official_url")
    if broker.get("enabled") is not True:
        actions.append(
            {
                "code": f"enable_broker:{broker_id}",
                "kind": "config",
                "target": broker_id,
                "official_url": official_url,
                "command": f"footballctl production-config-plan --broker {broker_id} --json",
                "reason": "broker_configured_but_disabled",
            }
        )
    for env_name in missing_credentials:
        actions.append(
            {
                "code": f"set_env:{env_name}",
                "kind": "credential",
                "target": env_name,
                "broker_id": broker_id,
                "official_url": official_url,
                "reason": "broker_credential_missing",
            }
        )
    if broker.get("stake_currency_per_unit") is None:
        actions.append(
            {
                "code": f"set_broker_stake_currency_per_unit:{broker_id}",
                "kind": "config",
                "target": broker_id,
                "official_url": official_url,
                "command": (
                    "footballctl production-config-plan "
                    f"--broker {broker_id} --stake-currency-per-unit <AMOUNT> --json"
                ),
                "reason": "stake_currency_per_unit_required",
            }
        )
    missing_mapping_count = int(broker_plan.get("missing_mapping_count", 0) or 0)
    if missing_mapping_count:
        actions.append(
            {
                "code": f"apply_broker_mappings:{broker_id}",
                "kind": "broker_mapping",
                "target": broker_id,
                "official_url": official_url,
                "command": (
                    "footballctl production-broker-discovery "
                    "--fetch-remote --apply-mappings --json"
                ),
                "missing_mapping_count": missing_mapping_count,
                "reason": "broker_market_or_selection_mapping_missing",
                "prerequisites": [
                    f"set_env:{env_name}" for env_name in missing_credentials
                ],
            }
        )
    return {
        "broker_id": broker_id,
        "status": "ready" if not actions else "action_required",
        "broker_plan_status": broker_plan.get("status"),
        "name": broker.get("name"),
        "provider": broker.get("provider"),
        "official_url": official_url,
        "enabled": broker.get("enabled") is True,
        "credential_status": credential_status,
        "missing_credentials": missing_credentials,
        "stake_currency": broker.get("stake_currency"),
        "stake_currency_per_unit": broker.get("stake_currency_per_unit"),
        "queue_count": broker_plan.get("queue_count", 0),
        "missing_mapping_count": missing_mapping_count,
        "issues": broker_plan.get("issues", []),
        "actions": actions,
    }


def _production_onboarding_profile_promotion(profile_promotion: dict[str, Any]) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    strategy_codes = list(profile_promotion.get("selected_strategy_codes", []) or [])
    max_stake_units = profile_promotion.get("max_stake_units")
    strategy_arg = ",".join(strategy_codes)
    stake_arg = f" --max-stake-units {float(max_stake_units):g}" if max_stake_units is not None else ""
    if profile_promotion.get("status") == "ready":
        actions.append(
            {
                "code": "apply_profile_promotion",
                "kind": "operator_approval",
                "target": strategy_arg,
                "command": (
                    "footballctl production-profile-promote "
                    f"--strategy-code {strategy_arg}{stake_arg} --apply --json"
                ),
                "reason": "profile_promotion_ready_but_not_applied",
            }
        )
    for issue in profile_promotion.get("issues", []) or []:
        if issue == "max_stake_units_required":
            actions.append(
                {
                    "code": "choose_profile_stake_cap",
                    "kind": "risk_config",
                    "target": strategy_arg,
                    "command": (
                        "footballctl production-profile-promote "
                        f"--strategy-code {strategy_arg or '<CODE>'}{stake_arg or ' --max-stake-units <UNITS>'} --json"
                    ),
                    "reason": issue,
                }
            )
        elif str(issue).startswith("profile_audit"):
            actions.append(
                {
                    "code": "run_profile_audit",
                    "kind": "validation",
                    "target": strategy_arg,
                    "command": "footballctl backtest profile-audit --json",
                    "reason": issue,
                }
            )
        else:
            actions.append(
                {
                    "code": f"resolve_profile_promotion_issue:{issue}",
                    "kind": "manual_review",
                    "target": strategy_arg,
                    "reason": issue,
                }
            )
    return {
        "status": "ready" if not actions else "action_required",
        "profile_promotion_status": profile_promotion.get("status"),
        "require_audit": profile_promotion.get("require_audit"),
        "audit_passed": profile_promotion.get("audit_passed"),
        "selected_profile_ids": profile_promotion.get("selected_profile_ids", []),
        "selected_strategy_codes": strategy_codes,
        "max_stake_units": profile_promotion.get("max_stake_units"),
        "ready_count": profile_promotion.get("ready_count", 0),
        "blocked_count": profile_promotion.get("blocked_count", 0),
        "already_live_count": profile_promotion.get("already_live_count", 0),
        "applied_count": profile_promotion.get("applied_count", 0),
        "issues": profile_promotion.get("issues", []),
        "actions": _dedupe_onboarding_actions(actions),
    }


def _dedupe_onboarding_actions(actions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for raw_action in actions:
        action = _standardize_onboarding_action(raw_action)
        code = str(action.get("code") or "")
        key = f"{code}:{action.get('target', '')}:{action.get('source_id', '')}:{action.get('broker_id', '')}"
        if key not in deduped:
            deduped[key] = dict(action)
            continue
        existing = deduped[key]
        for field in ("required_for_leagues", "prerequisites"):
            values = sorted(
                {
                    *[str(item) for item in existing.get(field, []) or []],
                    *[str(item) for item in action.get(field, []) or []],
                }
            )
            if values:
                existing[field] = values
    return sorted(deduped.values(), key=lambda item: str(item.get("code", "")))


def _standardize_onboarding_action(action: dict[str, Any]) -> dict[str, Any]:
    standardized = dict(action)
    code = str(standardized.get("code") or "")
    action_id = code or ":".join(
        str(part)
        for part in (
            standardized.get("kind"),
            standardized.get("target"),
            standardized.get("source_id"),
            standardized.get("broker_id"),
        )
        if part
    )
    standardized.setdefault("id", action_id)
    standardized.setdefault("status", "action_required")
    standardized.setdefault("title", _onboarding_action_title(standardized))
    return standardized


def _onboarding_action_title(action: dict[str, Any]) -> str:
    target = str(action.get("target") or "")
    source_id = str(action.get("source_id") or "")
    broker_id = str(action.get("broker_id") or "")
    code = str(action.get("code") or "")
    label = target or source_id or broker_id or code
    code_prefix = code.split(":", 1)[0]
    titles = {
        "apply_broker_mappings": "Apply broker market mappings",
        "apply_or_confirm_provider": "Apply for or confirm provider access",
        "choose_profile_stake_cap": "Choose profile stake cap",
        "enable_broker": "Enable execution broker",
        "enable_data_source": "Enable data source",
        "set_broker_stake_currency_per_unit": "Set broker stake unit value",
        "set_env": "Set environment credential",
    }
    prefix = titles.get(code_prefix, "Complete production onboarding action")
    return f"{prefix}: {label}" if label else prefix


def _production_onboarding_checklist_item(
    action: dict[str, Any],
    apply_item: dict[str, Any],
    order: int,
) -> dict[str, Any]:
    standardized = _standardize_onboarding_action(action)
    code = str(standardized.get("code") or "")
    category = _production_onboarding_checklist_category(code)
    operator_input = _production_onboarding_operator_input(standardized, apply_item, category)
    return {
        "order": order,
        "sort_order": _production_onboarding_checklist_sort_order(category, code),
        "id": standardized.get("id"),
        "code": code,
        "kind": standardized.get("kind"),
        "category": category,
        "title": standardized.get("title"),
        "status": apply_item.get("status"),
        "reason": standardized.get("reason"),
        "target": standardized.get("target"),
        "source_id": standardized.get("source_id"),
        "broker_id": standardized.get("broker_id"),
        "official_url": standardized.get("official_url"),
        "required_for_leagues": standardized.get("required_for_leagues", []),
        "command": standardized.get("command"),
        "dry_run_command": apply_item.get("dry_run_command"),
        "apply_command": apply_item.get("apply_command"),
        "writes_config": apply_item.get("writes_config", False),
        "requires_remote": apply_item.get("requires_remote", False),
        "requires_operator_approval": apply_item.get("requires_operator_approval", False),
        "blocking_reasons": apply_item.get("blocking_reasons", []),
        "operator_input": operator_input,
        "prerequisites": standardized.get("prerequisites", []),
    }


def _production_runtime_security_checklist_item(
    check: dict[str, Any],
    order: int,
) -> dict[str, Any]:
    code = str(check.get("code") or "")
    return {
        "order": order,
        "sort_order": _production_onboarding_checklist_sort_order("runtime_security", code),
        "id": code,
        "code": code,
        "kind": "runtime_security",
        "category": "runtime_security",
        "title": check.get("title"),
        "status": check.get("status"),
        "reason": check.get("severity"),
        "target": None,
        "source_id": None,
        "broker_id": None,
        "official_url": None,
        "required_for_leagues": [],
        "command": check.get("bootstrap_command"),
        "dry_run_command": check.get("bootstrap_command"),
        "apply_command": None,
        "writes_config": False,
        "requires_remote": False,
        "requires_operator_approval": True,
        "blocking_reasons": [check.get("code")] if check.get("severity") == "issue" else [],
        "operator_input": check.get("operator_input"),
        "prerequisites": [],
        "required_envs": check.get("required_envs", []),
    }


def _production_onboarding_checklist_sections(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[tuple[str, str, list[dict[str, Any]]]] = []
    buckets = [
        ("Runtime Security", "runtime_security"),
        ("Provider Access", "provider_access"),
        ("Secret Inputs", "secrets"),
        ("Data Source Configuration", "data_source_config"),
        ("Broker Configuration", "broker_config"),
        ("Risk Configuration", "risk_config"),
        ("Strategy Profile Promotion", "strategy_profile"),
        ("Broker Mappings", "broker_mapping"),
        ("Validation", "validation"),
        ("Manual Review", "manual_review"),
    ]
    for title, key in buckets:
        section_items = [item for item in items if item.get("category") == key]
        sections.append((title, key, section_items))
    return [
        {"title": title, "key": key, "items": section_items, "count": len(section_items)}
        for title, key, section_items in sections
        if section_items
    ]


def _production_onboarding_checklist_sort_order(category: str, code: str) -> tuple[int, str]:
    order = {
        "runtime_security": 5,
        "provider_access": 10,
        "secrets": 20,
        "data_source_config": 30,
        "broker_config": 40,
        "risk_config": 50,
        "strategy_profile": 60,
        "broker_mapping": 70,
        "validation": 80,
        "manual_review": 90,
    }.get(category, 100)
    return (order, code)


def _production_onboarding_checklist_category(code: str) -> str:
    prefix = code.split(":", 1)[0]
    if prefix == "runtime":
        return "runtime_security"
    if prefix == "apply_or_confirm_provider":
        return "provider_access"
    if prefix == "set_env":
        return "secrets"
    if prefix == "enable_data_source":
        return "data_source_config"
    if prefix == "enable_broker":
        return "broker_config"
    if prefix == "set_broker_stake_currency_per_unit":
        return "risk_config"
    if prefix == "apply_profile_promotion":
        return "strategy_profile"
    if prefix == "apply_broker_mappings":
        return "broker_mapping"
    if prefix == "run_profile_audit":
        return "validation"
    return "manual_review"


def _production_onboarding_operator_input(
    action: dict[str, Any],
    apply_item: dict[str, Any],
    category: str,
) -> str | None:
    code = str(action.get("code") or "")
    target = str(action.get("target") or action.get("source_id") or action.get("broker_id") or "")
    official_url = str(action.get("official_url") or "")
    if category == "provider_access":
        leagues = ", ".join(str(item) for item in action.get("required_for_leagues", []) or [])
        if leagues:
            return f"Confirm provider access for {target} covering: {leagues}."
        return f"Confirm provider access for {target}."
    if category == "secrets":
        return f"Set environment variable {target} in production."
    if category == "data_source_config":
        if apply_item.get("apply_command"):
            return "Apply the data source config patch in a candidate config, then rerun deploy-check."
        return f"Review the data source config for {target}."
    if category == "broker_config":
        if apply_item.get("apply_command"):
            return "Enable the broker in a candidate config after credentials and stake value are ready."
        return f"Review the broker config for {target}."
    if category == "risk_config":
        return f"Choose the broker stake currency-per-unit for {target}."
    if category == "strategy_profile":
        if apply_item.get("status") == "ready":
            return "Apply the ready profile promotion on a candidate config, then run deploy-check."
        return "Review the profile promotion prerequisites and audit status."
    if category == "broker_mapping":
        return "Run broker discovery with remote mapping enabled after credentials are set."
    if category == "validation":
        return "Run the required validation command before enabling live execution."
    if code:
        return f"Review the remaining onboarding step for {target or code}."
    return None


def _production_onboarding_official_urls(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    urls: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        official_url = str(item.get("official_url") or "").strip()
        if not official_url:
            continue
        target = str(item.get("target") or item.get("source_id") or item.get("broker_id") or item.get("code") or "")
        key = (target, official_url)
        urls.setdefault(
            key,
            {
                "target": target,
                "official_url": official_url,
                "category": item.get("category"),
            },
        )
    return sorted(urls.values(), key=lambda item: (str(item.get("category") or ""), str(item.get("target") or "")))


def build_production_config_plan(
    service: AnalysisService,
    source_ids: Iterable[str] | None = None,
    broker_ids: Iterable[str] | None = None,
    stake_currency_per_unit: float | None = None,
    config_path: str | Path | None = None,
    apply_changes: bool = False,
    allow_missing_credentials: bool = False,
) -> dict[str, Any]:
    settings = service.settings
    source_ids_provided = source_ids is not None
    broker_ids_provided = broker_ids is not None
    selected_source_ids = [item.strip() for item in (source_ids or []) if item.strip()]
    selected_broker_ids = [item.strip() for item in (broker_ids or []) if item.strip()]
    if not source_ids_provided and not broker_ids_provided:
        selected_source_ids = sorted(
            source_id
            for source_id, source in settings.data_sources.items()
            if getattr(source, "enabled", False) is not True
        )
        selected_broker_ids = sorted(
            broker_id
            for broker_id, broker in settings.execution_brokers.items()
            if getattr(broker, "enabled", False) is not True
            or getattr(broker, "stake_currency_per_unit", None) is None
        )

    items = [
        _production_config_source_item(
            source_id,
            settings.data_sources.get(source_id),
            allow_missing_credentials=allow_missing_credentials,
        )
        for source_id in selected_source_ids
    ]
    items.extend(
        _production_config_broker_item(
            broker_id,
            settings.execution_brokers.get(broker_id),
            stake_currency_per_unit=stake_currency_per_unit,
            allow_missing_credentials=allow_missing_credentials,
        )
        for broker_id in selected_broker_ids
    )

    ready_items = [item for item in items if item["status"] == "ready"]
    blocked_items = [item for item in items if item["status"] == "blocked"]
    applied: list[dict[str, Any]] = []
    apply_error: str | None = None
    resolved_config_path = str(Path(config_path or os.getenv("FOOTBALL_CONFIG", "config/default.yaml")))
    if apply_changes and blocked_items:
        status = "blocked"
    elif apply_changes and ready_items:
        try:
            applied = _apply_production_config(Path(resolved_config_path), ready_items)
            status = "applied" if applied else "no_changes"
        except Exception as exc:  # pragma: no cover - defensive for operator runs
            apply_error = str(exc)
            status = "failed"
    elif blocked_items:
        status = "blocked"
    elif ready_items:
        status = "ready"
    else:
        status = "no_changes"

    issues = [issue for item in items for issue in item.get("issues", [])]
    if apply_error:
        issues.append(f"apply_failed:{apply_error}")
    return {
        "checked_at": datetime.now(settings.app.tzinfo).isoformat(),
        "status": status,
        "apply": apply_changes,
        "config_path": resolved_config_path,
        "allow_missing_credentials": allow_missing_credentials,
        "source_ids": selected_source_ids,
        "broker_ids": selected_broker_ids,
        "stake_currency_per_unit": stake_currency_per_unit,
        "ready_count": len(ready_items),
        "blocked_count": len(blocked_items),
        "already_configured_count": sum(1 for item in items if item["status"] == "already_configured"),
        "applied_count": len(applied),
        "issues": sorted(set(issues)),
        "items": items,
        "applied": applied,
    }


def _production_config_source_item(
    source_id: str,
    source: Any | None,
    allow_missing_credentials: bool,
) -> dict[str, Any]:
    if source is None:
        return {
            "kind": "data_source",
            "id": source_id,
            "status": "blocked",
            "issues": [f"source_not_configured:{source_id}"],
            "current": None,
            "proposed": None,
        }
    api_key_env = getattr(source, "api_key_env", None)
    credential_present = bool(api_key_env and os.getenv(api_key_env))
    issues: list[str] = []
    if api_key_env and not credential_present and not allow_missing_credentials:
        issues.append(f"missing_credential:{api_key_env}")
    current = {
        "enabled": bool(getattr(source, "enabled", False)),
        "api_key_env": api_key_env,
        "credential_present": credential_present,
    }
    proposed = None if current["enabled"] else {"enabled": True}
    if issues:
        status = "blocked"
    elif proposed:
        status = "ready"
    else:
        status = "already_configured"
    return {
        "kind": "data_source",
        "id": source_id,
        "status": status,
        "issues": issues,
        "current": current,
        "proposed": proposed,
    }


def _production_config_broker_item(
    broker_id: str,
    broker: Any | None,
    stake_currency_per_unit: float | None,
    allow_missing_credentials: bool,
) -> dict[str, Any]:
    if broker is None:
        return {
            "kind": "broker",
            "id": broker_id,
            "status": "blocked",
            "issues": [f"broker_not_configured:{broker_id}"],
            "current": None,
            "proposed": None,
        }
    missing_credentials = [
        env_name
        for env_name in getattr(broker, "credential_envs", []) or []
        if not os.getenv(env_name)
    ]
    issues: list[str] = []
    if missing_credentials and not allow_missing_credentials:
        issues.extend(f"missing_broker_credential:{env_name}" for env_name in missing_credentials)
    current_stake = getattr(broker, "stake_currency_per_unit", None)
    proposed_stake = stake_currency_per_unit if stake_currency_per_unit is not None else current_stake
    if proposed_stake is None:
        issues.append("stake_currency_per_unit_required")
    elif proposed_stake <= 0:
        issues.append("stake_currency_per_unit_must_be_positive")
    current = {
        "enabled": bool(getattr(broker, "enabled", False)),
        "credential_envs": list(getattr(broker, "credential_envs", []) or []),
        "missing_credentials": missing_credentials,
        "stake_currency": getattr(broker, "stake_currency", None),
        "stake_currency_per_unit": current_stake,
    }
    proposed: dict[str, Any] = {}
    if current["enabled"] is not True:
        proposed["enabled"] = True
    if stake_currency_per_unit is not None and stake_currency_per_unit != current_stake:
        proposed["stake_currency_per_unit"] = stake_currency_per_unit
    elif current_stake is None and proposed_stake is not None:
        proposed["stake_currency_per_unit"] = proposed_stake
    if issues:
        status = "blocked"
    elif proposed:
        status = "ready"
    else:
        status = "already_configured"
    return {
        "kind": "broker",
        "id": broker_id,
        "status": status,
        "issues": issues,
        "current": current,
        "proposed": proposed or None,
    }


def _apply_production_config(config_path: Path, ready_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not config_path.exists():
        raise FileNotFoundError(str(config_path))
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data_sources = raw.get("data_sources")
    brokers = raw.get("execution_brokers")
    if not isinstance(data_sources, dict):
        raise ValueError("data_sources must be a mapping")
    if not isinstance(brokers, dict):
        raise ValueError("execution_brokers must be a mapping")
    applied: list[dict[str, Any]] = []
    for item in ready_items:
        item_id = str(item["id"])
        proposed = item.get("proposed") or {}
        if item["kind"] == "data_source":
            section = data_sources.get(item_id)
            if not isinstance(section, dict):
                continue
            if proposed.get("enabled") is True:
                section["enabled"] = True
            applied.append({"kind": "data_source", "id": item_id, "enabled": section.get("enabled")})
        elif item["kind"] == "broker":
            section = brokers.get(item_id)
            if not isinstance(section, dict):
                continue
            if proposed.get("enabled") is True:
                section["enabled"] = True
            if proposed.get("stake_currency_per_unit") is not None:
                section["stake_currency_per_unit"] = proposed["stake_currency_per_unit"]
            applied.append(
                {
                    "kind": "broker",
                    "id": item_id,
                    "enabled": section.get("enabled"),
                    "stake_currency_per_unit": section.get("stake_currency_per_unit"),
                }
            )
    if applied:
        config_path.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=False),
            encoding="utf-8",
        )
    return applied


def build_production_profile_promotion_plan(
    service: AnalysisService,
    profile_ids: Iterable[str] | None = None,
    strategy_codes: Iterable[str] | None = None,
    max_stake_units: float | None = None,
    config_path: str | Path | None = None,
    apply_changes: bool = False,
    seasons: Iterable[str] | None = None,
    roi_tolerance: float = 0.002,
    clv_tolerance: float = 0.002,
    require_audit: bool = True,
    audit_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    settings = service.settings
    selected_profile_ids = {item.strip() for item in (profile_ids or []) if item.strip()}
    selected_strategy_codes = {item.strip().upper() for item in (strategy_codes or []) if item.strip()}
    if not selected_profile_ids and not selected_strategy_codes:
        selected_strategy_codes = _profile_promotion_strategy_codes_from_data_plan(
            build_production_data_plan(service)
        )

    audit = None
    if require_audit:
        audit_fn = audit_runner or _run_strategy_profile_audit
        audit = audit_fn(
            service,
            seasons=list(seasons or _default_profile_audit_seasons()),
            roi_tolerance=roi_tolerance,
            clv_tolerance=clv_tolerance,
        )
    audit_by_id = {item.profile_id: item for item in getattr(audit, "items", []) or []}
    profile_by_id = {profile.id: profile for profile in settings.strategy_profiles}
    targets = _profile_promotion_targets(
        settings.strategy_profiles,
        selected_profile_ids=selected_profile_ids,
        selected_strategy_codes=selected_strategy_codes,
    )
    items = [
        _profile_promotion_item(
            profile,
            audit_by_id.get(profile.id),
            settings,
            max_stake_units=max_stake_units,
            require_audit=require_audit,
        )
        for profile in targets
    ]
    for missing_id in sorted(selected_profile_ids - set(profile_by_id)):
        items.append(
            {
                "profile_id": missing_id,
                "strategy_code": None,
                "status": "blocked",
                "issues": [f"profile_not_found:{missing_id}"],
                "current": None,
                "proposed": None,
                "audit": None,
            }
        )

    ready_items = [item for item in items if item["status"] == "ready"]
    blocked_items = [item for item in items if item["status"] == "blocked"]
    applied: list[dict[str, Any]] = []
    apply_error: str | None = None
    resolved_config_path = str(Path(config_path or os.getenv("FOOTBALL_CONFIG", "config/default.yaml")))
    if apply_changes and blocked_items:
        status = "blocked"
    elif apply_changes and ready_items:
        try:
            applied = _apply_profile_promotions(Path(resolved_config_path), ready_items)
            status = "applied" if applied else "no_changes"
        except Exception as exc:  # pragma: no cover - defensive for operator runs
            apply_error = str(exc)
            status = "failed"
    elif ready_items:
        status = "ready"
    elif blocked_items:
        status = "blocked"
    else:
        status = "no_changes"

    issues = [issue for item in items for issue in item.get("issues", [])]
    if apply_error:
        issues.append(f"apply_failed:{apply_error}")
    return {
        "checked_at": datetime.now(settings.app.tzinfo).isoformat(),
        "status": status,
        "apply": apply_changes,
        "config_path": resolved_config_path,
        "require_audit": require_audit,
        "audit_passed": getattr(audit, "passed", None),
        "selected_profile_ids": sorted(selected_profile_ids),
        "selected_strategy_codes": sorted(selected_strategy_codes),
        "max_stake_units": max_stake_units,
        "ready_count": len(ready_items),
        "blocked_count": len(blocked_items),
        "already_live_count": sum(1 for item in items if item["status"] == "already_live"),
        "applied_count": len(applied),
        "issues": sorted(set(issues)),
        "items": items,
        "applied": applied,
    }


def _run_strategy_profile_audit(
    service: AnalysisService,
    seasons: list[str],
    roi_tolerance: float,
    clv_tolerance: float,
) -> Any:
    from football_analysis.strategy import audit_strategy_profiles

    return audit_strategy_profiles(
        service.repository,
        configured_profiles=service.settings.strategy_profiles,
        seasons=seasons,
        roi_tolerance=roi_tolerance,
        clv_tolerance=clv_tolerance,
    )


def _default_profile_audit_seasons() -> list[str]:
    return ["2122", "2223", "2324", "2425", "2526"]


def _profile_promotion_strategy_codes_from_data_plan(plan: dict[str, Any]) -> set[str]:
    prefix = "review_and_enable_live_profile:"
    return {
        str(action)[len(prefix):].strip().upper()
        for action in plan.get("user_actions", [])
        if str(action).startswith(prefix) and str(action)[len(prefix):].strip()
    }


def _profile_promotion_targets(
    profiles: Iterable[Any],
    selected_profile_ids: set[str],
    selected_strategy_codes: set[str],
) -> list[Any]:
    targets: list[Any] = []
    for profile in profiles:
        if selected_profile_ids and profile.id not in selected_profile_ids:
            continue
        if selected_strategy_codes and profile.league_code.strip().upper() not in selected_strategy_codes:
            continue
        if not selected_profile_ids and not selected_strategy_codes:
            continue
        targets.append(profile)
    return targets


def _profile_promotion_item(
    profile: Any,
    audit_item: Any | None,
    settings: Any,
    max_stake_units: float | None,
    require_audit: bool,
) -> dict[str, Any]:
    issues: list[str] = []
    if not getattr(profile, "active", True):
        issues.append("profile_inactive")
    audit_payload = None
    if audit_item is not None:
        audit_payload = {"status": audit_item.status, "message": audit_item.message}
    if require_audit:
        if audit_item is None:
            issues.append("profile_audit_missing")
        elif audit_item.status != "matched":
            issues.append(f"profile_audit_not_matched:{audit_item.status}")

    proposed_stake = max_stake_units if max_stake_units is not None else profile.max_stake_units
    if proposed_stake is None:
        issues.append("max_stake_units_required")
    elif proposed_stake <= 0:
        issues.append("max_stake_units_must_be_positive")
    elif proposed_stake > settings.live_trading.max_stake_units_per_pick:
        issues.append(
            "max_stake_units_exceeds_global_per_pick:"
            f"{proposed_stake}/{settings.live_trading.max_stake_units_per_pick}"
        )

    if getattr(profile, "live_enabled", False):
        status = "already_live" if not issues else "blocked"
    else:
        status = "blocked" if issues else "ready"
    proposed = None
    if status in {"ready", "already_live"} or proposed_stake is not None:
        proposed = {"live_enabled": True, "max_stake_units": proposed_stake}
    return {
        "profile_id": profile.id,
        "strategy_code": profile.league_code,
        "status": status,
        "issues": issues,
        "current": {
            "live_enabled": profile.live_enabled,
            "max_stake_units": profile.max_stake_units,
        },
        "proposed": proposed,
        "audit": audit_payload,
    }


def _apply_profile_promotions(config_path: Path, ready_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not config_path.exists():
        raise FileNotFoundError(str(config_path))
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    profiles = raw.get("strategy_profiles")
    if not isinstance(profiles, list):
        raise ValueError("strategy_profiles must be a list")
    ready_by_id = {item["profile_id"]: item for item in ready_items}
    applied: list[dict[str, Any]] = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_id = str(profile.get("id", ""))
        item = ready_by_id.get(profile_id)
        if item is None:
            continue
        proposed = item.get("proposed") or {}
        profile["live_enabled"] = True
        if proposed.get("max_stake_units") is not None:
            profile["max_stake_units"] = proposed["max_stake_units"]
        applied.append(
            {
                "profile_id": profile_id,
                "live_enabled": profile["live_enabled"],
                "max_stake_units": profile.get("max_stake_units"),
            }
        )
    if applied:
        config_path.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=False),
            encoding="utf-8",
        )
    return applied


def build_production_execution_queue(
    service: AnalysisService,
    include_past: bool = False,
    platform: str = "real",
    audit_runner: Callable[..., Any] | None = None,
    league_codes: set[str] | None = None,
) -> dict[str, Any]:
    audit_fn = audit_runner or audit_live_trading
    audit_kwargs: dict[str, Any] = {"include_past": include_past}
    if league_codes and _callable_accepts_keyword(audit_fn, "league_codes"):
        audit_kwargs["league_codes"] = league_codes
    audit = audit_fn(service.repository, service.settings, **audit_kwargs)
    approved_audit_items: dict[tuple[str, str, str], dict[str, Any]] = {}
    for audit_item in getattr(audit, "items", []) or []:
        key = _audit_item_key(audit_item)
        if key is None:
            continue
        if getattr(audit_item, "status", None) != RecommendationStatus.recommended.value:
            continue
        if getattr(audit_item, "live_gate_passed", False) is not True:
            continue
        approved_audit_items[key] = _dump_model(audit_item)
    recommendations = service.repository.list_models("recommendations", Recommendation)
    matches_by_id = {
        match.id: match
        for match in service.repository.list_models("matches", Match)
    }
    bets = service.repository.list_models("bets", BetLog)
    now = datetime.now(service.settings.app.tzinfo)
    candidate_items = [
        item
        for recommendation in recommendations
        if (
            item := _execution_queue_item(
                recommendation,
                matches_by_id,
                bets,
                service.settings,
                now,
                platform,
                include_past,
                audit_item=approved_audit_items.get(_recommendation_queue_key(recommendation)),
            )
        )
        is not None
    ]
    blocked_profileless_candidates = [
        _profileless_queue_candidate_summary(item)
        for item in candidate_items
        if _queue_strategy_profile_matched(item) is not True
        and _queue_tier_policy_passed(item) is not True
    ]
    profileless_count = len(blocked_profileless_candidates)
    items = [
        item
        for item in candidate_items
        if _queue_strategy_profile_matched(item) is True
        or _queue_tier_policy_passed(item) is True
    ]
    items.sort(key=lambda item: (-float(item["value_score"]), item["kickoff_at"], item["match_id"]))
    issues = list(getattr(audit, "issues", []) or [])
    if profileless_count:
        issues.append(f"strategy_profile_required:{profileless_count}")
    if getattr(audit, "status", "no_matches") != "ready":
        status = getattr(audit, "status", "blocked")
        ready_to_execute = False
    elif items:
        status = "ready"
        ready_to_execute = True
    elif candidate_items:
        status = "profile_review_required"
        ready_to_execute = False
    else:
        status = "filled_or_no_open_stake"
        ready_to_execute = False
        if not issues:
            issues.append("no_unfilled_live_gate_passed_candidates")
    return {
        "checked_at": now.isoformat(),
        "status": status,
        "ready_to_execute": ready_to_execute,
        "platform": platform,
        "include_past": include_past,
        "league_codes": sorted(league_codes or []),
        "audit_status": getattr(audit, "status", None),
        "recommended_count": getattr(audit, "recommended_count", 0),
        "total_live_stake_units": getattr(audit, "total_live_stake_units", 0.0),
        "queue_count": len(items),
        "queue_stake_units": round(sum(float(item["remaining_stake_units"]) for item in items), 3),
        "profileless_candidate_count": profileless_count,
        "profileless_candidates": blocked_profileless_candidates,
        "candidate_count": len(candidate_items),
        "issues": issues,
        "items": items,
    }


def build_production_broker_plan(
    service: AnalysisService,
    broker_id: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    audit_runner: Callable[..., Any] | None = None,
    league_codes: set[str] | None = None,
) -> dict[str, Any]:
    settings = service.settings
    broker = getattr(settings, "execution_brokers", {}).get(broker_id)
    queue = build_production_execution_queue(
        service,
        include_past=include_past,
        platform=platform,
        audit_runner=audit_runner,
        league_codes=league_codes,
    )
    checked_at = datetime.now(settings.app.tzinfo).isoformat()
    if broker is None:
        return {
            "checked_at": checked_at,
            "status": "not_configured",
            "broker_id": broker_id,
            "ready_for_broker_execution": False,
            "league_codes": sorted(league_codes or []),
            "issues": [f"broker_not_configured:{broker_id}"],
            "queue": queue,
            "items": [],
        }

    credential_status = {
        env_name: bool(os.getenv(env_name))
        for env_name in broker.credential_envs
    }
    missing_credentials = [
        env_name for env_name, present in credential_status.items() if not present
    ]
    items = [
        _broker_plan_item(item, broker)
        for item in queue.get("items", [])
    ]
    missing_mapping_count = sum(1 for item in items if item["missing_fields"])
    issues: list[str] = []
    if broker.enabled is not True:
        issues.append(f"broker_disabled:{broker_id}")
    for env_name in missing_credentials:
        issues.append(f"missing_broker_credential:{env_name}")
    if broker.stake_currency_per_unit is None:
        issues.append("stake_currency_per_unit_required")
    if queue.get("ready_to_execute") is not True:
        issues.extend(f"queue:{issue}" for issue in queue.get("issues", []))
    if missing_mapping_count:
        issues.append(f"broker_mapping_missing:{missing_mapping_count}")

    if issues:
        status = "blocked"
        ready = False
    elif not items:
        status = "no_open_items"
        ready = False
    else:
        status = "ready"
        ready = True
    return {
        "checked_at": checked_at,
        "status": status,
        "broker_id": broker_id,
        "league_codes": sorted(league_codes or []),
        "broker": {
            "name": broker.name,
            "provider": broker.provider,
            "enabled": broker.enabled,
            "base_url": broker.base_url,
            "official_url": broker.official_url,
            "stake_currency": broker.stake_currency,
            "stake_currency_per_unit": broker.stake_currency_per_unit,
            "credential_status": credential_status,
            "notes": broker.notes,
        },
        "ready_for_broker_execution": ready,
        "queue_status": queue.get("status"),
        "queue_count": queue.get("queue_count", 0),
        "broker_ready_count": sum(1 for item in items if not item["missing_fields"]),
        "missing_mapping_count": missing_mapping_count,
        "issues": issues,
        "queue": queue,
        "items": items,
    }


def build_production_preflight(
    service: AnalysisService,
    broker_id: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    require_broker: bool = False,
    require_execution_queue: bool = False,
    require_health_history: bool = True,
    recent_limit: int = 10,
    max_cycle_age_minutes: int = 90,
    max_data_job_age_minutes: int = 180,
    profile_promotion_audit: bool = False,
    decision_runner: DecisionRunner | None = None,
    audit_runner: Callable[..., Any] | None = None,
    profile_audit_runner: Callable[..., Any] | None = None,
    league_codes: set[str] | None = None,
) -> dict[str, Any]:
    health = build_production_health(
        service,
        recent_limit=recent_limit,
        include_past=include_past,
        max_cycle_age_minutes=max_cycle_age_minutes,
        max_data_job_age_minutes=max_data_job_age_minutes,
        decision_runner=decision_runner,
        execution_queue_runner=lambda svc, include_past=False, league_codes=None: build_production_execution_queue(
            svc,
            include_past=include_past,
            platform=platform,
            audit_runner=audit_runner,
            league_codes=league_codes,
        ),
        league_codes=league_codes,
    )
    data_plan = build_production_data_plan(
        service,
        include_past=include_past,
        league_codes=league_codes,
    )
    broker_plan = build_production_broker_plan(
        service,
        broker_id=broker_id,
        include_past=include_past,
        platform=platform,
        audit_runner=audit_runner,
        league_codes=league_codes,
    )
    queue = (health.get("production_status") or {}).get("execution_queue") or broker_plan.get("queue") or {}
    queue_items = [item for item in queue.get("items", []) if isinstance(item, dict)]
    record_execution_history = _record_execution_history(service, platform=platform)
    profile_matched_queue_count = int(
        queue.get(
            "queue_count",
            sum(1 for item in queue_items if _queue_strategy_profile_matched(item) is True),
        )
        or 0
    )
    profileless_queue_count = int(
        queue.get(
            "profileless_candidate_count",
            sum(1 for item in queue_items if _queue_strategy_profile_matched(item) is False),
        )
        or 0
    )
    tier_policy_queue_count = int(
        queue.get(
            "tier_policy_queue_count",
            sum(1 for item in queue_items if _queue_tier_policy_passed(item) is True),
        )
        or 0
    )
    profile_promotion = _build_preflight_profile_promotion(
        service,
        data_plan,
        require_audit=profile_promotion_audit,
        audit_runner=profile_audit_runner,
    )

    health_status = health.get("status")
    worker_startup_health_ready = (
        health_status == "unhealthy"
        and not require_health_history
        and _production_worker_startup_health_ready(health)
    )
    queue_ready = queue.get("ready_to_execute") is True
    broker_ready = broker_plan.get("ready_for_broker_execution") is True
    record_execution_verified = record_execution_history["recorded_count"] > 0
    ready_for_worker = health_status != "unhealthy" or worker_startup_health_ready
    ready_for_record_execution = ready_for_worker and (
        queue_ready or (record_execution_verified and not require_execution_queue)
    )
    ready_for_broker_execution = ready_for_worker and broker_ready

    issues: list[str] = []
    warnings: list[str] = []
    if health_status == "unhealthy":
        if worker_startup_health_ready:
            warnings.append("production_health_startup_history_missing")
            warnings.extend(f"production_health:{issue}" for issue in health.get("issues", []))
        else:
            issues.append("production_health_unhealthy")
            issues.extend(f"production_health:{issue}" for issue in health.get("issues", []))
    elif health_status == "degraded":
        warnings.append("production_health_degraded")
        warnings.extend(f"production_health:{issue}" for issue in health.get("issues", []))
    warnings.extend(f"production_health:{warning}" for warning in health.get("warnings", []))

    if require_execution_queue and not queue_ready:
        issues.append(f"execution_queue_not_ready:{queue.get('status')}")
        issues.extend(f"execution_queue:{issue}" for issue in queue.get("issues", []))
    elif not queue_ready:
        warnings.append(f"execution_queue_not_ready:{queue.get('status')}")
        warnings.extend(f"execution_queue:{issue}" for issue in queue.get("issues", []))

    if data_plan.get("status") == "action_required":
        warnings.append(f"production_data_plan_action_required:{data_plan.get('task_count', 0)}")

    profile_promotion_status = profile_promotion.get("status")
    if profile_promotion_status in {"blocked", "failed", "error"}:
        warnings.append(f"profile_promotion_not_ready:{profile_promotion_status}")
        warnings.extend(
            f"profile_promotion:{issue}" for issue in profile_promotion.get("issues", [])
        )
    elif profile_promotion_status == "ready":
        warnings.append(f"profile_promotion_ready:{profile_promotion.get('ready_count', 0)}")
    if (
        profile_promotion_status in {"ready", "blocked"}
        and profile_promotion.get("require_audit") is False
    ):
        warnings.append("profile_promotion_audit_not_checked")

    if require_broker and not broker_ready:
        issues.append(f"broker_not_ready:{broker_plan.get('status')}")
        issues.extend(f"broker:{issue}" for issue in broker_plan.get("issues", []))
    elif not broker_ready:
        warnings.append(f"broker_not_ready:{broker_plan.get('status')}")

    if issues:
        status = "blocked"
        action = "fix_blocking_issues"
    elif warnings:
        status = "degraded"
        action = "run_production_worker_with_review"
    elif require_broker:
        status = "ready"
        action = "run_production_worker_with_broker"
    elif queue_ready:
        status = "ready"
        action = "run_production_worker_record_only_or_dry_run"
    else:
        status = "ready"
        action = "run_production_worker_observe"

    return {
        "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
        "status": status,
        "action": action,
        "league_codes": sorted(league_codes or []),
        "require_broker": require_broker,
        "require_execution_queue": require_execution_queue,
        "require_health_history": require_health_history,
        "worker_startup_health_ready": worker_startup_health_ready,
        "ready_for_worker": ready_for_worker,
        "ready_for_record_execution": ready_for_record_execution,
        "ready_for_broker_execution": ready_for_broker_execution,
        "issues": sorted(set(issues)),
        "warnings": sorted(set(warnings)),
        "health": {
            "status": health_status,
            "ready_to_bet": health.get("ready_to_bet"),
            "action": health.get("action"),
            "issues": health.get("issues", []),
            "job_health": health.get("job_health", {}),
        },
        "execution_queue": {
            "status": queue.get("status"),
            "ready_to_execute": queue_ready,
            "queue_count": queue.get("queue_count", 0),
            "queue_stake_units": queue.get("queue_stake_units", 0),
            "profile_matched_queue_count": profile_matched_queue_count,
            "profileless_queue_count": profileless_queue_count,
            "profileless_candidates": queue.get("profileless_candidates", []),
            "tier_policy_queue_count": tier_policy_queue_count,
            "issues": queue.get("issues", []),
        },
        "record_execution_history": record_execution_history,
        "broker_plan": {
            "status": broker_plan.get("status"),
            "broker_id": broker_plan.get("broker_id"),
            "ready_for_broker_execution": broker_ready,
            "queue_count": broker_plan.get("queue_count", 0),
            "broker_ready_count": broker_plan.get("broker_ready_count", 0),
            "missing_mapping_count": broker_plan.get("missing_mapping_count", 0),
            "issues": broker_plan.get("issues", []),
        },
        "data_plan": {
            "status": data_plan.get("status"),
            "readiness_status": data_plan.get("readiness_status"),
            "task_count": data_plan.get("task_count", 0),
            "user_action_count": data_plan.get("user_action_count", 0),
            "local_command_count": data_plan.get("local_command_count", 0),
            "user_actions": data_plan.get("user_actions", []),
            "local_commands": data_plan.get("local_commands", []),
        },
        "profile_promotion": {
            "status": profile_promotion.get("status"),
            "apply": profile_promotion.get("apply"),
            "config_path": profile_promotion.get("config_path"),
            "require_audit": profile_promotion.get("require_audit"),
            "audit_passed": profile_promotion.get("audit_passed"),
            "selected_profile_ids": profile_promotion.get("selected_profile_ids", []),
            "selected_strategy_codes": profile_promotion.get("selected_strategy_codes", []),
            "max_stake_units": profile_promotion.get("max_stake_units"),
            "ready_count": profile_promotion.get("ready_count", 0),
            "blocked_count": profile_promotion.get("blocked_count", 0),
            "already_live_count": profile_promotion.get("already_live_count", 0),
            "applied_count": profile_promotion.get("applied_count", 0),
            "issues": profile_promotion.get("issues", []),
            "items": profile_promotion.get("items", []),
        },
    }


def _queue_strategy_profile_matched(item: dict[str, Any]) -> bool | None:
    gate_evidence = _dict_payload(item.get("gate_evidence"))
    strategy_profile = _dict_payload(gate_evidence.get("strategy_profile"))
    matched = strategy_profile.get("matched")
    return matched if isinstance(matched, bool) else None


def _record_execution_history(service: AnalysisService, platform: str = "real") -> dict[str, Any]:
    bets = service.repository.list_models("bets", BetLog)
    production_bets = [
        bet
        for bet in bets
        if str(getattr(bet, "platform", "")) == platform
        and str(getattr(bet, "id", "")).startswith("production-execution:")
    ]
    production_bets.sort(key=lambda bet: getattr(bet, "placed_at", datetime.min), reverse=True)
    latest = production_bets[0] if production_bets else None
    return {
        "platform": platform,
        "recorded_count": len(production_bets),
        "latest_bet": latest.model_dump(mode="json") if latest is not None else None,
    }


def _queue_tier_policy_passed(item: dict[str, Any]) -> bool | None:
    gate_evidence = _dict_payload(item.get("gate_evidence"))
    tier_policy = _dict_payload(gate_evidence.get("tier_policy"))
    passed = tier_policy.get("passed")
    return passed if isinstance(passed, bool) else None


def _build_preflight_profile_promotion(
    service: AnalysisService,
    data_plan: dict[str, Any],
    require_audit: bool = False,
    audit_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    strategy_codes = _profile_promotion_strategy_codes_from_data_plan(data_plan)
    if not strategy_codes:
        return {
            "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
            "status": "not_required",
            "apply": False,
            "config_path": str(Path(os.getenv("FOOTBALL_CONFIG", "config/default.yaml"))),
            "require_audit": require_audit,
            "audit_passed": None,
            "selected_profile_ids": [],
            "selected_strategy_codes": [],
            "max_stake_units": None,
            "ready_count": 0,
            "blocked_count": 0,
            "already_live_count": 0,
            "applied_count": 0,
            "issues": [],
            "items": [],
            "applied": [],
        }
    try:
        return build_production_profile_promotion_plan(
            service,
            strategy_codes=strategy_codes,
            max_stake_units=_suggested_profile_max_stake_units(service.settings),
            apply_changes=False,
            require_audit=require_audit,
            audit_runner=audit_runner,
        )
    except Exception as exc:  # pragma: no cover - defensive for operator preflight
        return {
            "checked_at": datetime.now(service.settings.app.tzinfo).isoformat(),
            "status": "error",
            "apply": False,
            "config_path": str(Path(os.getenv("FOOTBALL_CONFIG", "config/default.yaml"))),
            "require_audit": require_audit,
            "audit_passed": None,
            "selected_profile_ids": [],
            "selected_strategy_codes": sorted(strategy_codes),
            "max_stake_units": None,
            "ready_count": 0,
            "blocked_count": len(strategy_codes),
            "already_live_count": 0,
            "applied_count": 0,
            "issues": [f"profile_promotion_check_failed:{type(exc).__name__}:{exc}"],
            "items": [],
            "applied": [],
        }


def run_production_broker_execution(
    service: AnalysisService,
    broker_id: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    execute_broker_orders: bool = False,
    max_items: int | None = None,
    audit_runner: Callable[..., Any] | None = None,
    request_sender: Callable[..., Any] | None = None,
    request_timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    plan = build_production_broker_plan(
        service,
        broker_id=broker_id,
        include_past=include_past,
        platform=platform,
        audit_runner=audit_runner,
    )
    from football_analysis.brokers import execute_broker_plan

    return execute_broker_plan(
        plan,
        execute_broker_orders=execute_broker_orders,
        max_items=max_items,
        request_sender=request_sender,
        request_timeout_seconds=request_timeout_seconds,
    )


def run_production_broker_discovery(
    service: AnalysisService,
    broker_id: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    fetch_remote: bool = False,
    apply_mappings: bool = False,
    max_items: int | None = None,
    max_results: int = 20,
    match_window_hours: int = 36,
    min_apply_confidence: str = "high",
    audit_runner: Callable[..., Any] | None = None,
    request_sender: Callable[..., Any] | None = None,
    request_timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    plan = build_production_broker_plan(
        service,
        broker_id=broker_id,
        include_past=include_past,
        platform=platform,
        audit_runner=audit_runner,
    )
    from football_analysis.brokers import discover_broker_mappings

    report = discover_broker_mappings(
        plan,
        fetch_remote=fetch_remote,
        max_items=max_items,
        request_sender=request_sender,
        request_timeout_seconds=request_timeout_seconds,
        max_results=max_results,
        match_window_hours=match_window_hours,
    )
    if apply_mappings:
        report["applied_mappings"] = _apply_broker_discovery_mappings(
            service,
            report,
            min_confidence=min_apply_confidence,
        )
    else:
        report["applied_mappings"] = []
    return report


def _apply_broker_discovery_mappings(
    service: AnalysisService,
    report: dict[str, Any],
    min_confidence: str = "high",
) -> list[dict[str, Any]]:
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    threshold = confidence_order.get(min_confidence, 0)
    applied: list[dict[str, Any]] = []
    for record in report.get("records", []):
        candidates = list(record.get("candidates") or [])
        if not candidates:
            continue
        candidate = candidates[0]
        confidence = str(candidate.get("confidence") or "low")
        if confidence_order.get(confidence, 99) > threshold:
            applied.append(
                {
                    "match_id": record.get("match_id"),
                    "status": "skipped",
                    "reason": f"confidence_below_threshold:{confidence}",
                }
            )
            continue
        match_id = str(record.get("match_id") or "")
        match = service.repository.get_model("matches", match_id, Match)
        if match is None:
            applied.append(
                {
                    "match_id": match_id,
                    "status": "error",
                    "reason": "match_not_found",
                }
            )
            continue
        patch = dict(candidate.get("external_ids_patch") or {})
        if not patch:
            continue
        external_ids = {**dict(match.external_ids), **patch}
        updated = match.model_copy(update={"external_ids": external_ids})
        service.repository.upsert_model("matches", updated.id, updated)
        applied.append(
            {
                "match_id": match_id,
                "status": "applied",
                "confidence": confidence,
                "external_ids_patch": patch,
            }
        )
    return applied


def _broker_plan_item(item: dict[str, Any], broker: Any) -> dict[str, Any]:
    match_external_ids = dict(item.get("match_external_ids") or {})
    normalized_selection = item["normalized_selection"]
    match_refs, missing_match = _broker_match_refs(match_external_ids, broker)
    selection_refs, missing_selection = _broker_selection_refs(
        match_external_ids,
        broker,
        normalized_selection,
        item["selection"],
    )
    stake_currency_amount = None
    if broker.stake_currency_per_unit is not None:
        stake_currency_amount = round(
            float(item["remaining_stake_units"]) * float(broker.stake_currency_per_unit),
            2,
        )
    missing_fields = [
        *missing_match,
        *missing_selection,
    ]
    if stake_currency_amount is None:
        missing_fields.append("stake_currency_per_unit")
    order_payload = None
    if not missing_fields:
        order_payload = {
            "broker_id": broker.provider,
            "market_id": match_refs["market_id"],
            "selection_id": selection_refs["selection_id"],
            "side": "BACK",
            "order_type": "LIMIT",
            "limit_price": item["minimum_execution_odds"],
            "size": stake_currency_amount,
            "currency": broker.stake_currency,
            "customer_order_ref": item["idempotency_key"],
        }
        if "handicap" in selection_refs:
            order_payload["handicap"] = selection_refs["handicap"]
    return {
        "idempotency_key": item["idempotency_key"],
        "recommendation_id": item["recommendation_id"],
        "match_id": item["match_id"],
        "home_team": item["home_team"],
        "away_team": item["away_team"],
        "market_type": item["market_type"],
        "selection": item["selection"],
        "normalized_selection": normalized_selection,
        "minimum_execution_odds": item["minimum_execution_odds"],
        "remaining_stake_units": item["remaining_stake_units"],
        "stake_currency_amount": stake_currency_amount,
        "match_external_ids": match_external_ids,
        "broker_refs": {**match_refs, **selection_refs},
        "missing_fields": missing_fields,
        "order_payload": order_payload,
    }


def _broker_match_refs(match_external_ids: dict[str, str], broker: Any) -> tuple[dict[str, str], list[str]]:
    refs: dict[str, str] = {}
    missing: list[str] = []
    for field in broker.required_match_external_ids:
        value = match_external_ids.get(field)
        if not value:
            missing.append(field)
            continue
        if field.endswith("market_id"):
            refs["market_id"] = value
        else:
            refs[field] = value
    return refs, missing


def _broker_selection_refs(
    match_external_ids: dict[str, str],
    broker: Any,
    normalized_selection: str,
    selection: str,
) -> tuple[dict[str, str], list[str]]:
    refs: dict[str, str] = {}
    missing: list[str] = []
    selection_keys = [
        f"betfair_selection_id_{_broker_key(normalized_selection)}",
        f"betfair_selection_id_{_broker_key(selection)}",
        f"selection_id_{_broker_key(normalized_selection)}",
        f"selection_id_{_broker_key(selection)}",
    ]
    for field in broker.required_selection_external_ids:
        value = match_external_ids.get(field)
        if not value:
            value = next((match_external_ids.get(key) for key in selection_keys if match_external_ids.get(key)), None)
        if not value:
            missing.append(f"{field}:{normalized_selection}")
            continue
        if field.endswith("selection_id"):
            refs["selection_id"] = value
        else:
            refs[field] = value
    handicap_keys = [
        f"betfair_handicap_{_broker_key(normalized_selection)}",
        f"betfair_handicap_{_broker_key(selection)}",
        f"handicap_{_broker_key(normalized_selection)}",
        f"handicap_{_broker_key(selection)}",
    ]
    handicap = next((match_external_ids.get(key) for key in handicap_keys if match_external_ids.get(key)), None)
    if handicap:
        refs["handicap"] = handicap
    return refs, missing


def _broker_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")


def run_production_execution(
    service: AnalysisService,
    include_past: bool = False,
    platform: str = "real",
    execute_records: bool = False,
    max_items: int | None = None,
    fills: dict[str, Any] | None = None,
    require_fills: bool = False,
    audit_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    queue = build_production_execution_queue(
        service,
        include_past=include_past,
        platform=platform,
        audit_runner=audit_runner,
    )
    queue_items = list(queue.get("items", []))
    if max_items is not None:
        queue_items = queue_items[: max(0, max_items)]
    checked_at = datetime.now(service.settings.app.tzinfo).isoformat()
    records: list[dict[str, Any]] = []
    if queue.get("ready_to_execute") is not True:
        return {
        "checked_at": checked_at,
        "status": "blocked",
        "mode": "record_only" if execute_records else "dry_run",
        "execute_records": execute_records,
        "require_fills": require_fills,
        "platform": platform,
            "queue_status": queue.get("status"),
            "queue_count": queue.get("queue_count", 0),
            "selected_count": 0,
            "recorded_count": 0,
            "dry_run_count": 0,
            "error_count": 0,
            "issues": list(queue.get("issues", [])),
            "queue": queue,
            "records": records,
        }
    for item in queue_items:
        fill = _execution_fill_for_item(fills, item)
        if not execute_records:
            records.append(_execution_dry_run_record(item, fill=fill))
            continue
        records.append(_record_execution_item(service, item, fill=fill, require_fill=require_fills))
    error_count = sum(1 for record in records if record["status"] == "error")
    recorded_count = sum(1 for record in records if record["status"] == "recorded")
    dry_run_count = sum(1 for record in records if record["status"] == "dry_run")
    if not records:
        status = "no_open_items"
    elif error_count:
        status = "partial_error" if recorded_count or dry_run_count else "error"
    elif execute_records:
        status = "executed"
    else:
        status = "dry_run"
    return {
        "checked_at": checked_at,
        "status": status,
        "mode": "record_only" if execute_records else "dry_run",
        "execute_records": execute_records,
        "require_fills": require_fills,
        "platform": platform,
        "queue_status": queue.get("status"),
        "queue_count": queue.get("queue_count", 0),
        "selected_count": len(queue_items),
        "recorded_count": recorded_count,
        "dry_run_count": dry_run_count,
        "error_count": error_count,
        "issues": [],
        "queue": queue,
        "records": records,
    }


def _normalize_execution_mode(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"off", "disabled", "none"}:
        return "off"
    if normalized in {"dry-run", "dryrun", "plan"}:
        return "dry-run"
    if normalized in {"record-only", "record", "ledger"}:
        return "record-only"
    raise ValueError(f"unsupported_execution_mode:{value}")


def _normalize_data_apply_mode(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"off", "disabled", "none"}:
        return "off"
    if normalized in {"dry-run", "dryrun", "plan", "preview"}:
        return "dry-run"
    if normalized in {"safe", "local", "execute-safe"}:
        return "safe"
    if normalized in {"remote", "all", "execute-remote"}:
        return "remote"
    raise ValueError(f"unsupported_data_apply_mode:{value}")


def _normalize_broker_discovery_mode(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"off", "disabled", "none"}:
        return "off"
    if normalized in {"dry-run", "dryrun", "plan", "preview"}:
        return "dry-run"
    if normalized in {"remote", "fetch", "read-only", "readonly"}:
        return "remote"
    if normalized in {"apply", "apply-mappings", "remote-apply"}:
        return "apply"
    raise ValueError(f"unsupported_broker_discovery_mode:{value}")


def _normalize_broker_execution_mode(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"off", "disabled", "none"}:
        return "off"
    if normalized in {"dry-run", "dryrun", "plan", "preview"}:
        return "dry-run"
    if normalized in {"live", "execute", "send", "broker-live"}:
        return "live"
    raise ValueError(f"unsupported_broker_execution_mode:{value}")


def _execution_fill_for_item(
    fills: dict[str, Any] | None,
    item: dict[str, Any],
) -> dict[str, Any] | None:
    if not fills:
        return None
    fill = fills.get(item["idempotency_key"]) or fills.get(item["recommendation_id"])
    return dict(fill) if isinstance(fill, dict) else None


def _execution_dry_run_record(
    item: dict[str, Any],
    fill: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "dry_run",
        "idempotency_key": item["idempotency_key"],
        "recommendation_id": item["recommendation_id"],
        "match_id": item["match_id"],
        "market_type": item["market_type"],
        "selection": item["selection"],
        "odds": item["approved_odds"],
        "stake_units": item["remaining_stake_units"],
        "platform": item["platform"],
        "record_bet_command": item["record_bet_command"],
        "record_bet_argv": item["record_bet_argv"],
        "fill": fill,
    }


def _record_execution_item(
    service: AnalysisService,
    item: dict[str, Any],
    fill: dict[str, Any] | None = None,
    require_fill: bool = False,
) -> dict[str, Any]:
    if require_fill and fill is None:
        return {
            "status": "error",
            "idempotency_key": item["idempotency_key"],
            "recommendation_id": item["recommendation_id"],
            "match_id": item["match_id"],
            "market_type": item["market_type"],
            "selection": item["selection"],
            "platform": item["platform"],
            "error": "execution_fill_required",
        }
    odds = float((fill or {}).get("odds", item["approved_odds"]))
    stake_units = float((fill or {}).get("stake_units", item["remaining_stake_units"]))
    platform = str((fill or {}).get("platform", item["platform"]))
    external_bet_id = (fill or {}).get("external_bet_id")
    fill_note = f" external_bet_id={external_bet_id}" if external_bet_id else ""
    bet = BetLog(
        id=item["idempotency_key"],
        match_id=item["match_id"],
        market_type=item["market_type"],
        selection=item["selection"],
        odds=odds,
        stake_units=stake_units,
        platform=platform,
        notes=(
            f"production_execution idempotency_key={item['idempotency_key']} "
            f"recommendation_id={item['recommendation_id']}{fill_note}"
        ),
    )
    try:
        recorded = service.record_bet(bet)
    except ValueError as exc:
        return {
            "status": "error",
            "idempotency_key": item["idempotency_key"],
            "recommendation_id": item["recommendation_id"],
            "match_id": item["match_id"],
            "market_type": item["market_type"],
            "selection": item["selection"],
            "odds": odds,
            "stake_units": stake_units,
            "platform": platform,
            "error": str(exc),
        }
    return {
        "status": "recorded",
        "idempotency_key": item["idempotency_key"],
        "recommendation_id": item["recommendation_id"],
        "bet": recorded.model_dump(mode="json"),
    }


def _execution_queue_item(
    recommendation: Recommendation,
    matches_by_id: dict[str, Match],
    bets: list[BetLog],
    settings: Any,
    now: datetime,
    platform: str,
    include_past: bool,
    audit_item: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if audit_item is None:
        return None
    if _status_value(recommendation.status) != RecommendationStatus.recommended.value:
        return None
    market_type = _market_type_value(recommendation.market_type)
    selection = recommendation.selection or ""
    if not market_type or not selection:
        return None
    live_gate = _dict_payload(recommendation.score_breakdown.get("live_gate"))
    if live_gate.get("passed") is not True:
        return None
    match = matches_by_id.get(recommendation.match_id)
    if match is None:
        return None
    if not include_past and _kickoff_has_started(match.kickoff_at, now):
        return None
    approved_odds = _approved_odds(recommendation)
    if approved_odds is None:
        return None
    approved_stake_units = float(recommendation.stake_units)
    if approved_stake_units <= 0:
        return None
    existing_real_stake_units = _existing_real_stake_units(recommendation, bets)
    remaining_stake_units = approved_stake_units - existing_real_stake_units
    if remaining_stake_units <= 1e-9:
        return None

    minimum_execution_odds = approved_odds * (
        1.0 - settings.live_trading.max_execution_odds_slippage
    )
    expires_at = match.kickoff_at - timedelta(minutes=10)
    remaining_stake_units = round(remaining_stake_units, 3)
    approved_odds = round(approved_odds, 3)
    minimum_execution_odds = round(minimum_execution_odds, 3)
    record_bet = _record_bet_command(
        match_id=match.id,
        market_type=market_type,
        selection=selection,
        odds=approved_odds,
        stake_units=remaining_stake_units,
        platform=platform,
    )
    strategy_profile = _dict_payload(
        recommendation.score_breakdown.get("strategy_profile")
        or recommendation.odds_basis.get("strategy_profile")
    )
    tier_policy = _dict_payload(
        recommendation.score_breakdown.get("tier_policy")
        or recommendation.odds_basis.get("tier_policy")
    )
    strategy_confidence_class = (
        recommendation.score_breakdown.get("strategy_confidence_class")
        or recommendation.odds_basis.get("strategy_confidence_class")
    )

    return {
        "idempotency_key": _execution_key(recommendation, market_type, selection, platform),
        "status": "open",
        "recommendation_id": recommendation.id,
        "match_id": match.id,
        "league": match.league,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "kickoff_at": match.kickoff_at.isoformat(),
        "match_external_ids": dict(match.external_ids),
        "market_type": market_type,
        "selection": selection,
        "normalized_selection": _normalized_strategy_selection(selection, market_type),
        "approved_odds": approved_odds,
        "minimum_execution_odds": minimum_execution_odds,
        "max_execution_odds_slippage": settings.live_trading.max_execution_odds_slippage,
        "expires_at": expires_at.isoformat(),
        "mutual_exclusion_tag": match.id,
        "correlation_group": live_gate.get("correlation_group"),
        "kelly_fraction": live_gate.get("kelly_fraction"),
        "kelly_stake_units": live_gate.get("kelly_stake_units"),
        "portfolio_adjusted": bool(live_gate.get("portfolio_adjusted")),
        "portfolio_reason": live_gate.get("portfolio_reason"),
        "approved_stake_units": round(approved_stake_units, 3),
        "existing_real_stake_units": round(existing_real_stake_units, 3),
        "remaining_stake_units": remaining_stake_units,
        "platform": platform,
        "value_score": recommendation.value_score,
        "risk_score": recommendation.risk_score,
        "confidence": recommendation.confidence,
        "odds_source": recommendation.odds_basis.get("source"),
        "bookmaker": recommendation.odds_basis.get("bookmaker"),
        "reason": recommendation.reason,
        "gate_evidence": {
            "live_gate": live_gate,
            "strategy_profile": strategy_profile,
            "strategy_confidence_class": strategy_confidence_class,
            "tier_policy": tier_policy,
            "audit_item": audit_item,
        },
        "record_bet_command": record_bet["command"],
        "record_bet_argv": record_bet["argv"],
    }


def _profileless_queue_candidate_summary(item: dict[str, Any]) -> dict[str, Any]:
    gate_evidence = _dict_payload(item.get("gate_evidence"))
    strategy_profile = _dict_payload(gate_evidence.get("strategy_profile"))
    live_gate = _dict_payload(gate_evidence.get("live_gate"))
    tier_policy = _dict_payload(gate_evidence.get("tier_policy"))
    return {
        "recommendation_id": item.get("recommendation_id"),
        "match_id": item.get("match_id"),
        "league": item.get("league"),
        "home_team": item.get("home_team"),
        "away_team": item.get("away_team"),
        "kickoff_at": item.get("kickoff_at"),
        "market_type": item.get("market_type"),
        "selection": item.get("selection"),
        "normalized_selection": item.get("normalized_selection"),
        "approved_odds": item.get("approved_odds"),
        "remaining_stake_units": item.get("remaining_stake_units"),
        "value_score": item.get("value_score"),
        "risk_score": item.get("risk_score"),
        "confidence": item.get("confidence"),
        "odds_source": item.get("odds_source"),
        "bookmaker": item.get("bookmaker"),
        "strategy_profile_matched": strategy_profile.get("matched"),
        "profileless_live_allowed": live_gate.get("profileless_live_allowed"),
        "tier_policy_label": tier_policy.get("label"),
        "review_reason": "strategy_profile_required",
    }


def _audit_item_key(item: Any) -> tuple[str, str, str] | None:
    match_id = getattr(item, "match_id", None)
    market_type = getattr(item, "market_type", None)
    selection = getattr(item, "selection", None)
    if not match_id or not market_type or not selection:
        return None
    market_value = _market_type_value(market_type)
    return (
        str(match_id),
        market_value,
        _normalized_strategy_selection(str(selection), market_value),
    )


def _recommendation_queue_key(recommendation: Recommendation) -> tuple[str, str, str] | None:
    market_type = _market_type_value(recommendation.market_type)
    if not market_type or not recommendation.selection:
        return None
    return (
        recommendation.match_id,
        market_type,
        _normalized_strategy_selection(recommendation.selection, market_type),
    )


def _existing_real_stake_units(recommendation: Recommendation, bets: list[BetLog]) -> float:
    market_type = _market_type_value(recommendation.market_type)
    if not market_type or not recommendation.selection:
        return 0.0
    normalized_selection = _normalized_strategy_selection(recommendation.selection, market_type)
    total = 0.0
    for bet in bets:
        if bet.match_id != recommendation.match_id:
            continue
        if _market_type_value(bet.market_type) != market_type:
            continue
        if _is_paper_platform(bet.platform):
            continue
        if _normalized_strategy_selection(bet.selection, market_type) != normalized_selection:
            continue
        total += float(bet.stake_units)
    return round(total, 3)


def _record_bet_command(
    match_id: str,
    market_type: str,
    selection: str,
    odds: float,
    stake_units: float,
    platform: str,
) -> dict[str, Any]:
    argv = [
        "footballctl",
        "record-bet",
        match_id,
        market_type,
        selection,
        f"{odds:.3f}",
        f"{stake_units:.3f}",
        platform,
        "--json",
    ]
    return {
        "argv": argv,
        "command": " ".join(["footballctl", *(_powershell_quote_arg(arg) for arg in argv[1:])]),
    }


def _execution_key(
    recommendation: Recommendation,
    market_type: str,
    selection: str,
    platform: str,
) -> str:
    raw = "\x1f".join(
        [
            platform,
            recommendation.match_id,
            market_type,
            _normalized_strategy_selection(selection, market_type),
            recommendation.id,
            f"{recommendation.stake_units:.3f}",
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"production-execution:{digest}"


def _powershell_quote_arg(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _status_value(status: Any) -> str:
    return str(getattr(status, "value", status))


def _market_type_value(market_type: Any) -> str:
    return str(getattr(market_type, "value", market_type or ""))


def _dict_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _kickoff_has_started(kickoff_at: datetime, now: datetime) -> bool:
    current = now
    kickoff = kickoff_at
    if current.tzinfo is None and kickoff.tzinfo is not None:
        current = current.replace(tzinfo=kickoff.tzinfo)
    elif current.tzinfo is not None and kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=current.tzinfo)
    return current >= kickoff


def _default_decision_runner(
    service: AnalysisService,
    include_past: bool = False,
    league_codes: set[str] | None = None,
    require_strategy_profiles: bool = True,
) -> Any:
    return run_live_decision(
        service.repository,
        service.settings,
        include_past=include_past,
        league_codes=league_codes,
        require_strategy_profiles=require_strategy_profiles,
    )


def _default_refresh_runner(**kwargs: Any) -> Any:
    return run_live_refresh(**kwargs)


def _callable_accepts_keyword(fn: Callable[..., Any], keyword: str) -> bool:
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == keyword
        for parameter in parameters
    )


def _default_daily_ops_runner(service: AnalysisService, run_date: date) -> Any:
    return run_daily_ops(
        service,
        date=run_date.isoformat(),
        ingest_results=False,
    )


def format_production_alert(report: ProductionCycleReport, max_issues: int = 5) -> str:
    issue_text = "; ".join(report.issues[:max_issues]) or "none"
    if len(report.issues) > max_issues:
        issue_text = f"{issue_text}; ...(+{len(report.issues) - max_issues})"
    queue = report.execution_queue or {}
    queue_status = queue.get("status", "not_checked")
    queue_count = queue.get("queue_count", 0)
    queue_stake_units = queue.get("queue_stake_units", 0.0)
    data_apply = report.data_apply or {}
    data_apply_text = (
        f"{data_apply.get('status', 'not_run')}:"
        f"selected={data_apply.get('selected_count', 0)}:"
        f"ok={data_apply.get('succeeded_count', 0)}:"
        f"failed={data_apply.get('failed_count', 0)}:"
        f"remote={str(data_apply.get('allow_remote', False)).lower()}"
    )
    analysis_advice = report.analysis_advice or {}
    analysis_text = (
        f"{analysis_advice.get('status', 'not_run')}:"
        f"picks={analysis_advice.get('pick_count', 0)}:"
        f"analyses={analysis_advice.get('analysis_count', 0)}"
    )
    execution = report.execution or {}
    execution_text = (
        f"{execution.get('mode', 'off')}:"
        f"{execution.get('status', 'not_run')}:"
        f"recorded={execution.get('recorded_count', 0)}:"
        f"dry_run={execution.get('dry_run_count', 0)}:"
        f"errors={execution.get('error_count', 0)}"
    )
    broker_discovery = report.broker_discovery or {}
    broker_discovery_text = (
        f"{broker_discovery.get('mode', 'off')}:"
        f"{broker_discovery.get('status', 'not_run')}:"
        f"selected={broker_discovery.get('selected_count', 0)}:"
        f"discovered={broker_discovery.get('discovered_count', 0)}:"
        f"errors={broker_discovery.get('error_count', 0)}"
    )
    broker_execution = report.broker_execution or {}
    broker_execution_text = (
        f"{broker_execution.get('mode', 'off')}:"
        f"{broker_execution.get('status', 'not_run')}:"
        f"sent={broker_execution.get('sent_count', 0)}:"
        f"dry_run={broker_execution.get('dry_run_count', 0)}:"
        f"errors={broker_execution.get('error_count', 0)}"
    )
    return "\n".join(
        [
            "football-analysis production",
            f"date={report.date}",
            f"status={report.status}",
            f"ready_to_bet={str(report.ready_to_bet).lower()}",
            f"action={report.action}",
            f"refresh_mode={report.refresh_mode}",
            f"refresh_dry_run={str(report.refresh_dry_run).lower()}",
            f"leagues={','.join(report.leagues)}",
            f"fixtures={_result_count(report.fixture_results)}",
            f"odds={_result_count(report.odds_results)}",
            f"results={_result_count(report.result_results)}",
            f"analysis_advice={analysis_text}",
            f"data_apply={data_apply_text}",
            f"execution_queue={queue_status}:{queue_count}:{queue_stake_units}u",
            f"execution={execution_text}",
            f"broker_discovery={broker_discovery_text}",
            f"broker_execution={broker_execution_text}",
            f"issues={issue_text}",
            "",
            format_analysis_advice_alert(analysis_advice) if analysis_advice else "football-analysis advice\nstatus=not_run",
        ]
    )


def send_telegram_alert(
    text: str,
    bot_token: str | None,
    chat_id: str | None,
    timeout_seconds: float = 10.0,
    request_fn: Callable[[str, dict[str, object], float], dict[str, Any]] | None = None,
) -> TelegramAlertResult:
    token = (bot_token or "").strip()
    target_chat = (chat_id or "").strip()
    if not token or not target_chat:
        return TelegramAlertResult(enabled=False, sent=False, skipped_reason="missing_credentials")

    payload: dict[str, object] = {
        "chat_id": target_chat,
        "text": text,
        "disable_web_page_preview": True,
    }
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requester = request_fn or _post_json
    try:
        response = requester(url, payload, timeout_seconds)
    except Exception as exc:  # pragma: no cover - exercised through fake requesters in verification.
        return TelegramAlertResult(enabled=True, sent=False, error=type(exc).__name__)

    ok = bool(response.get("ok", False))
    status_code = response.get("status_code")
    return TelegramAlertResult(
        enabled=True,
        sent=ok,
        status_code=status_code if isinstance(status_code, int) else None,
        error=None if ok else str(response.get("description") or "telegram_send_failed"),
    )


def _post_json(url: str, payload: dict[str, object], timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            if isinstance(data, dict):
                data.setdefault("status_code", response.status)
                return data
            return {"ok": False, "status_code": response.status, "description": "unexpected_response"}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"description": raw[:200]}
        if isinstance(data, dict):
            data.setdefault("ok", False)
            data["status_code"] = exc.code
            return data
        return {"ok": False, "status_code": exc.code, "description": "http_error"}


def _result_count(results: list[IngestionResult]) -> str:
    inserted = sum(result.inserted for result in results)
    updated = sum(result.updated for result in results)
    errors = sum(len(result.errors) for result in results)
    return f"inserted:{inserted},updated:{updated},errors:{errors}"


def _run_auto_refresh_reports(
    refresh_fn: RefreshRunner,
    service: AnalysisService,
    day: str,
    leagues: list[str],
    fixture_source: str,
    odds_source: str,
    refresh_scope: str,
    max_events: int | None,
    include_past: bool,
    allow_odds_fallback: bool,
    expand_live_leagues_on_empty: bool,
    refresh_dry_run: bool,
) -> list[Any]:
    requested_leagues = [
        league
        for league in leagues
        if league.strip() and league.strip().lower() not in {"auto", "*"}
    ]
    if not requested_leagues:
        report = refresh_fn(
            service=service,
            date=day,
            fixture_source=fixture_source,
            odds_source=odds_source,
            scope=refresh_scope,
            max_events=max_events,
            include_past=include_past,
            dry_run=refresh_dry_run,
            allow_odds_fallback=allow_odds_fallback,
        )
        reports = [report]
        if _should_expand_live_leagues(report, refresh_scope, expand_live_leagues_on_empty):
            reports.append(
                refresh_fn(
                    service=service,
                    date=day,
                    fixture_source=fixture_source,
                    odds_source=odds_source,
                    scope="live-leagues",
                    max_events=max_events,
                    include_past=include_past,
                    dry_run=refresh_dry_run,
                    allow_odds_fallback=False,
                )
            )
        return reports
    return [
        refresh_fn(
            service=service,
            date=day,
            fixture_source=fixture_source,
            odds_source=odds_source,
            league=league,
            scope=refresh_scope,
            max_events=max_events,
            include_past=include_past,
            dry_run=refresh_dry_run,
            allow_odds_fallback=allow_odds_fallback,
        )
        for league in requested_leagues
    ]


def _should_expand_live_leagues(report: Any, refresh_scope: str, enabled: bool) -> bool:
    if not enabled or refresh_scope.strip().lower() != "active-profiles":
        return False
    return "consider_scope_live_leagues" in set(getattr(report, "issues", []) or [])


def _refresh_target_leagues(refresh_reports: list[Any]) -> list[str]:
    seen: set[str] = set()
    target_leagues: list[str] = []
    for report in refresh_reports:
        for league in getattr(report, "leagues", []) or []:
            if league not in seen:
                seen.add(league)
                target_leagues.append(str(league))
    return target_leagues


def _refresh_reports_summary(refresh_reports: list[Any]) -> dict[str, Any]:
    return {"reports": [_refresh_report_summary(report) for report in refresh_reports]}


def _refresh_report_summary(report: Any) -> dict[str, Any]:
    return {
        "date": getattr(report, "date", None),
        "scope": getattr(report, "scope", None),
        "fixture_source": getattr(report, "fixture_source", None),
        "odds_source": getattr(report, "odds_source", None),
        "requested_league": getattr(report, "requested_league", None),
        "dry_run": bool(getattr(report, "dry_run", False)),
        "leagues": [str(league) for league in getattr(report, "leagues", []) or []],
        "status": getattr(report, "status", None),
        "ready_to_bet": bool(getattr(report, "ready_to_bet", False)),
        "action": getattr(report, "action", None),
        "operations": [_dump_model(operation) for operation in getattr(report, "operations", []) or []],
        "refresh_requirements": [
            _dump_model(requirement)
            for requirement in getattr(report, "refresh_requirements", []) or []
        ],
        "issues": [str(issue) for issue in getattr(report, "issues", []) or []],
    }


def _dump_model(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return dict(value)
    return {
        key: item
        for key, item in vars(value).items()
        if not key.startswith("_")
    }


def _recent_jobs(jobs: Iterable[JobRun], limit: int) -> list[JobRun]:
    ordered = sorted(
        jobs,
        key=lambda job: job.finished_at or job.started_at,
        reverse=True,
    )
    return ordered[: max(limit, 0)]


def _monitoring_jobs(jobs: Iterable[JobRun]) -> list[JobRun]:
    return [
        job
        for job in jobs
        if not _is_refresh_dry_run_cycle_job(job)
    ]


def _is_refresh_dry_run_cycle_job(job: JobRun) -> bool:
    return job.job_type == "production_cycle" and bool((job.summary or {}).get("refresh_dry_run"))


def _job_summary(job: JobRun) -> dict[str, Any]:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status.value,
        "source": job.source,
        "started_at": job.started_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "summary": job.summary,
        "error": job.error,
    }


def _provider_status(repository: Any, provider_id: str, source: Any) -> dict[str, Any]:
    api_key_env = getattr(source, "api_key_env", None)
    return {
        "enabled": bool(getattr(source, "enabled", False)),
        "base_url": getattr(source, "base_url", None),
        "api_key_env": api_key_env,
        "credential_present": bool(api_key_env and os.getenv(api_key_env)),
        "quota": _safe_quota_snapshot(repository, provider_id),
        "cache_entries": _safe_cache_count(repository, provider_id),
    }


def _production_status_issues(jobs: Iterable[JobRun], required_job_types: Iterable[str]) -> list[str]:
    job_list = list(jobs)
    latest_by_type = _latest_jobs_by_type(job_list)
    issues: list[str] = []
    for job_type in required_job_types:
        job = latest_by_type.get(job_type)
        if job is None:
            issues.append(f"missing_recent_job:{job_type}")
            continue
        status = getattr(job.status, "value", str(job.status))
        if status != "succeeded" and not _production_cycle_partial_is_valid_heartbeat(job):
            issues.append(f"latest_job_not_succeeded:{job_type}:{status}")
        if _recent_jobs_are_empty(job_list, job_type):
            issues.append(f"empty_recent_job:{job_type}")
    return issues


def _latest_jobs_by_type(jobs: Iterable[JobRun]) -> dict[str, JobRun]:
    latest: dict[str, JobRun] = {}
    for job in jobs:
        current = latest.get(job.job_type)
        if current is None or (job.finished_at or job.started_at) > (current.finished_at or current.started_at):
            latest[job.job_type] = job
    return latest


def _recent_jobs_are_empty(jobs: Iterable[JobRun], job_type: str, sample_size: int = 50) -> bool:
    type_jobs = [
        job
        for job in _recent_jobs(jobs, limit=1000)
        if job.job_type == job_type
    ][:sample_size]
    counts = [
        count
        for count in (_job_primary_count(job) for job in type_jobs)
        if count is not None
    ]
    return bool(counts) and max(counts) == 0


def _job_primary_count(job: JobRun) -> int | None:
    summary_key = {
        "ingest_fixtures": "matches",
        "ingest_odds": "odds_snapshots",
        "ingest_results": "matches",
        "ingest_standings": "standings",
        "ingest_historical": "matches",
    }.get(job.job_type)
    if summary_key is None:
        return None
    value = job.summary.get(summary_key)
    return int(value) if isinstance(value, int | float) else None


def _odds_readiness_status(
    service: AnalysisService,
    include_past: bool = False,
    league_codes: set[str] | None = None,
) -> dict[str, Any]:
    try:
        report = audit_odds_readiness(
            service.repository,
            service.settings,
            include_past=include_past,
            league_codes=league_codes,
            require_strategy_profiles=league_codes is None,
        )
    except Exception as exc:
        return {"status": "unavailable", "error": type(exc).__name__}

    return {
        "status": report.status,
        "league_codes": sorted(league_codes or []),
        "scoped_matches": report.scoped_matches,
        "scoped_odds_snapshots": report.scoped_odds_snapshots,
        "active_profiles": report.active_profiles,
        "ready_profiles": report.ready_profiles,
        "partial_profiles": report.partial_profiles,
        "insufficient_profiles": report.insufficient_profiles,
        "issues": report.issues,
        "refresh_requirements": [
            requirement.model_dump(mode="json")
            for requirement in report.refresh_requirements
        ],
        "league_coverages": [
            _league_coverage_summary(coverage)
            for coverage in report.league_coverages
            if coverage.strategy_mode == "live" or coverage.status != "idle"
        ],
    }


def _league_coverage_summary(coverage: Any) -> dict[str, Any]:
    return {
        "code": coverage.code,
        "name": coverage.name,
        "strategy_mode": coverage.strategy_mode,
        "paper_only": coverage.paper_only,
        "scoped_matches": coverage.scoped_matches,
        "odds_snapshots": coverage.odds_snapshots,
        "market_groups": coverage.market_groups,
        "ready_market_groups": coverage.ready_market_groups,
        "status": coverage.status,
        "issues": coverage.issues,
    }


def _build_production_readiness(
    service: AnalysisService,
    include_past: bool = False,
    league_codes: set[str] | None = None,
) -> dict[str, Any]:
    settings = service.settings
    repository = service.repository
    now = datetime.now(settings.app.tzinfo)
    scope_values = {str(code).strip() for code in (league_codes or set()) if str(code).strip()}
    scope_codes = {code.upper() for code in scope_values}
    scope_labels = {_normalize_match_value(code) for code in scope_values}

    matches = repository.list_models("matches", Match)
    odds = repository.list_models("odds", OddsSnapshot)
    candidate_matches = [
        match
        for match in matches
        if include_past or _match_local_date(match, settings) >= now.date()
    ]

    matches_by_league: dict[str, list[Match]] = defaultdict(list)
    odds_by_league: dict[str, list[OddsSnapshot]] = defaultdict(list)
    market_groups_by_league: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    unknown_matches_by_league: dict[str, list[Match]] = defaultdict(list)
    unknown_odds_by_league: dict[str, list[OddsSnapshot]] = defaultdict(list)
    unknown_market_groups_by_league: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    league_code_by_match: dict[str, str] = {}
    unknown_label_by_match: dict[str, str] = {}
    scoped_matches: list[Match] = []

    for match in candidate_matches:
        league = _league_settings_for_match(match, settings)
        if league is None:
            label = _raw_league_label(match)
            if scope_codes and label.upper() not in scope_codes and _normalize_match_value(label) not in scope_labels:
                continue
            scoped_matches.append(match)
            unknown_label_by_match[match.id] = label
            unknown_matches_by_league[label].append(match)
            continue
        if scope_codes and league.code.upper() not in scope_codes:
            continue
        scoped_matches.append(match)
        league_code_by_match[match.id] = league.code
        matches_by_league[league.code].append(match)

    scoped_match_ids = {match.id for match in scoped_matches}
    scoped_odds = [snapshot for snapshot in odds if snapshot.match_id in scoped_match_ids]

    for snapshot in scoped_odds:
        market_group = _market_group_key(snapshot)
        if league_code := league_code_by_match.get(snapshot.match_id):
            odds_by_league[league_code].append(snapshot)
            market_groups_by_league[league_code].add(market_group)
        elif label := unknown_label_by_match.get(snapshot.match_id):
            unknown_odds_by_league[label].append(snapshot)
            unknown_market_groups_by_league[label].add(market_group)

    history_by_code = _historical_data_by_strategy_code(settings, repository)
    profiles_by_code = _active_profiles_by_strategy_code(settings)
    rows: list[dict[str, Any]] = []
    for league in settings.leagues:
        if scope_codes and league.code.upper() not in scope_codes:
            continue
        strategy_code = _strategy_code_for_league(league)
        profiles = profiles_by_code.get(strategy_code, [])
        live_profiles = [profile for profile in profiles if getattr(profile, "live_enabled", False)]
        league_market_groups = market_groups_by_league.get(league.code, set())
        league_matches = matches_by_league.get(league.code, [])
        league_odds = odds_by_league.get(league.code, [])
        history_seasons = history_by_code.get(strategy_code, [])
        required_historical_seasons = _public_historical_target_seasons(
            strategy_code,
            settings.backtest.default_season,
            active_season=getattr(league, "season", None),
        )
        missing_historical_seasons = [
            season for season in required_historical_seasons if season not in history_seasons
        ]
        status, issues, next_actions = _production_readiness_decision(
            league=league,
            strategy_code=strategy_code,
            scoped_matches=len(league_matches),
            odds_snapshots=len(league_odds),
            odds_market_types=_market_types_from_groups(league_market_groups),
            historical_seasons=history_seasons,
            missing_historical_seasons=missing_historical_seasons,
            profiles=profiles,
            live_profiles=live_profiles,
        )
        rows.append(
            {
                "configured": True,
                "code": league.code,
                "name": league.name,
                "strategy_code": strategy_code,
                "tier": league.tier,
                "strategy_mode": league.strategy_mode,
                "paper_only": league.paper_only,
                "scoped_matches": len(league_matches),
                "odds_snapshots": len(league_odds),
                "market_groups": len(league_market_groups),
                "odds_market_types": _market_types_from_groups(league_market_groups),
                "historical_data": {
                    "available": bool(history_seasons),
                    "season_count": len(history_seasons),
                    "seasons": history_seasons,
                    "required_seasons": required_historical_seasons,
                    "missing_required_seasons": missing_historical_seasons,
                },
                "active_profile_count": len(profiles),
                "live_enabled_profile_count": len(live_profiles),
                "profiles": [_profile_summary(profile) for profile in profiles],
                "status": status,
                "issues": issues,
                "next_actions": next_actions,
            }
        )

    for label in sorted(unknown_matches_by_league):
        rows.append(
            {
                "configured": False,
                "code": label,
                "name": label,
                "strategy_code": label.upper(),
                "tier": None,
                "strategy_mode": "unconfigured",
                "paper_only": True,
                "scoped_matches": len(unknown_matches_by_league[label]),
                "odds_snapshots": len(unknown_odds_by_league.get(label, [])),
                "market_groups": len(unknown_market_groups_by_league.get(label, set())),
                "odds_market_types": _market_types_from_groups(
                    unknown_market_groups_by_league.get(label, set())
                ),
                "historical_data": {"available": False, "season_count": 0, "seasons": []},
                "active_profile_count": 0,
                "live_enabled_profile_count": 0,
                "profiles": [],
                "status": "blocked",
                "issues": ["league_not_configured"],
                "next_actions": [f"configure_league:{label}"],
            }
        )

    rows.sort(key=_production_readiness_sort_key)
    ready_rows = [row for row in rows if row["status"] == "production_ready"]
    blocked_active_rows = [
        row
        for row in rows
        if row["status"] == "blocked" and (row["scoped_matches"] > 0 or row["odds_snapshots"] > 0)
    ]
    issues = _production_readiness_issues(ready_rows, blocked_active_rows)
    status = "ready" if ready_rows else "blocked" if blocked_active_rows else "idle"
    return {
        "checked_at": now.isoformat(),
        "status": status,
        "include_past": include_past,
        "league_codes": sorted(scope_values),
        "configured_leagues": sum(1 for row in rows if row.get("configured") is True),
        "scoped_matches": len(scoped_matches),
        "scoped_odds_snapshots": len(scoped_odds),
        "production_ready_leagues": len(ready_rows),
        "blocked_active_leagues": len(blocked_active_rows),
        "historical_data_dir": str(_historical_data_dir(settings)),
        "issues": issues,
        "leagues": rows,
    }


def _production_readiness_decision(
    league: Any,
    strategy_code: str,
    scoped_matches: int,
    odds_snapshots: int,
    odds_market_types: list[str],
    historical_seasons: list[str],
    missing_historical_seasons: list[str],
    profiles: list[Any],
    live_profiles: list[Any],
) -> tuple[str, list[str], list[str]]:
    issues: list[str] = []
    next_actions: list[str] = []
    configured_live = league.strategy_mode == "live" and not league.paper_only
    has_live_activity = scoped_matches > 0 or odds_snapshots > 0

    if not configured_live:
        issues.append("league_not_enabled_for_live")
        if has_live_activity:
            next_actions.append(f"paper_track_only:{league.code}")
        return "paper_only", issues, next_actions

    if scoped_matches <= 0:
        issues.append("no_live_fixtures")
        next_actions.append(f"need_live_fixtures:{league.code}")
    if odds_snapshots <= 0:
        issues.append("no_live_odds")
        next_actions.append(f"need_live_odds:{league.code}")
    if not historical_seasons or missing_historical_seasons:
        issues.append("missing_historical_data")
        if missing_historical_seasons:
            seasons = ",".join(missing_historical_seasons)
            next_actions.append(f"need_historical_data:{strategy_code}:{seasons}")
        else:
            next_actions.append(f"need_historical_data:{strategy_code}")
    profileless_tier_allowed = league.tier in {"secondary_professional", "major_tournament"}
    if not profiles:
        if profileless_tier_allowed and historical_seasons and not missing_historical_seasons:
            next_actions.append(f"profileless_tier_policy_live:{strategy_code}")
        else:
            issues.append("missing_active_strategy_profile")
            next_actions.append(f"need_strategy_profile:{strategy_code}")
    elif not live_profiles:
        issues.append("missing_live_enabled_strategy_profile")
        next_actions.append(f"need_live_enabled_profile:{strategy_code}")

    checked_profiles = live_profiles or profiles
    profile_market_types = sorted(
        {str(getattr(profile, "market_type", "")).lower() for profile in checked_profiles if profile}
    )
    missing_markets = [
        market_type
        for market_type in profile_market_types
        if market_type and market_type not in {item.lower() for item in odds_market_types}
    ]
    if odds_snapshots > 0 and checked_profiles and missing_markets:
        issues.append(f"missing_profile_market_odds:{','.join(missing_markets)}")
        next_actions.append(f"need_profile_market_odds:{league.code}:{','.join(missing_markets)}")

    if not has_live_activity and set(issues) <= {"no_live_fixtures", "no_live_odds"}:
        return "idle", issues, next_actions
    if issues:
        return "blocked", issues, next_actions
    return "production_ready", [], []


def _production_readiness_issues(
    ready_rows: list[dict[str, Any]],
    blocked_active_rows: list[dict[str, Any]],
) -> list[str]:
    issues: list[str] = []
    if not ready_rows:
        issues.append("no_production_ready_leagues")
    for row in blocked_active_rows:
        issues.extend(f"{row['code']}:{issue}" for issue in row["issues"])
    return issues


def _production_readiness_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    status_rank = {"production_ready": 0, "blocked": 1, "paper_only": 2, "idle": 3}
    activity = int(row.get("scoped_matches", 0)) + int(row.get("odds_snapshots", 0))
    return (status_rank.get(str(row.get("status")), 9), -activity, str(row.get("code", "")))


def _active_profiles_by_strategy_code(settings: Any) -> dict[str, list[Any]]:
    profiles_by_code: dict[str, list[Any]] = defaultdict(list)
    for profile in settings.strategy_profiles:
        if not getattr(profile, "active", True):
            continue
        profiles_by_code[str(profile.league_code).upper()].append(profile)
    return profiles_by_code


def _historical_data_by_strategy_code(settings: Any, repository: Any) -> dict[str, list[str]]:
    data_dir = _historical_data_dir(settings)
    seasons_by_code: dict[str, set[str]] = defaultdict(set)
    if not data_dir.exists():
        pass
    else:
        for path in data_dir.glob("*/*.csv"):
            if not path.is_file():
                continue
            seasons_by_code[path.stem.upper()].add(path.parent.name)
    try:
        for row in repository.list_models("historical_matches", HistoricalMatchRow):
            seasons_by_code[str(row.league).upper()].add(str(row.season))
    except Exception:
        pass
    return {code: sorted(seasons) for code, seasons in seasons_by_code.items()}


def _historical_data_dir(settings: Any) -> Path:
    data_dir = Path(settings.backtest.data_dir)
    return data_dir if data_dir.is_absolute() else Path.cwd() / data_dir


def _league_settings_for_match(match: Match, settings: Any) -> Any | None:
    normalized_league = _normalize_match_value(match.league)
    for league in settings.leagues:
        if normalized_league in {_normalize_match_value(value) for value in _league_match_values(league) if value}:
            return league
    return None


def _league_match_values(league: Any) -> list[str | None]:
    return [
        league.code,
        league.name,
        league.football_data_uk_code,
        league.football_data_org_code,
        *list(getattr(league, "aliases", []) or []),
    ]


def _strategy_code_for_league(league: Any) -> str:
    return str(league.football_data_uk_code or league.code).upper()


def _match_local_date(match: Match, settings: Any) -> date:
    kickoff = match.kickoff_at
    if kickoff.tzinfo is None:
        return kickoff.date()
    return kickoff.astimezone(settings.app.tzinfo).date()


def _raw_league_label(match: Match) -> str:
    return str(match.league or "UNKNOWN").strip() or "UNKNOWN"


def _normalize_match_value(value: Any) -> str:
    return str(value).strip().lower()


def _market_group_key(snapshot: OddsSnapshot) -> tuple[str, str, str]:
    return (snapshot.match_id, _market_type(snapshot), snapshot.line or "")


def _market_types_from_groups(groups: set[tuple[str, str, str]]) -> list[str]:
    return sorted({market_type for _, market_type, _ in groups})


def _market_type(snapshot: OddsSnapshot) -> str:
    raw = snapshot.market_type
    return str(getattr(raw, "value", raw))


def _profile_summary(profile: Any) -> dict[str, Any]:
    return {
        "id": profile.id,
        "name": profile.name,
        "market_type": profile.market_type,
        "selections": list(profile.selections),
        "season_phases": list(profile.season_phases),
        "stability_label": profile.stability_label,
        "live_enabled": profile.live_enabled,
        "roi": profile.roi,
        "settled_bets": profile.settled_bets,
        "average_clv": profile.average_clv,
    }


def _data_plan_tasks_for_league(
    row: dict[str, Any],
    settings: Any,
    historical_odds_plan_options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    issues = set(row.get("issues", []))
    tasks: list[dict[str, Any]] = []
    if "missing_historical_data" in issues:
        tasks.append(_historical_data_task(row, settings, historical_odds_plan_options))
    if "no_live_odds" in issues or any(str(issue).startswith("missing_profile_market_odds:") for issue in issues):
        tasks.append(_live_odds_task(row, settings))
    if "missing_active_strategy_profile" in issues:
        tasks.append(_strategy_profile_task(row, settings, prerequisite="historical_training_data"))
    if "missing_live_enabled_strategy_profile" in issues:
        prerequisite = (
            "historical_training_data"
            if "missing_historical_data" in issues
            else "profile_live_enablement"
        )
        tasks.append(_strategy_profile_task(row, settings, prerequisite=prerequisite))
    return tasks


def _data_apply_commands_from_plan(
    plan: dict[str, Any],
    allow_remote: bool,
    include_backtests: bool,
    include_blocked_prerequisites: bool,
    max_commands: int | None,
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    seen: set[str] = set()
    selected_count = 0
    for task in plan.get("tasks", []):
        for command in task.get("local_commands", []) or []:
            command = str(command)
            if command in seen:
                continue
            seen.add(command)
            classification = _data_apply_command_classification(command)
            skipped_reasons: list[str] = []
            if classification["requires_remote_quota"] and not allow_remote:
                skipped_reasons.append("remote_command_requires_allow_remote")
            if classification["category"] == "backtest" and not include_backtests:
                skipped_reasons.append("backtest_commands_disabled")
            if classification["category"] == "profile_promotion_plan" and not include_backtests:
                skipped_reasons.append("profile_promotion_commands_disabled")
            if task.get("status") == "blocked_by_prerequisite" and not include_blocked_prerequisites:
                skipped_reasons.append("blocked_by_prerequisite")
            if classification.get("requires_manual_risk_config"):
                skipped_reasons.append("manual_risk_config_required")
            if classification["category"] == "unknown":
                skipped_reasons.append("unknown_command_requires_manual_review")
            if max_commands is not None and selected_count >= max_commands:
                skipped_reasons.append("max_commands_reached")
            selected = not skipped_reasons
            if selected:
                selected_count += 1
            commands.append(
                {
                    "command": command,
                    "selected": selected,
                    "skip_reasons": skipped_reasons,
                    "skipped_reasons": skipped_reasons,
                    "category": classification["category"],
                    "classification": classification["category"],
                    "requires_remote_quota": classification["requires_remote_quota"],
                    "task_type": task.get("task_type"),
                    "league": task.get("league"),
                    "strategy_code": task.get("strategy_code"),
                    "task_status": task.get("status"),
                }
            )
    return commands


def _data_apply_command_summary(commands: list[dict[str, Any]]) -> dict[str, Any]:
    by_category: dict[str, int] = {}
    selected_by_category: dict[str, int] = {}
    skipped_by_reason: dict[str, int] = {}
    remote_quota_command_count = 0
    selected_remote_quota_command_count = 0
    for command in commands:
        category = str(command.get("category") or command.get("classification") or "unknown")
        by_category[category] = by_category.get(category, 0) + 1
        selected = command.get("selected") is True
        if selected:
            selected_by_category[category] = selected_by_category.get(category, 0) + 1
        if command.get("requires_remote_quota") is True:
            remote_quota_command_count += 1
            if selected:
                selected_remote_quota_command_count += 1
        for reason in command.get("skipped_reasons") or command.get("skip_reasons") or []:
            reason = str(reason)
            skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1
    next_actions: list[str] = []
    if skipped_by_reason.get("remote_command_requires_allow_remote"):
        next_actions.append("review_provider_quota_then_rerun_with_allow_remote")
    if skipped_by_reason.get("backtest_commands_disabled"):
        next_actions.append("rerun_without_skip_backtests_for_local_validation")
    if skipped_by_reason.get("blocked_by_prerequisite"):
        next_actions.append("satisfy_prerequisite_tasks_before_validation")
    if skipped_by_reason.get("manual_risk_config_required"):
        next_actions.append("provide_max_stake_units_for_profile_promotion")
    if skipped_by_reason.get("max_commands_reached"):
        next_actions.append("increase_max_commands_or_rerun_remaining_batch")
    if skipped_by_reason.get("unknown_command_requires_manual_review"):
        next_actions.append("manually_review_unknown_commands")
    selected_count = sum(1 for command in commands if command.get("selected") is True)
    return {
        "total_count": len(commands),
        "selected_count": selected_count,
        "skipped_count": len(commands) - selected_count,
        "by_category": dict(sorted(by_category.items())),
        "selected_by_category": dict(sorted(selected_by_category.items())),
        "skipped_by_reason": dict(sorted(skipped_by_reason.items())),
        "remote_quota_command_count": remote_quota_command_count,
        "selected_remote_quota_command_count": selected_remote_quota_command_count,
        "next_actions": next_actions,
    }


def _data_apply_command_classification(command: str) -> dict[str, Any]:
    if command.startswith("footballctl ingest historical-odds "):
        return {"category": "remote_historical_odds_ingestion", "requires_remote_quota": True}
    if command.startswith("footballctl ingest odds "):
        return {"category": "remote_odds_ingestion", "requires_remote_quota": True}
    if command.startswith("footballctl ingest fixtures ") or command.startswith("footballctl ingest results "):
        return {"category": "remote_match_ingestion", "requires_remote_quota": True}
    if command.startswith("footballctl ingest historical "):
        if " --source qqsd_local_asian " in command:
            return {"category": "local_qqsd_asian_history", "requires_remote_quota": False}
        if " --source qqsd " in command:
            return {"category": "remote_qqsd_historical_archive", "requires_remote_quota": True}
        return {"category": "public_historical_download", "requires_remote_quota": False}
    if command.startswith("footballctl backtest "):
        return {"category": "backtest", "requires_remote_quota": False}
    if command.startswith("footballctl production-profile-promote "):
        return {
            "category": "profile_promotion_plan",
            "requires_remote_quota": False,
            "requires_manual_risk_config": "--max-stake-units" not in command,
        }
    return {"category": "unknown", "requires_remote_quota": True}


def _data_apply_command_argv(command: str) -> list[str] | None:
    argv = command.split()
    if not argv or argv[0] != "footballctl":
        return None
    return _resolve_footballctl_argv(argv)


def _resolve_footballctl_argv(argv: list[str]) -> list[str]:
    resolved = shutil.which("footballctl")
    if resolved:
        return [resolved, *argv[1:]]

    python_path = Path(sys.executable)
    candidates = [
        python_path.with_name("footballctl.exe"),
        python_path.with_name("footballctl"),
        Path.cwd() / ".venv312" / "Scripts" / "footballctl.exe",
        Path.cwd() / ".venv" / "Scripts" / "footballctl.exe",
        Path.cwd() / ".venv" / "bin" / "footballctl",
    ]
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate), *argv[1:]]

    return [sys.executable, "-m", "football_analysis", *argv[1:]]


def _run_data_apply_command(argv: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout_seconds,
    )


def _command_payload_status(stdout: str) -> str | None:
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    return str(status) if status is not None else None


def _text_tail(value: str, max_chars: int = 4000) -> str:
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]


def _historical_data_task(
    row: dict[str, Any],
    settings: Any,
    historical_odds_plan_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    code = str(row["code"])
    strategy_code = str(row["strategy_code"])
    commands: list[str] = []
    candidates: list[dict[str, Any]] = []
    league = _league_by_code(settings, code)
    the_odds_source = settings.data_sources.get("the_odds_api")
    the_odds_sport_key = (
        sport_key_for_league(code, getattr(league, "odds_api_slug", None), getattr(the_odds_source, "sport_keys", {}))
        if league is not None and the_odds_source is not None
        else None
    )
    the_odds_ready = bool(
        the_odds_sport_key
        and the_odds_source is not None
        and getattr(the_odds_source, "enabled", False)
        and getattr(the_odds_source, "api_key_env", None)
        and os.getenv(str(the_odds_source.api_key_env))
    )
    historical_odds_plan: dict[str, Any] | None = None
    qqsd_source = settings.data_sources.get("qqsd")
    if qqsd_source is not None and getattr(qqsd_source, "enabled", False):
        end_date = datetime.now(settings.app.tzinfo).date()
        start_date = end_date - timedelta(days=29)
        season = str(getattr(league, "season", None) or settings.backtest.default_season)
        commands.append(
            "footballctl ingest historical "
            f"--league {code} --season {season} --source qqsd_local_asian --json"
        )
        commands.append(
            "footballctl ingest historical "
            f"--league {code} --season {season} --source qqsd "
            f"--start-date {start_date.isoformat()} --end-date {end_date.isoformat()} "
            "--max-pages 3 --json"
        )
        candidates.append(
            _source_candidate(
                settings,
                "qqsd",
                purpose="qqsd_recent_archive_history",
                official_url="https://i.qqshidao.com",
                coverage="QQSD archive score list with finished results and 1x2 odds for recent configured-league backtests",
                requires_user_application=False,
                notes=[
                    "Use QQSD first for configured leagues before paid provider fallbacks.",
                    "Archive import maps reliable 1x2 historical rows; stored QQSD Asian handicap snapshots can be backfilled from finished local matches.",
                    "Dedicated company-level historical Asian handicap archive remains separate from the live 40020 endpoint.",
                ],
            )
        )

    if strategy_code in _football_data_uk_download_codes():
        historical_data = row.get("historical_data") or {}
        missing_seasons = list(historical_data.get("missing_required_seasons") or [])
        if not missing_seasons:
            missing_seasons = _public_historical_missing_seasons(
                strategy_code,
                list(historical_data.get("seasons") or []),
                settings.backtest.default_season,
                active_season=getattr(league, "season", None),
            )
        if not missing_seasons and strategy_code in FootballDataUkClient.extra_league_codes:
            available_seasons = list(historical_data.get("seasons") or [])
            if not available_seasons:
                missing_seasons = [str(getattr(league, "season", None) or settings.backtest.default_season)]
        for season in missing_seasons:
            commands.append(
                "footballctl ingest historical "
                f"--league {strategy_code} --season {season} --download --json"
        )
        candidates.append(
            _source_candidate(
                settings,
                "football_data_uk",
                purpose="historical_csv_with_odds",
                official_url=_football_data_uk_url(strategy_code),
                coverage="current public CSV for extra leagues or season CSV for standard leagues",
                requires_user_application=False,
                notes=[
                    "Use this first when the league has a football_data_uk_code mapping.",
                    "For extra leagues, football-data.co.uk publishes the current CSV under /new/{code}.csv.",
                ],
            )
        )

    candidates.extend(
        [
            _source_candidate(
                settings,
                "the_odds_api",
                purpose="paid_historical_odds_snapshots",
                official_url="https://the-odds-api.com/historical-odds-data",
                coverage="historical odds snapshots for covered sports/bookmakers from 2020 onward",
                requires_user_application=not the_odds_ready,
                notes=[
                    "Needed when public CSV history is unavailable or too shallow for strategy validation.",
                    "Live/upcoming and historical snapshot odds ingestion are implemented; confirm plan coverage and quota before enabling.",
                ],
            ),
            _source_candidate(
                settings,
                "sportmonks",
                purpose="paid_premium_pre_match_odds",
                official_url=(
                    "https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/"
                    "premium-odds-feed"
                ),
                coverage="premium pre-match odds and short historical odds window when add-on is active",
                requires_user_application=True,
                notes=[
                    "Useful as a paid live/premium odds fallback.",
                    "Not enough by itself for long multi-season backtests unless the subscribed history covers the target window.",
                ],
            ),
        ]
    )

    if historical_odds_plan_options is not None:
        historical_odds_plan = _build_the_odds_api_historical_odds_plan(
            settings,
            leagues=[code],
            start_time=str(historical_odds_plan_options.get("start_time") or ""),
            end_time=str(historical_odds_plan_options.get("end_time") or ""),
            interval_minutes=int(historical_odds_plan_options.get("interval_minutes") or 10),
            max_snapshots=int(historical_odds_plan_options.get("max_snapshots") or 24),
            max_events=historical_odds_plan_options.get("max_events"),
        )
        if historical_odds_plan.get("status") == "ready":
            commands.extend(str(command) for command in historical_odds_plan.get("commands", []))

    return {
        "league": code,
        "strategy_code": strategy_code,
        "task_type": "historical_training_data",
        "reason": "missing_historical_data",
        "status": "local_command_available" if commands else "requires_provider_application",
        "local_commands": commands,
        "provider_candidates": candidates,
        "historical_odds_batch_plan": historical_odds_plan,
        "user_actions": [] if commands else _provider_user_actions(candidates),
    }


def _live_odds_task(row: dict[str, Any], settings: Any) -> dict[str, Any]:
    code = str(row["code"])
    league = _league_by_code(settings, code)
    commands: list[str] = []
    candidates: list[dict[str, Any]] = []

    qqsd_source = settings.data_sources.get("qqsd")
    if qqsd_source is not None:
        if getattr(qqsd_source, "enabled", False):
            max_events = getattr(league, "max_events", None) if league is not None else None
            commands.append(
                f"footballctl ingest odds --source qqsd --league {code} --max-events {max_events or 20} --json"
            )
        candidates.append(
            _source_candidate(
                settings,
                "qqsd",
                purpose="primary_live_fixtures_and_odds",
                official_url="https://i.qqshidao.com",
                coverage="QQSD score list plus company 1x2, Asian handicap, and over/under odds filtered by configured league aliases",
                requires_user_application=False,
                notes=[
                    "Primary live datasource for production refresh when configured.",
                    "Deep context endpoints cover match detail, standings, odds history, heat, handicap-Europe odds, league stats, votes, and lingsi findings.",
                    "Use before free fallback APIs because it provides richer fixtures, context, and odds coverage.",
                ],
            )
        )

    if league is not None and getattr(league, "odds_api_slug", None):
        max_events = getattr(league, "max_events", None) or 20
        commands.append(
            f"footballctl ingest odds --source odds_api_io --league {code} --max-events {max_events} --json"
        )
        candidates.append(
            _source_candidate(
                settings,
                "odds_api_io",
                purpose="free_live_odds_fallback",
                official_url="https://api.odds-api.io",
                coverage=f"configured league slug {league.odds_api_slug}",
                requires_user_application=False,
                notes=["Fallback odds source after QQSD when supported by the account and endpoint quota."],
            )
        )
    if league is not None and getattr(league, "api_football_league_id", None):
        commands.append(f"footballctl ingest odds --source api_football --league {code} --json")
        candidates.append(
            _source_candidate(
                settings,
                "api_football",
                purpose="free_live_or_recent_odds_fallback",
                official_url="https://api-sports.io/documentation/football/v3",
                coverage=f"configured API-FOOTBALL league id {league.api_football_league_id}",
                requires_user_application=False,
                notes=["Fallback odds source when QQSD and odds-api.io do not cover the fixture."],
            )
        )

    the_odds_source = settings.data_sources.get("the_odds_api")
    the_odds_sport_key = (
        sport_key_for_league(code, getattr(league, "odds_api_slug", None), getattr(the_odds_source, "sport_keys", {}))
        if league is not None and the_odds_source is not None
        else None
    )
    if the_odds_sport_key:
        the_odds_ready = bool(
            getattr(the_odds_source, "enabled", False)
            and getattr(the_odds_source, "api_key_env", None)
            and os.getenv(str(the_odds_source.api_key_env))
        )
        if the_odds_ready:
            max_events = getattr(league, "max_events", None) or 20
            commands.append(
                f"footballctl ingest odds --source the_odds_api --league {code} --max-events {max_events} --json"
            )
        candidates.append(
            _source_candidate(
                settings,
                "the_odds_api",
                purpose="paid_live_odds_fallback",
                official_url="https://the-odds-api.com/liveapi/guides/v4",
                coverage=f"configured The Odds API sport key {the_odds_sport_key}",
                requires_user_application=not the_odds_ready,
                notes=[
                    "Uses The Odds API v4 odds endpoint for upcoming/live h2h, spreads, and totals.",
                    "Enable only after confirming plan quota and bookmaker region coverage.",
                ],
            )
        )

    sportmonks_source = settings.data_sources.get("sportmonks")
    sportmonks_league_id = getattr(league, "sportmonks_league_id", None) if league is not None else None
    if sportmonks_source is not None:
        sportmonks_ready = bool(
            sportmonks_league_id
            and getattr(sportmonks_source, "enabled", False)
            and getattr(sportmonks_source, "api_key_env", None)
            and os.getenv(str(sportmonks_source.api_key_env))
        )
        if sportmonks_ready:
            max_events = getattr(league, "max_events", None) or 20
            commands.append(
                f"footballctl ingest odds --source sportmonks --league {code} --max-events {max_events} --json"
            )
        candidates.append(
            _source_candidate(
                settings,
                "sportmonks",
                purpose="premium_pre_match_odds_fallback",
                official_url="https://docs.sportmonks.com/v3/tutorials-and-guides/tutorials/odds-and-predictions/pre-match-odds",
                coverage=(
                    f"configured Sportmonks league id {sportmonks_league_id}"
                    if sportmonks_league_id
                    else "requires configured sportmonks_league_id for this league"
                ),
                requires_user_application=not sportmonks_ready,
                notes=[
                    "Uses Sportmonks v3 fixtures by date plus pre-match odds by fixture.",
                    "Enable only after confirming subscription coverage and filling sportmonks_league_id.",
                ],
            )
        )

    if not candidates:
        candidates.append(
            _source_candidate(
                settings,
                "qqsd",
                purpose="primary_live_provider_to_configure",
                official_url="https://i.qqshidao.com",
                coverage="QQSD live fixtures, match context, company 1x2, Asian handicap, over/under odds, and odds-context endpoints",
                requires_user_application=False,
                notes=["Configure QQSD as the primary live datasource before adding fallback APIs."],
            )
        )

    return {
        "league": code,
        "strategy_code": row["strategy_code"],
        "task_type": "live_odds",
        "reason": "no_live_odds_or_profile_market_odds",
        "status": "local_command_available" if commands else "requires_provider_application",
        "local_commands": commands,
        "provider_candidates": candidates,
        "user_actions": [] if _has_ready_provider_candidate(candidates) else _provider_user_actions(candidates),
    }


def _strategy_profile_task(row: dict[str, Any], settings: Any, prerequisite: str) -> dict[str, Any]:
    strategy_code = str(row["strategy_code"])
    commands = [
        _long_horizon_command_for_row(row),
        "footballctl backtest profile-audit --json",
    ]
    suggested_max_stake_units = None
    if prerequisite == "profile_live_enablement":
        suggested_max_stake_units = _suggested_profile_max_stake_units(settings)
        promotion_command = f"footballctl production-profile-promote --strategy-code {strategy_code}"
        if suggested_max_stake_units is not None:
            promotion_command += f" --max-stake-units {suggested_max_stake_units:g}"
        commands.append(f"{promotion_command} --json")
    return {
        "league": row["code"],
        "strategy_code": strategy_code,
        "task_type": "strategy_profile_validation",
        "reason": "missing_live_enabled_strategy_profile",
        "status": "blocked_by_prerequisite" if prerequisite == "historical_training_data" else "manual_config_required",
        "prerequisite": prerequisite,
        "suggested_max_stake_units": suggested_max_stake_units,
        "local_commands": commands,
        "provider_candidates": [],
        "user_actions": (
            [f"import_historical_training_data:{strategy_code}"]
            if prerequisite == "historical_training_data"
            else [f"review_and_enable_live_profile:{strategy_code}"]
        ),
    }


def _suggested_profile_max_stake_units(settings: Any) -> float | None:
    global_cap = getattr(getattr(settings, "live_trading", None), "max_stake_units_per_pick", None)
    if global_cap is None or float(global_cap) <= 0:
        return None
    return round(min(float(global_cap), 0.2), 4)


def _long_horizon_command_for_row(row: dict[str, Any]) -> str:
    strategy_code = str(row["strategy_code"])
    seasons = list((row.get("historical_data") or {}).get("seasons") or [])
    if len(seasons) >= 2 and all(
        str(season).isdigit() and len(str(season)) == 4 and int(str(season)) >= 1900
        for season in seasons
    ):
        split_index = max(1, min(len(seasons) - 1, len(seasons) // 2))
        return (
            f"footballctl backtest long-horizon-scan --league {strategy_code} "
            f"--discovery-start {seasons[0]} --discovery-end {seasons[split_index - 1]} "
            f"--holdout-start {seasons[split_index]} --json"
        )
    return f"footballctl backtest long-horizon-scan --league {strategy_code} --json"


def _source_candidate(
    settings: Any,
    source_id: str,
    purpose: str,
    official_url: str,
    coverage: str,
    requires_user_application: bool,
    notes: list[str],
) -> dict[str, Any]:
    source = settings.data_sources.get(source_id)
    api_key_env = getattr(source, "api_key_env", None) if source is not None else None
    return {
        "source_id": source_id,
        "name": getattr(source, "name", source_id),
        "purpose": purpose,
        "official_url": official_url,
        "coverage": coverage,
        "configured": source is not None,
        "enabled": bool(getattr(source, "enabled", False)) if source is not None else False,
        "credential_env": api_key_env,
        "credential_present": bool(api_key_env and os.getenv(api_key_env)),
        "requires_user_application": requires_user_application,
        "notes": notes,
    }


def _provider_user_actions(candidates: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for candidate in candidates:
        env_name = candidate.get("credential_env")
        if candidate.get("requires_user_application"):
            actions.append(f"apply_or_confirm_provider:{candidate['source_id']}")
        if env_name and not candidate.get("credential_present"):
            actions.append(f"set_env:{env_name}")
        if candidate.get("configured") and not candidate.get("enabled") and candidate.get("requires_user_application"):
            actions.append(f"enable_data_source:{candidate['source_id']}")
    return sorted(set(actions))


def _has_ready_provider_candidate(candidates: list[dict[str, Any]]) -> bool:
    for candidate in candidates:
        if candidate.get("configured") is not True or candidate.get("enabled") is not True:
            continue
        env_name = candidate.get("credential_env")
        if env_name and candidate.get("credential_present") is not True:
            continue
        return True
    return False


def _football_data_uk_standard_season_codes() -> set[str]:
    return {"E0", "SP1", "I1", "D1", "F1"}


def _football_data_uk_download_codes() -> set[str]:
    return _football_data_uk_standard_season_codes() | set(FootballDataUkClient.extra_league_codes)


def _public_historical_target_seasons(
    strategy_code: str,
    default_season: str,
    active_season: int | str | None = None,
) -> list[str]:
    code = strategy_code.upper()
    if code in _football_data_uk_standard_season_codes():
        return _default_profile_audit_seasons()
    return []


def _public_historical_missing_seasons(
    strategy_code: str,
    available_seasons: list[str],
    default_season: str,
    active_season: int | str | None = None,
) -> list[str]:
    available = {str(season) for season in available_seasons}
    return [
        season
        for season in _public_historical_target_seasons(
            strategy_code,
            default_season,
            active_season=active_season,
        )
        if season not in available
    ]


def _football_data_uk_url(strategy_code: str) -> str:
    code = strategy_code.upper()
    if code in FootballDataUkClient.extra_league_codes:
        return f"https://www.football-data.co.uk/new/{code}.csv"
    return "https://www.football-data.co.uk/data.php"


def _league_by_code(settings: Any, code: str) -> Any | None:
    normalized = code.upper()
    for league in settings.leagues:
        if str(league.code).upper() == normalized:
            return league
    return None


def _safe_count(repository: Any, bucket: str) -> int:
    try:
        return int(repository.count(bucket))
    except Exception:
        return 0


def _safe_cache_count(repository: Any, provider_id: str) -> int:
    try:
        return int(repository.cache_count(provider_id))
    except Exception:
        return 0


def _safe_quota_snapshot(repository: Any, provider_id: str) -> dict[str, int]:
    try:
        snapshot = repository.quota_snapshot(provider_id)
    except Exception:
        return {}
    return {str(key): int(value) for key, value in dict(snapshot).items()}


def _extract_issues(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        raw = payload.get("issues", [])
    else:
        raw = getattr(payload, "issues", [])
    return [str(item) for item in raw]


def _daily_ops_summary(payload: Any) -> dict[str, Any] | None:
    if payload is None:
        return None
    if isinstance(payload, dict):
        preflight = payload.get("preflight") or {}
        live_review = payload.get("live_review") or {}
        return {
            "date": payload.get("date"),
            "status": payload.get("status"),
            "ready_to_bet": bool(payload.get("ready_to_bet", False)),
            "action": payload.get("action"),
            "issues": [str(issue) for issue in payload.get("issues", [])],
            "preflight_status": preflight.get("status") if isinstance(preflight, dict) else None,
            "live_review_status": live_review.get("status") if isinstance(live_review, dict) else None,
        }
    return {
        "date": getattr(payload, "date", None),
        "status": getattr(payload, "status", None),
        "ready_to_bet": bool(getattr(payload, "ready_to_bet", False)),
        "action": getattr(payload, "action", None),
        "issues": [str(issue) for issue in getattr(payload, "issues", [])],
        "preflight_status": getattr(getattr(payload, "preflight", None), "status", None),
        "live_review_status": getattr(getattr(payload, "live_review", None), "status", None),
    }


def _decision_summary(decision: Any) -> dict[str, Any]:
    return {
        "status": getattr(decision, "status", "unknown"),
        "ready_to_bet": bool(getattr(decision, "ready_to_bet", False)),
        "action": getattr(decision, "action", "unknown"),
        "issues": [str(issue) for issue in getattr(decision, "issues", [])],
        "components": dict(getattr(decision, "components", {}) or {}),
    }
