from __future__ import annotations

from datetime import datetime
from itertools import combinations
from zoneinfo import ZoneInfo

from football_analysis.cli import get_service
from football_analysis.models import MarketType
from football_analysis.world_cup_parlay import (
    recommend_world_cup_parlays,
    _build_combinations,
    _findings_by_match,
    _match_legs,
    _odds_by_match,
    _recommendations_by_match,
    _related_match_ids_by_selected_event,
    _select_distinct_event_legs,
    _short_leg_label,
    _world_cup_matches_for_date,
)


MATCH_RESULTS: dict[str, tuple[int, int]] = {
    "czechia:southafrica": (1, 1),
    "switzerland:bosniaandherzegovina": (4, 1),
    "canada:qatar": (6, 0),
}


def main() -> None:
    service = get_service()
    checked_at = datetime.fromisoformat("2026-06-18T22:10:11+08:00")
    report = recommend_world_cup_parlays(
        service,
        "2026-06-18",
        stake_units_per_combo=5.0,
        checked_at=checked_at,
    )
    print("checked_at", report["checked_at"])
    print("status", report["status"], "legs", report["selected_leg_count"], "combos", len(report["combinations"]))
    _print_report("tuned", report)

    original = _original_score_selected_report(service, checked_at)
    _print_report("original_score_order", original)


def _original_score_selected_report(service, checked_at: datetime) -> dict[str, object]:
    matches = _world_cup_matches_for_date(service, "2026-06-18", checked_at=checked_at)
    related_match_ids = _related_match_ids_by_selected_event(service, matches)
    odds_by_match = _odds_by_match(service)
    findings_by_match = _findings_by_match(service)
    recommendations_by_match = _recommendations_by_match(service)
    raw_legs = []
    for match in matches:
        match_ids = related_match_ids.get(match.id, {match.id})
        legs, _ = _match_legs(
            match,
            [snapshot for match_id in match_ids for snapshot in odds_by_match.get(match_id, [])],
            [finding for match_id in match_ids for finding in findings_by_match.get(match_id, [])],
            [
                recommendation
                for match_id in match_ids
                for recommendation in recommendations_by_match.get(match_id, [])
            ],
            settings=service.settings,
            now=checked_at,
            max_age_minutes=service.settings.live_trading.max_odds_age_minutes,
        )
        raw_legs.extend(legs)
    original_legs = _select_by_original_score(raw_legs, target_count=3)
    combos = _build_combinations(
        original_legs,
        stake_units_per_combo=5.0,
        combo_count=3,
        legs_per_combo=2,
    )
    return {
        "selected_legs": [leg.model_dump() for leg in original_legs],
        "combinations": [combo.model_dump() for combo in combos],
    }


def _select_by_original_score(legs, *, target_count: int):
    selected = []
    seen_events = set()
    market_priority = {
        MarketType.asian_handicap: 0,
        MarketType.over_under: 1,
        MarketType.one_x_two: 2,
    }
    for leg in sorted(
        legs,
        key=lambda item: (
            item.score,
            item.expected_value,
            item.confidence,
            -market_priority.get(item.market_type, 9),
        ),
        reverse=True,
    ):
        if leg.event_key in seen_events:
            continue
        selected.append(leg)
        seen_events.add(leg.event_key)
        if len(selected) >= target_count:
            break
    return selected


def _print_report(label: str, report: dict[str, object]) -> None:
    print(f"\n== {label} ==")
    leg_returns = {}
    for index, leg in enumerate(report["selected_legs"], start=1):
        return_multiplier = _settle_leg_return(leg)
        leg_returns[leg["id"]] = return_multiplier
        status = _return_status(return_multiplier)
        print(
            "LEG",
            index,
            leg["match_zh"],
            leg["market_label"],
            f"@{leg['price']}",
            status,
            "return",
            round(return_multiplier, 3),
            "score",
            leg["score"],
            "risk",
            ",".join(leg["risk_tags"]) or "-",
        )
    total_profit = 0.0
    for index, combo in enumerate(report["combinations"], start=1):
        combo_return = 1.0
        for leg in combo["legs"]:
            combo_return *= leg_returns[leg["id"]]
        profit = combo["stake_units"] * combo_return - combo["stake_units"]
        total_profit += profit
        print(
            "COMBO",
            index,
            _return_status(combo_return),
            "return",
            round(combo_return, 3),
            "profit",
            round(profit, 3),
            combo["reason"],
        )
    print("TOTAL_PROFIT", round(total_profit, 3))


def _settle_leg_return(leg: dict[str, object]) -> float:
    result = MATCH_RESULTS[_result_key(str(leg["match_zh"]))]
    home_score, away_score = result
    market_type = str(leg["market_type"])
    selection = str(leg["selection"])
    line = leg.get("line")
    price = float(leg["price"])
    if market_type == "1x2":
        if selection == "HOME":
            return price if home_score > away_score else 0.0
        if selection == "AWAY":
            return price if away_score > home_score else 0.0
        return price if home_score == away_score else 0.0
    if market_type == "over_under":
        total = home_score + away_score
        margin = total - _line_value(str(line))
        if selection == "UNDER":
            margin = -margin
        return _asian_return_multiplier(margin, price)
    if market_type == "asian_handicap":
        line_value = _line_value(str(line))
        adjusted = home_score + line_value - away_score
        if selection == "HOME":
            margin = adjusted
        else:
            margin = -adjusted
        return _asian_return_multiplier(margin, price)
    raise ValueError(f"unsupported market: {market_type}")


def _result_key(match_zh: str) -> str:
    mapping = {
        "捷克 vs 南非": "czechia:southafrica",
        "瑞士 vs 波黑": "switzerland:bosniaandherzegovina",
        "加拿大 vs 卡塔尔": "canada:qatar",
    }
    return mapping[match_zh]


def _line_value(value: str) -> float:
    if "/" not in value:
        return float(value)
    parts = [float(part) for part in value.split("/")]
    if value.startswith("-"):
        parts = [part if part < 0 else -part for part in parts]
    return sum(parts) / len(parts)


def _asian_return_multiplier(margin: float, price: float) -> float:
    rounded = round(margin * 4) / 4
    if abs(rounded) < 1e-9:
        return 1.0
    if rounded > 0:
        if abs(rounded % 0.5) < 1e-9:
            return price
        if rounded > 0:
            return (price + 1.0) / 2.0
    if rounded < 0:
        if abs(rounded % 0.5) < 1e-9:
            return 0.0
        return 0.5
    return 0.0


def _return_status(return_multiplier: float) -> str:
    if return_multiplier <= 0:
        return "LOSS"
    if return_multiplier < 1:
        return "HALF_LOSS"
    if abs(return_multiplier - 1.0) < 1e-9:
        return "PUSH"
    if return_multiplier < 1.5:
        return "HALF_WIN"
    return "WIN"


if __name__ == "__main__":
    main()
