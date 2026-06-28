"""Bankroll curve reporting for validated long-horizon strategy candidates."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from football_analysis.strategy import LongHorizonCandidate, long_horizon_scan


class KellyBankrollReport(BaseModel):
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    league: str
    family: str
    candidate_name: str | None
    initial_bankroll_units: float
    final_bankroll_units: float
    cagr: float | None
    max_drawdown_pct: float | None
    total_roi: float | None
    holdout_roi: float | None
    settled_bets: int
    holdout_settled_bets: int
    positive_holdout_seasons: int
    holdout_season_count: int
    target_cagr: float
    max_allowed_drawdown_pct: float
    target_passed: bool
    issues: list[str] = Field(default_factory=list)
    season_curve: list[dict] = Field(default_factory=list)
    candidate: dict | None = None


def build_kelly_bankroll_report(
    repository,
    *,
    league: str = "I1",
    family: str = "asian-away",
    quick: bool = True,
    initial_bankroll_units: float = 10000.0,
    target_cagr: float = 0.20,
    max_allowed_drawdown_pct: float = 0.20,
) -> KellyBankrollReport:
    """Build a bankroll curve from the top long-horizon candidate."""
    scan = long_horizon_scan(
        repository,
        league=league,
        family=family,
        quick=quick,
        limit=1,
        min_discovery_roi=0.05 if quick else 0.08,
        min_holdout_roi=0.05 if quick else 0.08,
    )
    candidate = scan.candidates[0] if scan.candidates else None
    if candidate is None:
        return KellyBankrollReport(
            league=league.upper(),
            family=family,
            candidate_name=None,
            initial_bankroll_units=initial_bankroll_units,
            final_bankroll_units=initial_bankroll_units,
            cagr=None,
            max_drawdown_pct=None,
            total_roi=None,
            holdout_roi=None,
            settled_bets=0,
            holdout_settled_bets=0,
            positive_holdout_seasons=0,
            holdout_season_count=0,
            target_cagr=target_cagr,
            max_allowed_drawdown_pct=max_allowed_drawdown_pct,
            target_passed=False,
            issues=["no_long_horizon_candidate"],
        )
    return _candidate_bankroll_report(
        candidate,
        initial_bankroll_units=initial_bankroll_units,
        target_cagr=target_cagr,
        max_allowed_drawdown_pct=max_allowed_drawdown_pct,
    )


def _candidate_bankroll_report(
    candidate: LongHorizonCandidate,
    *,
    initial_bankroll_units: float,
    target_cagr: float,
    max_allowed_drawdown_pct: float,
) -> KellyBankrollReport:
    bankroll = initial_bankroll_units
    peak = bankroll
    max_drawdown_pct = 0.0
    season_curve: list[dict] = []
    breakdown = sorted(candidate.total.season_breakdown, key=lambda row: str(row.get("season", "")))
    for row in breakdown:
        profit = float(row.get("profit_units") or 0.0)
        seasons = row.get("seasons") or []
        season = seasons[0] if isinstance(seasons, list) and seasons else row.get("season")
        bankroll += profit
        peak = max(peak, bankroll)
        drawdown = (peak - bankroll) / peak if peak else 0.0
        max_drawdown_pct = max(max_drawdown_pct, drawdown)
        season_curve.append(
            {
                "season": season,
                "bets": row.get("bets"),
                "profit_units": round(profit, 3),
                "roi": row.get("roi"),
                "bankroll_units": round(bankroll, 3),
                "drawdown_pct": round(drawdown, 4),
            }
        )

    years = max(1, len([row for row in season_curve if (row.get("bets") or 0) > 0]))
    cagr = (bankroll / initial_bankroll_units) ** (1 / years) - 1 if initial_bankroll_units > 0 else None
    issues: list[str] = []
    if cagr is None or cagr < target_cagr:
        observed = "none" if cagr is None else f"{cagr:.4f}"
        issues.append(f"cagr_below_target:{observed}/{target_cagr:.4f}")
    if max_drawdown_pct > max_allowed_drawdown_pct:
        issues.append(f"drawdown_above_limit:{max_drawdown_pct:.4f}/{max_allowed_drawdown_pct:.4f}")
    return KellyBankrollReport(
        league=candidate.league,
        family=candidate.family,
        candidate_name=candidate.name,
        initial_bankroll_units=initial_bankroll_units,
        final_bankroll_units=round(bankroll, 3),
        cagr=round(cagr, 4) if cagr is not None else None,
        max_drawdown_pct=round(max_drawdown_pct, 4),
        total_roi=candidate.total.roi,
        holdout_roi=candidate.holdout.roi,
        settled_bets=candidate.total.settled_bets,
        holdout_settled_bets=candidate.holdout.settled_bets,
        positive_holdout_seasons=candidate.holdout.positive_seasons,
        holdout_season_count=candidate.holdout.season_count,
        target_cagr=target_cagr,
        max_allowed_drawdown_pct=max_allowed_drawdown_pct,
        target_passed=not issues,
        issues=issues,
        season_curve=season_curve,
        candidate=candidate.model_dump(mode="json"),
    )
