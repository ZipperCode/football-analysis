# DATASOURCES

## OVERVIEW
11 files, ~5k LOC. External data adapters behind a shared context object. Each file wraps one provider.

## THE CONTRACT
[base.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/datasources/base.py) defines `ClientContext` (@dataclass(frozen=True)): provider, source, settings, repository, http. Properties `api_key` (reads `source.api_key_env` from `os.getenv`) and `credential_present`. `DataSourceError(RuntimeError)` for provider failures. Every adapter builds on this.

## ADAPTERS
| File | Provider | Env | Status / Notes |
|---|---|---|---|
| [api_football.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/datasources/api_football.py) | API-Football | `API_FOOTBALL_KEY` | fixtures, odds, results, standings |
| [odds_api_io.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/datasources/odds_api_io.py) | Odds-API.io | `ODDS_API_IO_KEY` | batches ≤10 via `/odds/multi`; per-league `max_events` quota guard |
| [football_data_org.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/datasources/football_data_org.py) | football-data.org | `FOOTBALL_DATA_ORG_TOKEN` | — |
| [football_data_uk.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/datasources/football_data_uk.py) | football-data.co.uk | — | CSV `/new/{code}.csv`; maps `BRA_SERIE_A→BRA`, `MLS→USA`, `J1→JPN` |
| [the_odds_api.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/datasources/the_odds_api.py) | The Odds API v4 | `THE_ODDS_API_KEY` | h2h/spreads/totals + historical snapshots; DISABLED |
| [sportmonks.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/datasources/sportmonks.py) | Sportmonks v3 | `SPORTMONKS_TOKEN` | needs per-league `sportmonks_league_id`; DISABLED |
| [dongqiudi.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/datasources/dongqiudi.py) | 懂球帝 | — | Chinese platform |
| [leisu.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/datasources/leisu.py) | 雷速 | — | Chinese platform |
| [qqsd.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/datasources/qqsd.py) | 球球是道 (QQSD) | `QQSD_C_CK` | anti-scrape / pycryptodome; primary for World Cup live context + Pinnacle odds timeline |

## GOTCHAS
- New adapter = new file using ClientContext from base.py; register in source config.
- Credentials come ONLY from env via `source.api_key_env`. Never hardcode.
- Remote calls gated by `FOOTBALL_VALIDATE_REMOTE`; quota config in `config/default.yaml`.
- [qqsd.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/datasources/qqsd.py) is the fragile one. Anti-scrape logic breaks when upstream changes; treat with care.
