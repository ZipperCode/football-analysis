from __future__ import annotations

from football_analysis.devig import DevigMethod
from football_analysis.edge import best_soft_price, find_sharp_anchor, group_snapshots
from football_analysis.models import MarketType, OddsSnapshot


def _snapshot(bookmaker: str, odds: dict[str, float], *, line: str | None = None) -> OddsSnapshot:
    return OddsSnapshot(
        id=f"{bookmaker}-{line or 'na'}",
        match_id="m1",
        market_type=MarketType.one_x_two,
        line=line,
        source=f"src-{bookmaker.lower()}",
        bookmaker=bookmaker,
        outcome_odds=dict(odds),
    )


def main() -> None:
    pinnacle = _snapshot("Pinnacle Sports", {"HOME": 2.05, "DRAW": 3.5, "AWAY": 3.7})
    soft = _snapshot("BetSoft", {"HOME": 2.20, "DRAW": 3.3, "AWAY": 3.6})
    market_avg = _snapshot("Market average", {"HOME": 2.10, "DRAW": 3.4, "AWAY": 3.65})

    # find_sharp_anchor picks Pinnacle regardless of order, ignores synthetic book.
    for group in ([pinnacle, soft, market_avg], [market_avg, soft, pinnacle]):
        fair = find_sharp_anchor(group, ["Pinnacle", "Betfair Exchange"], DevigMethod.power)
        assert fair is not None, "expected Pinnacle anchor"
        assert fair.anchor_bookmaker == "Pinnacle Sports", fair.anchor_bookmaker
        assert abs(sum(fair.fair_probability.values()) - 1.0) < 1e-6, fair.fair_probability

    # Priority order respected: Betfair beats Pinnacle when listed first.
    betfair = _snapshot("Betfair Exchange", {"HOME": 2.0, "DRAW": 3.6, "AWAY": 3.8})
    fair = find_sharp_anchor([pinnacle, betfair, soft], ["Betfair", "Pinnacle"], DevigMethod.power)
    assert fair is not None and fair.anchor_bookmaker == "Betfair Exchange", fair.anchor_bookmaker

    # No sharp book present -> None.
    assert find_sharp_anchor([soft, market_avg], ["Pinnacle"], DevigMethod.power) is None

    # best_soft_price excludes anchor and synthetic, returns the max soft price.
    soft2 = _snapshot("BetOther", {"HOME": 2.25, "DRAW": 3.2, "AWAY": 3.5})
    price = best_soft_price([pinnacle, soft, soft2, market_avg], "HOME", "Pinnacle Sports")
    assert price == 2.25, price

    # EV-edge math: fair_prob 0.50 + best soft price 2.10 -> edge == 0.05 exactly.
    fair_prob = 0.50
    soft_price = 2.10
    edge = soft_price * fair_prob - 1.0
    assert abs(edge - 0.05) < 1e-12, edge

    # group_snapshots keys by (market_type, line).
    ah_a = _snapshot("Pinnacle", {"HOME": 1.9, "AWAY": 1.95}, line="-0.5")
    ah_b = _snapshot("Pinnacle", {"HOME": 1.85, "AWAY": 2.0}, line="-1.0")
    grouped = group_snapshots([pinnacle, ah_a, ah_b])
    assert ("1x2", None) in grouped, grouped.keys()
    assert ("1x2", "-0.5") in grouped, grouped.keys()
    assert ("1x2", "-1.0") in grouped, grouped.keys()

    print("edge verification passed")


if __name__ == "__main__":
    main()
