from __future__ import annotations

from football_analysis.ai_analysis import (
    AISignal,
    _extract_json_object,
    _parse_signal,
    selection_label,
)
from football_analysis.scoring import AIAdjustment, MarketEdge, _ai_adjustment
from football_analysis.settings import AISettings


def _edge(market_type: str, selection: str, best_price: float, *, line: str | None = None) -> MarketEdge:
    return MarketEdge(
        market_type=market_type,
        selection=selection,
        best_price=best_price,
        market_average=best_price - 0.1,
        edge=0.03,
        source="test",
        bookmaker="test-book",
        movement=0.0,
        line=line,
    )


def _assert_json_extraction() -> None:
    clean = _extract_json_object('{"prob_selection": 0.6, "confidence": 0.7}')
    assert clean == {"prob_selection": 0.6, "confidence": 0.7}, "clean JSON must parse"

    wrapped = _extract_json_object('前缀文字 {"a": 1} 后缀文字')
    assert wrapped == {"a": 1}, "must extract JSON embedded in surrounding text"

    assert _extract_json_object("no json here") is None, "non-JSON must return None"
    assert _extract_json_object("[1, 2, 3]") is None, "non-object JSON must return None"
    assert _extract_json_object('{"broken": ') is None, "malformed JSON must return None"


def _assert_one_x_two_parsing() -> None:
    edge = _edge("1x2", "HOME", 2.10)
    content = '{"prob_home": 0.5, "prob_draw": 0.25, "prob_away": 0.25, "confidence": 0.7, "analysis": "主队占优"}'
    parsed = _parse_signal(edge, content)
    assert parsed is not None, "valid 1x2 payload must parse"
    probabilities, confidence, analysis = parsed
    assert set(probabilities) == {"HOME", "DRAW", "AWAY"}, "1x2 must return all three outcomes"
    assert abs(sum(probabilities.values()) - 1.0) < 1e-6, "1x2 probabilities must normalize to 1"
    assert confidence == 0.7 and analysis == "主队占优", "confidence and analysis must be preserved"

    bad = _parse_signal(edge, '{"prob_home": 0.5, "confidence": 0.7}')
    assert bad is None, "1x2 missing outcomes must return None"

    negative = _parse_signal(edge, '{"prob_home": -0.1, "prob_draw": 0.5, "prob_away": 0.6, "confidence": 0.7}')
    assert negative is None, "negative probability must return None"


def _assert_single_selection_parsing() -> None:
    ah_edge = _edge("asian_handicap", "AH_AWAY", 1.95, line="+0.5")
    parsed = _parse_signal(ah_edge, '{"prob_selection": 0.58, "confidence": 0.66, "analysis": "客队让球有利"}')
    assert parsed is not None, "valid AH payload must parse"
    probabilities, confidence, _ = parsed
    assert probabilities == {"AH_AWAY": 0.58}, "single-selection must map to the edge selection"
    assert confidence == 0.66, "confidence must be preserved"

    ou_edge = _edge("over_under", "OVER", 2.05, line="2.5")
    parsed_ou = _parse_signal(ou_edge, '{"prob_selection": 0.52, "confidence": 0.6}')
    assert parsed_ou is not None and parsed_ou[0] == {"OVER": 0.52}, "over_under must map to OVER"

    out_of_range = _parse_signal(ah_edge, '{"prob_selection": 1.4, "confidence": 0.6}')
    assert out_of_range is None, "probability above 1 must return None"

    missing = _parse_signal(ah_edge, '{"confidence": 0.6}')
    assert missing is None, "missing prob_selection must return None"


def _assert_adjustment_below_confidence_gate() -> None:
    settings = AISettings(min_apply_confidence=0.60)
    edge = _edge("1x2", "HOME", 2.10)
    signal = AISignal(
        market_type="1x2",
        selection="HOME",
        probabilities={"HOME": 0.55, "DRAW": 0.25, "AWAY": 0.20},
        confidence=0.40,
        analysis="信心不足",
        model="test",
    )
    adjustment = _ai_adjustment(edge, signal, settings)
    assert adjustment.value_delta == 0.0, "below-gate confidence must not shift value"
    assert adjustment.confidence_delta == 0.0, "below-gate confidence must not shift confidence"
    assert adjustment.note, "below-gate signal must still emit an advisory note"
    assert adjustment.payload is not None and adjustment.payload["applied"] is False, "payload must record not-applied"


def _assert_adjustment_positive_edge() -> None:
    settings = AISettings(min_apply_confidence=0.45, max_value_shift=14.0, max_confidence_shift=0.08)
    edge = _edge("1x2", "HOME", 2.50)
    signal = AISignal(
        market_type="1x2",
        selection="HOME",
        probabilities={"HOME": 0.50, "DRAW": 0.25, "AWAY": 0.25},
        confidence=0.80,
        analysis="主队价值明显",
        model="test",
    )
    adjustment = _ai_adjustment(edge, signal, settings)
    # ai_edge = 0.50 * 2.50 - 1 = 0.25 -> value_delta raw = 0.25 * 180 * 0.80 = 36 -> clamped to 14
    assert adjustment.value_delta == 14.0, "large positive edge must clamp to max_value_shift"
    # prob_gap = 0.50 - 0.40 = 0.10 -> raw = 0.10 * 0.80 = 0.08 -> exactly at cap
    assert abs(adjustment.confidence_delta - 0.08) < 1e-9, "confidence delta must clamp to max_confidence_shift"
    assert adjustment.payload is not None and adjustment.payload["applied"] is True, "payload must record applied"


def _assert_adjustment_negative_edge() -> None:
    settings = AISettings(min_apply_confidence=0.45, max_value_shift=14.0)
    edge = _edge("asian_handicap", "AH_AWAY", 1.80, line="+0.25")
    signal = AISignal(
        market_type="asian_handicap",
        selection="AH_AWAY",
        probabilities={"AH_AWAY": 0.40},
        confidence=0.70,
        analysis="AI 认为价值不足",
        model="test",
    )
    adjustment = _ai_adjustment(edge, signal, settings)
    # market_prob = 1/1.80 = 0.556; ai_edge = 0.40*1.80 - 1 = -0.28 -> negative value_delta
    assert adjustment.value_delta < 0.0, "AI probability below market implied must reduce value"
    assert adjustment.value_delta >= -14.0, "negative shift must respect the clamp"


def _assert_market_mismatch_ignored() -> None:
    settings = AISettings()
    edge = _edge("over_under", "OVER", 2.00, line="2.5")
    signal = AISignal(
        market_type="1x2",
        selection="HOME",
        probabilities={"HOME": 0.6, "DRAW": 0.2, "AWAY": 0.2},
        confidence=0.9,
        analysis="市场不匹配",
        model="test",
    )
    adjustment = _ai_adjustment(edge, signal, settings)
    assert adjustment == AIAdjustment(), "signal for a different market must be ignored"


def _assert_selection_labels() -> None:
    assert selection_label("HOME") == "主胜", "HOME label"
    assert selection_label("AH_AWAY") == "让球客胜", "AH_AWAY label"
    assert selection_label("OVER") == "大球", "OVER label"
    assert selection_label("UNKNOWN") == "UNKNOWN", "unknown selection falls back to raw key"


def main() -> None:
    _assert_json_extraction()
    _assert_one_x_two_parsing()
    _assert_single_selection_parsing()
    _assert_adjustment_below_confidence_gate()
    _assert_adjustment_positive_edge()
    _assert_adjustment_negative_edge()
    _assert_market_mismatch_ignored()
    _assert_selection_labels()
    print("ai analysis verification passed")


if __name__ == "__main__":
    main()
