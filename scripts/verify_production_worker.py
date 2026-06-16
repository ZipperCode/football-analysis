from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import yaml

from football_analysis.models import (
    BetLog,
    IngestionResult,
    JobRun,
    JobStatus,
    MarketType,
    Match,
    OddsSnapshot,
    Recommendation,
    RecommendationStatus,
)
from football_analysis.production import run_production_cycle
from football_analysis.production import run_production_worker


class FakeIngestion:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def ingest_fixtures(self, **kwargs: object) -> IngestionResult:
        self.calls.append(("fixtures", kwargs))
        return _result("fixtures", inserted=2)

    def ingest_odds(self, **kwargs: object) -> IngestionResult:
        self.calls.append(("odds", kwargs))
        return _result("odds", inserted=3)

    def ingest_results(self, **kwargs: object) -> IngestionResult:
        self.calls.append(("results", kwargs))
        return _result("results", inserted=1, updated=1)


class FakeService:
    def __init__(self) -> None:
        self.ingestion = FakeIngestion()


def _result(kind: str, inserted: int = 0, updated: int = 0) -> IngestionResult:
    return IngestionResult(
        job=JobRun(
            id=f"job:{kind}",
            job_type=kind,
            source="fake",
            status=JobStatus.succeeded,
            summary={"inserted": inserted, "updated": updated},
        ),
        inserted=inserted,
        updated=updated,
        errors=[],
    )


def _assert_footballctl_argv(argv: list[str], expected_args: list[str]) -> None:
    assert argv[-len(expected_args):] == expected_args
    assert (
        Path(argv[0]).name.lower() in {"footballctl", "footballctl.exe"}
        or argv[:3] == [os.sys.executable, "-m", "football_analysis"]
    )


def test_production_cycle_runs_refresh_decision_and_daily_ops() -> None:
    service = FakeService()
    today = date(2026, 6, 11)

    report = run_production_cycle(
        service,
        run_date=today,
        leagues=["EPL", "SERIE_A"],
        fixture_source="api_football",
        odds_source="odds_api_io",
        include_results=True,
        include_daily_ops=True,
        include_past=False,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            issues=["odds_not_ready"],
        ),
        daily_ops_runner=lambda svc, day: {
            "date": str(day),
            "status": "blocked",
            "ready_to_bet": False,
            "action": "refresh_fixtures_and_odds",
            "issues": [],
            "preflight": {"status": "blocked"},
            "live_review": {"status": "monitoring"},
            "candidates": [{"id": str(index)} for index in range(100)],
        },
    )

    assert report.date == "2026-06-11"
    assert report.status == "blocked"
    assert report.action == "refresh_fixtures_and_odds"
    assert report.ready_to_bet is False
    assert report.leagues == ["EPL", "SERIE_A"]
    assert report.fixture_results[0].inserted == 2
    assert report.odds_results[1].inserted == 3
    assert report.result_results[0].updated == 1
    assert report.daily_ops == {
        "date": "2026-06-11",
        "status": "blocked",
        "ready_to_bet": False,
        "action": "refresh_fixtures_and_odds",
        "issues": [],
        "preflight_status": "blocked",
        "live_review_status": "monitoring",
    }
    assert "candidates" not in report.daily_ops
    assert report.issues == ["decision:odds_not_ready"]
    assert service.ingestion.calls == [
        ("fixtures", {"date": "2026-06-11", "source": "api_football", "league_code": "EPL"}),
        ("odds", {"date": "2026-06-11", "source": "odds_api_io", "league_code": "EPL", "max_events": None}),
        ("results", {"date": "2026-06-11", "source": "api_football", "league_code": "EPL"}),
        ("fixtures", {"date": "2026-06-11", "source": "api_football", "league_code": "SERIE_A"}),
        ("odds", {"date": "2026-06-11", "source": "odds_api_io", "league_code": "SERIE_A", "max_events": None}),
        ("results", {"date": "2026-06-11", "source": "api_football", "league_code": "SERIE_A"}),
    ]


def test_production_cycle_auto_refresh_uses_live_refresh_plan() -> None:
    service = FakeService()
    calls: list[dict[str, object]] = []

    def fake_refresh(**kwargs: object) -> SimpleNamespace:
        calls.append(dict(kwargs))
        return SimpleNamespace(
            date=kwargs["date"],
            scope=kwargs["scope"],
            fixture_source=kwargs["fixture_source"],
            odds_source=kwargs["odds_source"],
            requested_league=kwargs.get("league"),
            leagues=["SERIE_A"],
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            operations=[],
            refresh_requirements=[],
            fixture_results=[_result("fixtures", inserted=4)],
            odds_results=[_result("odds", inserted=2)],
            issues=["odds_refresh_empty:EPL"],
        )

    report = run_production_cycle(
        service,
        run_date=date(2026, 6, 11),
        leagues=["auto"],
        fixture_source="auto",
        odds_source="auto",
        include_results=False,
        include_daily_ops=False,
        auto_refresh=True,
        refresh_scope="active-profiles",
        allow_odds_fallback=True,
        refresh_runner=fake_refresh,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            issues=[],
        ),
    )

    assert len(calls) == 1
    assert calls[0]["fixture_source"] == "auto"
    assert calls[0]["odds_source"] == "auto"
    assert calls[0]["scope"] == "active-profiles"
    assert calls[0]["allow_odds_fallback"] is True
    assert report.refresh_mode == "auto"
    assert report.leagues == ["SERIE_A"]
    assert report.fixture_results[0].inserted == 4
    assert report.odds_results[0].inserted == 2
    assert report.refresh is not None
    assert report.refresh["reports"][0]["leagues"] == ["SERIE_A"]
    assert report.issues == ["refresh:odds_refresh_empty:EPL"]
    assert service.ingestion.calls == []


def test_production_cycle_refresh_dry_run_skips_auto_refresh_side_effects() -> None:
    service = FakeService()
    service.repository = SimpleNamespace()
    service.settings = SimpleNamespace(app=SimpleNamespace(tzinfo=UTC))
    calls: list[dict[str, object]] = []

    def fake_refresh(**kwargs: object) -> SimpleNamespace:
        calls.append(dict(kwargs))
        return SimpleNamespace(
            date=kwargs["date"],
            scope=kwargs["scope"],
            fixture_source=kwargs["fixture_source"],
            odds_source=kwargs["odds_source"],
            requested_league=kwargs.get("league"),
            dry_run=kwargs["dry_run"],
            leagues=["EPL"],
            status="planned",
            ready_to_bet=False,
            action="execute_live_refresh",
            operations=[],
            refresh_requirements=[],
            fixture_results=[],
            odds_results=[],
            issues=[],
        )

    def fail_execution(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("execution runner must not run during refresh dry-run")

    def fail_broker(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("broker runner must not run during refresh dry-run")

    report = run_production_cycle(
        service,
        run_date=date(2026, 6, 11),
        leagues=["auto"],
        fixture_source="auto",
        odds_source="auto",
        include_results=True,
        include_daily_ops=False,
        auto_refresh=True,
        refresh_dry_run=True,
        refresh_runner=fake_refresh,
        execution_mode="record-only",
        execution_runner=fail_execution,
        broker_discovery_mode="remote",
        broker_discovery_runner=fail_broker,
        broker_execution_mode="live",
        broker_execution_runner=fail_broker,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_bets",
            issues=[],
        ),
    )

    assert len(calls) == 1
    assert calls[0]["dry_run"] is True
    assert service.ingestion.calls == []
    assert report.status == "planned"
    assert report.action == "refresh_dry_run"
    assert report.ready_to_bet is False
    assert report.refresh_dry_run is True
    assert report.refresh is not None
    assert report.refresh["reports"][0]["dry_run"] is True
    assert "refresh_dry_run_enabled" in report.issues
    assert report.execution is not None
    assert report.execution["status"] == "skipped"
    assert report.execution["reason"] == "refresh_dry_run"
    assert report.broker_discovery is not None
    assert report.broker_discovery["status"] == "skipped"
    assert report.broker_discovery["reason"] == "refresh_dry_run"
    assert report.broker_execution is not None
    assert report.broker_execution["status"] == "skipped"
    assert report.broker_execution["reason"] == "refresh_dry_run"


def test_production_cycle_refresh_dry_run_skips_fixed_ingestion() -> None:
    service = FakeService()
    service.repository = SimpleNamespace()
    service.settings = SimpleNamespace(app=SimpleNamespace(tzinfo=UTC))

    report = run_production_cycle(
        service,
        run_date=date(2026, 6, 11),
        leagues=["EPL"],
        fixture_source="api_football",
        odds_source="odds_api_io",
        include_results=True,
        include_daily_ops=False,
        refresh_dry_run=True,
        execution_mode="record-only",
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_bets",
            issues=[],
        ),
    )

    assert service.ingestion.calls == []
    assert report.refresh_mode == "fixed"
    assert report.refresh_dry_run is True
    assert report.leagues == ["EPL"]
    assert report.fixture_results == []
    assert report.odds_results == []
    assert report.result_results == []
    assert report.ready_to_bet is False
    assert report.execution is not None
    assert report.execution["reason"] == "refresh_dry_run"


def test_production_cycle_expands_live_leagues_when_active_profiles_are_empty() -> None:
    service = FakeService()
    scopes: list[str] = []

    def fake_refresh(**kwargs: object) -> SimpleNamespace:
        scope = str(kwargs["scope"])
        scopes.append(scope)
        if scope == "active-profiles":
            return SimpleNamespace(
                date=kwargs["date"],
                scope=scope,
                fixture_source=kwargs["fixture_source"],
                odds_source=kwargs["odds_source"],
                requested_league=None,
                leagues=["EPL"],
                status="blocked",
                ready_to_bet=False,
                action="refresh_fixtures_and_odds",
                operations=[],
                refresh_requirements=[],
                fixture_results=[],
                odds_results=[],
                issues=["consider_scope_live_leagues"],
            )
        return SimpleNamespace(
            date=kwargs["date"],
            scope=scope,
            fixture_source=kwargs["fixture_source"],
            odds_source=kwargs["odds_source"],
            requested_league=None,
            leagues=["BRA_SERIE_A"],
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            operations=[],
            refresh_requirements=[],
            fixture_results=[_result("fixtures", inserted=8)],
            odds_results=[_result("odds", inserted=5)],
            issues=[],
        )

    report = run_production_cycle(
        service,
        run_date=date(2026, 6, 11),
        leagues=["auto"],
        fixture_source="auto",
        odds_source="auto",
        include_results=False,
        include_daily_ops=False,
        auto_refresh=True,
        refresh_scope="active-profiles",
        expand_live_leagues_on_empty=True,
        refresh_runner=fake_refresh,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            issues=[],
        ),
    )

    assert scopes == ["active-profiles", "live-leagues"]
    assert report.leagues == ["EPL", "BRA_SERIE_A"]
    assert report.fixture_results[0].inserted == 8
    assert report.odds_results[0].inserted == 5
    assert report.refresh is not None
    assert [item["scope"] for item in report.refresh["reports"]] == ["active-profiles", "live-leagues"]
    assert "refresh:consider_scope_live_leagues" in report.issues


def test_production_cycle_runs_execution_stage_when_ready() -> None:
    service = FakeService()
    calls: list[dict[str, object]] = []

    def fake_execution(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {
            "status": "dry_run",
            "mode": "dry-run",
            "execute_records": False,
            "platform": kwargs["platform"],
            "queue_status": "ready",
            "queue_count": 1,
            "selected_count": 1,
            "recorded_count": 0,
            "dry_run_count": 1,
            "error_count": 0,
            "issues": [],
            "queue": {
                "status": "ready",
                "ready_to_execute": True,
                "queue_count": 1,
                "queue_stake_units": 0.5,
                "items": [{"idempotency_key": "production-execution:test"}],
            },
            "records": [{"status": "dry_run"}],
        }

    service.repository = object()
    service.settings = SimpleNamespace(app=SimpleNamespace(tzinfo=UTC))
    report = run_production_cycle(
        service,
        run_date=date(2026, 6, 11),
        leagues=[],
        include_results=False,
        include_daily_ops=False,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
        ),
        execution_mode="dry-run",
        execution_platform="real",
        execution_max_items=1,
        execution_runner=fake_execution,
    )

    assert report.status == "ready"
    assert report.ready_to_bet is True
    assert len(calls) == 1
    assert calls[0]["platform"] == "real"
    assert calls[0]["execute_records"] is False
    assert calls[0]["max_items"] == 1
    assert report.execution is not None
    assert report.execution["status"] == "dry_run"
    assert report.execution["dry_run_count"] == 1
    assert report.execution_queue is not None
    assert report.execution_queue["queue_count"] == 1


def test_production_cycle_blocks_top_level_when_execution_queue_requires_review() -> None:
    from football_analysis.production import build_production_cycle_log

    service = FakeService()

    def fake_execution(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "status": "blocked",
            "mode": "dry-run",
            "execute_records": False,
            "platform": kwargs["platform"],
            "queue_status": "profile_review_required",
            "queue_count": 0,
            "selected_count": 0,
            "recorded_count": 0,
            "dry_run_count": 0,
            "error_count": 0,
            "issues": ["strategy_profile_required:2"],
            "queue": {
                "status": "profile_review_required",
                "ready_to_execute": False,
                "queue_count": 0,
                "queue_stake_units": 0,
                "candidate_count": 2,
                "profileless_candidate_count": 2,
                "issues": ["strategy_profile_required:2"],
                "items": [],
            },
            "records": [],
        }

    service.repository = object()
    service.settings = SimpleNamespace(app=SimpleNamespace(tzinfo=UTC))
    report = run_production_cycle(
        service,
        run_date=date(2026, 6, 13),
        leagues=[],
        include_results=False,
        include_daily_ops=False,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
        ),
        execution_mode="dry-run",
        execution_platform="real",
        execution_runner=fake_execution,
    )

    assert report.decision is not None
    assert report.decision["ready_to_bet"] is True
    assert report.status == "blocked"
    assert report.ready_to_bet is False
    assert report.action == "review_execution_queue"
    assert "execution_queue_not_ready:profile_review_required" in report.issues
    assert "execution_queue:strategy_profile_required:2" in report.issues
    summary = build_production_cycle_log(report)
    assert summary["summary"]["status"] == "blocked"
    assert summary["summary"]["ready_to_bet"] is False


def test_production_cycle_can_run_record_only_execution_stage() -> None:
    service = FakeService()
    calls: list[dict[str, object]] = []
    fills = {"production-execution:test": {"odds": 2.0, "stake_units": 0.2}}

    def fake_execution(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {
            "status": "executed",
            "mode": "record_only",
            "execute_records": True,
            "platform": kwargs["platform"],
            "queue_status": "ready",
            "queue_count": 1,
            "selected_count": 1,
            "recorded_count": 1,
            "dry_run_count": 0,
            "error_count": 0,
            "issues": [],
            "queue": {"status": "ready", "queue_count": 1, "queue_stake_units": 0.2},
            "records": [{"status": "recorded"}],
        }

    service.repository = object()
    service.settings = SimpleNamespace(app=SimpleNamespace(tzinfo=UTC))
    report = run_production_cycle(
        service,
        run_date=date(2026, 6, 11),
        leagues=[],
        include_results=False,
        include_daily_ops=False,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
        ),
        execution_mode="record-only",
        execution_platform="real",
        execution_fills=fills,
        require_execution_fills=True,
        execution_runner=fake_execution,
    )

    assert report.execution is not None
    assert report.execution["status"] == "executed"
    assert report.execution["recorded_count"] == 1
    assert calls[0]["execute_records"] is True
    assert calls[0]["fills"] == fills
    assert calls[0]["require_fills"] is True


def test_production_cycle_can_run_broker_stages_when_ready() -> None:
    service = FakeService()
    discovery_calls: list[dict[str, object]] = []
    execution_calls: list[dict[str, object]] = []

    def fake_broker_discovery(*args: object, **kwargs: object) -> dict[str, object]:
        discovery_calls.append(dict(kwargs))
        return {
            "status": "dry_run",
            "mode": "dry_run",
            "fetch_remote": False,
            "broker_id": kwargs["broker_id"],
            "selected_count": 1,
            "discovered_count": 0,
            "dry_run_count": 1,
            "error_count": 0,
            "issues": [],
            "records": [{"status": "dry_run"}],
            "applied_mappings": [],
        }

    def fake_broker_execution(*args: object, **kwargs: object) -> dict[str, object]:
        execution_calls.append(dict(kwargs))
        return {
            "status": "dry_run",
            "mode": "dry_run",
            "execute_broker_orders": False,
            "broker_id": kwargs["broker_id"],
            "selected_count": 1,
            "sent_count": 0,
            "dry_run_count": 1,
            "error_count": 0,
            "issues": [],
            "records": [{"status": "dry_run"}],
        }

    def fake_execution(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "status": "dry_run",
            "mode": "dry-run",
            "queue": {"status": "ready", "queue_count": 1, "queue_stake_units": 0.5},
            "selected_count": 1,
            "recorded_count": 0,
            "dry_run_count": 1,
            "error_count": 0,
            "issues": [],
            "records": [],
        }

    service.repository = object()
    service.settings = SimpleNamespace(app=SimpleNamespace(tzinfo=UTC))
    report = run_production_cycle(
        service,
        run_date=date(2026, 6, 11),
        leagues=[],
        include_results=False,
        include_daily_ops=False,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
        ),
        execution_mode="dry-run",
        execution_runner=fake_execution,
        broker_id="betfair_exchange",
        broker_discovery_mode="dry-run",
        broker_discovery_max_items=1,
        broker_discovery_max_results=7,
        broker_discovery_match_window_hours=12,
        broker_discovery_runner=fake_broker_discovery,
        broker_execution_mode="dry-run",
        broker_execution_max_items=1,
        broker_execution_runner=fake_broker_execution,
    )

    assert report.broker_discovery is not None
    assert report.broker_discovery["status"] == "dry_run"
    assert report.broker_execution is not None
    assert report.broker_execution["status"] == "dry_run"
    assert len(discovery_calls) == 1
    assert discovery_calls[0]["broker_id"] == "betfair_exchange"
    assert discovery_calls[0]["fetch_remote"] is False
    assert discovery_calls[0]["apply_mappings"] is False
    assert discovery_calls[0]["max_items"] == 1
    assert discovery_calls[0]["max_results"] == 7
    assert discovery_calls[0]["match_window_hours"] == 12
    assert len(execution_calls) == 1
    assert execution_calls[0]["execute_broker_orders"] is False
    assert execution_calls[0]["max_items"] == 1


def test_production_cycle_can_run_remote_apply_and_live_broker_modes() -> None:
    service = FakeService()
    discovery_calls: list[dict[str, object]] = []
    execution_calls: list[dict[str, object]] = []

    def fake_broker_discovery(*args: object, **kwargs: object) -> dict[str, object]:
        discovery_calls.append(dict(kwargs))
        return {
            "status": "discovered",
            "mode": "remote_read",
            "fetch_remote": True,
            "broker_id": kwargs["broker_id"],
            "selected_count": 1,
            "discovered_count": 1,
            "dry_run_count": 0,
            "error_count": 0,
            "issues": [],
            "records": [{"status": "discovered"}],
            "applied_mappings": [{"status": "applied"}],
        }

    def fake_broker_execution(*args: object, **kwargs: object) -> dict[str, object]:
        execution_calls.append(dict(kwargs))
        return {
            "status": "executed",
            "mode": "broker_live",
            "execute_broker_orders": True,
            "broker_id": kwargs["broker_id"],
            "selected_count": 1,
            "sent_count": 1,
            "dry_run_count": 0,
            "error_count": 0,
            "issues": [],
            "records": [{"status": "sent"}],
        }

    def fake_execution(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "status": "dry_run",
            "mode": "dry-run",
            "queue": {"status": "ready", "queue_count": 1, "queue_stake_units": 0.5},
            "selected_count": 1,
            "recorded_count": 0,
            "dry_run_count": 1,
            "error_count": 0,
            "issues": [],
            "records": [],
        }

    service.repository = object()
    service.settings = SimpleNamespace(app=SimpleNamespace(tzinfo=UTC))
    report = run_production_cycle(
        service,
        run_date=date(2026, 6, 11),
        leagues=[],
        include_results=False,
        include_daily_ops=False,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
        ),
        execution_mode="dry-run",
        execution_runner=fake_execution,
        broker_discovery_mode="apply",
        broker_discovery_runner=fake_broker_discovery,
        broker_execution_mode="live",
        broker_execution_runner=fake_broker_execution,
    )

    assert report.broker_discovery["status"] == "discovered"
    assert discovery_calls[0]["fetch_remote"] is True
    assert discovery_calls[0]["apply_mappings"] is True
    assert report.broker_execution["status"] == "executed"
    assert execution_calls[0]["execute_broker_orders"] is True


def test_production_cycle_records_heartbeat_job() -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.jobs: dict[str, JobRun] = {}

        def upsert_model(self, bucket: str, record_id: str, model: object) -> None:
            assert bucket == "jobs"
            self.jobs[record_id] = model

    service = FakeService()
    service.repository = FakeRepository()
    report = run_production_cycle(
        service,
        run_date=date(2026, 6, 11),
        leagues=["EPL"],
        include_results=False,
        include_daily_ops=False,
        execution_mode="off",
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            issues=["odds_not_ready"],
        ),
    )

    assert len(service.repository.jobs) == 1
    job = next(iter(service.repository.jobs.values()))
    assert job.job_type == "production_cycle"
    assert job.status == JobStatus.partial
    assert job.summary["date"] == "2026-06-11"
    assert job.summary["status"] == "blocked"
    assert job.summary["action"] == "refresh_fixtures_and_odds"
    assert job.summary["ready_to_bet"] is False
    assert job.summary["fixture_count"] == "inserted:2,updated:0,errors:0"
    assert job.summary["odds_count"] == "inserted:3,updated:0,errors:0"
    assert job.summary["execution_status"] is None
    assert report.issues == ["decision:odds_not_ready"]


def test_production_cycle_can_run_data_apply_stage() -> None:
    service = FakeService()
    service.repository = SimpleNamespace()
    service.settings = SimpleNamespace(app=SimpleNamespace(tzinfo=UTC))
    calls: list[dict[str, object]] = []

    def fake_data_apply_runner(svc: object, **kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "status": "succeeded",
            "execute": kwargs["execute"],
            "allow_remote": kwargs["allow_remote"],
            "selected_count": 1,
            "succeeded_count": 1,
            "failed_count": 0,
            "commands": [],
        }

    report = run_production_cycle(
        service,
        run_date=date(2026, 6, 11),
        leagues=["EPL"],
        include_results=False,
        include_daily_ops=False,
        execution_mode="off",
        data_apply_mode="safe",
        data_apply_include_backtests=False,
        data_apply_include_blocked_prerequisites=True,
        data_apply_max_commands=3,
        data_apply_timeout_seconds=12,
        data_apply_historical_odds_start_time="2026-01-01T12:00:00Z",
        data_apply_historical_odds_end_time="2026-01-01T12:20:00Z",
        data_apply_historical_odds_interval_minutes=10,
        data_apply_historical_odds_max_snapshots=2,
        data_apply_historical_odds_max_events=5,
        data_apply_runner=fake_data_apply_runner,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            issues=[],
        ),
    )

    assert report.data_apply is not None
    assert report.data_apply["status"] == "succeeded"
    assert calls == [
        {
            "include_past": False,
            "execute": True,
            "allow_remote": False,
            "include_backtests": False,
            "include_blocked_prerequisites": True,
            "max_commands": 3,
            "timeout_seconds": 12,
            "historical_odds_start_time": "2026-01-01T12:00:00Z",
            "historical_odds_end_time": "2026-01-01T12:20:00Z",
            "historical_odds_interval_minutes": 10,
            "historical_odds_max_snapshots": 2,
            "historical_odds_max_events": 5,
        }
    ]


def test_production_worker_passes_data_apply_stage_options() -> None:
    calls: list[dict[str, object]] = []

    def service_factory() -> object:
        service = FakeService()
        service.repository = SimpleNamespace()
        service.settings = SimpleNamespace(app=SimpleNamespace(tzinfo=UTC))
        return service

    def fake_data_apply_runner(svc: object, **kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "status": "dry_run",
            "execute": kwargs["execute"],
            "allow_remote": kwargs["allow_remote"],
            "selected_count": 2,
            "succeeded_count": 0,
            "failed_count": 0,
            "commands": [],
        }

    reports = run_production_worker(
        service_factory,
        leagues=["EPL"],
        once=True,
        include_results=False,
        include_daily_ops=False,
        execution_mode="off",
        data_apply_mode="remote",
        data_apply_include_backtests=False,
        data_apply_max_commands=2,
        data_apply_historical_odds_start_time="2026-01-01T12:00:00Z",
        data_apply_historical_odds_end_time="2026-01-01T12:20:00Z",
        data_apply_historical_odds_interval_minutes=10,
        data_apply_historical_odds_max_snapshots=2,
        data_apply_historical_odds_max_events=5,
        data_apply_runner=fake_data_apply_runner,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            issues=[],
        ),
    )

    assert len(reports) == 1
    assert reports[0].data_apply is not None
    assert reports[0].data_apply["allow_remote"] is True
    assert calls[0]["execute"] is True
    assert calls[0]["allow_remote"] is True
    assert calls[0]["include_backtests"] is False
    assert calls[0]["max_commands"] == 2
    assert calls[0]["historical_odds_start_time"] == "2026-01-01T12:00:00Z"
    assert calls[0]["historical_odds_end_time"] == "2026-01-01T12:20:00Z"
    assert calls[0]["historical_odds_interval_minutes"] == 10
    assert calls[0]["historical_odds_max_snapshots"] == 2
    assert calls[0]["historical_odds_max_events"] == 5


def test_production_worker_passes_refresh_dry_run_to_auto_refresh() -> None:
    services: list[FakeService] = []
    calls: list[dict[str, object]] = []

    def service_factory() -> FakeService:
        service = FakeService()
        service.settings = SimpleNamespace(app=SimpleNamespace(tzinfo=UTC))
        services.append(service)
        return service

    def fake_refresh(**kwargs: object) -> SimpleNamespace:
        calls.append(dict(kwargs))
        return SimpleNamespace(
            date=kwargs["date"],
            scope=kwargs["scope"],
            fixture_source=kwargs["fixture_source"],
            odds_source=kwargs["odds_source"],
            requested_league=kwargs.get("league"),
            dry_run=kwargs["dry_run"],
            leagues=["EPL"],
            status="planned",
            ready_to_bet=False,
            action="execute_live_refresh",
            operations=[],
            refresh_requirements=[],
            fixture_results=[],
            odds_results=[],
            issues=[],
        )

    reports = run_production_worker(
        service_factory,
        leagues=["auto"],
        once=True,
        include_results=True,
        include_daily_ops=False,
        auto_refresh=True,
        refresh_dry_run=True,
        refresh_runner=fake_refresh,
        execution_mode="off",
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_bets",
            issues=[],
        ),
    )

    assert len(reports) == 1
    assert len(calls) == 1
    assert calls[0]["dry_run"] is True
    assert reports[0].refresh_dry_run is True
    assert reports[0].ready_to_bet is False
    assert services[0].ingestion.calls == []


def test_production_alert_message_summarizes_blocked_report() -> None:
    from football_analysis.production import format_production_alert

    report = run_production_cycle(
        FakeService(),
        run_date=date(2026, 6, 11),
        leagues=["EPL"],
        include_results=False,
        include_daily_ops=False,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            issues=["odds_not_ready", "no_live_gate_passed_candidates"],
        ),
    )

    message = format_production_alert(report)

    assert "football-analysis production" in message
    assert "date=2026-06-11" in message
    assert "status=blocked" in message
    assert "ready_to_bet=false" in message
    assert "action=refresh_fixtures_and_odds" in message
    assert "leagues=EPL" in message
    assert "issues=decision:odds_not_ready; decision:no_live_gate_passed_candidates" in message


def test_production_cycle_log_is_compact_for_worker_logs() -> None:
    from football_analysis.production import ProductionCycleReport, build_production_cycle_log

    report = ProductionCycleReport(
        date="2026-06-13",
        status="blocked",
        action="review_execution_queue",
        ready_to_bet=False,
        leagues=["BRA_SERIE_A"],
        fixture_source="api_football",
        odds_source="odds_api_io",
        result_source="api_football",
        data_apply={
            "status": "dry_run",
            "execute": False,
            "allow_remote": False,
            "selected_count": 1,
            "succeeded_count": 0,
            "failed_count": 0,
            "commands": [
                {
                    "command": "footballctl ingest odds --source odds_api_io --league EPL --max-events 20 --json",
                    "task_type": "live_odds",
                }
            ],
        },
        execution_queue={
            "status": "profile_review_required",
            "ready_to_execute": False,
            "queue_count": 0,
            "candidate_count": 2,
            "profileless_candidate_count": 2,
            "items": [{"record_bet_command": "footballctl record-bet ..."}],
            "issues": ["strategy_profile_required:2"],
        },
        issues=["strategy_profile_required:2"],
    )

    payload = build_production_cycle_log(report)
    encoded = json.dumps(payload, ensure_ascii=False)

    assert payload["type"] == "production_cycle"
    assert payload["summary"]["data_apply_selected_count"] == 1
    assert payload["execution_queue"]["profileless_candidate_count"] == 2
    assert "commands" not in encoded
    assert "footballctl ingest odds" not in encoded
    assert "items" not in encoded
    assert "record-bet" not in encoded


def test_production_worker_notifies_each_report() -> None:
    from football_analysis.production import format_production_alert

    messages: list[str] = []

    class FakeSettings:
        class App:
            tzinfo = None

        app = App()

    class WorkerService(FakeService):
        settings = FakeSettings()

    reports = run_production_worker(
        service_factory=WorkerService,
        leagues=["EPL"],
        include_results=False,
        include_daily_ops=False,
        once=True,
        on_report=lambda report: messages.append(format_production_alert(report)),
        sleep_fn=lambda seconds: None,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            issues=["odds_not_ready"],
        ),
    )

    assert len(reports) == 1
    assert len(messages) == 1
    assert "status=blocked" in messages[0]


def test_production_cli_exposes_alert_text_option() -> None:
    worker_help = subprocess.check_output(
        ["footballctl", "production-worker", "--help"],
        text=True,
        encoding="utf-8",
    )
    worker_env_help = subprocess.check_output(
        ["footballctl", "production-worker-env", "--help"],
        text=True,
        encoding="utf-8",
    )
    cycle_help = subprocess.check_output(
        ["footballctl", "production-cycle", "--help"],
        text=True,
        encoding="utf-8",
    )

    assert "--alert-text" in worker_help
    assert "--alert-text" in worker_env_help
    assert "--alert-text" in cycle_help
    assert "--notify-telegram" in worker_help
    assert "--notify-telegram" in worker_env_help
    assert "--notify-telegram" in cycle_help
    assert "--once" in worker_env_help
    assert "--json" in worker_env_help
    assert "--auto-refresh" in worker_help
    assert "--auto-refresh" in cycle_help
    assert "--fixed-leagues" in worker_help
    assert "--fixed-leagues" in cycle_help
    assert "--refresh-scope" in worker_help
    assert "--refresh-scope" in cycle_help
    assert "--expand-live" in worker_help
    assert "--expand-live" in cycle_help
    assert "--refresh-dry-run" in worker_help
    assert "--refresh-dry-run" in cycle_help
    assert "--execution-mode" in worker_help
    assert "--execution-mode" in cycle_help
    assert "--execution-fills" in worker_help
    assert "--execution-fills" in cycle_help
    assert "--require-execut" in worker_help
    assert "--require-execut" in cycle_help


def test_telegram_alert_skips_without_credentials() -> None:
    from football_analysis.production import send_telegram_alert

    calls: list[object] = []
    result = send_telegram_alert(
        "hello",
        bot_token="",
        chat_id="",
        request_fn=lambda *args: calls.append(args),
    )

    assert result.enabled is False
    assert result.sent is False
    assert result.skipped_reason == "missing_credentials"
    assert calls == []


def test_telegram_alert_posts_send_message_payload() -> None:
    from football_analysis.production import send_telegram_alert

    calls: list[tuple[str, dict[str, object], float]] = []

    def fake_request(url: str, payload: dict[str, object], timeout_seconds: float) -> dict[str, object]:
        calls.append((url, payload, timeout_seconds))
        return {"ok": True, "result": {"message_id": 42}}

    result = send_telegram_alert(
        "football-analysis production",
        bot_token="token-123",
        chat_id="chat-456",
        request_fn=fake_request,
        timeout_seconds=3.5,
    )

    assert result.enabled is True
    assert result.sent is True
    assert result.status_code is None
    assert calls == [
        (
            "https://api.telegram.org/bottoken-123/sendMessage",
            {
                "chat_id": "chat-456",
                "text": "football-analysis production",
                "disable_web_page_preview": True,
            },
            3.5,
        )
    ]


def test_cli_telegram_notification_result_is_json() -> None:
    worker_env = os.environ.copy()
    worker_env.update(
        {
            "WORKER_ONCE": "1",
            "WORKER_JSON": "1",
            "WORKER_NOTIFY_TELEGRAM": "1",
            "WORKER_REFRESH_DRY_RUN": "1",
            "WORKER_INCLUDE_RESULTS": "0",
            "WORKER_INCLUDE_DAILY_OPS": "0",
            "WORKER_EXECUTION_MODE": "off",
            "WORKER_DATA_APPLY_MODE": "dry-run",
            "WORKER_BROKER_DISCOVERY_MODE": "off",
            "WORKER_BROKER_EXECUTION_MODE": "off",
            "WORKER_REQUIRE_DEPLOY_READY": "0",
        }
    )
    output = subprocess.check_output(
        [
            "footballctl",
            "production-worker-env",
            "--once",
            "--notify-telegram",
        ],
        text=True,
        encoding="utf-8",
        env=worker_env,
    )

    last_line = [line for line in output.splitlines() if line.strip()][-1]
    payload = json.loads(last_line)

    assert payload["telegram"]["enabled"] is False
    assert payload["telegram"]["sent"] is False
    assert payload["telegram"]["skipped_reason"] == "missing_credentials"


def test_production_worker_env_can_enforce_startup_deploy_gate() -> None:
    worker_env = os.environ.copy()
    worker_env.update(
        {
            "PRODUCTION_DEPLOY_TARGET": "broker-live",
            "WORKER_REQUIRE_DEPLOY_READY": "1",
            "WORKER_NOTIFY_TELEGRAM": "0",
            "WORKER_REFRESH_DRY_RUN": "1",
            "WORKER_INCLUDE_RESULTS": "0",
            "WORKER_INCLUDE_DAILY_OPS": "0",
            "WORKER_EXECUTION_MODE": "off",
            "WORKER_DATA_APPLY_MODE": "dry-run",
            "WORKER_BROKER_DISCOVERY_MODE": "off",
            "WORKER_BROKER_EXECUTION_MODE": "off",
        }
    )
    result = subprocess.run(
        [
            "footballctl",
            "production-worker-env",
            "--once",
            "--json",
        ],
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=worker_env,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"
    assert payload["production_deploy_check"]["target"] == "broker-live"
    assert payload["production_deploy_check"]["status"] == "blocked"


def test_production_status_summarizes_recent_jobs_and_counts() -> None:
    from football_analysis.production import build_production_status

    class FakeRepository:
        def __init__(self) -> None:
            base = datetime(2026, 6, 11, 12, 0, 0)
            self.jobs = [
                JobRun(
                    id="job-1",
                    job_type="ingest_fixtures",
                    status=JobStatus.succeeded,
                    source="api_football",
                    started_at=base + timedelta(minutes=1),
                    summary={"matches": 12},
                ),
                JobRun(
                    id="job-2",
                    job_type="ingest_odds",
                    status=JobStatus.succeeded,
                    source="odds_api_io",
                    started_at=base + timedelta(minutes=2),
                    summary={"odds_snapshots": 34},
                ),
                JobRun(
                    id="job-3",
                    job_type="production_cycle",
                    status=JobStatus.succeeded,
                    source="production",
                    started_at=base,
                    summary={"status": "ready", "ready_to_bet": True},
                ),
            ]

        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            assert bucket == "jobs"
            return list(self.jobs)

        def count(self, bucket: str) -> int:
            return {
                "matches": 12,
                "odds": 34,
                "bets": 2,
                "recommendations": 5,
                "jobs": len(self.jobs),
            }[bucket]

        def quota_snapshot(self, provider: str) -> dict[str, int]:
            return {"minute": 1, "hour": 3, "day": 9}

        def cache_count(self, provider: str | None = None) -> int:
            return 7 if provider == "api_football" else 11

    service = SimpleNamespace(
        repository=FakeRepository(),
        settings=SimpleNamespace(
            data_sources={
                "api_football": SimpleNamespace(enabled=True),
                "odds_api_io": SimpleNamespace(enabled=True),
            }
        ),
    )

    status = build_production_status(
        service,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            issues=["odds_not_ready"],
            components={"preflight": "blocked"},
        ),
    )

    assert status["overall_status"] == "blocked"
    assert status["ready_to_bet"] is False
    assert status["action"] == "refresh_fixtures_and_odds"
    assert status["decision"]["issues"] == ["odds_not_ready"]
    assert status["counts"] == {
        "matches": 12,
        "odds": 34,
        "bets": 2,
        "recommendations": 5,
                "jobs": 3,
    }
    assert status["recent_jobs"][0]["job_type"] == "ingest_odds"
    assert status["recent_jobs"][1]["job_type"] == "ingest_fixtures"
    assert status["providers"]["api_football"]["quota"] == {"minute": 1, "hour": 3, "day": 9}
    assert status["providers"]["api_football"]["cache_entries"] == 7
    assert status["issues"] == []


def test_production_status_blocks_ready_decision_when_execution_queue_requires_review() -> None:
    from football_analysis.production import build_production_status
    from football_analysis.settings import AppSettings, Settings

    class FakeRepository:
        def __init__(self) -> None:
            now = datetime(2026, 6, 13, 10, 0, tzinfo=UTC)
            self.jobs = [
                JobRun(
                    id="status-production-cycle",
                    job_type="production_cycle",
                    status=JobStatus.succeeded,
                    source="production",
                    started_at=now,
                    finished_at=now,
                    summary={"status": "ready", "ready_to_bet": True},
                )
            ]

        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "jobs":
                return list(self.jobs)
            return []

        def count(self, bucket: str) -> int:
            return {"matches": 0, "odds": 0, "bets": 0, "recommendations": 0, "jobs": len(self.jobs)}.get(bucket, 0)

        def quota_snapshot(self, provider: str) -> dict[str, int]:
            return {}

        def cache_count(self, provider: str | None = None) -> int:
            return 0

    def queue_runner(service: object, include_past: bool = False) -> dict[str, object]:
        return {
            "status": "profile_review_required",
            "ready_to_execute": False,
            "queue_count": 0,
            "queue_stake_units": 0,
            "candidate_count": 2,
            "profileless_candidate_count": 2,
            "issues": ["strategy_profile_required:2"],
        }

    status = build_production_status(
        SimpleNamespace(
            repository=FakeRepository(),
            settings=Settings(app=AppSettings(timezone="UTC")),
        ),
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={"preflight": "ready"},
        ),
        execution_queue_runner=queue_runner,
    )

    assert status["decision"]["ready_to_bet"] is True
    assert status["overall_status"] == "blocked"
    assert status["ready_to_bet"] is False
    assert status["action"] == "review_execution_queue"
    assert status["execution_queue"]["status"] == "profile_review_required"
    assert status["execution_queue"]["profileless_candidate_count"] == 2
    assert "execution_queue_not_ready:profile_review_required" in status["issues"]
    assert "execution_queue:strategy_profile_required:2" in status["issues"]


def test_production_status_flags_missing_recent_odds_job() -> None:
    from football_analysis.production import build_production_status

    service = SimpleNamespace(
        repository=SimpleNamespace(
            list_models=lambda bucket, model_type: [
                JobRun(id="job-0", job_type="production_cycle", status=JobStatus.partial, source="production"),
                JobRun(id="job-1", job_type="ingest_fixtures", status=JobStatus.succeeded, source="api_football"),
            ],
            count=lambda bucket: 0,
            quota_snapshot=lambda provider: {},
            cache_count=lambda provider=None: 0,
        ),
        settings=SimpleNamespace(data_sources={}),
    )

    status = build_production_status(
        service,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            issues=[],
            components={},
        ),
    )

    assert "missing_recent_job:ingest_odds" in status["issues"]
    assert status["overall_status"] == "blocked"


def test_production_status_uses_all_jobs_for_required_job_health() -> None:
    from football_analysis.production import build_production_status

    base = datetime(2026, 6, 11, 12, 0, 0)
    jobs = [
        JobRun(
            id="odds-empty-2",
            job_type="ingest_odds",
            status=JobStatus.succeeded,
            source="odds_api_io",
            started_at=base + timedelta(minutes=4),
            summary={"odds_snapshots": 0},
        ),
        JobRun(
            id="odds-empty-1",
            job_type="ingest_odds",
            status=JobStatus.succeeded,
            source="odds_api_io",
            started_at=base + timedelta(minutes=3),
            summary={"odds_snapshots": 0},
        ),
        JobRun(
            id="odds-positive",
            job_type="ingest_odds",
            status=JobStatus.succeeded,
            source="odds_api_io",
            started_at=base + timedelta(minutes=2),
            summary={"odds_snapshots": 13},
        ),
        JobRun(
            id="fixtures-positive",
            job_type="ingest_fixtures",
            status=JobStatus.succeeded,
            source="api_football",
            started_at=base + timedelta(minutes=1),
            summary={"matches": 80},
        ),
        JobRun(
            id="production-cycle",
            job_type="production_cycle",
            status=JobStatus.succeeded,
            source="production",
            started_at=base,
            summary={"status": "ready", "ready_to_bet": True},
        ),
    ]

    service = SimpleNamespace(
        repository=SimpleNamespace(
            list_models=lambda bucket, model_type: list(jobs),
            count=lambda bucket: 0,
            quota_snapshot=lambda provider: {},
            cache_count=lambda provider=None: 0,
        ),
        settings=SimpleNamespace(data_sources={}),
    )

    status = build_production_status(
        service,
        recent_limit=2,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            issues=[],
            components={},
        ),
    )

    assert "missing_recent_job:ingest_fixtures" not in status["issues"]
    assert "missing_recent_job:production_cycle" not in status["issues"]
    assert "empty_recent_job:ingest_odds" not in status["issues"]
    assert [job["id"] for job in status["recent_jobs"]] == ["odds-empty-2", "odds-empty-1"]


def test_production_status_ignores_refresh_dry_run_for_required_heartbeat() -> None:
    from football_analysis.production import build_production_status

    base = datetime(2026, 6, 11, 12, 0, 0)
    jobs = [
        JobRun(
            id="cycle-refresh-dry-run",
            job_type="production_cycle",
            status=JobStatus.partial,
            source="production",
            started_at=base + timedelta(minutes=4),
            summary={"status": "planned", "refresh_dry_run": True},
        ),
        JobRun(
            id="odds-fresh",
            job_type="ingest_odds",
            status=JobStatus.succeeded,
            source="odds_api_io",
            started_at=base + timedelta(minutes=3),
            summary={"odds_snapshots": 13},
        ),
        JobRun(
            id="fixtures-fresh",
            job_type="ingest_fixtures",
            status=JobStatus.succeeded,
            source="api_football",
            started_at=base + timedelta(minutes=2),
            summary={"matches": 80},
        ),
        JobRun(
            id="cycle-real",
            job_type="production_cycle",
            status=JobStatus.succeeded,
            source="production",
            started_at=base + timedelta(minutes=1),
            summary={"status": "ready", "ready_to_bet": True},
        ),
    ]
    service = SimpleNamespace(
        repository=SimpleNamespace(
            list_models=lambda bucket, model_type: list(jobs),
            count=lambda bucket: 0,
            quota_snapshot=lambda provider: {},
            cache_count=lambda provider=None: 0,
        ),
        settings=SimpleNamespace(data_sources={}),
    )

    status = build_production_status(
        service,
        recent_limit=2,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )

    assert "latest_job_not_succeeded:production_cycle:partial" not in status["issues"]
    assert "missing_recent_job:production_cycle" not in status["issues"]
    assert [job["id"] for job in status["recent_jobs"]][0] == "cycle-refresh-dry-run"


def test_production_health_reports_fresh_pipeline() -> None:
    from football_analysis.production import build_production_health

    now = datetime.now(UTC)
    jobs = [
        JobRun(
            id="cycle-fresh",
            job_type="production_cycle",
            status=JobStatus.succeeded,
            source="production",
            started_at=now - timedelta(minutes=4),
            finished_at=now - timedelta(minutes=3),
            summary={"status": "ready", "ready_to_bet": True},
        ),
        JobRun(
            id="cycle-refresh-dry-run",
            job_type="production_cycle",
            status=JobStatus.partial,
            source="production",
            started_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1),
            summary={"status": "planned", "refresh_dry_run": True},
        ),
        JobRun(
            id="fixtures-fresh",
            job_type="ingest_fixtures",
            status=JobStatus.succeeded,
            source="api_football",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=19),
            summary={"matches": 12},
        ),
        JobRun(
            id="odds-fresh",
            job_type="ingest_odds",
            status=JobStatus.succeeded,
            source="odds_api_io",
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=9),
            summary={"odds_snapshots": 34},
        ),
    ]
    service = SimpleNamespace(
        repository=SimpleNamespace(
            list_models=lambda bucket, model_type: list(jobs) if bucket == "jobs" else [],
            count=lambda bucket: len(jobs) if bucket == "jobs" else 0,
            quota_snapshot=lambda provider: {},
            cache_count=lambda provider=None: 0,
        ),
        settings=SimpleNamespace(data_sources={}),
    )

    health = build_production_health(
        service,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )

    assert health["status"] == "healthy"
    assert health["issues"] == []
    assert health["job_health"]["production_cycle"]["status"] == "ok"
    assert health["job_health"]["production_cycle"]["latest_job"]["id"] == "cycle-fresh"


def test_production_health_treats_blocked_cycle_as_valid_heartbeat() -> None:
    from football_analysis.production import build_production_health

    now = datetime.now(UTC)
    jobs = [
        JobRun(
            id="cycle-blocked",
            job_type="production_cycle",
            status=JobStatus.partial,
            source="production",
            started_at=now - timedelta(minutes=4),
            finished_at=now - timedelta(minutes=3),
            summary={
                "status": "blocked",
                "action": "review_execution_queue",
                "ready_to_bet": False,
                "queue_status": "profile_review_required",
            },
        ),
        JobRun(
            id="fixtures-fresh",
            job_type="ingest_fixtures",
            status=JobStatus.succeeded,
            source="api_football",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=19),
            summary={"matches": 12},
        ),
        JobRun(
            id="odds-fresh",
            job_type="ingest_odds",
            status=JobStatus.succeeded,
            source="odds_api_io",
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=9),
            summary={"odds_snapshots": 34},
        ),
    ]
    service = SimpleNamespace(
        repository=SimpleNamespace(
            list_models=lambda bucket, model_type: list(jobs) if bucket == "jobs" else [],
            count=lambda bucket: {"matches": 12, "odds": 34, "jobs": len(jobs)}.get(bucket, 0),
            quota_snapshot=lambda provider: {},
            cache_count=lambda provider=None: 0,
        ),
        settings=SimpleNamespace(data_sources={}),
    )

    health = build_production_health(
        service,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
        execution_queue_runner=lambda svc, include_past=False: {
            "status": "profile_review_required",
            "ready_to_execute": False,
            "queue_count": 0,
            "queue_stake_units": 0,
            "candidate_count": 2,
            "profileless_candidate_count": 2,
            "issues": ["strategy_profile_required:2"],
        },
    )

    assert health["issues"] == []
    assert health["status"] == "degraded"
    assert health["job_health"]["production_cycle"]["status"] == "ok"
    assert "execution_queue_not_ready:profile_review_required" in health["warnings"]
    assert "latest_job_not_succeeded:production_cycle:partial" not in health["production_status"]["issues"]
    assert health["job_health"]["ingest_fixtures"]["status"] == "ok"
    assert health["job_health"]["ingest_odds"]["status"] == "ok"


def test_production_health_treats_empty_refresh_as_warning_when_data_exists() -> None:
    from football_analysis.production import build_production_health

    now = datetime.now(UTC)
    jobs = [
        JobRun(
            id="cycle-fresh",
            job_type="production_cycle",
            status=JobStatus.succeeded,
            source="production",
            started_at=now - timedelta(minutes=4),
            finished_at=now - timedelta(minutes=3),
            summary={"status": "ready", "ready_to_bet": True},
        ),
        JobRun(
            id="fixtures-empty",
            job_type="ingest_fixtures",
            status=JobStatus.succeeded,
            source="api_football",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=19),
            summary={"matches": 0},
        ),
        JobRun(
            id="odds-fresh",
            job_type="ingest_odds",
            status=JobStatus.succeeded,
            source="odds_api_io",
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=9),
            summary={"odds_snapshots": 34},
        ),
    ]
    service = SimpleNamespace(
        repository=SimpleNamespace(
            list_models=lambda bucket, model_type: list(jobs) if bucket == "jobs" else [],
            count=lambda bucket: {"matches": 58, "odds": 115, "bets": 0, "recommendations": 58, "jobs": len(jobs)}.get(bucket, 0),
            quota_snapshot=lambda provider: {},
            cache_count=lambda provider=None: 0,
        ),
        settings=SimpleNamespace(data_sources={}),
    )

    health = build_production_health(
        service,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )

    assert health["status"] == "healthy"
    assert health["issues"] == []
    assert health["warnings"] == ["empty_recent_job:ingest_fixtures"]
    assert health["job_health"]["ingest_fixtures"]["status"] == "ok"
    assert health["job_health"]["ingest_fixtures"]["empty_recent_jobs"] is True
    assert health["production_status"]["issues"] == ["empty_recent_job:ingest_fixtures"]


def test_production_health_flags_empty_refresh_when_no_data_exists() -> None:
    from football_analysis.production import build_production_health

    now = datetime.now(UTC)
    jobs = [
        JobRun(
            id="cycle-fresh",
            job_type="production_cycle",
            status=JobStatus.succeeded,
            source="production",
            started_at=now - timedelta(minutes=4),
            finished_at=now - timedelta(minutes=3),
            summary={"status": "ready", "ready_to_bet": True},
        ),
        JobRun(
            id="fixtures-empty",
            job_type="ingest_fixtures",
            status=JobStatus.succeeded,
            source="api_football",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=19),
            summary={"matches": 0},
        ),
        JobRun(
            id="odds-fresh",
            job_type="ingest_odds",
            status=JobStatus.succeeded,
            source="odds_api_io",
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=9),
            summary={"odds_snapshots": 34},
        ),
    ]
    service = SimpleNamespace(
        repository=SimpleNamespace(
            list_models=lambda bucket, model_type: list(jobs) if bucket == "jobs" else [],
            count=lambda bucket: {"matches": 0, "odds": 34, "bets": 0, "recommendations": 0, "jobs": len(jobs)}.get(bucket, 0),
            quota_snapshot=lambda provider: {},
            cache_count=lambda provider=None: 0,
        ),
        settings=SimpleNamespace(data_sources={}),
    )

    health = build_production_health(
        service,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )

    assert health["status"] == "degraded"
    assert "empty_job:ingest_fixtures" in health["issues"]
    assert "empty_recent_job:ingest_fixtures" in health["issues"]
    assert health["warnings"] == []


def test_production_health_allows_fresh_running_job() -> None:
    from football_analysis.production import build_production_health

    now = datetime.now(UTC)
    jobs = [
        JobRun(
            id="cycle-fresh",
            job_type="production_cycle",
            status=JobStatus.succeeded,
            source="production",
            started_at=now - timedelta(minutes=4),
            finished_at=now - timedelta(minutes=3),
            summary={"status": "ready", "ready_to_bet": True},
        ),
        JobRun(
            id="fixtures-fresh",
            job_type="ingest_fixtures",
            status=JobStatus.succeeded,
            source="api_football",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=19),
            summary={"matches": 12},
        ),
        JobRun(
            id="odds-running",
            job_type="ingest_odds",
            status=JobStatus.started,
            source="odds_api_io",
            started_at=now - timedelta(minutes=1),
            finished_at=None,
            summary={},
        ),
    ]
    service = SimpleNamespace(
        repository=SimpleNamespace(
            list_models=lambda bucket, model_type: list(jobs) if bucket == "jobs" else [],
            count=lambda bucket: len(jobs) if bucket == "jobs" else 0,
            quota_snapshot=lambda provider: {},
            cache_count=lambda provider=None: 0,
        ),
        settings=SimpleNamespace(data_sources={}),
    )

    health = build_production_health(
        service,
        max_data_job_age_minutes=30,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )

    assert health["status"] == "healthy"
    assert health["issues"] == []
    assert health["job_health"]["ingest_odds"]["status"] == "running"


def test_production_health_flags_stale_running_job() -> None:
    from football_analysis.production import build_production_health

    now = datetime.now(UTC)
    jobs = [
        JobRun(
            id="cycle-fresh",
            job_type="production_cycle",
            status=JobStatus.succeeded,
            source="production",
            started_at=now - timedelta(minutes=4),
            finished_at=now - timedelta(minutes=3),
            summary={"status": "ready", "ready_to_bet": True},
        ),
        JobRun(
            id="fixtures-fresh",
            job_type="ingest_fixtures",
            status=JobStatus.succeeded,
            source="api_football",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=19),
            summary={"matches": 12},
        ),
        JobRun(
            id="odds-stale-running",
            job_type="ingest_odds",
            status=JobStatus.started,
            source="odds_api_io",
            started_at=now - timedelta(minutes=45),
            finished_at=None,
            summary={},
        ),
    ]
    service = SimpleNamespace(
        repository=SimpleNamespace(
            list_models=lambda bucket, model_type: list(jobs) if bucket == "jobs" else [],
            count=lambda bucket: len(jobs) if bucket == "jobs" else 0,
            quota_snapshot=lambda provider: {},
            cache_count=lambda provider=None: 0,
        ),
        settings=SimpleNamespace(data_sources={}),
    )

    health = build_production_health(
        service,
        max_data_job_age_minutes=30,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )

    assert health["status"] == "unhealthy"
    assert health["job_health"]["ingest_odds"]["status"] == "stale"
    assert "stale_job:ingest_odds" in health["issues"]


def test_production_health_flags_stale_heartbeat() -> None:
    from football_analysis.production import build_production_health

    now = datetime.now(UTC)
    jobs = [
        JobRun(
            id="cycle-stale",
            job_type="production_cycle",
            status=JobStatus.succeeded,
            source="production",
            started_at=now - timedelta(minutes=120),
            finished_at=now - timedelta(minutes=119),
            summary={"status": "ready", "ready_to_bet": True},
        ),
        JobRun(
            id="fixtures-fresh",
            job_type="ingest_fixtures",
            status=JobStatus.succeeded,
            source="api_football",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=19),
            summary={"matches": 12},
        ),
        JobRun(
            id="odds-fresh",
            job_type="ingest_odds",
            status=JobStatus.succeeded,
            source="odds_api_io",
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=9),
            summary={"odds_snapshots": 34},
        ),
    ]
    service = SimpleNamespace(
        repository=SimpleNamespace(
            list_models=lambda bucket, model_type: list(jobs) if bucket == "jobs" else [],
            count=lambda bucket: len(jobs) if bucket == "jobs" else 0,
            quota_snapshot=lambda provider: {},
            cache_count=lambda provider=None: 0,
        ),
        settings=SimpleNamespace(data_sources={}),
    )

    health = build_production_health(
        service,
        max_cycle_age_minutes=30,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )

    assert health["status"] == "unhealthy"
    assert health["job_health"]["production_cycle"]["status"] == "stale"
    assert "stale_job:production_cycle" in health["issues"]


def test_production_readiness_flags_active_live_league_without_history_or_profile() -> None:
    from football_analysis.production import build_production_readiness
    from football_analysis.settings import AppSettings, BacktestSettings, LeagueSettings, Settings

    match = Match(
        id="bra-1",
        league="Brazil - Brasileiro Serie A",
        home_team="Home",
        away_team="Away",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )
    odds = OddsSnapshot(
        id="odds-1",
        match_id=match.id,
        market_type=MarketType.one_x_two,
        source="odds_api_io",
        bookmaker="Bet365",
        outcome_odds={"HOME": 2.2, "DRAW": 3.1, "AWAY": 3.4},
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return [odds]
            raise AssertionError(bucket)

    with TemporaryDirectory() as data_dir:
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            backtest=BacktestSettings(data_dir=data_dir),
            leagues=[
                LeagueSettings(
                    code="BRA_SERIE_A",
                    name="Brasileiro Serie A",
                    season=2026,
                    aliases=["Brazil - Brasileiro Serie A"],
                    strategy_mode="live",
                    paper_only=False,
                )
            ],
            strategy_profiles=[],
            data_sources={},
        )
        result = build_production_readiness(
            SimpleNamespace(repository=FakeRepository(), settings=settings),
            include_past=False,
        )

    league = result["leagues"][0]
    assert result["status"] == "blocked"
    assert league["code"] == "BRA_SERIE_A"
    assert league["status"] == "blocked"
    assert league["scoped_matches"] == 1
    assert league["odds_snapshots"] == 1
    assert "missing_historical_data" in league["issues"]
    assert "missing_active_strategy_profile" in league["issues"]
    assert "need_historical_data:BRA_SERIE_A" in league["next_actions"]
    assert "need_strategy_profile:BRA_SERIE_A" in league["next_actions"]


def test_production_readiness_allows_profileless_tier_policy_when_history_exists() -> None:
    from football_analysis.contracts import HistoricalMatchRow
    from football_analysis.production import build_production_readiness
    from football_analysis.settings import AppSettings, BacktestSettings, LeagueSettings, Settings

    match = Match(
        id="aus-ready-1",
        league="澳塔超",
        home_team="Hobart Home",
        away_team="Hobart Away",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )
    odds = OddsSnapshot(
        id="aus-ready-odds-1",
        match_id=match.id,
        market_type=MarketType.asian_handicap,
        line="-0.5",
        source="qqsd",
        bookmaker="QQSD",
        outcome_odds={"HOME": 1.92, "AWAY": 1.88},
    )
    historical = HistoricalMatchRow(
        id="qqsd-local:AUS_NPL_TAS:2026:aus-ready-history:ah",
        league="AUS_NPL_TAS",
        season="2026",
        date=datetime.now(UTC) - timedelta(days=7),
        home_team="Old Home",
        away_team="Old Away",
        home_goals=2,
        away_goals=0,
        ah_line=-0.5,
        ah_home_odds=1.92,
        ah_away_odds=1.88,
        avg_ah_home_odds=1.9,
        avg_ah_away_odds=1.86,
        closing_ah_home_odds=1.94,
        closing_ah_away_odds=1.9,
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return [odds]
            if bucket == "historical_matches":
                return [historical]
            raise AssertionError(bucket)

    with TemporaryDirectory() as data_dir:
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            backtest=BacktestSettings(data_dir=data_dir),
            leagues=[
                LeagueSettings(
                    code="AUS_NPL_TAS",
                    name="Australia Tasmania NPL",
                    season=2026,
                    aliases=["澳塔超"],
                    tier="secondary_professional",
                    strategy_mode="live",
                    paper_only=False,
                )
            ],
            strategy_profiles=[],
            data_sources={},
        )
        result = build_production_readiness(
            SimpleNamespace(repository=FakeRepository(), settings=settings),
            include_past=False,
            league_codes={"AUS_NPL_TAS"},
        )

    league = result["leagues"][0]
    assert result["status"] == "ready"
    assert league["status"] == "production_ready"
    assert league["historical_data"]["available"] is True
    assert league["active_profile_count"] == 0
    assert league["live_enabled_profile_count"] == 0
    assert league["issues"] == []
    assert league["next_actions"] == []


def test_production_readiness_and_data_plan_scope_to_league_codes() -> None:
    from football_analysis.production import build_production_data_plan, build_production_readiness
    from football_analysis.settings import AppSettings, BacktestSettings, LeagueSettings, Settings

    aus_match = Match(
        id="aus-scope-1",
        league="澳首超",
        home_team="Canberra Home",
        away_team="Canberra Away",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )
    epl_match = Match(
        id="epl-scope-1",
        league="England - Premier League",
        home_team="London Home",
        away_team="London Away",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )
    aus_odds = OddsSnapshot(
        id="aus-scope-odds-1",
        match_id=aus_match.id,
        market_type=MarketType.one_x_two,
        source="qqsd",
        bookmaker="QQSD",
        outcome_odds={"HOME": 2.1, "DRAW": 3.2, "AWAY": 3.5},
    )
    epl_odds = OddsSnapshot(
        id="epl-scope-odds-1",
        match_id=epl_match.id,
        market_type=MarketType.one_x_two,
        source="qqsd",
        bookmaker="QQSD",
        outcome_odds={"HOME": 1.8, "DRAW": 3.4, "AWAY": 4.2},
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [aus_match, epl_match]
            if bucket == "odds":
                return [aus_odds, epl_odds]
            if bucket == "historical_matches":
                return []
            raise AssertionError(bucket)

    with TemporaryDirectory() as data_dir:
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            backtest=BacktestSettings(data_dir=data_dir),
            leagues=[
                LeagueSettings(
                    code="AUS_ACT_NPL",
                    name="Australia Capital Territory NPL",
                    season=2026,
                    aliases=["澳首超"],
                    strategy_mode="live",
                    paper_only=False,
                ),
                LeagueSettings(
                    code="EPL",
                    name="Premier League",
                    season=2026,
                    aliases=["England - Premier League"],
                    strategy_mode="live",
                    paper_only=False,
                ),
            ],
            strategy_profiles=[],
            data_sources={},
        )
        service = SimpleNamespace(repository=FakeRepository(), settings=settings)
        readiness = build_production_readiness(
            service,
            include_past=False,
            league_codes={"AUS_ACT_NPL"},
        )
        plan = build_production_data_plan(
            service,
            include_past=False,
            league_codes={"AUS_ACT_NPL"},
        )

    assert readiness["league_codes"] == ["AUS_ACT_NPL"]
    assert readiness["configured_leagues"] == 1
    assert readiness["scoped_matches"] == 1
    assert readiness["scoped_odds_snapshots"] == 1
    assert [row["code"] for row in readiness["leagues"]] == ["AUS_ACT_NPL"]
    assert plan["league_codes"] == ["AUS_ACT_NPL"]
    assert plan["tasks"], "expected scoped data tasks"
    assert {task["league"] for task in plan["tasks"]} == {"AUS_ACT_NPL"}


def test_production_data_plan_prefers_football_data_uk_extra_csv() -> None:
    from football_analysis.production import build_production_data_plan
    from football_analysis.settings import AppSettings, BacktestSettings, LeagueSettings, Settings, SourceSettings

    match = Match(
        id="bra-plan-1",
        league="Brazil - Brasileiro Serie A",
        home_team="Home",
        away_team="Away",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )
    odds = OddsSnapshot(
        id="odds-plan-1",
        match_id=match.id,
        market_type=MarketType.one_x_two,
        source="odds_api_io",
        bookmaker="Bet365",
        outcome_odds={"HOME": 2.2, "DRAW": 3.1, "AWAY": 3.4},
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return [odds]
            raise AssertionError(bucket)

    with TemporaryDirectory() as data_dir:
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            backtest=BacktestSettings(data_dir=data_dir, default_season="2526"),
            leagues=[
                LeagueSettings(
                    code="BRA_SERIE_A",
                    name="Brasileiro Serie A",
                    season=2026,
                    aliases=["Brazil - Brasileiro Serie A"],
                    odds_api_slug="brazil-brasileiro-serie-a",
                    football_data_uk_code="BRA",
                    strategy_mode="live",
                    paper_only=False,
                )
            ],
            strategy_profiles=[],
            data_sources={
                "football_data_uk": SourceSettings(
                    name="football-data.co.uk",
                    enabled=True,
                    base_url="https://www.football-data.co.uk",
                ),
                "odds_api_io": SourceSettings(
                    name="Odds-API.io",
                    enabled=True,
                    base_url="https://api.odds-api.io/v3",
                    api_key_env="ODDS_API_IO_KEY",
                ),
                "the_odds_api": SourceSettings(
                    name="The Odds API",
                    enabled=False,
                    base_url="https://api.the-odds-api.com/v4",
                    api_key_env="THE_ODDS_API_KEY",
                ),
                "sportmonks": SourceSettings(
                    name="Sportmonks Football API",
                    enabled=False,
                    base_url="https://api.sportmonks.com/v3/football",
                    api_key_env="SPORTMONKS_TOKEN",
                ),
            },
        )
        plan = build_production_data_plan(
            SimpleNamespace(repository=FakeRepository(), settings=settings),
            include_past=False,
        )

    historical_tasks = [task for task in plan["tasks"] if task["task_type"] == "historical_training_data"]
    assert historical_tasks, "expected a historical training data task"
    historical_task = historical_tasks[0]
    assert historical_task["status"] == "local_command_available"
    assert "footballctl ingest historical --league BRA --season 2026 --download --json" in historical_task["local_commands"]
    football_data_candidate = historical_task["provider_candidates"][0]
    assert football_data_candidate["source_id"] == "football_data_uk"
    assert football_data_candidate["official_url"] == "https://www.football-data.co.uk/new/BRA.csv"
    assert historical_task["user_actions"] == []
    assert "apply_or_confirm_provider:the_odds_api" not in plan["user_actions"]


def test_production_data_plan_imports_missing_standard_csv_seasons() -> None:
    from football_analysis.contracts import HistoricalMatchRow
    from football_analysis.production import build_production_data_plan
    from football_analysis.settings import (
        AppSettings,
        BacktestSettings,
        LeagueSettings,
        Settings,
        SourceSettings,
        StrategyProfileSettings,
    )

    match = Match(
        id="e0-partial-history-plan-1",
        league="English Premier League",
        home_team="Home",
        away_team="Away",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )
    odds = OddsSnapshot(
        id="e0-partial-history-odds-1",
        match_id=match.id,
        market_type=MarketType.one_x_two,
        source="odds_api_io",
        bookmaker="Bet365",
        outcome_odds={"HOME": 2.2, "DRAW": 3.1, "AWAY": 3.4},
    )
    row = HistoricalMatchRow(
        id="E0:2526:home:away",
        league="E0",
        season="2526",
        date=datetime(2026, 5, 1, tzinfo=UTC),
        home_team="Home",
        away_team="Away",
        home_goals=1,
        away_goals=0,
        home_odds=2.2,
        draw_odds=3.1,
        away_odds=3.4,
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return [odds]
            if bucket == "historical_matches":
                return [row]
            raise AssertionError(bucket)

    with TemporaryDirectory() as data_dir:
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            backtest=BacktestSettings(data_dir=data_dir, default_season="2526"),
            leagues=[
                LeagueSettings(
                    code="EPL",
                    name="English Premier League",
                    aliases=["English Premier League"],
                    odds_api_slug="england-premier-league",
                    football_data_uk_code="E0",
                    strategy_mode="live",
                    paper_only=False,
                )
            ],
            strategy_profiles=[
                StrategyProfileSettings(
                    id="e0-active-not-live",
                    name="E0 active not live",
                    league_code="E0",
                    market_type="1x2",
                    selections=["HOME"],
                    season_phases=["all"],
                    stability_label="robust",
                    live_enabled=False,
                    max_stake_units=0.2,
                )
            ],
            data_sources={
                "football_data_uk": SourceSettings(
                    name="football-data.co.uk",
                    enabled=True,
                    base_url="https://www.football-data.co.uk",
                ),
                "the_odds_api": SourceSettings(
                    name="The Odds API",
                    enabled=False,
                    base_url="https://api.the-odds-api.com/v4",
                    api_key_env="THE_ODDS_API_KEY",
                ),
                "sportmonks": SourceSettings(
                    name="Sportmonks Football API",
                    enabled=False,
                    base_url="https://api.sportmonks.com/v3/football",
                    api_key_env="SPORTMONKS_TOKEN",
                ),
            },
        )
        plan = build_production_data_plan(
            SimpleNamespace(repository=FakeRepository(), settings=settings),
            include_past=False,
        )

    historical_task = next(task for task in plan["tasks"] if task["task_type"] == "historical_training_data")
    assert historical_task["local_commands"] == [
        "footballctl ingest historical --league E0 --season 2122 --download --json",
        "footballctl ingest historical --league E0 --season 2223 --download --json",
        "footballctl ingest historical --league E0 --season 2324 --download --json",
        "footballctl ingest historical --league E0 --season 2425 --download --json",
    ]
    assert historical_task["user_actions"] == []
    profile_task = next(task for task in plan["tasks"] if task["task_type"] == "strategy_profile_validation")
    assert profile_task["status"] == "blocked_by_prerequisite"
    assert profile_task["prerequisite"] == "historical_training_data"
    assert all("production-profile-promote" not in command for command in profile_task["local_commands"])


def test_production_data_plan_adds_the_odds_api_live_command_when_enabled() -> None:
    from football_analysis.production import build_production_data_plan
    from football_analysis.settings import (
        AppSettings,
        BacktestSettings,
        LeagueSettings,
        Settings,
        SourceSettings,
        StrategyProfileSettings,
    )

    match = Match(
        id="epl-plan-1",
        league="England - Premier League",
        home_team="Arsenal",
        away_team="Chelsea",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return []
            if bucket == "historical_matches":
                return []
            raise AssertionError(bucket)

    old_key = os.environ.get("THE_ODDS_API_KEY")
    os.environ["THE_ODDS_API_KEY"] = "test-the-odds-api-key"
    try:
        with TemporaryDirectory() as data_dir:
            season_dir = Path(data_dir) / "2526"
            season_dir.mkdir()
            (season_dir / "E0.csv").write_text("Date,HomeTeam,AwayTeam\n", encoding="utf-8")
            settings = Settings(
                app=AppSettings(timezone="UTC"),
                backtest=BacktestSettings(data_dir=data_dir, default_season="2526"),
                leagues=[
                    LeagueSettings(
                        code="EPL",
                        name="Premier League",
                        aliases=["England - Premier League"],
                        odds_api_slug="england-premier-league",
                        api_football_league_id=39,
                        football_data_uk_code="E0",
                        strategy_mode="live",
                        paper_only=False,
                        max_events=20,
                    )
                ],
                strategy_profiles=[
                    StrategyProfileSettings(
                        id="e0-live",
                        name="E0 live",
                        league_code="E0",
                        market_type="1x2",
                        selections=["HOME"],
                        season_phases=["all"],
                        stability_label="robust",
                        live_enabled=True,
                        max_stake_units=0.2,
                    )
                ],
                data_sources={
                    "odds_api_io": SourceSettings(
                        name="Odds-API.io",
                        enabled=False,
                        base_url="https://api.odds-api.io/v3",
                        api_key_env="ODDS_API_IO_KEY",
                    ),
                    "api_football": SourceSettings(
                        name="API-FOOTBALL/API-SPORTS",
                        enabled=False,
                        base_url="https://v3.football.api-sports.io",
                        api_key_env="API_FOOTBALL_KEY",
                    ),
                    "the_odds_api": SourceSettings(
                        name="The Odds API",
                        enabled=True,
                        base_url="https://api.the-odds-api.com/v4",
                        api_key_env="THE_ODDS_API_KEY",
                        regions=["uk"],
                        markets=["h2h", "spreads", "totals"],
                        sport_keys={"EPL": "soccer_epl"},
                    ),
                },
            )
            plan = build_production_data_plan(
                SimpleNamespace(repository=FakeRepository(), settings=settings),
                include_past=False,
            )
    finally:
        if old_key is None:
            os.environ.pop("THE_ODDS_API_KEY", None)
        else:
            os.environ["THE_ODDS_API_KEY"] = old_key

    live_tasks = [task for task in plan["tasks"] if task["task_type"] == "live_odds"]
    assert live_tasks, "expected a live odds task"
    task = live_tasks[0]
    assert (
        "footballctl ingest odds --source the_odds_api --league EPL --max-events 20 --json"
        in task["local_commands"]
    )
    the_odds_candidate = next(item for item in task["provider_candidates"] if item["source_id"] == "the_odds_api")
    assert the_odds_candidate["enabled"] is True
    assert the_odds_candidate["credential_present"] is True
    assert the_odds_candidate["requires_user_application"] is False
    assert "apply_or_confirm_provider:the_odds_api" not in task["user_actions"]


def test_production_data_plan_adds_sportmonks_live_command_when_enabled() -> None:
    from football_analysis.production import build_production_data_plan
    from football_analysis.settings import (
        AppSettings,
        BacktestSettings,
        LeagueSettings,
        Settings,
        SourceSettings,
        StrategyProfileSettings,
    )

    match = Match(
        id="sportmonks-plan-1",
        league="England - Premier League",
        home_team="Arsenal",
        away_team="Chelsea",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return []
            if bucket == "historical_matches":
                return []
            raise AssertionError(bucket)

    old_key = os.environ.get("SPORTMONKS_TOKEN")
    os.environ["SPORTMONKS_TOKEN"] = "test-sportmonks-token"
    try:
        with TemporaryDirectory() as data_dir:
            season_dir = Path(data_dir) / "2526"
            season_dir.mkdir()
            (season_dir / "E0.csv").write_text("Date,HomeTeam,AwayTeam\n", encoding="utf-8")
            settings = Settings(
                app=AppSettings(timezone="UTC"),
                backtest=BacktestSettings(data_dir=data_dir, default_season="2526"),
                leagues=[
                    LeagueSettings(
                        code="EPL",
                        name="Premier League",
                        aliases=["England - Premier League"],
                        sportmonks_league_id=8,
                        football_data_uk_code="E0",
                        strategy_mode="live",
                        paper_only=False,
                        max_events=12,
                    )
                ],
                strategy_profiles=[
                    StrategyProfileSettings(
                        id="e0-live",
                        name="E0 live",
                        league_code="E0",
                        market_type="1x2",
                        selections=["HOME"],
                        season_phases=["all"],
                        stability_label="robust",
                        live_enabled=True,
                        max_stake_units=0.2,
                    )
                ],
                data_sources={
                    "sportmonks": SourceSettings(
                        name="Sportmonks Football API",
                        enabled=True,
                        base_url="https://api.sportmonks.com/v3/football",
                        api_key_env="SPORTMONKS_TOKEN",
                    ),
                },
            )
            plan = build_production_data_plan(
                SimpleNamespace(repository=FakeRepository(), settings=settings),
                include_past=False,
            )
    finally:
        if old_key is None:
            os.environ.pop("SPORTMONKS_TOKEN", None)
        else:
            os.environ["SPORTMONKS_TOKEN"] = old_key

    live_tasks = [task for task in plan["tasks"] if task["task_type"] == "live_odds"]
    assert live_tasks, "expected a live odds task"
    task = live_tasks[0]
    assert (
        "footballctl ingest odds --source sportmonks --league EPL --max-events 12 --json"
        in task["local_commands"]
    )
    sportmonks_candidate = next(item for item in task["provider_candidates"] if item["source_id"] == "sportmonks")
    assert sportmonks_candidate["enabled"] is True
    assert sportmonks_candidate["credential_present"] is True
    assert sportmonks_candidate["requires_user_application"] is False
    assert "apply_or_confirm_provider:sportmonks" not in task["user_actions"]


def test_production_data_plan_does_not_require_paid_provider_when_live_source_ready() -> None:
    from football_analysis.production import build_production_data_plan
    from football_analysis.settings import (
        AppSettings,
        BacktestSettings,
        LeagueSettings,
        Settings,
        SourceSettings,
        StrategyProfileSettings,
    )

    match = Match(
        id="serie-a-ready-source-plan-1",
        league="Italy - Serie A",
        home_team="Inter",
        away_team="Milan",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return []
            if bucket == "historical_matches":
                return []
            raise AssertionError(bucket)

    old_odds_key = os.environ.get("ODDS_API_IO_KEY")
    old_the_odds_key = os.environ.get("THE_ODDS_API_KEY")
    old_sportmonks_key = os.environ.get("SPORTMONKS_TOKEN")
    os.environ["ODDS_API_IO_KEY"] = "test-odds-api-io-key"
    os.environ.pop("THE_ODDS_API_KEY", None)
    os.environ.pop("SPORTMONKS_TOKEN", None)
    try:
        with TemporaryDirectory() as data_dir:
            season_dir = Path(data_dir) / "2526"
            season_dir.mkdir()
            (season_dir / "I1.csv").write_text("Date,HomeTeam,AwayTeam\n", encoding="utf-8")
            settings = Settings(
                app=AppSettings(timezone="UTC"),
                backtest=BacktestSettings(data_dir=data_dir, default_season="2526"),
                leagues=[
                    LeagueSettings(
                        code="SERIE_A",
                        name="Serie A",
                        aliases=["Italy - Serie A"],
                        odds_api_slug="italy-serie-a",
                        football_data_uk_code="I1",
                        strategy_mode="live",
                        paper_only=False,
                        max_events=20,
                    )
                ],
                strategy_profiles=[
                    StrategyProfileSettings(
                        id="i1-live",
                        name="I1 live",
                        league_code="I1",
                        market_type="1x2",
                        selections=["HOME"],
                        season_phases=["all"],
                        stability_label="robust",
                        live_enabled=True,
                        max_stake_units=0.2,
                    )
                ],
                data_sources={
                    "odds_api_io": SourceSettings(
                        name="Odds-API.io",
                        enabled=True,
                        base_url="https://api.odds-api.io/v3",
                        api_key_env="ODDS_API_IO_KEY",
                    ),
                    "the_odds_api": SourceSettings(
                        name="The Odds API",
                        enabled=False,
                        base_url="https://api.the-odds-api.com/v4",
                        api_key_env="THE_ODDS_API_KEY",
                        sport_keys={"SERIE_A": "soccer_italy_serie_a"},
                    ),
                    "sportmonks": SourceSettings(
                        name="Sportmonks Football API",
                        enabled=False,
                        base_url="https://api.sportmonks.com/v3/football",
                        api_key_env="SPORTMONKS_TOKEN",
                    ),
                },
            )
            plan = build_production_data_plan(
                SimpleNamespace(repository=FakeRepository(), settings=settings),
                include_past=False,
            )
    finally:
        if old_odds_key is None:
            os.environ.pop("ODDS_API_IO_KEY", None)
        else:
            os.environ["ODDS_API_IO_KEY"] = old_odds_key
        if old_the_odds_key is None:
            os.environ.pop("THE_ODDS_API_KEY", None)
        else:
            os.environ["THE_ODDS_API_KEY"] = old_the_odds_key
        if old_sportmonks_key is None:
            os.environ.pop("SPORTMONKS_TOKEN", None)
        else:
            os.environ["SPORTMONKS_TOKEN"] = old_sportmonks_key

    live_tasks = [task for task in plan["tasks"] if task["task_type"] == "live_odds"]
    assert live_tasks, "expected a live odds task"
    task = live_tasks[0]
    assert "footballctl ingest odds --source odds_api_io --league SERIE_A --max-events 20 --json" in task[
        "local_commands"
    ]
    odds_api_io_candidate = next(item for item in task["provider_candidates"] if item["source_id"] == "odds_api_io")
    assert odds_api_io_candidate["enabled"] is True
    assert odds_api_io_candidate["credential_present"] is True
    assert task["user_actions"] == []
    assert "set_env:THE_ODDS_API_KEY" not in plan["user_actions"]
    assert "set_env:SPORTMONKS_TOKEN" not in plan["user_actions"]


def test_production_data_plan_prefers_qqsd_live_odds_before_free_fallbacks() -> None:
    from football_analysis.production import build_production_data_plan
    from football_analysis.settings import (
        AppSettings,
        BacktestSettings,
        LeagueSettings,
        Settings,
        SourceSettings,
        StrategyProfileSettings,
    )

    match = Match(
        id="aus-qqsd-plan-1",
        league="澳首超",
        home_team="Canberra Home",
        away_team="Canberra Away",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return []
            if bucket == "historical_matches":
                return []
            raise AssertionError(bucket)

    with TemporaryDirectory() as data_dir:
        season_dir = Path(data_dir) / "2026"
        season_dir.mkdir()
        (season_dir / "AUS_ACT_NPL.csv").write_text("Date,HomeTeam,AwayTeam\n", encoding="utf-8")
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            backtest=BacktestSettings(data_dir=data_dir, default_season="2026"),
            leagues=[
                LeagueSettings(
                    code="AUS_ACT_NPL",
                    name="Australia Capital Territory NPL",
                    season=2026,
                    aliases=["澳首超"],
                    api_football_league_id=9999,
                    odds_api_slug="australia-act-npl",
                    strategy_mode="live",
                    paper_only=False,
                    max_events=12,
                )
            ],
            strategy_profiles=[
                StrategyProfileSettings(
                    id="aus-act-live",
                    name="AUS ACT live",
                    league_code="AUS_ACT_NPL",
                    market_type="1x2",
                    selections=["HOME"],
                    season_phases=["all"],
                    stability_label="robust",
                    live_enabled=True,
                    max_stake_units=0.2,
                )
            ],
            data_sources={
                "qqsd": SourceSettings(
                    name="球球是道 API",
                    enabled=True,
                    base_url="https://i.qqshidao.com",
                    api_key_env="QQSD_C_CK",
                ),
                "odds_api_io": SourceSettings(
                    name="Odds-API.io",
                    enabled=True,
                    base_url="https://api.odds-api.io/v3",
                    api_key_env="ODDS_API_IO_KEY",
                ),
                "api_football": SourceSettings(
                    name="API-Football",
                    enabled=True,
                    base_url="https://v3.football.api-sports.io",
                    api_key_env="API_FOOTBALL_KEY",
                ),
            },
        )
        plan = build_production_data_plan(
            SimpleNamespace(repository=FakeRepository(), settings=settings),
            include_past=False,
            league_codes={"AUS_ACT_NPL"},
        )

    live_tasks = [task for task in plan["tasks"] if task["task_type"] == "live_odds"]
    assert live_tasks, "expected a live odds task"
    task = live_tasks[0]
    assert task["provider_candidates"][0]["source_id"] == "qqsd"
    assert task["local_commands"][0] == (
        "footballctl ingest odds --source qqsd --league AUS_ACT_NPL --max-events 12 --json"
    )
    assert "footballctl ingest odds --source odds_api_io --league AUS_ACT_NPL --max-events 12 --json" in task[
        "local_commands"
    ]
    assert "footballctl ingest odds --source api_football --league AUS_ACT_NPL --json" in task[
        "local_commands"
    ]


def test_the_odds_api_sports_report_is_dry_run_by_default() -> None:
    from football_analysis.production import build_the_odds_api_sports_report
    from football_analysis.settings import AppSettings, LeagueSettings, Settings, SourceSettings

    settings = Settings(
        app=AppSettings(timezone="UTC"),
        leagues=[
            LeagueSettings(
                code="EPL",
                name="Premier League",
                odds_api_slug="england-premier-league",
                strategy_mode="live",
                paper_only=False,
            )
        ],
        data_sources={
            "the_odds_api": SourceSettings(
                name="The Odds API",
                enabled=False,
                base_url="https://api.the-odds-api.com/v4",
                api_key_env="THE_ODDS_API_KEY",
                sport_keys={"EPL": "soccer_epl"},
            )
        },
    )
    report = build_the_odds_api_sports_report(
        SimpleNamespace(repository=SimpleNamespace(), settings=settings),
        fetch_remote=False,
    )

    assert report["status"] == "action_required"
    assert report["fetch_remote"] is False
    assert report["remote_sport_count"] == 0
    assert "source_disabled:the_odds_api" in report["issues"]
    assert "missing_credentials:THE_ODDS_API_KEY" in report["issues"]
    assert report["league_sport_keys"][0]["configured_sport_key"] == "soccer_epl"
    assert report["league_sport_keys"][0]["available"] is None


def test_production_historical_odds_plan_generates_bounded_commands_and_cost() -> None:
    from football_analysis.production import build_production_historical_odds_plan
    from football_analysis.settings import AppSettings, LeagueSettings, Settings, SourceSettings

    old_key = os.environ.get("THE_ODDS_API_KEY")
    os.environ["THE_ODDS_API_KEY"] = "test-the-odds-api-key"
    try:
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            leagues=[
                LeagueSettings(
                    code="EPL",
                    name="Premier League",
                    odds_api_slug="england-premier-league",
                    strategy_mode="live",
                    paper_only=False,
                )
            ],
            data_sources={
                "the_odds_api": SourceSettings(
                    name="The Odds API",
                    enabled=True,
                    base_url="https://api.the-odds-api.com/v4",
                    api_key_env="THE_ODDS_API_KEY",
                    regions=["uk", "eu"],
                    markets=["h2h", "spreads"],
                    sport_keys={"EPL": "soccer_epl"},
                )
            },
        )
        report = build_production_historical_odds_plan(
            SimpleNamespace(repository=SimpleNamespace(), settings=settings),
            leagues=["EPL"],
            start_time="2026-01-01T12:00:00Z",
            end_time="2026-01-01T13:00:00Z",
            interval_minutes=10,
            max_snapshots=3,
            max_events=2,
        )
    finally:
        if old_key is None:
            os.environ.pop("THE_ODDS_API_KEY", None)
        else:
            os.environ["THE_ODDS_API_KEY"] = old_key

    assert report["status"] == "ready"
    assert report["snapshot_times"] == [
        "2026-01-01T12:00:00Z",
        "2026-01-01T12:10:00Z",
        "2026-01-01T12:20:00Z",
    ]
    assert report["truncated"] is True
    assert report["command_count"] == 3
    assert report["estimated_request_count"] == 3
    assert report["estimated_usage_credits"] == 120
    assert "--max-events 2" in report["commands"][0]


def test_arg_primera_has_the_odds_api_sport_key_mapping() -> None:
    from football_analysis.settings import load_settings

    settings = load_settings()
    assert (
        settings.data_sources["the_odds_api"].sport_keys["ARG_PRIMERA"]
        == "soccer_argentina_primera_division"
    )


def test_production_data_plan_can_batch_the_odds_api_historical_snapshots() -> None:
    from football_analysis.production import build_production_data_apply, build_production_data_plan
    from football_analysis.settings import AppSettings, BacktestSettings, LeagueSettings, Settings, SourceSettings

    match = Match(
        id="aleague-plan-1",
        league="Australia - A-League",
        home_team="Sydney FC",
        away_team="Melbourne Victory",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket in {"odds", "historical_matches"}:
                return []
            raise AssertionError(bucket)

    old_key = os.environ.get("THE_ODDS_API_KEY")
    os.environ["THE_ODDS_API_KEY"] = "test-the-odds-api-key"
    try:
        with TemporaryDirectory() as data_dir:
            settings = Settings(
                app=AppSettings(timezone="UTC"),
                backtest=BacktestSettings(data_dir=data_dir, default_season="2526"),
                leagues=[
                    LeagueSettings(
                        code="A_LEAGUE",
                        name="A-League",
                        aliases=["Australia - A-League"],
                        odds_api_slug="australia-a-league",
                        strategy_mode="live",
                        paper_only=False,
                        max_events=20,
                    )
                ],
                strategy_profiles=[],
                data_sources={
                    "the_odds_api": SourceSettings(
                        name="The Odds API",
                        enabled=True,
                        base_url="https://api.the-odds-api.com/v4",
                        api_key_env="THE_ODDS_API_KEY",
                        regions=["uk"],
                        markets=["h2h"],
                        sport_keys={"A_LEAGUE": "soccer_australia_aleague"},
                    )
                },
            )
            service = SimpleNamespace(repository=FakeRepository(), settings=settings)
            plan = build_production_data_plan(
                service,
                historical_odds_start_time="2026-01-01T12:00:00Z",
                historical_odds_end_time="2026-01-01T12:10:00Z",
                historical_odds_interval_minutes=10,
                historical_odds_max_snapshots=2,
                historical_odds_max_events=5,
            )
            dry_run = build_production_data_apply(
                service,
                include_backtests=False,
                historical_odds_start_time="2026-01-01T12:00:00Z",
                historical_odds_end_time="2026-01-01T12:10:00Z",
                historical_odds_interval_minutes=10,
                historical_odds_max_snapshots=2,
                historical_odds_max_events=5,
            )
            remote_run = build_production_data_apply(
                service,
                allow_remote=True,
                include_backtests=False,
                historical_odds_start_time="2026-01-01T12:00:00Z",
                historical_odds_end_time="2026-01-01T12:10:00Z",
                historical_odds_interval_minutes=10,
                historical_odds_max_snapshots=2,
                historical_odds_max_events=5,
            )
    finally:
        if old_key is None:
            os.environ.pop("THE_ODDS_API_KEY", None)
        else:
            os.environ["THE_ODDS_API_KEY"] = old_key

    historical_task = next(task for task in plan["tasks"] if task["task_type"] == "historical_training_data")
    assert historical_task["historical_odds_batch_plan"]["status"] == "ready"
    assert len(historical_task["local_commands"]) == 2
    assert historical_task["local_commands"][0].startswith("footballctl ingest historical-odds ")
    candidate = next(item for item in historical_task["provider_candidates"] if item["source_id"] == "the_odds_api")
    assert candidate["requires_user_application"] is False
    assert "apply_or_confirm_provider:the_odds_api" not in historical_task["user_actions"]

    historical_commands = [
        item for item in dry_run["commands"] if item["category"] == "remote_historical_odds_ingestion"
    ]
    assert len(historical_commands) == 2
    assert all(item["selected"] is False for item in historical_commands)
    assert all("remote_command_requires_allow_remote" in item["skip_reasons"] for item in historical_commands)

    remote_commands = [
        item for item in remote_run["commands"] if item["category"] == "remote_historical_odds_ingestion"
    ]
    assert len(remote_commands) == 2
    assert all(item["selected"] is True for item in remote_commands)


def test_production_data_apply_runs_safe_local_commands_and_skips_remote_by_default() -> None:
    from football_analysis.production import build_production_data_apply
    from football_analysis.settings import AppSettings, BacktestSettings, LeagueSettings, Settings, SourceSettings

    match = Match(
        id="bra-apply-1",
        league="Brazil - Brasileiro Serie A",
        home_team="Home",
        away_team="Away",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return []
            raise AssertionError(bucket)

    with TemporaryDirectory() as data_dir:
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            backtest=BacktestSettings(data_dir=data_dir, default_season="2526"),
            leagues=[
                LeagueSettings(
                    code="BRA_SERIE_A",
                    name="Brasileiro Serie A",
                    season=2026,
                    aliases=["Brazil - Brasileiro Serie A"],
                    odds_api_slug="brazil-brasileiro-serie-a",
                    api_football_league_id=71,
                    football_data_uk_code="BRA",
                    strategy_mode="live",
                    paper_only=False,
                )
            ],
            strategy_profiles=[],
            data_sources={
                "football_data_uk": SourceSettings(
                    name="football-data.co.uk",
                    enabled=True,
                    base_url="https://www.football-data.co.uk",
                ),
                "odds_api_io": SourceSettings(
                    name="Odds-API.io",
                    enabled=True,
                    base_url="https://api.odds-api.io/v3",
                    api_key_env="ODDS_API_IO_KEY",
                ),
                "api_football": SourceSettings(
                    name="API-FOOTBALL/API-SPORTS",
                    enabled=True,
                    base_url="https://v3.football.api-sports.io",
                    api_key_env="API_FOOTBALL_KEY",
                ),
                "the_odds_api": SourceSettings(
                    name="The Odds API",
                    enabled=False,
                    base_url="https://api.the-odds-api.com/v4",
                    api_key_env="THE_ODDS_API_KEY",
                ),
                "sportmonks": SourceSettings(
                    name="Sportmonks Football API",
                    enabled=False,
                    base_url="https://api.sportmonks.com/v3/football",
                    api_key_env="SPORTMONKS_TOKEN",
                ),
            },
        )
        service = SimpleNamespace(repository=FakeRepository(), settings=settings)
        dry_run = build_production_data_apply(
            service,
            execute=False,
            allow_remote=False,
            include_backtests=False,
        )
        calls: list[list[str]] = []

        def fake_runner(argv: list[str], timeout_seconds: int) -> object:
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        executed = build_production_data_apply(
            service,
            execute=True,
            allow_remote=False,
            include_backtests=False,
            command_runner=fake_runner,
        )

    assert dry_run["status"] == "dry_run"
    assert dry_run["command_summary"]["total_count"] == len(dry_run["commands"])
    assert dry_run["command_summary"]["selected_count"] == dry_run["selected_count"]
    assert dry_run["command_summary"]["skipped_count"] == dry_run["skipped_count"]
    assert dry_run["command_summary"]["by_category"]["remote_odds_ingestion"] == 2
    assert dry_run["command_summary"]["selected_by_category"]["public_historical_download"] == 1
    assert dry_run["command_summary"]["skipped_by_reason"]["remote_command_requires_allow_remote"] == 2
    assert "review_provider_quota_then_rerun_with_allow_remote" in dry_run["command_summary"]["next_actions"]
    historical = [item for item in dry_run["commands"] if item["category"] == "public_historical_download"]
    remote = [item for item in dry_run["commands"] if item["category"] == "remote_odds_ingestion"]
    assert historical and historical[0]["selected"] is True
    assert historical[0]["classification"] == "public_historical_download"
    assert historical[0]["skipped_reasons"] == []
    assert remote and all(item["selected"] is False for item in remote)
    assert all(item["classification"] == "remote_odds_ingestion" for item in remote)
    assert all("remote_command_requires_allow_remote" in item["skip_reasons"] for item in remote)
    assert all("remote_command_requires_allow_remote" in item["skipped_reasons"] for item in remote)
    assert executed["status"] == "succeeded"
    assert executed["selected_count"] == 1
    assert calls
    _assert_footballctl_argv(
        calls[0],
        ["ingest", "historical", "--league", "BRA", "--season", "2026", "--download", "--json"],
    )


def test_production_data_apply_skips_profile_promotion_without_risk_cap() -> None:
    from football_analysis.production import _data_apply_commands_from_plan

    plan = {
        "tasks": [
            {
                "league": "EPL",
                "strategy_code": "E0",
                "task_type": "strategy_profile_validation",
                "status": "manual_config_required",
                "local_commands": [
                    "footballctl backtest long-horizon-scan --league E0 --json",
                    "footballctl production-profile-promote --strategy-code E0 --json",
                ],
            }
        ]
    }

    commands = _data_apply_commands_from_plan(
        plan,
        allow_remote=False,
        include_backtests=True,
        include_blocked_prerequisites=True,
        max_commands=None,
    )

    assert len(commands) == 2
    assert commands[0]["selected"] is True
    assert commands[1]["selected"] is False
    assert "manual_risk_config_required" in commands[1]["skip_reasons"]


def test_production_data_apply_skips_profile_promotion_with_skip_backtests() -> None:
    from football_analysis.production import _data_apply_commands_from_plan

    plan = {
        "tasks": [
            {
                "league": "EPL",
                "strategy_code": "E0",
                "task_type": "strategy_profile_validation",
                "status": "manual_config_required",
                "local_commands": [
                    "footballctl production-profile-promote --strategy-code E0 --max-stake-units 0.2 --json",
                ],
            }
        ]
    }

    commands = _data_apply_commands_from_plan(
        plan,
        allow_remote=False,
        include_backtests=False,
        include_blocked_prerequisites=True,
        max_commands=None,
    )

    assert len(commands) == 1
    assert commands[0]["selected"] is False
    assert "profile_promotion_commands_disabled" in commands[0]["skip_reasons"]


def test_production_data_plan_suggests_conservative_profile_stake_cap() -> None:
    from football_analysis.production import _data_apply_commands_from_plan, _strategy_profile_task

    task = _strategy_profile_task(
        {"code": "EPL", "strategy_code": "E0"},
        SimpleNamespace(live_trading=SimpleNamespace(max_stake_units_per_pick=0.5)),
        prerequisite="profile_live_enablement",
    )

    assert task["suggested_max_stake_units"] == 0.2
    assert task["local_commands"][-1] == (
        "footballctl production-profile-promote --strategy-code E0 --max-stake-units 0.2 --json"
    )

    commands = _data_apply_commands_from_plan(
        {"tasks": [task]},
        allow_remote=False,
        include_backtests=True,
        include_blocked_prerequisites=True,
        max_commands=None,
    )

    promotion = commands[-1]
    assert promotion["category"] == "profile_promotion_plan"
    assert promotion["selected"] is True
    assert promotion["skip_reasons"] == []


def test_production_config_plan_blocks_missing_credentials_by_default() -> None:
    from football_analysis.production import build_production_config_plan
    from football_analysis.settings import AppSettings, ExecutionBrokerSettings, Settings, SourceSettings

    old_odds_key = os.environ.get("THE_ODDS_API_KEY")
    old_app_key = os.environ.get("BETFAIR_APP_KEY")
    old_session = os.environ.get("BETFAIR_SESSION_TOKEN")
    os.environ.pop("THE_ODDS_API_KEY", None)
    os.environ.pop("BETFAIR_APP_KEY", None)
    os.environ.pop("BETFAIR_SESSION_TOKEN", None)
    try:
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            data_sources={
                "the_odds_api": SourceSettings(
                    name="The Odds API",
                    enabled=False,
                    base_url="https://api.the-odds-api.com/v4",
                    api_key_env="THE_ODDS_API_KEY",
                )
            },
            execution_brokers={
                "betfair_exchange": ExecutionBrokerSettings(
                    name="Betfair Exchange API-NG",
                    enabled=False,
                    provider="betfair",
                    base_url="https://api.betfair.com/exchange/betting/json-rpc/v1",
                    credential_envs=["BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN"],
                    stake_currency="GBP",
                    stake_currency_per_unit=None,
                )
            },
        )
        report = build_production_config_plan(
            SimpleNamespace(settings=settings),
            source_ids=["the_odds_api"],
            broker_ids=["betfair_exchange"],
            stake_currency_per_unit=10.0,
        )
    finally:
        if old_odds_key is None:
            os.environ.pop("THE_ODDS_API_KEY", None)
        else:
            os.environ["THE_ODDS_API_KEY"] = old_odds_key
        if old_app_key is None:
            os.environ.pop("BETFAIR_APP_KEY", None)
        else:
            os.environ["BETFAIR_APP_KEY"] = old_app_key
        if old_session is None:
            os.environ.pop("BETFAIR_SESSION_TOKEN", None)
        else:
            os.environ["BETFAIR_SESSION_TOKEN"] = old_session

    assert report["status"] == "blocked"
    assert report["ready_count"] == 0
    assert report["blocked_count"] == 2
    assert "missing_credential:THE_ODDS_API_KEY" in report["issues"]
    assert "missing_broker_credential:BETFAIR_APP_KEY" in report["issues"]
    assert "missing_broker_credential:BETFAIR_SESSION_TOKEN" in report["issues"]


def test_production_config_plan_can_apply_ready_config_patch() -> None:
    from football_analysis.production import build_production_config_plan
    from football_analysis.settings import AppSettings, ExecutionBrokerSettings, Settings, SourceSettings

    old_odds_key = os.environ.get("THE_ODDS_API_KEY")
    old_app_key = os.environ.get("BETFAIR_APP_KEY")
    old_session = os.environ.get("BETFAIR_SESSION_TOKEN")
    os.environ["THE_ODDS_API_KEY"] = "test-the-odds-api-key"
    os.environ["BETFAIR_APP_KEY"] = "test-betfair-app-key"
    os.environ["BETFAIR_SESSION_TOKEN"] = "test-betfair-session"
    try:
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "data_sources": {
                            "the_odds_api": {
                                "name": "The Odds API",
                                "enabled": False,
                                "base_url": "https://api.the-odds-api.com/v4",
                                "api_key_env": "THE_ODDS_API_KEY",
                            }
                        },
                        "execution_brokers": {
                            "betfair_exchange": {
                                "name": "Betfair Exchange API-NG",
                                "enabled": False,
                                "provider": "betfair",
                                "base_url": "https://api.betfair.com/exchange/betting/json-rpc/v1",
                                "credential_envs": ["BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN"],
                                "stake_currency": "GBP",
                                "stake_currency_per_unit": None,
                            }
                        },
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            settings = Settings(
                app=AppSettings(timezone="UTC"),
                data_sources={
                    "the_odds_api": SourceSettings(
                        name="The Odds API",
                        enabled=False,
                        base_url="https://api.the-odds-api.com/v4",
                        api_key_env="THE_ODDS_API_KEY",
                    )
                },
                execution_brokers={
                    "betfair_exchange": ExecutionBrokerSettings(
                        name="Betfair Exchange API-NG",
                        enabled=False,
                        provider="betfair",
                        base_url="https://api.betfair.com/exchange/betting/json-rpc/v1",
                        credential_envs=["BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN"],
                        stake_currency="GBP",
                        stake_currency_per_unit=None,
                    )
                },
            )
            report = build_production_config_plan(
                SimpleNamespace(settings=settings),
                source_ids=["the_odds_api"],
                broker_ids=["betfair_exchange"],
                stake_currency_per_unit=10.0,
                config_path=config_path,
                apply_changes=True,
            )
            patched = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    finally:
        if old_odds_key is None:
            os.environ.pop("THE_ODDS_API_KEY", None)
        else:
            os.environ["THE_ODDS_API_KEY"] = old_odds_key
        if old_app_key is None:
            os.environ.pop("BETFAIR_APP_KEY", None)
        else:
            os.environ["BETFAIR_APP_KEY"] = old_app_key
        if old_session is None:
            os.environ.pop("BETFAIR_SESSION_TOKEN", None)
        else:
            os.environ["BETFAIR_SESSION_TOKEN"] = old_session

    assert report["status"] == "applied"
    assert report["ready_count"] == 2
    assert report["applied_count"] == 2
    assert patched["data_sources"]["the_odds_api"]["enabled"] is True
    assert patched["execution_brokers"]["betfair_exchange"]["enabled"] is True
    assert patched["execution_brokers"]["betfair_exchange"]["stake_currency_per_unit"] == 10.0


def test_production_config_plan_respects_explicit_source_and_broker_scope() -> None:
    from football_analysis.production import build_production_config_plan
    from football_analysis.settings import AppSettings, ExecutionBrokerSettings, Settings, SourceSettings

    old_odds_key = os.environ.get("THE_ODDS_API_KEY")
    old_app_key = os.environ.get("BETFAIR_APP_KEY")
    old_session = os.environ.get("BETFAIR_SESSION_TOKEN")
    os.environ["THE_ODDS_API_KEY"] = "test-the-odds-api-key"
    os.environ["BETFAIR_APP_KEY"] = "test-betfair-app-key"
    os.environ["BETFAIR_SESSION_TOKEN"] = "test-betfair-session"

    def make_raw_config() -> dict[str, object]:
        return {
            "data_sources": {
                "the_odds_api": {
                    "name": "The Odds API",
                    "enabled": False,
                    "base_url": "https://api.the-odds-api.com/v4",
                    "api_key_env": "THE_ODDS_API_KEY",
                }
            },
            "execution_brokers": {
                "betfair_exchange": {
                    "name": "Betfair Exchange API-NG",
                    "enabled": False,
                    "provider": "betfair",
                    "base_url": "https://api.betfair.com/exchange/betting/json-rpc/v1",
                    "credential_envs": ["BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN"],
                    "stake_currency": "GBP",
                    "stake_currency_per_unit": None,
                }
            },
        }

    try:
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            data_sources={
                "the_odds_api": SourceSettings(
                    name="The Odds API",
                    enabled=False,
                    base_url="https://api.the-odds-api.com/v4",
                    api_key_env="THE_ODDS_API_KEY",
                )
            },
            execution_brokers={
                "betfair_exchange": ExecutionBrokerSettings(
                    name="Betfair Exchange API-NG",
                    enabled=False,
                    provider="betfair",
                    base_url="https://api.betfair.com/exchange/betting/json-rpc/v1",
                    credential_envs=["BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN"],
                    stake_currency="GBP",
                    stake_currency_per_unit=None,
                )
            },
        )
        service = SimpleNamespace(settings=settings)

        default_report = build_production_config_plan(service, stake_currency_per_unit=10.0)
        source_report = build_production_config_plan(service, source_ids=["the_odds_api"])
        broker_report = build_production_config_plan(
            service,
            broker_ids=["betfair_exchange"],
            stake_currency_per_unit=10.0,
        )

        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                yaml.safe_dump(make_raw_config(), sort_keys=False),
                encoding="utf-8",
            )
            source_apply_report = build_production_config_plan(
                service,
                source_ids=["the_odds_api"],
                config_path=config_path,
                apply_changes=True,
            )
            after_source = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            broker_apply_report = build_production_config_plan(
                service,
                broker_ids=["betfair_exchange"],
                stake_currency_per_unit=10.0,
                config_path=config_path,
                apply_changes=True,
            )
            patched = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    finally:
        if old_odds_key is None:
            os.environ.pop("THE_ODDS_API_KEY", None)
        else:
            os.environ["THE_ODDS_API_KEY"] = old_odds_key
        if old_app_key is None:
            os.environ.pop("BETFAIR_APP_KEY", None)
        else:
            os.environ["BETFAIR_APP_KEY"] = old_app_key
        if old_session is None:
            os.environ.pop("BETFAIR_SESSION_TOKEN", None)
        else:
            os.environ["BETFAIR_SESSION_TOKEN"] = old_session

    assert default_report["source_ids"] == ["the_odds_api"]
    assert default_report["broker_ids"] == ["betfair_exchange"]
    assert source_report["source_ids"] == ["the_odds_api"]
    assert source_report["broker_ids"] == []
    assert [item["id"] for item in source_report["items"]] == ["the_odds_api"]
    assert broker_report["source_ids"] == []
    assert broker_report["broker_ids"] == ["betfair_exchange"]
    assert [item["id"] for item in broker_report["items"]] == ["betfair_exchange"]
    assert source_apply_report["status"] == "applied"
    assert source_apply_report["applied_count"] == 1
    assert after_source["data_sources"]["the_odds_api"]["enabled"] is True
    assert after_source["execution_brokers"]["betfair_exchange"]["enabled"] is False
    assert after_source["execution_brokers"]["betfair_exchange"]["stake_currency_per_unit"] is None
    assert broker_apply_report["status"] == "applied"
    assert broker_apply_report["applied_count"] == 1
    assert patched["data_sources"]["the_odds_api"]["enabled"] is True
    assert patched["execution_brokers"]["betfair_exchange"]["enabled"] is True
    assert patched["execution_brokers"]["betfair_exchange"]["stake_currency_per_unit"] == 10.0


def test_production_profile_promotion_requires_stake_and_can_apply_temp_config() -> None:
    from football_analysis.production import build_production_profile_promotion_plan
    from football_analysis.settings import AppSettings, Settings, StrategyProfileSettings

    profile = StrategyProfileSettings(
        id="e0_all_home_robust",
        name="E0 all-season home value",
        league_code="E0",
        market_type="1x2",
        selections=["HOME"],
        season_phases=["all"],
        stability_label="robust",
        roi=0.0502,
        settled_bets=176,
        positive_folds=3,
        fold_count=3,
        average_clv=0.0182,
    )
    service = SimpleNamespace(
        repository=SimpleNamespace(),
        settings=Settings(app=AppSettings(timezone="UTC"), strategy_profiles=[profile]),
    )

    def fake_audit_runner(svc: object, **kwargs: object) -> object:
        return SimpleNamespace(
            passed=True,
            items=[SimpleNamespace(profile_id="e0_all_home_robust", status="matched", message="ok")],
        )

    blocked = build_production_profile_promotion_plan(
        service,
        strategy_codes=["E0"],
        audit_runner=fake_audit_runner,
    )
    assert blocked["status"] == "blocked"
    assert "max_stake_units_required" in blocked["issues"]

    with TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "config.yaml"
        config_path.write_text(
            "strategy_profiles:\n"
            "  - id: e0_all_home_robust\n"
            "    name: E0 all-season home value\n"
            "    league_code: E0\n"
            "    market_type: 1x2\n"
            "    selections:\n"
            "      - HOME\n"
            "    season_phases:\n"
            "      - all\n"
            "    stability_label: robust\n"
            "    roi: 0.0502\n"
            "    settled_bets: 176\n"
            "    positive_folds: 3\n"
            "    fold_count: 3\n"
            "    average_clv: 0.0182\n",
            encoding="utf-8",
        )
        applied = build_production_profile_promotion_plan(
            service,
            strategy_codes=["E0"],
            max_stake_units=0.2,
            config_path=config_path,
            apply_changes=True,
            audit_runner=fake_audit_runner,
        )
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert applied["status"] == "applied"
    assert applied["applied_count"] == 1
    stored = raw["strategy_profiles"][0]
    assert stored["live_enabled"] is True
    assert stored["max_stake_units"] == 0.2


def _production_execution_queue_report(existing_stake_units: float) -> dict[str, object]:
    from football_analysis.production import build_production_execution_queue
    from football_analysis.settings import AppSettings, Settings

    match = Match(
        id="queue-match-1",
        league="Brazil - Brasileiro Serie A",
        home_team="Corinthians",
        away_team="Remo",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.92,
    )
    recommendation = Recommendation(
        id="queue-match-1-asian_handicap-AH_AWAY(+0.5)-v1",
        match_id=match.id,
        market_type=MarketType.asian_handicap,
        selection="AH_AWAY(+0.5)",
        status=RecommendationStatus.recommended,
        value_score=82.5,
        risk_score=24.0,
        confidence=0.71,
        stake_units=0.5,
        odds_basis={
            "best_price": 2.0,
            "source": "odds_api_io",
            "bookmaker": "Bet365",
            "strategy_profile": {"matched": True, "id": "bra_ah_away"},
            "strategy_confidence_class": "validated_strategy",
            "tier_policy": {"matched": True, "passed": True},
        },
        score_breakdown={
            "live_gate": {"passed": True, "gates_failed": []},
            "strategy_profile": {"matched": True, "id": "bra_ah_away"},
            "strategy_confidence_class": "validated_strategy",
            "tier_policy": {"matched": True, "passed": True},
        },
        reason="test live gate passed candidate",
        risk_notice="test risk notice",
    )
    bet = BetLog(
        id="existing-real-bet",
        match_id=match.id,
        market_type=MarketType.asian_handicap,
        selection="AWAY",
        odds=2.01,
        stake_units=existing_stake_units,
        platform="real",
        placed_at=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "recommendations":
                return [recommendation]
            if bucket == "bets":
                return [bet] if existing_stake_units else []
            return []

    def fake_audit(repository: object, settings: object, include_past: bool = False) -> object:
        return SimpleNamespace(
            status="ready",
            issues=[],
            recommended_count=1,
            total_live_stake_units=0.5,
            items=[
                SimpleNamespace(
                    match_id=match.id,
                    market_type="asian_handicap",
                    selection="AWAY",
                    status="recommended",
                    live_gate_passed=True,
                    value_score=82.5,
                    stake_units=0.5,
                )
            ],
        )

    return build_production_execution_queue(
        SimpleNamespace(
            repository=FakeRepository(),
            settings=Settings(app=AppSettings(timezone="UTC")),
        ),
        include_past=False,
        platform="real",
        audit_runner=fake_audit,
    )


def test_production_execution_queue_generates_safe_record_bet_command() -> None:
    report = _production_execution_queue_report(existing_stake_units=0.2)

    assert report["status"] == "ready"
    assert report["ready_to_execute"] is True
    assert report["queue_count"] == 1
    item = report["items"][0]
    assert item["status"] == "open"
    assert item["match_id"] == "queue-match-1"
    assert item["market_type"] == "asian_handicap"
    assert item["selection"] == "AH_AWAY(+0.5)"
    assert item["normalized_selection"] == "AH_AWAY"
    assert item["approved_odds"] == 2.0
    assert item["minimum_execution_odds"] == 1.98
    assert item["approved_stake_units"] == 0.5
    assert item["existing_real_stake_units"] == 0.2
    assert item["remaining_stake_units"] == 0.3
    assert item["idempotency_key"].startswith("production-execution:")
    assert item["record_bet_argv"] == [
        "footballctl",
        "record-bet",
        "queue-match-1",
        "asian_handicap",
        "AH_AWAY(+0.5)",
        "2.000",
        "0.300",
        "real",
        "--json",
    ]
    assert "footballctl 'record-bet' 'queue-match-1'" in item["record_bet_command"]
    assert item["gate_evidence"]["live_gate"]["passed"] is True
    assert item["gate_evidence"]["strategy_confidence_class"] == "validated_strategy"


def test_production_execution_queue_scopes_audit_to_league_codes() -> None:
    from football_analysis.production import build_production_execution_queue
    from football_analysis.settings import AppSettings, Settings

    audit_scopes: list[set[str] | None] = []

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            return []

    def fake_audit(
        repository: object,
        settings: object,
        include_past: bool = False,
        league_codes: set[str] | None = None,
    ) -> object:
        audit_scopes.append(league_codes)
        return SimpleNamespace(
            status="ready",
            issues=[],
            recommended_count=0,
            total_live_stake_units=0.0,
            items=[],
        )

    report = build_production_execution_queue(
        SimpleNamespace(
            repository=FakeRepository(),
            settings=Settings(app=AppSettings(timezone="UTC")),
        ),
        include_past=False,
        platform="real",
        audit_runner=fake_audit,
        league_codes={"AUS_ACT_NPL"},
    )

    assert audit_scopes == [{"AUS_ACT_NPL"}]
    assert report["league_codes"] == ["AUS_ACT_NPL"]
    assert report["ready_to_execute"] is False


def test_production_execution_queue_allows_tier_policy_profileless_candidates() -> None:
    from football_analysis.production import build_production_execution_queue
    from football_analysis.settings import AppSettings, Settings

    match = Match(
        id="profileless-match-1",
        league="Brazil - Brasileiro Serie A",
        home_team="Corinthians",
        away_team="Remo",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.92,
    )
    recommendation = Recommendation(
        id="profileless-match-1-over_under-OVER-v1",
        match_id=match.id,
        market_type=MarketType.over_under,
        selection="OVER",
        status=RecommendationStatus.recommended,
        value_score=91.0,
        risk_score=18.0,
        confidence=0.9,
        stake_units=0.5,
        odds_basis={"best_price": 3.1, "strategy_profile": {"matched": False}},
        score_breakdown={
            "live_gate": {"passed": True, "profileless_live_allowed": True},
            "strategy_profile": {"matched": False},
            "tier_policy": {"matched": True, "passed": True},
        },
        reason="profileless candidate allowed by tier policy",
        risk_notice="test risk notice",
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "recommendations":
                return [recommendation]
            if bucket == "bets":
                return []
            return []

    def fake_audit(repository: object, settings: object, include_past: bool = False) -> object:
        return SimpleNamespace(
            status="ready",
            issues=[],
            recommended_count=1,
            total_live_stake_units=0.5,
            items=[
                SimpleNamespace(
                    match_id=match.id,
                    market_type="over_under",
                    selection="OVER",
                    status="recommended",
                    live_gate_passed=True,
                    value_score=91.0,
                    stake_units=0.5,
                )
            ],
        )

    report = build_production_execution_queue(
        SimpleNamespace(
            repository=FakeRepository(),
            settings=Settings(app=AppSettings(timezone="UTC")),
        ),
        include_past=False,
        platform="real",
        audit_runner=fake_audit,
    )

    assert report["status"] == "ready"
    assert report["ready_to_execute"] is True
    assert report["candidate_count"] == 1
    assert report["profileless_candidate_count"] == 0
    assert report["profileless_candidates"] == []
    assert report["queue_count"] == 1
    assert report["items"][0]["recommendation_id"] == recommendation.id
    assert report["items"][0]["gate_evidence"]["tier_policy"]["passed"] is True
    assert report["issues"] == []


def test_production_execution_queue_omits_fully_filled_candidates() -> None:
    report = _production_execution_queue_report(existing_stake_units=0.5)

    assert report["status"] == "filled_or_no_open_stake"
    assert report["ready_to_execute"] is False
    assert report["queue_count"] == 0
    assert report["items"] == []
    assert "no_unfilled_live_gate_passed_candidates" in report["issues"]


def _production_execution_service(existing_stake_units: float = 0.0) -> tuple[object, object]:
    from football_analysis.service import AnalysisService
    from football_analysis.settings import AppSettings, Settings

    match = Match(
        id="execute-match-1",
        league="Brazil - Brasileiro Serie A",
        home_team="Mirassol",
        away_team="Gremio",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.92,
    )
    recommendation = Recommendation(
        id="execute-match-1-asian_handicap-HOME-v1",
        match_id=match.id,
        market_type=MarketType.asian_handicap,
        selection="HOME",
        status=RecommendationStatus.recommended,
        value_score=84.0,
        risk_score=23.0,
        confidence=0.73,
        stake_units=0.5,
        odds_basis={
            "best_price": 9.0,
            "source": "odds_api_io",
            "bookmaker": "Bet365",
            "strategy_profile": {"matched": True, "id": "bra_all_home_live_long_horizon"},
            "strategy_confidence_class": "validated_strategy",
        },
        score_breakdown={
            "live_gate": {"passed": True, "gates_failed": []},
            "strategy_profile": {"matched": True, "id": "bra_all_home_live_long_horizon"},
            "strategy_confidence_class": "validated_strategy",
        },
        reason="test production execution candidate",
        risk_notice="test risk notice",
    )
    existing = BetLog(
        id="existing-execute-bet",
        match_id=match.id,
        market_type=MarketType.asian_handicap,
        selection="AH_HOME(-0.5)",
        odds=9.0,
        stake_units=existing_stake_units,
        platform="real",
        placed_at=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )

    class MutableRepository:
        def __init__(self) -> None:
            self.buckets: dict[str, list[object]] = {
                "matches": [match],
                "recommendations": [recommendation],
                "bets": [existing] if existing_stake_units else [],
            }

        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            return list(self.buckets.get(bucket, []))

        def get_model(self, bucket: str, model_id: str, model_type: type[object]) -> object | None:
            for model in self.buckets.get(bucket, []):
                if getattr(model, "id", None) == model_id:
                    return model
            return None

        def upsert_model(self, bucket: str, model_id: str, model: object) -> None:
            values = self.buckets.setdefault(bucket, [])
            for index, existing_model in enumerate(values):
                if getattr(existing_model, "id", None) == model_id:
                    values[index] = model
                    return
            values.append(model)

    repository = MutableRepository()
    service = AnalysisService(Settings(app=AppSettings(timezone="UTC")), repository)

    def fake_audit(repo: object, settings: object, include_past: bool = False) -> object:
        return SimpleNamespace(
            status="ready",
            issues=[],
            recommended_count=1,
            total_live_stake_units=0.5,
            items=[
                SimpleNamespace(
                    match_id=match.id,
                    market_type="asian_handicap",
                    selection="AH_HOME(-0.5)",
                    status="recommended",
                    live_gate_passed=True,
                    value_score=84.0,
                    stake_units=0.5,
                )
            ],
        )

    return service, fake_audit


def test_production_execute_dry_run_does_not_write_bets() -> None:
    from football_analysis.production import run_production_execution

    service, fake_audit = _production_execution_service()

    report = run_production_execution(service, audit_runner=fake_audit)

    assert report["status"] == "dry_run"
    assert report["mode"] == "dry_run"
    assert report["dry_run_count"] == 1
    assert report["recorded_count"] == 0
    assert report["records"][0]["status"] == "dry_run"
    assert service.repository.list_models("bets", BetLog) == []


def test_production_execute_records_remaining_stake_idempotently() -> None:
    from football_analysis.production import run_production_execution

    service, fake_audit = _production_execution_service(existing_stake_units=0.2)

    missing_fill = run_production_execution(
        service,
        execute_records=True,
        require_fills=True,
        audit_runner=fake_audit,
    )

    assert missing_fill["status"] == "error"
    assert missing_fill["error_count"] == 1
    assert missing_fill["records"][0]["error"] == "execution_fill_required"
    assert len(service.repository.list_models("bets", BetLog)) == 1

    dry_run = run_production_execution(service, audit_runner=fake_audit)
    idempotency_key = dry_run["records"][0]["idempotency_key"]
    report = run_production_execution(
        service,
        execute_records=True,
        fills={
            idempotency_key: {
                "odds": 8.95,
                "stake_units": 0.3,
                "platform": "real",
                "external_bet_id": "book:123",
            }
        },
        require_fills=True,
        audit_runner=fake_audit,
    )

    assert report["status"] == "executed"
    assert report["mode"] == "record_only"
    assert report["recorded_count"] == 1
    recorded = report["records"][0]["bet"]
    assert recorded["id"].startswith("production-execution:")
    assert recorded["match_id"] == "execute-match-1"
    assert recorded["selection"] == "HOME"
    assert recorded["odds"] == 8.95
    assert recorded["stake_units"] == 0.3
    assert "recommendation_id=execute-match-1-asian_handicap-HOME-v1" in recorded["notes"]
    assert "external_bet_id=book:123" in recorded["notes"]
    bets_after_first_run = service.repository.list_models("bets", BetLog)
    assert len(bets_after_first_run) == 2

    second = run_production_execution(service, execute_records=True, audit_runner=fake_audit)

    assert second["status"] == "blocked"
    assert second["queue_status"] == "filled_or_no_open_stake"
    assert second["recorded_count"] == 0
    assert len(service.repository.list_models("bets", BetLog)) == 2


def _production_broker_plan_service(
    mapped: bool,
    enabled: bool = True,
    existing_bets: list[BetLog] | None = None,
) -> tuple[object, object]:
    from football_analysis.settings import AppSettings, ExecutionBrokerSettings, Settings

    external_ids = {}
    if mapped:
        external_ids = {
            "betfair_market_id": "1.23456789",
            "betfair_selection_id_AH_HOME": "12345",
        }
    match = Match(
        id="broker-match-1",
        league="Brazil - Brasileiro Serie A",
        home_team="Corinthians",
        away_team="Remo",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.94,
        external_ids=external_ids,
    )
    recommendation = Recommendation(
        id="broker-match-1-asian_handicap-HOME-v1",
        match_id=match.id,
        market_type=MarketType.asian_handicap,
        selection="HOME",
        status=RecommendationStatus.recommended,
        value_score=84.0,
        risk_score=23.0,
        confidence=0.73,
        stake_units=0.5,
        odds_basis={
            "best_price": 5.5,
            "source": "odds_api_io",
            "bookmaker": "Bet365",
            "strategy_profile": {"matched": True, "id": "bra_all_home_live_long_horizon"},
            "strategy_confidence_class": "validated_strategy",
        },
        score_breakdown={
            "live_gate": {"passed": True, "gates_failed": []},
            "strategy_profile": {"matched": True, "id": "bra_all_home_live_long_horizon"},
            "strategy_confidence_class": "validated_strategy",
        },
        reason="test broker candidate",
        risk_notice="test risk notice",
    )

    now = datetime.now(UTC)
    jobs = [
        JobRun(
            id="broker-production-cycle",
            job_type="production_cycle",
            status=JobStatus.succeeded,
            source="production",
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4),
            summary={"status": "ready", "ready_to_bet": True},
        ),
        JobRun(
            id="broker-fixtures",
            job_type="ingest_fixtures",
            status=JobStatus.succeeded,
            source="api_football",
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4),
            summary={"matches": 1},
        ),
        JobRun(
            id="broker-odds",
            job_type="ingest_odds",
            status=JobStatus.succeeded,
            source="odds_api_io",
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4),
            summary={"odds_snapshots": 1},
        ),
    ]

    class FakeRepository:
        def __init__(self) -> None:
            self.matches = {match.id: match}
            self.jobs = list(jobs)
            self.bets = list(existing_bets or [])

        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return list(self.matches.values())
            if bucket == "recommendations":
                return [recommendation]
            if bucket == "bets":
                return list(self.bets)
            if bucket == "jobs":
                return list(self.jobs)
            return []

        def get_model(self, bucket: str, record_id: str, model_type: type[object]) -> object | None:
            if bucket == "matches":
                return self.matches.get(record_id)
            return None

        def upsert_model(self, bucket: str, record_id: str, model: object) -> None:
            if bucket == "matches":
                self.matches[record_id] = model

    settings = Settings(
        app=AppSettings(timezone="UTC"),
        execution_brokers={
            "betfair_exchange": ExecutionBrokerSettings(
                name="Betfair Exchange API-NG",
                enabled=enabled,
                provider="betfair",
                base_url="https://api.betfair.com/exchange/betting/json-rpc/v1",
                credential_envs=["BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN"],
                required_match_external_ids=["betfair_market_id"],
                required_selection_external_ids=["betfair_selection_id"],
                stake_currency="GBP",
                stake_currency_per_unit=10.0 if mapped else None,
                official_url="https://docs.developer.betfair.com/",
            )
        },
    )

    def fake_audit(repository: object, settings: object, include_past: bool = False) -> object:
        return SimpleNamespace(
            status="ready",
            issues=[],
            recommended_count=1,
            total_live_stake_units=0.5,
            items=[
                SimpleNamespace(
                    match_id=match.id,
                    market_type="asian_handicap",
                    selection="AH_HOME(-0.5)",
                    status="recommended",
                    live_gate_passed=True,
                    value_score=84.0,
                    stake_units=0.5,
                )
            ],
        )

    return SimpleNamespace(repository=FakeRepository(), settings=settings), fake_audit


def test_production_broker_plan_blocks_missing_credentials_and_mapping() -> None:
    from football_analysis.production import build_production_broker_plan

    service, fake_audit = _production_broker_plan_service(mapped=False, enabled=False)

    plan = build_production_broker_plan(service, audit_runner=fake_audit)

    assert plan["status"] == "blocked"
    assert plan["ready_for_broker_execution"] is False
    assert "broker_disabled:betfair_exchange" in plan["issues"]
    assert "missing_broker_credential:BETFAIR_APP_KEY" in plan["issues"]
    assert "stake_currency_per_unit_required" in plan["issues"]
    assert "broker_mapping_missing:1" in plan["issues"]
    assert plan["items"][0]["missing_fields"] == [
        "betfair_market_id",
        "betfair_selection_id:AH_HOME",
        "stake_currency_per_unit",
    ]


def test_production_broker_plan_builds_betfair_order_payload() -> None:
    from football_analysis.production import build_production_broker_plan

    old_app_key = os.environ.get("BETFAIR_APP_KEY")
    old_session = os.environ.get("BETFAIR_SESSION_TOKEN")
    os.environ["BETFAIR_APP_KEY"] = "app-key-test"
    os.environ["BETFAIR_SESSION_TOKEN"] = "session-test"
    try:
        service, fake_audit = _production_broker_plan_service(mapped=True, enabled=True)
        plan = build_production_broker_plan(service, audit_runner=fake_audit)
    finally:
        if old_app_key is None:
            os.environ.pop("BETFAIR_APP_KEY", None)
        else:
            os.environ["BETFAIR_APP_KEY"] = old_app_key
        if old_session is None:
            os.environ.pop("BETFAIR_SESSION_TOKEN", None)
        else:
            os.environ["BETFAIR_SESSION_TOKEN"] = old_session

    assert plan["status"] == "ready"
    assert plan["ready_for_broker_execution"] is True
    assert plan["broker_ready_count"] == 1
    item = plan["items"][0]
    assert item["missing_fields"] == []
    assert item["stake_currency_amount"] == 5.0
    assert item["order_payload"] == {
        "broker_id": "betfair",
        "market_id": "1.23456789",
        "selection_id": "12345",
        "side": "BACK",
        "order_type": "LIMIT",
        "limit_price": 5.445,
        "size": 5.0,
        "currency": "GBP",
        "customer_order_ref": item["idempotency_key"],
    }


def test_production_onboarding_reports_broker_prerequisites() -> None:
    from football_analysis.production import build_production_onboarding

    old_app_key = os.environ.get("BETFAIR_APP_KEY")
    old_session = os.environ.get("BETFAIR_SESSION_TOKEN")
    os.environ.pop("BETFAIR_APP_KEY", None)
    os.environ.pop("BETFAIR_SESSION_TOKEN", None)
    try:
        service, fake_audit = _production_broker_plan_service(mapped=False, enabled=False)
        report = build_production_onboarding(service, audit_runner=fake_audit)
    finally:
        if old_app_key is None:
            os.environ.pop("BETFAIR_APP_KEY", None)
        else:
            os.environ["BETFAIR_APP_KEY"] = old_app_key
        if old_session is None:
            os.environ.pop("BETFAIR_SESSION_TOKEN", None)
        else:
            os.environ["BETFAIR_SESSION_TOKEN"] = old_session

    action_codes = {action["code"] for action in report["actions"]}
    actions_by_code = {action["code"]: action for action in report["actions"]}
    assert report["status"] == "action_required"
    for action in report["actions"]:
        assert action["id"] == action["code"]
        assert action["status"] == "action_required"
        assert action["title"]
    assert actions_by_code["enable_broker:betfair_exchange"]["title"].startswith("Enable execution broker")
    assert actions_by_code["set_env:BETFAIR_APP_KEY"]["title"].startswith("Set environment credential")
    assert "enable_broker:betfair_exchange" in action_codes
    assert "set_env:BETFAIR_APP_KEY" in action_codes
    assert "set_env:BETFAIR_SESSION_TOKEN" in action_codes
    assert "set_broker_stake_currency_per_unit:betfair_exchange" in action_codes
    assert "apply_broker_mappings:betfair_exchange" in action_codes
    assert report["broker"]["missing_mapping_count"] == 1
    assert report["broker"]["missing_credentials"] == [
        "BETFAIR_APP_KEY",
        "BETFAIR_SESSION_TOKEN",
    ]


def test_production_deploy_check_blocks_broker_live_until_prerequisites_are_ready() -> None:
    from football_analysis.production import build_production_deploy_check

    old_app_key = os.environ.get("BETFAIR_APP_KEY")
    old_session = os.environ.get("BETFAIR_SESSION_TOKEN")
    os.environ.pop("BETFAIR_APP_KEY", None)
    os.environ.pop("BETFAIR_SESSION_TOKEN", None)
    try:
        service, fake_audit = _production_broker_plan_service(mapped=False, enabled=False)
        record_only = build_production_deploy_check(
            service,
            target="record-only",
            audit_runner=fake_audit,
            decision_runner=lambda svc, include_past=False: SimpleNamespace(
                status="ready",
                ready_to_bet=True,
                action="place_approved_live_bets",
                issues=[],
                components={},
            ),
        )
        broker_live = build_production_deploy_check(
            service,
            target="broker-live",
            audit_runner=fake_audit,
            decision_runner=lambda svc, include_past=False: SimpleNamespace(
                status="ready",
                ready_to_bet=True,
                action="place_approved_live_bets",
                issues=[],
                components={},
            ),
        )
    finally:
        if old_app_key is None:
            os.environ.pop("BETFAIR_APP_KEY", None)
        else:
            os.environ["BETFAIR_APP_KEY"] = old_app_key
        if old_session is None:
            os.environ.pop("BETFAIR_SESSION_TOKEN", None)
        else:
            os.environ["BETFAIR_SESSION_TOKEN"] = old_session

    assert record_only["status"] == "ready_with_warnings"
    assert record_only["ready_for_record_execution"] is True
    assert record_only["issues"] == []
    assert broker_live["status"] == "blocked"
    assert "broker_execution_not_ready" in broker_live["issues"]
    assert "broker_onboarding_action_required:set_env:BETFAIR_APP_KEY" in broker_live["issues"]
    assert "broker_onboarding_action_required:apply_broker_mappings:betfair_exchange" in broker_live["issues"]


def test_production_deploy_check_record_only_allows_empty_execution_queue_by_default() -> None:
    from football_analysis.production import build_production_deploy_check

    service, _ = _production_broker_plan_service(mapped=False, enabled=True)

    def empty_audit(repository: object, settings: object, include_past: bool = False) -> object:
        return SimpleNamespace(
            status="blocked",
            issues=["no_live_gate_passed_candidates"],
            recommended_count=0,
            total_live_stake_units=0.0,
            items=[],
        )

    def idle_decision(svc: object, include_past: bool = False) -> object:
        return SimpleNamespace(
            status="blocked",
            ready_to_bet=False,
            action="refresh_fixtures_and_odds",
            issues=["preflight:live:no_live_gate_passed_candidates"],
            components={"preflight": "blocked"},
        )

    relaxed = build_production_deploy_check(
        service,
        target="record-only",
        audit_runner=empty_audit,
        decision_runner=idle_decision,
    )
    strict = build_production_deploy_check(
        service,
        target="record-only",
        require_execution_queue=True,
        audit_runner=empty_audit,
        decision_runner=idle_decision,
    )

    assert relaxed["status"] == "ready_with_warnings"
    assert relaxed["ready_for_worker"] is True
    assert relaxed["ready_for_record_execution"] is False
    assert relaxed["issues"] == []
    assert "record_execution_not_ready" in relaxed["warnings"]
    assert strict["status"] == "blocked"
    assert "record_execution_not_ready" in strict["issues"]


def test_production_deploy_check_reports_record_only_verified_after_queue_is_consumed() -> None:
    from football_analysis.production import build_production_deploy_check

    existing_bet = BetLog(
        id="production-execution:already-recorded",
        match_id="broker-match-1",
        market_type=MarketType.asian_handicap,
        selection="HOME",
        odds=5.5,
        stake_units=0.5,
        platform="real",
        notes="production_execution idempotency_key=production-execution:already-recorded",
    )
    service, _ = _production_broker_plan_service(
        mapped=False,
        enabled=True,
        existing_bets=[existing_bet],
    )

    def consumed_audit(repository: object, settings: object, include_past: bool = False) -> object:
        return SimpleNamespace(
            status="ready",
            issues=[],
            recommended_count=1,
            total_live_stake_units=0.5,
            items=[
                SimpleNamespace(
                    match_id="broker-match-1",
                    market_type="asian_handicap",
                    selection="AH_HOME(-0.5)",
                    status="recommended",
                    live_gate_passed=True,
                    value_score=84.0,
                    stake_units=0.5,
                )
            ],
        )

    report = build_production_deploy_check(
        service,
        target="record-only",
        audit_runner=consumed_audit,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )
    strict = build_production_deploy_check(
        service,
        target="record-only",
        require_execution_queue=True,
        audit_runner=consumed_audit,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )

    assert report["status"] == "ready_with_warnings"
    assert report["ready_for_record_execution"] is True
    assert "record_execution_not_ready" not in report["warnings"]
    assert report["preflight"]["execution_queue"]["status"] == "filled_or_no_open_stake"
    assert report["preflight"]["record_execution_history"]["recorded_count"] == 1
    assert strict["status"] == "blocked"
    assert "record_execution_not_ready" in strict["issues"]


def test_production_deploy_check_defaults_to_worker_target() -> None:
    from football_analysis.production import build_production_deploy_check

    service, fake_audit = _production_broker_plan_service(mapped=False, enabled=True)
    report = build_production_deploy_check(
        service,
        audit_runner=fake_audit,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )

    assert report["target"] == "worker"
    assert report["ready_for_worker"] is True
    assert "record_execution_not_ready" not in report["issues"]


def test_production_deploy_check_worker_allows_missing_health_history_on_fresh_db() -> None:
    from football_analysis.production import build_production_deploy_check

    service, fake_audit = _production_broker_plan_service(mapped=False, enabled=True)
    service.repository.jobs = []

    ready_decision = lambda svc, include_past=False: SimpleNamespace(
        status="ready",
        ready_to_bet=True,
        action="place_approved_live_bets",
        issues=[],
        components={},
    )
    worker = build_production_deploy_check(
        service,
        target="worker",
        audit_runner=fake_audit,
        decision_runner=ready_decision,
    )
    record_only = build_production_deploy_check(
        service,
        target="record-only",
        audit_runner=fake_audit,
        decision_runner=ready_decision,
    )

    assert worker["status"] == "ready_with_warnings"
    assert worker["ready_for_worker"] is True
    assert worker["issues"] == []
    assert "worker_not_ready" not in worker["issues"]
    assert "preflight:production_health_startup_history_missing" in worker["warnings"]
    assert record_only["status"] == "ready_with_warnings"
    assert record_only["ready_for_worker"] is False
    assert "worker_not_ready" not in record_only["issues"]
    assert "preflight:production_health_unhealthy" in record_only["warnings"]


def test_production_deploy_check_worker_blocks_stale_health_history() -> None:
    from football_analysis.production import build_production_deploy_check

    service, fake_audit = _production_broker_plan_service(mapped=False, enabled=True)
    now = datetime.now(UTC)
    service.repository.jobs[0] = JobRun(
        id="broker-production-cycle-stale",
        job_type="production_cycle",
        status=JobStatus.succeeded,
        source="production",
        started_at=now - timedelta(minutes=130),
        finished_at=now - timedelta(minutes=129),
        summary={"status": "ready", "ready_to_bet": True},
    )

    report = build_production_deploy_check(
        service,
        target="worker",
        audit_runner=fake_audit,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )

    assert report["status"] == "blocked"
    assert report["ready_for_worker"] is False
    assert "worker_not_ready" in report["issues"]
    assert "production_health:stale_job:production_cycle" in report["preflight"]["issues"]


def test_production_deploy_check_record_only_downgrades_stale_worker_health() -> None:
    from football_analysis.production import build_production_deploy_check

    service, fake_audit = _production_broker_plan_service(mapped=False, enabled=True)
    now = datetime.now(UTC)
    service.repository.jobs[0] = JobRun(
        id="broker-production-cycle-stale",
        job_type="production_cycle",
        status=JobStatus.succeeded,
        source="production",
        started_at=now - timedelta(minutes=130),
        finished_at=now - timedelta(minutes=129),
        summary={"status": "ready", "ready_to_bet": True},
    )

    report = build_production_deploy_check(
        service,
        target="record-only",
        audit_runner=fake_audit,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )

    assert report["status"] == "ready_with_warnings"
    assert report["ready_for_worker"] is False
    assert "worker_not_ready" not in report["issues"]
    assert "preflight:production_health_unhealthy" in report["warnings"]
    assert "preflight:production_health:stale_job:production_cycle" in report["warnings"]


def test_production_runtime_security_warns_on_local_defaults() -> None:
    from football_analysis.production import build_production_runtime_security

    env_names = ["FOOTBALL_ADMIN_TOKEN", "API_BIND_HOST", "POSTGRES_BIND_HOST", "POSTGRES_PASSWORD"]
    old_env = {name: os.environ.get(name) for name in env_names}
    os.environ["FOOTBALL_ADMIN_TOKEN"] = ""
    os.environ["API_BIND_HOST"] = "127.0.0.1"
    os.environ["POSTGRES_BIND_HOST"] = "127.0.0.1"
    os.environ["POSTGRES_PASSWORD"] = "football"
    try:
        report = build_production_runtime_security(target="worker")
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    assert report["status"] == "ready_with_warnings"
    assert report["issues"] == []
    assert "runtime_admin_token_missing" in report["warnings"]
    assert "runtime_postgres_default_password" in report["warnings"]
    assert report["api_public_bind"] is False
    assert report["postgres_public_bind"] is False


def test_production_runtime_security_blocks_broker_live_insecure_runtime() -> None:
    from football_analysis.production import build_production_runtime_security

    env_names = ["FOOTBALL_ADMIN_TOKEN", "API_BIND_HOST", "POSTGRES_BIND_HOST", "POSTGRES_PASSWORD"]
    old_env = {name: os.environ.get(name) for name in env_names}
    os.environ["FOOTBALL_ADMIN_TOKEN"] = ""
    os.environ["API_BIND_HOST"] = "0.0.0.0"
    os.environ["POSTGRES_BIND_HOST"] = "0.0.0.0"
    os.environ["POSTGRES_PASSWORD"] = "football"
    try:
        report = build_production_runtime_security(target="broker-live")
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    assert report["status"] == "blocked"
    assert "runtime_admin_token_missing" in report["issues"]
    assert "runtime_api_public_bind" in report["issues"]
    assert "runtime_postgres_default_password" in report["issues"]
    assert "runtime_postgres_public_bind" in report["issues"]


def test_production_runtime_secret_bootstrap_redacts_values_by_default() -> None:
    from football_analysis.production import build_production_runtime_secret_bootstrap

    env_names = ["FOOTBALL_ADMIN_TOKEN", "API_BIND_HOST", "POSTGRES_BIND_HOST", "POSTGRES_PASSWORD"]
    old_env = {name: os.environ.get(name) for name in env_names}
    os.environ["FOOTBALL_ADMIN_TOKEN"] = ""
    os.environ["API_BIND_HOST"] = "127.0.0.1"
    os.environ["POSTGRES_BIND_HOST"] = "127.0.0.1"
    os.environ["POSTGRES_PASSWORD"] = "football"
    try:
        redacted = build_production_runtime_secret_bootstrap(
            target="broker-live",
            value_factory=lambda env_name, byte_count: f"{env_name.lower()}-{byte_count}-secret",
        )
        visible = build_production_runtime_secret_bootstrap(
            target="broker-live",
            show_secret_values=True,
            value_factory=lambda env_name, byte_count: f"{env_name.lower()}-{byte_count}-secret",
        )
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    assert redacted["status"] == "manual_required"
    assert redacted["secret_values_visible"] is False
    assert redacted["required_envs"] == ["FOOTBALL_ADMIN_TOKEN", "POSTGRES_PASSWORD"]
    assert "secret_values_redacted" in redacted["warnings"]
    assert all(item["secret_value"] is None for item in redacted["items"])
    assert all("-secret" not in item["env_line"] for item in redacted["items"])
    assert "ALTER USER" in redacted["apply_steps"][2]["command"]
    assert "<generated POSTGRES_PASSWORD>" in redacted["apply_steps"][2]["command"]

    admin_item = next(item for item in visible["items"] if item["env"] == "FOOTBALL_ADMIN_TOKEN")
    postgres_item = next(item for item in visible["items"] if item["env"] == "POSTGRES_PASSWORD")
    assert admin_item["secret_value"] == "football_admin_token-32-secret"
    assert postgres_item["secret_value"] == "postgres_password-24-secret"
    assert "POSTGRES_PASSWORD=postgres_password-24-secret" in [
        item["env_line"] for item in visible["items"]
    ]
    assert "postgres_password-24-secret" in visible["apply_steps"][2]["command"]


def test_production_onboarding_checklist_includes_runtime_security_inputs() -> None:
    from football_analysis.production import build_production_onboarding_checklist

    service, fake_audit = _production_broker_plan_service(mapped=False, enabled=True)
    env_names = ["FOOTBALL_ADMIN_TOKEN", "API_BIND_HOST", "POSTGRES_BIND_HOST", "POSTGRES_PASSWORD"]
    old_env = {name: os.environ.get(name) for name in env_names}
    os.environ["FOOTBALL_ADMIN_TOKEN"] = ""
    os.environ["API_BIND_HOST"] = "127.0.0.1"
    os.environ["POSTGRES_BIND_HOST"] = "127.0.0.1"
    os.environ["POSTGRES_PASSWORD"] = "football"
    try:
        report = build_production_onboarding_checklist(
            service,
            target="broker-live",
            audit_runner=fake_audit,
        )
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    runtime_codes = {
        item["code"]
        for section in report["sections"]
        if section["key"] == "runtime_security"
        for item in section["items"]
    }
    assert "runtime_admin_token_missing" in runtime_codes
    assert "runtime_postgres_default_password" in runtime_codes
    runtime_items = [
        item
        for section in report["sections"]
        if section["key"] == "runtime_security"
        for item in section["items"]
    ]
    assert all(
        item["command"] == "footballctl production-runtime-secrets --target broker-live --json"
        for item in runtime_items
        if item["code"] in {"runtime_admin_token_missing", "runtime_postgres_default_password"}
    )
    assert "FOOTBALL_ADMIN_TOKEN" in report["required_envs"]
    assert "POSTGRES_PASSWORD" in report["required_envs"]
    assert report["runtime_security"]["status"] == "blocked"


def test_production_deployment_doctor_blocks_broker_live_insecure_runtime() -> None:
    from football_analysis.production import build_production_deployment_doctor

    service, fake_audit = _production_broker_plan_service(mapped=False, enabled=True)
    env_names = ["FOOTBALL_ADMIN_TOKEN", "API_BIND_HOST", "POSTGRES_BIND_HOST", "POSTGRES_PASSWORD"]
    old_env = {name: os.environ.get(name) for name in env_names}
    os.environ["FOOTBALL_ADMIN_TOKEN"] = ""
    os.environ["API_BIND_HOST"] = "127.0.0.1"
    os.environ["POSTGRES_BIND_HOST"] = "127.0.0.1"
    os.environ["POSTGRES_PASSWORD"] = "football"
    try:
        with TemporaryDirectory() as tmp:
            source_config = Path(tmp) / "source.yaml"
            candidate_config = Path(tmp) / "candidate.yaml"
            source_config.write_text(
                yaml.safe_dump(
                    service.settings.model_dump(mode="json"),
                    sort_keys=False,
                    allow_unicode=False,
                ),
                encoding="utf-8",
            )
            report = build_production_deployment_doctor(
                service,
                target="broker-live",
                source_config_path=source_config,
                candidate_config_path=candidate_config,
                max_apply_passes=1,
                audit_runner=fake_audit,
                decision_runner=lambda svc, include_past=False: SimpleNamespace(
                    status="ready",
                    ready_to_bet=True,
                    action="place_approved_live_bets",
                    issues=[],
                    components={},
                ),
            )
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    assert report["status"] == "blocked"
    assert report["ready_for_target"] is False
    assert "runtime_security_blocked" in report["issues"]
    assert "runtime_security:runtime_admin_token_missing" in report["issues"]
    assert "runtime_security:runtime_postgres_default_password" in report["issues"]
    assert report["runtime_security"]["status"] == "blocked"
    bootstrap = next(action for action in report["next_actions"] if action["id"] == "bootstrap_runtime_secrets")
    assert bootstrap["status"] == "manual_required"
    assert bootstrap["requires_operator_approval"] is True
    assert bootstrap["secret_values_visible_when_run"] is True
    assert "FOOTBALL_ADMIN_TOKEN" in bootstrap["required_envs"]
    assert "POSTGRES_PASSWORD" in bootstrap["required_envs"]
    assert "--show-secret-values" in bootstrap["command"]


def test_production_deployment_doctor_worker_allows_missing_health_history_on_fresh_db() -> None:
    from football_analysis.production import build_production_deployment_doctor

    service, fake_audit = _production_broker_plan_service(mapped=False, enabled=True)
    service.repository.jobs = []
    with TemporaryDirectory() as tmp:
        source_config = Path(tmp) / "source.yaml"
        candidate_config = Path(tmp) / "candidate.yaml"
        source_config.write_text(
            yaml.safe_dump(
                service.settings.model_dump(mode="json"),
                sort_keys=False,
                allow_unicode=False,
            ),
            encoding="utf-8",
        )
        report = build_production_deployment_doctor(
            service,
            target="worker",
            source_config_path=source_config,
            candidate_config_path=candidate_config,
            max_apply_passes=1,
            audit_runner=fake_audit,
            decision_runner=lambda svc, include_past=False: SimpleNamespace(
                status="ready",
                ready_to_bet=True,
                action="place_approved_live_bets",
                issues=[],
                components={},
            ),
        )

    assert report["status"] == "ready_with_warnings"
    assert report["ready_for_target"] is True
    assert "production_health_unhealthy" not in report["issues"]
    assert "deploy_check_blocked:worker" not in report["issues"]
    assert "candidate_check_blocked:worker" not in report["issues"]
    assert "production_health_startup_history_missing" in report["warnings"]
    assert report["summary"]["health_status"] == "unhealthy"


def test_production_preflight_warns_about_broker_by_default() -> None:
    from football_analysis.production import build_production_preflight

    service, fake_audit = _production_broker_plan_service(mapped=False, enabled=True)
    report = build_production_preflight(
        service,
        require_broker=False,
        require_execution_queue=True,
        audit_runner=fake_audit,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )

    assert report["status"] == "degraded"
    assert report["ready_for_worker"] is True
    assert report["ready_for_record_execution"] is True
    assert report["ready_for_broker_execution"] is False
    assert report["issues"] == []
    assert "broker_not_ready:blocked" in report["warnings"]


def test_production_preflight_reports_profile_promotion_gate() -> None:
    from football_analysis.production import build_production_preflight
    from football_analysis.settings import (
        AppSettings,
        BacktestSettings,
        LeagueSettings,
        Settings,
        StrategyProfileSettings,
    )

    now = datetime.now(UTC)
    match = Match(
        id="profile-gate-match-1",
        league="Premier League",
        home_team="Home",
        away_team="Away",
        kickoff_at=now + timedelta(days=1),
        data_completeness=0.95,
    )
    odds = OddsSnapshot(
        id="profile-gate-odds-1",
        match_id=match.id,
        market_type=MarketType.one_x_two,
        source="odds_api_io",
        bookmaker="Bet365",
        outcome_odds={"HOME": 2.1, "DRAW": 3.2, "AWAY": 3.6},
    )
    jobs = [
        JobRun(
            id="profile-gate-cycle",
            job_type="production_cycle",
            status=JobStatus.succeeded,
            source="production",
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4),
            summary={"status": "ready", "ready_to_bet": True},
        ),
        JobRun(
            id="profile-gate-fixtures",
            job_type="ingest_fixtures",
            status=JobStatus.succeeded,
            source="api_football",
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4),
            summary={"matches": 1},
        ),
        JobRun(
            id="profile-gate-odds",
            job_type="ingest_odds",
            status=JobStatus.succeeded,
            source="odds_api_io",
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4),
            summary={"odds_snapshots": 1},
        ),
    ]

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return [odds]
            if bucket == "jobs":
                return list(jobs)
            return []

    profile = StrategyProfileSettings(
        id="e0_all_home_robust",
        name="E0 all-season home value",
        league_code="E0",
        market_type="1x2",
        selections=["HOME"],
        season_phases=["all"],
        stability_label="robust",
        roi=0.0502,
        settled_bets=176,
        positive_folds=3,
        fold_count=3,
        average_clv=0.0182,
    )

    with TemporaryDirectory() as data_dir:
        for season in ["2122", "2223", "2324", "2425", "2526"]:
            season_dir = Path(data_dir) / season
            season_dir.mkdir()
            (season_dir / "E0.csv").write_text(
                "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n",
                encoding="utf-8",
            )
        service = SimpleNamespace(
            repository=FakeRepository(),
            settings=Settings(
                app=AppSettings(timezone="UTC"),
                backtest=BacktestSettings(data_dir=data_dir, default_season="2526"),
                leagues=[
                    LeagueSettings(
                        code="EPL",
                        name="Premier League",
                        aliases=["Premier League"],
                        football_data_uk_code="E0",
                        strategy_mode="live",
                        paper_only=False,
                    )
                ],
                strategy_profiles=[profile],
            ),
        )

        def fake_profile_audit_runner(svc: object, **kwargs: object) -> object:
            return SimpleNamespace(
                passed=True,
                items=[
                    SimpleNamespace(
                        profile_id="e0_all_home_robust",
                        status="matched",
                        message="ok",
                    )
                ],
            )

        report = build_production_preflight(
            service,
            require_broker=False,
            require_execution_queue=False,
            audit_runner=lambda repository, settings, include_past=False: SimpleNamespace(
                status="blocked",
                issues=[],
                recommended_count=0,
                total_live_stake_units=0,
                items=[],
            ),
            profile_audit_runner=fake_profile_audit_runner,
            decision_runner=lambda svc, include_past=False: SimpleNamespace(
                status="ready",
                ready_to_bet=True,
                action="place_approved_live_bets",
                issues=[],
                components={},
            ),
        )
        audited_report = build_production_preflight(
            service,
            require_broker=False,
            require_execution_queue=False,
            profile_promotion_audit=True,
            audit_runner=lambda repository, settings, include_past=False: SimpleNamespace(
                status="blocked",
                issues=[],
                recommended_count=0,
                total_live_stake_units=0,
                items=[],
            ),
            profile_audit_runner=fake_profile_audit_runner,
            decision_runner=lambda svc, include_past=False: SimpleNamespace(
                status="ready",
                ready_to_bet=True,
                action="place_approved_live_bets",
                issues=[],
                components={},
            ),
        )

    assert report["profile_promotion"]["status"] == "ready"
    assert report["profile_promotion"]["require_audit"] is False
    assert report["profile_promotion"]["selected_strategy_codes"] == ["E0"]
    assert report["profile_promotion"]["max_stake_units"] == 0.2
    assert report["profile_promotion"]["issues"] == []
    assert "profile_promotion_ready:1" in report["warnings"]
    assert "profile_promotion_audit_not_checked" in report["warnings"]
    assert audited_report["profile_promotion"]["require_audit"] is True
    assert audited_report["profile_promotion"]["audit_passed"] is True
    assert audited_report["profile_promotion"]["max_stake_units"] == 0.2
    assert "profile_promotion_audit_not_checked" not in audited_report["warnings"]


def test_production_onboarding_apply_plan_marks_profile_promotion_ready() -> None:
    from football_analysis.production import build_production_onboarding_apply_plan
    from football_analysis.settings import AppSettings, BacktestSettings, LeagueSettings, Settings, StrategyProfileSettings

    match = Match(
        id="epl-apply-plan-1",
        league="Premier League",
        home_team="Home",
        away_team="Away",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )
    odds = OddsSnapshot(
        id="epl-apply-plan-odds-1",
        match_id=match.id,
        market_type=MarketType.one_x_two,
        source="odds_api_io",
        bookmaker="Bet365",
        outcome_odds={"HOME": 2.1, "DRAW": 3.2, "AWAY": 3.6},
    )
    profile = StrategyProfileSettings(
        id="e0_all_home_robust",
        name="E0 all-season home value",
        league_code="E0",
        market_type="1x2",
        selections=["HOME"],
        season_phases=["all"],
        stability_label="robust",
        roi=0.0502,
        settled_bets=176,
        positive_folds=3,
        fold_count=3,
        average_clv=0.0182,
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return [odds]
            if bucket == "historical_matches":
                return []
            return []

    with TemporaryDirectory() as data_dir:
        for season in ["2122", "2223", "2324", "2425", "2526"]:
            season_dir = Path(data_dir) / season
            season_dir.mkdir()
            (season_dir / "E0.csv").write_text(
                "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n",
                encoding="utf-8",
            )
        service = SimpleNamespace(
            repository=FakeRepository(),
            settings=Settings(
                app=AppSettings(timezone="UTC"),
                backtest=BacktestSettings(data_dir=data_dir, default_season="2526"),
                leagues=[
                    LeagueSettings(
                        code="EPL",
                        name="Premier League",
                        aliases=["Premier League"],
                        football_data_uk_code="E0",
                        strategy_mode="live",
                        paper_only=False,
                    )
                ],
                strategy_profiles=[profile],
            ),
        )
        plan = build_production_onboarding_apply_plan(
            service,
            audit_runner=lambda repository, settings, include_past=False: SimpleNamespace(
                status="blocked",
                issues=[],
                recommended_count=0,
                total_live_stake_units=0,
                items=[],
            ),
        )
        candidate_config = Path(data_dir) / "candidate.yaml"
        candidate_plan = build_production_onboarding_apply_plan(
            service,
            config_path=candidate_config,
            audit_runner=lambda repository, settings, include_past=False: SimpleNamespace(
                status="blocked",
                issues=[],
                recommended_count=0,
                total_live_stake_units=0,
                items=[],
            ),
        )
        calls: list[list[str]] = []

        def fake_runner(argv: list[str], timeout_seconds: int) -> object:
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout="applied", stderr="")

        executed = build_production_onboarding_apply_plan(
            service,
            execute_ready=True,
            timeout_seconds=60,
            command_runner=fake_runner,
            audit_runner=lambda repository, settings, include_past=False: SimpleNamespace(
                status="blocked",
                issues=[],
                recommended_count=0,
                total_live_stake_units=0,
                items=[],
            ),
        )
        blocked_calls: list[list[str]] = []

        def fake_blocked_runner(argv: list[str], timeout_seconds: int) -> object:
            blocked_calls.append(argv)
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"status": "blocked", "issues": ["profile_audit_not_matched"]}),
                stderr="",
            )

        blocked_executed = build_production_onboarding_apply_plan(
            service,
            execute_ready=True,
            timeout_seconds=60,
            command_runner=fake_blocked_runner,
            audit_runner=lambda repository, settings, include_past=False: SimpleNamespace(
                status="blocked",
                issues=[],
                recommended_count=0,
                total_live_stake_units=0,
                items=[],
            ),
        )

    assert plan["status"] == "partial_ready"
    assert plan["execute_ready"] is False
    assert plan["executions"] == []
    assert plan["ready_count"] == 1
    assert plan["ready_commands"] == [
        "footballctl production-profile-promote --strategy-code E0 --max-stake-units 0.2 --apply --json"
    ]
    profile_item = next(item for item in plan["items"] if item["code"] == "apply_profile_promotion")
    assert profile_item["status"] == "ready"
    assert profile_item["writes_config"] is True
    assert profile_item["requires_operator_approval"] is True
    assert profile_item["dry_run_command"] == (
        "footballctl production-profile-promote --strategy-code E0 --max-stake-units 0.2 --json"
    )
    candidate_command = (
        "footballctl production-profile-promote --strategy-code E0 --max-stake-units 0.2 "
        f"--apply --config-path {candidate_config} --json"
    )
    assert candidate_plan["ready_commands"] == [candidate_command]
    candidate_item = next(item for item in candidate_plan["items"] if item["code"] == "apply_profile_promotion")
    assert candidate_item["dry_run_command"] == (
        "footballctl production-profile-promote --strategy-code E0 --max-stake-units 0.2 "
        f"--config-path {candidate_config} --json"
    )
    assert executed["status"] == "partial_applied"
    assert executed["execute_ready"] is True
    assert executed["succeeded_count"] == 1
    assert executed["failed_count"] == 0
    assert executed["executions"][0]["status"] == "succeeded"
    assert calls
    _assert_footballctl_argv(
        calls[0],
        [
            "production-profile-promote",
            "--strategy-code",
            "E0",
            "--max-stake-units",
            "0.2",
            "--apply",
            "--json",
        ],
    )
    assert blocked_calls
    assert blocked_executed["failed_count"] == 1
    assert blocked_executed["succeeded_count"] == 0
    assert blocked_executed["executions"][0]["status"] == "failed"
    assert blocked_executed["executions"][0]["payload_status"] == "blocked"
    assert blocked_executed["executions"][0]["error"] == "command_payload_status:blocked"


def test_production_candidate_check_applies_ready_items_to_candidate_only() -> None:
    from football_analysis.production import build_production_candidate_check
    from football_analysis.settings import AppSettings, BacktestSettings, LeagueSettings, Settings, StrategyProfileSettings

    match = Match(
        id="epl-candidate-1",
        league="Premier League",
        home_team="Home",
        away_team="Away",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )
    odds = OddsSnapshot(
        id="epl-candidate-odds-1",
        match_id=match.id,
        market_type=MarketType.one_x_two,
        source="odds_api_io",
        bookmaker="Bet365",
        outcome_odds={"HOME": 2.1, "DRAW": 3.2, "AWAY": 3.6},
    )
    profile = StrategyProfileSettings(
        id="e0_all_home_robust",
        name="E0 all-season home value",
        league_code="E0",
        market_type="1x2",
        selections=["HOME"],
        season_phases=["all"],
        stability_label="robust",
        roi=0.0502,
        settled_bets=176,
        positive_folds=3,
        fold_count=3,
        average_clv=0.0182,
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return [odds]
            return []

    with TemporaryDirectory() as data_dir:
        for season in ["2122", "2223", "2324", "2425", "2526"]:
            season_dir = Path(data_dir) / season
            season_dir.mkdir()
            (season_dir / "E0.csv").write_text(
                "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n",
                encoding="utf-8",
            )
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            backtest=BacktestSettings(data_dir=data_dir, default_season="2526"),
            leagues=[
                LeagueSettings(
                    code="EPL",
                    name="Premier League",
                    aliases=["Premier League"],
                    football_data_uk_code="E0",
                    strategy_mode="live",
                    paper_only=False,
                )
            ],
            strategy_profiles=[profile],
        )
        service = SimpleNamespace(repository=FakeRepository(), settings=settings)
        source_config = Path(data_dir) / "source.yaml"
        candidate_config = Path(data_dir) / "candidate.yaml"
        source_config.write_text(
            yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=False, allow_unicode=False),
            encoding="utf-8",
        )
        source_before = source_config.read_text(encoding="utf-8")
        calls: list[list[str]] = []

        def fake_runner(argv: list[str], timeout_seconds: int) -> object:
            calls.append(argv)
            config_path = Path(argv[argv.index("--config-path") + 1])
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            for item in raw["strategy_profiles"]:
                if item["id"] == "e0_all_home_robust":
                    item["live_enabled"] = True
                    item["max_stake_units"] = 0.2
            config_path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=False), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="applied", stderr="")

        report = build_production_candidate_check(
            service,
            source_config_path=source_config,
            candidate_config_path=candidate_config,
            target="record-only",
            timeout_seconds=60,
            command_runner=fake_runner,
            audit_runner=lambda repository, settings, include_past=False: SimpleNamespace(
                status="blocked",
                issues=[],
                recommended_count=0,
                total_live_stake_units=0,
                items=[],
            ),
            decision_runner=lambda svc, include_past=False: SimpleNamespace(
                status="ready",
                ready_to_bet=True,
                action="place_approved_live_bets",
                issues=[],
                components={},
            ),
        )

        assert source_config.read_text(encoding="utf-8") == source_before
        assert report["source_config_changed"] is False
        assert report["copy_status"] == "copied"
        assert report["execute_ready"] is True
        assert report["apply_plan"]["succeeded_count"] == 1
        assert calls
        _assert_footballctl_argv(
            calls[0],
            [
                "production-profile-promote",
                "--strategy-code",
                "E0",
                "--max-stake-units",
                "0.2",
                "--apply",
                "--config-path",
                str(candidate_config),
                "--json",
            ],
        )
        assert report["deploy_check"]["target"] == "record-only"
        assert report["deploy_check"]["preflight"]["profile_promotion"]["config_path"] == str(candidate_config)
        assert report["deploy_check"]["onboarding"]["profile_promotion"]["actions"] == []
        assert report["config_diff"]["strategy_profile_changes"] == [
            {
                "id": "e0_all_home_robust",
                "fields": {
                    "live_enabled": {"before": False, "after": True},
                    "max_stake_units": {"before": None, "after": 0.2},
                },
            }
        ]


def test_production_onboarding_checklist_exports_required_inputs_and_markdown() -> None:
    from football_analysis.production import (
        build_production_onboarding_checklist,
        format_production_onboarding_checklist_markdown,
    )
    from football_analysis.settings import (
        AppSettings,
        BacktestSettings,
        ExecutionBrokerSettings,
        LeagueSettings,
        Settings,
        SourceSettings,
        StrategyProfileSettings,
    )

    match = Match(
        id="epl-checklist-1",
        league="Premier League",
        home_team="Home",
        away_team="Away",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )
    profile = StrategyProfileSettings(
        id="e0_all_home_robust",
        name="E0 all-season home value",
        league_code="E0",
        market_type="1x2",
        selections=["HOME"],
        season_phases=["all"],
        stability_label="robust",
        roi=0.0502,
        settled_bets=176,
        positive_folds=3,
        fold_count=3,
        average_clv=0.0182,
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return []
            if bucket == "historical_matches":
                return []
            return []

    with TemporaryDirectory() as data_dir:
        for season in ["2122", "2223", "2324", "2425", "2526"]:
            season_dir = Path(data_dir) / season
            season_dir.mkdir()
            (season_dir / "E0.csv").write_text(
                "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n",
                encoding="utf-8",
            )
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            backtest=BacktestSettings(data_dir=data_dir, default_season="2526"),
            leagues=[
                LeagueSettings(
                    code="EPL",
                    name="Premier League",
                    aliases=["Premier League"],
                    football_data_uk_code="E0",
                    strategy_mode="live",
                    paper_only=False,
                )
            ],
            strategy_profiles=[profile],
            data_sources={
                "the_odds_api": SourceSettings(
                    name="The Odds API",
                    enabled=False,
                    base_url="https://api.the-odds-api.com/v4",
                    api_key_env="THE_ODDS_API_KEY",
                )
            },
            execution_brokers={
                "betfair_exchange": ExecutionBrokerSettings(
                    name="Betfair Exchange API-NG",
                    enabled=False,
                    provider="betfair",
                    base_url="https://api.betfair.com/exchange/betting/json-rpc/v1",
                    credential_envs=["BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN"],
                    required_match_external_ids=[],
                    required_selection_external_ids=[],
                    stake_currency="GBP",
                    stake_currency_per_unit=None,
                    official_url="https://docs.developer.betfair.com/",
                )
            },
        )
        service = SimpleNamespace(repository=FakeRepository(), settings=settings)
        candidate_config = Path(data_dir) / "candidate.yaml"
        env_names = [
            "BETFAIR_APP_KEY",
            "BETFAIR_SESSION_TOKEN",
            "FOOTBALL_ADMIN_TOKEN",
            "THE_ODDS_API_KEY",
            "POSTGRES_BIND_HOST",
            "POSTGRES_PASSWORD",
            "API_BIND_HOST",
        ]
        old_env = {name: os.environ.get(name) for name in env_names}
        for name in ["BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN", "FOOTBALL_ADMIN_TOKEN", "THE_ODDS_API_KEY"]:
            os.environ[name] = ""
        os.environ["API_BIND_HOST"] = "127.0.0.1"
        os.environ["POSTGRES_BIND_HOST"] = "127.0.0.1"
        os.environ["POSTGRES_PASSWORD"] = "football"
        try:
            report = build_production_onboarding_checklist(
                service,
                config_path=candidate_config,
                audit_runner=lambda repository, settings, include_past=False: SimpleNamespace(
                    status="blocked",
                    issues=[],
                    recommended_count=0,
                    total_live_stake_units=0,
                    items=[],
                ),
            )
            markdown = format_production_onboarding_checklist_markdown(report)
        finally:
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    assert report["status"] == "action_required"
    assert report["required_envs"] == [
        "BETFAIR_APP_KEY",
        "BETFAIR_SESSION_TOKEN",
        "FOOTBALL_ADMIN_TOKEN",
        "POSTGRES_PASSWORD",
    ]
    assert any(section["key"] == "manual_review" for section in report["sections"])
    assert any(section["key"] == "runtime_security" for section in report["sections"])
    assert any(section["key"] == "secrets" for section in report["sections"])
    assert any(item["code"] == "apply_profile_promotion" for item in report["items"])
    assert any(item["code"] == "configure_data_source:qqsd" for item in report["items"])
    assert any(item["code"] == "enable_broker:betfair_exchange" for item in report["items"])
    assert "Production Onboarding Checklist" in markdown
    assert "Required Environment Variables" in markdown
    assert "Official Provider Links" in markdown
    assert "footballctl production-deploy-check --target full --fail-on-blocked --json" in markdown


def test_production_onboarding_apply_plan_marks_config_ready_when_prerequisites_exist() -> None:
    from football_analysis.production import build_production_onboarding_apply_plan
    from football_analysis.settings import (
        AppSettings,
        BacktestSettings,
        ExecutionBrokerSettings,
        LeagueSettings,
        Settings,
        SourceSettings,
        StrategyProfileSettings,
    )

    match = Match(
        id="epl-config-ready-1",
        league="Premier League",
        home_team="Home",
        away_team="Away",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )
    profile = StrategyProfileSettings(
        id="e0_all_home_robust",
        name="E0 all-season home value",
        league_code="E0",
        market_type="1x2",
        selections=["HOME"],
        season_phases=["all"],
        stability_label="robust",
        roi=0.0502,
        settled_bets=176,
        positive_folds=3,
        fold_count=3,
        average_clv=0.0182,
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return []
            if bucket == "historical_matches":
                return []
            return []

    with TemporaryDirectory() as data_dir:
        for season in ["2122", "2223", "2324", "2425", "2526"]:
            season_dir = Path(data_dir) / season
            season_dir.mkdir()
            (season_dir / "E0.csv").write_text(
                "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n",
                encoding="utf-8",
            )
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            backtest=BacktestSettings(data_dir=data_dir, default_season="2526"),
            leagues=[
                LeagueSettings(
                    code="EPL",
                    name="Premier League",
                    aliases=["Premier League"],
                    football_data_uk_code="E0",
                    strategy_mode="live",
                    paper_only=False,
                )
            ],
            strategy_profiles=[profile],
            data_sources={
                "the_odds_api": SourceSettings(
                    name="The Odds API",
                    enabled=False,
                    base_url="https://api.the-odds-api.com/v4",
                    api_key_env="THE_ODDS_API_KEY",
                    sport_keys={"EPL": "soccer_epl"},
                )
            },
            execution_brokers={
                "betfair_exchange": ExecutionBrokerSettings(
                    name="Betfair Exchange API-NG",
                    enabled=False,
                    provider="betfair",
                    base_url="https://api.betfair.com/exchange/betting/json-rpc/v1",
                    credential_envs=["BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN"],
                    required_match_external_ids=[],
                    required_selection_external_ids=[],
                    stake_currency="GBP",
                    stake_currency_per_unit=None,
                    official_url="https://docs.developer.betfair.com/",
                )
            },
        )
        service = SimpleNamespace(repository=FakeRepository(), settings=settings)
        candidate_config = Path(data_dir) / "candidate.yaml"
        env_names = ["BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN", "THE_ODDS_API_KEY"]
        old_env = {name: os.environ.get(name) for name in env_names}
        for name in env_names:
            os.environ[name] = "dummy"
        try:
            plan = build_production_onboarding_apply_plan(
                service,
                config_path=candidate_config,
                broker_stake_currency_per_unit=25.0,
                audit_runner=lambda repository, settings, include_past=False: SimpleNamespace(
                    status="blocked",
                    issues=[],
                    recommended_count=0,
                    total_live_stake_units=0,
                    items=[],
                ),
            )
        finally:
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    items = {item["code"]: item for item in plan["items"]}
    assert items["enable_data_source:the_odds_api"]["status"] == "ready"
    assert items["set_broker_stake_currency_per_unit:betfair_exchange"]["status"] == "ready"
    assert items["enable_broker:betfair_exchange"]["status"] == "blocked"
    assert items["enable_broker:betfair_exchange"]["blocking_reasons"] == ["stake_currency_per_unit_required"]
    assert "credential_or_value_prerequisite_required" not in plan["blocked_reasons"]
    assert any(
        command
        == (
            f"footballctl production-config-plan --source the_odds_api --apply "
            f"--config-path {candidate_config} --json"
        )
        for command in plan["ready_commands"]
    )
    assert any(
        command
        == (
            "footballctl production-config-plan --broker betfair_exchange "
            f"--stake-currency-per-unit 25 --apply --config-path {candidate_config} --json"
        )
        for command in plan["ready_commands"]
    )


def test_production_candidate_check_converges_ready_config_passes() -> None:
    from football_analysis.production import build_production_candidate_check
    from football_analysis.settings import (
        AppSettings,
        BacktestSettings,
        ExecutionBrokerSettings,
        LeagueSettings,
        Settings,
        SourceSettings,
        StrategyProfileSettings,
    )

    match = Match(
        id="epl-candidate-converge-1",
        league="Premier League",
        home_team="Home",
        away_team="Away",
        kickoff_at=datetime.now(UTC) + timedelta(days=1),
        data_completeness=0.9,
    )
    profile = StrategyProfileSettings(
        id="e0_all_home_robust",
        name="E0 all-season home value",
        league_code="E0",
        market_type="1x2",
        selections=["HOME"],
        season_phases=["all"],
        stability_label="robust",
        roi=0.0502,
        settled_bets=176,
        positive_folds=3,
        fold_count=3,
        average_clv=0.0182,
    )

    class FakeRepository:
        def list_models(self, bucket: str, model_type: type[object]) -> list[object]:
            if bucket == "matches":
                return [match]
            if bucket == "odds":
                return []
            if bucket == "historical_matches":
                return []
            return []

    with TemporaryDirectory() as data_dir:
        for season in ["2122", "2223", "2324", "2425", "2526"]:
            season_dir = Path(data_dir) / season
            season_dir.mkdir()
            (season_dir / "E0.csv").write_text(
                "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n",
                encoding="utf-8",
            )
        settings = Settings(
            app=AppSettings(timezone="UTC"),
            backtest=BacktestSettings(data_dir=data_dir, default_season="2526"),
            leagues=[
                LeagueSettings(
                    code="EPL",
                    name="Premier League",
                    aliases=["Premier League"],
                    football_data_uk_code="E0",
                    strategy_mode="live",
                    paper_only=False,
                )
            ],
            strategy_profiles=[profile],
            data_sources={
                "the_odds_api": SourceSettings(
                    name="The Odds API",
                    enabled=False,
                    base_url="https://api.the-odds-api.com/v4",
                    api_key_env="THE_ODDS_API_KEY",
                    sport_keys={"EPL": "soccer_epl"},
                )
            },
            execution_brokers={
                "betfair_exchange": ExecutionBrokerSettings(
                    name="Betfair Exchange API-NG",
                    enabled=False,
                    provider="betfair",
                    base_url="https://api.betfair.com/exchange/betting/json-rpc/v1",
                    credential_envs=["BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN"],
                    required_match_external_ids=[],
                    required_selection_external_ids=[],
                    stake_currency="GBP",
                    stake_currency_per_unit=None,
                    official_url="https://docs.developer.betfair.com/",
                )
            },
        )
        source_config = Path(data_dir) / "source.yaml"
        candidate_config = Path(data_dir) / "candidate.yaml"
        source_config.write_text(
            yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=False, allow_unicode=False),
            encoding="utf-8",
        )
        service = SimpleNamespace(repository=FakeRepository(), settings=settings)
        calls: list[list[str]] = []

        def fake_runner(argv: list[str], timeout_seconds: int) -> object:
            calls.append(argv)
            config_path = Path(argv[argv.index("--config-path") + 1])
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if argv[1] == "production-profile-promote":
                for item in raw["strategy_profiles"]:
                    if item["id"] == "e0_all_home_robust":
                        item["live_enabled"] = True
                        item["max_stake_units"] = 0.2
            elif argv[1] == "production-config-plan" and "--source" in argv:
                source_id = argv[argv.index("--source") + 1]
                raw["data_sources"][source_id]["enabled"] = True
            elif argv[1] == "production-config-plan" and "--broker" in argv:
                broker_id = argv[argv.index("--broker") + 1]
                broker = raw["execution_brokers"][broker_id]
                if "--stake-currency-per-unit" in argv:
                    broker["stake_currency_per_unit"] = float(argv[argv.index("--stake-currency-per-unit") + 1])
                    broker["enabled"] = True
                else:
                    broker["enabled"] = True
            config_path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=False), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="applied", stderr="")

        env_names = ["BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN", "THE_ODDS_API_KEY"]
        old_env = {name: os.environ.get(name) for name in env_names}
        for name in env_names:
            os.environ[name] = "dummy"
        try:
            report = build_production_candidate_check(
                service,
                source_config_path=source_config,
                candidate_config_path=candidate_config,
                broker_stake_currency_per_unit=25.0,
                target="record-only",
                timeout_seconds=60,
                command_runner=fake_runner,
                audit_runner=lambda repository, settings, include_past=False: SimpleNamespace(
                    status="blocked",
                    issues=[],
                    recommended_count=0,
                    total_live_stake_units=0,
                    items=[],
                ),
                decision_runner=lambda svc, include_past=False: SimpleNamespace(
                    status="ready",
                    ready_to_bet=True,
                    action="place_approved_live_bets",
                    issues=[],
                    components={},
                ),
            )
        finally:
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        raw = yaml.safe_load(candidate_config.read_text(encoding="utf-8"))

    assert report["apply_plan"]["pass_count"] == 2
    assert report["apply_plan"]["succeeded_count"] == 3
    assert len(report["apply_passes"]) == 2
    assert report["apply_passes"][0]["succeeded_count"] == 3
    assert report["apply_passes"][1]["ready_count"] == 0
    assert any("--source" in call and "the_odds_api" in call for call in calls)
    assert any("--stake-currency-per-unit" in call and "25" in call for call in calls)
    broker_config_calls = [call for call in calls if call[1] == "production-config-plan" and "--broker" in call]
    assert len(broker_config_calls) == 1
    assert "--stake-currency-per-unit" in broker_config_calls[0]
    assert raw["data_sources"]["the_odds_api"]["enabled"] is True
    assert raw["execution_brokers"]["betfair_exchange"]["stake_currency_per_unit"] == 25.0
    assert raw["execution_brokers"]["betfair_exchange"]["enabled"] is True


def test_production_preflight_blocks_when_broker_is_required() -> None:
    from football_analysis.production import build_production_preflight

    service, fake_audit = _production_broker_plan_service(mapped=False, enabled=True)
    report = build_production_preflight(
        service,
        require_broker=True,
        require_execution_queue=True,
        audit_runner=fake_audit,
        decision_runner=lambda svc, include_past=False: SimpleNamespace(
            status="ready",
            ready_to_bet=True,
            action="place_approved_live_bets",
            issues=[],
            components={},
        ),
    )

    assert report["status"] == "blocked"
    assert report["ready_for_worker"] is True
    assert report["ready_for_record_execution"] is True
    assert report["ready_for_broker_execution"] is False
    assert "broker_not_ready:blocked" in report["issues"]
    assert any(issue.startswith("broker:broker_mapping_missing") for issue in report["issues"])


def test_production_broker_execute_blocks_without_credentials() -> None:
    from football_analysis.production import run_production_broker_execution

    old_app_key = os.environ.get("BETFAIR_APP_KEY")
    old_session = os.environ.get("BETFAIR_SESSION_TOKEN")
    os.environ.pop("BETFAIR_APP_KEY", None)
    os.environ.pop("BETFAIR_SESSION_TOKEN", None)
    try:
        service, fake_audit = _production_broker_plan_service(mapped=True, enabled=True)
        report = run_production_broker_execution(service, audit_runner=fake_audit)
    finally:
        if old_app_key is not None:
            os.environ["BETFAIR_APP_KEY"] = old_app_key
        if old_session is not None:
            os.environ["BETFAIR_SESSION_TOKEN"] = old_session

    assert report["status"] == "blocked"
    assert report["selected_count"] == 0
    assert "missing_broker_credential:BETFAIR_APP_KEY" in report["issues"]
    assert "missing_broker_credential:BETFAIR_SESSION_TOKEN" in report["issues"]


def test_production_broker_execute_dry_run_builds_redacted_betfair_request() -> None:
    from football_analysis.production import run_production_broker_execution

    calls: list[tuple[str, object, dict[str, str], float]] = []

    def fake_sender(url: str, body: object, headers: dict[str, str], timeout: float) -> object:
        calls.append((url, body, headers, timeout))
        return {}

    old_app_key = os.environ.get("BETFAIR_APP_KEY")
    old_session = os.environ.get("BETFAIR_SESSION_TOKEN")
    os.environ["BETFAIR_APP_KEY"] = "app-key-test"
    os.environ["BETFAIR_SESSION_TOKEN"] = "session-test"
    try:
        service, fake_audit = _production_broker_plan_service(mapped=True, enabled=True)
        report = run_production_broker_execution(
            service,
            audit_runner=fake_audit,
            request_sender=fake_sender,
        )
    finally:
        if old_app_key is None:
            os.environ.pop("BETFAIR_APP_KEY", None)
        else:
            os.environ["BETFAIR_APP_KEY"] = old_app_key
        if old_session is None:
            os.environ.pop("BETFAIR_SESSION_TOKEN", None)
        else:
            os.environ["BETFAIR_SESSION_TOKEN"] = old_session

    assert calls == []
    assert report["status"] == "dry_run"
    assert report["execute_broker_orders"] is False
    assert report["selected_count"] == 1
    assert report["dry_run_count"] == 1
    record = report["records"][0]
    assert record["status"] == "dry_run"
    assert record["customer_ref"].startswith("fa")
    assert len(record["customer_ref"]) == 32
    request = record["request"]
    assert request["headers"]["X-Application"] == "<redacted>"
    assert request["headers"]["X-Authentication"] == "<redacted>"
    assert "app-key-test" not in json.dumps(report)
    assert "session-test" not in json.dumps(report)
    body = request["body"][0]
    assert body["method"] == "SportsAPING/v1.0/placeOrders"
    assert body["params"]["marketId"] == "1.23456789"
    assert body["params"]["customerRef"] == record["customer_ref"]
    instruction = body["params"]["instructions"][0]
    assert instruction["selectionId"] == 12345
    assert instruction["side"] == "BACK"
    assert instruction["orderType"] == "LIMIT"
    assert instruction["limitOrder"] == {
        "size": 5.0,
        "price": 5.445,
        "persistenceType": "LAPSE",
    }


def test_production_broker_execute_posts_only_with_explicit_flag() -> None:
    from football_analysis.production import run_production_broker_execution

    calls: list[tuple[str, object, dict[str, str], float]] = []

    def fake_sender(url: str, body: object, headers: dict[str, str], timeout: float) -> object:
        calls.append((url, body, headers, timeout))
        return [
            {
                "jsonrpc": "2.0",
                "result": {
                    "status": "SUCCESS",
                    "marketId": "1.23456789",
                    "instructionReports": [{"status": "SUCCESS", "betId": "bet-1"}],
                },
                "id": 1,
            }
        ]

    old_app_key = os.environ.get("BETFAIR_APP_KEY")
    old_session = os.environ.get("BETFAIR_SESSION_TOKEN")
    os.environ["BETFAIR_APP_KEY"] = "app-key-test"
    os.environ["BETFAIR_SESSION_TOKEN"] = "session-test"
    try:
        service, fake_audit = _production_broker_plan_service(mapped=True, enabled=True)
        report = run_production_broker_execution(
            service,
            execute_broker_orders=True,
            audit_runner=fake_audit,
            request_sender=fake_sender,
            request_timeout_seconds=3.0,
        )
    finally:
        if old_app_key is None:
            os.environ.pop("BETFAIR_APP_KEY", None)
        else:
            os.environ["BETFAIR_APP_KEY"] = old_app_key
        if old_session is None:
            os.environ.pop("BETFAIR_SESSION_TOKEN", None)
        else:
            os.environ["BETFAIR_SESSION_TOKEN"] = old_session

    assert report["status"] == "executed"
    assert report["sent_count"] == 1
    assert report["dry_run_count"] == 0
    assert report["error_count"] == 0
    assert len(calls) == 1
    url, body, headers, timeout = calls[0]
    assert url == "https://api.betfair.com/exchange/betting/json-rpc/v1"
    assert body[0]["method"] == "SportsAPING/v1.0/placeOrders"
    assert headers["X-Application"] == "app-key-test"
    assert headers["X-Authentication"] == "session-test"
    assert timeout == 3.0
    assert report["records"][0]["request"]["headers"]["X-Application"] == "<redacted>"
    assert report["records"][0]["response"][0]["result"]["instructionReports"][0]["betId"] == "bet-1"


def test_production_broker_discovery_dry_run_builds_catalogue_request() -> None:
    from football_analysis.production import run_production_broker_discovery

    calls: list[tuple[str, object, dict[str, str], float]] = []

    def fake_sender(url: str, body: object, headers: dict[str, str], timeout: float) -> object:
        calls.append((url, body, headers, timeout))
        return {}

    service, fake_audit = _production_broker_plan_service(mapped=False, enabled=False)
    report = run_production_broker_discovery(
        service,
        audit_runner=fake_audit,
        request_sender=fake_sender,
        max_results=7,
        match_window_hours=12,
    )

    assert calls == []
    assert report["status"] == "dry_run"
    assert report["selected_count"] == 1
    assert report["dry_run_count"] == 1
    record = report["records"][0]
    assert record["status"] == "dry_run"
    request = record["request"]
    body = request["body"][0]
    assert body["method"] == "SportsAPING/v1.0/listMarketCatalogue"
    assert body["params"]["filter"]["eventTypeIds"] == ["1"]
    assert body["params"]["filter"]["textQuery"] == "Corinthians Remo"
    assert body["params"]["maxResults"] == "7"
    assert "RUNNER_DESCRIPTION" in body["params"]["marketProjection"]


def test_production_broker_discovery_blocks_remote_without_credentials() -> None:
    from football_analysis.production import run_production_broker_discovery

    old_app_key = os.environ.get("BETFAIR_APP_KEY")
    old_session = os.environ.get("BETFAIR_SESSION_TOKEN")
    os.environ.pop("BETFAIR_APP_KEY", None)
    os.environ.pop("BETFAIR_SESSION_TOKEN", None)
    try:
        service, fake_audit = _production_broker_plan_service(mapped=False, enabled=False)
        report = run_production_broker_discovery(
            service,
            fetch_remote=True,
            audit_runner=fake_audit,
        )
    finally:
        if old_app_key is not None:
            os.environ["BETFAIR_APP_KEY"] = old_app_key
        if old_session is not None:
            os.environ["BETFAIR_SESSION_TOKEN"] = old_session

    assert report["status"] == "blocked"
    assert report["records"] == []
    assert "missing_broker_credential:BETFAIR_APP_KEY" in report["issues"]
    assert "missing_broker_credential:BETFAIR_SESSION_TOKEN" in report["issues"]


def test_production_broker_discovery_fetches_and_applies_mapping() -> None:
    from football_analysis.production import run_production_broker_discovery

    calls: list[tuple[str, object, dict[str, str], float]] = []

    def fake_sender(url: str, body: object, headers: dict[str, str], timeout: float) -> object:
        calls.append((url, body, headers, timeout))
        return [
            {
                "jsonrpc": "2.0",
                "result": [
                    {
                        "marketId": "1.23456789",
                        "marketName": "Asian Handicap",
                        "marketStartTime": "2026-06-13T20:00:00.000Z",
                        "totalMatched": 1234.5,
                        "event": {"name": "Corinthians v Remo"},
                        "runners": [
                            {
                                "selectionId": 12345,
                                "runnerName": "Corinthians",
                                "handicap": -0.5,
                            },
                            {
                                "selectionId": 67890,
                                "runnerName": "Remo",
                                "handicap": 0.5,
                            },
                        ],
                    }
                ],
                "id": 1,
            }
        ]

    old_app_key = os.environ.get("BETFAIR_APP_KEY")
    old_session = os.environ.get("BETFAIR_SESSION_TOKEN")
    os.environ["BETFAIR_APP_KEY"] = "app-key-test"
    os.environ["BETFAIR_SESSION_TOKEN"] = "session-test"
    try:
        service, fake_audit = _production_broker_plan_service(mapped=False, enabled=True)
        report = run_production_broker_discovery(
            service,
            fetch_remote=True,
            apply_mappings=True,
            audit_runner=fake_audit,
            request_sender=fake_sender,
            request_timeout_seconds=5.0,
        )
    finally:
        if old_app_key is None:
            os.environ.pop("BETFAIR_APP_KEY", None)
        else:
            os.environ["BETFAIR_APP_KEY"] = old_app_key
        if old_session is None:
            os.environ.pop("BETFAIR_SESSION_TOKEN", None)
        else:
            os.environ["BETFAIR_SESSION_TOKEN"] = old_session

    assert report["status"] == "discovered"
    assert report["discovered_count"] == 1
    assert len(calls) == 1
    assert calls[0][2]["X-Application"] == "app-key-test"
    record = report["records"][0]
    assert record["status"] == "discovered"
    assert record["suggested_external_ids"] == {
        "betfair_market_id": "1.23456789",
        "betfair_selection_id_AH_HOME": "12345",
        "betfair_handicap_AH_HOME": "-0.5",
    }
    assert report["applied_mappings"] == [
        {
            "match_id": "broker-match-1",
            "status": "applied",
            "confidence": "high",
            "external_ids_patch": record["suggested_external_ids"],
        }
    ]
    stored = service.repository.get_model("matches", "broker-match-1", Match)
    assert stored.external_ids["betfair_market_id"] == "1.23456789"
    assert stored.external_ids["betfair_selection_id_AH_HOME"] == "12345"


if __name__ == "__main__":
    test_production_cycle_runs_refresh_decision_and_daily_ops()
    test_production_cycle_auto_refresh_uses_live_refresh_plan()
    test_production_cycle_refresh_dry_run_skips_auto_refresh_side_effects()
    test_production_cycle_refresh_dry_run_skips_fixed_ingestion()
    test_production_cycle_expands_live_leagues_when_active_profiles_are_empty()
    test_production_cycle_runs_execution_stage_when_ready()
    test_production_cycle_can_run_record_only_execution_stage()
    test_production_cycle_can_run_broker_stages_when_ready()
    test_production_cycle_can_run_remote_apply_and_live_broker_modes()
    test_production_cycle_records_heartbeat_job()
    test_production_cycle_can_run_data_apply_stage()
    test_production_worker_passes_data_apply_stage_options()
    test_production_worker_passes_refresh_dry_run_to_auto_refresh()
    test_production_alert_message_summarizes_blocked_report()
    test_production_worker_notifies_each_report()
    test_production_cli_exposes_alert_text_option()
    test_telegram_alert_skips_without_credentials()
    test_telegram_alert_posts_send_message_payload()
    test_cli_telegram_notification_result_is_json()
    test_production_status_summarizes_recent_jobs_and_counts()
    test_production_status_blocks_ready_decision_when_execution_queue_requires_review()
    test_production_status_flags_missing_recent_odds_job()
    test_production_status_uses_all_jobs_for_required_job_health()
    test_production_status_ignores_refresh_dry_run_for_required_heartbeat()
    test_production_health_reports_fresh_pipeline()
    test_production_health_treats_empty_refresh_as_warning_when_data_exists()
    test_production_health_flags_empty_refresh_when_no_data_exists()
    test_production_health_allows_fresh_running_job()
    test_production_health_flags_stale_running_job()
    test_production_health_flags_stale_heartbeat()
    test_production_readiness_flags_active_live_league_without_history_or_profile()
    test_production_readiness_allows_profileless_tier_policy_when_history_exists()
    test_production_data_plan_prefers_football_data_uk_extra_csv()
    test_production_data_plan_imports_missing_standard_csv_seasons()
    test_production_data_plan_adds_the_odds_api_live_command_when_enabled()
    test_production_data_plan_adds_sportmonks_live_command_when_enabled()
    test_production_data_plan_does_not_require_paid_provider_when_live_source_ready()
    test_the_odds_api_sports_report_is_dry_run_by_default()
    test_production_historical_odds_plan_generates_bounded_commands_and_cost()
    test_arg_primera_has_the_odds_api_sport_key_mapping()
    test_production_data_plan_can_batch_the_odds_api_historical_snapshots()
    test_production_data_apply_runs_safe_local_commands_and_skips_remote_by_default()
    test_production_data_apply_skips_profile_promotion_without_risk_cap()
    test_production_data_apply_skips_profile_promotion_with_skip_backtests()
    test_production_data_plan_suggests_conservative_profile_stake_cap()
    test_production_config_plan_blocks_missing_credentials_by_default()
    test_production_config_plan_can_apply_ready_config_patch()
    test_production_config_plan_respects_explicit_source_and_broker_scope()
    test_production_profile_promotion_requires_stake_and_can_apply_temp_config()
    test_production_execution_queue_generates_safe_record_bet_command()
    test_production_execution_queue_omits_fully_filled_candidates()
    test_production_execute_dry_run_does_not_write_bets()
    test_production_execute_records_remaining_stake_idempotently()
    test_production_broker_plan_blocks_missing_credentials_and_mapping()
    test_production_broker_plan_builds_betfair_order_payload()
    test_production_onboarding_reports_broker_prerequisites()
    test_production_deploy_check_blocks_broker_live_until_prerequisites_are_ready()
    test_production_preflight_warns_about_broker_by_default()
    test_production_deploy_check_record_only_allows_empty_execution_queue_by_default()
    test_production_deploy_check_reports_record_only_verified_after_queue_is_consumed()
    test_production_deploy_check_defaults_to_worker_target()
    test_production_deploy_check_worker_allows_missing_health_history_on_fresh_db()
    test_production_deploy_check_worker_blocks_stale_health_history()
    test_production_runtime_security_warns_on_local_defaults()
    test_production_runtime_security_blocks_broker_live_insecure_runtime()
    test_production_runtime_secret_bootstrap_redacts_values_by_default()
    test_production_onboarding_checklist_includes_runtime_security_inputs()
    test_production_deployment_doctor_blocks_broker_live_insecure_runtime()
    test_production_deployment_doctor_worker_allows_missing_health_history_on_fresh_db()
    test_production_preflight_reports_profile_promotion_gate()
    test_production_onboarding_apply_plan_marks_profile_promotion_ready()
    test_production_candidate_check_applies_ready_items_to_candidate_only()
    test_production_onboarding_checklist_exports_required_inputs_and_markdown()
    test_production_onboarding_apply_plan_marks_config_ready_when_prerequisites_exist()
    test_production_candidate_check_converges_ready_config_passes()
    test_production_preflight_blocks_when_broker_is_required()
    test_production_broker_execute_blocks_without_credentials()
    test_production_broker_execute_dry_run_builds_redacted_betfair_request()
    test_production_broker_execute_posts_only_with_explicit_flag()
    test_production_broker_discovery_dry_run_builds_catalogue_request()
    test_production_broker_discovery_blocks_remote_without_credentials()
    test_production_broker_discovery_fetches_and_applies_mapping()
    print("production worker verification passed")
