from __future__ import annotations

from datetime import datetime
from typing import Any

from football_analysis.datasources.base import ClientContext, DataSourceError
from football_analysis.models import AgentFinding, EvidenceSource, Match, MatchStatus


TAB_IDS = {
    "EPL": 6,
    "LALIGA": 7,
    "SERIE_A": 9,
    "BUNDESLIGA": 8,
    "LIGUE_1": 30,
    "UCL": 31,
}
DEFAULT_TEAM_FEED_SIZE = 20
DEFAULT_ARTICLE_DETAIL_LIMIT = 3


class DongqiudiClient:
    provider = "dongqiudi"

    def __init__(self, context: ClientContext):
        if not context.source.enabled:
            raise DataSourceError("dongqiudi_source_disabled")
        self.context = context

    def fixtures(self, date: str, league_code: str | None = None) -> list[Match]:
        tab_id = TAB_IDS.get((league_code or "").upper(), 10)
        response = self.context.http.get_json(
            provider=self.provider,
            url=f"{self.context.source.base_url.rstrip('/')}/data/tab/new/{tab_id}",
            endpoint="/data/tab/new",
            params={"start": _date_to_timestamp(date), "version": 576, "init": 1, "wfrom": 2},
        )
        if response.error:
            raise DataSourceError(f"dongqiudi_request_failed:{response.error}")
        if not isinstance(response.payload, dict):
            raise DataSourceError("dongqiudi_invalid_payload:expected_object")
        return map_fixtures(response.payload)

    def match_analysis(self, match_id: str) -> Any:
        return self._get_json(f"/mobile/match/analysis/{match_id}")

    def highlights(self, match_id: str) -> Any:
        return self._get_json(f"/mobile/match/highlights/{match_id}")

    def lineup(self, match_id: str) -> Any:
        return self._get_json(f"/mobile/match/lineup/{match_id}")

    def situation(self, match_id: str) -> Any:
        return self._get_json(f"/mobile/match/situation/{match_id}")

    def poll(self, match_id: str) -> Any:
        return self._get_json("/poll", params={"id": match_id})

    def article_detail(self, article_id: str) -> Any:
        return self._get_json(f"/v2/article/detail/{article_id}")

    def team_feeds(self, team_id: str, size: int = DEFAULT_TEAM_FEED_SIZE) -> Any:
        return self._get_json(
            "/v3/archive/app/channel/feeds",
            params={"id": team_id, "type": "team", "size": size, "platform": "web"},
        )

    def intelligence_findings(
        self,
        match: Match,
        include_team_feeds: bool = True,
        article_detail_limit: int = DEFAULT_ARTICLE_DETAIL_LIMIT,
        errors: list[str] | None = None,
    ) -> list[AgentFinding]:
        match_id = match.external_ids.get("dongqiudi_match")
        if not match_id:
            return []
        findings: list[AgentFinding] = []
        for kind, payload_fn in (
            ("match_analysis", lambda: self.match_analysis(match_id)),
            ("lineup", lambda: self.lineup(match_id)),
            ("situation", lambda: self.situation(match_id)),
            ("poll", lambda: self.poll(match_id)),
        ):
            try:
                payload = payload_fn()
            except Exception as exc:
                if errors is not None:
                    errors.append(f"{match.id}:{kind}:{type(exc).__name__}: {exc}")
                continue
            if _has_payload_data(payload):
                findings.append(make_finding(match, kind, payload, match_id=match_id))
        if match.status == MatchStatus.finished:
            try:
                payload = self.highlights(match_id)
            except Exception as exc:
                if errors is not None:
                    errors.append(f"{match.id}:highlights:{type(exc).__name__}: {exc}")
            else:
                if _has_payload_data(payload):
                    findings.append(make_finding(match, "highlights", payload, match_id=match_id))
        if include_team_feeds:
            for side, team_id in _team_ids(match).items():
                try:
                    payload = self.team_feeds(team_id)
                except Exception as exc:
                    if errors is not None:
                        errors.append(f"{match.id}:{side}_team_feeds:{type(exc).__name__}: {exc}")
                    continue
                payload = self._with_article_details(
                    match=match,
                    side=side,
                    team_id=team_id,
                    payload=payload,
                    article_detail_limit=article_detail_limit,
                    errors=errors,
                )
                if _has_payload_data(payload):
                    findings.append(make_finding(match, f"{side}_team_feeds", payload, match_id=match_id, team_id=team_id))
        return findings

    def _with_article_details(
        self,
        match: Match,
        side: str,
        team_id: str,
        payload: Any,
        article_detail_limit: int,
        errors: list[str] | None,
    ) -> Any:
        article_ids = _feed_article_ids(payload)[: max(article_detail_limit, 0)]
        if not article_ids:
            return {"feed": payload, "article_details": []}
        details: list[Any] = []
        for article_id in article_ids:
            try:
                detail = self.article_detail(article_id)
            except Exception as exc:
                if errors is not None:
                    errors.append(f"{match.id}:{side}_article_detail:{article_id}:{type(exc).__name__}: {exc}")
                continue
            if _has_payload_data(detail):
                details.append(detail)
        return {"feed": payload, "article_details": details, "team_id": team_id}

    def _get_json(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        response = self.context.http.get_json(
            provider=self.provider,
            url=f"{self.context.source.base_url.rstrip('/')}{endpoint}",
            endpoint=endpoint,
            params=params or {},
        )
        if response.error:
            raise DataSourceError(f"dongqiudi_request_failed:{endpoint}:{response.error}")
        return response.payload


def map_fixtures(payload: dict[str, Any]) -> list[Match]:
    data = payload.get("data", payload)
    items = []
    if isinstance(data, dict):
        items = data.get("matchList") or data.get("list") or []
    elif isinstance(data, list):
        items = data
    matches: list[Match] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        match_id = _string(item, "match_id", "id")
        home = _string(item, "team_A_name", "home_team", "home_name")
        away = _string(item, "team_B_name", "away_team", "away_name")
        kickoff = _string(item, "start_play", "start_time")
        if not match_id or not home or not away or not kickoff:
            continue
        matches.append(
            Match(
                id=f"dongqiudi:{match_id}",
                league=_string(item, "competition_name", "league_name") or "Unknown",
                home_team=home,
                away_team=away,
                kickoff_at=_parse_datetime(kickoff),
                status=_map_status(_string(item, "status")),
                data_completeness=0.64,
                country=None,
                external_ids=_external_ids(item, match_id),
            )
        )
    return matches


def make_finding(
    match: Match,
    kind: str,
    payload: Any,
    match_id: str,
    team_id: str | None = None,
) -> AgentFinding:
    title = _finding_title(kind, match)
    url = f"https://m.dongqiudi.com/matchDetail/{match_id}"
    if kind.endswith("_team_feeds") and team_id:
        url = f"https://m.dongqiudi.com/team/{team_id}"
    return AgentFinding(
        id=f"dongqiudi:{match.id}:{kind}:{team_id or match_id}",
        match_id=match.id,
        agent_name=f"dongqiudi_{kind}",
        summary=title,
        evidence_sources=[EvidenceSource(title=title, url=url, publisher="dongqiudi")],
        confidence=_confidence(kind, payload),
        risk_tags=[],
        score_delta=0.0,
        payload={"source": "dongqiudi", "kind": kind, "team_id": team_id, "payload": payload},
    )


def _date_to_timestamp(date: str) -> int:
    return int(datetime.strptime(date, "%Y-%m-%d").timestamp())


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _map_status(value: str) -> MatchStatus:
    lowered = value.lower()
    if lowered in {"finished", "ft", "played", "完场"}:
        return MatchStatus.finished
    if lowered in {"postponed", "cancelled", "canceled", "延期", "取消"}:
        return MatchStatus.postponed
    return MatchStatus.scheduled


def _string(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _external_ids(item: dict[str, Any], match_id: str) -> dict[str, str]:
    external_ids = {"dongqiudi_match": match_id}
    for target, keys in {
        "dongqiudi_home_team": ("team_A_id", "team_A_team_id", "home_team_id", "teamAId"),
        "dongqiudi_away_team": ("team_B_id", "team_B_team_id", "away_team_id", "teamBId"),
    }.items():
        value = _string(item, *keys)
        if value:
            external_ids[target] = value
    return external_ids


def _team_ids(match: Match) -> dict[str, str]:
    result: dict[str, str] = {}
    home = match.external_ids.get("dongqiudi_home_team")
    away = match.external_ids.get("dongqiudi_away_team")
    if home:
        result["home"] = home
    if away:
        result["away"] = away
    return result


def _has_payload_data(payload: Any) -> bool:
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if data is None:
        return False
    if isinstance(data, (list, tuple, str, dict)):
        return bool(data)
    return True


def _feed_article_ids(payload: Any) -> list[str]:
    seen: set[str] = set()
    article_ids: list[str] = []
    for article in _feed_articles(payload):
        article_id = _string(article, "id", "article_id", "aid")
        if not article_id and isinstance(article.get("article"), dict):
            article_id = _string(article["article"], "id", "article_id", "aid")
        if article_id and article_id not in seen:
            seen.add(article_id)
            article_ids.append(article_id)
    return article_ids


def _feed_articles(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        for key in ("articles", "list", "feeds", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if _string(data, "id", "article_id", "aid"):
            return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _finding_title(kind: str, match: Match) -> str:
    labels = {
        "match_analysis": "懂球帝比赛分析",
        "lineup": "懂球帝阵容信息",
        "situation": "懂球帝比赛局势",
        "poll": "懂球帝实时比分轮询",
        "highlights": "懂球帝比赛集锦",
        "home_team_feeds": "懂球帝主队近况资讯",
        "away_team_feeds": "懂球帝客队近况资讯",
    }
    return f"{labels.get(kind, '懂球帝情报')}：{match.home_team} vs {match.away_team}"


def _confidence(kind: str, payload: Any) -> float:
    if kind in {"lineup", "match_analysis"}:
        return 0.72 if _has_payload_data(payload) else 0.5
    if kind == "highlights":
        return 0.60
    if kind.endswith("_team_feeds"):
        if isinstance(payload, dict) and payload.get("article_details"):
            return 0.68
        return 0.62
    return 0.58
