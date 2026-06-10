from __future__ import annotations

import json
import subprocess
import sys

from football_analysis.service import get_service
from football_analysis.strategy import audit_strategy_profiles, long_horizon_scan


def main() -> None:
    service = get_service()
    try:
        report = long_horizon_scan(
            service.repository,
            league="I1",
            family="asian-away",
            quick=True,
        )
        assert report.candidates, "expected at least one long-horizon candidate"
        top = report.candidates[0]
        assert top.params["mode"] == "asian_value", "expected Asian handicap value mode"
        assert top.params["selection_bias"] == "ah_away", "expected away AH strategy family"
        assert top.params["season_phase"] == "middle", "expected middle-season filter"
        assert top.params["min_edge"] == 0.025, "expected high edge threshold"
        assert top.params["min_odds"] == 1.8, "expected minimum odds threshold"
        assert top.params["max_odds"] == 2.7, "expected maximum odds threshold"
        assert top.params["min_strength"] == 0.5, "expected away strength gate"
        assert top.params["max_bets_per_season"] == 25, "expected per-season volume cap"
        assert top.total.settled_bets >= 220, "expected long-horizon total sample"
        assert top.total.roi is not None and top.total.roi >= 0.18, "expected total ROI above 18%"
        assert top.holdout.settled_bets >= 120, "expected meaningful holdout sample"
        assert top.holdout.roi is not None and top.holdout.roi >= 0.18, "expected holdout ROI above 18%"
        assert top.holdout.positive_seasons >= 5, "expected at least five positive holdout seasons"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "football_analysis",
                "backtest",
                "long-horizon-scan",
                "--league",
                "I1",
                "--family",
                "asian-away",
                "--quick",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        assert payload["league"] == "I1", "expected CLI JSON league"
        assert payload["family"] == "asian-away", "expected CLI JSON family"
        assert payload["candidates"], "expected CLI JSON candidates"
        assert payload["candidates"][0]["holdout"]["roi"] >= 0.18, "expected CLI holdout ROI above 18%"

        audit = audit_strategy_profiles(
            service.repository,
            service.settings.strategy_profiles,
            seasons=["2122", "2223", "2324", "2425", "2526"],
        )
        audit_by_id = {item.profile_id: item for item in audit.items}
        long_horizon_item = audit_by_id.get("i1_middle_ah_away_live_long_horizon")
        assert long_horizon_item is not None, "expected live long-horizon profile audit item"
        assert long_horizon_item.status == "matched", (
            "expected configured live long-horizon profile to match the current scan, "
            f"got {long_horizon_item.status}: {long_horizon_item.message}"
        )
        assert long_horizon_item.portfolio is not None, "expected long-horizon audit portfolio payload"
        assert long_horizon_item.portfolio["source"] == "long_horizon", "expected long-horizon audit source"
        assert long_horizon_item.portfolio["holdout_settled_bets"] >= 80, "expected holdout sample in audit payload"

        paper_report = long_horizon_scan(
            service.repository,
            league="I1",
            family="asian-home",
            quick=True,
            min_discovery_roi=0.05,
            min_holdout_roi=0.05,
            min_holdout_positive_seasons=5,
        )
        assert paper_report.candidates, "expected AH home research candidate under paper thresholds"
        paper_top = paper_report.candidates[0]
        assert paper_top.params["mode"] == "asian_value", "expected Asian handicap value mode"
        assert paper_top.params["selection_bias"] == "ah_home", "expected home AH strategy family"
        assert paper_top.total.settled_bets >= 300, "expected meaningful AH home long-horizon sample"
        assert paper_top.total.roi is not None and 0.05 <= paper_top.total.roi < 0.08, (
            "expected AH home to remain below live ROI gate"
        )
        assert paper_top.holdout.roi is not None and 0.05 <= paper_top.holdout.roi < 0.08, (
            "expected AH home holdout ROI below live ROI gate"
        )

        for unsupported_live_family in ["market-home", "market-away"]:
            research_report = long_horizon_scan(
                service.repository,
                league="I1",
                family=unsupported_live_family,
                quick=True,
            )
            assert research_report.family == unsupported_live_family, "expected normalized family echo"
            assert research_report.candidates == [], f"expected no live candidate for {unsupported_live_family}"

        print("long-horizon scan verification passed")
        print(
            f"{top.name}: total ROI={top.total.roi} bets={top.total.settled_bets} "
            f"holdout ROI={top.holdout.roi} holdout bets={top.holdout.settled_bets}"
        )
    finally:
        service.repository.close()


if __name__ == "__main__":
    main()
