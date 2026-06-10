from __future__ import annotations

import os
from dataclasses import dataclass

from football_analysis.db import StructuredRepository
from football_analysis.http_client import ProviderHttpClient
from football_analysis.settings import Settings, SourceSettings


@dataclass(frozen=True)
class ClientContext:
    provider: str
    source: SourceSettings
    settings: Settings
    repository: StructuredRepository
    http: ProviderHttpClient

    @property
    def api_key(self) -> str | None:
        if not self.source.api_key_env:
            return None
        return os.getenv(self.source.api_key_env)

    @property
    def credential_present(self) -> bool:
        return bool(self.api_key) if self.source.api_key_env else True


class DataSourceError(RuntimeError):
    pass
