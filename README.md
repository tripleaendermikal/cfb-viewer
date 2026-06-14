# CFB Simulation Viewer

Local web app for exploring Monte Carlo simulation results exported from the pipeline CSVs.

**Live site:** https://cfb-viewer.onrender.com

## Prerequisites

- Python 3.10+
- Pipeline outputs in `C:\Users\ender` (see parent skill for canonical file names)
- `flask` (`pip install -r requirements.txt`)

## Workflow

After rerunning the simulation pipeline:

```bash
# 1. Export CSVs → JSON summaries
python C:\Users\ender\cfb-viewer\export_sim_data.py

# 2. Start the viewer
python C:\Users\ender\cfb-viewer\app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

## Data layout

`export_sim_data.py` reads canonical CSVs from `C:\Users\ender` and writes JSON to `cfb-viewer/data/`:

| File | Contents | Loaded at startup |
|------|----------|-------------------|
| `meta.json` | Season, sim count, export time | Yes |
| `leaderboard.json` | Title odds, playoff odds, conf title, FPI | Yes |
| `teams.json` | Win histograms and avg wins | Yes |
| `eligibility.json` | `{ sim_count }` only | Yes |
| `field_analysis.json` | Top teams, pairs, closest fields | Yes |
| `conferences.json` | Per-conference aggregates | Yes |
| `schedule.json` | Per-game win_pct and avg margin | Lazy (schedule route) |
| `games.json` | Game detail records keyed by `game_id` | Lazy (game/schedule routes) |
| `brackets_summary.json` | Team seed histograms, avg seed | Lazy (bracket/team routes) |
| `conf_championship_summary.json` | CCG team summary | Lazy (team route) |
| `data/sim/0001.json` … | Per-sim field, bracket, conf champs | On demand (sim lookup) |
| `last_year.json` | 2025 FBS ratings and game results | Lazy (last-year route) |

Large per-sim arrays are split into `data/sim/` so the app cold-starts on ~150 KB of core JSON instead of parsing multi-megabyte monolithic files.

Re-run the export script after any pipeline change; restart the app (or redeploy) to pick up new data.

## Pages

| Route | View |
|-------|------|
| `/` | Leaderboard — sortable, filter by conference, team search |
| `/team/<id>` | Team detail — stats, CCG record, win chart |
| `/compare` | Compare 2–4 teams side-by-side |
| `/fields` | Playoff field frequency + sim lookup (conf champs) |
| `/bracket` | Playoff bracket by sim or team seed history |
| `/schedule` | Per-game win rates; rows link to game detail |
| `/game/<id>` | Single-game matchup detail |
| `/conferences` | Conference summary cards and team tables |
| `/methodology` | How the simulations and metrics work |
| `/last-year` | 2025 team ratings and game results |

## Notes

- Export uses stdlib `csv` only (no pandas).
- Chart.js is loaded from CDN; no frontend build step.
- Raw 1000-column CSVs are not served to the browser — only pre-aggregated JSON.

## Deploy (Render)

Production uses **gunicorn** via [`wsgi.py`](wsgi.py). Config is in [`render.yaml`](render.yaml).

### Refresh data after pipeline runs

```bash
python C:\Users\ender\cfb-viewer\export_sim_data.py
cd C:\Users\ender\cfb-viewer
git add data/
git commit -m "Refresh simulation data"
git push
```

Render auto-redeploys on push to `main`. On the free plan, the app sleeps after ~15 minutes of inactivity; cold starts are faster with lazy JSON loading.
