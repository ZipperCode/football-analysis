from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from football_analysis.models import BetLog
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
def performance(as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools.")) -> None:
    summary = get_service().performance()
    if as_json:
        _print_json(summary)
        return
    console.print(summary.model_dump())


@app.command("sources")
def sources(as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools.")) -> None:
    import asyncio

    result = asyncio.run(get_service().sources_health())
    if as_json:
        _print_json([item.model_dump(mode="json") for item in result])
        return
    for item in result:
        console.print(f"{item.source_id}: {item.state.value} - {item.detail}")


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


@ingest_app.command("odds")
def ingest_odds(
    date: str | None = typer.Option(None, "--date", help="Date in YYYY-MM-DD."),
    source: str = typer.Option("api_football", "--source"),
    league: str | None = typer.Option(None, "--league", help="Configured league code, e.g. EPL."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = get_service().ingestion.ingest_odds(date=date, source=source, league_code=league)
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
    recorded = get_service().record_bet(bet)
    if as_json:
        _print_json(recorded)
        return
    console.print(f"Recorded bet {recorded.id}")


if __name__ == "__main__":
    app()
