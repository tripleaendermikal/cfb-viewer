#!/usr/bin/env python3
"""
Add margin_0001..margin_N and home_flag to cfb_2026_fbs_games_with_fpi.csv.

Preseason simulation margins (no baked-in HFA):
  team_sim_fpi - opponent_sim_fpi

home_flag per row: 1 home, 0 neutral site, -1 away.
HFA enters win probability via home_flag in simulate_cfb_games_preseason_simulation.py.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from _paths import DATA_ROOT
from simulate_cfb_games_preseason_simulation import is_tbd_team

SIM_RE = re.compile(r"^sim_(\d+)$")


def is_neutral_site(neutral_site: str) -> bool:
    return (neutral_site or "").strip().lower() in ("true", "1", "yes")


def site_home_flag(neutral_site: str, home_away: str) -> int:
    if is_neutral_site(neutral_site):
        return 0
    if home_away == "home":
        return 1
    if home_away == "away":
        return -1
    return 0


def parse_float(val: str | None) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input_csv",
        nargs="?",
        type=Path,
        default=DATA_ROOT / "cfb_2026_fbs_games_with_fpi.csv",
    )
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args()

    inp = args.input_csv
    if not inp.is_file():
        print(f"Input not found: {inp}", file=sys.stderr)
        return 1

    out_path = args.output or inp

    with inp.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        all_fields = list(reader.fieldnames or [])
        rows = list(reader)

    sim_cols = sorted(
        [c for c in all_fields if SIM_RE.match(c or "")],
        key=lambda c: int(SIM_RE.match(c).group(1)),
    )
    if not sim_cols:
        print("No sim_XXXX columns found", file=sys.stderr)
        return 1

    margin_cols = []
    for c in sim_cols:
        num = SIM_RE.match(c).group(1)
        w = len(c) - len("sim_")
        margin_cols.append(f"margin_{num.zfill(w)}")

    base_fields = [c for c in all_fields if not c.startswith("margin_")]
    if "home_flag" not in base_fields:
        base_fields.append("home_flag")
    out_fields = base_fields + margin_cols

    fpi_by_game_team: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        gid = (row.get("game_id") or "").strip()
        tid = (row.get("team_id") or "").strip()
        if gid and tid:
            fpi_by_game_team[(gid, tid)] = {c: row.get(c, "") for c in sim_cols}

    for row in rows:
        gid = (row.get("game_id") or "").strip()
        oid = (row.get("opponent_id") or "").strip()
        row["home_flag"] = str(
            site_home_flag(row.get("neutral_site", ""), row.get("home_away", ""))
        )

        opp_vals = fpi_by_game_team.get((gid, oid), {})
        tid = (row.get("team_id") or "").strip()
        tname = row.get("team_name", "")
        oname = row.get("opponent_name", "")
        vs_tbd = is_tbd_team(tid, tname) or is_tbd_team(oid, oname)

        for sc, mc in zip(sim_cols, margin_cols):
            if vs_tbd:
                row[mc] = "0"
                continue
            tf = parse_float(row.get(sc))
            of = parse_float(opp_vals.get(sc))
            if tf is not None and of is not None:
                row[mc] = round(tf - of, 3)
            else:
                row[mc] = ""

    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(
        f"Added home_flag + {len(margin_cols)} FPI-only margin columns "
        f"to {len(rows)} rows -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
