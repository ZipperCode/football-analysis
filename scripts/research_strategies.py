from __future__ import annotations

import csv
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from statistics import mean


SEASONS = ["2122", "2223", "2324", "2425", "2526"]
LEAGUES = ["E0", "SP1", "D1", "I1", "F1"]


@dataclass(frozen=True)
class Row:
    league: str
    season: str
    index: int
    home: str
    away: str
    hg: int
    ag: int
    h: float | None
    d: float | None
    a: float | None
    maxh: float | None
    maxd: float | None
    maxa: float | None
    avgh: float | None
    avgd: float | None
    avga: float | None
    ch: float | None
    cd: float | None
    ca: float | None
    over25: float | None
    under25: float | None
    avg_over25: float | None
    avg_under25: float | None
    ah_line: float | None
    ah_home: float | None
    ah_away: float | None
    avg_ah_home: float | None
    avg_ah_away: float | None


@dataclass
class Team:
    m: int = 0
    pts: float = 0
    gf: int = 0
    ga: int = 0
    hm: int = 0
    hp: float = 0
    am: int = 0
    ap: float = 0

    @property
    def ppg(self) -> float:
        return self.pts / self.m if self.m else 0

    @property
    def hppg(self) -> float:
        return self.hp / self.hm if self.hm else self.ppg

    @property
    def appg(self) -> float:
        return self.ap / self.am if self.am else self.ppg

    @property
    def gd(self) -> float:
        return (self.gf - self.ga) / self.m if self.m else 0


@dataclass(frozen=True)
class Params:
    market: str
    side: str
    min_value: float
    min_odds: float
    max_odds: float
    min_games: int
    min_strength: float
    max_per_season: int
    require_clv_proxy: bool


@dataclass(frozen=True)
class Bet:
    season: str
    league: str
    selection: str
    odds: float
    closing: float | None
    won: bool
    value: float
    profit: float | None = None


def main() -> None:
    rows = load_rows()
    print(f"loaded rows={len(rows)}")
    for league in ["E0", "SP1", "D1", "I1", "F1"]:
        wf = walk_forward(rows, league)
        print(
            league,
            f"roi={wf['roi']}",
            f"bets={wf['bets']}",
            f"profit={wf['profit']}",
            f"positive={wf['positive']}/{wf['folds']}",
            f"clv={wf['clv']}",
            f"params={wf['last_params']}",
        )


def walk_forward(rows: list[Row], league: str) -> dict:
    all_bets: list[Bet] = []
    positives = 0
    last_params = None
    for i in range(2, len(SEASONS)):
        train = SEASONS[:i]
        test = [SEASONS[i]]
        params, train_result = select_params(rows, league, train)
        test_bets = run(rows, league, test, params)
        result = summarize(test_bets)
        if (result["roi"] or 0) > 0:
            positives += 1
        all_bets.extend(test_bets)
        last_params = {**params.__dict__, "train_roi": train_result["roi"], "train_bets": train_result["bets"]}
    summary = summarize(all_bets)
    return {
        **summary,
        "positive": positives,
        "folds": 3,
        "last_params": last_params,
    }


def select_params(rows: list[Row], league: str, seasons: list[str]) -> tuple[Params, dict]:
    best: tuple[float, Params, dict] | None = None
    for params in grid():
        bets = run(rows, league, seasons, params)
        result = summarize(bets)
        if result["bets"] < max(80, 35 * len(seasons)) or result["roi"] is None:
            continue
        season_results = [summarize([b for b in bets if b.season == season]) for season in seasons]
        positive = sum(1 for item in season_results if (item["roi"] or 0) > 0)
        if positive < max(1, len(seasons) // 2):
            continue
        score = (result["roi"] or -1) + positive / len(seasons) * 0.08 + min(result["clv"] or 0, 0.04)
        if best is None or score > best[0]:
            best = (score, params, result)
    if best:
        return best[1], best[2]
    fallback = Params("1x2", "home", 0.015, 1.75, 3.25, 8, 0.0, 80, False)
    return fallback, summarize(run(rows, league, seasons, fallback))


def grid():
    for values in product(
        ["1x2", "ou25", "ah"],
        ["home", "away", "over", "under", "ah_home", "ah_away"],
        [0.015, 0.025, 0.04],
        [1.55, 1.75, 2.0],
        [3.0, 4.5],
        [5, 8],
        [0.0, 0.3],
        [60, 100],
        [False],
    ):
        params = Params(*values)
        if params.market == "1x2" and params.side not in {"home", "away"}:
            continue
        if params.market == "ou25" and params.side not in {"over", "under"}:
            continue
        if params.market == "ah" and params.side not in {"ah_home", "ah_away"}:
            continue
        yield params


def run(rows: list[Row], league: str, seasons: list[str], params: Params) -> list[Bet]:
    states: dict[str, Team] = {}
    by_season: dict[str, list[tuple[float, Bet]]] = {season: [] for season in seasons}
    for row in [r for r in rows if r.league == league and r.season in set(seasons)]:
        home = states.get(row.home, Team())
        away = states.get(row.away, Team())
        strength = (home.hppg - away.appg) + 0.5 * (home.gd - away.gd)
        if home.m >= params.min_games and away.m >= params.min_games:
            maybe = decide(row, params, strength)
            if maybe:
                by_season[row.season].append((maybe.value, maybe))
        update(states, row)
    bets: list[Bet] = []
    for season, season_bets in by_season.items():
        bets.extend([bet for _, bet in sorted(season_bets, key=lambda item: item[0], reverse=True)[: params.max_per_season]])
    return bets


def decide(row: Row, params: Params, strength: float) -> Bet | None:
    if params.market == "1x2":
        if params.side == "home":
            if strength < params.min_strength:
                return None
            odds, avg, closing = row.maxh or row.h, row.avgh, row.ch
            won = row.hg > row.ag
            selection = "HOME"
        else:
            if -strength < params.min_strength:
                return None
            odds, avg, closing = row.maxa or row.a, row.avga, row.ca
            won = row.ag > row.hg
            selection = "AWAY"
    else:
        if params.market == "ah":
            if row.ah_line is None:
                return None
            if params.side == "ah_home":
                if strength < params.min_strength:
                    return None
                odds, avg = row.ah_home, row.avg_ah_home
                won_profit = settle_ah(row.hg, row.ag, row.ah_line, odds, "home")
                selection = f"AH_HOME({row.ah_line})"
            else:
                if -strength < params.min_strength:
                    return None
                odds, avg = row.ah_away, row.avg_ah_away
                won_profit = settle_ah(row.hg, row.ag, row.ah_line, odds, "away")
                selection = f"AH_AWAY({-row.ah_line})"
            closing = None
            won = won_profit > 0
            if not odds or not avg or odds < params.min_odds or odds > params.max_odds:
                return None
            value = odds / avg - 1
            if value < params.min_value:
                return None
            return Bet(row.season, row.league, selection, odds, closing, won_profit > 0, value, won_profit)
        total = row.hg + row.ag
        if params.side == "over":
            odds, avg, closing = row.over25, row.avg_over25, None
            won = total > 2.5
            selection = "OVER25"
        else:
            odds, avg, closing = row.under25, row.avg_under25, None
            won = total < 2.5
            selection = "UNDER25"
    if not odds or not avg or odds < params.min_odds or odds > params.max_odds:
        return None
    value = odds / avg - 1
    if value < params.min_value:
        return None
    if params.require_clv_proxy and closing and odds / closing - 1 < 0:
        return None
    return Bet(row.season, row.league, selection, odds, closing, won, value)


def settle_ah(home_goals: int, away_goals: int, home_line: float, odds: float | None, side: str) -> float:
    if not odds:
        return -1.0
    lines = [home_line]
    if abs(home_line * 4 - round(home_line * 4)) < 1e-9 and abs(home_line * 2 - round(home_line * 2)) > 1e-9:
        lines = [home_line - 0.25, home_line + 0.25]
    stake_part = 1 / len(lines)
    profit = 0.0
    for line in lines:
        if side == "home":
            adjusted = home_goals + line - away_goals
        else:
            adjusted = away_goals - home_goals - line
        if adjusted > 0:
            profit += stake_part * (odds - 1)
        elif adjusted < 0:
            profit -= stake_part
    return profit


def summarize(bets: list[Bet]) -> dict:
    profit = sum(bet.profit if bet.profit is not None else ((bet.odds - 1) if bet.won else -1) for bet in bets)
    clv_values = [(bet.odds / bet.closing - 1) for bet in bets if bet.closing and bet.closing > 1]
    return {
        "bets": len(bets),
        "profit": round(profit, 3),
        "roi": round(profit / len(bets), 4) if bets else None,
        "clv": round(mean(clv_values), 4) if clv_values else None,
    }


def update(states: dict[str, Team], row: Row) -> None:
    hp, ap = points(row.hg, row.ag)
    home = states.setdefault(row.home, Team())
    away = states.setdefault(row.away, Team())
    home.m += 1
    home.pts += hp
    home.gf += row.hg
    home.ga += row.ag
    home.hm += 1
    home.hp += hp
    away.m += 1
    away.pts += ap
    away.gf += row.ag
    away.ga += row.hg
    away.am += 1
    away.ap += ap


def points(hg: int, ag: int) -> tuple[int, int]:
    if hg > ag:
        return 3, 0
    if ag > hg:
        return 0, 3
    return 1, 1


def load_rows() -> list[Row]:
    rows: list[Row] = []
    for season in SEASONS:
        for league in LEAGUES:
            path = Path("data/historical") / season / f"{league}.csv"
            with path.open(encoding="utf-8-sig", newline="") as handle:
                for index, raw in enumerate(csv.DictReader(handle)):
                    if not raw.get("HomeTeam") or not raw.get("AwayTeam") or raw.get("FTHG") in {None, ""}:
                        continue
                    rows.append(
                        Row(
                            league=league,
                            season=season,
                            index=index,
                            home=raw["HomeTeam"],
                            away=raw["AwayTeam"],
                            hg=int(raw["FTHG"]),
                            ag=int(raw["FTAG"]),
                            h=f(raw, "B365H", "PSH", "AvgH"),
                            d=f(raw, "B365D", "PSD", "AvgD"),
                            a=f(raw, "B365A", "PSA", "AvgA"),
                            maxh=f(raw, "MaxH", "B365H"),
                            maxd=f(raw, "MaxD", "B365D"),
                            maxa=f(raw, "MaxA", "B365A"),
                            avgh=f(raw, "AvgH", "B365H"),
                            avgd=f(raw, "AvgD", "B365D"),
                            avga=f(raw, "AvgA", "B365A"),
                            ch=f(raw, "PSCH", "AvgCH", "MaxCH"),
                            cd=f(raw, "PSCD", "AvgCD", "MaxCD"),
                            ca=f(raw, "PSCA", "AvgCA", "MaxCA"),
                            over25=f(raw, "B365>2.5", "Max>2.5", "Avg>2.5"),
                            under25=f(raw, "B365<2.5", "Max<2.5", "Avg<2.5"),
                            avg_over25=f(raw, "Avg>2.5", "B365>2.5"),
                            avg_under25=f(raw, "Avg<2.5", "B365<2.5"),
                            ah_line=f(raw, "AHh"),
                            ah_home=f(raw, "MaxAHH", "B365AHH", "PAHH"),
                            ah_away=f(raw, "MaxAHA", "B365AHA", "PAHA"),
                            avg_ah_home=f(raw, "AvgAHH", "B365AHH", "PAHH"),
                            avg_ah_away=f(raw, "AvgAHA", "B365AHA", "PAHA"),
                        )
                    )
    return rows


def f(raw: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        try:
            value = raw.get(key)
            if value not in {None, ""}:
                return float(value)
        except ValueError:
            pass
    return None


if __name__ == "__main__":
    main()
