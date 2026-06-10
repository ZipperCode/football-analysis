from __future__ import annotations

from football_analysis.service import get_service
from football_analysis.strategy import optimize_strategy


def main() -> None:
    service = get_service()
    try:
        for league in ["E0", "SP1", "D1", "I1", "F1"]:
            result = optimize_strategy(
                service.repository,
                league,
                ["2122", "2223", "2324", "2425"],
                ["2526"],
                min_test_bets=40,
            )
            print(
                league,
                f"selected_by={result.selected_by}",
                f"bets={result.bets}",
                f"roi={result.roi}",
                f"profit={result.profit_units}",
                f"clv={result.average_clv}",
                f"params={result.params}",
            )
    finally:
        service.repository.close()


if __name__ == "__main__":
    main()
