#!/usr/bin/env python3
"""Export CFB simulation CSV outputs to JSON for the viewer app."""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

SIM_COL_PATTERN = re.compile(r"^sim_\d+$")
GROUP_OF_6 = {"American", "Pac-12", "Sun Belt", "CUSA", "Mountain West", "MAC"}
DEFAULT_SIGMA = 10.0
FPI_MIN = -40.0
FPI_MAX = 40.0
FPI_MAX_GROUP_OF_6 = 27.0
Z_90 = 1.645
FCS_CONFERENCES = {
    "Big Sky",
    "CAA",
    "FCS Indep.",
    "MEAC",
    "MVFC",
    "NEC",
    "OVC-Big South",
    "Patriot",
    "SWAC",
    "Southern",
    "Southland",
    "UAC",
}


def is_fbs_conference(conf: str) -> bool:
    return bool(conf) and conf not in FCS_CONFERENCES and conf != "Unknown"

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(__file__).resolve().parent / "data"

SOURCES = {
    "champ_odds": ROOT / "cfb_2026_FBS_playoff_champ_odds_fpi_seed.csv",
    "conf_champ_odds": ROOT / "cfb_2026_FBS_conf_champ_odds.csv",
    "elig_pct": ROOT / "cfb_2026_FBS_playoff_elig_pct.csv",
    "elig": ROOT / "cfb_2026_FBS_playoff_elig_v2.csv",
    "records": ROOT / "cfb_2026_fbs_team_sim_records_v2.csv",
    "games_sim": ROOT / "cfb_2026_fbs_games_with_fpi_simulated.csv",
    "games_fpi": ROOT / "cfb_2026_fbs_games_with_fpi.csv",
    "conferences": ROOT / "espn_cfb_teams_conferences.csv",
}

LAST_YEAR_SOURCES = {
    "margin_ratings": ROOT / "cfb_2025_fbs_margin_ratings.csv",
    "espn_games": ROOT / "cfb_2025_espn_games.csv",
    "conferences": ROOT / "espn_cfb_teams_conferences.csv",
}


def sim_columns(fieldnames: list[str]) -> list[str]:
    cols = [c for c in fieldnames if SIM_COL_PATTERN.match(c or "")]
    return sorted(cols, key=lambda c: int(c.split("_")[1]))


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        return fieldnames, list(reader)


def write_json(path: Path, data: object) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def build_teams(records_rows: list[dict], sim_cols: list[str]) -> list[dict]:
    teams = []
    for row in records_rows:
        tid = row["team_id"]
        wins = [int(row[c]) for c in sim_cols]
        hist = Counter(wins)
        histogram = {str(w): hist.get(w, 0) for w in range(0, 13)}
        teams.append(
            {
                "team_id": tid,
                "team_name": row["team_name"],
                "conference": row.get("conference", ""),
                "avg_wins": round(sum(wins) / len(wins), 2) if wins else 0,
                "win_histogram": histogram,
            }
        )
    return teams


def merge_conf_champ_odds(leaderboard: list[dict], conf_champ_rows: list[dict]) -> None:
    by_name = {r["team_name"]: r for r in conf_champ_rows}
    by_id = {r["team_id"]: r for r in conf_champ_rows}
    for row in leaderboard:
        src = by_id.get(row.get("team_id") or "") or by_name.get(row["team_name"])
        if src:
            row["conf_champ_odds_pct"] = float(src["conf_champ_odds_pct"])
            row["conf_champ_appearances"] = int(src["conf_champ_appearances"])
            row["conf_champ_game_win_pct"] = float(src.get("conf_champ_game_win_pct", 0))
        else:
            row["conf_champ_odds_pct"] = 0.0
            row["conf_champ_appearances"] = 0
            row["conf_champ_game_win_pct"] = 0.0


def build_leaderboard(champ_rows: list[dict], elig_pct_rows: list[dict]) -> list[dict]:
    pct_by_name = {r["team_name"]: float(r["eligibility_pct"]) for r in elig_pct_rows}
    out = []
    for row in champ_rows:
        name = row["team_name"]
        out.append(
            {
                "team_id": None,
                "team_name": name,
                "conference": row["conference"],
                "title_odds_pct": float(row["title_odds_pct"]),
                "playoff_appearances": int(row["playoff_appearances"]),
                "avg_seed_when_in": float(row["avg_seed_when_in"]),
                "eligibility_pct": pct_by_name.get(name, 0.0),
            }
        )
    for row in elig_pct_rows:
        if row["team_name"] not in {r["team_name"] for r in out}:
            out.append(
                {
                    "team_id": None,
                    "team_name": row["team_name"],
                    "conference": row["conference"],
                    "title_odds_pct": 0.0,
                    "playoff_appearances": 0,
                    "avg_seed_when_in": 0.0,
                    "eligibility_pct": float(row["eligibility_pct"]),
                }
            )
    return out


def merge_team_ids(leaderboard: list[dict], teams: list[dict]) -> None:
    by_name = {t["team_name"]: t["team_id"] for t in teams}
    by_id = {t["team_id"]: t for t in teams}
    for row in leaderboard:
        row["team_id"] = by_name.get(row["team_name"])
        team = by_id.get(row["team_id"]) if row["team_id"] else None
        row["avg_wins"] = team["avg_wins"] if team else None


def clamp_fpi(x: float, cap: float) -> float:
    return max(FPI_MIN, min(cap, x))


def fpi_cap_for_conference(conf: str) -> float:
    if conf in GROUP_OF_6:
        return FPI_MAX_GROUP_OF_6
    return FPI_MAX


def build_baseline_fpi(games_fpi_rows: list[dict]) -> dict[str, float]:
    base: dict[str, float] = {}
    for row in games_fpi_rows:
        tid = (row.get("team_id") or "").strip()
        if not tid or tid in base:
            continue
        raw = (row.get("team_fpi") or "").strip()
        try:
            base[tid] = float(raw) if raw else 0.0
        except ValueError:
            base[tid] = 0.0
    return base


def fpi_ci(mu: float, cap: float, sigma: float = DEFAULT_SIGMA) -> tuple[float, float]:
    low = clamp_fpi(mu - Z_90 * sigma, cap)
    high = clamp_fpi(mu + Z_90 * sigma, cap)
    return low, high


def enrich_leaderboard_fpi(
    leaderboard: list[dict],
    baseline_by_id: dict[str, float],
    conf_by_id: dict[str, str],
) -> None:
    for row in leaderboard:
        tid = row.get("team_id")
        if not tid or tid not in baseline_by_id:
            row["baseline_fpi"] = None
            row["fpi_ci_low"] = None
            row["fpi_ci_high"] = None
            continue
        mu = baseline_by_id[tid]
        conf = conf_by_id.get(tid) or row.get("conference", "")
        cap = fpi_cap_for_conference(conf)
        low, high = fpi_ci(mu, cap)
        row["baseline_fpi"] = round(mu, 1)
        row["fpi_ci_low"] = round(low, 1)
        row["fpi_ci_high"] = round(high, 1)


def build_eligibility(elig_rows: list[dict], sim_cols: list[str], name_to_id: dict) -> dict:
    fields = []
    for col in sim_cols:
        ids = sorted(
            name_to_id[r["team_name"]]
            for r in elig_rows
            if r[col] == "1" and r["team_name"] in name_to_id
        )
        fields.append(ids)
    return {"sim_count": len(sim_cols), "fields": fields}


def build_field_analysis(fields: list[list[str]], id_to_name: dict, n_sims: int) -> dict:
    team_freq: Counter[str] = Counter()
    pair_freq: Counter[tuple[str, str]] = Counter()

    for field in fields:
        for tid in field:
            team_freq[tid] += 1
        for a, b in combinations(sorted(field), 2):
            pair_freq[(a, b)] += 1

    def team_entry(tid: str, count: int) -> dict:
        return {
            "team_id": tid,
            "team_name": id_to_name.get(tid, tid),
            "count": count,
            "pct": round(count / n_sims * 100, 2),
        }

    top_teams = [team_entry(tid, c) for tid, c in team_freq.most_common(25)]
    top_pairs = []
    for (a, b), c in pair_freq.most_common(25):
        top_pairs.append(
            {
                "team_a_id": a,
                "team_a_name": id_to_name.get(a, a),
                "team_b_id": b,
                "team_b_name": id_to_name.get(b, b),
                "count": c,
                "pct": round(c / n_sims * 100, 2),
            }
        )

    field_sets = [frozenset(f) for f in fields]
    best_overlap = (0, 0, 0)
    for i in range(len(field_sets)):
        for j in range(i + 1, len(field_sets)):
            ov = len(field_sets[i] & field_sets[j])
            if ov > best_overlap[0]:
                best_overlap = (ov, i, j)

    closest = None
    if best_overlap[0] > 0:
        i, j = best_overlap[1], best_overlap[2]
        shared = sorted(field_sets[i] & field_sets[j])
        closest = {
            "sim_a": i + 1,
            "sim_b": j + 1,
            "shared_count": best_overlap[0],
            "shared_team_ids": list(shared),
            "shared_team_names": [id_to_name.get(t, t) for t in shared],
            "only_a_ids": sorted(field_sets[i] - field_sets[j]),
            "only_b_ids": sorted(field_sets[j] - field_sets[i]),
        }

    unique_fields = len(set(tuple(f) for f in fields))
    return {
        "unique_field_count": unique_fields,
        "top_teams": top_teams,
        "top_pairs": top_pairs,
        "closest_fields": closest,
    }


def build_schedule(
    game_rows: list[dict],
    sim_cols: list[str],
    margin_by_game_team: dict[tuple[str, str], float],
    conf_by_id: dict[str, str],
) -> list[dict]:
    n = len(sim_cols)
    schedule = []
    for row in game_rows:
        gid = row.get("game_id", "")
        tid = row.get("team_id", "")
        wins = sum(1 for c in sim_cols if row.get(c) == "1")
        win_pct = round(wins / n * 100, 2) if n else 0
        margin = margin_by_game_team.get((gid, tid))
        schedule.append(
            {
                "game_id": gid,
                "game_date": (row.get("game_date_utc") or "")[:10],
                "week": int(row["week"]) if row.get("week", "").isdigit() else row.get("week"),
                "season_type": row.get("season_type", ""),
                "team_id": tid,
                "team_name": row.get("team_name", ""),
                "conference": conf_by_id.get(tid, ""),
                "home_away": row.get("home_away", ""),
                "neutral_site": row.get("neutral_site", ""),
                "opponent_id": row.get("opponent_id", ""),
                "opponent_name": row.get("opponent_name", ""),
                "team_fpi": float(row["team_fpi"]) if row.get("team_fpi") else None,
                "opponent_fpi": float(row["opponent_fpi"]) if row.get("opponent_fpi") else None,
                "win_pct": win_pct,
                "avg_margin": round(margin, 3) if margin is not None else None,
            }
        )
    return dedupe_schedule(schedule)


def dedupe_schedule(schedule: list[dict]) -> list[dict]:
    """One row per game_id; prefer the home team's perspective."""
    by_game: dict[str, dict] = {}
    for row in schedule:
        gid = row.get("game_id", "")
        if not gid:
            continue
        existing = by_game.get(gid)
        if existing is None or row.get("home_away") == "home":
            by_game[gid] = row
    return sorted(
        by_game.values(),
        key=lambda g: (g.get("game_date", ""), g.get("week") or 0, g.get("game_id", "")),
    )


def load_avg_margins(path: Path, sim_cols: list[str]) -> dict[tuple[str, str], float]:
    if not path.is_file():
        return {}
    fieldnames, rows = read_csv(path)
    margin_cols = sorted(
        [c for c in fieldnames if re.match(r"^margin_\d+$", c or "")],
        key=lambda c: int(c.split("_")[1]),
    )
    if len(margin_cols) != len(sim_cols):
        margin_cols = margin_cols[: len(sim_cols)]
    out: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        gid = row.get("game_id", "")
        tid = row.get("team_id", "")
        if not gid or not tid:
            continue
        vals = []
        for mc in margin_cols:
            raw = (row.get(mc) or "").strip()
            if raw:
                try:
                    vals.append(float(raw))
                except ValueError:
                    pass
        if vals:
            out[(gid, tid)] = vals
    return {k: sum(v) / len(v) for k, v in out.items()}


def build_conferences(teams: list[dict], leaderboard: list[dict], fields: list[list[str]], n_sims: int) -> list[dict]:
    lb_by_id = {r["team_id"]: r for r in leaderboard if r.get("team_id")}
    by_conf: dict[str, list[dict]] = {}
    for t in teams:
        by_conf.setdefault(t["conference"] or "Unknown", []).append(t)

    out = []
    for conf, members in sorted(by_conf.items()):
        if not is_fbs_conference(conf):
            continue
        team_ids = {m["team_id"] for m in members}
        conf_fields_with_member = sum(1 for field in fields if any(tid in team_ids for tid in field))
        playoff_apps = sum(lb_by_id.get(m["team_id"], {}).get("playoff_appearances", 0) for m in members)
        title_sum = sum(lb_by_id.get(m["team_id"], {}).get("title_odds_pct", 0) for m in members)
        conf_champ_sum = sum(
            lb_by_id.get(m["team_id"], {}).get("conf_champ_odds_pct", 0) for m in members
        )
        conf_favorite = max(
            members,
            key=lambda m: lb_by_id.get(m["team_id"], {}).get("conf_champ_odds_pct", 0),
        )
        fav_lb = lb_by_id.get(conf_favorite["team_id"], {})
        out.append(
            {
                "conference": conf,
                "team_count": len(members),
                "is_group_of_6": conf in GROUP_OF_6,
                "total_title_odds_pct": round(title_sum, 2),
                "total_conf_champ_odds_pct": round(conf_champ_sum, 2),
                "conf_favorite_name": conf_favorite["team_name"],
                "conf_favorite_odds_pct": fav_lb.get("conf_champ_odds_pct", 0),
                "total_playoff_appearances": playoff_apps,
                "sims_with_member": conf_fields_with_member,
                "sims_with_member_pct": round(conf_fields_with_member / n_sims * 100, 2) if n_sims else 0,
                "teams": sorted(
                    [
                        {
                            "team_id": m["team_id"],
                            "team_name": m["team_name"],
                            "avg_wins": m["avg_wins"],
                            "title_odds_pct": lb_by_id.get(m["team_id"], {}).get("title_odds_pct", 0),
                            "conf_champ_odds_pct": lb_by_id.get(m["team_id"], {}).get(
                                "conf_champ_odds_pct", 0
                            ),
                            "conf_champ_appearances": lb_by_id.get(m["team_id"], {}).get(
                                "conf_champ_appearances", 0
                            ),
                            "eligibility_pct": lb_by_id.get(m["team_id"], {}).get("eligibility_pct", 0),
                            "playoff_appearances": lb_by_id.get(m["team_id"], {}).get("playoff_appearances", 0),
                        }
                        for m in members
                    ],
                    key=lambda x: -x["conf_champ_odds_pct"],
                ),
            }
        )
    return out


def load_fbs_team_ids(conferences_path: Path) -> set[str]:
    _, rows = read_csv(conferences_path)
    return {
        row["team_id"].strip()
        for row in rows
        if is_fbs_conference(row.get("conference", ""))
    }


def build_last_year() -> dict:
    """Export 2025 FBS ratings and game results for the viewer Last Year tab."""
    _, rating_rows = read_csv(LAST_YEAR_SOURCES["margin_ratings"])
    teams = []
    for row in rating_rows:
        teams.append(
            {
                "team_id": row["team_id"].strip(),
                "team_name": row["team_name"].strip(),
                "conference": row["conference"].strip(),
                "rating": float(row["rating"]),
                "r": float(row["r"]),
                "rs": float(row["rs"]),
                "margin_rating": float(row["margin_rating"]),
            }
        )
    teams.sort(key=lambda t: (-t["margin_rating"], t["team_name"].lower()))

    fbs_ids = load_fbs_team_ids(LAST_YEAR_SOURCES["conferences"])
    _, conf_rows = read_csv(LAST_YEAR_SOURCES["conferences"])
    conf_by_id = {
        row["team_id"].strip(): row["conference"].strip()
        for row in conf_rows
        if row.get("team_id")
    }

    _, game_rows = read_csv(LAST_YEAR_SOURCES["espn_games"])
    games = []
    for row in game_rows:
        home_id = row["home_team_id"].strip()
        away_id = row["away_team_id"].strip()
        if home_id not in fbs_ids or away_id not in fbs_ids:
            continue
        home_yards = int(float(row["home_total_yards"]))
        away_yards = int(float(row["away_total_yards"]))
        if home_yards <= 0 or away_yards <= 0:
            continue
        games.append(
            {
                "game_id": row["game_id"].strip(),
                "season": int(row["season"]),
                "week": int(row["week"]),
                "game_date": row["game_date_utc"][:10],
                "neutral_site": row["neutral_site"].strip().lower() == "true",
                "home_team_id": home_id,
                "home_team": row["home_team"].strip(),
                "home_conference": conf_by_id.get(home_id, ""),
                "home_points": int(row["home_points"]),
                "home_yards": home_yards,
                "away_team_id": away_id,
                "away_team": row["away_team"].strip(),
                "away_conference": conf_by_id.get(away_id, ""),
                "away_points": int(row["away_points"]),
                "away_yards": away_yards,
            }
        )
    games.sort(key=lambda g: (g["game_date"], g["week"], g["game_id"]))

    return {
        "season_year": 2025,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "sources": {k: str(v) for k, v in LAST_YEAR_SOURCES.items()},
        "team_count": len(teams),
        "game_count": len(games),
        "teams": teams,
        "games": games,
    }


def main() -> int:
    for key, path in SOURCES.items():
        if not path.is_file():
            print(f"Missing source file ({key}): {path}", file=sys.stderr)
            return 1

    _, records_rows = read_csv(SOURCES["records"])
    sim_cols = sim_columns(list(records_rows[0].keys()) if records_rows else [])
    n_sims = len(sim_cols)

    teams = build_teams(records_rows, sim_cols)
    name_to_id = {t["team_name"]: t["team_id"] for t in teams}
    id_to_name = {t["team_id"]: t["team_name"] for t in teams}

    _, champ_rows = read_csv(SOURCES["champ_odds"])
    _, elig_pct_rows = read_csv(SOURCES["elig_pct"])
    _, conf_champ_rows = read_csv(SOURCES["conf_champ_odds"])
    leaderboard = build_leaderboard(champ_rows, elig_pct_rows)
    merge_team_ids(leaderboard, teams)
    merge_conf_champ_odds(leaderboard, conf_champ_rows)
    conf_by_id = {t["team_id"]: t["conference"] for t in teams}
    _, games_fpi_rows = read_csv(SOURCES["games_fpi"])
    baseline_by_id = build_baseline_fpi(games_fpi_rows)
    enrich_leaderboard_fpi(leaderboard, baseline_by_id, conf_by_id)
    leaderboard = [r for r in leaderboard if is_fbs_conference(r.get("conference", ""))]
    leaderboard.sort(
        key=lambda r: (
            r.get("baseline_fpi") is None,
            -(r.get("baseline_fpi") if r.get("baseline_fpi") is not None else -999),
            r["team_name"].lower(),
        )
    )

    _, elig_rows = read_csv(SOURCES["elig"])
    eligibility = build_eligibility(elig_rows, sim_cols, name_to_id)
    field_analysis = build_field_analysis(eligibility["fields"], id_to_name, n_sims)

    _, game_rows = read_csv(SOURCES["games_sim"])
    margin_map = load_avg_margins(SOURCES["games_fpi"], sim_cols)
    schedule = build_schedule(game_rows, sim_cols, margin_map, conf_by_id)
    conferences = build_conferences(teams, leaderboard, eligibility["fields"], n_sims)

    season_year = 2026
    if game_rows and game_rows[0].get("season_year"):
        try:
            season_year = int(game_rows[0]["season_year"])
        except ValueError:
            pass

    meta = {
        "season_year": season_year,
        "sim_count": n_sims,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "sources": {k: str(v) for k, v in SOURCES.items()},
        "team_count": len(teams),
        "game_count": len(schedule),
        "fpi_sigma": DEFAULT_SIGMA,
        "fpi_ci_method": "analytical_90",
    }

    sizes = {}
    for name, payload in [
        ("meta.json", meta),
        ("leaderboard.json", leaderboard),
        ("teams.json", teams),
        ("eligibility.json", eligibility),
        ("field_analysis.json", field_analysis),
        ("schedule.json", schedule),
        ("conferences.json", conferences),
        ("last_year.json", build_last_year()),
    ]:
        sizes[name] = write_json(DATA_DIR / name, payload)

    print(f"Exported to {DATA_DIR}")
    print(f"  Teams: {len(teams)}, Sims: {n_sims}, Games: {len(schedule)}")
    print(f"  Unique playoff fields: {field_analysis['unique_field_count']}")
    for name, nbytes in sizes.items():
        print(f"  {name}: {nbytes / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
