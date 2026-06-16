from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from football_analysis.db import StructuredRepository
from football_analysis.models import BetLog
from football_analysis.simulation import simulate_execution_queue
from football_analysis.settings import load_settings


def main() -> None:
    queue = {
        "status": "ready",
        "ready_to_execute": True,
        "items": [
            {
                "match_id": "sim-1",
                "market_type": "1x2",
                "selection": "HOME",
                "odds": 2.10,
                "minimum_execution_odds": 2.05,
                "remaining_stake_units": 0.5,
            },
            {
                "match_id": "sim-2",
                "market_type": "1x2",
                "selection": "AWAY",
                "odds": 1.80,
                "minimum_execution_odds": 1.79,
                "remaining_stake_units": 0.25,
            },
        ],
    }
    report = simulate_execution_queue(queue, odds_slippage=0.01)
    assert report.real_execution_allowed is False
    assert report.mode == "simulation_only"
    assert report.simulated_count == 2
    assert report.accepted_count == 1
    assert report.rejected_count == 1
    assert report.items[1].reason == "simulated_reject_below_minimum_odds"

    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'simulation.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            assert repository.list_models("bets", BetLog) == [], "simulation must not write real or paper bets"
        finally:
            repository.close()

    print("simulated execution verification passed")


if __name__ == "__main__":
    main()
