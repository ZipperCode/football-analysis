from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from pydantic import Field

from football_analysis.live_decision import LiveDecisionReport, run_live_decision
from football_analysis.models import AppModel, JobRun
from football_analysis.service import AnalysisService


class ProductionStatusReport(AppModel):
    checked_at: datetime
    overall_status: str
    ready_to_bet: bool
    action: str
    decision: LiveDecisionReport
    counts: dict[str, int] = Field(default_factory=dict)
    recent_jobs: list[JobRun] = Field(default_factory=list)
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)


DecisionRunner = Callable[[AnalysisService, bool], LiveDecisionReport]


def build_production_status(
    service: AnalysisService,
    recent_limit: int = 10,
    include_past: bool = False,
    decision_runner: DecisionRunner | None = None,
    required_job_types: tuple[str, ...] = ("ingest_fixtures", "ingest_odds"),
) -> ProductionStatusReport:
    runner = decision_runner or _run_decision
    decision = runner(service, include_past)
    counts = {
        "matches": service.repository.count("matches"),
        "odds": service.repository.count("odds"),
        "recommendations": service.repository.count("recommendations"),
        "bets": service.repository.count("bets"),
        "jobs": service.repository.count("jobs"),
    }
    jobs = sorted(
        service.repository.list_models("jobs", JobRun),
        key=lambda job: job.started_at,
        reverse=True,
    )
    recent_jobs = jobs[:recent_limit]
    issues = list(decision.issues)
    missing_job_types = [
        job_type
        for job_type in required_job_types
        if not any(job.job_type == job_type and job.status.value == "succeeded" for job in jobs)
    ]
    issues.extend(f"missing_recent_job:{job_type}" for job_type in missing_job_types)
    providers = _providers(recent_jobs)
    return ProductionStatusReport(
        checked_at=decision.checked_at,
        overall_status=_overall_status(decision, missing_job_types),
        ready_to_bet=decision.ready_to_bet and not missing_job_types,
        action=decision.action,
        decision=decision,
        counts=counts,
        recent_jobs=recent_jobs,
        providers=providers,
        issues=issues,
    )


def _run_decision(service: AnalysisService, include_past: bool) -> LiveDecisionReport:
    return run_live_decision(service.repository, service.settings, include_past=include_past)


def _overall_status(decision: LiveDecisionReport, missing_job_types: list[str]) -> str:
    if decision.ready_to_bet and not missing_job_types:
        return "ready"
    if decision.status in {"blocked", "paused"} or missing_job_types:
        return "blocked"
    return decision.status


def _providers(jobs: list[JobRun]) -> dict[str, dict[str, Any]]:
    providers: dict[str, dict[str, Any]] = {}
    for job in jobs:
        source = job.source or "unknown"
        current = providers.setdefault(source, {"recent_jobs": 0, "last_status": None, "last_finished_at": None})
        current["recent_jobs"] += 1
        if current["last_finished_at"] is None:
            current["last_status"] = job.status.value
            current["last_finished_at"] = job.finished_at
    return providers
