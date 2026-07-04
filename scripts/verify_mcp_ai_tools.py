from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

import football_analysis.mcp_server as mcp_server
from football_analysis.models import MarketType, Recommendation, RecommendationStatus
from football_analysis.production import _analysis_advice_item


@dataclass(frozen=True)
class FakeNestedMetrics:
    brier: float
    hits: int


@dataclass(frozen=True)
class FakeQualityReport:
    status: str
    metrics: FakeNestedMetrics


@dataclass(frozen=True)
class FakeService:
    repository: object
    settings: object


def _patch_attrs(module: object, replacements: dict[str, object]) -> dict[str, object]:
    originals: dict[str, object] = {}
    for name, replacement in replacements.items():
        originals[name] = getattr(module, name)
        setattr(module, name, replacement)
    return originals


def _restore_attrs(module: object, originals: dict[str, object]) -> None:
    for name, original in originals.items():
        setattr(module, name, original)


def _fake_service() -> FakeService:
    return FakeService(repository=object(), settings=object())


def _make_recommendation(*, ai_analysis: dict[str, object] | None = None) -> Recommendation:
    return Recommendation(
        id="rec-1",
        match_id="match-1",
        market_type=MarketType.one_x_two,
        selection="HOME",
        status=RecommendationStatus.recommended,
        value_score=71.5,
        risk_score=18.0,
        confidence=0.82,
        stake_units=0.6,
        odds_basis={"bookmaker": "test-book"},
        score_breakdown={"ai_analysis": ai_analysis} if ai_analysis is not None else {},
        risk_tags=["low-risk"],
        reason="local fixture",
        risk_notice="test risk notice",
    )


def _assert_analysis_advice_item_ai_analysis() -> None:
    recommendation = _make_recommendation(ai_analysis={"confidence": 0.76, "summary": "local"})
    item = _analysis_advice_item(recommendation, {})

    assert item["ai_analysis"] == {"confidence": 0.76, "summary": "local"}, "ai_analysis must be exposed"


def _assert_analysis_advice_item_defaults_to_empty_ai_analysis() -> None:
    recommendation = _make_recommendation()
    item = _analysis_advice_item(recommendation, {})

    assert item["ai_analysis"] == {}, "missing ai_analysis must default to an empty dict"


def _assert_evaluate_ai_quality_uses_dataclass_conversion() -> None:
    service = _fake_service()
    observed_target_date: date | None = None
    observed_league: str | None = None
    observed_service: FakeService | None = None

    def fake_get_service() -> FakeService:
        return service

    def fake_build_ai_quality_evaluation(
        received_service: FakeService,
        *,
        target_date: date,
        league: str,
    ) -> FakeQualityReport:
        nonlocal observed_target_date, observed_league, observed_service
        observed_target_date = target_date
        observed_league = league
        observed_service = received_service
        return FakeQualityReport(status="ok", metrics=FakeNestedMetrics(brier=0.12, hits=3))

    originals = _patch_attrs(
        mcp_server,
        {
            "get_service": fake_get_service,
            "build_ai_quality_evaluation": fake_build_ai_quality_evaluation,
        },
    )
    try:
        result: dict[str, object] = mcp_server.evaluate_ai_quality("2026-07-04", "EPL")
    finally:
        _restore_attrs(mcp_server, originals)

    assert result == {"status": "ok", "metrics": {"brier": 0.12, "hits": 3}}, "dataclass report must convert to dict"
    assert observed_target_date == date.fromisoformat("2026-07-04"), "target date must be parsed from ISO text"
    assert observed_league == "EPL", "league must pass through unchanged"
    assert observed_service is service, "builder must receive the service returned by get_service"


def _assert_review_strategies_returns_dict_shape() -> None:
    service = FakeService(repository=object(), settings=object())
    payload: dict[str, object] = {
        "status": "ok",
        "strategies": [{"code": "E0", "action": "pause_live"}],
        "summary": {"approved": 1, "blocked": 0},
    }
    observed_repository: object | None = None
    observed_settings: object | None = None

    def fake_get_service() -> FakeService:
        return service

    def fake_build_live_review(repository: object, settings: object) -> dict[str, object]:
        nonlocal observed_repository, observed_settings
        observed_repository = repository
        observed_settings = settings
        return payload

    originals = _patch_attrs(
        mcp_server,
        {
            "get_service": fake_get_service,
            "build_live_review": fake_build_live_review,
        },
    )
    try:
        result: dict[str, object] = mcp_server.review_strategies()
    finally:
        _restore_attrs(mcp_server, originals)

    assert result == payload, "review_strategies must return the dict-shaped payload"
    assert payload == {
        "status": "ok",
        "strategies": [{"code": "E0", "action": "pause_live"}],
        "summary": {"approved": 1, "blocked": 0},
    }, "review payload must not be mutated"
    assert observed_repository is service.repository, "builder must receive repository from service"
    assert observed_settings is service.settings, "builder must receive settings from service"


def _assert_get_analysis_refresh_false_skips_refresh() -> None:
    calls: list[str] = []
    service = _fake_service()

    def fake_get_service() -> FakeService:
        calls.append("get_service")
        return service

    def fake_run_live_refresh(
        service_obj: SimpleNamespace,
        *,
        date: str,
        fixture_source: str,
        odds_source: str,
        scope: str,
        include_past: bool,
        dry_run: bool,
        allow_odds_fallback: bool,
    ) -> dict[str, object]:
        calls.append("refresh")
        _ = service_obj
        _ = date
        _ = fixture_source
        _ = odds_source
        _ = scope
        _ = include_past
        _ = dry_run
        _ = allow_odds_fallback
        return {"status": "should-not-run"}

    def fake_build_analysis_advice_report(service_obj: SimpleNamespace, *, limit: int, hours: int) -> dict[str, object]:
        calls.append("report")
        assert service_obj is service, "report builder must receive the service returned by get_service"
        assert limit == 8, "default limit must pass through"
        assert hours == 24, "default hours must pass through"
        return {"status": "ready", "items": []}

    originals = _patch_attrs(
        mcp_server,
        {
            "get_service": fake_get_service,
            "run_live_refresh": fake_run_live_refresh,
            "build_analysis_advice_report": fake_build_analysis_advice_report,
        },
    )
    try:
        result: dict[str, object] = mcp_server.get_analysis()
    finally:
        _restore_attrs(mcp_server, originals)

    assert result == {"status": "ready", "items": []}, "refresh=False must return the report shape"
    assert calls == ["get_service", "report"], "refresh=False must not call run_live_refresh"


def _assert_get_analysis_refresh_true_refreshes_before_report() -> None:
    calls: list[str] = []
    service = _fake_service()

    def fake_get_service() -> FakeService:
        calls.append("get_service")
        return service

    def fake_run_live_refresh(
        service_obj: SimpleNamespace,
        *,
        date: str,
        fixture_source: str,
        odds_source: str,
        scope: str,
        include_past: bool,
        dry_run: bool,
        allow_odds_fallback: bool,
    ) -> dict[str, object]:
        calls.append("refresh")
        _ = service_obj
        _ = date
        _ = fixture_source
        _ = odds_source
        _ = scope
        _ = include_past
        _ = dry_run
        _ = allow_odds_fallback
        return {"status": "refreshed"}

    def fake_build_analysis_advice_report(service_obj: SimpleNamespace, *, limit: int, hours: int) -> dict[str, object]:
        calls.append("report")
        assert service_obj is service, "report builder must receive the service returned by get_service"
        assert limit == 8, "default limit must pass through"
        assert hours == 24, "default hours must pass through"
        return {"status": "ready", "items": []}

    originals = _patch_attrs(
        mcp_server,
        {
            "get_service": fake_get_service,
            "run_live_refresh": fake_run_live_refresh,
            "build_analysis_advice_report": fake_build_analysis_advice_report,
        },
    )
    try:
        result: dict[str, object] = mcp_server.get_analysis(refresh=True)
    finally:
        _restore_attrs(mcp_server, originals)

    assert result == {
        "status": "refreshed",
        "refresh": {"status": "refreshed"},
        "report": {"status": "ready", "items": []},
    }, "refresh=True must return refresh and report payloads"
    assert calls == ["get_service", "refresh", "report"], "refresh=True must refresh before building the report"


def main() -> None:
    _assert_analysis_advice_item_ai_analysis()
    _assert_analysis_advice_item_defaults_to_empty_ai_analysis()
    _assert_evaluate_ai_quality_uses_dataclass_conversion()
    _assert_review_strategies_returns_dict_shape()
    _assert_get_analysis_refresh_false_skips_refresh()
    _assert_get_analysis_refresh_true_refreshes_before_report()
    print("verify_mcp_ai_tools ok")


if __name__ == "__main__":
    main()
