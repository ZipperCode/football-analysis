from __future__ import annotations

from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

from football_analysis.db import StructuredRepository
from football_analysis.models import AgentFinding, EvidenceSource, Match, MarketType, OddsSnapshot
from football_analysis.service import AnalysisService
from football_analysis.settings import LeagueSettings, load_settings
from football_analysis.world_cup import recommend_world_cup
from football_analysis.world_cup import _dedupe_world_cup_matches
from football_analysis.world_cup_parlay import ParlayLeg, _select_distinct_event_legs, recommend_world_cup_parlays


def main() -> None:
    with TemporaryDirectory() as tmpdir:
        repository = StructuredRepository(f"sqlite:///{tmpdir}/verify.db")
        repository.initialize()
        try:
            settings = load_settings()
            settings.leagues = [
                LeagueSettings(
                    code="WORLD_CUP",
                    name="FIFA World Cup",
                    country="World",
                    aliases=["International - FIFA World Cup", "世界杯"],
                    tier="major_tournament",
                    analysis_depth="deep",
                    strategy_mode="live",
                    min_bookmakers=2,
                    paper_only=False,
                )
            ]
            settings.live_trading.max_odds_age_minutes = 180
            service = AnalysisService(settings, repository)
            match_date = _seed_three_match_card(repository)

            result = recommend_world_cup_parlays(service, match_date, stake_units_per_combo=5.0)
            assert result["status"] == "advisory", result
            assert result["advisory_only"] is True
            assert result["total_stake_units"] == 15.0
            assert len(result["combinations"]) == 3
            assert len(result["selected_legs"]) == 3
            assert {combo["stake_units"] for combo in result["combinations"]} == {5.0}
            assert all(len(combo["legs"]) == 2 for combo in result["combinations"])
            assert all(
                len({leg["event_key"] for leg in combo["legs"]}) == 2
                for combo in result["combinations"]
            )
            assert any(
                leg["market_type"] in {"asian_handicap", "over_under"}
                for leg in result["selected_legs"]
            )
            assert any("低赔率腿仅作为串关稳定锚点" in " ".join(leg["reasons"]) for leg in result["selected_legs"])
            assert result["one_miss_tolerance"]["mode"] == "round_robin_3_choose_2"
            assert len(result["one_miss_tolerance"]["scenarios"]) == 3
            assert any(sample["reasons"] == ["stale_odds"] for sample in result["rejected_samples"])
            assert not any("葡萄牙" in leg["match_zh"] for leg in result["selected_legs"])
            portugal_rejection = next(
                sample
                for sample in result["rejected_samples"]
                if sample["match_id"] == "match-portugal" and sample["selection"] == "HOME"
            )
            assert "deep_handicap_qqsd_negative" in portugal_rejection["reasons"]
            assert "qqsd_same_odds_negative" in portugal_rejection["risk_tags"]
            assert "recent_panlu_negative" in portugal_rejection["risk_tags"]
            assert "hot_favorite_stall" in portugal_rejection["risk_tags"]
            assert "key_injury_deep_handicap" in portugal_rejection["risk_tags"]
            selected_labels = {
                f"{leg['match_zh']} {leg['market_label']}"
                for leg in result["selected_legs"]
            }
            assert "法国 vs 加纳 法国 -1.25" in selected_labels
            assert "英格兰 vs 克罗地亚 小 2.5" in selected_labels

            integrated = recommend_world_cup(
                service,
                match_date=match_date,
                stage="advisory",
                include_parlays=True,
                parlay_stake_units=5.0,
                parlay_combo_count=3,
            )
            assert integrated["parlays"]["status"] == "advisory"
            assert integrated["parlays"]["total_stake_units"] == 15.0

            for odds in repository.list_models("odds", OddsSnapshot):
                repository.upsert_model(
                    "odds",
                    odds.id,
                    odds.model_copy(update={"collected_at": odds.collected_at - timedelta(hours=8)}),
                )
            stale_result = recommend_world_cup_parlays(service, match_date, stake_units_per_combo=5.0)
            assert stale_result["status"] == "blocked"
            assert stale_result["candidate_leg_count"] == 0
            assert stale_result["rejected_leg_count"] > 0
            assert any(
                sample["reasons"][0] == "stale_odds"
                for sample in stale_result["rejected_samples"]
            )
            _verify_event_leg_preference()
            _verify_world_cup_team_alias_dedupe(settings)
        finally:
            repository.close()

    print("verify_world_cup_parlay: ok")


def _verify_event_leg_preference() -> None:
    kickoff = datetime.now(timezone.utc) + timedelta(hours=2)
    base = {
        "match_id": "preference-match",
        "event_key": "preference-event",
        "kickoff_at": kickoff,
        "home_team": "Czechia",
        "away_team": "South Africa",
        "home_team_zh": "捷克",
        "away_team_zh": "南非",
        "market_average": 1.9,
        "edge": 0.05,
        "bookmaker": "Book",
        "source": "the_odds_api",
        "bookmaker_count": 5,
        "freshest_age_minutes": 5.0,
        "risk_score": 34.0,
        "reasons": (),
    }
    one_x_two = ParlayLeg(
        id="preference:1x2",
        market_type=MarketType.one_x_two,
        selection="HOME",
        line=None,
        price=1.96,
        confidence=0.67,
        expected_value=0.31,
        score=76.9,
        risk_tags=(),
        **base,
    )
    protected_total = ParlayLeg(
        id="preference:ou",
        market_type=MarketType.over_under,
        selection="UNDER",
        line="2.25",
        price=2.31,
        confidence=0.55,
        expected_value=0.27,
        score=75.9,
        risk_tags=("bookmaker_price_dispersion",),
        **{**base, "market_average": 2.13},
    )
    selected = _select_distinct_event_legs([one_x_two, protected_total], target_count=1)
    assert selected[0].id == "preference:ou", selected[0]


def _verify_world_cup_team_alias_dedupe(settings) -> None:
    kickoff = datetime.now(timezone.utc) + timedelta(hours=3)
    english = Match(
        id="the_odds_api:usa-australia",
        league="FIFA World Cup",
        home_team="USA",
        away_team="Australia",
        kickoff_at=kickoff,
        data_completeness=0.78,
    )
    chinese = Match(
        id="qqsd:usa-australia",
        league="世界杯",
        home_team="美国",
        away_team="澳大利",
        kickoff_at=kickoff.astimezone(settings.app.tzinfo),
        data_completeness=0.90,
    )
    deduped = _dedupe_world_cup_matches([english, chinese], settings)
    assert len(deduped) == 1, deduped


def _seed_three_match_card(repository: StructuredRepository) -> str:
    kickoff = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    matches = [
        ("match-portugal", "Portugal", "Congo DR", "HOME", "asian_handicap", "-1.5", [1.50, 1.52, 1.54]),
        ("match-france", "France", "Ghana", "HOME", "asian_handicap", "-1.25", [1.48, 1.50, 1.52]),
        ("match-england", "England", "Croatia", "UNDER", "over_under", "2.5", [1.83, 1.86, 1.88]),
        ("match-colombia", "Uzbekistan", "Colombia", "AWAY", "asian_handicap", "1.25", [1.82, 1.85, 1.88]),
    ]
    for index, (match_id, home, away, selection, market_type, line, prices) in enumerate(matches):
        match = Match(
            id=match_id,
            league="FIFA World Cup",
            home_team=home,
            away_team=away,
            kickoff_at=kickoff + timedelta(hours=index),
            data_completeness=0.9,
        )
        repository.upsert_model("matches", match.id, match)
        _store_market(repository, match, market_type, selection, line, prices, stale=False)
        _store_counter_market(repository, match, market_type, selection, line)
        _store_qqsd_context(repository, match, selection, negative_deep_favorite=match_id == "match-portugal")

    stale_match = Match(
        id="match-stale",
        league="FIFA World Cup",
        home_team="Ghana",
        away_team="Panama",
        kickoff_at=kickoff + timedelta(hours=5),
        data_completeness=0.9,
    )
    repository.upsert_model("matches", stale_match.id, stale_match)
    _store_market(repository, stale_match, "over_under", "OVER", "2.5", [1.9, 1.92, 1.95], stale=True)
    _store_qqsd_context(repository, stale_match, "OVER")
    return kickoff.astimezone().date().isoformat()


def _store_market(
    repository: StructuredRepository,
    match: Match,
    market_type: str,
    selection: str,
    line: str,
    prices: list[float],
    *,
    stale: bool,
) -> None:
    collected_at = datetime.now(timezone.utc) - (timedelta(hours=5) if stale else timedelta(minutes=12))
    counter = "AWAY" if selection == "HOME" else "HOME"
    if market_type == "over_under":
        counter = "UNDER" if selection == "OVER" else "OVER"
    for index, price in enumerate(prices, start=1):
        repository.upsert_model(
            "odds",
            f"{match.id}:{market_type}:{selection}:{index}",
            OddsSnapshot(
                id=f"{match.id}:{market_type}:{selection}:{index}",
                match_id=match.id,
                market_type=market_type,
                line=line,
                source="qqsd",
                bookmaker=f"Book {index}",
                collected_at=collected_at,
                outcome_odds={selection: price, counter: 1.22},
                market_average={selection: sum(prices) / len(prices), counter: 1.22},
                best_price={selection: max(prices), counter: 1.22},
            ),
        )


def _store_counter_market(
    repository: StructuredRepository,
    match: Match,
    market_type: str,
    selection: str,
    line: str,
) -> None:
    counter = "AWAY" if selection == "HOME" else "HOME"
    if market_type == "over_under":
        counter = "UNDER" if selection == "OVER" else "OVER"
    repository.upsert_model(
        "odds",
        f"{match.id}:{market_type}:{counter}:weak",
        OddsSnapshot(
            id=f"{match.id}:{market_type}:{counter}:weak",
            match_id=match.id,
            market_type=market_type,
            line=line,
            source="qqsd",
            bookmaker="Book weak",
            collected_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            outcome_odds={counter: 1.22},
            market_average={counter: 1.22},
            best_price={counter: 1.22},
        ),
    )


def _store_qqsd_context(
    repository: StructuredRepository,
    match: Match,
    selection: str,
    *,
    negative_deep_favorite: bool = False,
) -> None:
    home_score, away_score = _power_scores(match, selection)
    home_recent = [{"panlu": "赢"}, {"panlu": "赢"}, {"panlu": "走"}, {"panlu": "赢"}, {"panlu": "输"}]
    home_injuries: list[dict[str, object]] = [{"name": "rotation forward"}]
    same_odds_yazhi = {"count": "120", "winrate": "56%"}
    timeline_asian = {
        "current_available": True,
        "history_row_count": 8,
        "first_row": {"handi": "-1.25"},
        "last_row": {"handi": "-1.5" if selection == "HOME" else "1.5"},
    }
    betting_distribution: dict[str, object] = {"tend": {"tradetend": "主胜热度稳定"}}
    if negative_deep_favorite:
        home_recent = [{"panlu": value} for value in ("输", "输", "赢", "输", "输", "输", "赢", "输")]
        home_injuries = [{"name": "鲁本·迪亚斯", "status": "伤病成疑", "role": "主力中卫", "value": "75000000"}]
        same_odds_yazhi = {"count": "139", "winrate": "42%"}
        timeline_asian = {
            "current_available": True,
            "history_row_count": 8,
            "first_row": {"handi": "-1.5"},
            "last_row": {"handi": "-1.5"},
        }
        betting_distribution = {
            "home_rate": "89%",
            "tend": {"tradetend": "主胜热度89%，热门方向偏热"},
        }
    trend = {
        "HOME": "主胜热度稳定",
        "AWAY": "客胜热度稳定",
        "OVER": "大球进球倾向稳定",
        "UNDER": "小球防守倾向稳定",
    }.get(selection, "趋势稳定")
    finding = AgentFinding(
        id=f"{match.id}:qqsd",
        match_id=match.id,
        agent_name="qqsd_full_context",
        summary="QQSD完整数据：盘口、阵容、投注趋势已覆盖。",
        evidence_sources=[
            EvidenceSource(title="QQSD odds", publisher="QQSD"),
            EvidenceSource(title="QQSD lineup", publisher="QQSD"),
        ],
        confidence=0.75,
        payload={
            "provider": "qqsd",
            "fid": match.id,
            "detail": {"hname": match.home_team, "aname": match.away_team},
            "standings": {
                "hpower": {"total_score": str(home_score)},
                "apower": {"total_score": str(away_score)},
                "home_datadetail": home_recent,
                "away_datadetail": [{"panlu": "输"}, {"panlu": "赢"}, {"panlu": "赢"}, {"panlu": "走"}, {"panlu": "赢"}],
            },
            "match_context": {
                "injury_rows": 1,
                "h2h_rows": 4,
                "lineup_full": {
                    "home_shape": "4-3-3",
                    "away_shape": "4-2-3-1",
                    "home_starters": 11,
                    "away_starters": 11,
                },
                "summary": trend,
            },
            "lineup_full": {
                "home": {"shangbing": home_injuries},
                "away": {"shangbing": []},
            },
            "odds_context": {
                "betting_distribution": betting_distribution,
                "same_odds_history": {
                    "spf": {"count": "120", "winrate": "57%"},
                    "yazhi": same_odds_yazhi,
                    "daxiao": {"count": "120", "winrate": "52%"},
                },
                "odds_change_rows": 6,
                "company_count": 24,
            },
            "same_odds_history": {
                "spf": {"count": "120", "winrate": "57%"},
                "yazhi": same_odds_yazhi,
                "daxiao": {"count": "120", "winrate": "52%"},
            },
            "odds_timeline": {
                "markets": {
                    "asian_handicap": timeline_asian,
                    "over_under": {"current_available": True, "history_row_count": 8},
                    "1x2": {"current_available": True, "history_row_count": 8},
                }
            },
        },
    )
    repository.upsert_model("findings", finding.id, finding)


def _power_scores(match: Match, selection: str) -> tuple[int, int]:
    if selection == "HOME":
        return 520, 310
    if selection == "AWAY":
        return 260, 430
    return 360, 355


if __name__ == "__main__":
    main()
