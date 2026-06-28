from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import combinations
from math import prod
from statistics import mean, pstdev
from typing import Any

from football_analysis.models import AgentFinding, Match, MarketType, OddsSnapshot, Recommendation
from football_analysis.service import AnalysisService
from football_analysis.world_cup import _dedupe_world_cup_matches, _world_cup_event_key, _world_cup_local_kickoff

WORLD_CUP_PARLAY_PROFILE_ID = "world_cup_ah_ou_parlay_advisory"
DEFAULT_STAKE_UNITS_PER_COMBO = 5.0
DEFAULT_COMBO_COUNT = 3
DEFAULT_LEGS_PER_COMBO = 2
MIN_LEG_PRICE = 1.28
MAX_LEG_PRICE = 2.45
LOW_PRICE_THRESHOLD = 1.55
NEGATIVE_QQSD_TAGS = {
    "qqsd_same_odds_negative",
    "recent_panlu_negative",
    "hot_favorite_stall",
    "key_injury_deep_handicap",
}
COMPARABLE_AH_OU_SCORE_GAP = 4.0
COMPARABLE_AH_OU_EV_GAP = 0.06
CONFLICT_ALT_SCORE_GAP = 6.0
CONFLICT_ALT_EV_GAP = 0.08


COUNTRY_ZH: dict[str, str] = {
    "Argentina": "阿根廷",
    "Australia": "澳大利亚",
    "Austria": "奥地利",
    "Belgium": "比利时",
    "Brazil": "巴西",
    "Cameroon": "喀麦隆",
    "Canada": "加拿大",
    "Chile": "智利",
    "China": "中国",
    "Colombia": "哥伦比亚",
    "Czech Republic": "捷克",
    "Czechia": "捷克",
    "Congo DR": "刚果(金)",
    "Croatia": "克罗地亚",
    "Denmark": "丹麦",
    "Ecuador": "厄瓜多尔",
    "England": "英格兰",
    "France": "法国",
    "Germany": "德国",
    "Ghana": "加纳",
    "Iran": "伊朗",
    "Italy": "意大利",
    "Japan": "日本",
    "Mexico": "墨西哥",
    "Morocco": "摩洛哥",
    "Netherlands": "荷兰",
    "Nigeria": "尼日利亚",
    "Panama": "巴拿马",
    "Poland": "波兰",
    "Portugal": "葡萄牙",
    "Qatar": "卡塔尔",
    "Saudi Arabia": "沙特阿拉伯",
    "Senegal": "塞内加尔",
    "South Africa": "南非",
    "Serbia": "塞尔维亚",
    "South Korea": "韩国",
    "Spain": "西班牙",
    "Switzerland": "瑞士",
    "Bosnia & Herzegovina": "波黑",
    "Bosnia and Herzegovina": "波黑",
    "Tunisia": "突尼斯",
    "United States": "美国",
    "Uruguay": "乌拉圭",
    "Uzbekistan": "乌兹别克斯坦",
    "Wales": "威尔士",
}


@dataclass(frozen=True)
class ParlayLeg:
    id: str
    match_id: str
    event_key: str
    kickoff_at: datetime
    home_team: str
    away_team: str
    home_team_zh: str
    away_team_zh: str
    market_type: MarketType
    selection: str
    line: str | None
    price: float
    market_average: float
    edge: float
    bookmaker: str
    source: str
    bookmaker_count: int
    freshest_age_minutes: float | None
    confidence: float
    expected_value: float
    score: float
    risk_score: float
    risk_tags: tuple[str, ...]
    reasons: tuple[str, ...]

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "match_id": self.match_id,
            "event_key": self.event_key,
            "kickoff_at": self.kickoff_at.isoformat(),
            "home_team": self.home_team,
            "away_team": self.away_team,
            "home_team_zh": self.home_team_zh,
            "away_team_zh": self.away_team_zh,
            "match_zh": f"{self.home_team_zh} vs {self.away_team_zh}",
            "market_type": self.market_type.value,
            "market_label": _market_label(self),
            "selection": self.selection,
            "line": self.line,
            "price": round(self.price, 3),
            "market_average": round(self.market_average, 3),
            "edge": round(self.edge, 4),
            "bookmaker": self.bookmaker,
            "source": self.source,
            "bookmaker_count": self.bookmaker_count,
            "freshest_age_minutes": (
                None if self.freshest_age_minutes is None else round(self.freshest_age_minutes, 1)
            ),
            "confidence": round(self.confidence, 4),
            "expected_value": round(self.expected_value, 4),
            "score": round(self.score, 3),
            "risk_score": round(self.risk_score, 3),
            "risk_tags": list(self.risk_tags),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ParlayCombination:
    id: str
    legs: tuple[ParlayLeg, ...]
    stake_units: float
    combined_odds: float
    confidence: float
    expected_value: float
    expected_profit_units: float
    max_return_units: float
    risk_tags: tuple[str, ...]
    reason: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "stake_units": round(self.stake_units, 3),
            "combined_odds": round(self.combined_odds, 3),
            "confidence": round(self.confidence, 4),
            "expected_value": round(self.expected_value, 4),
            "expected_profit_units": round(self.expected_profit_units, 3),
            "max_return_units": round(self.max_return_units, 3),
            "risk_tags": list(self.risk_tags),
            "reason": self.reason,
            "legs": [leg.model_dump() for leg in self.legs],
        }


def recommend_world_cup_parlays(
    service: AnalysisService,
    match_date: str,
    *,
    stake_units_per_combo: float = DEFAULT_STAKE_UNITS_PER_COMBO,
    combo_count: int = DEFAULT_COMBO_COUNT,
    legs_per_combo: int = DEFAULT_LEGS_PER_COMBO,
    max_odds_age_minutes: int | None = None,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    """Build advisory World Cup 2-leg parlays from fresh AH/OU/1x2 evidence."""
    now = checked_at or datetime.now(service.settings.app.tzinfo)
    if now.tzinfo is None:
        now = now.replace(tzinfo=service.settings.app.tzinfo)
    else:
        now = now.astimezone(service.settings.app.tzinfo)
    max_age = max_odds_age_minutes or service.settings.live_trading.max_odds_age_minutes
    matches = _world_cup_matches_for_date(service, match_date, checked_at=now)
    related_match_ids = _related_match_ids_by_selected_event(service, matches)
    odds_by_match = _odds_by_match(service)
    findings_by_match = _findings_by_match(service)
    recommendations_by_match = _recommendations_by_match(service)

    issues: list[str] = []
    rejected: list[dict[str, Any]] = []
    raw_legs: list[ParlayLeg] = []
    for match in matches:
        match_ids = related_match_ids.get(match.id, {match.id})
        legs, leg_rejections = _match_legs(
            match,
            [
                snapshot
                for match_id in match_ids
                for snapshot in odds_by_match.get(match_id, [])
            ],
            [
                finding
                for match_id in match_ids
                for finding in findings_by_match.get(match_id, [])
            ],
            [
                recommendation
                for match_id in match_ids
                for recommendation in recommendations_by_match.get(match_id, [])
            ],
            settings=service.settings,
            now=now,
            max_age_minutes=max_age,
        )
        raw_legs.extend(legs)
        rejected.extend(leg_rejections)

    selected_legs = _select_distinct_event_legs(
        raw_legs,
        target_count=max(legs_per_combo + 1, _minimum_leg_count(combo_count, legs_per_combo)),
    )
    combinations_ = _build_combinations(
        selected_legs,
        stake_units_per_combo=stake_units_per_combo,
        combo_count=combo_count,
        legs_per_combo=legs_per_combo,
    )

    if not matches:
        issues.append(f"no_world_cup_fixtures:{match_date}")
    if len(selected_legs) < legs_per_combo:
        issues.append(f"world_cup_parlay_insufficient_legs:{len(selected_legs)}/{legs_per_combo}")
    if len(combinations_) < combo_count:
        issues.append(f"world_cup_parlay_combo_count:{len(combinations_)}/{combo_count}")
    if raw_legs and not any(leg.market_type in {MarketType.asian_handicap, MarketType.over_under} for leg in selected_legs):
        issues.append("world_cup_parlay_no_ah_ou_leg")
    if combinations_ and max(item.expected_value for item in combinations_) < 0.0:
        issues.append("world_cup_parlay_no_positive_ev_combo")

    total_stake = sum(item.stake_units for item in combinations_)
    one_miss = _one_miss_tolerance(combinations_, selected_legs, total_stake)
    status = (
        "ready"
        if combinations_
        and not any(issue.startswith("world_cup_parlay_insufficient") for issue in issues)
        and "world_cup_parlay_no_positive_ev_combo" not in issues
        else "blocked"
    )
    if status == "ready":
        status = "advisory"

    return {
        "profile_id": WORLD_CUP_PARLAY_PROFILE_ID,
        "checked_at": now.isoformat(),
        "date": match_date,
        "status": status,
        "advisory_only": True,
        "stake_units_per_combo": round(stake_units_per_combo, 3),
        "requested_combo_count": combo_count,
        "legs_per_combo": legs_per_combo,
        "total_stake_units": round(total_stake, 3),
        "selected_leg_count": len(selected_legs),
        "candidate_leg_count": len(raw_legs),
        "rejected_leg_count": len(rejected),
        "selected_legs": [leg.model_dump() for leg in selected_legs],
        "combinations": [item.model_dump() for item in combinations_],
        "one_miss_tolerance": one_miss,
        "issues": _dedupe(issues),
        "rejected_samples": rejected[:8],
        "risk_notice": service.settings.app.risk_notice,
    }


def _match_legs(
    match: Match,
    odds: list[OddsSnapshot],
    findings: list[AgentFinding],
    recommendations: list[Recommendation],
    *,
    settings: Any,
    now: datetime,
    max_age_minutes: int,
) -> tuple[list[ParlayLeg], list[dict[str, Any]]]:
    groups: dict[tuple[MarketType, str | None, str], list[tuple[OddsSnapshot, float]]] = defaultdict(list)
    rejected: list[dict[str, Any]] = []
    for snapshot in odds:
        if snapshot.market_type not in {MarketType.one_x_two, MarketType.asian_handicap, MarketType.over_under}:
            continue
        prices = snapshot.best_price or snapshot.outcome_odds
        for selection, price in prices.items():
            normalized_selection = selection.strip().upper()
            if normalized_selection not in _market_selections(snapshot.market_type):
                continue
            if price <= 1.0:
                continue
            groups[(snapshot.market_type, snapshot.line, normalized_selection)].append((snapshot, float(price)))

    context = _qqsd_context_payload(findings)
    recommendation_by_key = {
        (
            recommendation.market_type,
            recommendation.odds_basis.get("line"),
            (recommendation.selection or "").upper(),
        ): recommendation
        for recommendation in recommendations
        if recommendation.market_type is not None and recommendation.selection
    }
    home_zh, away_zh = _team_names_zh(match, context)
    legs: list[ParlayLeg] = []
    for (market_type, line, selection), entries in groups.items():
        prices = [price for _, price in entries]
        best_snapshot, best_price = max(entries, key=lambda item: item[1])
        freshest = max(
            (snapshot.collected_at for snapshot, _ in entries),
            key=_as_utc,
            default=None,
        )
        age = _age_minutes(freshest, now) if freshest is not None else None
        bookmaker_count = len({snapshot.bookmaker for snapshot, _ in entries})
        has_qqsd_quote = any(snapshot.source == "qqsd" for snapshot, _ in entries)
        average = mean(prices)
        edge = (best_price / average - 1.0) if average > 1.0 else 0.0
        rejection_reasons: list[str] = []
        if age is None or age > max_age_minutes:
            rejection_reasons.append("stale_odds")
        if best_price < MIN_LEG_PRICE:
            rejection_reasons.append("price_too_low")
        if best_price > MAX_LEG_PRICE:
            rejection_reasons.append("price_too_high")
        if rejection_reasons:
            rejected.append(
                {
                    "match_id": match.id,
                    "match_zh": f"{home_zh} vs {away_zh}",
                    "market_type": market_type.value,
                    "selection": selection,
                    "line": line,
                    "price": round(best_price, 3),
                    "reasons": rejection_reasons,
                }
            )
            continue

        recommendation = recommendation_by_key.get((market_type, line, selection))
        confidence, expected_value, score, risk_score, tags, reasons = _score_leg(
            match=match,
            market_type=market_type,
            selection=selection,
            line=line,
            price=best_price,
            average=average,
            edge=edge,
            bookmaker_count=bookmaker_count,
            price_stdev=pstdev(prices) if len(prices) > 1 else 0.0,
            context=context,
            recommendation=recommendation,
            has_qqsd=has_qqsd_quote,
        )
        if best_price < LOW_PRICE_THRESHOLD and confidence < 0.64:
            rejected.append(
                {
                    "match_id": match.id,
                    "match_zh": f"{home_zh} vs {away_zh}",
                    "market_type": market_type.value,
                    "selection": selection,
                    "line": line,
                    "price": round(best_price, 3),
                    "confidence": round(confidence, 4),
                    "reasons": ["low_price_without_success_buffer"],
                }
            )
            continue
        deep_rejections = _deep_handicap_rejections(
            market_type=market_type,
            line=line,
            edge=edge,
            bookmaker_count=bookmaker_count,
            tags=tags,
        )
        if deep_rejections:
            rejected.append(
                {
                    "match_id": match.id,
                    "match_zh": f"{home_zh} vs {away_zh}",
                    "market_type": market_type.value,
                    "selection": selection,
                    "line": line,
                    "price": round(best_price, 3),
                    "edge": round(edge, 4),
                    "bookmaker_count": bookmaker_count,
                    "risk_tags": sorted(set(tags)),
                    "reasons": deep_rejections,
                }
            )
            continue
        leg = ParlayLeg(
            id=f"{match.id}:{market_type.value}:{selection}:{line or 'main'}",
            match_id=match.id,
            event_key=_event_key(match, settings),
            kickoff_at=match.kickoff_at,
            home_team=match.home_team,
            away_team=match.away_team,
            home_team_zh=home_zh,
            away_team_zh=away_zh,
            market_type=market_type,
            selection=selection,
            line=line,
            price=best_price,
            market_average=average,
            edge=edge,
            bookmaker=best_snapshot.bookmaker,
            source=best_snapshot.source,
            bookmaker_count=bookmaker_count,
            freshest_age_minutes=age,
            confidence=confidence,
            expected_value=expected_value,
            score=score,
            risk_score=risk_score,
            risk_tags=tuple(sorted(set(tags))),
            reasons=tuple(reasons),
        )
        legs.append(leg)
    return legs, rejected


def _score_leg(
    *,
    match: Match,
    market_type: MarketType,
    selection: str,
    line: str | None,
    price: float,
    average: float,
    edge: float,
    bookmaker_count: int,
    price_stdev: float,
    context: dict[str, Any],
    recommendation: Recommendation | None,
    has_qqsd: bool,
) -> tuple[float, float, float, float, list[str], list[str]]:
    implied = 1.0 / price
    confidence = implied + 0.035 + min(0.035, max(0.0, edge) * 0.7)
    score = 48.0 + min(14.0, max(0.0, edge) * 180.0)
    risk_score = 34.0
    tags: list[str] = []
    reasons: list[str] = []

    if has_qqsd:
        confidence += 0.018
        score += 3.0
        reasons.append("QQSD盘口为主数据源")
    else:
        tags.append("non_qqsd_price_source")

    if bookmaker_count >= 3:
        confidence += 0.012
        score += 2.0
        reasons.append(f"{bookmaker_count}家公司有报价")
    elif bookmaker_count == 1:
        risk_score += 7.0
        tags.append("single_bookmaker_price")

    if price < LOW_PRICE_THRESHOLD:
        confidence += 0.035
        score -= 2.0
        tags.append("low_odds_parlay_anchor")
        reasons.append("低赔率腿仅作为串关稳定锚点")

    if price_stdev >= 0.08:
        risk_score += 5.0
        tags.append("bookmaker_price_dispersion")

    qqsd_boost, qqsd_risk, qqsd_reasons, qqsd_tags = _qqsd_selection_signal(
        match=match,
        market_type=market_type,
        selection=selection,
        line=line,
        context=context,
    )
    confidence += qqsd_boost
    score += qqsd_boost * 120.0
    risk_score += qqsd_risk
    reasons.extend(qqsd_reasons)
    tags.extend(qqsd_tags)
    if qqsd_boost < -0.01:
        confidence -= 0.035
        score -= 9.0
        risk_score += 8.0
        tags.append("qqsd_direction_conflict")

    if recommendation is not None:
        confidence += min(0.035, max(0.0, recommendation.confidence - 0.55) * 0.25)
        score += min(6.0, max(0.0, recommendation.value_score - 60.0) * 0.25)
        risk_score += max(-4.0, min(7.0, (recommendation.risk_score - 42.0) * 0.12))
        reasons.append("单场评分与该方向一致")

    if market_type is MarketType.asian_handicap:
        line_value = _line_value(line)
        if line_value is not None and abs(line_value) >= 1.5:
            risk_score += 4.0
            tags.append("wide_handicap")
    if market_type is MarketType.over_under and line:
        reasons.append(f"大小球盘口 {line}")

    confidence = _clamp(confidence, 0.42, 0.78)
    expected_value = confidence * price - 1.0
    score += expected_value * 28.0 - max(0.0, risk_score - 42.0) * 0.35
    return (
        round(confidence, 5),
        round(expected_value, 5),
        round(_clamp(score, 0.0, 100.0), 4),
        round(_clamp(risk_score, 0.0, 100.0), 4),
        tags,
        _dedupe(reasons)[:5],
    )


def _qqsd_selection_signal(
    *,
    match: Match,
    market_type: MarketType,
    selection: str,
    line: str | None,
    context: dict[str, Any],
) -> tuple[float, float, list[str], list[str]]:
    if not context:
        return 0.0, 4.0, [], ["qqsd_context_missing"]
    reasons: list[str] = []
    tags: list[str] = []
    boost = 0.0
    risk = 0.0

    match_context = context.get("match_context") if isinstance(context.get("match_context"), dict) else {}
    odds_context = context.get("odds_context") if isinstance(context.get("odds_context"), dict) else {}
    standings = context.get("standings") if isinstance(context.get("standings"), dict) else {}
    lineup_full = context.get("lineup_full") if isinstance(context.get("lineup_full"), dict) else {}
    timeline = context.get("odds_timeline") if isinstance(context.get("odds_timeline"), dict) else {}
    markets = timeline.get("markets") if isinstance(timeline.get("markets"), dict) else {}
    timeline_market = markets.get(market_type.value) if isinstance(markets, dict) else None
    if isinstance(timeline_market, dict) and timeline_market.get("current_available"):
        boost += 0.014
        reasons.append("QQSD当前盘口可用")
    if isinstance(timeline_market, dict) and int(timeline_market.get("history_row_count") or 0) >= 6:
        boost += 0.01
        reasons.append("QQSD盘口时间线有历史深度")

    lineup_quality = _lineup_quality(match_context)
    if lineup_quality >= 2:
        boost += 0.012
        reasons.append("阵容/伤停证据覆盖")
    else:
        risk += 3.0
        tags.append("lineup_not_confirmed")

    side_boost, side_reason = _side_power_signal(match, selection, context)
    if market_type in {MarketType.one_x_two, MarketType.asian_handicap}:
        boost += side_boost
        if side_reason:
            reasons.append(side_reason)
        if side_boost < 0:
            risk += 5.0
            tags.append("power_rating_conflict")

    trend_text = _nested_text(odds_context.get("betting_distribution"))
    if not trend_text:
        trend_text = _nested_text(context.get("betting_distribution"))
    if trend_text:
        if _selection_text_aligned(selection, trend_text):
            boost += 0.01
            reasons.append("投注分布与方向一致")
        if any(token in trend_text for token in ("过热", "偏热", "异常", "分歧")):
            risk += 3.0
            tags.append("betting_heat_watch")

    same_odds_row = _same_odds_row(context, market_type)
    same_odds_sample = _safe_int(same_odds_row.get("count") or same_odds_row.get("rows")) if same_odds_row else 0
    same_odds_winrate = _safe_rate(same_odds_row.get("winrate") or same_odds_row.get("rate")) if same_odds_row else None
    if same_odds_sample >= 60 and same_odds_winrate is not None:
        market_name = {"1x2": "胜平负", "asian_handicap": "亚盘", "over_under": "大小球"}.get(
            market_type.value,
            market_type.value,
        )
        reasons.append(f"QQSD同赔{market_name}{same_odds_sample}场/胜率{same_odds_winrate:.0%}")
        if same_odds_winrate < 0.50:
            boost -= 0.03
            risk += 8.0
            tags.append("qqsd_same_odds_negative")
        elif same_odds_winrate >= 0.54:
            boost += 0.012

    if market_type is MarketType.asian_handicap and selection in {"HOME", "AWAY"}:
        panlu_rate = _recent_panlu_loss_rate(standings, selection)
        if panlu_rate is not None and panlu_rate > 0.60:
            boost -= 0.022
            risk += 7.0
            tags.append("recent_panlu_negative")
            reasons.append(f"近期盘路输盘率{panlu_rate:.0%}")

        heat = _selection_heat(context, selection)
        if heat is not None and heat >= 0.80 and _favorite_handicap_stalled(timeline_market, selection):
            boost -= 0.018
            risk += 8.0
            tags.append("hot_favorite_stall")
            reasons.append("大热方向盘口未同步升盘")

        line_value = _line_value(line)
        if (
            line_value is not None
            and abs(line_value) >= 1.25
            and _has_key_injury(lineup_full, selection, match_context)
        ):
            boost -= 0.02
            risk += 8.0
            tags.append("key_injury_deep_handicap")
            reasons.append("深让盘热门方存在关键伤停")

    if market_type is MarketType.over_under:
        total_hint = _total_hint(match_context, odds_context)
        if total_hint and total_hint == selection:
            boost += 0.012
            reasons.append("进球倾向与大小球方向一致")
        elif total_hint and total_hint != selection:
            boost -= 0.012
            risk += 5.0
            tags.append("total_hint_conflict")

    if market_type is MarketType.asian_handicap and line:
        line_value = _line_value(line)
        if line_value is not None and abs(line_value) <= 1.25:
            boost += 0.006

    return boost, risk, reasons, tags


def _deep_handicap_rejections(
    *,
    market_type: MarketType,
    line: str | None,
    edge: float,
    bookmaker_count: int,
    tags: list[str],
) -> list[str]:
    if market_type is not MarketType.asian_handicap:
        return []
    line_value = _line_value(line)
    if line_value is None or abs(line_value) < 1.5:
        return []
    reasons: list[str] = []
    if edge < 0.03:
        reasons.append("deep_handicap_edge_too_low")
    if bookmaker_count < 3:
        reasons.append("deep_handicap_bookmaker_count_low")
    if "key_injury_deep_handicap" in tags:
        reasons.append("deep_handicap_key_injury")
    if any(tag in NEGATIVE_QQSD_TAGS for tag in tags):
        reasons.append("deep_handicap_qqsd_negative")
    return _dedupe(reasons)


def _select_distinct_event_legs(legs: list[ParlayLeg], *, target_count: int) -> list[ParlayLeg]:
    by_event: dict[str, list[ParlayLeg]] = defaultdict(list)
    for leg in legs:
        by_event[leg.event_key].append(leg)
    preferred = [_preferred_event_leg(event_legs) for event_legs in by_event.values()]
    return sorted(preferred, key=_leg_selection_key, reverse=True)[:target_count]


def _preferred_event_leg(legs: list[ParlayLeg]) -> ParlayLeg:
    ranked = sorted(legs, key=_leg_selection_key, reverse=True)
    best = ranked[0]
    if "power_rating_conflict" in best.risk_tags:
        alternative = next(
            (
                leg
                for leg in ranked
                if "power_rating_conflict" not in leg.risk_tags
                and leg.score >= best.score - CONFLICT_ALT_SCORE_GAP
                and leg.expected_value >= best.expected_value - CONFLICT_ALT_EV_GAP
            ),
            None,
        )
        if alternative is not None:
            best = alternative
    if best.market_type is MarketType.one_x_two:
        alternative = next(
            (
                leg
                for leg in ranked
                if leg.market_type in {MarketType.asian_handicap, MarketType.over_under}
                and "power_rating_conflict" not in leg.risk_tags
                and leg.score >= best.score - COMPARABLE_AH_OU_SCORE_GAP
                and leg.expected_value >= best.expected_value - COMPARABLE_AH_OU_EV_GAP
                and leg.confidence >= 0.50
            ),
            None,
        )
        if alternative is not None:
            best = alternative
    return best


def _leg_selection_key(leg: ParlayLeg) -> tuple[float, float, float, int]:
    market_priority = {
        MarketType.asian_handicap: 3,
        MarketType.over_under: 2,
        MarketType.one_x_two: 1,
    }
    return (
        leg.score,
        leg.expected_value,
        leg.confidence,
        market_priority.get(leg.market_type, 0),
    )


def _build_combinations(
    legs: list[ParlayLeg],
    *,
    stake_units_per_combo: float,
    combo_count: int,
    legs_per_combo: int,
) -> list[ParlayCombination]:
    if legs_per_combo < 2:
        return []
    combos: list[ParlayCombination] = []
    for index, combo_legs in enumerate(combinations(legs, legs_per_combo), start=1):
        if len({leg.event_key for leg in combo_legs}) != legs_per_combo:
            continue
        combined_odds = prod(leg.price for leg in combo_legs)
        correlation_penalty = 0.97 if len({leg.market_type for leg in combo_legs}) < legs_per_combo else 1.0
        confidence = prod(leg.confidence for leg in combo_legs) * correlation_penalty
        expected_value = confidence * combined_odds - 1.0
        risk_tags = sorted({tag for leg in combo_legs for tag in leg.risk_tags})
        if any(leg.price < LOW_PRICE_THRESHOLD for leg in combo_legs):
            risk_tags.append("contains_low_odds_anchor")
        reason = " × ".join(_short_leg_label(leg) for leg in combo_legs)
        combos.append(
            ParlayCombination(
                id=f"world-cup-parlay-{index}",
                legs=tuple(combo_legs),
                stake_units=stake_units_per_combo,
                combined_odds=combined_odds,
                confidence=confidence,
                expected_value=expected_value,
                expected_profit_units=stake_units_per_combo * expected_value,
                max_return_units=stake_units_per_combo * combined_odds,
                risk_tags=tuple(_dedupe(risk_tags)),
                reason=reason,
            )
        )
    return sorted(
        combos,
        key=lambda item: (item.expected_value, item.confidence, item.combined_odds),
        reverse=True,
    )[:combo_count]


def _one_miss_tolerance(
    combos: list[ParlayCombination],
    selected_legs: list[ParlayLeg],
    total_stake_units: float,
) -> dict[str, Any]:
    scenarios: list[dict[str, Any]] = []
    for failed_leg in selected_legs:
        winning = [combo for combo in combos if failed_leg.id not in {leg.id for leg in combo.legs}]
        return_units = sum(combo.max_return_units for combo in winning)
        scenarios.append(
            {
                "failed_leg_id": failed_leg.id,
                "failed_leg": _short_leg_label(failed_leg),
                "winning_combo_count": len(winning),
                "return_units": round(return_units, 3),
                "net_units": round(return_units - total_stake_units, 3),
            }
        )
    worst = min((item["net_units"] for item in scenarios), default=None)
    return {
        "mode": "round_robin_3_choose_2" if len(selected_legs) >= 3 and len(combos) >= 3 else "partial",
        "total_stake_units": round(total_stake_units, 3),
        "worst_one_miss_net_units": worst,
        "scenarios": scenarios,
    }


def _world_cup_matches_for_date(
    service: AnalysisService,
    match_date: str,
    *,
    checked_at: datetime | None = None,
) -> list[Match]:
    start = datetime.combine(
        datetime.strptime(match_date, "%Y-%m-%d").date(),
        datetime.min.time(),
        tzinfo=service.settings.app.tzinfo,
    )
    end = start + timedelta(hours=30)
    now = checked_at or datetime.now(service.settings.app.tzinfo)
    if now.tzinfo is None:
        now = now.replace(tzinfo=service.settings.app.tzinfo)
    else:
        now = now.astimezone(service.settings.app.tzinfo)
    lower_bound = max(start, now)
    candidates: list[Match] = []
    for match in service.repository.list_models("matches", Match):
        if not _is_world_cup_match(match, service):
            continue
        kickoff = _world_cup_local_kickoff(match, service.settings)
        if lower_bound <= kickoff <= end:
            candidates.append(match)
    return sorted(
        _dedupe_world_cup_matches(candidates, service.settings),
        key=lambda item: _world_cup_local_kickoff(item, service.settings),
    )


def _related_match_ids_by_selected_event(
    service: AnalysisService,
    selected_matches: list[Match],
) -> dict[str, set[str]]:
    selected_by_event = {
        _world_cup_event_key(match, service.settings): match.id
        for match in selected_matches
    }
    related: dict[str, set[str]] = {
        match.id: {match.id}
        for match in selected_matches
    }
    if not selected_by_event:
        return related
    for match in service.repository.list_models("matches", Match):
        if not _is_world_cup_match(match, service):
            continue
        selected_id = selected_by_event.get(_world_cup_event_key(match, service.settings))
        if selected_id is not None:
            related.setdefault(selected_id, {selected_id}).add(match.id)
    return related


def _is_world_cup_match(match: Match, service: AnalysisService) -> bool:
    league = match.league.strip().lower()
    for item in service.settings.leagues:
        if item.code.upper() != "WORLD_CUP":
            continue
        aliases = {str(value).strip().lower() for value in [item.code, item.name, *(item.aliases or [])] if value}
        if league in aliases:
            return True
    return "world cup" in league or "世界杯" in league


def _odds_by_match(service: AnalysisService) -> dict[str, list[OddsSnapshot]]:
    grouped: dict[str, list[OddsSnapshot]] = defaultdict(list)
    for snapshot in service.repository.list_models("odds", OddsSnapshot):
        grouped[snapshot.match_id].append(snapshot)
    return grouped


def _findings_by_match(service: AnalysisService) -> dict[str, list[AgentFinding]]:
    grouped: dict[str, list[AgentFinding]] = defaultdict(list)
    for finding in service.repository.list_models("findings", AgentFinding):
        grouped[finding.match_id].append(finding)
    return grouped


def _recommendations_by_match(service: AnalysisService) -> dict[str, list[Recommendation]]:
    grouped: dict[str, list[Recommendation]] = defaultdict(list)
    for recommendation in service.repository.list_models("recommendations", Recommendation):
        grouped[recommendation.match_id].append(recommendation)
    return grouped


def _market_selections(market_type: MarketType) -> set[str]:
    if market_type is MarketType.one_x_two:
        return {"HOME", "DRAW", "AWAY"}
    if market_type is MarketType.asian_handicap:
        return {"HOME", "AWAY"}
    if market_type is MarketType.over_under:
        return {"OVER", "UNDER"}
    return set()


def _qqsd_context_payload(findings: list[AgentFinding]) -> dict[str, Any]:
    for finding in findings:
        if finding.agent_name == "qqsd_full_context" and isinstance(finding.payload, dict):
            return finding.payload
    return {}


def _team_names_zh(match: Match, context: dict[str, Any]) -> tuple[str, str]:
    detail = context.get("detail") if isinstance(context.get("detail"), dict) else {}
    home = str(detail.get("hname") or detail.get("home") or match.home_team).strip()
    away = str(detail.get("aname") or detail.get("away") or match.away_team).strip()
    return _team_zh(home), _team_zh(away)


def _team_zh(name: str) -> str:
    if _contains_cjk(name):
        return name
    normalized = " ".join(name.replace("-", " ").split())
    return COUNTRY_ZH.get(name) or COUNTRY_ZH.get(normalized) or name


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _event_key(match: Match, settings: Any) -> str:
    local_time = _world_cup_local_kickoff(match, settings).astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    teams = sorted([_team_key(match.home_team), _team_key(match.away_team)])
    return f"{local_time}:{teams[0]}:{teams[1]}"


def _team_key(value: str) -> str:
    normalized = value.lower().strip()
    replacements = {
        "捷克": "czechia",
        "南非": "south africa",
        "瑞士": "switzerland",
        "波黑": "bosnia and herzegovina",
        "加拿大": "canada",
        "卡塔尔": "qatar",
        "czech republic": "czechia",
        "bosnia & herzegovina": "bosnia and herzegovina",
        "korea republic": "south korea",
        "turkiye": "turkey",
        "curaçao": "curacao",
    }
    normalized = replacements.get(normalized, normalized)
    return "".join(char.lower() for char in normalized if char.isalnum())


def _age_minutes(collected_at: datetime, now: datetime) -> float:
    return max(0.0, (now - _as_utc(collected_at).astimezone(now.tzinfo)).total_seconds() / 60.0)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _lineup_quality(match_context: dict[str, Any]) -> int:
    lineup = match_context.get("lineup_full") if isinstance(match_context.get("lineup_full"), dict) else {}
    if int(lineup.get("home_starters") or 0) >= 11 and int(lineup.get("away_starters") or 0) >= 11:
        return 3
    if lineup.get("home_shape") and lineup.get("away_shape"):
        return 2
    if int(match_context.get("injury_rows") or 0) or int(match_context.get("h2h_rows") or 0):
        return 1
    return 0


def _side_power_signal(match: Match, selection: str, context: dict[str, Any]) -> tuple[float, str | None]:
    standings = context.get("standings") if isinstance(context.get("standings"), dict) else {}
    home_power = _power_score(standings.get("hpower"))
    away_power = _power_score(standings.get("apower"))
    if home_power is None or away_power is None:
        return 0.0, None
    diff = home_power - away_power
    if selection == "HOME":
        aligned = diff >= 15
        strong = match.home_team
    elif selection == "AWAY":
        aligned = diff <= -15
        strong = match.away_team
    else:
        aligned = abs(diff) < 8
        strong = "平衡盘"
    if aligned:
        return min(0.025, abs(diff) / 400.0), f"实力评分支持{_team_zh(strong)}方向"
    if abs(diff) >= 35 and selection in {"HOME", "AWAY"}:
        return -0.018, "实力评分与选择方向冲突"
    return 0.0, None


def _power_score(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    for key in ("total_score", "score", "rating", "power", "rank_score"):
        value = _safe_float(payload.get(key))
        if value is not None:
            return value
    return None


def _total_hint(match_context: dict[str, Any], odds_context: dict[str, Any]) -> str | None:
    text = _nested_text(match_context) + " " + _nested_text(odds_context)
    if any(token in text for token in ("大球", "进球偏多", "火力", "OVER", "over")):
        return "OVER"
    if any(token in text for token in ("小球", "进球偏少", "防守", "UNDER", "under")):
        return "UNDER"
    return None


def _nested_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_nested_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_nested_text(item) for item in value)
    return str(value)


def _selection_text_aligned(selection: str, text: str) -> bool:
    upper = selection.upper()
    if upper == "HOME":
        return any(token in text for token in ("主", "胜", "home", "HOME"))
    if upper == "AWAY":
        return any(token in text for token in ("客", "负", "away", "AWAY"))
    if upper == "DRAW":
        return any(token in text for token in ("平", "draw", "DRAW"))
    if upper == "OVER":
        return any(token in text for token in ("大", "over", "OVER"))
    if upper == "UNDER":
        return any(token in text for token in ("小", "under", "UNDER"))
    return False


def _same_odds_row(context: dict[str, Any], market_type: MarketType) -> dict[str, Any]:
    market_key = {
        MarketType.one_x_two: "spf",
        MarketType.asian_handicap: "yazhi",
        MarketType.over_under: "daxiao",
    }.get(market_type)
    if not market_key:
        return {}
    candidates: list[Any] = []
    odds_context = context.get("odds_context") if isinstance(context.get("odds_context"), dict) else {}
    candidates.append(odds_context.get("same_odds_history"))
    candidates.append(context.get("same_odds_history"))
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get(market_key), dict):
            return candidate[market_key]
    return {}


def _recent_panlu_loss_rate(standings: dict[str, Any], selection: str) -> float | None:
    key = "home_datadetail" if selection == "HOME" else "away_datadetail"
    rows = standings.get(key)
    if not isinstance(rows, list):
        return None
    signals = []
    for row in rows[:10]:
        if not isinstance(row, dict):
            continue
        panlu = str(row.get("panlu") or row.get("panlu_result") or "").strip()
        if not panlu:
            continue
        if "输" in panlu or panlu.upper() in {"L", "LOSE", "LOST"}:
            signals.append(False)
        elif "赢" in panlu or panlu.upper() in {"W", "WIN", "WON"}:
            signals.append(True)
        elif "走" in panlu or panlu.upper() in {"P", "PUSH", "VOID"}:
            continue
    if len(signals) < 5:
        return None
    losses = len([item for item in signals if item is False])
    return losses / len(signals)


def _selection_heat(context: dict[str, Any], selection: str) -> float | None:
    texts = [
        context.get("vote_infos"),
        context.get("betting_distribution"),
        context.get("odds_heat"),
    ]
    odds_context = context.get("odds_context") if isinstance(context.get("odds_context"), dict) else {}
    texts.extend([odds_context.get("betting_distribution"), odds_context.get("heat")])
    key_candidates = {
        "HOME": ("home", "win", "胜", "主", "主胜", "homerate", "winrate"),
        "DRAW": ("draw", "平", "平局", "drawrate"),
        "AWAY": ("away", "lost", "lose", "负", "客", "客胜", "awayrate", "lostrate"),
        "OVER": ("over", "big", "大", "大球", "bigrate"),
        "UNDER": ("under", "small", "小", "小球", "smallrate"),
    }.get(selection, ())
    values: list[float] = []
    for source in texts:
        _collect_heat_values(source, key_candidates, values)
    if values:
        return max(values)
    return None


def _collect_heat_values(value: Any, keys: tuple[str, ...], result: list[float]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).strip().lower()
            if any(candidate.lower() in key_text for candidate in keys):
                rate = _safe_rate(item)
                if rate is not None:
                    result.append(rate)
            if isinstance(item, (dict, list)):
                _collect_heat_values(item, keys, result)
        return
    if isinstance(value, list):
        for item in value:
            _collect_heat_values(item, keys, result)


def _favorite_handicap_stalled(timeline_market: Any, selection: str) -> bool:
    if not isinstance(timeline_market, dict):
        return False
    first_row = timeline_market.get("first_row")
    last_row = timeline_market.get("last_row")
    first_line = _timeline_line_value(first_row)
    last_line = _timeline_line_value(last_row)
    if first_line is None or last_line is None:
        history_count = _safe_int(timeline_market.get("history_row_count"))
        return history_count >= 6
    if selection == "HOME":
        return last_line >= first_line
    if selection == "AWAY":
        return last_line <= first_line
    return False


def _timeline_line_value(row: Any) -> float | None:
    if not isinstance(row, dict):
        return None
    for key in ("line", "handi", "handicap", "pan", "goal", "goalline"):
        value = _line_value(str(row.get(key))) if row.get(key) is not None else None
        if value is not None:
            return value
    return None


def _has_key_injury(lineup_full: dict[str, Any], selection: str, match_context: dict[str, Any]) -> bool:
    side_keys = ("home", "hteam", "h", "home_shangbing") if selection == "HOME" else ("away", "ateam", "a", "away_shangbing")
    candidates: list[Any] = [lineup_full]
    nested = lineup_full.get("data") if isinstance(lineup_full.get("data"), dict) else None
    if nested is not None:
        candidates.append(nested)
    candidates.append(match_context.get("lineup_full"))
    for candidate in candidates:
        for row in _injury_rows_for_side(candidate, side_keys):
            if _injury_row_is_key(row):
                return True
    return False


def _injury_rows_for_side(value: Any, side_keys: tuple[str, ...]) -> list[Any]:
    if not isinstance(value, dict):
        return []
    rows: list[Any] = []
    shangbing = value.get("shangbing")
    if isinstance(shangbing, dict):
        for key in side_keys:
            item = shangbing.get(key)
            if isinstance(item, list):
                rows.extend(item)
            elif isinstance(item, dict):
                rows.append(item)
    elif isinstance(shangbing, list):
        rows.extend(shangbing)
    for key in side_keys:
        item = value.get(key)
        if isinstance(item, dict):
            rows.extend(_injury_rows_for_side(item, side_keys))
            nested = item.get("shangbing")
            if isinstance(nested, list):
                rows.extend(nested)
        elif isinstance(item, list):
            rows.extend(item)
    return rows


def _injury_row_is_key(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    text = _nested_text(row)
    value = max(
        (
            _safe_float(row.get(key)) or 0.0
            for key in ("value", "worth", "market_value", "身价")
        ),
        default=0.0,
    )
    if value >= 5_000_000:
        return True
    return any(token in text for token in ("主力", "核心", "队长", "首发", "关键", "伤病成疑", "缺阵"))


def _market_label(leg: ParlayLeg) -> str:
    if leg.market_type is MarketType.one_x_two:
        return {"HOME": "主胜", "DRAW": "平局", "AWAY": "客胜"}.get(leg.selection, leg.selection)
    if leg.market_type is MarketType.over_under:
        prefix = "大" if leg.selection == "OVER" else "小"
        return f"{prefix} {leg.line or ''}".strip()
    if leg.market_type is MarketType.asian_handicap:
        team = leg.home_team_zh if leg.selection == "HOME" else leg.away_team_zh
        line = _display_handicap_line(leg.selection, leg.line)
        return f"{team} {line}".strip()
    return leg.selection


def _display_handicap_line(selection: str, line: str | None) -> str:
    if not line:
        return ""
    line_value = _line_value(line)
    if line_value is None:
        return line
    display_value = line_value if selection == "HOME" else -line_value
    return _format_line(display_value)


def _format_line(value: float) -> str:
    if value > 0:
        return f"+{value:g}"
    return f"{value:g}"


def _line_value(line: str | None) -> float | None:
    if not line:
        return None
    normalized = line.strip().replace("/", ",")
    raw_parts = [part.strip() for part in normalized.split(",") if part.strip()]
    if len(raw_parts) > 1 and raw_parts[0].startswith("-"):
        raw_parts = [
            part if part.startswith(("-", "+")) else f"-{part}"
            for part in raw_parts
        ]
    parts = [_safe_float(part) for part in raw_parts]
    values = [part for part in parts if part is not None]
    if not values:
        return _safe_float(normalized)
    return mean(values)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except ValueError:
        return None


def _safe_int(value: Any) -> int:
    number = _safe_float(value)
    if number is None:
        return 0
    return int(number)


def _safe_rate(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    number = _safe_float(text)
    if number is None:
        return None
    if "%" in text or number > 1:
        return number / 100.0
    return number


def _short_leg_label(leg: ParlayLeg) -> str:
    return f"{leg.home_team_zh} vs {leg.away_team_zh} {leg.model_dump()['market_label']} @{leg.price:.2f}"


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _minimum_leg_count(combo_count: int, legs_per_combo: int) -> int:
    for count in range(legs_per_combo, 12):
        possible = 0
        for _ in combinations(range(count), legs_per_combo):
            possible += 1
        if possible >= combo_count:
            return count
    return max(legs_per_combo, combo_count)
