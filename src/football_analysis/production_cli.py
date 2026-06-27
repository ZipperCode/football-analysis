from __future__ import annotations

from collections.abc import Callable

import typer
from rich.console import Console
from rich.table import Table

from football_analysis.execution_queue import ExecutionQueueReport, build_execution_queue
from football_analysis.production import ProductionStatusReport, build_production_status
from football_analysis.service import AnalysisService


ServiceFactory = Callable[[], AnalysisService]
JsonPrinter = Callable[[ProductionStatusReport], None]


def register_production_commands(
    app: typer.Typer,
    get_service: ServiceFactory,
    print_json: JsonPrinter,
    console: Console,
) -> None:
    @app.command("production-status")
    def production_status(
        recent_limit: int = typer.Option(10, "--recent-limit", help="Recent production jobs to include."),
        include_past: bool = typer.Option(False, "--include-past", help="Include past matches in live decision scope."),
        as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
    ) -> None:
        service = get_service()
        try:
            result = build_production_status(service, recent_limit=recent_limit, include_past=include_past)
            if as_json:
                print_json(result)
                return
            _print_production_status_summary(result, console)
        finally:
            service.repository.close()

    @app.command("production-queue")
    def production_queue(
        include_past: bool = typer.Option(False, "--include-past", help="Include past matches in queue scope."),
        limit: int = typer.Option(20, "--limit", min=1, help="Maximum queue items to include."),
        as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
    ) -> None:
        service = get_service()
        try:
            result = build_execution_queue(service, include_past=include_past, limit=limit)
            if as_json:
                print_json(result)
                return
            _print_execution_queue_summary(result, console)
        finally:
            service.repository.close()


def _print_production_status_summary(result: ProductionStatusReport, console: Console) -> None:
    table = Table(title="Production Status")
    table.add_column("Status")
    table.add_column("Ready")
    table.add_column("Action")
    table.add_column("Issues")
    table.add_row(
        result.overall_status,
        "yes" if result.ready_to_bet else "no",
        result.action,
        _clip_text("; ".join(result.issues[:3]) if result.issues else "-", 120),
    )
    console.print(table)


def _print_execution_queue_summary(result: ExecutionQueueReport, console: Console) -> None:
    table = Table(title="Production Queue")
    table.add_column("#")
    table.add_column("State")
    table.add_column("Match")
    table.add_column("Market")
    table.add_column("Min odds")
    table.add_column("Stake")
    table.add_column("Issues")
    for item in result.items:
        table.add_row(
            str(item.rank),
            item.state,
            f"{item.home_team} vs {item.away_team}",
            f"{item.market_type or '-'} {item.selection or '-'}",
            "-" if item.minimum_odds is None else f"{item.minimum_odds:.3f}",
            f"{item.stake_units:.2f}u",
            _clip_text("; ".join(item.gates_failed) if item.gates_failed else "-", 80),
        )
    console.print(table)


def _clip_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "..."
