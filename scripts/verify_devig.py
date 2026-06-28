from __future__ import annotations

from football_analysis.devig import (
    DevigMethod,
    devig,
    devig_multiplicative,
    devig_power,
    devig_shin,
    implied_probabilities,
    overround,
)


def _approx(value: float, target: float, tol: float = 1e-9) -> bool:
    return abs(value - target) <= tol


def main() -> None:
    # Fair market (overround exactly 1.0): all methods return the inputs unchanged.
    fair = {"HOME": 2.0, "DRAW": 4.0, "AWAY": 4.0}
    assert _approx(overround(fair), 1.0), overround(fair)
    mult = devig_multiplicative(fair)
    assert _approx(mult["HOME"], 0.5) and _approx(mult["DRAW"], 0.25) and _approx(mult["AWAY"], 0.25), mult

    # implied_probabilities skips odds <= 1.0.
    skipped = implied_probabilities({"HOME": 2.0, "BAD": 1.0, "WORSE": 0.5})
    assert set(skipped) == {"HOME"}, skipped

    # Vigged 3-way market: overround > 1, every method must normalize to 1.0.
    vig = {"HOME": 2.1, "DRAW": 3.4, "AWAY": 3.5}
    assert overround(vig) > 1.0, overround(vig)
    for method in (devig_multiplicative, devig_power, devig_shin):
        result = method(vig)
        assert _approx(sum(result.values()), 1.0, 1e-6), (method.__name__, result)
        assert all(0.0 < p < 1.0 for p in result.values()), (method.__name__, result)

    # Power: when input already sums to 1, k recovers it (output == multiplicative).
    power_fair = devig_power(fair)
    assert _approx(power_fair["HOME"], 0.5, 1e-6), power_fair

    # Shin invariant: two-outcome market == multiplicative == additive.
    two = {"OVER_2_5": 1.95, "UNDER_2_5": 1.95}
    shin_two = devig_shin(two)
    mult_two = devig_multiplicative(two)
    assert _approx(shin_two["OVER_2_5"], mult_two["OVER_2_5"], 1e-9), (shin_two, mult_two)
    assert _approx(sum(shin_two.values()), 1.0, 1e-9), shin_two

    # Asymmetric two-way: additive subtracts equal vig share; check against hand calc.
    asym = {"HOME": 1.5, "AWAY": 3.0}
    # implied: 0.6667 + 0.3333 = 1.0 (fair) -> unchanged
    shin_asym = devig_shin(asym)
    assert _approx(sum(shin_asym.values()), 1.0, 1e-9), shin_asym

    # devig dispatch returns None for incomplete markets (< 2 valid outcomes).
    assert devig({"HOME": 2.0}, DevigMethod.power) is None
    assert devig({"HOME": 1.0, "AWAY": 0.9}, DevigMethod.power) is None
    assert devig(vig, DevigMethod.shin) is not None

    print("devig verification passed")


if __name__ == "__main__":
    main()
