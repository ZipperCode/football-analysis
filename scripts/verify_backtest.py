from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from football_analysis.backtest import run_historical_backtest
from football_analysis.db import StructuredRepository
from football_analysis.ingestion import IngestionService
from football_analysis.settings import load_settings


CSV_FIXTURE = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A,PSCH,PSCD,PSCA
E0,09/08/2025,Alpha FC,Beta FC,2,1,2.20,3.30,3.40,2.00,3.25,3.50
E0,10/08/2025,Gamma FC,Delta FC,0,1,1.80,3.60,4.50,1.82,3.55,4.40
"""


def main() -> None:
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'backtest.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            csv_path = Path(tmp) / "E0.csv"
            csv_path.write_text(CSV_FIXTURE, encoding="utf-8")
            ingestion = IngestionService(settings, repository)
            result = ingestion.ingest_historical("E0", "2526", path=str(csv_path))
            assert result.inserted == 2, "expected two imported historical rows"
            summary = run_historical_backtest(repository, "E0", "2526")
            assert summary.matches == 2
            assert summary.bets >= 1
            assert summary.settled_bets >= 1
            assert summary.hit_rate is not None
            assert summary.positive_clv_rate is not None
            assert summary.max_drawdown_units is not None
            assert summary.brier_score is not None
            assert summary.calibration_buckets, "expected calibration buckets for settled backtest bets"
            assert summary.segment_breakdown, "expected odds segment breakdown"
        finally:
            repository.close()

    print("backtest verification passed")


if __name__ == "__main__":
    main()
