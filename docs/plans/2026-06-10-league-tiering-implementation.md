# League Tiering Implementation Plan

**Goal:** Expand live coverage beyond the biggest European leagues while keeping production picks separated from paper-only strategy incubation.

## Scope

- Extend league settings with aliases, tier, analysis depth, strategy mode, bookmaker gate, event cap, and paper-only flag.
- Add configured coverage for elite club leagues, major tournaments, and smaller professional leagues.
- Make Odds-API.io ingestion use each league's configured event cap unless a command explicitly overrides it.
- Add `paper_candidate` recommendation status so small-league candidates can be reviewed without entering main picks.
- Extend odds readiness with per-league coverage status.

## Strategy Rules

- `validated_strategy`: current market, selection, and league match a configured backtested strategy profile.
- `live_scoring`: configured league is allowed to produce live picks even without a matched profile.
- `paper_candidate`: paper-only or unknown league. Passing candidates keep value/risk scores but use `stake_units: 0`.

## Lightweight Verification

Use only small local checks:

- `python -m compileall src scripts`
- `git diff --check`
- focused config loading assertions
- focused scoring assertions for one validated profile and one paper-only league
- `footballctl odds-readiness --json`
- `footballctl picks today --json`
- `python scripts/verify_scenarios.py`

No commits, no unit tests, and no large compile runs.
