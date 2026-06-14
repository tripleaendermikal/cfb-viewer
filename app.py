"""CFB Simulation Viewer — local Flask app over pre-exported JSON."""

from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, abort, render_template, request, url_for

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"

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


class DataStore:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._sim_cache: dict[int, dict] = {}

        self.meta = self._load(data_dir / "meta.json")
        self.leaderboard = self._load(data_dir / "leaderboard.json")
        self.teams = self._load(data_dir / "teams.json")
        self.eligibility = self._load(data_dir / "eligibility.json")
        self.field_analysis = self._load(data_dir / "field_analysis.json")
        self.conferences = [
            c for c in self._load(data_dir / "conferences.json")
            if is_fbs_conference(c.get("conference", ""))
        ]

        self._schedule: list[dict] | None = None
        self._games: dict | None = None
        self._last_year: dict | None = None
        self._brackets_summary: dict | None = None
        self._conf_championship_summary: dict | None = None
        self._legacy_brackets: dict | None = None
        self._legacy_conf_championship: dict | None = None
        self._legacy_eligibility_fields: list | None = None

        self.teams_by_id = {t["team_id"]: t for t in self.teams}
        self.fbs_leaderboard = [
            r for r in self.leaderboard if is_fbs_conference(r.get("conference", ""))
        ]
        for row in self.leaderboard:
            tid = row.get("team_id")
            if tid and tid in self.teams_by_id:
                row["avg_wins"] = self.teams_by_id[tid]["avg_wins"]
        self.lb_by_id = {r["team_id"]: r for r in self.leaderboard}
        self.fbs_teams = sorted(
            [t for t in self.teams if is_fbs_team(t)],
            key=lambda t: t["team_name"].lower(),
        )
        self.conference_names = sorted(
            {r["conference"] for r in self.leaderboard if is_fbs_conference(r.get("conference", ""))}
        )

    @property
    def sim_count(self) -> int:
        return self.eligibility.get("sim_count", self.meta.get("sim_count", 1000))

    @property
    def has_sim_files(self) -> bool:
        return (self._data_dir / "sim" / "0001.json").is_file()

    @staticmethod
    def _load(path: Path):
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def _load_optional(self, path: Path) -> dict | list | None:
        if not path.is_file():
            return None
        return self._load(path)

    @property
    def schedule(self) -> list[dict]:
        if self._schedule is None:
            raw = self._load(self._data_dir / "schedule.json")
            self._schedule = self._dedupe_schedule(raw)
        return self._schedule

    @property
    def games(self) -> dict:
        if self._games is None:
            data = self._load_optional(self._data_dir / "games.json")
            self._games = data if isinstance(data, dict) else {}
        return self._games

    @property
    def last_year(self) -> dict:
        if self._last_year is None:
            data = self._load_optional(self._data_dir / "last_year.json")
            self._last_year = data if isinstance(data, dict) else {}
        return self._last_year

    @property
    def brackets_summary(self) -> dict:
        if self._brackets_summary is None:
            data = self._load_optional(self._data_dir / "brackets_summary.json")
            if data:
                self._brackets_summary = data
            else:
                self._brackets_summary = self._legacy_brackets_data()
        return self._brackets_summary

    @property
    def conf_championship_summary(self) -> dict:
        if self._conf_championship_summary is None:
            data = self._load_optional(self._data_dir / "conf_championship_summary.json")
            if data:
                self._conf_championship_summary = data
            else:
                self._conf_championship_summary = self._legacy_conf_championship_data()
        return self._conf_championship_summary

    def _legacy_brackets_data(self) -> dict:
        if self._legacy_brackets is None:
            data = self._load_optional(self._data_dir / "brackets.json")
            self._legacy_brackets = data if isinstance(data, dict) else {}
        full = self._legacy_brackets
        return {
            "sim_count": full.get("sim_count", self.sim_count),
            "r1_pairings": full.get("r1_pairings", [[5, 12], [6, 11], [7, 10], [8, 9]]),
            "team_summary": full.get("team_summary", {}),
            "by_sim": full.get("by_sim", []),
        }

    def _legacy_conf_championship_data(self) -> dict:
        if self._legacy_conf_championship is None:
            data = self._load_optional(self._data_dir / "conf_championship.json")
            self._legacy_conf_championship = data if isinstance(data, dict) else {}
        full = self._legacy_conf_championship
        return {
            "sim_count": full.get("sim_count", self.sim_count),
            "conferences": full.get("conferences", []),
            "team_summary": full.get("team_summary", {}),
            "champions_by_sim": full.get("champions_by_sim", []),
            "finalists_by_sim": full.get("finalists_by_sim", []),
        }

    def _legacy_eligibility_fields(self) -> list:
        if self._legacy_eligibility_fields is None:
            elig_path = self._data_dir / "eligibility.json"
            if elig_path.is_file():
                data = self._load(elig_path)
                self._legacy_eligibility_fields = data.get("fields", [])
            else:
                self._legacy_eligibility_fields = []
        return self._legacy_eligibility_fields

    @staticmethod
    def _dedupe_schedule(schedule: list[dict]) -> list[dict]:
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

    def sim_data_at(self, sim_index: int) -> dict | None:
        """0-based sim index."""
        if sim_index in self._sim_cache:
            return self._sim_cache[sim_index]

        if self.has_sim_files:
            path = self._data_dir / "sim" / f"{sim_index + 1:04d}.json"
            if path.is_file():
                data = self._load(path)
                self._sim_cache[sim_index] = data
                return data
            return None

        legacy_brackets = self.brackets_summary.get("by_sim", [])
        legacy_champs = self.conf_championship_summary.get("champions_by_sim", [])
        legacy_finalists = self.conf_championship_summary.get("finalists_by_sim", [])
        fields = self._legacy_eligibility_fields()
        if sim_index < 0 or sim_index >= self.sim_count:
            return None
        data = {
            "field": fields[sim_index] if sim_index < len(fields) else [],
            "bracket": legacy_brackets[sim_index] if sim_index < len(legacy_brackets) else {},
            "conf_champions": legacy_champs[sim_index] if sim_index < len(legacy_champs) else {},
            "conf_finalists": legacy_finalists[sim_index] if sim_index < len(legacy_finalists) else {},
        }
        self._sim_cache[sim_index] = data
        return data

    def team_name(self, team_id: str) -> str:
        t = self.teams_by_id.get(str(team_id))
        if t:
            return t["team_name"]
        lb = self.lb_by_id.get(str(team_id))
        if lb:
            return lb["team_name"]
        return str(team_id)

    def conference_for(self, team_id: str) -> str:
        t = self.teams_by_id.get(str(team_id))
        return (t or {}).get("conference", "")

    def field_at(self, sim_index: int) -> list[str] | None:
        data = self.sim_data_at(sim_index)
        if data:
            return data.get("field")
        return None

    def game_at(self, game_id: str) -> dict | None:
        return self.games.get(str(game_id))

    def team_schedule(self, team_id: str) -> list[dict]:
        """This team's games with win odds and home/away from their perspective."""
        tid = str(team_id)
        rows: list[dict] = []
        for g in self.games.values():
            gid = g.get("game_id", "")
            neutral = g.get("neutral_site", False)
            home_wp = g.get("home_win_pct")
            margin = g.get("avg_margin")
            if g.get("home_team_id") == tid:
                rows.append(
                    {
                        "game_id": gid,
                        "game_date": g.get("game_date", ""),
                        "week": g.get("week"),
                        "opponent_id": g.get("away_team_id", ""),
                        "opponent_name": g.get("away_team_name", ""),
                        "win_pct": home_wp,
                        "avg_margin": margin,
                        "home_away": "neutral" if neutral else "home",
                        "is_conference_game": g.get("is_conference_game", False),
                    }
                )
            elif g.get("away_team_id") == tid:
                away_wp = round(100 - float(home_wp), 2) if home_wp is not None else None
                away_margin = round(-float(margin), 3) if margin is not None else None
                rows.append(
                    {
                        "game_id": gid,
                        "game_date": g.get("game_date", ""),
                        "week": g.get("week"),
                        "opponent_id": g.get("home_team_id", ""),
                        "opponent_name": g.get("home_team_name", ""),
                        "win_pct": away_wp,
                        "avg_margin": away_margin,
                        "home_away": "neutral" if neutral else "away",
                        "is_conference_game": g.get("is_conference_game", False),
                    }
                )
        return sorted(
            rows,
            key=lambda r: (r.get("game_date", ""), r.get("week") or 0, r.get("game_id", "")),
        )

    def conf_champs_at(self, sim_index: int) -> dict[str, dict] | None:
        data = self.sim_data_at(sim_index)
        if not data:
            return None
        row = data.get("conf_champions", {})
        return {
            conf: {
                "team_id": tid,
                "team_name": self.team_name(tid),
            }
            for conf, tid in row.items()
        }

    def bracket_at(self, sim_index: int) -> dict | None:
        data = self.sim_data_at(sim_index)
        if not data:
            return None
        bracket = data.get("bracket")
        return bracket if bracket else None


def is_fbs_conference(conf: str) -> bool:
    return bool(conf) and conf not in FCS_CONFERENCES and conf != "Unknown"


def is_fbs_team(team: dict) -> bool:
    return is_fbs_conference(team.get("conference", ""))


def create_app() -> Flask:
    app = Flask(__name__)
    store = DataStore(DATA_DIR)

    @app.context_processor
    def inject_globals():
        return {
            "meta": store.meta,
            "conference_color": CONFERENCE_COLORS,
            "team_search": [
                {"id": t["team_id"], "name": t["team_name"]}
                for t in store.fbs_teams
            ],
        }

    @app.errorhandler(404)
    def not_found(e):
        team_suggestion = None
        if request.path.startswith("/team/"):
            bad_id = request.path.split("/team/", 1)[-1].split("/", 1)[0]
            for t in store.fbs_teams:
                if bad_id.lower() in t["team_name"].lower() or bad_id in t["team_id"]:
                    team_suggestion = t
                    break
        return render_template("404.html", team_suggestion=team_suggestion), 404

    @app.template_filter("conf_color")
    def conf_color(conf: str) -> str:
        return CONFERENCE_COLORS.get(conf or "", "#555555")

    @app.template_filter("pct")
    def fmt_pct(val) -> str:
        if val is None:
            return "—"
        return f"{float(val):.1f}%"

    @app.route("/methodology")
    def methodology():
        return render_template("methodology.html", active="methodology")

    @app.route("/game/<game_id>")
    def game_detail(game_id: str):
        game = store.game_at(game_id)
        if not game:
            abort(404)
        compare_url = url_for(
            "compare",
            teams=f"{game['home_team_id']},{game['away_team_id']}",
        )
        return render_template(
            "game.html",
            game=game,
            compare_url=compare_url,
            active="schedule",
        )

    @app.route("/bracket")
    def bracket():
        sim_raw = request.args.get("sim", "").strip()
        team_raw = request.args.get("team", "").strip()
        sim_index = None
        sim_bracket = None
        sim_error = None
        team_bracket = None

        if sim_raw:
            try:
                sim_index = int(sim_raw)
                n = store.sim_count
                if sim_index < 1 or sim_index > n:
                    sim_error = f"Sim index must be 1–{n}"
                else:
                    sim_bracket = store.bracket_at(sim_index - 1)
                    if not sim_bracket:
                        sim_error = "Bracket data not available for this sim"
            except ValueError:
                sim_error = "Invalid sim index"

        if team_raw:
            summary = store.brackets_summary.get("team_summary", {}).get(team_raw)
            lb = store.lb_by_id.get(team_raw, {})
            if summary or lb:
                team_bracket = summary or {}
                team_bracket.setdefault("team_name", store.team_name(team_raw))
                team_bracket.setdefault("title_odds_pct", lb.get("title_odds_pct", 0))

        team_options = store.fbs_teams
        return render_template(
            "bracket.html",
            sim_index=sim_index,
            sim_bracket=sim_bracket,
            sim_error=sim_error,
            team_filter=team_raw,
            team_bracket=team_bracket,
            team_options=team_options,
            r1_pairings=store.brackets_summary.get(
                "r1_pairings", [[5, 12], [6, 11], [7, 10], [8, 9]]
            ),
            active="bracket",
        )

    @app.route("/")
    def leaderboard():
        conf_filter = request.args.get("conference", "").strip()
        rows = sorted(
            store.fbs_leaderboard,
            key=lambda r: (
                r.get("baseline_fpi") is None,
                -(r.get("baseline_fpi") if r.get("baseline_fpi") is not None else -999),
                r.get("team_name", "").lower(),
            ),
        )
        if conf_filter:
            rows = [r for r in rows if r.get("conference") == conf_filter]
        return render_template(
            "leaderboard.html",
            rows=rows,
            conferences=store.conference_names,
            conf_filter=conf_filter,
            active="leaderboard",
        )

    @app.route("/team/<team_id>")
    def team_detail(team_id: str):
        team = store.teams_by_id.get(team_id)
        lb = store.lb_by_id.get(team_id)
        if not team and not lb:
            abort(404)
        team = team or {}
        lb = lb or {}
        merged = {
            "team_id": team_id,
            "team_name": team.get("team_name") or lb.get("team_name", team_id),
            "conference": team.get("conference") or lb.get("conference", ""),
            "avg_wins": team.get("avg_wins"),
            "win_histogram": team.get("win_histogram", {}),
            "title_odds_pct": lb.get("title_odds_pct", 0),
            "conf_champ_odds_pct": lb.get("conf_champ_odds_pct", 0),
            "eligibility_pct": lb.get("eligibility_pct", 0),
            "conf_champ_appearances": lb.get("conf_champ_appearances", 0),
        }
        ccg = store.conf_championship_summary.get("team_summary", {}).get(team_id, {})
        merged["ccg_appearances"] = ccg.get("ccg_appearances", merged["conf_champ_appearances"])
        merged["ccg_wins"] = ccg.get("ccg_wins", 0)
        bracket_summary = store.brackets_summary.get("team_summary", {}).get(team_id)
        team_schedule = store.team_schedule(team_id)
        hist_labels = [str(i) for i in range(13)]
        hist_data = [merged["win_histogram"].get(str(i), 0) for i in range(13)]
        return render_template(
            "team.html",
            team=merged,
            bracket_summary=bracket_summary,
            team_schedule=team_schedule,
            hist_labels=hist_labels,
            hist_data=hist_data,
            active="team",
        )

    @app.route("/compare")
    def compare():
        ids = [x.strip() for x in request.args.getlist("teams") if x.strip()][:4]
        if not ids:
            raw = request.args.get("teams", "")
            ids = [x.strip() for x in raw.split(",") if x.strip()][:4]
        selected = []
        chart_datasets = []
        invalid_ids = []
        colors = ["#3d8bfd", "#3dd68c", "#f5a524", "#ff6b9d"]
        for i, tid in enumerate(ids):
            team = store.teams_by_id.get(tid)
            lb = store.lb_by_id.get(tid, {})
            if not team or not is_fbs_team(team):
                invalid_ids.append(tid)
                continue
            hist = team.get("win_histogram", {})
            selected.append(
                {
                    "team_id": tid,
                    "team_name": team["team_name"],
                    "conference": team.get("conference", ""),
                    "avg_wins": team.get("avg_wins"),
                    "title_odds_pct": lb.get("title_odds_pct", 0),
                    "conf_champ_odds_pct": lb.get("conf_champ_odds_pct", 0),
                    "eligibility_pct": lb.get("eligibility_pct", 0),
                }
            )
            chart_datasets.append(
                {
                    "label": team["team_name"],
                    "data": [hist.get(str(w), 0) for w in range(13)],
                    "backgroundColor": colors[(len(selected) - 1) % len(colors)] + "99",
                    "borderColor": colors[(len(selected) - 1) % len(colors)],
                    "borderWidth": 1,
                }
            )
        team_options = store.fbs_teams
        return render_template(
            "compare.html",
            selected=selected,
            selected_ids=ids,
            invalid_ids=invalid_ids,
            chart_datasets=chart_datasets,
            team_options=team_options,
            active="compare",
        )

    @app.route("/fields")
    def fields():
        sim_raw = request.args.get("sim", "").strip()
        sim_index = None
        sim_field = None
        sim_error = None
        sim_conf_champs = None
        if sim_raw:
            try:
                sim_index = int(sim_raw)
                if sim_index < 1 or sim_index > store.sim_count:
                    sim_error = f"Sim index must be 1–{store.sim_count}"
                else:
                    ids = store.field_at(sim_index - 1)
                    if ids:
                        sim_field = [
                            {
                                "team_id": tid,
                                "team_name": store.team_name(tid),
                                "conference": store.conference_for(tid),
                            }
                            for tid in ids
                        ]
                    sim_conf_champs = store.conf_champs_at(sim_index - 1)
            except ValueError:
                sim_error = "Invalid sim index"
        return render_template(
            "fields.html",
            analysis=store.field_analysis,
            sim_index=sim_index,
            sim_field=sim_field,
            sim_conf_champs=sim_conf_champs,
            sim_error=sim_error,
            active="fields",
        )

    @app.route("/schedule")
    def schedule():
        team_filter = request.args.get("team", "").strip()
        conf_filter = request.args.get("conference", "").strip()
        week_filter = request.args.get("week", "").strip()

        rows = store.schedule
        if team_filter:
            rows = [g for g in rows if g["team_id"] == team_filter or g["opponent_id"] == team_filter]
        if conf_filter:
            rows = [
                g
                for g in rows
                if g.get("conference") == conf_filter
                or store.conference_for(g.get("opponent_id", "")) == conf_filter
            ]
        if week_filter:
            try:
                wk = int(week_filter)
                rows = [g for g in rows if g.get("week") == wk]
            except ValueError:
                pass

        team_options = store.fbs_teams
        weeks = sorted({g["week"] for g in store.schedule if g.get("week") is not None})
        return render_template(
            "schedule.html",
            games=rows,
            team_filter=team_filter,
            conf_filter=conf_filter,
            week_filter=week_filter,
            team_options=team_options,
            conferences=store.conference_names,
            weeks=weeks,
            active="schedule",
        )

    @app.route("/conferences")
    def conferences():
        conf_name = request.args.get("conference", "").strip()
        if conf_name:
            conf = next((c for c in store.conferences if c["conference"] == conf_name), None)
            if not conf:
                abort(404)
            return render_template(
                "conference_detail.html",
                conf=conf,
                active="conferences",
            )
        return render_template(
            "conferences.html",
            conferences=store.conferences,
            active="conferences",
        )

    @app.route("/last-year")
    def last_year():
        ly = store.last_year
        teams = ly.get("teams", [])
        games = ly.get("games", [])
        conf_filter = request.args.get("conference", "").strip()

        if conf_filter:
            teams = [t for t in teams if t.get("conference") == conf_filter]
            games = [
                g
                for g in games
                if g.get("home_conference") == conf_filter or g.get("away_conference") == conf_filter
            ]

        conferences = sorted(
            {t["conference"] for t in ly.get("teams", []) if is_fbs_conference(t.get("conference", ""))}
        )
        return render_template(
            "last_year.html",
            last_year=ly,
            teams=teams,
            games=games,
            conf_filter=conf_filter,
            conferences=conferences,
            active="last_year",
        )

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=5000)
