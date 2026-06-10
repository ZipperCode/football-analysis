from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from football_analysis.db import StructuredRepository
from football_analysis.service import AnalysisService
from football_analysis.settings import load_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", action="store_true", help="Enable remote source probes.")
    parser.add_argument("--no-remote", action="store_true", help="Keep remote probes disabled.")
    args = parser.parse_args()

    if args.remote and args.no_remote:
        raise SystemExit("Use only one of --remote or --no-remote.")

    previous = os.getenv("FOOTBALL_VALIDATE_REMOTE")
    os.environ["FOOTBALL_VALIDATE_REMOTE"] = "1" if args.remote else "0"
    try:
        with TemporaryDirectory() as tmp:
            settings = load_settings()
            settings.storage.database_url = f"sqlite:///{Path(tmp) / 'sources.db'}"
            repository = StructuredRepository(settings.storage.database_url)
            repository.initialize()
            try:
                service = AnalysisService(settings, repository)
                health = asyncio.run(service.sources_health())
                assert health, "expected configured data sources"
                for item in health:
                    assert item.source_id, "source id missing"
                    assert item.name, "source name missing"
                    assert "<redacted>" not in item.detail, "health detail should not contain redacted markers"
                    if item.credential_present:
                        assert item.state.value != "missing_credentials"
            finally:
                repository.close()
    finally:
        if previous is None:
            os.environ.pop("FOOTBALL_VALIDATE_REMOTE", None)
        else:
            os.environ["FOOTBALL_VALIDATE_REMOTE"] = previous

    print("datasource verification passed")


if __name__ == "__main__":
    main()
