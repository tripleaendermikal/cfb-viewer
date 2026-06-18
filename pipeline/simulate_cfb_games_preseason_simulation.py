#!/usr/bin/env python3
"""
Simulate college football game outcomes for preseason simulation.

When margin_0001..margin_N columns are present (from add_sim_margins_preseason_simulation.py):
  - One Bernoulli draw per game_id per margin column.
  - margin = team_sim_fpi - opponent_sim_fpi (no baked-in HFA).
  - home_flag per row: 1 home, 0 neutral, -1 away.
  - P(home win) = exp(0.175*margin + 0.475*home_flag) /
    (1 + exp(0.175*margin + 0.475*home_flag))
    using the home row's margin and home_flag (1 or 0 at draw time).
  - Complementary 1/0 on both rows for each game.

Legacy mode (no margin columns): recompute margins from variable FPI draws.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

from _paths import DATA_ROOT

if str(DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(DATA_ROOT))

from cfb_in_season_sim import IN_SEASON_PREFIX, actual_team_win, is_completed

DEFAULT_NUM_SIMULATIONS = 100
DEFAULT_FPI_SIGMA = 14.0
FPI_MIN = -42.0
FPI_MAX = 42.0

MARGIN_RE = re.compile(r"^margin_(\d+)$")

TBD_TEAM_IDS = frozenset({"-1", "-2"})


def is_tbd_team(team_id: str, team_name: str = "") -> bool:
    tid = (team_id or "").strip()
    if tid in TBD_TEAM_IDS:
        return True
    return (team_name or "").strip().upper() == "TBD"


def is_neutral_site(neutral_site: str) -> bool:
    return (neutral_site or "").strip().lower() in ("true", "1", "yes")


def site_home_flag(neutral_site: str, home_away: str) -> float:
    if is_neutral_site(neutral_site):
        return 0.0
    if home_away == "home":
        return 1.0
    if home_away == "away":
        return -1.0
    return 0.0


def resolve_home_row_flag(row: dict[str, str]) -> float:
    raw = (row.get("home_flag") or "").strip()
    if raw:
        return float(raw)
    return site_home_flag(row.get("neutral_site", ""), row.get("home_away", ""))


def win_probability(margin: float, home_flag_val: float) -> float:
    """P(home win) from fitted preseason logistic model."""
    x = 0.175 * margin + 0.475 * home_flag_val
    return math.exp(x) / (1.0 + math.exp(x))


def clamp_fpi(x: float) -> float:
    return max(FPI_MIN, min(FPI_MAX, x))


def sim_column_names(n: int, width: int | None = None) -> list[str]:
    w = width if width is not None else max(3, len(str(n)))
    return [f"sim_{i:0{w}d}" for i in range(1, n + 1)]


def find_margin_columns(fieldnames: list[str] | None) -> list[str]:
    if not fieldnames:
        return []
    cols = [c for c in fieldnames if MARGIN_RE.match(c or "")]
    return sorted(cols, key=lambda c: int(MARGIN_RE.match(c).group(1)))


def margin_to_win_col(margin_col: str) -> str:
    num = MARGIN_RE.match(margin_col).group(1)
    w = len(margin_col) - len("margin_")
    return f"sim_{num.zfill(w)}"


def parse_float(val: str | None) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def build_base_fpi_by_team(rows: list[dict[str, str]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in rows:
        for tid_key, fpi_key in (
            ("team_id", "team_fpi"),
            ("opponent_id", "opponent_fpi"),
        ):
            tid = (r.get(tid_key) or "").strip()
            if not tid:
                continue
            raw = (r.get(fpi_key) or "").strip()
            try:
                v = float(raw) if raw else 0.0
            except ValueError:
                v = 0.0
            if tid not in out:
                out[tid] = v
    return out


def game_has_pinned_result(sides: list[dict[str, str]]) -> bool:
    """Return True when both teams have a completed game with valid scores."""
    if len(sides) != 2:
        return False
    if not all(is_completed(side) for side in sides):
        return False
    return all(actual_team_win(side) is not None for side in sides)


def simulate_from_margins(
    rows: list[dict[str, str]],
    rng: random.Random,
    margin_cols: list[str],
    *,
    pin_completed: bool = False,
) -> list[dict[str, str]]:
    by_game: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_game[row["game_id"]].append(row)

    win_cols = [margin_to_win_col(mc) for mc in margin_cols]
    win_by_team: dict[tuple[str, str], dict[str, str]] = {}

    for game_id, sides in by_game.items():
        for s in sides:
            win_by_team[(game_id, s["team_id"])] = {wc: "" for wc in win_cols}

        if len(sides) != 2:
            continue

        home = next((s for s in sides if s.get("home_away") == "home"), sides[0])
        away = next(
            (s for s in sides if s.get("home_away") == "away"),
            sides[1] if sides[1] is not home else sides[0],
        )

        pinned = pin_completed and game_has_pinned_result(sides)
        if pinned:
            for wc in win_cols:
                home_result = actual_team_win(home)
                away_result = actual_team_win(away)
                if home_result is None or away_result is None:
                    continue
                win_by_team[(game_id, home["team_id"])][wc] = home_result
                win_by_team[(game_id, away["team_id"])][wc] = away_result
            continue

        tbd_matchup = is_tbd_team(home["team_id"], home.get("team_name", "")) or is_tbd_team(
            away["team_id"], away.get("team_name", "")
        )

        for mc, wc in zip(margin_cols, win_cols):
            if tbd_matchup:
                p_home = 0.5
            else:
                margin = parse_float(home.get(mc))
                if margin is None:
                    margin = parse_float(away.get(mc))
                    if margin is not None:
                        margin = -margin
                if margin is None:
                    continue
                hf = resolve_home_row_flag(home)
                p_home = win_probability(margin, hf)
            home_wins = rng.random() < p_home

            win_by_team[(game_id, home["team_id"])][wc] = "1" if home_wins else "0"
            win_by_team[(game_id, away["team_id"])][wc] = "0" if home_wins else "1"

    out: list[dict[str, str]] = []
    for row in rows:
        key = (row["game_id"], row["team_id"])
        wins = win_by_team.get(key, {})
        r = {k: v for k, v in row.items() if not k.startswith(("sim_", "margin_"))}
        for wc in win_cols:
            r[wc] = wins.get(wc, "")
        out.append(r)
    return out


def simulate_from_variable_fpi(
    rows: list[dict[str, str]],
    rng: random.Random,
    n_sims: int,
    fpi_sigma: float,
) -> list[dict[str, str]]:
    by_game: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_game[row["game_id"]].append(row)

    base_fpi = build_base_fpi_by_team(rows)
    team_ids = sorted(base_fpi.keys())
    cols = sim_column_names(n_sims)
    win_by_team: dict[tuple[str, str], list[str]] = {}

    for game_id, sides in by_game.items():
        for s in sides:
            win_by_team[(game_id, s["team_id"])] = [""] * n_sims

    game_list = list(by_game.items())

    for sim_i in range(n_sims):
        adj: dict[str, float] = {}
        for tid in team_ids:
            adj[tid] = clamp_fpi(rng.gauss(base_fpi[tid], fpi_sigma))

        for game_id, sides in game_list:
            if len(sides) != 2:
                continue

            home = next((s for s in sides if s.get("home_away") == "home"), None)
            away = next((s for s in sides if s.get("home_away") == "away"), None)
            if home is None or away is None:
                home, away = sides[0], sides[1]

            hid, aid = home["team_id"], away["team_id"]

            for s in (home, away):
                tid = s["team_id"]
                if tid not in adj:
                    mu = base_fpi.get(tid)
                    if mu is None:
                        mu = float((s.get("team_fpi") or "0").strip() or 0)
                    adj[tid] = clamp_fpi(rng.gauss(mu, fpi_sigma))

            if is_tbd_team(hid, home.get("team_name", "")) or is_tbd_team(
                aid, away.get("team_name", "")
            ):
                p_home = 0.5
            else:
                fh, fa = adj[hid], adj[aid]
                margin_home = fh - fa
                hf = resolve_home_row_flag(home)
                p_home = win_probability(margin_home, hf)
            home_wins = rng.random() < p_home

            win_by_team[(game_id, hid)][sim_i] = "1" if home_wins else "0"
            win_by_team[(game_id, aid)][sim_i] = "0" if home_wins else "1"

    out: list[dict[str, str]] = []
    for row in rows:
        r = {k: v for k, v in row.items() if not k.startswith(("sim_", "margin_"))}
        key = (row["game_id"], row["team_id"])
        series = win_by_team.get(key)
        if series is not None:
            for name, val in zip(cols, series):
                r[name] = val
        else:
            for name in cols:
                r[name] = ""
        out.append(r)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input_csv",
        nargs="?",
        default=DATA_ROOT / "cfb_2026_fbs_games_with_fpi.csv",
        type=Path,
    )
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "-n",
        "--simulations",
        type=int,
        default=None,
        metavar="N",
        help="Number of simulations (default: all margin_* columns, or 100)",
    )
    parser.add_argument(
        "--fpi-sigma",
        type=float,
        default=DEFAULT_FPI_SIGMA,
        help=f"Legacy mode only: FPI noise std dev (default: {DEFAULT_FPI_SIGMA})",
    )
    parser.add_argument(
        "--legacy-fpi",
        action="store_true",
        help="Ignore margin columns; draw variable FPI per team per sim",
    )
    parser.add_argument(
        "--pin-completed",
        action="store_true",
        help="Use actual W/L for completed games in all sim columns",
    )
    args = parser.parse_args()

    inp = args.input_csv
    if not inp.is_file():
        alt = inp.parent / "cfb_2026_fbs_games_with_fpi_margin.csv"
        if alt.is_file():
            inp = alt
        else:
            print(f"Input not found: {args.input_csv}", file=sys.stderr)
            return 1

    out_path = args.output
    if out_path is None:
        if args.pin_completed and IN_SEASON_PREFIX in inp.stem:
            out_path = inp.with_name(f"{IN_SEASON_PREFIX}_fbs_games_with_fpi_preseason_simulated.csv")
        else:
            out_path = inp.with_name(inp.stem + "_preseason_simulated" + inp.suffix)

    with inp.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    margin_cols = find_margin_columns(fieldnames)
    rng = random.Random(args.seed)

    if margin_cols and not args.legacy_fpi:
        if args.simulations is not None:
            margin_cols = margin_cols[: args.simulations]
        simulated = simulate_from_margins(
            rows, rng, margin_cols, pin_completed=args.pin_completed
        )
        win_cols = [margin_to_win_col(mc) for mc in margin_cols]
        base_fields = [c for c in fieldnames if not c.startswith(("sim_", "margin_"))]
        out_fields = base_fields + win_cols
        mode = f"margin columns ({len(margin_cols)} sims)"
        if args.pin_completed:
            mode += ", pin completed"
    else:
        n_sims = args.simulations or DEFAULT_NUM_SIMULATIONS
        if n_sims < 1:
            print("--simulations must be at least 1", file=sys.stderr)
            return 1
        if args.fpi_sigma <= 0:
            print("--fpi-sigma must be positive", file=sys.stderr)
            return 1
        simulated = simulate_from_variable_fpi(rows, rng, n_sims, args.fpi_sigma)
        win_cols = sim_column_names(n_sims)
        base_fields = [c for c in fieldnames if not c.startswith(("sim_", "margin_"))]
        out_fields = base_fields + win_cols
        mode = f"variable FPI ({n_sims} sims, sigma={args.fpi_sigma})"

    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(simulated)

    print(f"Wrote {len(simulated)} rows and {len(win_cols)} win columns to {out_path}")
    print(f"Mode: {mode}")
    print(
        "P(home win) = exp(0.175*margin + 0.475*home_flag) / "
        "(1 + exp(0.175*margin + 0.475*home_flag)); one draw per game per column"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
