from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from football_analysis.db import StructuredRepository
from football_analysis.models import BetLog, MarketType
from football_analysis.service import AnalysisService
from football_analysis.settings import load_settings


def main() -> None:
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'paper-bankroll.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            for index in range(3):
                repository.upsert_model(
                    "bets",
                    f"paper-win-{index}",
                    _bet(f"paper-win-{index}", profit=0.8, closing_odds=1.85, index=index),
                )
            qualified = service.paper_bankroll(
                "paper-profile",
                initial_units=100.0,
                min_promotion_bets=3,
                min_positive_clv_rate=0.60,
                min_roi=0.10,
            )
            assert qualified.status == "qualified"
            assert qualified.action == "promote_to_simulated_live"
            assert qualified.current_units == 102.4
            assert qualified.positive_clv_rate == 1.0

            for index in range(4):
                repository.upsert_model(
                    "bets",
                    f"paper-loss-{index}",
                    _bet(
                        f"paper-loss-{index}",
                        profile_id="failed-profile",
                        profit=-1.0,
                        closing_odds=2.2,
                        index=index,
                    ),
                )
            failed = service.paper_bankroll(
                "failed-profile",
                early_stop_bets=4,
                stop_roi=-0.05,
                stop_positive_clv_rate=0.40,
            )
            assert failed.status == "failed"
            assert failed.action == "stop_strategy"
            assert "paper_stop_threshold" in failed.issues
            assert failed.consecutive_losses == 4
        finally:
            repository.close()

    print("paper bankroll verification passed")


def _bet(
    bet_id: str,
    *,
    profile_id: str = "paper-profile",
    profit: float,
    closing_odds: float,
    index: int,
) -> BetLog:
    return BetLog(
        id=bet_id,
        match_id=f"{profile_id}-{index}",
        market_type=MarketType.one_x_two,
        selection="HOME",
        odds=2.0,
        stake_units=1.0,
        platform="paper",
        placed_at=datetime(2026, 1, 1, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")) + timedelta(days=index),
        notes=f"profile_id={profile_id}",
        closing_odds=closing_odds,
        result="win" if profit > 0 else "loss",
        profit_units=profit,
    )


if __name__ == "__main__":
    main()
