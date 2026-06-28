from __future__ import annotations

import hashlib
import os
import re
import urllib.parse
from datetime import datetime, timedelta
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from football_analysis.contracts import HistoricalMatchRow
from football_analysis.datasources.base import ClientContext, DataSourceError
from football_analysis.models import AgentFinding, EvidenceSource, Match, MatchStatus, OddsSnapshot


C_CPID = "2"
C_TYPE = "2"
C_KEY_SECRET = "ake5%2*&$8k)dfek!r"
QQSD_TIMEZONE = ZoneInfo("Asia/Shanghai")

QQSD_SCORE_LIST_ENDPOINT = "41000"
QQSD_ARCHIVE_SCORE_ENDPOINT = "91002"
QQSD_MATCH_DETAIL_ENDPOINT = "40006"
QQSD_STANDINGS_ENDPOINT = "41101"
QQSD_EXTREME_DATA_ENDPOINT = "40034"
QQSD_ANALYSIS_TOOLS_ENDPOINT = "40046"
QQSD_INJURY_PREVIEW_ENDPOINT = "40025"
QQSD_BETTING_DISTRIBUTION_ENDPOINT = "40030"
QQSD_ODDS_TREND_ENDPOINT = "40032"
QQSD_SAME_ODDS_HISTORY_ENDPOINT = "40035"
QQSD_LINEUP_SIMPLE_ENDPOINT = "41105"
QQSD_LINEUP_DETAIL_ENDPOINT = "41106"
QQSD_BIFA_TRADE_ENDPOINT = "41107"
QQSD_COMPANY_LIST_ENDPOINT = "41108"
QQSD_LINEUP_FULL_ENDPOINT = "41111"
QQSD_ODDS_CHANGE_LIST_ENDPOINT = "41112"
QQSD_EUROPE_ODDS_ENDPOINT = "40005"
QQSD_ASIAN_HANDICAP_ENDPOINT = "40020"
QQSD_OVER_UNDER_ENDPOINT = "40004"
QQSD_EUROPE_ODDS_HISTORY_ENDPOINT = "40018"
QQSD_HANDICAP_TOTALS_ODDS_HISTORY_ENDPOINT = "50012"
QQSD_ODDS_SUMMARY_ENDPOINT = "40022"
QQSD_ODDS_HEAT_ENDPOINT = "40029"
QQSD_HANDICAP_EUROPE_ODDS_ENDPOINT = "40038"
QQSD_LEAGUE_STATS_ENDPOINT = "50017"
QQSD_LEAGUE_LIST_ENDPOINT = "50000"
QQSD_LEAGUE_STANDINGS_ENDPOINT = "50002"
QQSD_FOLLOWED_MATCHES_ENDPOINT = "41004"

QQSD_LIVE_ODDS_ENDPOINT_IDS = (
    QQSD_EUROPE_ODDS_ENDPOINT,
    QQSD_ASIAN_HANDICAP_ENDPOINT,
    QQSD_OVER_UNDER_ENDPOINT,
)
QQSD_ODDS_TIMELINE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "1x2": {
        "status": "supported",
        "company_endpoint": QQSD_EUROPE_ODDS_ENDPOINT,
        "timeline_endpoint": QQSD_EUROPE_ODDS_HISTORY_ENDPOINT,
        "company_id_param": "companyid",
        "row_fields": ("time", "win", "draw", "lost", "pay", "kwin", "kdraw", "klost"),
        "verification": "remote_verified",
    },
    "asian_handicap": {
        "status": "supported",
        "company_endpoint": QQSD_ASIAN_HANDICAP_ENDPOINT,
        "timeline_endpoint": QQSD_ASIAN_HANDICAP_ENDPOINT,
        "company_id_param": "cid",
        "market_param": "t",
        "market_param_value": "2",
        "row_fields": ("time", "home", "handi", "away", "pay", "pk", "s1", "s2"),
        "verification": "remote_verified_matches_app_timeline",
    },
    "over_under": {
        "status": "supported",
        "company_endpoint": QQSD_OVER_UNDER_ENDPOINT,
        "timeline_endpoint": QQSD_OVER_UNDER_ENDPOINT,
        "company_id_param": "cid",
        "market_param": "t",
        "market_param_value": "3",
        "row_fields": ("time", "big", "handi", "small", "pay", "pk", "b", "s"),
        "verification": "remote_verified_matches_app_timeline",
    },
}
QQSD_DEEP_CONTEXT_ENDPOINT_IDS = (
    QQSD_MATCH_DETAIL_ENDPOINT,
    QQSD_STANDINGS_ENDPOINT,
    QQSD_EXTREME_DATA_ENDPOINT,
    QQSD_ANALYSIS_TOOLS_ENDPOINT,
    QQSD_INJURY_PREVIEW_ENDPOINT,
    QQSD_BETTING_DISTRIBUTION_ENDPOINT,
    QQSD_ODDS_TREND_ENDPOINT,
    QQSD_SAME_ODDS_HISTORY_ENDPOINT,
    QQSD_LINEUP_SIMPLE_ENDPOINT,
    QQSD_LINEUP_DETAIL_ENDPOINT,
    QQSD_BIFA_TRADE_ENDPOINT,
    QQSD_COMPANY_LIST_ENDPOINT,
    QQSD_LINEUP_FULL_ENDPOINT,
    QQSD_ODDS_CHANGE_LIST_ENDPOINT,
    QQSD_EUROPE_ODDS_HISTORY_ENDPOINT,
    QQSD_HANDICAP_TOTALS_ODDS_HISTORY_ENDPOINT,
    QQSD_ODDS_SUMMARY_ENDPOINT,
    QQSD_ODDS_HEAT_ENDPOINT,
    QQSD_HANDICAP_EUROPE_ODDS_ENDPOINT,
    QQSD_LEAGUE_STATS_ENDPOINT,
)

QQSD_ENDPOINT_CATALOG: dict[str, dict[str, Any]] = {
    QQSD_SCORE_LIST_ENDPOINT: {
        "name": "score_list",
        "category": "fixtures",
        "params": ("stid", "t", "mytime", "cid"),
        "execution_role": "live_fixture_discovery",
        "reverse_status": "runtime_verified",
    },
    QQSD_ARCHIVE_SCORE_ENDPOINT: {
        "name": "archive_score_page",
        "category": "historical_results",
        "params": ("date", "page"),
        "execution_role": "historical_backtest_seed",
        "reverse_status": "remote_verified",
    },
    QQSD_MATCH_DETAIL_ENDPOINT: {
        "name": "match_detail",
        "category": "match_context",
        "params": ("fid",),
        "execution_role": "deep_context",
        "reverse_status": "runtime_verified",
    },
    QQSD_STANDINGS_ENDPOINT: {
        "name": "standings",
        "category": "match_context",
        "params": ("fid",),
        "execution_role": "deep_context",
        "reverse_status": "runtime_verified",
    },
    QQSD_EXTREME_DATA_ENDPOINT: {
        "name": "extreme_data",
        "category": "match_context",
        "params": (),
        "execution_role": "deep_context",
        "reverse_status": "runtime_verified",
    },
    QQSD_ANALYSIS_TOOLS_ENDPOINT: {
        "name": "analysis_tools",
        "category": "match_context",
        "params": (),
        "execution_role": "deep_context",
        "reverse_status": "runtime_verified",
    },
    QQSD_INJURY_PREVIEW_ENDPOINT: {
        "name": "injury_preview",
        "category": "match_context",
        "params": ("fid",),
        "execution_role": "deep_context",
        "reverse_status": "api_reference_verified",
    },
    QQSD_LINEUP_SIMPLE_ENDPOINT: {
        "name": "lineup_simple",
        "category": "match_context",
        "params": ("fid",),
        "execution_role": "deep_context",
        "reverse_status": "api_reference_verified",
    },
    QQSD_LINEUP_DETAIL_ENDPOINT: {
        "name": "lineup_detail",
        "category": "match_context",
        "params": ("fid",),
        "execution_role": "deep_context",
        "reverse_status": "api_reference_verified",
    },
    QQSD_LINEUP_FULL_ENDPOINT: {
        "name": "lineup_full",
        "category": "match_context",
        "params": ("fid",),
        "execution_role": "deep_context",
        "reverse_status": "api_reference_verified",
    },
    QQSD_BETTING_DISTRIBUTION_ENDPOINT: {
        "name": "betting_distribution",
        "category": "odds_context",
        "params": ("fid",),
        "execution_role": "deep_context",
        "reverse_status": "api_reference_verified",
    },
    QQSD_SAME_ODDS_HISTORY_ENDPOINT: {
        "name": "same_odds_history",
        "category": "odds_context",
        "params": ("fid", "companyid?"),
        "execution_role": "deep_context",
        "reverse_status": "api_reference_verified",
    },
    QQSD_ODDS_TREND_ENDPOINT: {
        "name": "odds_trend",
        "category": "odds_context",
        "params": ("fid", "companyid?"),
        "execution_role": "deep_context",
        "reverse_status": "api_reference_verified",
    },
    QQSD_BIFA_TRADE_ENDPOINT: {
        "name": "bifa_trade",
        "category": "odds_context",
        "params": ("fid",),
        "execution_role": "deep_context",
        "reverse_status": "api_reference_verified",
    },
    QQSD_COMPANY_LIST_ENDPOINT: {
        "name": "company_list",
        "category": "odds_metadata",
        "params": (),
        "execution_role": "company_mapping",
        "reverse_status": "api_reference_verified",
    },
    QQSD_ODDS_CHANGE_LIST_ENDPOINT: {
        "name": "odds_change_list",
        "category": "odds_context",
        "params": (),
        "execution_role": "deep_context",
        "reverse_status": "api_reference_verified",
    },
    QQSD_EUROPE_ODDS_ENDPOINT: {
        "name": "company_europe_odds",
        "category": "odds",
        "market_type": "1x2",
        "params": ("fid",),
        "execution_role": "live_odds",
        "reverse_status": "remote_verified",
    },
    QQSD_ASIAN_HANDICAP_ENDPOINT: {
        "name": "company_asian_odds",
        "category": "odds",
        "market_type": "asian_handicap",
        "params": ("fid",),
        "execution_role": "live_odds",
        "reverse_status": "remote_verified",
        "timeline_status": QQSD_ODDS_TIMELINE_CAPABILITIES["asian_handicap"]["status"],
    },
    QQSD_OVER_UNDER_ENDPOINT: {
        "name": "company_over_under_odds",
        "category": "odds",
        "market_type": "over_under",
        "params": ("fid",),
        "execution_role": "live_odds",
        "reverse_status": "remote_verified",
        "timeline_status": QQSD_ODDS_TIMELINE_CAPABILITIES["over_under"]["status"],
    },
    QQSD_EUROPE_ODDS_HISTORY_ENDPOINT: {
        "name": "europe_odds_history",
        "category": "odds_context",
        "market_type": "1x2",
        "params": ("fid", "companyid?"),
        "execution_role": "deep_context",
        "reverse_status": "remote_verified",
        "timeline_status": QQSD_ODDS_TIMELINE_CAPABILITIES["1x2"]["status"],
    },
    QQSD_HANDICAP_TOTALS_ODDS_HISTORY_ENDPOINT: {
        "name": "handicap_totals_odds_history_auxiliary",
        "category": "odds_context",
        "market_type": "unknown_auxiliary",
        "params": ("observed_from_app",),
        "execution_role": "context_only",
        "reverse_status": "app_runtime_observed_empty_for_handicap_totals",
        "timeline_status": "not_primary_timeline",
    },
    QQSD_ODDS_SUMMARY_ENDPOINT: {
        "name": "odds_summary",
        "category": "odds_context",
        "market_type": "mixed",
        "params": ("fid",),
        "execution_role": "deep_context",
        "reverse_status": "remote_verified",
    },
    QQSD_ODDS_HEAT_ENDPOINT: {
        "name": "odds_heat",
        "category": "odds_context",
        "params": ("fid",),
        "execution_role": "deep_context",
        "reverse_status": "remote_verified",
    },
    QQSD_HANDICAP_EUROPE_ODDS_ENDPOINT: {
        "name": "handicap_europe_odds",
        "category": "odds_context",
        "market_type": "handicap_1x2",
        "params": ("fid",),
        "execution_role": "context_only",
        "reverse_status": "remote_verified",
    },
    QQSD_LEAGUE_STATS_ENDPOINT: {
        "name": "league_stats",
        "category": "match_context",
        "params": ("fid",),
        "execution_role": "deep_context",
        "reverse_status": "remote_verified",
    },
    QQSD_LEAGUE_LIST_ENDPOINT: {
        "name": "league_list",
        "category": "league_metadata",
        "params": (),
        "execution_role": "historical_backtest_mapping",
        "reverse_status": "remote_verified",
    },
    QQSD_LEAGUE_STANDINGS_ENDPOINT: {
        "name": "league_standings",
        "category": "league_metadata",
        "params": ("matchid", "sid"),
        "execution_role": "historical_backtest_mapping",
        "reverse_status": "remote_verified",
    },
    QQSD_FOLLOWED_MATCHES_ENDPOINT: {
        "name": "followed_matches",
        "category": "fixtures",
        "params": ("t",),
        "execution_role": "operator_context",
        "reverse_status": "runtime_verified",
    },
    "p:home/lingsi/": {
        "name": "lingsi",
        "category": "match_context",
        "params": ("fid",),
        "execution_role": "deep_context",
        "reverse_status": "runtime_verified",
    },
    "p:team/voteinfos/": {
        "name": "vote_infos",
        "category": "match_context",
        "params": ("fid",),
        "execution_role": "deep_context",
        "reverse_status": "runtime_verified",
    },
}


def qqsd_endpoint_catalog() -> dict[str, dict[str, Any]]:
    return {endpoint: dict(metadata) for endpoint, metadata in QQSD_ENDPOINT_CATALOG.items()}


def qqsd_odds_timeline_capabilities() -> dict[str, dict[str, Any]]:
    return {market: dict(metadata) for market, metadata in QQSD_ODDS_TIMELINE_CAPABILITIES.items()}


class QQSDClient:
    provider = "qqsd"

    def __init__(self, context: ClientContext):
        self.context = context
        self._score_list_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._extreme_data_cache: dict[str, Any] | None = None
        self._league_list_cache: dict[str, Any] | None = None
        self._analysis_tools_cache: dict[str, Any] | None = None
        self._company_list_cache: dict[str, Any] | None = None
        self._odds_change_list_cache: dict[str, Any] | None = None
        self._archive_score_cache: dict[tuple[str, int], dict[str, Any]] = {}

    def score_list(self, *, cid: str = "3", stid: str = "1") -> dict[str, Any]:
        cache_key = (cid, stid)
        if cache_key in self._score_list_cache:
            return self._score_list_cache[cache_key]
        mytime = str(int(datetime.utcnow().timestamp() * 1000))
        payload = self._post(
            c_id=QQSD_SCORE_LIST_ENDPOINT,
            body={
                "stid": stid,
                "t": "1",
                "mytime": mytime,
                "cid": cid,
            },
        )
        self._score_list_cache[cache_key] = payload
        return payload

    def score_lists(self, *, cids: list[str] | None = None, stid: str = "1") -> list[dict[str, Any]]:
        return [self.score_list(cid=cid, stid=stid) for cid in (cids or _default_score_cids())]

    def archive_score_page(self, date: str, *, page: int = 1) -> dict[str, Any]:
        cache_key = (date, page)
        if cache_key in self._archive_score_cache:
            return self._archive_score_cache[cache_key]
        payload = self._post(c_id=QQSD_ARCHIVE_SCORE_ENDPOINT, body={"date": date, "page": str(page)})
        self._archive_score_cache[cache_key] = payload
        return payload

    def archive_score_pages(self, date: str, *, max_pages: int | None = None) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        seen_signatures: set[tuple[str, ...]] = set()
        for page in range(1, (max_pages or _archive_max_pages()) + 1):
            payload = self.archive_score_page(date, page=page)
            rows = _archive_rows(payload)
            if not rows:
                break
            signature = _archive_page_signature(rows)
            if signature in seen_signatures:
                break
            seen_signatures.add(signature)
            pages.append(payload)
        return pages

    def match_detail(self, fid: str) -> dict[str, Any]:
        return self._post(c_id=QQSD_MATCH_DETAIL_ENDPOINT, body={"fid": fid})

    def standings(self, fid: str) -> dict[str, Any]:
        return self._post(c_id=QQSD_STANDINGS_ENDPOINT, body={"fid": fid})

    def extreme_data(self) -> dict[str, Any]:
        if self._extreme_data_cache is None:
            self._extreme_data_cache = self._post(c_id=QQSD_EXTREME_DATA_ENDPOINT)
        return self._extreme_data_cache

    def league_list(self) -> dict[str, Any]:
        if self._league_list_cache is None:
            self._league_list_cache = self._post(c_id=QQSD_LEAGUE_LIST_ENDPOINT)
        return self._league_list_cache

    def league_standings(self, matchid: str, seasonid: str) -> dict[str, Any]:
        return self._post(c_id=QQSD_LEAGUE_STANDINGS_ENDPOINT, body={"matchid": matchid, "sid": seasonid})

    def followed_matches(self) -> dict[str, Any]:
        return self._post(c_id=QQSD_FOLLOWED_MATCHES_ENDPOINT, body={"t": "2"})

    def analysis_tools(self) -> dict[str, Any]:
        if self._analysis_tools_cache is None:
            self._analysis_tools_cache = self._post(c_id=QQSD_ANALYSIS_TOOLS_ENDPOINT)
        return self._analysis_tools_cache

    def injury_preview(self, fid: str) -> dict[str, Any]:
        return self._post(c_id=QQSD_INJURY_PREVIEW_ENDPOINT, body={"fid": fid})

    def lineup_simple(self, fid: str) -> dict[str, Any]:
        return self._post(c_id=QQSD_LINEUP_SIMPLE_ENDPOINT, body={"fid": fid})

    def lineup_detail(self, fid: str) -> dict[str, Any]:
        return self._post(c_id=QQSD_LINEUP_DETAIL_ENDPOINT, body={"fid": fid})

    def lineup_full(self, fid: str) -> dict[str, Any]:
        return self._post(c_id=QQSD_LINEUP_FULL_ENDPOINT, body={"fid": fid})

    def betting_distribution(self, fid: str) -> dict[str, Any]:
        return self._post(c_id=QQSD_BETTING_DISTRIBUTION_ENDPOINT, body={"fid": fid})

    def same_odds_history(self, fid: str, *, company_id: str | None = None) -> dict[str, Any]:
        body = {"fid": fid}
        if company_id:
            body["companyid"] = company_id
        return self._post(c_id=QQSD_SAME_ODDS_HISTORY_ENDPOINT, body=body)

    def odds_trend(self, fid: str, *, company_id: str | None = None) -> dict[str, Any]:
        body = {"fid": fid}
        if company_id:
            body["companyid"] = company_id
        return self._post(c_id=QQSD_ODDS_TREND_ENDPOINT, body=body)

    def bifa_trade(self, fid: str) -> dict[str, Any]:
        return self._post(c_id=QQSD_BIFA_TRADE_ENDPOINT, body={"fid": fid})

    def company_list(self) -> dict[str, Any]:
        if self._company_list_cache is None:
            self._company_list_cache = self._post(c_id=QQSD_COMPANY_LIST_ENDPOINT)
        return self._company_list_cache

    def odds_change_list(self) -> dict[str, Any]:
        if self._odds_change_list_cache is None:
            self._odds_change_list_cache = self._post(c_id=QQSD_ODDS_CHANGE_LIST_ENDPOINT)
        return self._odds_change_list_cache

    def lingsi(self, fid: str) -> dict[str, Any]:
        return self._new_api_post("home/lingsi/", body={"fid": fid})

    def vote_infos(self, fid: str) -> dict[str, Any]:
        return self._new_api_post("team/voteinfos/", body={"fid": fid})

    def europe_odds_history(self, fid: str, *, company_id: str | None = None) -> dict[str, Any]:
        body = {"fid": fid}
        if company_id:
            body["companyid"] = company_id
        return self._post(c_id=QQSD_EUROPE_ODDS_HISTORY_ENDPOINT, body=body)

    def asian_odds_history(
        self,
        fid: str,
        *,
        company_id: str,
        vsdate: str | None = None,
        t: str | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"fid": fid, "cid": company_id, "t": t or "2"}
        if vsdate:
            body["vsdate"] = vsdate
        if extra_params:
            body.update({key: value for key, value in extra_params.items() if value not in (None, "")})
        return self._post(c_id=QQSD_ASIAN_HANDICAP_ENDPOINT, body=body)

    def over_under_odds_history(
        self,
        fid: str,
        *,
        company_id: str,
        vsdate: str | None = None,
        t: str | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"fid": fid, "cid": company_id, "t": t or "3"}
        if vsdate:
            body["vsdate"] = vsdate
        if extra_params:
            body.update({key: value for key, value in extra_params.items() if value not in (None, "")})
        return self._post(c_id=QQSD_OVER_UNDER_ENDPOINT, body=body)

    def _handicap_totals_odds_history(
        self,
        fid: str,
        *,
        company_id: str,
        market_type: str,
        vsdate: str | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"fid": fid, "cid": company_id, "t": market_type}
        if vsdate:
            body["vsdate"] = vsdate
        if extra_params:
            body.update({key: value for key, value in extra_params.items() if value not in (None, "")})
        return self._post(c_id=QQSD_HANDICAP_TOTALS_ODDS_HISTORY_ENDPOINT, body=body)

    def odds_summary(self, fid: str) -> dict[str, Any]:
        return self._post(c_id=QQSD_ODDS_SUMMARY_ENDPOINT, body={"fid": fid})

    def odds_heat(self, fid: str) -> dict[str, Any]:
        return self._post(c_id=QQSD_ODDS_HEAT_ENDPOINT, body={"fid": fid})

    def handicap_europe_odds(self, fid: str) -> dict[str, Any]:
        return self._post(c_id=QQSD_HANDICAP_EUROPE_ODDS_ENDPOINT, body={"fid": fid})

    def league_stats(self, fid: str) -> dict[str, Any]:
        return self._post(c_id=QQSD_LEAGUE_STATS_ENDPOINT, body={"fid": fid})

    def match_odds_timeline_bundle(
        self,
        fid: str,
        *,
        vsdate: str | None = None,
        company_name: str | None = "Pinnacle",
        company_id: str | None = None,
        markets: list[str] | None = None,
    ) -> dict[str, Any]:
        return build_match_odds_timeline_bundle(
            self,
            fid,
            vsdate=vsdate,
            company_name=company_name,
            company_id=company_id,
            markets=markets,
        )

    def match_analysis_bundle(self, fid: str) -> dict[str, Any]:
        bundle: dict[str, Any] = {
            "fid": fid,
            "detail": None,
            "standings": None,
            "extremes": None,
            "analysis_tools": None,
            "injury_preview": None,
            "lineup_simple": None,
            "lineup_detail": None,
            "lineup_full": None,
            "lingsi": None,
            "vote_infos": None,
            "europe_odds_history": None,
            "odds_summary": None,
            "odds_heat": None,
            "handicap_europe_odds": None,
            "league_stats": None,
            "betting_distribution": None,
            "same_odds_history": None,
            "odds_trend": None,
            "bifa_trade": None,
            "company_list": None,
            "odds_change_list": None,
            "errors": [],
        }
        for key, loader in (
            ("detail", lambda: self.match_detail(fid)),
            ("standings", lambda: self.standings(fid)),
            ("extremes", self.extreme_data),
            ("analysis_tools", self.analysis_tools),
            ("injury_preview", lambda: self.injury_preview(fid)),
            ("lineup_simple", lambda: self.lineup_simple(fid)),
            ("lineup_detail", lambda: self.lineup_detail(fid)),
            ("lineup_full", lambda: self.lineup_full(fid)),
            ("lingsi", lambda: self.lingsi(fid)),
            ("vote_infos", lambda: self.vote_infos(fid)),
            ("europe_odds_history", lambda: self.europe_odds_history(fid)),
            ("odds_summary", lambda: self.odds_summary(fid)),
            ("odds_heat", lambda: self.odds_heat(fid)),
            ("handicap_europe_odds", lambda: self.handicap_europe_odds(fid)),
            ("league_stats", lambda: self.league_stats(fid)),
            ("betting_distribution", lambda: self.betting_distribution(fid)),
            ("same_odds_history", lambda: self.same_odds_history(fid)),
            ("odds_trend", lambda: self.odds_trend(fid)),
            ("bifa_trade", lambda: self.bifa_trade(fid)),
            ("company_list", self.company_list),
            ("odds_change_list", self.odds_change_list),
        ):
            try:
                bundle[key] = loader()
            except Exception as exc:
                bundle["errors"].append({"key": key, "error": f"{type(exc).__name__}:{exc}"})
        return bundle

    def company_europe_odds(self, fid: str, *, c_id: str | None = None) -> list[OddsSnapshot]:
        endpoint = c_id or QQSD_EUROPE_ODDS_ENDPOINT
        payload = self._post(endpoint, {"fid": fid})
        return map_company_europe_odds(payload, fid=fid, endpoint=endpoint)

    def company_asian_odds(self, fid: str, *, c_id: str | None = None) -> list[OddsSnapshot]:
        endpoint = c_id or QQSD_ASIAN_HANDICAP_ENDPOINT
        payload = self._post(endpoint, {"fid": fid})
        return map_company_asian_odds(payload, fid=fid, endpoint=endpoint)

    def company_over_under_odds(self, fid: str, *, c_id: str | None = None) -> list[OddsSnapshot]:
        endpoint = c_id or QQSD_OVER_UNDER_ENDPOINT
        payload = self._post(endpoint, {"fid": fid})
        return map_company_over_under_odds(payload, fid=fid, endpoint=endpoint)

    def company_summary_odds(self, fid: str, *, c_id: str | None = None) -> list[OddsSnapshot]:
        endpoint = c_id or QQSD_ODDS_SUMMARY_ENDPOINT
        payload = self._post(endpoint, {"fid": fid})
        return map_company_summary_odds(payload, fid=fid, endpoint=endpoint)

    def company_handicap_europe_odds(self, fid: str, *, c_id: str | None = None) -> list[OddsSnapshot]:
        endpoint = c_id or QQSD_HANDICAP_EUROPE_ODDS_ENDPOINT
        payload = self._post(endpoint, {"fid": fid})
        return map_company_europe_odds(payload, fid=fid, endpoint=endpoint)

    def fixtures(self, date: str, *, cid: str | None = None, stid: str = "1") -> list[Match]:
        archive_payloads: list[dict[str, Any]] = []
        try:
            archive_payloads = self.archive_score_pages(date)
        except DataSourceError:
            archive_payloads = []

        score_payloads: list[dict[str, Any]] = []
        try:
            score_payloads = self.score_lists(cids=_score_cids(cid), stid=stid)
        except DataSourceError:
            if not archive_payloads:
                raise

        matches = _dedupe_matches(
            [
                *(
                    match
                    for payload in archive_payloads
                    for match in map_archive_score_matches(payload)
                    if _inside_match_date_window(match.kickoff_at, date)
                ),
                *(
                    match
                    for payload in score_payloads
                    for match in map_score_list_matches(payload)
                    if _inside_match_date_window(match.kickoff_at, date)
                ),
            ]
        )
        return matches

    def odds(
        self,
        date: str | None = None,
        *,
        cid: str | None = None,
        stid: str = "1",
        match_ids: set[str] | None = None,
    ) -> list[OddsSnapshot]:
        archive_payloads: list[dict[str, Any]] = []
        if date is not None:
            try:
                archive_payloads = self.archive_score_pages(date)
            except DataSourceError:
                archive_payloads = []

        score_payloads: list[dict[str, Any]] = []
        try:
            score_payloads = self.score_lists(cids=_score_cids(cid), stid=stid)
        except DataSourceError:
            if not archive_payloads:
                raise

        discovered_match_ids = {
            match.id
            for payload in [*archive_payloads, *score_payloads]
            for match in [*map_archive_score_matches(payload), *map_score_list_matches(payload)]
            if date is None or _inside_match_date_window(match.kickoff_at, date)
        }
        scoped_match_ids = discovered_match_ids
        if match_ids is not None:
            scoped_match_ids = set(match_ids)

        snapshots = _dedupe_odds(
            [
                *(snapshot for payload in archive_payloads for snapshot in map_archive_score_odds(payload)),
                *(snapshot for payload in score_payloads for snapshot in map_score_list_odds(payload)),
            ]
        )
        company_snapshots: list[OddsSnapshot] = []
        for match_id in scoped_match_ids:
            fid = match_id.removeprefix("qqsd:")
            try:
                company_snapshots.extend(self.company_europe_odds(fid))
            except DataSourceError:
                pass
            try:
                company_snapshots.extend(self.company_asian_odds(fid))
            except DataSourceError:
                pass
            try:
                company_snapshots.extend(self.company_over_under_odds(fid))
            except DataSourceError:
                pass
        snapshots = _dedupe_odds([*company_snapshots, *snapshots])
        if date is None:
            return [snapshot for snapshot in snapshots if match_ids is None or snapshot.match_id in scoped_match_ids]
        return [snapshot for snapshot in snapshots if snapshot.match_id in scoped_match_ids]

    def _post(self, c_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        c_ck = self.context.api_key or ""
        body = dict(body or {})
        if c_ck:
            body["c_ck"] = c_ck
        body["c_key"] = compute_ckey(c_id)
        params = {
                "c_id": c_id,
                "c_type": C_TYPE,
                "c_cpid": C_CPID,
                "suid": _env("QQSD_SUID", "1104a89793843366217"),
                "quid": _env("QQSD_QUID", "386401"),
        }
        body_str = urllib.parse.urlencode(body) + "&"
        try:
            with httpx.Client(timeout=self.context.settings.ingestion.request_timeout_seconds) as client:
                response = client.post(
                    f"{self.context.source.base_url}/api/index.php",
                    params=params,
                    headers=_headers(),
                    content=body_str,
                )
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DataSourceError(f"qqsd_request_failed:{type(exc).__name__}") from exc
        if not isinstance(payload, dict):
            raise DataSourceError("qqsd_invalid_response")
        if payload.get("code") not in {100, "100"}:
            raise DataSourceError(f"qqsd_response_code:{payload.get('code')}:{payload.get('msg')}")
        return payload

    def _new_api_post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self.context.api_key or ""
        body = dict(body or {})
        if token:
            body["token"] = token
        params = {
            "suid": _env("QQSD_SUID", "1104a89793843366217"),
            "quid": _env("QQSD_QUID", "386401"),
            "c_type": C_TYPE,
            "c_cpid": C_CPID,
        }
        body_str = urllib.parse.urlencode(body) + "&"
        api_path = path.strip("/") + "/"
        try:
            with httpx.Client(timeout=self.context.settings.ingestion.request_timeout_seconds) as client:
                response = client.post(
                    f"https://p.qqshidao.com/index/{api_path}",
                    params=params,
                    headers=_headers(),
                    content=body_str,
                )
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DataSourceError(f"qqsd_new_api_request_failed:{type(exc).__name__}") from exc
        if not isinstance(payload, dict):
            raise DataSourceError("qqsd_new_api_invalid_response")
        if payload.get("code") not in {100, "100", 1, "1"}:
            raise DataSourceError(f"qqsd_new_api_response_code:{payload.get('code')}:{payload.get('msg')}")
        return payload


def compute_ckey(c_id: str | int) -> str:
    plaintext = f"{c_id}{C_CPID}{C_TYPE}{C_KEY_SECRET}"
    return hashlib.md5(plaintext.encode("utf-8")).hexdigest()


def map_score_list_matches(payload: dict[str, Any]) -> list[Match]:
    rows = _score_rows(payload)
    matches: list[Match] = []
    for row in rows:
        fid = str(row.get("fid") or "")
        if not fid:
            continue
        kickoff = _parse_datetime(str(row.get("vsdate") or ""))
        if kickoff is None:
            continue
        matches.append(
            Match(
                id=f"qqsd:{fid}",
                league=str(row.get("lname") or "Unknown"),
                home_team=str(row.get("hname") or "Unknown Home"),
                away_team=str(row.get("aname") or "Unknown Away"),
                kickoff_at=kickoff,
                status=_map_status(str(row.get("status") or "0")),
                data_completeness=0.70,
                home_score=_safe_int(row.get("hscore")),
                away_score=_safe_int(row.get("ascore")),
                external_ids={
                    "qqsd_fid": fid,
                    "qqsd_lid": str(row.get("lid") or ""),
                    "qqsd_home_team": str(row.get("hid") or ""),
                    "qqsd_away_team": str(row.get("aid") or ""),
                },
            )
        )
    return matches


def map_archive_score_matches(payload: dict[str, Any]) -> list[Match]:
    matches: list[Match] = []
    for row in _archive_rows(payload):
        fixture = row.get("fixture") if isinstance(row.get("fixture"), dict) else row
        fid = str(fixture.get("fixtureid") or row.get("fixtureid") or fixture.get("fid") or row.get("fid") or "")
        if not fid:
            continue
        kickoff = _parse_datetime(str(fixture.get("vsdate") or row.get("vsdate") or ""))
        if kickoff is None:
            continue
        home_score = _safe_int(fixture.get("hscore") or row.get("hscore"))
        away_score = _safe_int(fixture.get("ascore") or row.get("ascore"))
        matches.append(
            Match(
                id=f"qqsd:{fid}",
                league=str(
                    fixture.get("simplegbname")
                    or fixture.get("matchgbname")
                    or fixture.get("lname")
                    or row.get("lname")
                    or "Unknown"
                ),
                home_team=str(fixture.get("homesxname") or fixture.get("hname") or row.get("hname") or "Unknown Home"),
                away_team=str(fixture.get("awaysxname") or fixture.get("aname") or row.get("aname") or "Unknown Away"),
                kickoff_at=kickoff,
                status=_map_archive_status(row, home_score=home_score, away_score=away_score),
                data_completeness=0.78,
                home_score=home_score,
                away_score=away_score,
                external_ids={
                    "qqsd_fid": fid,
                    "qqsd_fixtureid": fid,
                    "qqsd_lid": str(fixture.get("matchid") or row.get("matchid") or ""),
                    "qqsd_home_team": str(fixture.get("hometeamid") or fixture.get("hid") or row.get("hid") or ""),
                    "qqsd_away_team": str(fixture.get("awayteamid") or fixture.get("aid") or row.get("aid") or ""),
                    "qqsd_home_standing": str(fixture.get("homestanding") or ""),
                    "qqsd_away_standing": str(fixture.get("awaystanding") or ""),
                    "qqsd_jczq_expect": str(row.get("jczq_expect") or ""),
                },
            )
        )
    return matches


def map_match_detail_match(payload: dict[str, Any], fallback: Match) -> Match:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return fallback
    fid = str(data.get("fid") or fallback.external_ids.get("qqsd_fid") or fallback.id.removeprefix("qqsd:"))
    kickoff = _parse_datetime(str(data.get("vsdate") or "")) or fallback.kickoff_at
    external_ids = dict(fallback.external_ids)
    external_ids.update(
        {
            "qqsd_fid": fid,
            "qqsd_lid": str(data.get("matchid") or data.get("lid") or external_ids.get("qqsd_lid") or ""),
            "qqsd_sid": str(data.get("sid") or ""),
            "qqsd_season": str(data.get("season") or ""),
            "qqsd_seasonyear": str(data.get("seasonyear") or ""),
            "qqsd_matchround": str(data.get("matchround") or ""),
            "qqsd_stage": str(data.get("stagename") or ""),
            "qqsd_home_team": str(data.get("hid") or external_ids.get("qqsd_home_team") or ""),
            "qqsd_away_team": str(data.get("aid") or external_ids.get("qqsd_away_team") or ""),
            "qqsd_home_full_name": str(data.get("hteamname") or ""),
            "qqsd_away_full_name": str(data.get("ateamname") or ""),
            "qqsd_home_standing": str(data.get("hstanding") or ""),
            "qqsd_away_standing": str(data.get("astanding") or ""),
            "qqsd_neutral": str(data.get("isneutrality") or ""),
            "qqsd_tags": str(data.get("tags") or ""),
        }
    )
    return Match(
        id=f"qqsd:{fid}",
        league=str(data.get("lname") or fallback.league),
        home_team=str(data.get("hname") or fallback.home_team),
        away_team=str(data.get("aname") or fallback.away_team),
        kickoff_at=kickoff,
        status=_map_status(str(data.get("status") or fallback.status.value)),
        data_completeness=max(fallback.data_completeness, 0.86),
        season=_season_from_detail(data, fallback.season),
        country=fallback.country,
        home_score=_safe_int(data.get("hscore")) if data.get("hscore") not in {None, ""} else fallback.home_score,
        away_score=_safe_int(data.get("ascore")) if data.get("ascore") not in {None, ""} else fallback.away_score,
        external_ids={key: value for key, value in external_ids.items() if value is not None},
    )


def build_context_finding(
    match: Match,
    *,
    detail_payload: dict[str, Any] | None = None,
    standings_payload: dict[str, Any] | None = None,
    extreme_payload: dict[str, Any] | None = None,
    tools_payload: dict[str, Any] | None = None,
    injury_preview_payload: dict[str, Any] | None = None,
    lineup_simple_payload: dict[str, Any] | None = None,
    lineup_detail_payload: dict[str, Any] | None = None,
    lineup_full_payload: dict[str, Any] | None = None,
    lingsi_payload: dict[str, Any] | None = None,
    vote_payload: dict[str, Any] | None = None,
    europe_odds_history_payload: dict[str, Any] | None = None,
    odds_summary_payload: dict[str, Any] | None = None,
    odds_heat_payload: dict[str, Any] | None = None,
    handicap_europe_payload: dict[str, Any] | None = None,
    league_stats_payload: dict[str, Any] | None = None,
    betting_distribution_payload: dict[str, Any] | None = None,
    same_odds_history_payload: dict[str, Any] | None = None,
    odds_trend_payload: dict[str, Any] | None = None,
    bifa_trade_payload: dict[str, Any] | None = None,
    company_list_payload: dict[str, Any] | None = None,
    odds_change_list_payload: dict[str, Any] | None = None,
    odds_timeline_payload: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> AgentFinding | None:
    fid = match.external_ids.get("qqsd_fid") or match.id.removeprefix("qqsd:")
    standings = (standings_payload or {}).get("data") or {}
    if not isinstance(standings, dict):
        standings = {}
    extremes = _extremes_for_fid(extreme_payload, fid)
    tools = _analysis_tool_titles(tools_payload)
    match_context = _match_context_summary(
        injury_preview_payload=injury_preview_payload,
        lineup_simple_payload=lineup_simple_payload,
        lineup_detail_payload=lineup_detail_payload,
        lineup_full_payload=lineup_full_payload,
    )
    lingsi = (lingsi_payload or {}).get("data")
    votes = _vote_rows(vote_payload)
    odds_context = _odds_context_summary(
        europe_odds_history_payload=europe_odds_history_payload,
        odds_summary_payload=odds_summary_payload,
        odds_heat_payload=odds_heat_payload,
        handicap_europe_payload=handicap_europe_payload,
        league_stats_payload=league_stats_payload,
        betting_distribution_payload=betting_distribution_payload,
        same_odds_history_payload=same_odds_history_payload,
        odds_trend_payload=odds_trend_payload,
        bifa_trade_payload=bifa_trade_payload,
        company_list_payload=company_list_payload,
        odds_change_list_payload=odds_change_list_payload,
    )
    timeline_summary = _odds_timeline_summary(odds_timeline_payload)
    if (
        not standings
        and not detail_payload
        and not extremes
        and not tools
        and not match_context
        and not lingsi
        and not votes
        and not odds_context
        and not timeline_summary
    ):
        return None
    summary = _context_summary(
        match,
        standings,
        extremes,
        tools=tools,
        match_context=match_context,
        lingsi=lingsi,
        votes=votes,
        odds_context={**odds_context, **timeline_summary},
    )
    evidence_sources = [
        EvidenceSource(title="QQSD match detail", publisher="QQSD"),
        EvidenceSource(title="QQSD standings and team power", publisher="QQSD"),
    ]
    if extremes:
        evidence_sources.append(EvidenceSource(title="QQSD extreme indicators", publisher="QQSD"))
    if votes:
        evidence_sources.append(EvidenceSource(title="QQSD user vote heat", publisher="QQSD"))
    if tools or lingsi:
        evidence_sources.append(EvidenceSource(title="QQSD analysis tools", publisher="QQSD"))
    if match_context:
        evidence_sources.append(EvidenceSource(title="QQSD injury and lineup context", publisher="QQSD"))
    if odds_context:
        evidence_sources.append(EvidenceSource(title="QQSD odds context", publisher="QQSD"))
    if timeline_summary:
        evidence_sources.append(EvidenceSource(title="QQSD odds timeline", publisher="QQSD"))
    return AgentFinding(
        id=f"{match.id}:qqsd-context",
        match_id=match.id,
        agent_name="qqsd_full_context",
        summary=summary,
        evidence_sources=evidence_sources,
        confidence=0.68 if standings and (votes or extremes) else 0.62 if standings else 0.52,
        risk_tags=[],
        score_delta=0.0,
        payload={
            "provider": "qqsd",
            "fid": fid,
            "detail": (detail_payload or {}).get("data"),
            "standings": standings,
            "extremes": extremes,
            "analysis_tools": tools,
            "match_context": match_context,
            "injury_preview": _payload_data(injury_preview_payload),
            "lineup_simple": _payload_data(lineup_simple_payload),
            "lineup_detail": _payload_data(lineup_detail_payload),
            "lineup_full": _payload_data(lineup_full_payload),
            "lingsi": lingsi,
            "vote_infos": votes,
            "odds_context": odds_context,
            "europe_odds_history": _payload_data(europe_odds_history_payload),
            "odds_summary": _payload_data(odds_summary_payload),
            "odds_heat": _payload_data(odds_heat_payload),
            "handicap_europe_odds": _payload_data(handicap_europe_payload),
            "league_stats": _payload_data(league_stats_payload),
            "betting_distribution": _payload_data(betting_distribution_payload),
            "same_odds_history": _payload_data(same_odds_history_payload),
            "odds_trend": _payload_data(odds_trend_payload),
            "bifa_trade": _payload_data(bifa_trade_payload),
            "company_list": _payload_data(company_list_payload),
            "odds_change_list": _payload_data(odds_change_list_payload),
            "odds_timeline": odds_timeline_payload,
            "qqsd_errors": errors or [],
        },
    )


def find_league_entry(
    payload: dict[str, Any],
    *,
    identifiers: list[str],
    season: int | None = None,
) -> dict[str, Any] | None:
    normalized = {_normalize_identifier(value) for value in identifiers if value}
    for item in _league_entries(payload):
        item_names = {
            _normalize_identifier(str(item.get(key) or ""))
            for key in ("MATCHID", "SEASONGBNAME", "MATCHENNAME", "MATCHGBNAME", "SIMPLEGBNAME")
        }
        if season is not None and str(item.get("SEASONYEAR") or "") != str(season):
            continue
        if any(
            identifier == item_name or identifier in item_name or item_name in identifier
            for identifier in normalized
            for item_name in item_names
            if identifier and item_name
        ):
            return item
    return None


def map_score_list_odds(payload: dict[str, Any]) -> list[OddsSnapshot]:
    snapshots: list[OddsSnapshot] = []
    for row in _score_rows(payload):
        fid = str(row.get("fid") or "")
        if not fid:
            continue
        home = _asian_price(row.get("w"))
        away = _asian_price(row.get("l"))
        line = str(row.get("pshow") or row.get("p") or "").strip() or None
        odds = {key: value for key, value in {"HOME": home, "AWAY": away}.items() if value}
        if len(odds) >= 2:
            snapshots.append(
                OddsSnapshot(
                    id=f"qqsd:{fid}:qqsd:asian_handicap:{line or 'main'}",
                    match_id=f"qqsd:{fid}",
                    market_type="asian_handicap",
                    line=line,
                    source="qqsd",
                    bookmaker="QQSD",
                    collected_at=datetime.utcnow(),
                    outcome_odds=odds,
                    best_price=dict(odds),
                )
            )
    return snapshots


def map_archive_score_odds(payload: dict[str, Any]) -> list[OddsSnapshot]:
    snapshots: list[OddsSnapshot] = []
    for row in _archive_rows(payload):
        fixture = row.get("fixture") if isinstance(row.get("fixture"), dict) else row
        fid = str(fixture.get("fixtureid") or row.get("fixtureid") or fixture.get("fid") or row.get("fid") or "")
        if not fid:
            continue
        raw_odds = row.get("odds")
        if not isinstance(raw_odds, list) or len(raw_odds) < 3:
            continue
        odds = {
            key: value
            for key, value in {
                "HOME": _decimal_price(raw_odds[0]),
                "DRAW": _decimal_price(raw_odds[1]),
                "AWAY": _decimal_price(raw_odds[2]),
            }.items()
            if value
        }
        if len(odds) != 3:
            continue
        snapshots.append(
            OddsSnapshot(
                id=f"qqsd:{fid}:qqsd:1x2:archive",
                match_id=f"qqsd:{fid}",
                market_type="1x2",
                line=None,
                source="qqsd",
                bookmaker="QQSD",
                collected_at=datetime.utcnow(),
                outcome_odds=odds,
                best_price=dict(odds),
            )
        )
    return snapshots


def map_archive_score_historical_rows(
    payload: dict[str, Any],
    *,
    league: str,
    season: str,
    league_aliases: set[str] | None = None,
) -> list[HistoricalMatchRow]:
    aliases = {alias.strip().lower() for alias in (league_aliases or set()) if alias and alias.strip()}
    odds_by_match = {
        snapshot.match_id: snapshot
        for snapshot in map_archive_score_odds(payload)
        if getattr(snapshot.market_type, "value", snapshot.market_type) == "1x2"
    }
    rows: list[HistoricalMatchRow] = []
    for match in map_archive_score_matches(payload):
        if aliases and match.league.strip().lower() not in aliases:
            continue
        if match.home_score is None or match.away_score is None:
            continue
        snapshot = odds_by_match.get(match.id)
        odds = snapshot.outcome_odds if snapshot is not None else {}
        best_price = snapshot.best_price if snapshot is not None else {}
        market_average = snapshot.market_average if snapshot is not None else {}
        row_id = f"qqsd:{league}:{season}:{match.id.removeprefix('qqsd:')}"
        rows.append(
            HistoricalMatchRow(
                id=row_id,
                league=league,
                season=season,
                date=match.kickoff_at,
                home_team=match.home_team,
                away_team=match.away_team,
                home_goals=match.home_score,
                away_goals=match.away_score,
                home_odds=odds.get("HOME"),
                draw_odds=odds.get("DRAW"),
                away_odds=odds.get("AWAY"),
                max_home_odds=best_price.get("HOME") or odds.get("HOME"),
                max_draw_odds=best_price.get("DRAW") or odds.get("DRAW"),
                max_away_odds=best_price.get("AWAY") or odds.get("AWAY"),
                avg_home_odds=market_average.get("HOME") or odds.get("HOME"),
                avg_draw_odds=market_average.get("DRAW") or odds.get("DRAW"),
                avg_away_odds=market_average.get("AWAY") or odds.get("AWAY"),
                closing_home_odds=best_price.get("HOME") or odds.get("HOME"),
                closing_draw_odds=best_price.get("DRAW") or odds.get("DRAW"),
                closing_away_odds=best_price.get("AWAY") or odds.get("AWAY"),
            )
        )
    return rows


def map_finished_match_asian_historical_row(
    match: Match,
    odds_snapshots: list[OddsSnapshot],
    *,
    league: str,
    season: str,
) -> HistoricalMatchRow | None:
    if match.status is not MatchStatus.finished:
        return None
    if match.home_score is None or match.away_score is None:
        return None
    asian_snapshots = [
        snapshot
        for snapshot in odds_snapshots
        if snapshot.match_id == match.id
        and getattr(snapshot.market_type, "value", snapshot.market_type) == "asian_handicap"
        and snapshot.line
    ]
    signed_snapshots = [snapshot for snapshot in asian_snapshots if str(snapshot.line).strip().startswith(("+", "-"))]
    candidates = signed_snapshots or asian_snapshots
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda snapshot: (
            1 if str(snapshot.line).strip().startswith(("+", "-")) else 0,
            len(snapshot.outcome_odds),
            max(snapshot.best_price.values() or snapshot.outcome_odds.values() or [0.0]),
        ),
    )
    line = _parse_asian_line_value(best.line)
    if line is None:
        return None
    home_odds = best.outcome_odds.get("HOME")
    away_odds = best.outcome_odds.get("AWAY")
    if not home_odds or not away_odds:
        return None
    market_average = best.market_average or {}
    best_price = best.best_price or {}
    row_id = f"qqsd-local:{league}:{season}:{match.id.removeprefix('qqsd:')}:ah"
    return HistoricalMatchRow(
        id=row_id,
        league=league,
        season=season,
        date=match.kickoff_at,
        home_team=match.home_team,
        away_team=match.away_team,
        home_goals=match.home_score,
        away_goals=match.away_score,
        ah_line=line,
        ah_home_odds=home_odds,
        ah_away_odds=away_odds,
        avg_ah_home_odds=market_average.get("HOME") or home_odds,
        avg_ah_away_odds=market_average.get("AWAY") or away_odds,
        closing_ah_home_odds=best_price.get("HOME") or home_odds,
        closing_ah_away_odds=best_price.get("AWAY") or away_odds,
    )


def map_company_europe_odds(
    payload: dict[str, Any],
    *,
    fid: str | None = None,
    endpoint: str = "company_europe_odds",
) -> list[OddsSnapshot]:
    rows = _company_odds_rows(payload)
    line = _company_odds_line(payload)
    return _company_europe_rows_to_snapshots(rows, fid=fid, endpoint=endpoint, line=line)


def map_company_asian_odds(
    payload: dict[str, Any],
    *,
    fid: str | None = None,
    endpoint: str = "company_asian_odds",
) -> list[OddsSnapshot]:
    rows = _company_asian_odds_rows(payload)
    return _company_asian_rows_to_snapshots(rows, fid=fid, endpoint=endpoint)


def map_company_over_under_odds(
    payload: dict[str, Any],
    *,
    fid: str | None = None,
    endpoint: str = "company_over_under_odds",
) -> list[OddsSnapshot]:
    rows = _company_total_odds_rows(payload)
    return _company_total_rows_to_snapshots(rows, fid=fid, endpoint=endpoint)


def map_handicap_totals_odds_history_rows(
    payload: dict[str, Any] | None,
    *,
    market: str,
) -> list[dict[str, Any]]:
    normalized_market = market.strip().lower().replace("-", "_")
    rows = _payload_rows_deep(payload)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if normalized_market == "over_under":
            compact = _normalize_total_history_row(row)
        else:
            compact = _normalize_asian_history_row(row)
        if compact:
            compact["raw"] = row
            normalized.append(compact)
    return normalized


def qqsd_current_odds_available(row: dict[str, Any] | None, *, market: str) -> bool:
    if not isinstance(row, dict):
        return False
    normalized_market = market.strip().lower().replace("-", "_")
    if normalized_market == "asian_handicap":
        odds_keys = ("home", "away", "win", "lost", "w", "l", "homeodds", "awayodds", "winodds", "lostodds")
    elif normalized_market == "over_under":
        odds_keys = ("big", "small", "over", "under", "home", "away", "win", "lost", "winodds", "lostodds")
    else:
        odds_keys = ("win", "draw", "lost", "home", "same", "away", "w", "d", "l")

    candidates: list[dict[str, Any]] = []
    for key in ("end", "current", "first"):
        value = row.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    candidates.append(row)

    for candidate in candidates:
        if any(str(candidate.get(key) or "").strip() for key in odds_keys):
            return True
    return False


def qqsd_history_availability(history_row_count: int, *, current_available: bool) -> dict[str, str | None]:
    if history_row_count > 0:
        return {
            "history_availability": "history_available",
            "history_issue": None,
            "history_note": "QQSD returned odds timeline rows for the selected company and market.",
        }
    if current_available:
        return {
            "history_availability": "history_empty_current_available",
            "history_issue": "history_rows_empty",
            "history_note": (
                "QQSD returned no timeline rows while current company odds are available. "
                "Treat this as lower confidence or a configurable major-match blocker, not as endpoint failure."
            ),
        }
    return {
        "history_availability": "history_missing_current_missing",
        "history_issue": "current_odds_missing",
        "history_note": "Selected company has no usable current odds and QQSD returned no timeline rows.",
    }


def build_match_odds_timeline_bundle(
    client: QQSDClient,
    fid: str,
    *,
    vsdate: str | None = None,
    company_name: str | None = "Pinnacle",
    company_id: str | None = None,
    markets: list[str] | None = None,
) -> dict[str, Any]:
    normalized_markets = [_normalize_market_key(market) for market in (markets or ["1x2", "asian_handicap", "over_under"])]
    bundle: dict[str, Any] = {
        "fid": fid,
        "provider": "qqsd",
        "company_filter": {"company_id": company_id, "company_name": company_name},
        "vsdate": vsdate,
        "markets": {},
        "issues": [],
    }
    for market in normalized_markets:
        if market not in QQSD_ODDS_TIMELINE_CAPABILITIES:
            bundle["issues"].append(f"unsupported_market:{market}")
            continue
        bundle["markets"][market] = _build_market_timeline_context(
            client,
            fid,
            market=market,
            vsdate=vsdate,
            company_name=company_name,
            company_id=company_id,
        )
    bundle["summary"] = _timeline_bundle_summary(bundle["markets"])
    bundle["status"] = "ready" if not bundle["issues"] else "partial"
    return bundle


def _build_market_timeline_context(
    client: QQSDClient,
    fid: str,
    *,
    market: str,
    vsdate: str | None,
    company_name: str | None,
    company_id: str | None,
) -> dict[str, Any]:
    capability = QQSD_ODDS_TIMELINE_CAPABILITIES[market]
    result: dict[str, Any] = {
        "market": market,
        "company_endpoint": capability.get("company_endpoint"),
        "timeline_endpoint": capability.get("timeline_endpoint"),
        "timeline_status": capability.get("status"),
        "verification": capability.get("verification"),
        "status": "ok",
        "issues": [],
    }
    try:
        company_payload = client._post(str(capability["company_endpoint"]), {"fid": fid})
        company_rows = _company_rows_for_market(company_payload, market)
        selected_company, selected_row = _select_company_row(
            company_rows,
            company_id=company_id,
            company_name=company_name,
        )
        result.update(
            {
                "available_company_count": len(company_rows),
                "available_companies": [_compact_company_identity(row) for row in company_rows[:20]],
                "company": selected_company,
                "current": _compact_company_current_row(selected_row),
            }
        )
        if selected_company is None or selected_row is None:
            result["status"] = "company_required" if not (company_id or company_name) else "company_not_found"
            result["issues"].append(result["status"])
            result.update(qqsd_history_availability(0, current_available=False))
            return result
        current_available = qqsd_current_odds_available(selected_row, market=market)
        result["current_available"] = current_available
        history_payload = _fetch_market_history_payload(
            client,
            fid,
            market=market,
            company_id=selected_company["id"],
            vsdate=vsdate,
        )
        history_rows = _history_rows_for_market(history_payload, market)
        availability = qqsd_history_availability(len(history_rows), current_available=current_available)
        result.update(
            {
                "history_row_count": len(history_rows),
                "history_rows_sample": [_compact_history_row(row) for row in history_rows[:5]],
                "first_row": _compact_history_row(history_rows[0]) if history_rows else None,
                "last_row": _compact_history_row(history_rows[-1]) if history_rows else None,
                **availability,
            }
        )
        if availability.get("history_issue"):
            result["issues"].append(str(availability["history_issue"]))
        return result
    except Exception as exc:
        return {
            **result,
            "status": "error",
            "current_available": False,
            "history_row_count": 0,
            "history_availability": "history_request_error",
            "history_issue": f"{type(exc).__name__}:{exc}",
            "history_note": "QQSD market timeline request failed.",
            "issues": [f"history_request_error:{type(exc).__name__}"],
        }


def _normalize_market_key(market: str) -> str:
    normalized = market.strip().lower().replace("-", "_")
    if normalized in {"h2h", "europe", "europe_odds"}:
        return "1x2"
    if normalized in {"ah", "asian", "handicap"}:
        return "asian_handicap"
    if normalized in {"ou", "total", "totals", "overunder"}:
        return "over_under"
    return normalized


def _company_rows_for_market(payload: dict[str, Any], market: str) -> list[dict[str, Any]]:
    if market == "asian_handicap":
        return _company_asian_odds_rows(payload)
    if market == "over_under":
        return _company_total_odds_rows(payload)
    return _company_odds_rows(payload)


def _select_company_row(
    rows: list[dict[str, Any]],
    *,
    company_id: str | None,
    company_name: str | None,
) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    selected: dict[str, Any] | None = None
    if company_id:
        selected = next((row for row in rows if _compact_company_identity(row)["id"] == company_id), None)
    elif company_name:
        needle = company_name.lower()
        selected = next((row for row in rows if needle in _compact_company_identity(row)["name"].lower()), None)
    else:
        selected = rows[0] if rows else None
    if selected is None:
        return None, None
    return _compact_company_identity(selected), selected


def _fetch_market_history_payload(
    client: QQSDClient,
    fid: str,
    *,
    market: str,
    company_id: str,
    vsdate: str | None,
) -> dict[str, Any]:
    if market == "asian_handicap":
        return client.asian_odds_history(fid, company_id=company_id, vsdate=vsdate)
    if market == "over_under":
        return client.over_under_odds_history(fid, company_id=company_id, vsdate=vsdate)
    return client.europe_odds_history(fid, company_id=company_id)


def _history_rows_for_market(payload: dict[str, Any], market: str) -> list[dict[str, Any]]:
    if market in {"asian_handicap", "over_under"}:
        return map_handicap_totals_odds_history_rows(payload, market=market)
    return _payload_rows(payload)


def _compact_company_identity(row: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(row.get("id") or row.get("companyid") or row.get("cid") or ""),
        "name": _company_name(row),
    }


def _compact_company_current_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    compact: dict[str, Any] = _compact_company_identity(row)
    for key in ("first", "end", "current"):
        value = row.get(key)
        if isinstance(value, dict):
            compact[key] = _compact_dict(
                value,
                (
                    "win",
                    "draw",
                    "lost",
                    "home",
                    "away",
                    "big",
                    "small",
                    "handi",
                    "line",
                    "flat",
                    "pay",
                ),
            )
    return compact


def _compact_history_row(row: dict[str, Any]) -> dict[str, Any]:
    return _compact_dict(
        row,
        (
            "time",
            "updatetime",
            "date",
            "win",
            "draw",
            "lost",
            "home",
            "away",
            "over",
            "under",
            "big",
            "small",
            "handi",
            "line",
            "flat",
            "pay",
            "kwin",
            "kdraw",
            "klost",
        ),
    )


def _timeline_bundle_summary(markets: dict[str, Any]) -> dict[str, Any]:
    market_count = len(markets)
    history_available = sum(
        1 for item in markets.values() if item.get("history_availability") == "history_available"
    )
    current_available = sum(1 for item in markets.values() if item.get("current_available"))
    return {
        "market_count": market_count,
        "history_available_count": history_available,
        "current_available_count": current_available,
        "history_coverage_rate": round(history_available / market_count, 4) if market_count else 0.0,
        "current_coverage_rate": round(current_available / market_count, 4) if market_count else 0.0,
    }


def map_company_summary_odds(
    payload: dict[str, Any],
    *,
    fid: str | None = None,
    endpoint: str = "company_summary_odds",
) -> list[OddsSnapshot]:
    rows = _company_summary_rows(payload)
    snapshots: list[OddsSnapshot] = []
    snapshots.extend(_company_europe_rows_to_snapshots(rows, fid=fid, endpoint=endpoint))
    snapshots.extend(_company_asian_rows_to_snapshots(rows, fid=fid, endpoint=endpoint))
    snapshots.extend(_company_total_rows_to_snapshots(rows, fid=fid, endpoint=endpoint))
    return _dedupe_odds(snapshots)


def map_company_europe_odds_xml(xml_text: str, *, fid: str, endpoint: str = "ui_xml") -> list[OddsSnapshot]:
    rows: list[dict[str, Any]] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    for node in root.iter("node"):
        if not str(node.attrib.get("resource-id") or "").endswith("id/tv_company"):
            continue
        company = str(node.attrib.get("text") or "").strip()
        if not company:
            continue
        parent = _find_xml_parent(root, node)
        if parent is None:
            continue
        current = {
            "win": _xml_text_by_resource(parent, "tv_odd0011"),
            "draw": _xml_text_by_resource(parent, "tv_odd0012"),
            "lost": _xml_text_by_resource(parent, "tv_odd0013"),
        }
        first = {
            "win": _xml_text_by_resource(parent, "tv_odd001"),
            "draw": _xml_text_by_resource(parent, "tv_odd002"),
            "lost": _xml_text_by_resource(parent, "tv_odd003"),
        }
        rows.append({"name": company, "end": current, "first": first})
    return _company_europe_rows_to_snapshots(rows, fid=fid, endpoint=endpoint, line=None)



def _parse_asian_line_value(value: str | None) -> float | None:
    if value is None:
        return None
    raw = str(value).strip().replace("−", "-").replace("＋", "+")
    if not raw:
        return None
    sign = -1.0 if raw.startswith("-") else 1.0
    raw = raw.lstrip("+-").strip()
    if not raw:
        return None
    try:
        if "/" in raw:
            left, right = raw.split("/", 1)
            return round(sign * ((float(left) + float(right)) / 2.0), 4)
        return round(sign * float(raw), 4)
    except ValueError:
        return None

def _season_from_detail(data: dict[str, Any], fallback: int | None) -> int | None:
    raw = str(data.get("seasonyear") or data.get("season") or "").strip()
    if not raw:
        return fallback
    year = raw.split("/", 1)[0].strip()
    try:
        return int(year)
    except ValueError:
        return fallback


def _extremes_for_fid(payload: dict[str, Any] | None, fid: str) -> list[dict[str, Any]]:
    rows = (payload or {}).get("data") or []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and str(row.get("fid") or "") == fid]


def _analysis_tool_titles(payload: dict[str, Any] | None) -> list[str]:
    rows = (payload or {}).get("data") or []
    if not isinstance(rows, list):
        return []
    titles: list[str] = []
    for row in rows:
        if isinstance(row, dict) and row.get("title"):
            titles.append(str(row["title"]))
    return titles


def _vote_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = (payload or {}).get("data") or []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _vote_summary(rows: list[dict[str, Any]]) -> str:
    pieces: list[str] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        rate = str(row.get("rate") or "").strip()
        select = str(row.get("select") or "").strip()
        if name and rate:
            pieces.append(f"{name}{rate}")
        elif name and select:
            pieces.append(f"{name}{select}")
    return " / ".join(pieces[:3])


def _payload_data(payload: dict[str, Any] | None) -> Any:
    if not isinstance(payload, dict):
        return None
    return payload.get("data")


def _match_context_summary(
    *,
    injury_preview_payload: dict[str, Any] | None = None,
    lineup_simple_payload: dict[str, Any] | None = None,
    lineup_detail_payload: dict[str, Any] | None = None,
    lineup_full_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    injury_data = _payload_data(injury_preview_payload)
    if isinstance(injury_data, dict):
        injury_rows = _count_nested_rows(injury_data.get("shangbing"))
        h2h_rows = _count_nested_rows(injury_data.get("match"))
        preview = injury_data.get("xinshui")
        if injury_rows:
            summary["injury_rows"] = injury_rows
        if h2h_rows:
            summary["h2h_rows"] = h2h_rows
        if isinstance(preview, str) and preview.strip():
            summary["preview"] = preview.strip()[:240]
        elif isinstance(preview, dict):
            summary["preview"] = _compact_dict(preview, ("title", "content", "confidence", "result", "text"))
    lineup_simple = _payload_data(lineup_simple_payload)
    if isinstance(lineup_simple, dict):
        lineup = _lineup_payload_summary(lineup_simple)
        if lineup:
            summary["lineup_simple"] = lineup
    lineup_detail = _payload_data(lineup_detail_payload)
    if isinstance(lineup_detail, dict):
        lineup = _lineup_payload_summary(lineup_detail)
        if lineup:
            summary["lineup_detail"] = lineup
    lineup_full = _payload_data(lineup_full_payload)
    if isinstance(lineup_full, dict):
        lineup = _lineup_payload_summary(lineup_full)
        if lineup:
            summary["lineup_full"] = lineup
    return {key: value for key, value in summary.items() if value not in ({}, [], None, "")}


def _odds_context_summary(
    *,
    europe_odds_history_payload: dict[str, Any] | None = None,
    odds_summary_payload: dict[str, Any] | None = None,
    odds_heat_payload: dict[str, Any] | None = None,
    handicap_europe_payload: dict[str, Any] | None = None,
    league_stats_payload: dict[str, Any] | None = None,
    betting_distribution_payload: dict[str, Any] | None = None,
    same_odds_history_payload: dict[str, Any] | None = None,
    odds_trend_payload: dict[str, Any] | None = None,
    bifa_trade_payload: dict[str, Any] | None = None,
    company_list_payload: dict[str, Any] | None = None,
    odds_change_list_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    history_rows = _payload_rows(europe_odds_history_payload)
    if history_rows:
        summary["europe_history_rows"] = len(history_rows)
        latest = _first_dict(history_rows)
        if latest:
            summary["europe_history_latest"] = _compact_dict(
                latest,
                ("company", "name", "win", "draw", "lost", "pay", "time", "kwin", "kdraw", "klost"),
            )
    summary_rows = _company_summary_rows(odds_summary_payload or {})
    if summary_rows:
        summary["summary_rows"] = len(summary_rows)
        first_summary = _first_dict(summary_rows)
        if first_summary:
            summary["summary_latest"] = _compact_dict(
                first_summary,
                ("company", "name", "win", "draw", "lost", "home", "away", "handi", "big", "small"),
            )
    heat = _first_dict(_payload_rows(odds_heat_payload)) or _payload_data(odds_heat_payload)
    if isinstance(heat, dict):
        summary["heat"] = _compact_dict(
            heat,
            (
                "winamount",
                "drawamount",
                "lostamount",
                "winrate",
                "drawrate",
                "lostrate",
                "win",
                "draw",
                "lost",
            ),
        )
    handicap_rows = _company_odds_rows(handicap_europe_payload or {})
    if handicap_rows:
        summary["handicap_europe_rows"] = len(handicap_rows)
        line = _company_odds_line(handicap_europe_payload or {})
        if line:
            summary["handicap_europe_line"] = line
    league_stats = _payload_data(league_stats_payload)
    if isinstance(league_stats, dict):
        summary["league_stats"] = _compact_dict(
            league_stats,
            ("total", "win", "draw", "lost", "sp", "pp", "xp", "big", "small", "bigrate", "smallrate"),
        )
    betting_distribution = _payload_data(betting_distribution_payload)
    if isinstance(betting_distribution, dict):
        summary["betting_distribution"] = _compact_dict(
            betting_distribution,
            ("traderank", "tradetend", "compare", "tend", "qqtouzhu", "bifa", "jincai", "fenbu"),
        )
    same_odds = _payload_data(same_odds_history_payload)
    if isinstance(same_odds, dict):
        compact_same_odds: dict[str, Any] = {}
        for market in ("spf", "yazhi", "daxiao", "corner", "half"):
            value = same_odds.get(market)
            if isinstance(value, dict):
                compact_same_odds[market] = _compact_dict(
                    value,
                    ("count", "winrate", "win", "draw", "lost", "rate", "odds", "handi", "big", "small"),
                )
            elif isinstance(value, list):
                compact_same_odds[market] = {"rows": len([row for row in value if isinstance(row, dict)])}
        if compact_same_odds:
            summary["same_odds_history"] = compact_same_odds
    odds_trend = _payload_data(odds_trend_payload)
    if isinstance(odds_trend, dict):
        compact_trend: dict[str, Any] = {}
        for market in ("euro", "yazhi", "daxiao"):
            value = odds_trend.get(market)
            if isinstance(value, dict):
                compact_trend[market] = _compact_dict(
                    value,
                    ("win", "draw", "lost", "home", "away", "handi", "big", "small", "time", "w", "d", "l"),
                )
        if compact_trend:
            summary["odds_trend"] = compact_trend
    bifa_trade = _payload_data(bifa_trade_payload)
    if isinstance(bifa_trade, dict):
        summary["bifa_trade"] = _compact_dict(bifa_trade, ("amount", "trade", "toptrade", "total", "win", "draw", "lost"))
    company_rows = _company_list_rows(company_list_payload)
    if company_rows:
        summary["company_count"] = len(company_rows)
        first_company = _first_dict(company_rows)
        if first_company:
            summary["company_sample"] = _compact_dict(first_company, ("id", "companyid", "companyname", "name", "pyindex"))
    odds_change_rows = _payload_rows(odds_change_list_payload)
    if odds_change_rows:
        summary["odds_change_rows"] = len(odds_change_rows)
        latest_change = _first_dict(odds_change_rows)
        if latest_change:
            summary["odds_change_latest"] = _compact_dict(
                latest_change,
                ("fid", "change", "total", "hname", "aname", "lname", "vsdate", "cname", "win", "draw", "lost"),
            )
    return {key: value for key, value in summary.items() if value not in ({}, [], None, "")}


def _odds_timeline_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    markets = payload.get("markets")
    if not isinstance(markets, dict):
        return {}
    summary: dict[str, Any] = {}
    for market, label in (("1x2", "欧赔"), ("asian_handicap", "亚盘"), ("over_under", "大小球")):
        item = markets.get(market)
        if not isinstance(item, dict):
            continue
        history_count = int(item.get("history_row_count") or 0)
        current_available = bool(item.get("current_available"))
        if history_count > 0 or current_available:
            summary[f"{market}_timeline"] = {
                "label": label,
                "company": item.get("company"),
                "history_row_count": history_count,
                "current_available": current_available,
                "history_availability": item.get("history_availability"),
            }
    bundle_summary = payload.get("summary")
    if isinstance(bundle_summary, dict):
        summary["timeline_coverage"] = bundle_summary
    return summary


def _context_summary(
    match: Match,
    standings: dict[str, Any],
    extremes: list[dict[str, Any]],
    *,
    tools: list[str] | None = None,
    match_context: dict[str, Any] | None = None,
    lingsi: Any = None,
    votes: list[dict[str, Any]] | None = None,
    odds_context: dict[str, Any] | None = None,
) -> str:
    home_power = _power_score(standings.get("hpower"))
    away_power = _power_score(standings.get("apower"))
    ranks = standings.get("ranks") or []
    home_standing = away_standing = ""
    if ranks and isinstance(ranks[0], dict):
        home_standing = _standing_summary(ranks[0].get("homestanding"))
        away_standing = _standing_summary(ranks[0].get("awaystanding"))
    home_record = _record_summary(standings.get("home_datatotal"))
    away_record = _record_summary(standings.get("away_datatotal"))
    pieces = [f"QQSD完整数据：{match.home_team} vs {match.away_team}"]
    if home_power is not None and away_power is not None:
        pieces.append(f"综合评分 {match.home_team} {home_power:.0f} / {match.away_team} {away_power:.0f}")
    if home_standing or away_standing:
        pieces.append(f"排名 {home_standing or '未知'}；{away_standing or '未知'}")
    if home_record or away_record:
        pieces.append(f"近况 {match.home_team} {home_record or '未知'}；{match.away_team} {away_record or '未知'}")
    if extremes:
        pieces.append("极限数据 " + "；".join(str(row.get("str") or row.get("content") or "") for row in extremes[:3] if row))
    match_context_pieces = _match_context_pieces(match_context or {})
    if match_context_pieces:
        pieces.append("阵容伤停 " + "；".join(match_context_pieces))
    vote_summary = _vote_summary(votes or [])
    if vote_summary:
        pieces.append(f"投票热度 {vote_summary}")
    if isinstance(lingsi, dict) and lingsi.get("title"):
        pieces.append(f"临场入口 {lingsi.get('title')}")
    odds_pieces = _odds_context_pieces(odds_context or {})
    if odds_pieces:
        pieces.append("赔率上下文 " + "；".join(odds_pieces))
    if tools:
        pieces.append("可用分析工具 " + "、".join(tools[:5]))
    return "。".join(piece for piece in pieces if piece) + "。"


def _odds_context_pieces(context: dict[str, Any]) -> list[str]:
    pieces: list[str] = []
    if context.get("europe_history_rows"):
        pieces.append(f"欧指历史{context['europe_history_rows']}条")
    for key in ("1x2_timeline", "asian_handicap_timeline", "over_under_timeline"):
        timeline = context.get(key)
        if isinstance(timeline, dict):
            label = str(timeline.get("label") or key)
            rows = int(timeline.get("history_row_count") or 0)
            current = "当前盘可用" if timeline.get("current_available") else "当前盘缺失"
            pieces.append(f"{label}时间线{rows}条({current})")
    if context.get("summary_rows"):
        pieces.append(f"单公司摘要{context['summary_rows']}条")
    heat = context.get("heat")
    if isinstance(heat, dict):
        heat_bits = []
        for label, key in (("胜", "winrate"), ("平", "drawrate"), ("负", "lostrate")):
            value = str(heat.get(key) or "").strip()
            if value:
                heat_bits.append(f"{label}{value}")
        if heat_bits:
            pieces.append("冷热 " + " / ".join(heat_bits))
    if context.get("handicap_europe_rows"):
        line = str(context.get("handicap_europe_line") or "").strip()
        pieces.append(f"让球欧赔{context['handicap_europe_rows']}条" + (f"({line})" if line else ""))
    same_odds = context.get("same_odds_history")
    if isinstance(same_odds, dict):
        same_odds_bits: list[str] = []
        for label, key in (("胜平负", "spf"), ("亚盘", "yazhi"), ("大小", "daxiao")):
            value = same_odds.get(key)
            if isinstance(value, dict):
                count = str(value.get("count") or value.get("rows") or "").strip()
                rate = str(value.get("winrate") or value.get("rate") or "").strip()
                if count and rate:
                    same_odds_bits.append(f"{label}{count}场/{rate}")
                elif count:
                    same_odds_bits.append(f"{label}{count}场")
        if same_odds_bits:
            pieces.append("同赔 " + " / ".join(same_odds_bits))
    betting_distribution = context.get("betting_distribution")
    if isinstance(betting_distribution, dict):
        trend = _extract_nested_text(betting_distribution, ("tradetend", "tend", "compare", "traderank"))
        if trend:
            pieces.append(f"投注趋势 {trend}")
    bifa_trade = context.get("bifa_trade")
    if isinstance(bifa_trade, dict):
        total = _extract_nested_text(bifa_trade, ("total", "amount"))
        if total:
            pieces.append(f"必发交易 {total}")
    if context.get("odds_change_rows"):
        pieces.append(f"赔率异动{context['odds_change_rows']}条")
    league_stats = context.get("league_stats")
    if isinstance(league_stats, dict):
        total = str(league_stats.get("total") or "").strip()
        win = str(league_stats.get("win") or "").strip()
        draw = str(league_stats.get("draw") or "").strip()
        lost = str(league_stats.get("lost") or "").strip()
        if total or win or draw or lost:
            pieces.append(f"联赛统计 {total or '?'}场 {win or '?'}胜/{draw or '?'}平/{lost or '?'}负")
    return pieces


def _match_context_pieces(context: dict[str, Any]) -> list[str]:
    pieces: list[str] = []
    injury_rows = int(context.get("injury_rows") or 0)
    if injury_rows:
        pieces.append(f"伤停{injury_rows}条")
    h2h_rows = int(context.get("h2h_rows") or 0)
    if h2h_rows:
        pieces.append(f"交锋{h2h_rows}场")
    for key, label in (("lineup_full", "完整首发"), ("lineup_detail", "阵容详情"), ("lineup_simple", "简版首发")):
        lineup = context.get(key)
        if not isinstance(lineup, dict):
            continue
        home_shape = str(lineup.get("home_shape") or "").strip()
        away_shape = str(lineup.get("away_shape") or "").strip()
        home_count = int(lineup.get("home_starters") or 0)
        away_count = int(lineup.get("away_starters") or 0)
        if home_shape or away_shape:
            pieces.append(f"{label}{home_shape or '未知'} vs {away_shape or '未知'}")
            break
        if home_count or away_count:
            pieces.append(f"{label}{home_count}/{away_count}人")
            break
    preview = context.get("preview")
    if isinstance(preview, str) and preview.strip():
        pieces.append("前瞻" + preview.strip()[:80])
    elif isinstance(preview, dict):
        preview_text = _extract_nested_text(preview, ("title", "content", "confidence", "result", "text"))
        if preview_text:
            pieces.append("前瞻" + preview_text[:80])
    return pieces


def _lineup_payload_summary(data: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for side, prefix in (("home", "home"), ("away", "away")):
        team = data.get(side)
        if not isinstance(team, dict):
            continue
        name = str(team.get("name") or "").strip()
        shape = str(team.get("zhenxing") or team.get("formation") or "").strip()
        starters = _count_nested_rows(team.get("shoufa"))
        substitutes = _count_nested_rows(team.get("substitute"))
        injuries = _count_nested_rows(team.get("shangbing"))
        if name:
            summary[f"{prefix}_name"] = name
        if shape:
            summary[f"{prefix}_shape"] = shape
        if starters:
            summary[f"{prefix}_starters"] = starters
        if substitutes:
            summary[f"{prefix}_substitutes"] = substitutes
        if injuries:
            summary[f"{prefix}_injuries"] = injuries
        number_one = team.get("numberone")
        if isinstance(number_one, dict):
            summary[f"{prefix}_key_player"] = _compact_dict(number_one, ("name", "role", "goals", "shots", "passes"))
        focus = team.get("jiaodian")
        if isinstance(focus, dict):
            summary[f"{prefix}_focus"] = _compact_dict(focus, ("name", "role", "content", "text"))
    return {key: value for key, value in summary.items() if value not in ({}, [], None, "")}


def _count_nested_rows(value: Any) -> int:
    if isinstance(value, list):
        count = 0
        for item in value:
            if isinstance(item, dict):
                count += 1
            else:
                count += _count_nested_rows(item)
        return count
    if isinstance(value, dict):
        direct_rows = [row for row in value.values() if isinstance(row, dict)]
        list_rows = sum(_count_nested_rows(row) for row in value.values() if isinstance(row, list))
        if direct_rows or list_rows:
            return len(direct_rows) + list_rows
    return 0


def _extract_nested_text(value: Any, keys: tuple[str, ...]) -> str:
    if isinstance(value, str):
        return value.strip()[:120]
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in keys:
            child = value.get(key)
            text = _extract_nested_text(child, keys)
            if text:
                return text
    return ""


def _company_list_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    data = _payload_data(payload)
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()

    def collect(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            marker = id(value)
            if marker not in seen and any(value.get(key) not in (None, "", [], {}) for key in ("companyname", "companyid", "id", "name")):
                seen.add(marker)
                rows.append(value)
                return
            for child in value.values():
                if isinstance(child, (dict, list)):
                    collect(child)

    collect(data)
    return rows


def _power_score(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    return _safe_float(value.get("total_score"))


def _standing_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    name = str(value.get("name") or "").strip()
    standing = str(value.get("standing") or "").strip()
    score = str(value.get("score") or "").strip()
    if name and standing and score:
        return f"{name} 第{standing}名 {score}分"
    if name:
        return name
    return ""


def _record_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    count = str(value.get("count") or "").strip()
    win = str(value.get("win") or "").strip()
    draw = str(value.get("draw") or "").strip()
    lost = str(value.get("lost") or "").strip()
    goal_for = str(value.get("innum") or value.get("goal") or "").strip()
    goal_against = str(value.get("lostnum") or "").strip()
    if win and draw and lost:
        base = f"{win}胜{draw}平{lost}负"
        if count:
            base = f"{count}场" + base
        if goal_for and goal_against:
            base += f"，进{goal_for}/失{goal_against}"
        return base
    return ""


def _league_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if "MATCHID" in value and "SEASONID" in value:
                entries.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload.get("data") or payload)
    return entries


def _normalize_identifier(value: str) -> str:
    return value.strip().lower().replace(" ", "").replace("-", "").replace("_", "")


def _score_cids(cid: str | None) -> list[str] | None:
    if cid is None:
        return None
    value = cid.strip()
    return [value] if value else None


def _default_score_cids() -> list[str]:
    raw = os.getenv("QQSD_SCORE_CIDS")
    if raw:
        values = [value.strip() for value in raw.split(",") if value.strip()]
        if values:
            return values
    return [str(value) for value in range(1, 16)]


def _inside_match_date_window(kickoff: datetime, date: str) -> bool:
    start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=QQSD_TIMEZONE)
    end = start + timedelta(days=1, hours=_date_window_end_hours())
    return start <= kickoff < end


def _date_window_end_hours() -> int:
    raw = os.getenv("QQSD_DATE_WINDOW_END_HOURS")
    if raw is None or raw.strip() == "":
        return 4
    try:
        return max(0, min(int(raw), 12))
    except ValueError:
        return 4


def _dedupe_matches(matches: Any) -> list[Match]:
    deduped: dict[str, Match] = {}
    for match in matches:
        deduped.setdefault(match.id, match)
    return list(deduped.values())


def _dedupe_odds(snapshots: Any) -> list[OddsSnapshot]:
    deduped: dict[str, OddsSnapshot] = {}
    for snapshot in snapshots:
        deduped.setdefault(snapshot.id, snapshot)
    return list(deduped.values())


def _score_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return []
    rows = data.get("list") or []
    return [row for row in rows if isinstance(row, dict)]


def _archive_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("list") or data.get("data") or []
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _company_odds_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    candidates: list[Any] = []
    if isinstance(data, list):
        candidates.append(data)
    elif isinstance(data, dict):
        for key in ("list", "europe", "europelist", "europeList", "ou", "odds", "data"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.append(value)
        candidates.append(data)
    candidates.append(payload)

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for candidate in candidates:
        for row in _walk_company_odds_rows(candidate):
            marker = id(row)
            if marker not in seen:
                seen.add(marker)
                rows.append(row)
    return rows


def _company_asian_odds_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    candidates: list[Any] = []
    if isinstance(data, list):
        candidates.append(data)
    elif isinstance(data, dict):
        for key in ("list", "asian", "yazhi", "asia", "handicap", "odds", "data"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.append(value)
        candidates.append(data)
    candidates.append(payload)

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for candidate in candidates:
        for row in _walk_company_asian_odds_rows(candidate):
            marker = id(row)
            if marker not in seen:
                seen.add(marker)
                rows.append(row)
    return rows


def _company_total_odds_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    candidates: list[Any] = []
    if isinstance(data, list):
        candidates.append(data)
    elif isinstance(data, dict):
        for key in ("list", "totals", "total", "overunder", "over_under", "daxiao", "odds", "data"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.append(value)
        candidates.append(data)
    candidates.append(payload)

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for candidate in candidates:
        for row in _walk_company_total_odds_rows(candidate):
            marker = id(row)
            if marker not in seen:
                seen.add(marker)
                rows.append(row)
    return rows


def _company_summary_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    candidates: list[Any] = []
    if isinstance(data, list):
        candidates.append(data)
    elif isinstance(data, dict):
        for key in ("list", "summary", "company", "odds", "data"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.append(value)
        candidates.append(data)
    candidates.append(payload)

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for candidate in candidates:
        for row in _walk_company_summary_rows(candidate):
            marker = id(row)
            if marker not in seen:
                seen.add(marker)
                rows.append(row)
    return rows


def _company_odds_line(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    raw = str(data.get("handicapline") or "").strip()
    if not raw:
        return None
    return f"让球{raw}"


def _walk_company_odds_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if _looks_like_company_europe_row(value):
            rows.append(value)
        for child in value.values():
            rows.extend(_walk_company_odds_rows(child))
    elif isinstance(value, list):
        for child in value:
            rows.extend(_walk_company_odds_rows(child))
    return rows


def _walk_company_asian_odds_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if _looks_like_company_asian_row(value):
            rows.append(value)
        for child in value.values():
            rows.extend(_walk_company_asian_odds_rows(child))
    elif isinstance(value, list):
        for child in value:
            rows.extend(_walk_company_asian_odds_rows(child))
    return rows


def _walk_company_total_odds_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            rows.extend(_walk_company_total_odds_rows(item))
    elif isinstance(value, dict):
        if _looks_like_company_total_row(value):
            rows.append(value)
        for child in value.values():
            rows.extend(_walk_company_total_odds_rows(child))
    return rows


def _walk_company_summary_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            rows.extend(_walk_company_summary_rows(item))
    elif isinstance(value, dict):
        if _looks_like_company_summary_row(value):
            rows.append(value)
        for child in value.values():
            rows.extend(_walk_company_summary_rows(child))
    return rows


def _looks_like_company_europe_row(row: dict[str, Any]) -> bool:
    if not _company_name(row):
        return False
    odds = _company_current_odds(row)
    return all(_decimal_price(odds.get(key)) for key in ("win", "draw", "lost"))


def _looks_like_company_asian_row(row: dict[str, Any]) -> bool:
    if not _company_name(row):
        return False
    odds = _company_current_odds(row)
    return bool(_asian_line(odds)) and all(_asian_price(odds.get(key)) for key in ("home", "away"))


def _looks_like_company_total_row(row: dict[str, Any]) -> bool:
    if not _company_name(row):
        return False
    odds = _company_current_odds(row)
    return bool(_asian_line(odds)) and all(_asian_price(odds.get(key)) for key in ("big", "small"))


def _looks_like_company_summary_row(row: dict[str, Any]) -> bool:
    if not _company_name(row):
        return False
    odds = _company_current_odds(row)
    has_europe = all(_decimal_price(odds.get(key)) for key in ("win", "draw", "lost"))
    has_asian = bool(_asian_line(odds)) and all(_asian_price(odds.get(key)) for key in ("home", "away"))
    has_total = bool(_asian_line(odds)) and all(_asian_price(odds.get(key)) for key in ("big", "small"))
    return has_europe or has_asian or has_total


def _company_europe_rows_to_snapshots(
    rows: list[dict[str, Any]],
    *,
    fid: str | None,
    endpoint: str,
    line: str | None = None,
) -> list[OddsSnapshot]:
    snapshots: list[OddsSnapshot] = []
    for index, row in enumerate(rows):
        row_fid = str(row.get("fid") or row.get("fixtureid") or fid or "").strip()
        if not row_fid:
            continue
        bookmaker = _company_name(row)
        current = _company_current_odds(row)
        odds = {
            key: value
            for key, value in {
                "HOME": _decimal_price(current.get("win")),
                "DRAW": _decimal_price(current.get("draw")),
                "AWAY": _decimal_price(current.get("lost")),
            }.items()
            if value
        }
        if len(odds) != 3:
            continue
        bookmaker_id = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", bookmaker).strip("_") or str(index)
        snapshots.append(
            OddsSnapshot(
                id=f"qqsd:{row_fid}:{bookmaker_id}:1x2:{endpoint}:{line or 'main'}",
                match_id=f"qqsd:{row_fid}",
                market_type="1x2",
                line=line,
                source="qqsd",
                bookmaker=bookmaker,
                collected_at=datetime.utcnow(),
                outcome_odds=odds,
                best_price=dict(odds),
            )
        )
    return snapshots


def _company_asian_rows_to_snapshots(
    rows: list[dict[str, Any]],
    *,
    fid: str | None,
    endpoint: str,
) -> list[OddsSnapshot]:
    snapshots: list[OddsSnapshot] = []
    for index, row in enumerate(rows):
        row_fid = str(row.get("fid") or row.get("fixtureid") or fid or "").strip()
        if not row_fid:
            continue
        bookmaker = _company_name(row)
        current = _company_current_odds(row)
        line = _asian_line(current)
        odds = {
            key: value
            for key, value in {
                "HOME": _asian_price(current.get("home")),
                "AWAY": _asian_price(current.get("away")),
            }.items()
            if value
        }
        if not line or len(odds) != 2:
            continue
        bookmaker_id = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", bookmaker).strip("_") or str(index)
        snapshots.append(
            OddsSnapshot(
                id=f"qqsd:{row_fid}:{bookmaker_id}:asian_handicap:{endpoint}:{line}",
                match_id=f"qqsd:{row_fid}",
                market_type="asian_handicap",
                line=line,
                source="qqsd",
                bookmaker=bookmaker,
                collected_at=datetime.utcnow(),
                outcome_odds=odds,
                best_price=dict(odds),
            )
        )
    return snapshots


def _company_total_rows_to_snapshots(
    rows: list[dict[str, Any]],
    *,
    fid: str | None,
    endpoint: str,
) -> list[OddsSnapshot]:
    snapshots: list[OddsSnapshot] = []
    for index, row in enumerate(rows):
        row_fid = str(row.get("fid") or row.get("fixtureid") or fid or "").strip()
        if not row_fid:
            continue
        bookmaker = _company_name(row)
        current = _company_current_odds(row)
        line = _asian_line(current)
        odds = {
            key: value
            for key, value in {
                "OVER": _asian_price(current.get("big")),
                "UNDER": _asian_price(current.get("small")),
            }.items()
            if value
        }
        if not line or len(odds) != 2:
            continue
        bookmaker_id = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", bookmaker).strip("_") or str(index)
        snapshots.append(
            OddsSnapshot(
                id=f"qqsd:{row_fid}:{bookmaker_id}:over_under:{endpoint}:{line}",
                match_id=f"qqsd:{row_fid}",
                market_type="over_under",
                line=line,
                source="qqsd",
                bookmaker=bookmaker,
                collected_at=datetime.utcnow(),
                outcome_odds=odds,
                best_price=dict(odds),
            )
        )
    return snapshots


def _company_name(row: dict[str, Any]) -> str:
    for key in ("name", "company", "companyname", "bookmaker", "bookmaker_name", "cname"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _asian_line(row: dict[str, Any]) -> str | None:
    for key in ("handi", "handicap", "line", "draw", "p", "pshow", "flat", "flatodds"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return None


def _company_current_odds(row: dict[str, Any]) -> dict[str, Any]:
    current = row.get("end") or row.get("current") or row.get("now") or row.get("last") or row.get("instant")
    if isinstance(current, dict):
        return current
    return row


def _payload_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("list", "data", "rows", "history"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return [data]
    return []


def _payload_rows_deep(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    _collect_payload_rows(payload.get("data"), rows, seen)
    if not rows:
        _collect_payload_rows(payload, rows, seen)
    return rows


def _collect_payload_rows(value: Any, rows: list[dict[str, Any]], seen: set[int]) -> None:
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            for item in value:
                marker = id(item)
                if marker not in seen and _looks_like_history_row(item):
                    seen.add(marker)
                    rows.append(item)
            if rows:
                return
        for item in value:
            _collect_payload_rows(item, rows, seen)
    elif isinstance(value, dict):
        marker = id(value)
        if marker not in seen and _looks_like_history_row(value):
            seen.add(marker)
            rows.append(value)
            return
        for key in ("list", "data", "rows", "history", "odds", "items", "result"):
            child = value.get(key)
            if child is not None:
                _collect_payload_rows(child, rows, seen)
        if not rows:
            for child in value.values():
                if isinstance(child, (dict, list)):
                    _collect_payload_rows(child, rows, seen)


def _looks_like_history_row(row: dict[str, Any]) -> bool:
    return bool(_history_time(row) or _history_line(row)) and bool(
        _history_value(row, _LEFT_ODDS_KEYS) or _history_value(row, _RIGHT_ODDS_KEYS)
    )


_TIME_KEYS = ("time", "updatetime", "updateTime", "date", "uptime", "modifytime", "mtime", "addtime")
_LINE_KEYS = ("handi", "handicap", "line", "p", "pshow", "flat", "flatodds", "draw")
_LEFT_ODDS_KEYS = ("home", "win", "w", "winodds", "homeodds", "big", "over", "above")
_RIGHT_ODDS_KEYS = ("away", "lost", "l", "lostodds", "awayodds", "small", "under", "below")


def _normalize_asian_history_row(row: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "time": _history_time(row),
        "home": _history_value(row, ("home", "win", "w", "winodds", "homeodds", "big", "over")),
        "line": _history_line(row),
        "away": _history_value(row, ("away", "lost", "l", "lostodds", "awayodds", "small", "under")),
    }
    return {key: value for key, value in compact.items() if value not in (None, "")}


def _normalize_total_history_row(row: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "time": _history_time(row),
        "over": _history_value(row, ("big", "over", "home", "win", "w", "winodds", "homeodds")),
        "line": _history_line(row),
        "under": _history_value(row, ("small", "under", "away", "lost", "l", "lostodds", "awayodds")),
    }
    return {key: value for key, value in compact.items() if value not in (None, "")}


def _history_time(row: dict[str, Any]) -> str | None:
    value = _history_value(row, _TIME_KEYS)
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value).replace("\n", " ")).strip() or None


def _history_line(row: dict[str, Any]) -> str | None:
    value = _history_value(row, _LINE_KEYS)
    return str(value).strip() if value not in (None, "") else None


def _history_value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lower_lookup = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        if key in row and row[key] not in (None, "", [], {}):
            return row[key]
        value = lower_lookup.get(key.lower())
        if value not in (None, "", [], {}):
            return value
    return None


def _first_dict(rows: Any) -> dict[str, Any] | None:
    if isinstance(rows, dict):
        return rows
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                return row
    return None


def _compact_dict(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            compact[key] = value
    return compact


def _find_xml_parent(root: ElementTree.Element, target: ElementTree.Element) -> ElementTree.Element | None:
    for parent in root.iter():
        if any(child is target for child in list(parent)):
            return parent
    return None


def _xml_text_by_resource(parent: ElementTree.Element, resource_suffix: str) -> str | None:
    suffix = f"id/{resource_suffix}"
    for child in parent.iter("node"):
        if str(child.attrib.get("resource-id") or "").endswith(suffix):
            value = str(child.attrib.get("text") or "").strip()
            if value:
                return value
    return None


def _archive_page_signature(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    signature: list[str] = []
    for row in rows:
        fixture = row.get("fixture") if isinstance(row.get("fixture"), dict) else row
        signature.append(str(fixture.get("fixtureid") or row.get("fixtureid") or fixture.get("fid") or row.get("fid") or repr(row)[:80]))
    return tuple(signature)


def _archive_max_pages() -> int:
    raw = os.getenv("QQSD_ARCHIVE_MAX_PAGES", "5")
    try:
        value = int(raw)
    except ValueError:
        return 5
    return max(1, min(value, 20))


def _parse_datetime(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=QQSD_TIMEZONE)
        except ValueError:
            continue
    return None


def _map_status(value: str) -> MatchStatus:
    if value in {"2", "3"}:
        return MatchStatus.finished
    if value == "1":
        return MatchStatus.scheduled
    return MatchStatus.scheduled


def _map_archive_status(row: dict[str, Any], *, home_score: int | None, away_score: int | None) -> MatchStatus:
    status = str(row.get("status") or "")
    if status:
        return _map_status(status)
    result = str(row.get("result") or "")
    if home_score is not None and away_score is not None and result != "-1":
        return MatchStatus.finished
    return MatchStatus.scheduled


def _safe_int(value: Any) -> int | None:
    try:
        if value in {None, ""}:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _asian_price(value: Any) -> float | None:
    raw = _safe_float(value)
    if raw is None:
        return None
    if raw <= 0:
        return None
    return round(raw + 1.0, 4)


def _decimal_price(value: Any) -> float | None:
    raw = _safe_float(value)
    if raw is None or raw <= 1.0:
        return None
    return round(raw, 4)


def _headers() -> dict[str, str]:
    return {
        "AppVersion": "4.9.6.6",
        "AppRegfrom": "Web",
        "AppRegfromFirst": "Web",
        "platform": "2",
        "User-Agent": "qiuqiushidao/4.9.6.6 (Linux; U; Android 16)",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "*/*",
    }


def _env(name: str, default: str) -> str:
    return os.getenv(name) or default


def _allow_empty_token() -> bool:
    return (os.getenv("QQSD_ALLOW_EMPTY_C_CK") or "").lower() in {"1", "true", "yes"}
