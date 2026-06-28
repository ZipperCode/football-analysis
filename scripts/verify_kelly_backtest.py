from football_analysis.bankroll import build_kelly_bankroll_report
from football_analysis.service import get_service


def main() -> None:
    service = get_service()
    report = build_kelly_bankroll_report(
        service.repository,
        league="I1",
        family="asian-away",
        quick=True,
        initial_bankroll_units=10000,
        target_cagr=0.20,
        max_allowed_drawdown_pct=0.20,
    )
    assert report.league == "I1"
    assert report.family == "asian-away"
    assert report.settled_bets >= 180, "expected meaningful long-horizon sample"
    assert report.holdout_settled_bets >= 80, "expected meaningful holdout sample"
    assert report.holdout_roi is not None and report.holdout_roi >= 0.08
    assert report.max_drawdown_pct is not None
    assert report.cagr is not None
    assert report.season_curve, "expected bankroll season curve"
    assert report.season_curve[0]["season"] is not None
    assert report.target_cagr == 0.20
    assert report.max_allowed_drawdown_pct == 0.20
    print(
        "kelly backtest verification passed: "
        f"cagr={report.cagr} max_drawdown={report.max_drawdown_pct} "
        f"target_passed={report.target_passed}"
    )


if __name__ == "__main__":
    main()
