from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any


JsonPayload = dict[str, Any] | list[Any]
BrokerRequestSender = Callable[[str, JsonPayload, dict[str, str], float], Any]

BETFAIR_PLACE_ORDERS_METHOD = "SportsAPING/v1.0/placeOrders"
BETFAIR_LIST_MARKET_CATALOGUE_METHOD = "SportsAPING/v1.0/listMarketCatalogue"
BETFAIR_DEFAULT_TIMEOUT_SECONDS = 20.0


def execute_broker_plan(
    plan: dict[str, Any],
    execute_broker_orders: bool = False,
    max_items: int | None = None,
    request_sender: BrokerRequestSender | None = None,
    request_timeout_seconds: float = BETFAIR_DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    broker = dict(plan.get("broker") or {})
    provider = str(broker.get("provider") or "")
    checked_at = datetime.now(timezone.utc).isoformat()
    issues = list(plan.get("issues") or [])
    if provider != "betfair":
        return {
            "checked_at": checked_at,
            "status": "unsupported_broker",
            "mode": "broker_live" if execute_broker_orders else "dry_run",
            "execute_broker_orders": execute_broker_orders,
            "broker_id": plan.get("broker_id"),
            "broker_provider": provider or None,
            "selected_count": 0,
            "sent_count": 0,
            "dry_run_count": 0,
            "error_count": 0,
            "issues": [f"unsupported_broker_provider:{provider or 'missing'}"],
            "plan": plan,
            "records": [],
        }
    if plan.get("ready_for_broker_execution") is not True:
        return {
            "checked_at": checked_at,
            "status": "blocked",
            "mode": "broker_live" if execute_broker_orders else "dry_run",
            "execute_broker_orders": execute_broker_orders,
            "broker_id": plan.get("broker_id"),
            "broker_provider": provider,
            "plan_status": plan.get("status"),
            "selected_count": 0,
            "sent_count": 0,
            "dry_run_count": 0,
            "error_count": 0,
            "issues": issues,
            "plan": plan,
            "records": [],
        }

    items = [
        item
        for item in plan.get("items", [])
        if item.get("order_payload") and not item.get("missing_fields")
    ]
    if max_items is not None:
        items = items[: max(0, max_items)]
    headers = _betfair_headers()
    request_sender = request_sender or _post_json_rpc
    records: list[dict[str, Any]] = []
    for item in items:
        request_preview = _betfair_request_preview(broker, item)
        if not execute_broker_orders:
            records.append(
                {
                    **_broker_record_identity(item),
                    "status": "dry_run",
                    "request": request_preview,
                    "response": None,
                    "error": None,
                }
            )
            continue
        try:
            response = request_sender(
                request_preview["url"],
                request_preview["body"],
                headers,
                request_timeout_seconds,
            )
            status = _betfair_response_record_status(response)
            records.append(
                {
                    **_broker_record_identity(item),
                    "status": status,
                    "request": request_preview,
                    "response": response,
                    "error": None if status == "sent" else "broker_response_error",
                }
            )
        except Exception as exc:  # pragma: no cover - defensive around external IO.
            records.append(
                {
                    **_broker_record_identity(item),
                    "status": "error",
                    "request": request_preview,
                    "response": None,
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
            )

    error_count = sum(1 for record in records if record["status"] == "error")
    sent_count = sum(1 for record in records if record["status"] == "sent")
    dry_run_count = sum(1 for record in records if record["status"] == "dry_run")
    if not records:
        status = "no_open_items"
    elif error_count:
        status = "partial_error" if sent_count else "error"
    elif execute_broker_orders:
        status = "executed"
    else:
        status = "dry_run"
    return {
        "checked_at": checked_at,
        "status": status,
        "mode": "broker_live" if execute_broker_orders else "dry_run",
        "execute_broker_orders": execute_broker_orders,
        "broker_id": plan.get("broker_id"),
        "broker_provider": provider,
        "plan_status": plan.get("status"),
        "selected_count": len(items),
        "sent_count": sent_count,
        "dry_run_count": dry_run_count,
        "error_count": error_count,
        "issues": [],
        "plan": plan,
        "records": records,
    }


def discover_broker_mappings(
    plan: dict[str, Any],
    fetch_remote: bool = False,
    max_items: int | None = None,
    request_sender: BrokerRequestSender | None = None,
    request_timeout_seconds: float = BETFAIR_DEFAULT_TIMEOUT_SECONDS,
    max_results: int = 20,
    match_window_hours: int = 36,
) -> dict[str, Any]:
    broker = dict(plan.get("broker") or {})
    provider = str(broker.get("provider") or "")
    checked_at = datetime.now(timezone.utc).isoformat()
    if provider != "betfair":
        return {
            "checked_at": checked_at,
            "status": "unsupported_broker",
            "mode": "remote_read" if fetch_remote else "dry_run",
            "fetch_remote": fetch_remote,
            "broker_id": plan.get("broker_id"),
            "broker_provider": provider or None,
            "selected_count": 0,
            "discovered_count": 0,
            "dry_run_count": 0,
            "error_count": 0,
            "issues": [f"unsupported_broker_provider:{provider or 'missing'}"],
            "plan": plan,
            "records": [],
        }

    credential_status = dict(broker.get("credential_status") or {})
    missing_credentials = [env_name for env_name, present in credential_status.items() if not present]
    if fetch_remote and missing_credentials:
        return {
            "checked_at": checked_at,
            "status": "blocked",
            "mode": "remote_read",
            "fetch_remote": fetch_remote,
            "broker_id": plan.get("broker_id"),
            "broker_provider": provider,
            "selected_count": 0,
            "discovered_count": 0,
            "dry_run_count": 0,
            "error_count": 0,
            "issues": [f"missing_broker_credential:{env_name}" for env_name in missing_credentials],
            "plan": plan,
            "records": [],
        }

    queue = dict(plan.get("queue") or {})
    queue_items = list(queue.get("items") or [])
    if max_items is not None:
        queue_items = queue_items[: max(0, max_items)]
    request_sender = request_sender or _post_json_rpc
    headers = _betfair_headers()
    records: list[dict[str, Any]] = []
    for item in queue_items:
        request_preview = _betfair_market_catalogue_request_preview(
            broker,
            item,
            max_results=max_results,
            match_window_hours=match_window_hours,
        )
        if not fetch_remote:
            records.append(
                {
                    **_mapping_record_identity(item),
                    "status": "dry_run",
                    "request": request_preview,
                    "response": None,
                    "candidates": [],
                    "suggested_external_ids": {},
                    "error": None,
                }
            )
            continue
        try:
            response = request_sender(
                request_preview["url"],
                request_preview["body"],
                headers,
                request_timeout_seconds,
            )
            candidates = _betfair_mapping_candidates(item, response)
            records.append(
                {
                    **_mapping_record_identity(item),
                    "status": "discovered" if candidates else "no_candidates",
                    "request": request_preview,
                    "response": response,
                    "candidates": candidates,
                    "suggested_external_ids": candidates[0]["external_ids_patch"] if candidates else {},
                    "error": None,
                }
            )
        except Exception as exc:  # pragma: no cover - defensive around external IO.
            records.append(
                {
                    **_mapping_record_identity(item),
                    "status": "error",
                    "request": request_preview,
                    "response": None,
                    "candidates": [],
                    "suggested_external_ids": {},
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
            )

    error_count = sum(1 for record in records if record["status"] == "error")
    discovered_count = sum(1 for record in records if record["status"] == "discovered")
    dry_run_count = sum(1 for record in records if record["status"] == "dry_run")
    if not records:
        status = "no_open_items"
    elif error_count:
        status = "partial_error" if discovered_count or dry_run_count else "error"
    elif fetch_remote:
        status = "discovered" if discovered_count else "no_candidates"
    else:
        status = "dry_run"
    return {
        "checked_at": checked_at,
        "status": status,
        "mode": "remote_read" if fetch_remote else "dry_run",
        "fetch_remote": fetch_remote,
        "broker_id": plan.get("broker_id"),
        "broker_provider": provider,
        "plan_status": plan.get("status"),
        "queue_status": queue.get("status"),
        "selected_count": len(queue_items),
        "discovered_count": discovered_count,
        "dry_run_count": dry_run_count,
        "error_count": error_count,
        "issues": [],
        "plan": plan,
        "records": records,
    }


def _betfair_request_preview(broker: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": broker["base_url"],
        "headers": _redacted_betfair_headers(),
        "body": _betfair_place_orders_body(item),
    }


def _betfair_market_catalogue_request_preview(
    broker: dict[str, Any],
    item: dict[str, Any],
    max_results: int,
    match_window_hours: int,
) -> dict[str, Any]:
    return {
        "url": broker["base_url"],
        "headers": _redacted_betfair_headers(),
        "body": _betfair_market_catalogue_body(
            item,
            max_results=max_results,
            match_window_hours=match_window_hours,
        ),
    }


def _betfair_market_catalogue_body(
    item: dict[str, Any],
    max_results: int,
    match_window_hours: int,
) -> list[dict[str, Any]]:
    kickoff = _parse_datetime(item.get("kickoff_at"))
    market_filter: dict[str, Any] = {
        "eventTypeIds": ["1"],
        "textQuery": f"{item.get('home_team', '')} {item.get('away_team', '')}".strip(),
    }
    if kickoff is not None:
        market_filter["marketStartTime"] = {
            "from": (kickoff - timedelta(hours=match_window_hours)).isoformat().replace("+00:00", "Z"),
            "to": (kickoff + timedelta(hours=match_window_hours)).isoformat().replace("+00:00", "Z"),
        }
    return [
        {
            "jsonrpc": "2.0",
            "method": BETFAIR_LIST_MARKET_CATALOGUE_METHOD,
            "params": {
                "filter": market_filter,
                "marketProjection": [
                    "EVENT",
                    "MARKET_START_TIME",
                    "MARKET_DESCRIPTION",
                    "RUNNER_DESCRIPTION",
                ],
                "sort": "FIRST_TO_START",
                "maxResults": str(max(1, max_results)),
            },
            "id": 1,
        }
    ]


def _betfair_place_orders_body(item: dict[str, Any]) -> list[dict[str, Any]]:
    order = dict(item["order_payload"])
    instruction: dict[str, Any] = {
        "selectionId": _json_numeric(order["selection_id"]),
        "side": order["side"],
        "orderType": order["order_type"],
        "limitOrder": {
            "size": float(order["size"]),
            "price": float(order["limit_price"]),
            "persistenceType": "LAPSE",
        },
    }
    handicap = order.get("handicap") or item.get("broker_refs", {}).get("handicap")
    if handicap not in (None, ""):
        instruction["handicap"] = _json_numeric(handicap)
    return [
        {
            "jsonrpc": "2.0",
            "method": BETFAIR_PLACE_ORDERS_METHOD,
            "params": {
                "marketId": str(order["market_id"]),
                "instructions": [instruction],
                "customerRef": _betfair_customer_ref(str(order["customer_order_ref"])),
            },
            "id": 1,
        }
    ]


def _broker_record_identity(item: dict[str, Any]) -> dict[str, Any]:
    order = dict(item.get("order_payload") or {})
    return {
        "idempotency_key": item.get("idempotency_key"),
        "recommendation_id": item.get("recommendation_id"),
        "match_id": item.get("match_id"),
        "market_id": order.get("market_id"),
        "selection_id": order.get("selection_id"),
        "side": order.get("side"),
        "limit_price": order.get("limit_price"),
        "size": order.get("size"),
        "currency": order.get("currency"),
        "customer_ref": _betfair_customer_ref(str(order.get("customer_order_ref") or "")),
    }


def _mapping_record_identity(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "idempotency_key": item.get("idempotency_key"),
        "recommendation_id": item.get("recommendation_id"),
        "match_id": item.get("match_id"),
        "league": item.get("league"),
        "home_team": item.get("home_team"),
        "away_team": item.get("away_team"),
        "kickoff_at": item.get("kickoff_at"),
        "market_type": item.get("market_type"),
        "selection": item.get("selection"),
        "normalized_selection": item.get("normalized_selection"),
    }


def _betfair_headers() -> dict[str, str]:
    return {
        "X-Application": os.getenv("BETFAIR_APP_KEY") or "",
        "X-Authentication": os.getenv("BETFAIR_SESSION_TOKEN") or "",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _redacted_betfair_headers() -> dict[str, str]:
    headers = _betfair_headers()
    return {
        key: "<redacted>" if value and key in {"X-Application", "X-Authentication"} else value
        for key, value in headers.items()
    }


def _betfair_customer_ref(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]", "", value)
    if 1 <= len(normalized) <= 32:
        return normalized
    return "fa" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:30]


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_numeric(value: Any) -> int | float | str:
    text = str(value)
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _post_json_rpc(
    url: str,
    body: JsonPayload,
    headers: dict[str, str],
    timeout_seconds: float,
) -> JsonPayload:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload: JsonPayload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"description": raw[:200]}
        if isinstance(payload, dict):
            payload.setdefault("ok", False)
            payload["status_code"] = exc.code
        return payload
    except urllib.error.URLError as exc:
        return {"ok": False, "error": str(exc.reason)}


def _betfair_mapping_candidates(item: dict[str, Any], response: Any) -> list[dict[str, Any]]:
    catalogues = _json_rpc_result(response)
    if not isinstance(catalogues, list):
        return []
    candidates = [
        candidate
        for catalogue in catalogues
        for candidate in _catalogue_candidates(item, catalogue)
    ]
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        candidates,
        key=lambda candidate: (
            confidence_order.get(str(candidate["confidence"]), 99),
            -float(candidate.get("total_matched") or 0.0),
        ),
    )


def _json_rpc_result(response: Any) -> Any:
    envelope = response[0] if isinstance(response, list) and response else response
    if not isinstance(envelope, dict) or envelope.get("error"):
        return None
    return envelope.get("result")


def _catalogue_candidates(item: dict[str, Any], catalogue: Any) -> list[dict[str, Any]]:
    if not isinstance(catalogue, dict):
        return []
    market_id = catalogue.get("marketId")
    runners = catalogue.get("runners") or []
    if not market_id or not isinstance(runners, list):
        return []
    event = catalogue.get("event") if isinstance(catalogue.get("event"), dict) else {}
    event_name = str(event.get("name") or "")
    market_name = str(catalogue.get("marketName") or "")
    confidence_base = _event_confidence(
        event_name,
        str(item.get("home_team") or ""),
        str(item.get("away_team") or ""),
    )
    candidates: list[dict[str, Any]] = []
    for runner in runners:
        if not isinstance(runner, dict) or not runner.get("selectionId"):
            continue
        runner_confidence = _runner_confidence(item, runner)
        if runner_confidence is None:
            continue
        confidence = _merge_confidence(confidence_base, runner_confidence)
        patch = {
            "betfair_market_id": str(market_id),
            f"betfair_selection_id_{item.get('normalized_selection')}": str(runner["selectionId"]),
        }
        if runner.get("handicap") not in (None, ""):
            patch[f"betfair_handicap_{item.get('normalized_selection')}"] = str(runner["handicap"])
        candidates.append(
            {
                "confidence": confidence,
                "market_id": str(market_id),
                "market_name": market_name,
                "market_start_time": catalogue.get("marketStartTime"),
                "event_name": event_name,
                "selection_id": str(runner["selectionId"]),
                "runner_name": runner.get("runnerName"),
                "handicap": runner.get("handicap"),
                "total_matched": catalogue.get("totalMatched"),
                "external_ids_patch": patch,
            }
        )
    return candidates


def _event_confidence(event_name: str, home_team: str, away_team: str) -> str:
    event_norm = _normal_name(event_name)
    home_norm = _normal_name(home_team)
    away_norm = _normal_name(away_team)
    if home_norm and away_norm and home_norm in event_norm and away_norm in event_norm:
        return "high"
    if home_norm and home_norm in event_norm:
        return "medium"
    if away_norm and away_norm in event_norm:
        return "medium"
    return "low"


def _runner_confidence(item: dict[str, Any], runner: dict[str, Any]) -> str | None:
    runner_name = str(runner.get("runnerName") or "")
    aliases = _selection_aliases(item)
    runner_norm = _normal_name(runner_name)
    alias_norms = [_normal_name(alias) for alias in aliases if alias]
    if runner_norm in alias_norms:
        return "high"
    if any(alias_norm and (alias_norm in runner_norm or runner_norm in alias_norm) for alias_norm in alias_norms):
        return "medium"
    return None


def _selection_aliases(item: dict[str, Any]) -> list[str]:
    normalized_selection = str(item.get("normalized_selection") or item.get("selection") or "").upper()
    selection = str(item.get("selection") or "").upper()
    home_team = str(item.get("home_team") or "")
    away_team = str(item.get("away_team") or "")
    if normalized_selection.startswith("AH_HOME") or selection in {"HOME", "1"}:
        return [home_team, "Home", "1"]
    if normalized_selection.startswith("AH_AWAY") or selection in {"AWAY", "2"}:
        return [away_team, "Away", "2"]
    if "DRAW" in normalized_selection or selection in {"DRAW", "X"}:
        return ["Draw", "The Draw", "X"]
    if normalized_selection.startswith("OVER") or selection.startswith("OVER"):
        return ["Over"]
    if normalized_selection.startswith("UNDER") or selection.startswith("UNDER"):
        return ["Under"]
    return [str(item.get("selection") or "")]


def _normal_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _merge_confidence(left: str, right: str) -> str:
    order = {"high": 0, "medium": 1, "low": 2}
    values = {index: name for name, index in order.items()}
    return values[max(order.get(left, 2), order.get(right, 2))]


def _betfair_response_record_status(response: Any) -> str:
    if isinstance(response, dict) and response.get("ok") is False:
        return "error"
    envelope = response[0] if isinstance(response, list) and response else response
    if not isinstance(envelope, dict):
        return "error"
    if envelope.get("error"):
        return "error"
    result = envelope.get("result")
    if not isinstance(result, dict):
        return "sent"
    report_status = str(result.get("status") or "").upper()
    if report_status in {"FAILURE", "PROCESSED_WITH_ERRORS", "TIMEOUT"}:
        return "error"
    for instruction_report in result.get("instructionReports") or []:
        if not isinstance(instruction_report, dict):
            return "error"
        instruction_status = str(instruction_report.get("status") or "").upper()
        if instruction_status and instruction_status != "SUCCESS":
            return "error"
    return "sent"
