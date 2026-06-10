# Small League Live Recommendation Design

**Goal:** Allow smaller professional leagues to produce real-stake recommendations without sharing the exact same risk posture as elite leagues.

## Design

Small leagues move from paper-only observation to a low-stake live lane. The live lane uses stricter gates than the default scoring thresholds: higher value score, lower risk score, minimum confidence, lower stake cap, and minimum bookmaker coverage. This keeps the recommendation path usable for real betting while acknowledging that small-league data, liquidity, and news coverage are weaker.

## Policy

- Elite validated strategies keep the existing `validated_strategy` behavior.
- Configured live leagues without a matched profile remain `live_scoring`.
- Secondary professional leagues can become `secondary_live_small_stake` when they pass the stricter tier policy.
- If a secondary league fails the tier policy but otherwise passes the base score, it is downgraded to `paper_candidate`.

## Next Step After This

After live small-league recommendations exist, the next work should be result tracking by league and tier: ingest settled results, record closing odds, calculate ROI/CLV per league, and promote or demote each small league based on actual evidence.
