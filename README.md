# CFB Simulation Viewer

Local web app for exploring Monte Carlo simulation results exported from the pipeline CSVs.

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

## Data

`export_sim_data.py` reads canonical CSVs from `C:\Users\ender` and writes JSON to `cfb-viewer/data/`:

| File | Contents |
|------|----------|
| `meta.json` | Season, sim count, export time, source paths |
| `leaderboard.json` | Title odds, eligibility %, playoff apps, avg seed |
| `teams.json` | Win histograms and avg wins per team |
| `eligibility.json` | 12 team IDs per sim (compact) |
| `field_analysis.json` | Top teams, pairs, closest overlapping fields |
| `schedule.json` | Per-game win_pct (mean across sims) |
| `conferences.json` | Per-conference aggregates |
| `last_year.json` | 2025 FBS ratings and game results (ESPN + margin ratings) |

The Flask app loads these JSON files at startup. Re-run the export script after any pipeline change; restart the app to pick up new data.

## Pages

| Route | View |
|-------|------|
| `/` | Leaderboard — sortable, filter by conference |
| `/team/<id>` | Team detail — stats + win distribution chart |
| `/compare` | Compare 2–4 teams side-by-side |
| `/fields` | Playoff field frequency + sim lookup |
| `/schedule` | Per-game win rates with team/conference/week filters |
| `/conferences` | Conference summary cards and team tables |
| `/last-year` | Last Year — 2025 team ratings and game results |

## Notes

- Export uses stdlib `csv` only (no pandas).
- Chart.js is loaded from CDN; no frontend build step.
- Raw 1000-column CSVs are not served to the browser — only pre-aggregated JSON.

## Deploy (Render)

Production uses **gunicorn** via [`wsgi.py`](wsgi.py). Config is in [`render.yaml`](render.yaml).

### First-time setup

1. Install [Git for Windows](https://git-scm.com/download/win) and create a **public** GitHub repo (e.g. `cfb-viewer`).
2. Push this folder (include `data/*.json` — the server has no access to local CSVs):

```bash
cd C:\Users\ender\cfb-viewer
git init
git add .
git commit -m "Initial commit: CFB simulation viewer"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/cfb-viewer.git
git push -u origin main
```

3. Sign in at [render.com](https://render.com) → **New → Blueprint** → connect the repo.
4. After deploy, open the URL Render provides (e.g. `https://cfb-viewer.onrender.com`).

On the free plan, the app sleeps after ~15 minutes of inactivity; the first visit after that may take ~30 seconds to wake up.

### Refresh data after pipeline runs

```bash
python C:\Users\ender\cfb-viewer\export_sim_data.py
cd C:\Users\ender\cfb-viewer
git add data/
git commit -m "Refresh simulation data"
git push
```

Render auto-redeploys on push to `main`.

**Live site:** https://cfb-viewer.onrender.com
