from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from football_analysis.db import StructuredRepository
from football_analysis.models import RecommendationStatus, StrategySnapshot
from football_analysis.strategy_health import review_strategy_health


def main() -> None:
    with TemporaryDirectory() as tmp:
        repository = StructuredRepository(f"sqlite:///{Path(tmp) / 'strategy-health.db'}")
        repository.initialize()
        try:
            for index in range(5):
                repository.upsert_model(
                    "strategy_snapshots",
                    f"bad-clv-{index}",
                    _snapshot("bad-clv", index=index, clv=-0.02, profit=-1.0, probability=0.52, result="loss"),
                )
            for index in range(3):
                repository.upsert_model(
                    "strategy_snapshots",
                    f"bad-brier-{index}",
                    _snapshot("bad-brier", index=index, clv=0.02, profit=-1.0, probability=0.90, result="loss"),
                )
            report = review_strategy_health(repository, clv_window=5)
            by_name = {item.strategy_name: item for item in report.items}
            assert by_name["bad-clv"].status == "failed"
            assert by_name["bad-clv"].action == "retire_or_rebuild"
            assert any(issue.startswith("clv_disappeared") for issue in by_name["bad-clv"].issues)
            assert by_name["bad-brier"].status == "watch"
            assert by_name["bad-brier"].action == "calibration_review"
        finally:
            repository.close()

    print("strategy health verification passed")


def _snapshot(
    strategy_name: str,
    *,
    index: int,
    clv: float,
    profit: float,
    probability: float,
    result: str,
) -> StrategySnapshot:
    return StrategySnapshot(
        id=f"{strategy_name}-{index}",
        recommendation_id=f"rec-{strategy_name}-{index}",
        match_id=f"match-{strategy_name}-{index}",
        strategy_name=strategy_name,
        decision_time=datetime(2026, 1, 1, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")) + timedelta(days=index),
        recommendation_status=RecommendationStatus.recommended,
        model_prediction={"calibrated_probability": probability},
        market_odds={"selected": {"best_price": 2.0}},
        expected_value=0.01,
        clv=clv,
        settlement_result=result,
        profit_units=profit,
        stake_units=1.0,
        reasoning="verification snapshot",
    )


if __name__ == "__main__":
    main()
