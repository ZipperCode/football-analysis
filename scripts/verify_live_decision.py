from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from football_analysis.api import app
from football_analysis.db import StructuredRepository
from football_analysis.settings import load_settings


def main() -> None:
    from football_analysis import cli

    printed: list[str] = []

    class CaptureConsole:
        def print(self, value: object = "") -> None:
            printed.append(str(value))

    original_console = cli.console
    cli.console = CaptureConsole()
    try:
        cli._print_live_decision_summary(
            SimpleNamespace(
                status="blocked",
                ready_to_bet=False,
                action="refresh_fixtures_and_odds",
                components={"preflight": "blocked"},
                odds_readiness=SimpleNamespace(
                    ready_profiles=0,
                    active_profiles=1,
                    scoped_odds_snapshots=2,
                    refresh_requirements=[
                        SimpleNamespace(
                            profile_id="i1_middle_ah_away_live_long_horizon",
                            refresh_league_code="SERIE_A",
                            strategy_league_code="I1",
                            market_type="asian_handicap",
                            selections=["AH_AWAY"],
                            required_bookmakers=2,
                            ready_matches=0,
                            needed_ready_matches=1,
                            issues=["no_matching_market_odds"],
                        )
                    ],
                ),
                issues=[],
                preflight=SimpleNamespace(
                    live_audit=SimpleNamespace(
                        total_matches=2,
                        recommended_count=0,
                        paper_candidate_count=1,
                        analysis_only_count=1,
                        gate_counts={"live_min_bookmakers:1/2": 1},
                        items=[
                            SimpleNamespace(
                                status="paper_candidate",
                                match_id="odds_api_io:1",
                                league="Brazil - Brasileiro Serie A",
                                home_team="Botafogo",
                                away_team="Santos",
                                market_type="asian_handicap",
                                selection="HOME",
                                value_score=100.0,
                                risk_score=18.0,
                                confidence=0.93,
                                gates_failed=[
                                    "live_min_bookmakers:1/2",
                                    "live_missing_strategy_profile",
                                    "live_min_data_quality:0.75/0.82",
                                ],
                            )
                        ],
                    )
                ),
            )
        )
    finally:
        cli.console = original_console
    summary_text = "\n".join(printed)
    assert "Closest blocked candidates:" in summary_text, "summary should surface near-live blocked candidates"
    assert "Botafogo vs Santos" in summary_text, "summary should identify the blocked candidate"
    assert "asian_handicap HOME" in summary_text, "summary should include market and selection"
    assert "live_min_bookmakers:1/2" in summary_text, "summary should include the actionable gate blocker"
    assert "Odds refresh requirements:" in summary_text, "summary should surface profile odds refresh requirements"
    assert "SERIE_A" in summary_text and "asian_handicap AH_AWAY" in summary_text
    assert "bookmakers>=2" in summary_text

    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'decision.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            from football_analysis.live_decision import run_live_decision

            report = run_live_decision(repository, settings, include_past=True)
            assert report.ready_to_bet is False, "empty decision snapshot must not approve betting"
            assert report.status in {"blocked", "no_matches", "no_trade", "paused"}
            assert report.action in {
                "review_strategy_profiles",
                "refresh_fixtures_and_odds",
                "wait_for_fixtures",
                "paper_or_observe_only",
                "do_not_bet_loss_pause",
            }
            assert report.profile_audit is not None, "decision must include strategy profile audit"
            assert report.odds_readiness is not None, "decision must include odds readiness"
            assert report.live_review is not None, "decision must include live review"
            assert report.preflight is not None, "decision must include live preflight"
            assert report.thresholds["max_odds_age_minutes"] == settings.live_trading.max_odds_age_minutes
            assert report.thresholds["max_execution_odds_slippage"] == settings.live_trading.max_execution_odds_slippage
            assert report.reproducibility["config_timezone"] == settings.app.timezone
            assert report.reproducibility["profile_audit_mode"] == "contract", (
                "live decision should default to fast profile contract audit"
            )
            assert "profile_audit" in report.components
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'cli.db'}"
        subprocess.check_output(["footballctl", "db", "init", "--json"], env=env, text=True, encoding="utf-8")
        completed = subprocess.run(
            ["footballctl", "live-decision", "--include-past", "--json"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        payload = json.loads(completed.stdout)
        assert "profile_audit" in payload, "CLI live-decision JSON must include profile audit"
        assert "odds_readiness" in payload, "CLI live-decision JSON must include odds readiness"
        assert "live_review" in payload, "CLI live-decision JSON must include live review"
        assert "preflight" in payload, "CLI live-decision JSON must include preflight"
        assert "thresholds" in payload, "CLI live-decision JSON must include live thresholds"
        assert payload["reproducibility"]["profile_audit_mode"] == "contract"

        full_audit = subprocess.run(
            ["footballctl", "live-decision", "--include-past", "--full-profile-audit", "--json"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        full_payload = json.loads(full_audit.stdout)
        assert full_payload["reproducibility"]["profile_audit_mode"] == "full"

        summary = subprocess.run(
            ["footballctl", "live-decision", "--include-past"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        assert "Live decision:" in summary.stdout, "CLI live-decision should print an operator summary"
        assert len(summary.stdout.splitlines()) <= 30, "CLI live-decision summary must stay readable for live ops"
        assert "'profile_audit':" not in summary.stdout, "CLI live-decision must not dump nested model repr"

        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'api.db'}"
        client = TestClient(app)
        response = client.get("/live/decision", params={"include_past": True})
        assert response.status_code == 200
        api_payload = response.json()
        assert "profile_audit" in api_payload, "API live decision JSON must include profile audit"
        assert "preflight" in api_payload, "API live decision JSON must include preflight"
        assert api_payload["reproducibility"]["profile_audit_mode"] == "contract"

    print("live decision verification passed")


if __name__ == "__main__":
    main()
