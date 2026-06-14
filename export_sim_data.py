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


def is_fbs_team(team: dict) -> bool:
    return is_fbs_conference(team.get("conference", ""))


def summary_word_count(text: str) -> int:
    return len(text.split())


def truncate_summary(text: str, max_words: int = 100) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    trimmed = " ".join(words[:max_words]).rstrip(".,;")
    return f"{trimmed}."


def classify_team_tier(title_odds: float, eligibility: float, avg_wins: float) -> str:
    if title_odds >= 5:
        return "national title contender"
    if title_odds >= 2:
        return "title threat"
    if eligibility >= 40:
        return "playoff regular"
    if eligibility >= 20:
        return "fringe playoff team"
    if eligibility >= 8:
        return "occasional playoff candidate"
    if avg_wins >= 8:
        return "winning program"
    if avg_wins >= 6:
        return "middle-of-the-pack team"
    if avg_wins >= 4:
        return "rebuilding squad"
    return "long-shot program"


def short_opponent_name(full: str) -> str:
    tokens = full.split()
    if len(tokens) <= 2:
        return full
    return " ".join(tokens[:-1])


def team_schedule_from_games(games: dict[str, dict], team_id: str) -> list[dict]:
    tid = str(team_id)
    rows: list[dict] = []
    for game in games.values():
        home_wp = game.get("home_win_pct")
        if home_wp is None:
            continue
        try:
            home_wp_f = float(home_wp)
        except (TypeError, ValueError):
            continue
        neutral = bool(game.get("neutral_site"))
        if game.get("home_team_id") == tid:
            rows.append(
                {
                    "opponent_name": game.get("away_team_name", ""),
                    "win_pct": home_wp_f,
                    "home_away": "neutral" if neutral else "home",
                }
            )
        elif game.get("away_team_id") == tid:
            rows.append(
                {
                    "opponent_name": game.get("home_team_name", ""),
                    "win_pct": round(100 - home_wp_f, 1),
                    "home_away": "neutral" if neutral else "away",
                }
            )
    return rows


def format_swing_game(game: dict) -> str:
    site = "vs" if game["home_away"] == "home" else ("@" if game["home_away"] == "away" else "n")
    opp = short_opponent_name(game["opponent_name"])
    wp = game["win_pct"]
    wp_str = str(int(wp)) if wp == int(wp) else f"{wp:.1f}".rstrip("0").rstrip(".")
    return f"{site} {opp} ({wp_str}%)"


def conference_context(
    team_id: str, conferences: list[dict]
) -> tuple[int, int, str | None, float | None]:
    for conf in conferences:
        members = conf.get("teams", [])
        for rank, member in enumerate(members, start=1):
            if member.get("team_id") == team_id:
                return (
                    rank,
                    len(members),
                    conf.get("conf_favorite_name"),
                    member.get("conf_champ_odds_pct"),
                )
    return 0, 0, None, None


def tier_article(tier: str) -> str:
    return "an" if tier[:1].lower() in "aeiou" else "a"


def build_team_summary_text(
    team_name: str,
    conference: str,
    season_year: int,
    sim_count: int,
    avg_wins: float,
    title_odds: float,
    eligibility: float,
    conf_champ_odds: float,
    conf_rank: int,
    conf_size: int,
    conf_favorite: str | None,
    swing_games: list[dict],
) -> str:
    tier = classify_team_tier(title_odds, eligibility, avg_wins)
    opener = f"The {team_name} are {tier_article(tier)} {tier} in the {season_year} preseason model."

    if conference == FBS_INDEP:
        conf_sentence = f"They compete as an FBS independent."
    elif conf_rank == 1 and conf_favorite:
        conf_sentence = (
            f"They are the {conference} favorite at {conf_champ_odds:.1f}% conference title odds."
        )
    elif conf_rank > 0:
        conf_sentence = (
            f"In the {conference}, they rank #{conf_rank} of {conf_size} "
            f"by conference title odds ({conf_champ_odds:.1f}%)."
        )
    else:
        conf_sentence = f"They compete in the {conference}."

    stats_sentence = (
        f"Across {sim_count} simulations, they average {avg_wins:.2f} wins with "
        f"{eligibility:.1f}% playoff odds and {title_odds:.2f}% national title odds."
    )

    parts = [opener, conf_sentence, stats_sentence]
    swing_labels = [format_swing_game(g) for g in swing_games]
    while swing_labels:
        swing_sentence = f"Pivot games: {', '.join(swing_labels)}."
        candidate = " ".join([*parts, swing_sentence])
        if summary_word_count(candidate) <= 100:
            return candidate
        swing_labels = swing_labels[:-1]

    return truncate_summary(" ".join(parts), 100)


def build_team_summaries(
    teams: list[dict],
    leaderboard: list[dict],
    conferences: list[dict],
    games: dict[str, dict],
    sim_count: int,
    season_year: int,
) -> dict:
    lb_by_id = {r["team_id"]: r for r in leaderboard if r.get("team_id")}
    summaries: dict[str, dict] = {}

    for team in teams:
        if not is_fbs_team(team):
            continue
        tid = team["team_id"]
        lb = lb_by_id.get(tid, {})
        avg_wins = float(team.get("avg_wins") or lb.get("avg_wins") or 0)
        title_odds = float(lb.get("title_odds_pct") or 0)
        eligibility = float(lb.get("eligibility_pct") or 0)
        conf_champ_odds = float(lb.get("conf_champ_odds_pct") or 0)
        conf_rank, conf_size, conf_favorite, _ = conference_context(tid, conferences)

        schedule = team_schedule_from_games(games, tid)
        schedule.sort(key=lambda g: abs(g["win_pct"] - 50))
        swing_games = schedule[:3]

        summary = build_team_summary_text(
            team_name=team["team_name"],
            conference=team.get("conference", ""),
            season_year=season_year,
            sim_count=sim_count,
            avg_wins=avg_wins,
            title_odds=title_odds,
            eligibility=eligibility,
            conf_champ_odds=conf_champ_odds,
            conf_rank=conf_rank,
            conf_size=conf_size,
            conf_favorite=conf_favorite,
            swing_games=swing_games,
        )

        summaries[tid] = {
            "team_id": tid,
            "team_name": team["team_name"],
            "word_count": summary_word_count(summary),
            "summary": summary,
        }

    return {
        "sim_count": sim_count,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "summaries": summaries,
    }


CONF_SUMMARY_MAX_WORDS = 200


def format_conf_contender(team_name: str, pct: float) -> str:
    return f"{short_opponent_name(team_name)} ({pct:.1f}%)"


def build_conference_summary_text(
    conference: str,
    season_year: int,
    sim_count: int,
    conf_row: dict,
    deep: dict,
) -> str:
    is_g6 = bool(conf_row.get("is_group_of_6"))
    total_title = float(conf_row.get("total_title_odds_pct") or 0)
    sims_member_pct = float(conf_row.get("sims_with_member_pct") or 0)
    favorite_name = conf_row.get("conf_favorite_name")
    favorite_odds = float(conf_row.get("conf_favorite_odds_pct") or 0)
    teams = list(conf_row.get("teams") or [])
    teams_sorted = sorted(teams, key=lambda t: -float(t.get("conf_champ_odds_pct") or 0))

    playoff_share = float(deep.get("playoff_share_pct") or 0)
    title_top3_share = deep.get("title_top3_share_pct")
    avg_sos = deep.get("avg_conf_opponent_fpi")
    top_title = max(teams, key=lambda t: float(t.get("title_odds_pct") or 0)) if teams else None

    if is_g6:
        identity = (
            f"The {conference} is a Group of 6 league in the {season_year} preseason model, "
            f"with a member in the playoff field in {sims_member_pct:.1f}% of {sim_count} simulations."
        )
        if total_title >= 0.05:
            identity += f" The league accounts for {total_title:.2f}% of national title equity."
    else:
        identity = (
            f"The {conference} is a Power Four league that carries {total_title:.2f}% of national "
            f"title equity across {sim_count} simulations."
        )
        if playoff_share:
            share_note = "a dominant share" if playoff_share >= 25 else "a meaningful slice"
            identity += (
                f" Members fill {playoff_share:.2f}% of all playoff field slots — "
                f"{share_note} of the 12-team bracket."
            )

    if favorite_name and favorite_odds:
        fav_short = short_opponent_name(favorite_name)
        fav_row = next((t for t in teams if t.get("team_name") == favorite_name), None)
        fav_avg_wins = float(fav_row.get("avg_wins") or 0) if fav_row else 0
        fav_playoff = float(fav_row.get("eligibility_pct") or 0) if fav_row else 0

        if favorite_odds >= 30:
            race_open = f"{fav_short} is a heavy favorite at {favorite_odds:.1f}% conference title odds."
        elif favorite_odds < 25:
            race_open = (
                f"The league championship race is wide open: {fav_short} leads at "
                f"{favorite_odds:.1f}%, but no team clears a quarter of the sims."
            )
        else:
            race_open = f"{fav_short} leads the {conference} at {favorite_odds:.1f}% conference title odds."

        challengers = [t for t in teams_sorted if t.get("team_name") != favorite_name][:3]
        challenger_strs = [
            format_conf_contender(t["team_name"], float(t.get("conf_champ_odds_pct") or 0))
            for t in challengers
            if float(t.get("conf_champ_odds_pct") or 0) >= 2
        ]
        race_sentence = (
            f"{race_open} The next tier includes {', '.join(challenger_strs)}."
            if challenger_strs
            else race_open
        )
        if fav_row and fav_avg_wins:
            race_sentence += (
                f" {fav_short} averages {fav_avg_wins:.2f} wins and reaches the playoff "
                f"field in {fav_playoff:.1f}% of simulations."
            )
    else:
        race_sentence = f"The {conference} has no clear conference favorite in the model."

    national = ""
    if top_title and float(top_title.get("title_odds_pct") or 0) > 0:
        nt_short = short_opponent_name(top_title["team_name"])
        nt_odds = float(top_title["title_odds_pct"])
        nt_elig = float(top_title.get("eligibility_pct") or 0)
        national = (
            f"{nt_short} is the league's top national title threat at {nt_odds:.2f}% championship odds"
        )
        if title_top3_share is not None and not is_g6:
            national += (
                f", and the top three teams hold {title_top3_share:.1f}% "
                f"of the conference's title equity."
            )
        else:
            national += "."
        if is_g6 and nt_elig >= 5:
            national += (
                f" That {nt_elig:.1f}% playoff rate is what makes the {conference} "
                f"a live Group of 6 autobid path in this model."
            )

    title_threats = [
        t for t in teams if float(t.get("title_odds_pct") or 0) >= 1.0
    ]
    optional: list[str] = []
    if not is_g6 and len(title_threats) >= 2:
        threat_names = [
            format_conf_contender(t["team_name"], float(t.get("title_odds_pct") or 0))
            for t in sorted(title_threats, key=lambda t: -float(t.get("title_odds_pct") or 0))[:4]
        ]
        optional.append(
            f"{len(title_threats)} programs carry at least 1% national title equity, "
            f"led by {', '.join(threat_names)}."
        )

    if is_g6:
        top_playoff = max(teams, key=lambda t: float(t.get("eligibility_pct") or 0)) if teams else None
        if top_playoff:
            tp_short = short_opponent_name(top_playoff["team_name"])
            tp_elig = float(top_playoff.get("eligibility_pct") or 0)
            if tp_elig >= 3:
                optional.append(
                    f"When a {conference} team earns an autobid, it is usually {tp_short} "
                    f"({tp_elig:.1f}% playoff field rate)."
                )
    if not is_g6 and avg_sos is not None:
        optional.append(
            f"Conference play is demanding: average opponent rating in league games is "
            f"{avg_sos:+.1f} in the model."
        )

    if teams:
        bottom = min(teams, key=lambda t: float(t.get("avg_wins") or 999))
        bottom_wins = float(bottom.get("avg_wins") or 0)
        if bottom_wins < 5:
            optional.append(
                f"{short_opponent_name(bottom['team_name'])} brings up the rear "
                f"at {bottom_wins:.2f} average wins."
            )

    parts = [identity, race_sentence]
    if national:
        parts.append(national)

    while optional:
        candidate = " ".join([*parts, *optional])
        if summary_word_count(candidate) <= CONF_SUMMARY_MAX_WORDS:
            return candidate
        optional = optional[:-1]

    result = " ".join(parts)
    if summary_word_count(result) > CONF_SUMMARY_MAX_WORDS:
        return truncate_summary(result, CONF_SUMMARY_MAX_WORDS)
    return result


def build_conference_summaries(
    conferences: list[dict],
    conference_deep: dict[str, dict],
    sim_count: int,
    season_year: int,
) -> dict:
    summaries: dict[str, dict] = {}
    for conf_row in conferences:
        conf = conf_row.get("conference", "")
        if conf == FBS_INDEP:
            continue
        deep = conference_deep.get(conf, {})
        summary = build_conference_summary_text(
            conference=conf,
            season_year=season_year,
            sim_count=sim_count,
            conf_row=conf_row,
            deep=deep,
        )
        summaries[conf] = {
            "conference": conf,
            "word_count": summary_word_count(summary),
            "summary": summary,
        }

    return {
        "sim_count": sim_count,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "summaries": summaries,
    }


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


def build_conference_deep(
    conferences: list[dict],
    champions_by_sim: list[dict[str, str]],
    finalists_by_sim: list[dict[str, list[str]]],
    fields: list[list[str]],
    conf_by_id: dict[str, str],
    id_to_name: dict[str, str],
    n_sims: int,
    schedule: list[dict],
) -> dict[str, dict]:
    """Per-conference rollups for detail pages and charts."""
    conf_sos: dict[str, list[float]] = {}
    for row in schedule:
        tid = row.get("team_id", "")
        oid = row.get("opponent_id", "")
        t_conf = conf_by_id.get(tid, "")
        o_conf = conf_by_id.get(oid, "")
        if not t_conf or t_conf != o_conf or t_conf == FBS_INDEP:
            continue
        opp_fpi = row.get("opponent_fpi")
        if opp_fpi is not None:
            conf_sos.setdefault(t_conf, []).append(float(opp_fpi))

    deep: dict[str, dict] = {}
    for conf_row in conferences:
        conf = conf_row["conference"]
        if conf == FBS_INDEP:
            continue
        team_ids = {t["team_id"] for t in conf_row["teams"]}
        champ_counts: Counter[str] = Counter()
        finalist_counts: Counter[str] = Counter()
        playoff_per_sim: Counter[int] = Counter()

        for sim_champs in champions_by_sim:
            champ_id = sim_champs.get(conf)
            if champ_id:
                champ_counts[champ_id] += 1

        for sim_finals in finalists_by_sim:
            for tid in sim_finals.get(conf, []):
                finalist_counts[tid] += 1

        for field in fields:
            n_in_conf = sum(1 for tid in field if tid in team_ids)
            playoff_per_sim[n_in_conf] += 1

        def team_chart(counter: Counter[str]) -> list[dict]:
            items = []
            for tid, count in counter.most_common():
                items.append(
                    {
                        "team_id": tid,
                        "team_name": id_to_name.get(tid, tid),
                        "count": count,
                        "pct": round(count / n_sims * 100, 2) if n_sims else 0,
                    }
                )
            return items

        title_pcts = [t.get("title_odds_pct", 0) for t in conf_row["teams"]]
        title_total = sum(title_pcts)
        top3 = sum(sorted(title_pcts, reverse=True)[:3])
        sos_vals = conf_sos.get(conf, [])
        field_apps = sum(t.get("playoff_appearances", 0) for t in conf_row["teams"])
        member_slots = len(team_ids) * n_sims if n_sims else 1

        deep[conf] = {
            "champion_chart": team_chart(champ_counts),
            "finalist_chart": team_chart(finalist_counts),
            "playoff_teams_per_sim": {
                str(k): playoff_per_sim[k] for k in sorted(playoff_per_sim.keys())
            },
            "playoff_share_pct": round(field_apps / member_slots * 100, 2),
            "title_top3_share_pct": round(top3 / title_total * 100, 1) if title_total else 0,
            "avg_conf_opponent_fpi": round(sum(sos_vals) / len(sos_vals), 2) if sos_vals else None,
        }
    return deep


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
    conference_deep = build_conference_deep(
        conferences,
        conf_championship["champions_by_sim"],
        conf_championship["finalists_by_sim"],
        eligibility["fields"],
        conf_by_id,
        id_to_name,
        n_sims,
        schedule,
    )

    season_year = 2026
    if game_rows and game_rows[0].get("season_year"):
        try:
            season_year = int(game_rows[0]["season_year"])
        except ValueError:
            pass

    team_summaries = build_team_summaries(
        teams,
        leaderboard,
        conferences,
        games,
        n_sims,
        season_year,
    )
    conference_summaries = build_conference_summaries(
        conferences,
        conference_deep,
        n_sims,
        season_year,
    )

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
        ("conference_deep.json", conference_deep),
        ("games.json", games),
        ("game_slugs.json", game_slugs),
        ("brackets_summary.json", brackets_summary),
        ("conf_championship_summary.json", conf_championship_summary),
        ("team_summaries.json", team_summaries),
        ("conference_summaries.json", conference_summaries),
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
