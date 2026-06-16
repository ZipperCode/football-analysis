from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from football_analysis.db import StructuredRepository
from football_analysis.ingestion import IngestionService
from football_analysis.datasources.qqsd import (
    QQSD_ASIAN_HANDICAP_ENDPOINT,
    QQSD_DEEP_CONTEXT_ENDPOINT_IDS,
    QQSD_EUROPE_ODDS_ENDPOINT,
    QQSD_HANDICAP_TOTALS_ODDS_HISTORY_ENDPOINT,
    QQSD_HANDICAP_EUROPE_ODDS_ENDPOINT,
    QQSD_LIVE_ODDS_ENDPOINT_IDS,
    QQSD_ODDS_SUMMARY_ENDPOINT,
    QQSD_OVER_UNDER_ENDPOINT,
    build_context_finding,
    compute_ckey,
    find_league_entry,
    map_archive_score_matches,
    map_archive_score_odds,
    map_archive_score_historical_rows,
    map_finished_match_asian_historical_row,
    map_company_asian_odds,
    map_company_europe_odds,
    map_company_europe_odds_xml,
    map_company_summary_odds,
    map_company_over_under_odds,
    map_handicap_totals_odds_history_rows,
    map_match_detail_match,
    map_score_list_matches,
    map_score_list_odds,
    qqsd_current_odds_available,
    qqsd_endpoint_catalog,
    qqsd_history_availability,
    qqsd_odds_timeline_capabilities,
)
from football_analysis.models import AgentFinding, Match, MatchStatus, OddsSnapshot


def main() -> None:
    assert compute_ckey(30005) == "be47b6a7d0a216abc2df718580654d07"
    assert compute_ckey(30009) == "5630e99611ed65a15a8aac57d69b5f12"
    assert compute_ckey(40004) == "e9f20384e1d4506f0a7d79e95afecd2f"
    assert compute_ckey(40005) == "a0f3329a0dc2e459c04de66ed341968a"
    assert compute_ckey(40018) == "933048f2075007997de5666c5540a849"
    assert compute_ckey(40020) == "efff0a84f860ff38fe8f5abfa0a68496"
    assert compute_ckey(40022) == "1b86560ccd58b069ce4a21e872d4ab94"
    assert compute_ckey(40029) == "180cb5ec5433cefa0e604df978699a4b"
    assert compute_ckey(40038) == "3b780c66fe8928785469a976d7d9b22d"
    assert compute_ckey(40046) == "46f4e303da44acf341f3b2bd012acc52"
    assert compute_ckey(41000) == "c28d797bdf3f789e759150cdac45957a"
    assert compute_ckey(41004) == "1a54d0351a921dbbdf4d986d10e6074c"
    assert compute_ckey(50012) == "71fa21bd6c2ff00d9a3925abcad8c4d0"
    assert compute_ckey(50017) == "8ecd4ec4f7b98c8b0c56eb0d605868f2"
    assert compute_ckey(91002) == "56a32ccafeb41af4409e4c37b080c787"
    catalog = qqsd_endpoint_catalog()
    assert catalog[QQSD_EUROPE_ODDS_ENDPOINT]["market_type"] == "1x2"
    assert catalog[QQSD_ASIAN_HANDICAP_ENDPOINT]["market_type"] == "asian_handicap"
    assert catalog[QQSD_OVER_UNDER_ENDPOINT]["market_type"] == "over_under"
    assert catalog[QQSD_ASIAN_HANDICAP_ENDPOINT]["timeline_status"] == "supported"
    assert catalog[QQSD_OVER_UNDER_ENDPOINT]["timeline_status"] == "supported"
    assert catalog["40018"]["timeline_status"] == "supported"
    assert catalog[QQSD_HANDICAP_TOTALS_ODDS_HISTORY_ENDPOINT]["market_type"] == "unknown_auxiliary"
    assert catalog[QQSD_HANDICAP_TOTALS_ODDS_HISTORY_ENDPOINT]["timeline_status"] == "not_primary_timeline"
    assert catalog[QQSD_HANDICAP_TOTALS_ODDS_HISTORY_ENDPOINT]["execution_role"] == "context_only"
    assert catalog[QQSD_ODDS_SUMMARY_ENDPOINT]["market_type"] == "mixed"
    assert catalog[QQSD_HANDICAP_EUROPE_ODDS_ENDPOINT]["market_type"] == "handicap_1x2"
    assert catalog[QQSD_HANDICAP_EUROPE_ODDS_ENDPOINT]["execution_role"] == "context_only"
    assert set(QQSD_LIVE_ODDS_ENDPOINT_IDS) == {"40004", "40005", "40020"}
    assert set(QQSD_LIVE_ODDS_ENDPOINT_IDS).isdisjoint(QQSD_DEEP_CONTEXT_ENDPOINT_IDS)
    timeline_capabilities = qqsd_odds_timeline_capabilities()
    assert timeline_capabilities["1x2"]["status"] == "supported"
    assert timeline_capabilities["1x2"]["timeline_endpoint"] == "40018"
    assert timeline_capabilities["asian_handicap"]["status"] == "supported"
    assert timeline_capabilities["asian_handicap"]["timeline_endpoint"] == QQSD_ASIAN_HANDICAP_ENDPOINT
    assert timeline_capabilities["asian_handicap"]["company_id_param"] == "cid"
    assert timeline_capabilities["asian_handicap"]["market_param_value"] == "2"
    assert timeline_capabilities["over_under"]["status"] == "supported"
    assert timeline_capabilities["over_under"]["timeline_endpoint"] == QQSD_OVER_UNDER_ENDPOINT
    assert timeline_capabilities["over_under"]["company_id_param"] == "cid"
    assert timeline_capabilities["over_under"]["market_param_value"] == "3"

    payload = {
        "code": 100,
        "data": {
            "list": [
                {
                    "fid": "1282494",
                    "lid": "5",
                    "hid": "434",
                    "aid": "841",
                    "lname": "芬超",
                    "vsdate": "2026-06-13 22:00:00",
                    "hname": "瓦萨",
                    "aname": "库奥皮奥",
                    "hscore": "1",
                    "ascore": "1",
                    "status": "3",
                    "w": "0.98",
                    "p": "0.25",
                    "l": "0.83",
                    "pshow": "-0/0.5",
                }
            ]
        },
    }
    matches = map_score_list_matches(payload)
    assert len(matches) == 1
    assert matches[0].id == "qqsd:1282494"
    assert matches[0].league == "芬超"
    assert matches[0].status is MatchStatus.finished
    assert matches[0].home_score == 1
    assert matches[0].away_score == 1

    odds = map_score_list_odds(payload)
    assert len(odds) == 1
    assert odds[0].match_id == "qqsd:1282494"
    assert odds[0].market_type.value == "asian_handicap"
    assert odds[0].line == "-0/0.5"
    assert odds[0].outcome_odds == {"HOME": 1.98, "AWAY": 1.83}

    local_asian_row = map_finished_match_asian_historical_row(
        matches[0],
        odds,
        league="FIN_VEIKKAUSLIIGA",
        season="2026",
    )
    assert local_asian_row is not None
    assert local_asian_row.id == "qqsd-local:FIN_VEIKKAUSLIIGA:2026:1282494:ah"
    assert local_asian_row.ah_line == -0.25
    assert local_asian_row.ah_home_odds == 1.98
    assert local_asian_row.ah_away_odds == 1.83
    assert local_asian_row.closing_ah_home_odds == 1.98

    archive_payload = {
        "code": 100,
        "data": [
            {
                "fixtureid": "1282494",
                "fixture": {
                    "fixtureid": "1282494",
                    "matchid": "5",
                    "hometeamid": "434",
                    "awayteamid": "841",
                    "simplegbname": "芬超",
                    "homesxname": "瓦萨",
                    "awaysxname": "库奥皮奥",
                    "homestanding": "6",
                    "awaystanding": "3",
                    "vsdate": "2026-06-13 22:00:00",
                    "hscore": "1",
                    "ascore": "1",
                },
                "odds": ["3.26", "2.85", "2.12"],
                "result": "1",
                "jczq_expect": "2026-06-13",
            }
        ],
    }
    archive_matches = map_archive_score_matches(archive_payload)
    assert len(archive_matches) == 1
    assert archive_matches[0].id == "qqsd:1282494"
    assert archive_matches[0].league == "芬超"
    assert archive_matches[0].home_team == "瓦萨"
    assert archive_matches[0].away_team == "库奥皮奥"
    assert archive_matches[0].status is MatchStatus.finished
    assert archive_matches[0].external_ids["qqsd_fixtureid"] == "1282494"

    archive_odds = map_archive_score_odds(archive_payload)
    assert len(archive_odds) == 1
    assert archive_odds[0].market_type.value == "1x2"
    assert archive_odds[0].outcome_odds == {"HOME": 3.26, "DRAW": 2.85, "AWAY": 2.12}

    historical_rows = map_archive_score_historical_rows(
        archive_payload,
        league="FIN_VEIKKAUSLIIGA",
        season="2026",
        league_aliases={"芬超"},
    )
    assert len(historical_rows) == 1
    row = historical_rows[0]
    assert row.id == "qqsd:FIN_VEIKKAUSLIIGA:2026:1282494"
    assert row.league == "FIN_VEIKKAUSLIIGA"
    assert row.home_team == "瓦萨"
    assert row.away_team == "库奥皮奥"
    assert row.home_goals == 1
    assert row.away_goals == 1
    assert row.home_odds == 3.26
    assert row.draw_odds == 2.85
    assert row.away_odds == 2.12
    assert row.closing_away_odds == 2.12

    company_payload = {
        "code": 100,
        "data": {
            "handicapline": "1",
            "list": [
                {
                    "id": "3",
                    "name": "Bet365",
                    "first": {"win": "6.25", "draw": "4.75", "lost": "1.38"},
                    "end": {"win": "5.75", "draw": "4.50", "lost": "1.53"},
                },
                {
                    "id": "8",
                    "name": "Pinnacle平博",
                    "first": {"win": "6.44", "draw": "4.87", "lost": "1.43"},
                    "end": {"win": "5.65", "draw": "4.30", "lost": "1.63"},
                },
            ]
        },
    }
    company_odds = map_company_europe_odds(company_payload, fid="1279652", endpoint="test")
    assert len(company_odds) == 2
    assert {snapshot.bookmaker for snapshot in company_odds} == {"Bet365", "Pinnacle平博"}
    assert company_odds[0].match_id == "qqsd:1279652"
    assert company_odds[0].market_type.value == "1x2"
    assert company_odds[0].line == "让球1"
    assert company_odds[0].outcome_odds == {"HOME": 5.75, "DRAW": 4.5, "AWAY": 1.53}

    company_asian_payload = {
        "code": 100,
        "data": [
            {
                "id": "5",
                "name": "澳门",
                "first": {"home": "0.96", "handi": "0.5", "away": "0.88"},
                "end": {"home": "0.95", "handi": "0.5/1", "away": "0.89"},
            },
            {
                "id": "3",
                "name": "Bet365",
                "first": {"home": "1.05", "handi": "0.5/1", "away": "0.80"},
                "end": {"home": "0.93", "handi": "0.5/1", "away": "0.93"},
            },
            {
                "id": "11",
                "name": "Bwin",
                "first": [],
                "end": [],
            },
        ],
    }
    company_asian_odds = map_company_asian_odds(company_asian_payload, fid="1279652", endpoint="test")
    assert len(company_asian_odds) == 2
    assert {snapshot.bookmaker for snapshot in company_asian_odds} == {"澳门", "Bet365"}
    assert company_asian_odds[0].match_id == "qqsd:1279652"
    assert company_asian_odds[0].market_type.value == "asian_handicap"
    assert company_asian_odds[0].line == "0.5/1"
    assert company_asian_odds[0].outcome_odds == {"HOME": 1.95, "AWAY": 1.89}

    company_total_payload = {
        "code": 100,
        "data": [
            {
                "id": "5",
                "name": "澳门",
                "first": {"big": "0.88", "handi": "2.5", "small": "0.92"},
                "end": {"big": "1.02", "handi": "2.5", "small": "0.78"},
            },
            {
                "id": "3",
                "name": "Bet365",
                "first": {"big": "0.85", "handi": "2.5", "small": "1.00"},
                "end": {"big": "1.05", "handi": "2.5", "small": "0.80"},
            },
            {
                "id": "11",
                "name": "Bwin",
                "first": [],
                "end": [],
            },
        ],
    }
    company_total_odds = map_company_over_under_odds(company_total_payload, fid="1279652", endpoint="test")
    assert len(company_total_odds) == 2
    assert {snapshot.bookmaker for snapshot in company_total_odds} == {"澳门", "Bet365"}
    assert company_total_odds[0].match_id == "qqsd:1279652"
    assert company_total_odds[0].market_type.value == "over_under"
    assert company_total_odds[0].line == "2.5"
    assert company_total_odds[0].outcome_odds == {"OVER": 2.02, "UNDER": 1.78}

    asian_history_payload = {
        "code": 100,
        "data": [
            {"winodds": "0.99", "flatodds": "-3.5/4", "lostodds": "0.92", "time": "06-14\n23:26"},
            {"win": "0.90", "flat": "-3.5", "lost": "1.00", "updatetime": "06-14 22:46"},
        ],
    }
    asian_history_rows = map_handicap_totals_odds_history_rows(asian_history_payload, market="asian_handicap")
    assert len(asian_history_rows) == 2
    assert asian_history_rows[0]["time"] == "06-14 23:26"
    assert asian_history_rows[0]["home"] == "0.99"
    assert asian_history_rows[0]["line"] == "-3.5/4"
    assert asian_history_rows[0]["away"] == "0.92"
    assert asian_history_rows[1]["line"] == "-3.5"

    total_history_payload = {
        "code": 100,
        "data": {
            "history": [
                {"big": "0.88", "handi": "2.5", "small": "1.02", "time": "06-14 20:00"},
                {"winodds": "0.91", "flatodds": "2.5/3", "lostodds": "0.99", "updatetime": "06-14 18:30"},
            ]
        },
    }
    total_history_rows = map_handicap_totals_odds_history_rows(total_history_payload, market="over_under")
    assert len(total_history_rows) == 2
    assert total_history_rows[0]["over"] == "0.88"
    assert total_history_rows[0]["line"] == "2.5"
    assert total_history_rows[0]["under"] == "1.02"
    assert total_history_rows[1]["over"] == "0.91"
    assert total_history_rows[1]["line"] == "2.5/3"
    assert total_history_rows[1]["under"] == "0.99"
    assert qqsd_current_odds_available(
        {"end": {"home": "1.01", "handi": "-3.5/4", "away": "0.90"}},
        market="asian_handicap",
    )
    assert qqsd_current_odds_available(
        {"end": {"big": "0.94", "handi": "4/4.5", "small": "0.94"}},
        market="over_under",
    )
    assert not qqsd_current_odds_available({"end": []}, market="asian_handicap")
    assert qqsd_history_availability(2, current_available=True)["history_availability"] == "history_available"
    empty_current = qqsd_history_availability(0, current_available=True)
    assert empty_current["history_availability"] == "history_empty_current_available"
    assert empty_current["history_issue"] == "history_rows_empty"
    missing_current = qqsd_history_availability(0, current_available=False)
    assert missing_current["history_availability"] == "history_missing_current_missing"
    assert missing_current["history_issue"] == "current_odds_missing"

    company_summary_payload = {
        "code": 100,
        "data": {
            "list": [
                {
                    "id": "3",
                    "company": "Bet365",
                    "win": "2.10",
                    "draw": "3.30",
                    "lost": "3.40",
                    "home": "0.92",
                    "handi": "-0.5",
                    "away": "0.96",
                    "big": "0.88",
                    "small": "1.02",
                }
            ]
        },
    }
    company_summary_odds = map_company_summary_odds(company_summary_payload, fid="1279652", endpoint="40022")
    assert len(company_summary_odds) == 3
    assert {snapshot.market_type.value for snapshot in company_summary_odds} == {
        "1x2",
        "asian_handicap",
        "over_under",
    }
    assert company_summary_odds[0].bookmaker == "Bet365"
    assert company_summary_odds[0].outcome_odds == {"HOME": 2.1, "DRAW": 3.3, "AWAY": 3.4}

    company_xml = """
    <hierarchy>
      <node resource-id="" text="">
        <node resource-id="com.caiyu.qqsd:id/tv_company" text="Bet365" />
        <node resource-id="com.caiyu.qqsd:id/tv_odd001" text="6.25" />
        <node resource-id="com.caiyu.qqsd:id/tv_odd002" text="4.75" />
        <node resource-id="com.caiyu.qqsd:id/tv_odd003" text="1.38" />
        <node resource-id="com.caiyu.qqsd:id/tv_odd0011" text="5.75" />
        <node resource-id="com.caiyu.qqsd:id/tv_odd0012" text="4.50" />
        <node resource-id="com.caiyu.qqsd:id/tv_odd0013" text="1.53" />
      </node>
    </hierarchy>
    """
    xml_odds = map_company_europe_odds_xml(company_xml, fid="1279652")
    assert len(xml_odds) == 1
    assert xml_odds[0].bookmaker == "Bet365"
    assert xml_odds[0].outcome_odds == {"HOME": 5.75, "DRAW": 4.5, "AWAY": 1.53}

    detail_payload = {
        "code": 100,
        "data": {
            "fid": "1282494",
            "hid": "434",
            "aid": "841",
            "hname": "瓦萨",
            "aname": "库奥皮奥",
            "hteamname": "瓦萨",
            "ateamname": "库奥皮奥",
            "lname": "芬超",
            "matchid": "5",
            "sid": "2026",
            "seasonyear": "2026",
            "matchround": "11",
            "stagename": "联赛赛程",
            "vsdate": "2026-06-13 22:00:00",
            "status": "3",
            "hscore": "1",
            "ascore": "1",
            "hstanding": "[06]",
            "astanding": "[03]",
        },
    }
    enriched = map_match_detail_match(detail_payload, matches[0])
    assert enriched.id == "qqsd:1282494"
    assert enriched.data_completeness == 0.86
    assert enriched.external_ids["qqsd_sid"] == "2026"
    assert enriched.external_ids["qqsd_matchround"] == "11"
    assert enriched.external_ids["qqsd_home_standing"] == "[06]"

    standings_payload = {
        "code": 100,
        "data": {
            "hname": "瓦萨",
            "aname": "库奥皮奥",
            "hpower": {"total_score": "100"},
            "apower": {"total_score": "132"},
            "home_datatotal": {"count": "10", "win": "6", "draw": "3", "lost": "1", "innum": "28", "lostnum": "7"},
            "away_datatotal": {"count": "10", "win": "4", "draw": "5", "lost": "1", "innum": "11", "lostnum": "6"},
            "ranks": [
                {
                    "homestanding": {"name": "瓦萨(中游)", "standing": "6", "score": "13"},
                    "awaystanding": {"name": "库奥皮奥(上游)", "standing": "3", "score": "20"},
                }
            ],
        },
    }
    extreme_payload = {
        "code": 100,
        "data": [
            {"fid": "1282494", "str": "近期赢盘12，历史最高4"},
            {"fid": "other", "str": "忽略其他比赛"},
        ],
    }
    tools_payload = {
        "code": 100,
        "data": [
            {"title": "master精选", "url": "servicemaster://"},
            {"title": "指数倾向", "url": "servicepanhelper://"},
        ],
    }
    lingsi_payload = {"code": 100, "data": {"title": "球球龙虾", "type": "app", "url": "https://example.invalid"}}
    vote_payload = {
        "code": 100,
        "data": [
            {"name": "胜", "rate": "22%", "select": "12"},
            {"name": "平", "rate": "31%", "select": "17"},
            {"name": "负", "rate": "47%", "select": "26"},
        ],
    }
    europe_history_payload = {
        "code": 100,
        "data": [
            {"company": "Bet365", "win": "2.10", "draw": "3.30", "lost": "3.40", "time": "2026-06-13 12:00:00"},
            {"company": "Bet365", "win": "2.05", "draw": "3.35", "lost": "3.50", "time": "2026-06-13 10:00:00"},
        ],
    }
    heat_payload = {
        "code": 100,
        "data": {
            "fixtureid": "1282494",
            "winamount": "1200",
            "drawamount": "700",
            "lostamount": "900",
            "winrate": "43%",
            "drawrate": "25%",
            "lostrate": "32%",
        },
    }
    handicap_europe_payload = {
        "code": 100,
        "data": {
            "handicapline": "-1",
            "list": [
                {
                    "id": "3",
                    "name": "Bet365",
                    "end": {"win": "3.10", "draw": "3.60", "lost": "2.00"},
                }
            ],
        },
    }
    league_stats_payload = {
        "code": 100,
        "data": {"total": "24", "win": "11", "draw": "6", "lost": "7", "big": "13", "small": "11"},
    }
    finding = build_context_finding(
        enriched,
        detail_payload=detail_payload,
        standings_payload=standings_payload,
        extreme_payload=extreme_payload,
        tools_payload=tools_payload,
        lingsi_payload=lingsi_payload,
        vote_payload=vote_payload,
        europe_odds_history_payload=europe_history_payload,
        odds_summary_payload=company_summary_payload,
        odds_heat_payload=heat_payload,
        handicap_europe_payload=handicap_europe_payload,
        league_stats_payload=league_stats_payload,
        errors=[{"key": "vote_infos", "error": "DataSourceError:sample"}],
    )
    assert finding is not None
    assert finding.id == "qqsd:1282494:qqsd-context"
    assert finding.agent_name == "qqsd_full_context"
    assert finding.score_delta == 0.0
    assert "综合评分 瓦萨 100 / 库奥皮奥 132" in finding.summary
    assert "投票热度 胜22% / 平31% / 负47%" in finding.summary
    assert "欧指历史2条" in finding.summary
    assert "冷热 胜43% / 平25% / 负32%" in finding.summary
    assert "让球欧赔1条(让球-1)" in finding.summary
    assert "联赛统计 24场 11胜/6平/7负" in finding.summary
    assert "可用分析工具 master精选、指数倾向" in finding.summary
    assert len(finding.payload["extremes"]) == 1
    assert finding.payload["analysis_tools"] == ["master精选", "指数倾向"]
    assert len(finding.payload["vote_infos"]) == 3
    assert finding.payload["odds_context"]["europe_history_rows"] == 2
    assert finding.payload["odds_context"]["summary_rows"] == 1
    assert finding.payload["odds_context"]["heat"]["winrate"] == "43%"
    assert finding.payload["qqsd_errors"][0]["key"] == "vote_infos"

    league_payload = {
        "code": 100,
        "data": {
            "all": {
                "欧洲": {
                    "seasonlist": [
                        {
                            "COUNTRYNAME": "芬兰",
                            "DATA": [
                                [
                                    {
                                        "MATCHID": "5",
                                        "SEASONID": "8061",
                                        "SEASONYEAR": "2026",
                                        "SEASONGBNAME": "2026 芬兰超级联赛",
                                        "MATCHENNAME": "Finnish Veikkausliga",
                                        "MATCHGBNAME": "芬兰超级联赛",
                                        "SIMPLEGBNAME": "芬超",
                                    }
                                ]
                            ],
                        }
                    ]
                }
            }
        },
    }
    entry = find_league_entry(
        league_payload,
        identifiers=["FIN_VEIKKAUSLIIGA", "Veikkausliiga", "Finland - Veikkausliiga", "芬超"],
        season=2026,
    )
    assert entry is not None
    assert entry["MATCHID"] == "5"
    assert entry["SEASONID"] == "8061"

    australia_league_payload = {
        "code": 100,
        "data": {
            "all": {
                "大洋洲": {
                    "seasonlist": [
                        {
                            "COUNTRYNAME": "澳大利亚",
                            "DATA": [
                                [
                                    {
                                        "MATCHID": "312",
                                        "SEASONID": "9001",
                                        "SEASONYEAR": "2025/2026",
                                        "SEASONGBNAME": "2025/2026 澳大利亚甲级联赛",
                                        "MATCHENNAME": "Australia League A",
                                        "MATCHGBNAME": "澳大利亚超级联赛",
                                        "SIMPLEGBNAME": "澳超",
                                    },
                                    {
                                        "MATCHID": "615",
                                        "SEASONID": "9002",
                                        "SEASONYEAR": "2026",
                                        "SEASONGBNAME": "2026 澳大利亚首都直辖区超级联赛",
                                        "MATCHENNAME": "",
                                        "MATCHGBNAME": "澳大利亚首都直辖区超级联赛",
                                        "SIMPLEGBNAME": "澳首超",
                                    },
                                ]
                            ],
                        }
                    ]
                }
            }
        },
    }
    load_project_settings = __import__("football_analysis.settings", fromlist=["load_settings"]).load_settings
    configured = load_project_settings()
    a_league = next(league for league in configured.leagues if league.code == "A_LEAGUE")
    a_league_entry = find_league_entry(
        australia_league_payload,
        identifiers=[a_league.code, a_league.name, *a_league.aliases],
    )
    assert a_league_entry is not None
    assert a_league_entry["MATCHID"] == "312"
    act_npl = next(league for league in configured.leagues if league.code == "AUS_ACT_NPL")
    act_entry = find_league_entry(
        australia_league_payload,
        identifiers=[act_npl.code, act_npl.name, *act_npl.aliases],
        season=2026,
    )
    assert act_entry is not None
    assert act_entry["MATCHID"] == "615"

    from football_analysis.datasources.qqsd import QQSDClient

    class FakeQQSDClient(QQSDClient):
        def __init__(self, payloads: dict[str, dict], archive_payloads: dict[int, dict]) -> None:
            self.payloads = payloads
            self.archive_payloads = archive_payloads
            self.company_payload = company_payload
            self.company_asian_payload = company_asian_payload
            self.company_total_payload = company_total_payload
            self.company_summary_payload = company_summary_payload
            self.europe_history_payload = europe_history_payload
            self.heat_payload = heat_payload
            self.handicap_europe_payload = handicap_europe_payload
            self.league_stats_payload = league_stats_payload
            self._extreme_data_cache = None
            self._league_list_cache = None
            self._analysis_tools_cache = None
            self._score_list_cache = {}
            self._archive_score_cache = {}
            self.calls: list[str] = []
            self.archive_calls: list[int] = []
            self.company_calls: list[tuple[str, dict]] = []

        def score_list(self, *, cid: str = "3", stid: str = "1") -> dict:
            self.calls.append(cid)
            return self.payloads.get(cid, {"code": 100, "data": {"list": []}})

        def archive_score_page(self, date: str, *, page: int = 1) -> dict:
            self.archive_calls.append(page)
            return self.archive_payloads.get(page, {"code": 100, "data": []})

        def _post(self, c_id: str, body: dict | None = None) -> dict:
            self.company_calls.append((c_id, body or {}))
            if c_id == "40005":
                return self.company_payload
            if c_id == "40020":
                if (body or {}).get("cid"):
                    if str((body or {}).get("fid") or "") == "1282495" and str((body or {}).get("t") or "") == "2":
                        return {"code": 100, "data": [{"home": "0.91", "handi": "-0.5", "away": "0.99", "time": "06-14 23:26"}]}
                    return {"code": 100, "data": []}
                return self.company_asian_payload
            if c_id == "40004":
                if (body or {}).get("cid"):
                    return {"code": 100, "data": []}
                return self.company_total_payload
            if c_id == "40018":
                return self.europe_history_payload
            if c_id == "50012":
                return {"code": 100, "data": []}
            if c_id == "40022":
                return self.company_summary_payload
            if c_id == "40029":
                return self.heat_payload
            if c_id == "40038":
                return self.handicap_europe_payload
            if c_id == "50017":
                return self.league_stats_payload
            return {"code": 100, "data": {"list": []}}

        def _new_api_post(self, path: str, body: dict | None = None) -> dict:
            if path.strip("/") == "home/lingsi":
                return lingsi_payload
            if path.strip("/") == "team/voteinfos":
                return vote_payload
            return {"code": 100, "data": {}}

    previous_cids = os.environ.get("QQSD_SCORE_CIDS")
    previous_window = os.environ.get("QQSD_DATE_WINDOW_END_HOURS")
    try:
        os.environ["QQSD_SCORE_CIDS"] = "1,2"
        os.environ["QQSD_DATE_WINDOW_END_HOURS"] = "4"
        multi_payloads = {
            "1": payload,
            "2": {
                "code": 100,
                "data": {
                    "list": [
                        {
                            **payload["data"]["list"][0],
                            "fid": "1282495",
                            "vsdate": "2026-06-14 00:30:00",
                            "hname": "拉赫蒂",
                            "aname": "塞那乔其",
                            "w": "0.90",
                            "l": "0.90",
                        },
                        payload["data"]["list"][0],
                    ]
                },
            },
        }
        client = FakeQQSDClient(multi_payloads, {1: archive_payload})
        window_matches = client.fixtures("2026-06-13")
        assert [match.id for match in window_matches] == ["qqsd:1282494", "qqsd:1282495"]
        assert client.archive_calls == [1, 2]
        assert client.calls == ["1", "2"]

        window_odds = client.odds("2026-06-13")
        assert {snapshot.match_id for snapshot in window_odds} == {"qqsd:1282494", "qqsd:1282495"}
        assert {snapshot.market_type.value for snapshot in window_odds} == {"1x2", "asian_handicap", "over_under"}
        assert {call[0] for call in client.company_calls} == set(QQSD_LIVE_ODDS_ENDPOINT_IDS)
        assert {call[1]["fid"] for call in client.company_calls} == {"1282494", "1282495"}
        assert len(window_odds) == 15

        bundle = client.match_analysis_bundle("1282494")
        assert bundle["europe_odds_history"] == europe_history_payload
        assert bundle["odds_summary"] == company_summary_payload
        assert bundle["odds_heat"] == heat_payload
        assert bundle["handicap_europe_odds"] == handicap_europe_payload
        assert bundle["league_stats"] == league_stats_payload
        assert not bundle["errors"]
        assert client.asian_odds_history(
            "1282494",
            company_id="8",
            vsdate="2026-06-15 01:00:00",
            extra_params={"sid": "2026"},
        ) == {"code": 100, "data": []}
        assert client.company_calls[-1] == (
            QQSD_ASIAN_HANDICAP_ENDPOINT,
            {"fid": "1282494", "cid": "8", "t": "2", "vsdate": "2026-06-15 01:00:00", "sid": "2026"},
        )
        assert client.over_under_odds_history("1282494", company_id="8") == {"code": 100, "data": []}
        assert client.company_calls[-1] == (
            QQSD_OVER_UNDER_ENDPOINT,
            {"fid": "1282494", "cid": "8", "t": "3"},
        )
        timeline_bundle = client.match_odds_timeline_bundle(
            "1282495",
            vsdate="2026-06-14 00:30:00",
            company_name="Bet365",
        )
        assert timeline_bundle["summary"]["market_count"] == 3
        assert timeline_bundle["markets"]["asian_handicap"]["history_row_count"] == 1
        assert timeline_bundle["markets"]["asian_handicap"]["history_availability"] == "history_available"
        assert timeline_bundle["markets"]["over_under"]["history_availability"] == "history_empty_current_available"

        from football_analysis.cli import _qqsd_build_odds_history_coverage

        coverage = _qqsd_build_odds_history_coverage(
            client,
            date="2026-06-13",
            markets=["asian_handicap", "over_under"],
            company_id=None,
            company_name="Bet365",
            history_params=None,
        )
        assert coverage["matches_checked"] == 2
        assert coverage["market_checks"] == 4
        assert coverage["history_available_count"] == 1
        assert coverage["history_empty_current_available_count"] == 3
        assert coverage["current_missing_count"] == 0
        assert coverage["by_market"]["asian_handicap"]["history_available_count"] == 1
        assert coverage["by_market"]["over_under"]["history_empty_current_available_count"] == 2
        availability_by_key = {
            (check["fid"], check["market"]): check["history_availability"]
            for check in coverage["checks"]
        }
        assert availability_by_key[("1282495", "asian_handicap")] == "history_available"
        assert availability_by_key[("1282494", "asian_handicap")] == "history_empty_current_available"
        missing_company_coverage = _qqsd_build_odds_history_coverage(
            client,
            date="2026-06-13",
            markets=["asian_handicap"],
            company_id=None,
            company_name="NoSuchBook",
        )
        assert missing_company_coverage["company_missing_count"] == 2
        assert missing_company_coverage["history_available_count"] == 0
    finally:
        if previous_cids is None:
            os.environ.pop("QQSD_SCORE_CIDS", None)
        else:
            os.environ["QQSD_SCORE_CIDS"] = previous_cids
        if previous_window is None:
            os.environ.pop("QQSD_DATE_WINDOW_END_HOURS", None)
        else:
            os.environ["QQSD_DATE_WINDOW_END_HOURS"] = previous_window

    import football_analysis.ingestion as ingestion_module

    previous_client = ingestion_module.QQSDClient
    try:
        with TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "qqsd-fast.db"
            from football_analysis.settings import load_settings

            settings = load_settings()
            settings.storage.database_url = f"sqlite:///{settings_path}"
            settings.ingestion.qqsd_live_context_enabled = False
            settings.ingestion.qqsd_timeline_company_name = "Bet365"
            repository = StructuredRepository(settings.storage.database_url)
            repository.initialize()
            try:
                created_clients: list[FakeQQSDClient] = []

                def fake_client_factory(context):  # type: ignore[no-untyped-def]
                    client = FakeQQSDClient({"1": payload}, {1: archive_payload})
                    created_clients.append(client)
                    return client

                ingestion_module.QQSDClient = fake_client_factory  # type: ignore[assignment]
                repository.upsert_model(
                    "matches",
                    "qqsd:1282494",
                    Match(
                        id="qqsd:1282494",
                        league="芬超",
                        home_team="瓦萨",
                        away_team="库奥皮奥",
                        kickoff_at=matches[0].kickoff_at,
                        data_completeness=0.86,
                        external_ids={"qqsd_fid": "1282494", "qqsd_sid": "2026"},
                    ),
                )
                pipeline = IngestionService(settings, repository)
                result = pipeline.ingest_odds(date="2026-06-13", source="qqsd", league_code="FIN_VEIKKAUSLIIGA")
                assert result.inserted > 0
                assert created_clients, "QQSD ingestion should instantiate the client"
                assert not any(
                    c_id in set(QQSD_DEEP_CONTEXT_ENDPOINT_IDS)
                    for c_id, _ in created_clients[0].company_calls
                ), "default QQSD live odds refresh should skip deep context endpoints"
                stored_match = repository.get_model("matches", "qqsd:1282494", Match)
                assert stored_match is not None
                assert stored_match.data_completeness == 0.86
                assert stored_match.external_ids["qqsd_sid"] == "2026"
                assert repository.count("matches") >= 1
                assert repository.count("odds") >= 1

                result = pipeline.ingest_historical(
                    league="FIN_VEIKKAUSLIIGA",
                    season="2026",
                    source="qqsd",
                    start_date="2026-06-13",
                    end_date="2026-06-13",
                    max_pages=1,
                )
                assert result.inserted == 1
                from football_analysis.contracts import HistoricalMatchRow

                stored = repository.get_model(
                    "historical_matches",
                    "qqsd:FIN_VEIKKAUSLIIGA:2026:1282494",
                    HistoricalMatchRow,
                )
                assert stored is not None
                assert stored.away_odds == 2.12

                local_finished = Match(
                    id="qqsd:local-ah-1",
                    league="芬超",
                    home_team="本地主队",
                    away_team="本地客队",
                    kickoff_at=matches[0].kickoff_at,
                    status=MatchStatus.finished,
                    home_score=2,
                    away_score=0,
                    data_completeness=0.9,
                )
                repository.upsert_model("matches", local_finished.id, local_finished)
                repository.upsert_model(
                    "odds",
                    "qqsd:local-ah-1:qqsd:asian_handicap:-1",
                    OddsSnapshot(
                        id="qqsd:local-ah-1:qqsd:asian_handicap:-1",
                        match_id=local_finished.id,
                        market_type="asian_handicap",
                        line="-1",
                        source="qqsd",
                        bookmaker="QQSD",
                        outcome_odds={"HOME": 1.92, "AWAY": 1.88},
                        market_average={"HOME": 1.9, "AWAY": 1.86},
                        best_price={"HOME": 1.94, "AWAY": 1.9},
                    ),
                )
                result = pipeline.ingest_historical(
                    league="FIN_VEIKKAUSLIIGA",
                    season="2026",
                    source="qqsd_local_asian",
                )
                assert result.inserted >= 1
                stored_local = repository.get_model(
                    "historical_matches",
                    "qqsd-local:FIN_VEIKKAUSLIIGA:2026:local-ah-1:ah",
                    HistoricalMatchRow,
                )
                assert stored_local is not None
                assert stored_local.ah_line == -1.0
                assert stored_local.ah_home_odds == 1.92
                assert stored_local.closing_ah_away_odds == 1.9

                settings.ingestion.qqsd_live_context_enabled = True
                created_clients.clear()
                result = pipeline.ingest_fixtures(
                    date="2026-06-13",
                    source="qqsd",
                    league_code="FIN_VEIKKAUSLIIGA",
                )
                assert result.inserted >= 1
                finding = repository.get_model("findings", "qqsd:1282494:qqsd-context", AgentFinding)
                assert finding is not None
                assert finding.payload["odds_context"]["europe_history_rows"] == 2
                assert finding.payload["odds_context"]["summary_rows"] == 1
                assert finding.payload["odds_timeline"]["markets"]["asian_handicap"]["history_availability"] in {
                    "history_available",
                    "history_empty_current_available",
                }
                raw_timeline = repository.get_cached_payload("qqsd", "odds_timeline", "qqsd:odds_timeline:1282494")
                assert raw_timeline is not None
                assert raw_timeline["summary"]["market_count"] == 3
                raw_heat = repository.get_cached_payload("qqsd", "odds_heat", "qqsd:odds_heat:1282494")
                assert raw_heat is not None
                assert raw_heat["data"]["winrate"] == "43%"
            finally:
                repository.close()
    finally:
        ingestion_module.QQSDClient = previous_client  # type: ignore[assignment]
    print("verify_qqsd_datasource: ok")


if __name__ == "__main__":
    main()
