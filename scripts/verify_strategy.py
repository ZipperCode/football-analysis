from __future__ import annotations

from football_analysis.service import get_service
from football_analysis.strategy import walk_forward_optimize


def main() -> None:
    service = get_service()
    try:
        result = walk_forward_optimize(
            service.repository,
            league="E0",
            seasons=["2122", "2223", "2324", "2425", "2526"],
            min_train_seasons=2,
            min_test_bets=30,
        )
        assert result.fold_count == 3, "expected three walk-forward folds"
        assert result.settled_bets >= 150, "expected meaningful sample size"
        assert result.positive_folds == result.fold_count, "expected every E0 fold to be positive"
        assert result.roi is not None and result.roi > 0.03, "expected E0 walk-forward ROI above 3%"
        assert result.average_clv is not None and result.average_clv > 0.0, "expected positive CLV"
        high_yield = walk_forward_optimize(
            service.repository,
            league="I1",
            seasons=["2122", "2223", "2324", "2425", "2526"],
            min_train_seasons=2,
            min_test_bets=30,
        )
        assert high_yield.settled_bets >= 150, "expected meaningful high-yield sample size"
        assert high_yield.positive_folds >= 2, "expected at least two positive high-yield folds"
        assert high_yield.roi is not None and high_yield.roi > 0.10, "expected I1 high-yield ROI above 10%"
        assert high_yield.average_clv is not None and high_yield.average_clv > 0.04, "expected positive I1 AH CLV above 4%"
        print("strategy verification passed")
        print(f"E0 robust ROI={result.roi} bets={result.settled_bets} profit={result.profit_units} CLV={result.average_clv}")
        print(
            f"I1 high-yield ROI={high_yield.roi} bets={high_yield.settled_bets} "
            f"profit={high_yield.profit_units} CLV={high_yield.average_clv} "
            f"positive_folds={high_yield.positive_folds}/{high_yield.fold_count}"
        )
    finally:
        service.repository.close()


if __name__ == "__main__":
    main()
