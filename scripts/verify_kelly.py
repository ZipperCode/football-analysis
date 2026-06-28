from football_analysis.kelly import KellySettings, calculate_kelly_stake, kelly_fraction_for_display
from football_analysis.settings import load_settings


def main() -> None:
    settings = KellySettings(fraction=0.25, min_edge=0.025, max_stake_fraction=0.005)

    stake = calculate_kelly_stake(edge=0.02, odds=2.5, bankroll_units=10000, settings=settings)
    assert stake == 0.0, f"expected no stake below min edge, got {stake}"

    stake = calculate_kelly_stake(edge=0.025, odds=2.0, bankroll_units=10000, settings=settings)
    assert stake == 50.0, f"expected bankroll cap at 50u, got {stake}"

    capped = calculate_kelly_stake(edge=0.50, odds=2.0, bankroll_units=10000, settings=settings)
    assert capped == 50.0, f"expected bankroll cap at 50u, got {capped}"

    capped_by_profile = calculate_kelly_stake(
        edge=0.08,
        odds=2.0,
        bankroll_units=10000,
        settings=settings,
        stake_cap_units=0.4,
    )
    assert capped_by_profile == 0.4, f"expected profile cap at 0.4u, got {capped_by_profile}"

    full_fraction = kelly_fraction_for_display(edge=0.05, odds=2.0)
    assert full_fraction == 0.05, f"expected full Kelly fraction 0.05, got {full_fraction}"

    loaded = load_settings()
    assert loaded.bankroll.initial_units == 10000
    assert loaded.kelly.fraction == 0.25
    assert loaded.portfolio.max_daily_exposure_fraction == 0.00012

    print("kelly verification passed")


if __name__ == "__main__":
    main()
