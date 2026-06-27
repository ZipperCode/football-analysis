from __future__ import annotations

import base64
import gzip
import hashlib
import time
import uuid
from typing import Any

from Crypto.Cipher import AES

from football_analysis.datasources.base import ClientContext, DataSourceError
from football_analysis.models import Match, MatchStatus, MarketType, OddsSnapshot


DEFAULT_AES_KEY = b"c4GJx$jeFK@v*P#t"
DEFAULT_URL_KEY = "jilI2L3DWkhvIL5Ekh7WFOfkciw6ECiw"
DEFAULT_VERSION = "11.0.0"
DEFAULT_CHANNEL = "LeiSu"


class LeisuClient:
    provider = "leisu"

    def __init__(self, context: ClientContext, aid: str = "", ver: str = DEFAULT_VERSION, channel: str = DEFAULT_CHANNEL):
        if not context.source.enabled:
            raise DataSourceError("leisu_source_disabled")
        self.context = context
        self.aid = aid
        self.ver = ver
        self.channel = channel
        self.gateway_url = context.source.base_url.rstrip("/")
        self.url_key = DEFAULT_URL_KEY
        self.time_offset = 0
        self.start_time = int(time.time() * 1000)
        self._synced = False

    def sync_config(self) -> dict[str, Any]:
        path = "/v1/app/leisu/info"
        response = self.context.http.get_json(
            provider=self.provider,
            url=f"{self.gateway_url}{path}",
            endpoint=path,
            headers=self._headers(path),
            ttl_seconds=self.context.settings.cache.default_ttl_seconds,
        )
        if response.error:
            raise DataSourceError(f"leisu_sync_failed:{response.error}")
        if not isinstance(response.payload, dict):
            raise DataSourceError("leisu_invalid_payload:config_expected_object")
        data = decode_config_payload(response.payload)
        self._apply_config(data)
        self._synced = True
        return data

    def fixtures(self, date: str) -> list[Match]:
        self._ensure_synced()
        today = time.strftime("%Y-%m-%d", time.localtime())
        if date == today:
            payload = self._get("/v1/app/match/football/today_list", {"n": 1})
        else:
            payload = self._get("/v1/app/match/football/match_list", {"n": 1, "date": date})
        return map_fixtures(payload)

    def odds(self, match_id: str) -> list[OddsSnapshot]:
        self._ensure_synced()
        payload = self._get("/v1/app/match/common/odds_list", {"sport_id": 1, "match_id": match_id})
        return map_odds(payload, match_id=match_id)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = dict(params or {})
        query["auth_key"] = generate_auth_key(path, self.url_key, timestamp=int(time.time()) + self.time_offset)
        response = self.context.http.get_json(
            provider=self.provider,
            url=f"{self.gateway_url}{path}",
            endpoint=path,
            headers=self._headers(path),
            params=dict(sorted(query.items())),
        )
        if response.error:
            raise DataSourceError(f"leisu_auth_failed:{response.error}")
        if not isinstance(response.payload, dict):
            raise DataSourceError(f"leisu_invalid_payload:{path}:expected_object")
        return response.payload

    def _ensure_synced(self) -> None:
        if not self._synced:
            self.sync_config()

    def _headers(self, path: str) -> dict[str, str]:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        return {
            "ver": self.ver,
            "platform": "1",
            "start": str(self.start_time),
            "time": now,
            "User-Agent": f"leisu/{self.ver}/{self.channel}/1/Xiaomi;Mi 11;Android;13;33/{self.time_offset}",
            "sign": generate_sign(self.aid, now, self.ver),
            "cdid": str(uuid.uuid5(uuid.NAMESPACE_DNS, self.aid + "device_uuid")),
            "channel": self.channel,
            "device_id": self.aid,
            "aid": self.aid,
        }

    def _apply_config(self, data: dict[str, Any]) -> None:
        gateway_url = data.get("gateway_url")
        if isinstance(gateway_url, str) and gateway_url:
            self.gateway_url = gateway_url.rstrip("/")
        gateway_time = data.get("gateway_time")
        if isinstance(gateway_time, str) and gateway_time:
            try:
                server_epoch = int(time.mktime(time.strptime(gateway_time, "%Y-%m-%d %H:%M:%S")))
                self.time_offset = server_epoch - int(time.time())
            except ValueError:
                pass
        other = data.get("other") if isinstance(data.get("other"), dict) else {}
        rk = other.get("rk") if isinstance(other, dict) else None
        if isinstance(rk, str) and rk:
            self.url_key = decrypt_aes_zero_padded(rk).strip() or self.url_key


def generate_sign(aid: str, time_str: str, ver: str = DEFAULT_VERSION) -> str:
    return _md5(f"{aid}{time_str}{ver}")


def generate_auth_key(path: str, key: str, timestamp: int | None = None, uid: str | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    request_uid = (uid or str(uuid.uuid4())).replace("-", "")
    digest = _md5(f"{path}-{ts}-{request_uid}-0-{key}")
    return f"{ts}-{request_uid}-0-{digest}"


def decode_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    code = payload.get("code")
    data = payload.get("data")
    if code == 0 and isinstance(data, dict):
        return data
    if code == 1 and isinstance(data, str) and data:
        decoded = decrypt_aes_zero_padded(data)
        import json

        try:
            decoded_payload = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise DataSourceError(f"leisu_sync_failed:code=1:json:{exc}") from exc
        nested = decoded_payload.get("data", decoded_payload) if isinstance(decoded_payload, dict) else None
        if isinstance(nested, dict):
            return nested
    if isinstance(code, int) and 100 < code <= 126 and isinstance(data, str):
        decoded = caesar_gzip_base64_decode(data, shift=code - 100)
        raise DataSourceError(f"leisu_sync_failed:code={code}:decoded_prefix={decoded[:120]!r}")
    raise DataSourceError(f"leisu_sync_failed:code={code}:msg={payload.get('msg')}")


def decrypt_aes_zero_padded(ciphertext_b64: str, key: bytes = DEFAULT_AES_KEY) -> str:
    raw = base64.b64decode(ciphertext_b64)
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.decrypt(raw).rstrip(b"\x00").decode("utf-8", errors="replace")


def caesar_gzip_base64_decode(value: str, shift: int = 1) -> str:
    shifted: list[str] = []
    for char in value:
        if "A" <= char <= "Z":
            code = ord(char) - shift
            shifted.append(chr(code + 26 if code < ord("A") else code))
        elif "a" <= char <= "z":
            code = ord(char) - shift
            shifted.append(chr(code + 26 if code < ord("a") else code))
        else:
            shifted.append(char)
    shifted_text = "".join(shifted)
    try:
        return gzip.decompress(base64.b64decode(shifted_text)).decode("utf-8")
    except Exception:
        return shifted_text


def map_fixtures(payload: dict[str, Any]) -> list[Match]:
    items = _extract_items(payload)
    matches: list[Match] = []
    for item in items:
        match_id = _string(item, "match_id", "id", "mid")
        home = _string(item, "home_name", "home_team_name", "homeTeamName", "team_A_name")
        away = _string(item, "away_name", "away_team_name", "awayTeamName", "team_B_name")
        kickoff = _string(item, "match_time", "start_time", "start_play", "time")
        if not match_id or not home or not away or not kickoff:
            continue
        matches.append(
            Match(
                id=f"leisu:{match_id}",
                league=_string(item, "competition_name", "comp_name", "league_name") or "Unknown",
                home_team=home,
                away_team=away,
                kickoff_at=_parse_datetime(kickoff),
                status=_map_status(_string(item, "status", "state", "match_status")),
                data_completeness=0.68,
                home_score=_safe_int(item.get("home_score")),
                away_score=_safe_int(item.get("away_score")),
                external_ids={"leisu_match": match_id},
            )
        )
    return matches


def map_odds(payload: dict[str, Any], match_id: str) -> list[OddsSnapshot]:
    items = _extract_items(payload)
    snapshots: list[OddsSnapshot] = []
    for index, item in enumerate(items):
        bookmaker = _string(item, "company_name", "bookmaker", "name", "cid") or "Leisu"
        for market_type, keys in (
            (MarketType.one_x_two, ("home", "draw", "away")),
            (MarketType.asian_handicap, ("home", "handicap", "away")),
            (MarketType.over_under, ("over", "line", "under")),
        ):
            values = [_safe_float(item.get(key)) for key in keys]
            if market_type is not MarketType.one_x_two and values[1] is None:
                continue
            if not any(value is not None for value in values):
                continue
            odds = _market_odds(market_type, keys, values)
            if odds:
                line = str(values[1]) if market_type is not MarketType.one_x_two and values[1] is not None else None
                snapshots.append(
                    OddsSnapshot(
                        id=f"leisu:{match_id}:{index}:{market_type.value}:{line or 'main'}",
                        match_id=f"leisu:{match_id}",
                        market_type=market_type,
                        line=line,
                        source="leisu",
                        bookmaker=bookmaker,
                        outcome_odds=odds,
                        best_price=dict(odds),
                    )
                )
    return snapshots


def _extract_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("list", "matches", "matchList", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _market_odds(market_type: MarketType, keys: tuple[str, str, str], values: list[float | None]) -> dict[str, float]:
    if market_type is MarketType.one_x_two:
        selections = ("HOME", "DRAW", "AWAY")
    elif market_type is MarketType.asian_handicap:
        selections = ("HOME", "", "AWAY")
    else:
        selections = ("OVER", "", "UNDER")
    return {selection: value for selection, value in zip(selections, values) if selection and value is not None}


def _string(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _map_status(value: str) -> MatchStatus:
    lowered = value.lower()
    if lowered in {"ft", "finished", "finish", "ended", "8", "完场"}:
        return MatchStatus.finished
    if lowered in {"postponed", "cancelled", "canceled", "延期", "取消"}:
        return MatchStatus.postponed
    return MatchStatus.scheduled


def _parse_datetime(value: str):
    from datetime import datetime

    normalized = value.strip().replace("Z", "+00:00")
    if normalized.isdigit():
        timestamp = int(normalized)
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        return datetime.fromtimestamp(timestamp)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            pass
    return datetime.fromisoformat(normalized)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()
