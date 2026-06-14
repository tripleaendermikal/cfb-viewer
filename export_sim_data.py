#!/usr/bin/env python3
"""Export CFB simulation CSV outputs to JSON for the viewer app."""

from __future__ import annotations

import csv
import json
import math
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

SIM_COL_PATTERN = re.compile(r"^sim_\d+$")
GROUP_OF_6 = {"American", "Pac-12", "Sun Belt", "CUSA", "Mountain West", "MAC"}
FBS_INDEP = "FBS Indep."
DEFAULT_SIGMA = 10.0
FPI_MIN = -40.0
FPI_MAX = 40.0
FPI_MAX_GROUP_OF_6 = 27.0
Z_90 = 1.645
CONFERENCE_COLORS = {
    "SEC": "#c41e3a",
    "Big Ten": "#003366",
    "Big 12": "#006747",
    "ACC": "#013ca6",
    "Pac-12": "#8c2332",
    "American": "#c8102e",
    "Mountain West": "#005eb8",
    "Sun Belt": "#f15a22",
    "MAC": "#006633",
    "CUSA": "#00a3e0",
    "FBS Indep.": "#555555",
    "Unknown": "#666666",
}
ESPN_TEAMS_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams"
)

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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cfb_conf_championship import compute_conf_results_by_sim
from cfb_playoff_odds import championship_odds_exact
from cfb_playoff_odds_calc import load_sim_fpi_by_team

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


def normalize_hex(raw: str | None) -> str | None:
    if not raw:
        return None
    h = str(raw).strip().lstrip("#").lower()
    if len(h) == 6 and all(c in "0123456789abcdef" for c in h):
        return f"#{h}"
    return None


def _fetch_json_url(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        proc = subprocess.run(
            ["curl", "-sS", "--compressed", "-H", "User-Agent: Mozilla/5.0", url],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or f"curl failed: {proc.returncode}")
        return json.loads(proc.stdout)


def fetch_espn_team_branding() -> dict[str, dict]:
    branding: dict[str, dict] = {}
    offset = 0
    limit = 1000
    while True:
        url = f"{ESPN_TEAMS_URL}?limit={limit}&offset={offset}"
        data = _fetch_json_url(url)
        teams = data["sports"][0]["leagues"][0].get("teams") or []
        if not teams:
            break
        for entry in teams:
            t = entry.get("team") or {}
            tid = str(t.get("id", "")).strip()
            if not tid:
                continue
            logos = t.get("logos") or []
            logo_url = logos[0].get("href") if logos else None
            branding[tid] = {
                "primary_color": normalize_hex(t.get("color")),
                "alternate_color": normalize_hex(t.get("alternateColor")),
                "logo_url": logo_url,
            }
        if len(teams) < limit:
            break
        offset += limit
    return branding


def enrich_teams_branding(teams: list[dict], branding: dict[str, dict]) -> None:
    for team in teams:
        tid = team["team_id"]
        conf = team.get("conference", "")
        fb = branding.get(tid, {})
        primary = fb.get("primary_color") or CONFERENCE_COLORS.get(conf, "#555555")
        alternate = fb.get("alternate_color") or primary
        team["primary_color"] = primary
        team["alternate_color"] = alternate
        team["logo_url"] = fb.get("logo_url")


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


def load_margin_lists(path: Path, sim_cols: list[str]) -> dict[tuple[str, str], list[float]]:
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
    return out


def load_avg_margins(margin_lists: dict[tuple[str, str], list[float]]) -> dict[tuple[str, str], float]:
    return {k: sum(v) / len(v) for k, v in margin_lists.items() if v}


def margin_histogram_labels(bucket_size: int = 5, min_edge: int = -50, max_edge: int = 50) -> list[str]:
    return [str(x) for x in range(min_edge, max_edge, bucket_size)]


def build_margin_histogram(
    margins: list[float],
    bucket_size: int = 5,
    min_edge: int = -50,
    max_edge: int = 50,
) -> dict[str, int]:
    top_bucket = str(max_edge - bucket_size)
    hist = {label: 0 for label in margin_histogram_labels(bucket_size, min_edge, max_edge)}
    for margin in margins:
        if margin < min_edge:
            key = str(min_edge)
        elif margin >= max_edge:
            key = top_bucket
        else:
            key = str(int(math.floor(margin / bucket_size) * bucket_size))
            if key not in hist:
                key = top_bucket if float(key) >= max_edge - bucket_size else str(min_edge)
        hist[key] = hist.get(key, 0) + 1
    return hist


def slugify_team_name(name: str) -> str:
    parts = re.sub(r"[^\w\s]", "", name.lower()).split()
    if len(parts) >= 4:
        parts = parts[:-2]
    elif len(parts) >= 2:
        parts = parts[:-1]
    slug = "-".join(parts)
    return slug or "team"


def build_game_slugs(games: dict[str, dict]) -> dict[str, dict]:
    slug_to_id: dict[str, str] = {}
    id_to_slug: dict[str, str] = {}
    for gid, game in sorted(
        games.items(),
        key=lambda item: (item[1].get("game_date", ""), item[1].get("week") or 0, item[0]),
    ):
        base = (
            f"{slugify_team_name(game['away_team_name'])}-at-"
            f"{slugify_team_name(game['home_team_name'])}"
        )
        slug = base
        if slug in slug_to_id and slug_to_id[slug] != gid:
            slug = f"{base}-week-{game.get('week') or 0}"
        if slug in slug_to_id and slug_to_id[slug] != gid:
            slug = f"{base}-{game.get('game_date', '')}"
        slug_to_id[slug] = gid
        id_to_slug[gid] = slug
    return {"slug_to_id": slug_to_id, "id_to_slug": id_to_slug}


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
        if conf == FBS_INDEP:
            conf_favorite_name = None
            conf_favorite_odds_pct = None
        else:
            conf_favorite = max(
                members,
                key=lambda m: lb_by_id.get(m["team_id"], {}).get("conf_champ_odds_pct", 0),
            )
            fav_lb = lb_by_id.get(conf_favorite["team_id"], {})
            conf_favorite_name = conf_favorite["team_name"]
            conf_favorite_odds_pct = fav_lb.get("conf_champ_odds_pct", 0)
        out.append(
            {
                "conference": conf,
                "team_count": len(members),
                "is_group_of_6": conf in GROUP_OF_6,
                "total_title_odds_pct": round(title_sum, 2),
                "total_conf_champ_odds_pct": round(conf_champ_sum, 2),
                "conf_favorite_name": conf_favorite_name,
                "conf_favorite_odds_pct": conf_favorite_odds_pct,
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


def build_games(
    schedule: list[dict],
    conf_by_id: dict[str, str],
    margin_lists: dict[tuple[str, str], list[float]],
) -> dict[str, dict]:
    """Per-game detail records keyed by game_id (home-team perspective)."""
    by_id: dict[str, dict] = {}
    for row in schedule:
        gid = row.get("game_id", "")
        if not gid:
            continue
        home_id = row.get("team_id", "")
        away_id = row.get("opponent_id", "")
        home_conf = conf_by_id.get(home_id, row.get("conference", ""))
        away_conf = conf_by_id.get(away_id, "")
        is_conf = (
            bool(home_conf)
            and bool(away_conf)
            and home_conf == away_conf
            and home_conf != FBS_INDEP
        )
        neutral = row.get("neutral_site")
        home_margins = margin_lists.get((gid, home_id), [])
        by_id[gid] = {
            "game_id": gid,
            "game_date": row.get("game_date", ""),
            "week": row.get("week"),
            "neutral_site": neutral in ("True", True, "true"),
            "home_team_id": home_id,
            "home_team_name": row.get("team_name", ""),
            "home_conference": home_conf,
            "home_fpi": row.get("team_fpi"),
            "away_team_id": away_id,
            "away_team_name": row.get("opponent_name", ""),
            "away_conference": away_conf,
            "away_fpi": row.get("opponent_fpi"),
            "home_win_pct": row.get("win_pct"),
            "avg_margin": row.get("avg_margin"),
            "is_conference_game": is_conf,
            "margin_histogram": build_margin_histogram(home_margins),
        }
    return by_id


def build_conf_championship(
    sim_cols: list[str],
    id_to_name: dict[str, str],
) -> dict:
    """Per-sim conference champions/finalists and team CCG summary."""
    results = compute_conf_results_by_sim(
        SOURCES["games_sim"],
        SOURCES["games_fpi"],
        SOURCES["conferences"],
    )
    conf_names = sorted(
        {
            conf
            for confs in results["champions"].values()
            for conf in confs
        }
    )
    champions_by_sim: list[dict[str, str]] = []
    finalists_by_sim: list[dict[str, list[str]]] = []
    for col in sim_cols:
        champions_by_sim.append(dict(results["champions"].get(col, {})))
        finals = results["finalists"].get(col, {})
        finalists_by_sim.append({k: list(v) for k, v in finals.items()})

    team_summary: dict[str, dict] = {}
    for tid, appearances in results["appearances"].items():
        team_summary[tid] = {
            "ccg_appearances": appearances,
            "ccg_wins": results["champ_wins"].get(tid, 0),
            "team_name": id_to_name.get(tid, tid),
        }

    return {
        "sim_count": len(sim_cols),
        "conferences": conf_names,
        "champions_by_sim": champions_by_sim,
        "finalists_by_sim": finalists_by_sim,
        "team_summary": team_summary,
    }


def build_brackets(
    eligibility: dict,
    sim_cols: list[str],
    name_to_id: dict[str, str],
    id_to_name: dict[str, str],
    leaderboard: list[dict],
) -> dict:
    """Per-sim playoff seeds/brackets and team-centric summary."""
    sim_fpi_by_col = load_sim_fpi_by_team(SOURCES["games_fpi"])
    title_odds = {r["team_id"]: r.get("title_odds_pct", 0) for r in leaderboard if r.get("team_id")}
    fields = eligibility.get("fields", [])
    by_sim: list[dict] = []
    seed_hist: dict[str, Counter] = {}
    seed_sums: dict[str, float] = {}
    seed_counts: dict[str, int] = {}
    r1_seeds = [[5, 12], [6, 11], [7, 10], [8, 9]]

    for sim_idx, col in enumerate(sim_cols):
        field_ids = fields[sim_idx] if sim_idx < len(fields) else []
        fpi_by_name = sim_fpi_by_col.get(col, {})
        rated: list[tuple[str, float]] = []
        for tid in field_ids:
            name = id_to_name.get(tid, "")
            if name in fpi_by_name:
                rated.append((tid, fpi_by_name[name]))
        rated.sort(key=lambda x: (-x[1], x[0]))
        seeds = [
            {
                "team_id": tid,
                "team_name": id_to_name.get(tid, tid),
                "seed": i + 1,
                "fpi": round(fpi, 2),
            }
            for i, (tid, fpi) in enumerate(rated[:12])
        ]
        for s in seeds:
            tid = s["team_id"]
            seed_hist.setdefault(tid, Counter())[str(s["seed"])] += 1
            seed_sums[tid] = seed_sums.get(tid, 0) + s["seed"]
            seed_counts[tid] = seed_counts.get(tid, 0) + 1

        title_by_team: dict[str, float] = {}
        if len(seeds) == 12:
            ratings = [0.0] * 12
            for s in seeds:
                ratings[s["seed"] - 1] = s["fpi"]
            odds = championship_odds_exact(ratings)
            for s in seeds:
                title_by_team[s["team_id"]] = round(odds[s["seed"] - 1] * 100, 2)

        by_sim.append({
            "seeds": seeds,
            "r1": r1_seeds,
            "title_odds_by_team_id": title_by_team,
        })

    team_summary: dict[str, dict] = {}
    for tid, hist in seed_hist.items():
        n = seed_counts[tid]
        team_summary[tid] = {
            "team_name": id_to_name.get(tid, tid),
            "avg_seed": round(seed_sums[tid] / n, 2) if n else None,
            "seed_histogram": dict(sorted(hist.items(), key=lambda x: int(x[0]))),
            "title_odds_pct": title_odds.get(tid, 0),
            "field_appearances": n,
        }

    return {
        "sim_count": len(sim_cols),
        "by_sim": by_sim,
        "team_summary": team_summary,
        "r1_pairings": r1_seeds,
    }


def write_sim_files(
    data_dir: Path,
    sim_count: int,
    fields: list[list[str]],
    brackets_by_sim: list[dict],
    champions_by_sim: list[dict[str, str]],
    finalists_by_sim: list[dict[str, list[str]]],
) -> int:
    """Write per-sim JSON files; return total bytes written."""
    sim_dir = data_dir / "sim"
    sim_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for sim_idx in range(sim_count):
        payload = {
            "field": fields[sim_idx] if sim_idx < len(fields) else [],
            "bracket": brackets_by_sim[sim_idx] if sim_idx < len(brackets_by_sim) else {},
            "conf_champions": champions_by_sim[sim_idx] if sim_idx < len(champions_by_sim) else {},
            "conf_finalists": finalists_by_sim[sim_idx] if sim_idx < len(finalists_by_sim) else {},
        }
        total += write_json(sim_dir / f"{sim_idx + 1:04d}.json", payload)
    return total


def main() -> int:
    for key, path in SOURCES.items():
        if not path.is_file():
            print(f"Missing source file ({key}): {path}", file=sys.stderr)
            return 1

    _, records_rows = read_csv(SOURCES["records"])
    sim_cols = sim_columns(list(records_rows[0].keys()) if records_rows else [])
    n_sims = len(sim_cols)

    teams = build_teams(records_rows, sim_cols)
    try:
        branding = fetch_espn_team_branding()
        enrich_teams_branding(teams, branding)
        print(f"Merged ESPN branding for {len(branding)} teams")
    except Exception as exc:
        print(f"Warning: could not fetch ESPN team branding: {exc}", file=sys.stderr)
        enrich_teams_branding(teams, {})
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
    margin_lists = load_margin_lists(SOURCES["games_fpi"], sim_cols)
    margin_map = load_avg_margins(margin_lists)
    schedule = build_schedule(game_rows, sim_cols, margin_map, conf_by_id)
    conferences = build_conferences(teams, leaderboard, eligibility["fields"], n_sims)
    games = build_games(schedule, conf_by_id, margin_lists)
    game_slugs = build_game_slugs(games)
    conf_championship = build_conf_championship(sim_cols, id_to_name)
    brackets = build_brackets(eligibility, sim_cols, name_to_id, id_to_name, leaderboard)

    brackets_summary = {
        "sim_count": n_sims,
        "r1_pairings": brackets["r1_pairings"],
        "team_summary": brackets["team_summary"],
    }
    conf_championship_summary = {
        "sim_count": conf_championship["sim_count"],
        "conferences": conf_championship["conferences"],
        "team_summary": conf_championship["team_summary"],
    }
    eligibility_slim = {"sim_count": n_sims}

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
        ("eligibility.json", eligibility_slim),
        ("field_analysis.json", field_analysis),
        ("schedule.json", schedule),
        ("conferences.json", conferences),
        ("games.json", games),
        ("game_slugs.json", game_slugs),
        ("brackets_summary.json", brackets_summary),
        ("conf_championship_summary.json", conf_championship_summary),
        ("last_year.json", build_last_year()),
    ]:
        sizes[name] = write_json(DATA_DIR / name, payload)

    sim_bytes = write_sim_files(
        DATA_DIR,
        n_sims,
        eligibility["fields"],
        brackets["by_sim"],
        conf_championship["champions_by_sim"],
        conf_championship["finalists_by_sim"],
    )
    sizes["sim/*.json"] = sim_bytes

    print(f"Exported to {DATA_DIR}")
    print(f"  Teams: {len(teams)}, Sims: {n_sims}, Games: {len(schedule)}")
    print(f"  Unique playoff fields: {field_analysis['unique_field_count']}")
    for name, nbytes in sizes.items():
        print(f"  {name}: {nbytes / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
