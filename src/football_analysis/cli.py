from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from football_analysis.models import BetLog
from football_analysis.production_cli import register_production_commands
from football_analysis.service import get_service

app = typer.Typer(help="Football value analysis command line bridge.")
picks_app = typer.Typer(help="Pick commands.")
db_app = typer.Typer(help="Database commands.")
ingest_app = typer.Typer(help="Ingestion commands.")
backtest_app = typer.Typer(help="Backtest commands.")
app.add_typer(picks_app, name="picks")
app.add_typer(db_app, name="db")
app.add_typer(ingest_app, name="ingest")
app.add_typer(backtest_app, name="backtest")
console = Console()


def _print_json(payload: Any) -> None:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


register_production_commands(app, get_service, _print_json, console)


@picks_app.command("today")
def picks_today(as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools.")) -> None:
    result = get_service().picks_today()
    if as_json:
        _print_json(result)
        return

    table = Table(title=result.message)
    table.add_column("Match")
    table.add_column("Market")
    table.add_column("Selection")
    table.add_column("Value")
    table.add_column("Risk")
    table.add_column("Stake")
    analyses = {analysis.match.id: analysis for analysis in result.analyses}
    for pick in result.picks:
        match = analyses[pick.match_id].match
        table.add_row(
            f"{match.home_team} vs {match.away_team}",
            pick.market_type.value if pick.market_type else "-",
            pick.selection or "-",
            f"{pick.value_score:.1f}",
            f"{pick.risk_score:.1f}",
            f"{pick.stake_units:.1f}u",
        )
    console.print(table)


@app.command("analyze")
def analyze(match_id: str, as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools.")) -> None:
    analysis = get_service().analyze_match(match_id)
    if as_json:
        _print_json(analysis)
        return
    console.print(f"{analysis.match.home_team} vs {analysis.match.away_team}")
    console.print(analysis.recommendation.reason)
    console.print(analysis.recommendation.risk_notice)


@app.command("performance")
def performance(
    by_league: bool = typer.Option(False, "--by-league", help="Group performance by configured league and tier."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools."),
) -> None:
    service = get_service()
    summary = service.performance_by_league() if by_league else service.performance()
    if as_json:
        _print_json(summary)
        return
    console.print(summary.model_dump())


@app.command("live-audit")
def live_audit(
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in the audit."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.live_audit import audit_live_trading

    result = audit_live_trading(service.repository, service.settings, include_past=include_past)
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@app.command("live-preflight")
def live_preflight(
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in the audit scope."),
    min_bookmakers: int | None = typer.Option(None, "--min-bookmakers", help="Override live bookmaker minimum."),
    min_profile_matches: int = typer.Option(1, "--min-profile-matches", help="Minimum ready matches per profile."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.live_preflight import run_live_preflight

    result = run_live_preflight(
        service.repository,
        service.settings,
        include_past=include_past,
        min_bookmakers=min_bookmakers,
        min_profile_matches=min_profile_matches,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@app.command("live-review")
def live_review(
    include_paper: bool = typer.Option(True, "--include-paper/--real-only", help="Include paper observations in review."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.live_review import run_live_review

    result = run_live_review(service.repository, service.settings, include_paper=include_paper)
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@app.command("live-decision")
def live_decision(
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in preflight scope."),
    include_paper: bool = typer.Option(True, "--include-paper/--real-only", help="Include paper observations in review."),
    full_profile_audit: bool = typer.Option(False, "--full-profile-audit", help="Run the full backtest profile audit."),
    seasons: str = typer.Option("2122,2223,2324,2425,2526", "--seasons", help="Profile-audit seasons."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.live_decision import run_live_decision

    result = run_live_decision(
        service.repository,
        service.settings,
        include_past=include_past,
        include_paper=include_paper,
        seasons=_split_csv(seasons),
        full_profile_audit=full_profile_audit,
    )
    if as_json:
        _print_json(result)
        return
    _print_live_decision_summary(result)


@app.command("live-refresh")
def live_refresh(
    date: str = typer.Option(..., "--date", help="Refresh date in YYYY-MM-DD."),
    fixture_source: str = typer.Option("auto", "--fixture-source", help="Fixture ingestion source."),
    odds_source: str = typer.Option("auto", "--odds-source", help="Odds ingestion source."),
    league: str | None = typer.Option(None, "--league", help="Configured league code; defaults to active profiles."),
    scope: str = typer.Option(
        "active-profiles",
        "--scope",
        help="Refresh scope: active-profiles or live-leagues. Ignored when --league is set.",
    ),
    max_events: int | None = typer.Option(None, "--max-events", help="Maximum odds events per league."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in post-refresh preflight."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan the refresh without spending remote quota."),
    allow_odds_fallback: bool = typer.Option(
        False,
        "--allow-odds-fallback",
        help="Allow auto odds refresh to spend fallback source quota after the preferred source is empty or fails.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.live_refresh import run_live_refresh

    result = run_live_refresh(
        service,
        date=date,
        fixture_source=fixture_source,
        odds_source=odds_source,
        league=league,
        scope=scope,
        max_events=max_events,
        include_past=include_past,
        dry_run=dry_run,
        allow_odds_fallback=allow_odds_fallback,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


def _print_live_decision_summary(result: Any) -> None:
    console.print(
        f"Live decision: {result.status} "
        f"ready={str(result.ready_to_bet).lower()} action={result.action}"
    )
    console.print(
        "Components: "
        + " ".join(f"{name}={status}" for name, status in result.components.items())
    )
    live_audit = result.preflight.live_audit
    console.print(
        "Candidates: "
        f"matches={live_audit.total_matches} "
        f"recommended={live_audit.recommended_count} "
        f"paper={live_audit.paper_candidate_count} "
        f"analysis_only={live_audit.analysis_only_count}"
    )
    console.print(
        "Profiles/Odds: "
        f"ready_profiles={result.odds_readiness.ready_profiles}/{result.odds_readiness.active_profiles} "
        f"scoped_odds={result.odds_readiness.scoped_odds_snapshots}"
    )

    refresh_requirements = list(getattr(result.odds_readiness, "refresh_requirements", []) or [])
    if refresh_requirements:
        console.print("Odds refresh requirements:")
        for item in refresh_requirements[:2]:
            console.print(_refresh_requirement_line(item))
        if len(refresh_requirements) > 2:
            console.print(f"- ... {len(refresh_requirements) - 2} more")

    if result.issues:
        console.print("Issues:")
        for issue in result.issues[:4]:
            console.print(f"- {_clip(issue)}")
        if len(result.issues) > 4:
            console.print(f"- ... {len(result.issues) - 4} more")

    closest = _closest_blocked_candidates(live_audit.items)
    if closest:
        console.print("Closest blocked candidates:")
        for item in closest[:2]:
            console.print(_candidate_summary_line(item))

    blockers = sorted(live_audit.gate_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    if blockers:
        console.print("Top live gate blockers:")
        for gate, count in blockers:
            console.print(f"- {count}x {_clip(gate)}")


def _closest_blocked_candidates(items: list[Any], limit: int = 3) -> list[Any]:
    candidates = [
        item
        for item in items
        if getattr(item, "live_gate_passed", False) is False
        and getattr(item, "status", "") in {"paper_candidate", "recommended"}
    ]
    return sorted(
        candidates,
        key=lambda item: (
            getattr(item, "status", "") != "paper_candidate",
            -float(getattr(item, "value_score", 0.0) or 0.0),
            float(getattr(item, "risk_score", 100.0) or 100.0),
            -float(getattr(item, "confidence", 0.0) or 0.0),
        ),
    )[:limit]


def _refresh_requirement_line(item: Any) -> str:
    refresh_league = getattr(item, "refresh_league_code", None) or "-"
    strategy_league = getattr(item, "strategy_league_code", None) or "-"
    market = getattr(item, "market_type", None) or "-"
    selections = ",".join(getattr(item, "selections", []) or ["-"])
    issue = _first_issue(getattr(item, "issues", []) or [])
    return (
        f"- {refresh_league}({strategy_league}) {market} {selections} "
        f"bookmakers>={getattr(item, 'required_bookmakers', '-')} "
        f"ready={getattr(item, 'ready_matches', 0)} need={getattr(item, 'needed_ready_matches', 0)} "
        f"issue={_clip(issue, 24)}"
    )


def _candidate_summary_line(item: Any) -> str:
    market = getattr(item, "market_type", None) or "-"
    selection = getattr(item, "selection", None) or "-"
    home = getattr(item, "home_team", "-")
    away = getattr(item, "away_team", "-")
    gate = _first_issue(getattr(item, "gates_failed", []) or [])
    return (
        f"- {home} vs {away} {market} {selection} "
        f"v={float(getattr(item, 'value_score', 0.0) or 0.0):.1f} "
        f"r={float(getattr(item, 'risk_score', 0.0) or 0.0):.1f} "
        f"c={float(getattr(item, 'confidence', 0.0) or 0.0):.3f} "
        f"gate={_clip(gate, 26)}"
    )


def _first_issue(issues: list[str]) -> str:
    return str(issues[0]) if issues else "-"


def _clip(value: str, max_chars: int = 68) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


@app.command("daily-ops")
def daily_ops(
    date: str = typer.Option(..., "--date", help="Operational date in YYYY-MM-DD."),
    ingest_results: bool = typer.Option(False, "--ingest-results", help="Refresh finished results before settlement."),
    source: str = typer.Option("api_football", "--source", help="Result ingestion source."),
    league: str | None = typer.Option(None, "--league", help="Configured league code for result ingestion."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in preflight scope."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.daily_ops import run_daily_ops

    result = run_daily_ops(
        service,
        date=date,
        ingest_results=ingest_results,
        source=source,
        league=league,
        include_past=include_past,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@app.command("sources")
def sources(as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools.")) -> None:
    import asyncio

    result = asyncio.run(get_service().sources_health())
    if as_json:
        _print_json([item.model_dump(mode="json") for item in result])
        return
    for item in result:
        console.print(f"{item.source_id}: {item.state.value} - {item.detail}")


@app.command("odds-readiness")
def odds_readiness(
    min_bookmakers: int = typer.Option(2, "--min-bookmakers", help="Minimum bookmakers per match/market group."),
    min_profile_matches: int = typer.Option(1, "--min-profile-matches", help="Minimum ready matches per profile."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in the audit scope."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.odds_readiness import audit_odds_readiness

    result = audit_odds_readiness(
        service.repository,
        service.settings,
        min_bookmakers=min_bookmakers,
        min_profile_matches=min_profile_matches,
        include_past=include_past,
    )
    if as_json:
        _print_json(result)
        return

    table = Table(title=f"Odds readiness: {result.status}")
    table.add_column("Profile")
    table.add_column("Status")
    table.add_column("Ready")
    table.add_column("Matching")
    table.add_column("Issues")
    for profile in result.profiles:
        table.add_row(
            profile.profile_id,
            profile.status,
            str(profile.ready_matches),
            str(profile.matching_matches),
            ", ".join(profile.issues) or "-",
        )
    console.print(table)


@db_app.command("init")
def db_init(as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools.")) -> None:
    service = get_service()
    service.repository.initialize()
    payload = {"status": "ok", "database_url": service.settings.storage.database_url}
    if as_json:
        _print_json(payload)
        return
    console.print(payload)


@ingest_app.command("fixtures")
def ingest_fixtures(
    date: str = typer.Option(..., "--date", help="Date in YYYY-MM-DD."),
    source: str = typer.Option("api_football", "--source"),
    league: str | None = typer.Option(None, "--league", help="Configured league code, e.g. EPL."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = get_service().ingestion.ingest_fixtures(date=date, source=source, league_code=league)
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@ingest_app.command("results")
def ingest_results(
    date: str = typer.Option(..., "--date", help="Date in YYYY-MM-DD."),
    source: str = typer.Option("api_football", "--source"),
    league: str | None = typer.Option(None, "--league", help="Configured league code, e.g. EPL."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = get_service().ingestion.ingest_results(date=date, source=source, league_code=league)
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@ingest_app.command("odds")
def ingest_odds(
    date: str | None = typer.Option(None, "--date", help="Date in YYYY-MM-DD."),
    source: str = typer.Option("api_football", "--source"),
    league: str | None = typer.Option(None, "--league", help="Configured league code, e.g. EPL."),
    max_events: int | None = typer.Option(
        None,
        "--max-events",
        help="Maximum Odds-API.io events to price. Defaults to the configured league limit.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = get_service().ingestion.ingest_odds(
        date=date,
        source=source,
        league_code=league,
        max_events=max_events,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@ingest_app.command("standings")
def ingest_standings(
    league: str = typer.Option(..., "--league", help="Configured league code, e.g. EPL."),
    season: int | None = typer.Option(None, "--season"),
    source: str = typer.Option("api_football", "--source"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = get_service().ingestion.ingest_standings(league_code=league, season=season, source=source)
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@ingest_app.command("intelligence")
def ingest_intelligence(
    source: str = typer.Option("dongqiudi", "--source"),
    match_id: str | None = typer.Option(None, "--match-id", help="Internal match id. Defaults to all matches with source ids."),
    include_team_feeds: bool = typer.Option(True, "--team-feeds/--no-team-feeds", help="Fetch team news feeds when team ids are known."),
    article_detail_limit: int = typer.Option(3, "--article-detail-limit", min=0, help="Maximum article details to fetch per team feed."),
    max_matches: int | None = typer.Option(None, "--max-matches", help="Limit scanned matches."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = get_service().ingestion.ingest_intelligence(
        source=source,
        match_id=match_id,
        include_team_feeds=include_team_feeds,
        article_detail_limit=article_detail_limit,
        max_matches=max_matches,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@ingest_app.command("historical")
def ingest_historical(
    league: str = typer.Option("E0", "--league"),
    season: str = typer.Option("2526", "--season"),
    path: str | None = typer.Option(None, "--path", help="Local CSV path."),
    download: bool = typer.Option(False, "--download", help="Download from football-data.co.uk."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = get_service().ingestion.ingest_historical(league=league, season=season, path=path, download=download)
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@backtest_app.command("historical")
def backtest_historical(
    league: str = typer.Option("E0", "--league"),
    season: str = typer.Option("2526", "--season"),
    min_clv_edge: float = typer.Option(0.025, "--min-clv-edge"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.backtest import run_historical_backtest

    result = run_historical_backtest(service.repository, league=league, season=season, min_clv_edge=min_clv_edge)
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@backtest_app.command("optimize")
def backtest_optimize(
    league: str = typer.Option("E0", "--league"),
    train_seasons: str = typer.Option("2122,2223,2324,2425", "--train-seasons"),
    test_seasons: str = typer.Option("2526", "--test-seasons"),
    min_test_bets: int = typer.Option(80, "--min-test-bets"),
    season_phases: str = typer.Option("all", "--season-phases", help="Comma-separated phases: all, early, middle, late."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.strategy import optimize_strategy

    result = optimize_strategy(
        service.repository,
        league=league,
        train_seasons=_split_csv(train_seasons),
        test_seasons=_split_csv(test_seasons),
        min_test_bets=min_test_bets,
        season_phases=_split_csv(season_phases),
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@backtest_app.command("walk-forward")
def backtest_walk_forward(
    league: str = typer.Option("E0", "--league"),
    seasons: str = typer.Option("2122,2223,2324,2425,2526", "--seasons"),
    min_train_seasons: int = typer.Option(2, "--min-train-seasons"),
    min_test_bets: int = typer.Option(30, "--min-test-bets"),
    season_phases: str = typer.Option("all", "--season-phases", help="Comma-separated phases: all, early, middle, late."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.strategy import walk_forward_optimize

    result = walk_forward_optimize(
        service.repository,
        league=league,
        seasons=_split_csv(seasons),
        min_train_seasons=min_train_seasons,
        min_test_bets=min_test_bets,
        season_phases=_split_csv(season_phases),
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@backtest_app.command("portfolio")
def backtest_portfolio(
    seasons: str = typer.Option("2122,2223,2324,2425,2526", "--seasons"),
    leagues: str = typer.Option("E0,SP1,D1,I1,F1", "--leagues", help="Comma-separated league codes for phase scans."),
    season_phases: str = typer.Option("all,early,middle,late", "--season-phases", help="Comma-separated phases for phase scans."),
    scan_phases: bool = typer.Option(False, "--scan-phases", help="Run league/phase optimization scans."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.strategy import build_strategy_portfolio

    result = build_strategy_portfolio(
        service.repository,
        seasons=_split_csv(seasons),
        leagues=_split_csv(leagues),
        season_phases=_split_csv(season_phases),
        scan_phases=scan_phases,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@backtest_app.command("long-horizon-scan")
def backtest_long_horizon_scan(
    league: str = typer.Option("I1", "--league"),
    family: str = typer.Option(
        "asian-away",
        "--family",
        help="Strategy family: asian-away, asian-home, market-home, or market-away.",
    ),
    seasons: str = typer.Option("", "--seasons", help="Comma-separated seasons. Empty means all loaded seasons."),
    discovery_start: str = typer.Option("1011", "--discovery-start"),
    discovery_end: str = typer.Option("1819", "--discovery-end"),
    holdout_start: str = typer.Option("1920", "--holdout-start"),
    quick: bool = typer.Option(False, "--quick", help="Run the fixed regression candidate only."),
    limit: int = typer.Option(10, "--limit"),
    min_total_bets: int = typer.Option(180, "--min-total-bets"),
    min_discovery_bets: int = typer.Option(80, "--min-discovery-bets"),
    min_holdout_bets: int = typer.Option(80, "--min-holdout-bets"),
    min_discovery_roi: float = typer.Option(0.08, "--min-discovery-roi"),
    min_holdout_roi: float = typer.Option(0.08, "--min-holdout-roi"),
    min_holdout_positive_seasons: int = typer.Option(4, "--min-holdout-positive-seasons"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.strategy import long_horizon_scan

    result = long_horizon_scan(
        service.repository,
        league=league,
        family=family,
        seasons=_split_csv(seasons) or None,
        discovery_start=discovery_start,
        discovery_end=discovery_end,
        holdout_start=holdout_start,
        quick=quick,
        limit=limit,
        min_total_bets=min_total_bets,
        min_discovery_bets=min_discovery_bets,
        min_holdout_bets=min_holdout_bets,
        min_discovery_roi=min_discovery_roi,
        min_holdout_roi=min_holdout_roi,
        min_holdout_positive_seasons=min_holdout_positive_seasons,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@backtest_app.command("profile-audit")
def backtest_profile_audit(
    seasons: str = typer.Option("2122,2223,2324,2425,2526", "--seasons"),
    roi_tolerance: float = typer.Option(0.002, "--roi-tolerance"),
    clv_tolerance: float = typer.Option(0.002, "--clv-tolerance"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.strategy import audit_strategy_profiles

    result = audit_strategy_profiles(
        service.repository,
        configured_profiles=service.settings.strategy_profiles,
        seasons=_split_csv(seasons),
        roi_tolerance=roi_tolerance,
        clv_tolerance=clv_tolerance,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@app.command("record-bet")
def record_bet(
    match_id: str,
    market: str,
    selection: str,
    odds: float,
    stake_units: float,
    platform: str,
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools."),
) -> None:
    bet = BetLog(
        id="",
        match_id=match_id,
        market_type=market,
        selection=selection,
        odds=odds,
        stake_units=stake_units,
        platform=platform,
    )
    try:
        recorded = get_service().record_bet(bet)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if as_json:
        _print_json(recorded)
        return
    console.print(f"Recorded bet {recorded.id}")


@app.command("settle-bet")
def settle_bet(
    bet_id: str,
    result: str | None = typer.Option(
        None,
        "--result",
        help="Explicit settlement result: win, loss, void, half_win, or half_loss.",
    ),
    closing_odds: float | None = typer.Option(None, "--closing-odds", help="Closing odds for CLV tracking."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools."),
) -> None:
    try:
        settled = get_service().settle_bet(bet_id, result=result, closing_odds=closing_odds)
    except (KeyError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if as_json:
        _print_json(settled)
        return
    console.print(f"Settled bet {settled.id}: {settled.result} {settled.profit_units:+.2f}u")


@app.command("settle-open-bets")
def settle_open_bets(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools."),
) -> None:
    report = get_service().settle_open_bets()
    if as_json:
        _print_json(report)
        return
    console.print(
        f"Settled {report.settled_count}/{report.scanned_count} open bets; "
        f"skipped={report.skipped_count}, errors={report.error_count}"
    )


if __name__ == "__main__":
    app()
