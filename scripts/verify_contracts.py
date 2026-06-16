from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml


def main() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    postgres_environment = compose["services"]["postgres"]["environment"]
    assert postgres_environment["POSTGRES_DB"] == "${POSTGRES_DB:-football_analysis}"
    assert postgres_environment["POSTGRES_USER"] == "${POSTGRES_USER:-football}"
    assert postgres_environment["POSTGRES_PASSWORD"] == "${POSTGRES_PASSWORD:-football}"
    assert compose["services"]["postgres"]["ports"] == [
        "${POSTGRES_BIND_HOST:-127.0.0.1}:${POSTGRES_PORT:-5432}:5432"
    ]
    api_health = compose["services"]["api"]["healthcheck"]["test"]
    api_health_command = " ".join(str(part) for part in api_health)
    assert "/healthz" in api_health_command
    worker_health = compose["services"]["worker"]["healthcheck"]["test"]
    worker_health_command = " ".join(str(part) for part in worker_health)
    assert "production-deploy-check" in worker_health_command
    assert "PRODUCTION_DEPLOY_TARGET" in worker_health_command
    assert "--fail-on-blocked" in worker_health_command
    worker_command = [str(part) for part in compose["services"]["worker"]["command"]]
    assert worker_command == ["footballctl", "production-worker-env"]
    worker_environment = compose["services"]["worker"]["environment"]
    api_environment = compose["services"]["api"]["environment"]
    assert compose["services"]["api"]["ports"] == ["${API_BIND_HOST:-127.0.0.1}:${API_PORT:-18000}:8000"]
    assert (
        api_environment["DATABASE_URL"]
        == "postgresql+psycopg://${POSTGRES_USER:-football}:${POSTGRES_PASSWORD:-football}@postgres:5432/${POSTGRES_DB:-football_analysis}"
    )
    assert api_environment["FOOTBALL_ADMIN_TOKEN"] == "${FOOTBALL_ADMIN_TOKEN:-}"
    assert worker_environment["DATABASE_URL"] == api_environment["DATABASE_URL"]
    assert worker_environment["WORKER_EXECUTION_MODE"] == "${WORKER_EXECUTION_MODE:-dry-run}"
    assert worker_environment["WORKER_DATA_APPLY_MODE"] == "${WORKER_DATA_APPLY_MODE:-safe}"
    assert worker_environment["WORKER_REQUIRE_DEPLOY_READY"] == "${WORKER_REQUIRE_DEPLOY_READY:-1}"
    assert worker_environment["PRODUCTION_DEPLOY_TARGET"] == "${PRODUCTION_DEPLOY_TARGET:-worker}"

    with TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'contracts.db'}"
        commands = [
            ["footballctl", "db", "init", "--json"],
            ["footballctl", "picks", "today", "--json"],
            ["footballctl", "live-audit", "--json"],
            ["footballctl", "live-preflight", "--json"],
            ["footballctl", "live-review", "--json"],
            ["footballctl", "live-decision", "--json"],
            ["footballctl", "production-preflight", "--json"],
            ["footballctl", "production-status", "--json"],
            ["footballctl", "production-health", "--json"],
            ["footballctl", "production-onboarding", "--json"],
            ["footballctl", "production-onboarding-checklist", "--json"],
            ["footballctl", "production-onboarding-apply-plan", "--json"],
            ["footballctl", "production-deploy-check", "--json"],
            ["footballctl", "production-runtime-security", "--json"],
            ["footballctl", "production-runtime-secrets", "--json"],
            [
                "footballctl",
                "production-deployment-doctor",
                "--candidate-config",
                str(Path(tmp) / "doctor-candidate.yaml"),
                "--plan-only",
                "--json",
            ],
            [
                "footballctl",
                "production-candidate-check",
                "--candidate-config",
                str(Path(tmp) / "candidate.yaml"),
                "--plan-only",
                "--json",
            ],
            ["footballctl", "production-data-plan", "--json"],
            ["footballctl", "production-config-plan", "--json"],
            [
                "footballctl",
                "production-historical-odds-plan",
                "--league",
                "EPL",
                "--start-time",
                "2027-01-03T12:00:00Z",
                "--end-time",
                "2027-01-03T12:20:00Z",
                "--max-snapshots",
                "2",
                "--json",
            ],
            ["footballctl", "production-profile-promote", "--json"],
            ["footballctl", "production-data-apply", "--json"],
            ["footballctl", "production-execution-queue", "--json"],
            ["footballctl", "production-broker-plan", "--json"],
            ["footballctl", "production-broker-discovery", "--json"],
            ["footballctl", "production-broker-execute", "--json"],
            ["footballctl", "production-execute", "--json"],
            ["footballctl", "live-refresh", "--date", "2026-01-18", "--scope", "live-leagues", "--dry-run", "--json"],
            ["footballctl", "daily-ops", "--date", "2026-01-18", "--json"],
            ["footballctl", "settle-open-bets", "--json"],
            ["footballctl", "sources", "--json"],
            ["footballctl", "sources-the-odds-api-sports", "--json"],
            [
                "footballctl",
                "ingest",
                "historical-odds",
                "--league",
                "EPL",
                "--snapshot-time",
                "2027-01-03T12:00:00Z",
                "--json",
            ],
            ["footballctl", "performance", "--json"],
        ]
        for command in commands:
            output = subprocess.check_output(command, text=True, encoding="utf-8", env=env)
            payload = json.loads(output)
            if command[:2] == ["footballctl", "production-deploy-check"]:
                assert payload["target"] == "worker"
                assert payload["status"] != "blocked"
            if command[:2] == ["footballctl", "production-runtime-secrets"]:
                assert payload["secret_values_visible"] is False
                assert all(item["secret_value"] is None for item in payload["items"])
        ops_help = subprocess.check_output(
            ["footballctl", "production-ops-check", "--help"],
            text=True,
            encoding="utf-8",
            env=env,
        )
        assert "--api-url" in ops_help
        assert "--include-doctor" in ops_help
        blocked_deploy = subprocess.run(
            [
                "footballctl",
                "production-deploy-check",
                "--target",
                "broker-live",
                "--fail-on-blocked",
                "--json",
            ],
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        assert blocked_deploy.returncode == 2
        assert json.loads(blocked_deploy.stdout)["status"] == "blocked"

        worker_env = env.copy()
        worker_env.update(
            {
                "WORKER_ONCE": "1",
                "WORKER_JSON": "1",
                "WORKER_NOTIFY_TELEGRAM": "0",
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
        worker_output = subprocess.check_output(
            [
                "footballctl",
                "production-worker-env",
                "--once",
                "--json",
            ],
            text=True,
            encoding="utf-8",
            env=worker_env,
        )
        json.loads(worker_output)

        env["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'api.db'}"
        os.environ["DATABASE_URL"] = env["DATABASE_URL"]
        api_old_admin_token = os.environ.get("FOOTBALL_ADMIN_TOKEN")
        os.environ["FOOTBALL_ADMIN_TOKEN"] = ""
        from fastapi.testclient import TestClient
        from football_analysis.api import app

        client = TestClient(app)
        assert client.get("/").json()["status"] == "ok"
        assert client.get("/healthz").json()["database"] == "ok"
        assert client.get("/picks/today").status_code == 200
        assert client.get("/live/audit").status_code == 200
        assert client.get("/live/preflight").status_code == 200
        assert client.get("/live/review").status_code == 200
        assert client.get("/live/decision").status_code == 200
        assert client.get("/production/preflight").status_code == 200
        assert client.get("/production/status").status_code == 200
        assert client.get("/production/health").status_code == 200
        readiness_response = client.get("/production/readiness")
        assert readiness_response.status_code == 200
        readiness_payload = readiness_response.json()
        assert "status" in readiness_payload
        assert "leagues" in readiness_payload
        assert client.get("/production/onboarding").status_code == 200
        assert client.get("/production/onboarding-checklist").status_code == 200
        assert "markdown" in client.get(
            "/production/onboarding-checklist",
            params={"include_markdown": True},
        ).json()
        assert client.get("/production/onboarding-apply-plan").status_code == 200
        deploy_response = client.get("/production/deploy-check")
        assert deploy_response.status_code == 200
        assert deploy_response.json()["target"] == "worker"
        runtime_response = client.get("/production/runtime-security")
        assert runtime_response.status_code == 200
        assert runtime_response.json()["target"] == "worker"
        assert client.get(
            "/production/deployment-doctor",
            params={"candidate_config_path": str(Path(tmp) / "api-doctor-candidate.yaml")},
        ).status_code == 200
        assert client.post(
            "/production/candidate-check",
            params={"candidate_config_path": str(Path(tmp) / "api-candidate.yaml"), "execute_ready": False},
        ).status_code == 200
        assert client.get("/production/data-plan").status_code == 200
        assert client.post("/production/config-plan").status_code == 200
        assert client.get(
            "/production/historical-odds-plan",
            params={
                "league": "EPL",
                "start_time": "2027-01-03T12:00:00Z",
                "end_time": "2027-01-03T12:20:00Z",
                "max_snapshots": 2,
            },
        ).status_code == 200
        assert client.post("/production/profile-promotions").status_code == 200
        assert client.post("/production/data-apply").status_code == 200
        assert client.get("/production/execution-queue").status_code == 200
        assert client.get("/production/broker-plan").status_code == 200
        assert client.post("/production/broker-discovery").status_code == 200
        assert client.post("/production/broker-execute").status_code == 200
        assert client.post("/production/execute").status_code == 200
        assert client.post(
            "/live/refresh",
            params={"date": "2026-01-18", "scope": "live-leagues", "dry_run": True},
        ).status_code == 200
        assert client.post("/ops/daily", params={"date": "2026-01-18"}).status_code == 200
        assert client.post("/bets/settle-open").status_code == 200
        assert client.get("/sources/health").status_code == 200
        assert client.get("/sources/the-odds-api/sports").status_code == 200
        assert client.post(
            "/jobs/ingest/historical-odds",
            params={"league": "EPL", "snapshot_time": "2027-01-03T12:00:00Z"},
        ).status_code == 200
        assert client.get("/backtest/historical").status_code == 200

        from football_analysis.cli import _build_production_ops_check_report

        api_calls: list[tuple[str, dict[str, object]]] = []

        def fake_production_api(path: str, params: dict[str, object]) -> dict[str, object]:
            api_calls.append((path, params))
            payloads = {
                "/healthz": {"service": "football-analysis", "status": "ok", "database": "ok"},
                "/production/status": {
                    "overall_status": "ready",
                    "ready_to_bet": True,
                    "counts": {"matches": 3, "odds": 4, "recommendations": 2},
                    "recent_jobs": [
                        {
                            "id": "production_cycle:test",
                            "job_type": "production_cycle",
                            "status": "succeeded",
                            "finished_at": "2026-06-13T00:00:00",
                        }
                    ],
                },
                "/production/health": {
                    "status": "healthy",
                    "issues": [],
                    "warnings": ["empty_recent_job:ingest_fixtures"],
                },
                "/production/runtime-security": {
                    "status": "ready_with_warnings",
                    "issues": [],
                    "warnings": ["runtime_admin_token_missing"],
                },
                "/production/deploy-check": {
                    "status": "ready_with_warnings",
                    "issues": [],
                    "warnings": ["onboarding_action_required:1"],
                },
            }
            return {"ok": True, "status_code": 200, "payload": payloads[path]}

        ops_report = _build_production_ops_check_report(
            api_url="http://127.0.0.1:18000",
            target="worker",
            get_json=fake_production_api,
        )
        assert ops_report["status"] == "ready_with_warnings"
        assert ops_report["ready_for_target"] is True
        assert ops_report["api_reachable"] is True
        assert ops_report["issues"] == []
        assert ops_report["summary"]["counts"]["matches"] == 3
        assert "production_health:empty_recent_job:ingest_fixtures" in ops_report["warnings"]
        assert "runtime_security:runtime_admin_token_missing" in ops_report["warnings"]
        assert "deploy_check:onboarding_action_required:1" in ops_report["warnings"]
        assert ("/production/deploy-check", {"target": "worker", "broker": "betfair_exchange", "include_past": False, "platform": "real", "require_execution_queue": False}) in api_calls

        old_admin_token = os.environ.get("FOOTBALL_ADMIN_TOKEN")
        os.environ["FOOTBALL_ADMIN_TOKEN"] = "contract-admin-token"
        try:
            assert client.get("/production/deploy-check").status_code == 200
            assert client.get("/sources/the-odds-api/sports").status_code == 200
            assert client.get(
                "/sources/the-odds-api/sports",
                params={"fetch_remote": True},
            ).status_code == 401
            blocked_post = client.post("/production/data-apply")
            assert blocked_post.status_code == 401
            assert blocked_post.json()["detail"] == "admin_token_required"
            assert client.post(
                "/production/data-apply",
                headers={"X-Football-Admin-Token": "wrong-token"},
            ).status_code == 401
            assert client.post(
                "/production/data-apply",
                headers={"X-Football-Admin-Token": "contract-admin-token"},
            ).status_code == 200
            assert client.post(
                "/production/data-apply",
                headers={"Authorization": "Bearer contract-admin-token"},
            ).status_code == 200
        finally:
            if old_admin_token is None:
                os.environ.pop("FOOTBALL_ADMIN_TOKEN", None)
            else:
                os.environ["FOOTBALL_ADMIN_TOKEN"] = old_admin_token
        if api_old_admin_token is None:
            os.environ.pop("FOOTBALL_ADMIN_TOKEN", None)
        else:
            os.environ["FOOTBALL_ADMIN_TOKEN"] = api_old_admin_token

    print("contract verification passed")


if __name__ == "__main__":
    main()
