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
        self.meta = self._load(data_dir / "meta.json")
        self.leaderboard = self._load(data_dir / "leaderboard.json")
        self.teams = self._load(data_dir / "teams.json")
        self.eligibility = self._load(data_dir / "eligibility.json")
        self.field_analysis = self._load(data_dir / "field_analysis.json")
        self.schedule = self._dedupe_schedule(self._load(data_dir / "schedule.json"))
        self.conferences = [
            c for c in self._load(data_dir / "conferences.json")
            if is_fbs_conference(c.get("conference", ""))
        ]

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
        self.last_year = self._load_optional(data_dir / "last_year.json")

    @staticmethod
    def _load_optional(path: Path) -> dict:
        if not path.is_file():
            return {}
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _load(path: Path):
        with path.open(encoding="utf-8") as f:
            return json.load(f)

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
        fields = self.eligibility.get("fields", [])
        if 0 <= sim_index < len(fields):
            return fields[sim_index]
        return None


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
        }

    @app.template_filter("conf_color")
    def conf_color(conf: str) -> str:
        return CONFERENCE_COLORS.get(conf or "", "#555555")

    @app.template_filter("pct")
    def fmt_pct(val) -> str:
        if val is None:
            return "—"
        return f"{float(val):.1f}%"

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
            "conf_champ_appearances": lb.get("conf_champ_appearances", 0),
            "eligibility_pct": lb.get("eligibility_pct", 0),
            "playoff_appearances": lb.get("playoff_appearances", 0),
            "avg_seed_when_in": lb.get("avg_seed_when_in", 0),
        }
        hist_labels = [str(i) for i in range(13)]
        hist_data = [merged["win_histogram"].get(str(i), 0) for i in range(13)]
        return render_template(
            "team.html",
            team=merged,
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
        colors = ["#3d8bfd", "#3dd68c", "#f5a524", "#ff6b9d"]
        for i, tid in enumerate(ids):
            team = store.teams_by_id.get(tid)
            lb = store.lb_by_id.get(tid, {})
            if not team or not is_fbs_team(team):
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
                    "conf_champ_appearances": lb.get("conf_champ_appearances", 0),
                    "eligibility_pct": lb.get("eligibility_pct", 0),
                    "playoff_appearances": lb.get("playoff_appearances", 0),
                    "avg_seed_when_in": lb.get("avg_seed_when_in", 0),
                }
            )
            chart_datasets.append(
                {
                    "label": team["team_name"],
                    "data": [hist.get(str(w), 0) for w in range(13)],
                    "backgroundColor": colors[i % len(colors)] + "99",
                    "borderColor": colors[i % len(colors)],
                    "borderWidth": 1,
                }
            )
        team_options = store.fbs_teams
        return render_template(
            "compare.html",
            selected=selected,
            selected_ids=ids,
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
        if sim_raw:
            try:
                sim_index = int(sim_raw)
                if sim_index < 1 or sim_index > store.eligibility.get("sim_count", 1000):
                    sim_error = f"Sim index must be 1–{store.eligibility.get('sim_count', 1000)}"
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
            except ValueError:
                sim_error = "Invalid sim index"
        return render_template(
            "fields.html",
            analysis=store.field_analysis,
            sim_index=sim_index,
            sim_field=sim_field,
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
