from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from football_analysis.models import AgentFinding, EvidenceSource, Match
from football_analysis.settings import Settings


@dataclass(frozen=True)
class ResearchResult:
    title: str
    url: str | None
    publisher: str | None
    content: str


def build_research_finding(match: Match, results: list[ResearchResult]) -> AgentFinding | None:
    usable = [result for result in results if result.title and (result.content or result.url)]
    if len(usable) < 2:
        return None
    inferred = _infer_1x2_advice(match, usable)
    if inferred is None:
        return None
    selection, confidence, summary, risk_tags = inferred
    return AgentFinding(
        id=f"research-advisory-{match.id.replace(':', '-')}"[:120],
        match_id=match.id,
        agent_name="Research Advisory Agent",
        summary=summary,
        evidence_sources=[
            EvidenceSource(title=result.title[:220], url=result.url, publisher=result.publisher)
            for result in usable[:5]
        ],
        confidence=confidence,
        risk_tags=risk_tags,
        score_delta=round((confidence - 0.55) * 32.0, 2),
        payload={
            "advisory_recommendation": True,
            "market_type": "1x2",
            "selection": selection,
            "source": "external_research",
            "result_count": len(usable),
            "researched_at": datetime.utcnow().isoformat(),
        },
    )


def research_match(match: Match, provider: str = "auto", limit: int = 5) -> list[ResearchResult]:
    provider = provider.lower().strip()
    if provider == "auto":
        if os.getenv("EXA_API_KEY"):
            provider = "exa"
        elif os.getenv("FIRECRAWL_API_KEY"):
            provider = "firecrawl"
        elif os.getenv("TAVILY_API_KEY"):
            provider = "tavily"
        else:
            return []
    query = (
        f"{match.home_team} vs {match.away_team} FIFA World Cup preview "
        f"team news prediction odds"
    )
    if provider == "exa":
        return _search_exa(query, limit=limit)
    if provider == "firecrawl":
        return _search_firecrawl(query, limit=limit)
    if provider == "tavily":
        return _search_tavily(query, limit=limit)
    raise ValueError(f"Unsupported research provider: {provider}")


def research_and_store_match(
    match: Match,
    repository: Any,
    provider: str = "auto",
    limit: int = 5,
) -> AgentFinding | None:
    results = research_match(match, provider=provider, limit=limit)
    finding = build_research_finding(match, results)
    if finding is not None:
        repository.upsert_model("findings", finding.id, finding)
    return finding


def match_league_filter(match: Match, settings: Settings, league: str | None) -> bool:
    if not league:
        return True
    needle = league.strip().lower()
    values = {match.league.strip().lower()}
    for configured in settings.leagues:
        configured_values = {
            configured.code,
            configured.name,
            configured.football_data_org_code,
            configured.football_data_uk_code,
            *(configured.aliases or []),
        }
        normalized_values = {str(value).strip().lower() for value in configured_values if value}
        if match.league.strip().lower() in normalized_values:
            values.update(normalized_values)
    return needle in values or needle in match.league.strip().lower()


def _search_exa(query: str, limit: int) -> list[ResearchResult]:
    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        return []
    payload = {
        "query": query,
        "numResults": limit,
        "type": "auto",
        "contents": {"text": {"maxCharacters": 1200}},
    }
    with httpx.Client(timeout=20) as client:
        response = client.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )
    response.raise_for_status()
    data = response.json()
    return [
        ResearchResult(
            title=str(item.get("title") or "Untitled"),
            url=item.get("url"),
            publisher=_publisher_from_url(item.get("url")),
            content=str(item.get("text") or item.get("summary") or ""),
        )
        for item in data.get("results", [])[:limit]
    ]


def _search_firecrawl(query: str, limit: int) -> list[ResearchResult]:
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        return []
    payload = {"query": query, "limit": limit}
    with httpx.Client(timeout=20) as client:
        response = client.post(
            "https://api.firecrawl.dev/v1/search",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    response.raise_for_status()
    data = response.json()
    raw_results = data.get("data") or data.get("results") or []
    return [
        ResearchResult(
            title=str(item.get("title") or "Untitled"),
            url=item.get("url"),
            publisher=_publisher_from_url(item.get("url")),
            content=str(item.get("description") or item.get("markdown") or item.get("content") or ""),
        )
        for item in raw_results[:limit]
    ]


def _search_tavily(query: str, limit: int) -> list[ResearchResult]:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return []
    payload = {
        "query": query,
        "max_results": limit,
        "search_depth": "basic",
        "include_raw_content": False,
    }
    with httpx.Client(timeout=20) as client:
        response = client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    response.raise_for_status()
    data = response.json()
    return [
        ResearchResult(
            title=str(item.get("title") or "Untitled"),
            url=item.get("url"),
            publisher=_publisher_from_url(item.get("url")),
            content=str(item.get("content") or item.get("raw_content") or ""),
        )
        for item in data.get("results", [])[:limit]
    ]


def _infer_1x2_advice(match: Match, results: list[ResearchResult]) -> tuple[str, float, str, list[str]] | None:
    home_score = _support_score(match.home_team, results)
    away_score = _support_score(match.away_team, results)
    if max(home_score, away_score) < 2:
        return None
    if away_score > home_score:
        selection = "AWAY"
        team = match.away_team
        margin = away_score - home_score
    else:
        selection = "HOME"
        team = match.home_team
        margin = home_score - away_score
    confidence = min(0.84, 0.58 + 0.04 * min(5, len(results)) + 0.025 * min(4, margin))
    risk_tags = []
    if margin <= 1:
        risk_tags.append("research_consensus_thin")
    if margin <= 0:
        return None
    summary = (
        f"外部搜索证据倾向 {team} 不败/取胜；"
        f"基于 {len(results)} 条赛前预览、赔率或阵容信息生成 1x2 建议。"
    )
    return selection, round(confidence, 3), summary, risk_tags


def _support_score(team: str, results: list[ResearchResult]) -> int:
    team_key = team.casefold()
    score = 0
    positive_patterns = (
        f"{team_key} to win",
        f"back {team_key}",
        f"{team_key} win",
        f"{team_key} wins",
        f"{team_key} victory",
        f"{team_key} are favorites",
        f"{team_key} are favourites",
        f"{team_key} favorites",
        f"{team_key} favourites",
        f"pick {team_key}",
        f"prediction {team_key}",
        f"{team_key} 1-0",
        f"{team_key} 2-0",
        f"{team_key} 2-1",
        f"{team_key} 3-1",
    )
    for result in results:
        text = f"{result.title} {result.content}".casefold()
        score += sum(2 for pattern in positive_patterns if pattern in text)
    return score


def _publisher_from_url(url: str | None) -> str | None:
    if not url:
        return None
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return host.removeprefix("www.") or None
