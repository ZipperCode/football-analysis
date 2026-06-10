from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> None:
    with TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'contracts.db'}"
        commands = [
            ["footballctl", "db", "init", "--json"],
            ["footballctl", "picks", "today", "--json"],
            ["footballctl", "sources", "--json"],
            ["footballctl", "performance", "--json"],
        ]
        for command in commands:
            output = subprocess.check_output(command, text=True, encoding="utf-8", env=env)
            json.loads(output)

        env["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'api.db'}"
        os.environ["DATABASE_URL"] = env["DATABASE_URL"]
        from fastapi.testclient import TestClient
        from football_analysis.api import app

        client = TestClient(app)
        assert client.get("/").json()["status"] == "ok"
        assert client.get("/picks/today").status_code == 200
        assert client.get("/sources/health").status_code == 200
        assert client.get("/backtest/historical").status_code == 200

    print("contract verification passed")


if __name__ == "__main__":
    main()
