from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from football_analysis.models import AgentFinding, Match, OddsSnapshot
from football_analysis.settings import AISettings, Settings

if TYPE_CHECKING:
    from football_analysis.scoring import MarketEdge

_ONE_X_TWO = "1x2"
_SELECTION_LABELS = {
    "HOME": "主胜",
    "DRAW": "平局",
    "AWAY": "客胜",
    "AH_HOME": "让球主胜",
    "AH_AWAY": "让球客胜",
    "OVER": "大球",
    "UNDER": "小球",
}


@dataclass(frozen=True)
class AISignal:
    market_type: str
    selection: str
    probabilities: dict[str, float]
    confidence: float
    analysis: str
    model: str
    raw: dict[str, Any] = field(default_factory=dict)


def build_ai_signal(
    match: Match,
    odds_snapshots: list[OddsSnapshot],
    findings: list[AgentFinding],
    settings: Settings,
) -> AISignal | None:
    ai_settings = settings.ai
    if not ai_settings.enabled:
        return None
    api_key = os.getenv(ai_settings.api_key_env, "").strip()
    if not api_key:
        return None

    from football_analysis.scoring import _best_market_edge

    edge = _best_market_edge(odds_snapshots, settings)
    if edge is None or edge.market_type not in ai_settings.markets:
        return None

    payload = _completion_payload(match, edge, findings, ai_settings)
    try:
        content = _call_completion(ai_settings, api_key, payload)
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    parsed = _parse_signal(edge, content)
    if parsed is None:
        return None
    probabilities, confidence, analysis = parsed
    return AISignal(
        market_type=edge.market_type,
        selection=edge.selection,
        probabilities=probabilities,
        confidence=confidence,
        analysis=analysis,
        model=ai_settings.model,
        raw={"edge_selection": edge.selection, "best_price": edge.best_price},
    )


def _information_context(findings: list[AgentFinding]) -> str:
    notes = [f"- [{finding.agent_name}] {finding.summary.strip()}" for finding in findings if finding.summary.strip()]
    return "\n".join(notes[:12]) if notes else "- 无额外情报"


def _completion_payload(
    match: Match,
    edge: MarketEdge,
    findings: list[AgentFinding],
    ai_settings: AISettings,
) -> dict[str, Any]:
    info_block = _information_context(findings)
    line_note = f"，盘口线 {edge.line}" if edge.line else ""
    header = (
        f"比赛：{match.home_team} (主) vs {match.away_team} (客)，联赛 {match.league}。\n"
        f"关注盘口：{edge.market_type}{line_note}。\n"
        f"该选项当前最高赔率 {edge.best_price:.2f}，市场均价 {edge.market_average:.2f}。\n"
        f"情报信号：\n{info_block}\n\n"
    )
    if edge.market_type == _ONE_X_TWO:
        task = (
            "请综合赔率隐含概率与情报，估计主胜/平局/客胜三种结果的真实概率（三者之和应接近1），"
            "并给出一句中文分析。严格只输出 JSON，不要输出任何额外文字：\n"
            '{"prob_home":0-1的小数,"prob_draw":0-1的小数,"prob_away":0-1的小数,'
            '"confidence":0-1的小数,"analysis":"中文一句话"}'
        )
    else:
        label = selection_label(edge.selection)
        task = (
            f"当前系统关注的投注选项是「{label}」。请综合赔率隐含概率与情报，"
            "估计该选项最终成立的真实概率，并给出一句中文分析。"
            "严格只输出 JSON，不要输出任何额外文字：\n"
            '{"prob_selection":0-1的小数,"confidence":0-1的小数,"analysis":"中文一句话"}'
        )
    return {
        "model": ai_settings.model,
        "messages": [
            {
                "role": "system",
                "content": "你是资深足球赛前分析师。基于赔率与情报做客观概率估计，只输出严格 JSON。",
            },
            {"role": "user", "content": header + task},
        ],
        "temperature": ai_settings.temperature,
        "max_tokens": ai_settings.max_tokens,
    }


def _call_completion(ai_settings: AISettings, api_key: str, payload: dict[str, Any]) -> str:
    url = ai_settings.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=ai_settings.timeout_seconds) as client:
        response = client.post(url, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def _parse_signal(edge: MarketEdge, content: str) -> tuple[dict[str, float], float, str] | None:
    obj = _extract_json_object(content)
    if obj is None:
        return None
    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    confidence = max(0.0, min(1.0, confidence))
    analysis = str(obj.get("analysis", "")).strip()

    if edge.market_type == _ONE_X_TWO:
        probabilities = _parse_one_x_two(obj)
    else:
        probabilities = _parse_single_selection(edge.selection, obj)
    if probabilities is None:
        return None
    return probabilities, confidence, analysis


def _parse_one_x_two(obj: dict[str, Any]) -> dict[str, float] | None:
    try:
        raw = {
            "HOME": float(obj["prob_home"]),
            "DRAW": float(obj["prob_draw"]),
            "AWAY": float(obj["prob_away"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
    total = sum(raw.values())
    if total <= 0.0 or any(value < 0.0 for value in raw.values()):
        return None
    return {key: round(value / total, 4) for key, value in raw.items()}


def _parse_single_selection(selection: str, obj: dict[str, Any]) -> dict[str, float] | None:
    try:
        prob = float(obj["prob_selection"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0.0 <= prob <= 1.0:
        return None
    return {selection: round(prob, 4)}


def _extract_json_object(content: str) -> dict[str, Any] | None:
    text = content.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def selection_label(selection: str) -> str:
    return _SELECTION_LABELS.get(selection, selection)
