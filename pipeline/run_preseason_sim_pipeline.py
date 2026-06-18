#!/usr/bin/env python3
"""Run the 2026 preseason Monte Carlo simulation pipeline (margins through viewer export)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from _paths import DATA_ROOT, PIPELINE_DIR, REPO_ROOT

PYTHON = sys.executable
VIEWER_DATA = REPO_ROOT / "data"

GAMES_FPI = DATA_ROOT / "cfb_2026_fbs_games_with_fpi.csv"
GAMES_SIM = DATA_ROOT / "cfb_2026_fbs_games_with_fpi_simulated.csv"
TEAM_RECORDS = DATA_ROOT / "cfb_2026_fbs_team_sim_records_v2.csv"
CONF_ODDS = DATA_ROOT / "cfb_2026_FBS_conf_champ_odds.csv"
PLAYOFF_ELIG = DATA_ROOT / "cfb_2026_FBS_playoff_elig_v2.csv"
TITLE_ODDS = DATA_ROOT / "cfb_2026_FBS_playoff_champ_odds_fpi_seed.csv"
CONF_CSV = DATA_ROOT / "espn_cfb_teams_conferences.csv"
NOTRE_DAME_TEAM_ID = "87"


def run_step(label: str, cmd: list[str]) -> None:
    print(f"\n==> {label}")
    print("    " + " ".join(str(part) for part in cmd))
    subprocess.run(cmd, cwd=DATA_ROOT, check=True)


def spot_check_notre_dame_eligibility() -> None:
    leaderboard_path = VIEWER_DATA / "leaderboard.json"
    field_path = VIEWER_DATA / "field_analysis.json"
    if not leaderboard_path.is_file() or not field_path.is_file():
        print("Spot-check skipped: viewer JSON not found (run export first).")
        return

    leaderboard = json.loads(leaderboard_path.read_text(encoding="utf-8"))
    field = json.loads(field_path.read_text(encoding="utf-8"))

    lb_pct = None
    for row in leaderboard:
        if str(row.get("team_id", "")).strip() == NOTRE_DAME_TEAM_ID:
            lb_pct = row.get("eligibility_pct")
            break

    fa_pct = None
    for row in field.get("top_teams", field.get("teams", [])):
        if str(row.get("team_id", "")).strip() == NOTRE_DAME_TEAM_ID:
            fa_pct = row.get("pct")
            break

    print("\nNotre Dame eligibility_pct spot-check:")
    print(f"  leaderboard.json: {lb_pct}")
    print(f"  field_analysis.json: {fa_pct}")
    if lb_pct is not None and fa_pct is not None and float(lb_pct) == float(fa_pct):
        print("  OK: values match")
    else:
        print("  WARNING: values differ or missing")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=None, help="RNG seed for game simulation")
    parser.add_argument(
        "--skip-export",
        action="store_true",
        help="Skip cfb-viewer/export_sim_data.py",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if not GAMES_FPI.is_file():
        print(f"Base games file not found: {GAMES_FPI}", file=sys.stderr)
        return 1

    run_step(
        "Add FPI-only margins and home_flag",
        [PYTHON, str(PIPELINE_DIR / "add_sim_margins_preseason_simulation.py")],
    )

    sim_cmd = [
        PYTHON,
        str(PIPELINE_DIR / "simulate_cfb_games_preseason_simulation.py"),
        str(GAMES_FPI),
        "-o",
        str(GAMES_SIM),
    ]
    if args.seed is not None:
        sim_cmd.extend(["--seed", str(args.seed)])
    run_step("Simulate games (preseason win formula)", sim_cmd)

    run_step(
        "Conference championship odds",
        [
            PYTHON,
            str(DATA_ROOT / "cfb_conf_championship_odds.py"),
            "--from-data",
            "--games-sim",
            str(GAMES_SIM),
            "--games-fpi",
            str(GAMES_FPI),
            "--conferences",
            str(CONF_CSV),
            "--output",
            str(CONF_ODDS),
        ],
    )

    run_step(
        "Aggregate team win records",
        [
            PYTHON,
            str(DATA_ROOT / "team_simulation_records.py"),
            str(GAMES_SIM),
            "-o",
            str(TEAM_RECORDS),
            "--conferences",
            str(CONF_CSV),
        ],
    )

    run_step(
        "Playoff eligibility",
        [PYTHON, str(DATA_ROOT / "cfb_2026_FBS_playoff_elig_v2.py")],
    )

    run_step(
        "National title odds",
        [
            PYTHON,
            str(DATA_ROOT / "cfb_playoff_odds_calc.py"),
            "--from-data",
            "--elig",
            str(PLAYOFF_ELIG),
            "--games-fpi",
            str(GAMES_FPI),
            "--output",
            str(TITLE_ODDS),
        ],
    )

    if not args.skip_export:
        run_step(
            "Export viewer JSON",
            [PYTHON, str(REPO_ROOT / "export_sim_data.py")],
        )
        spot_check_notre_dame_eligibility()

    print("\nPreseason pipeline complete.")
    print(f"  Games (FPI):     {GAMES_FPI}")
    print(f"  Simulated games: {GAMES_SIM}")
    print(f"  Team records:    {TEAM_RECORDS}")
    print(f"  Title odds:      {TITLE_ODDS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
