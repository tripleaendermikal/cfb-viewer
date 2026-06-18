#!/usr/bin/env python3
"""Build 2025 game-level FPI margin data and fit logistic regression for home wins."""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

from _paths import DATA_ROOT

DEFAULT_GAMES_CSV = DATA_ROOT / "cfb_2025_espn_games.csv"
DEFAULT_FPI_CSV = DATA_ROOT / "historical_FPI.csv"
DEFAULT_GAMES_OUT = DATA_ROOT / "cfb_2025_fpi_logistic_games.csv"
DEFAULT_RESULTS_OUT = DATA_ROOT / "cfb_2025_fpi_logistic_results.txt"
FPI_COLUMN = "fpi_2025"

GAME_FIELDS = [
    "game_id",
    "week",
    "game_date_utc",
    "home_team_id",
    "home_team",
    "away_team_id",
    "away_team",
    "home_points",
    "away_points",
    "home_win",
    "fpi_margin",
    "not_neutral",
]


def parse_fpi(value: str | None) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    return float(value)


def is_neutral_site(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "1", "yes")


def load_fpi_lookup(path: Path) -> dict[str, float]:
    lookup: dict[str, float] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            fpi = parse_fpi(row.get(FPI_COLUMN))
            if fpi is not None:
                lookup[row["team_id"].strip()] = fpi
    return lookup


def build_game_rows(games_csv: Path, fpi_lookup: dict[str, float]) -> tuple[list[dict[str, str]], int]:
    rows: list[dict[str, str]] = []
    input_count = 0

    with games_csv.open(encoding="utf-8-sig", newline="") as handle:
        for game in csv.DictReader(handle):
            input_count += 1
            home_id = game["home_team_id"].strip()
            away_id = game["away_team_id"].strip()
            home_fpi = fpi_lookup.get(home_id)
            away_fpi = fpi_lookup.get(away_id)
            if home_fpi is None or away_fpi is None:
                continue

            home_points = int(float(game["home_points"]))
            away_points = int(float(game["away_points"]))
            home_win = 1 if home_points > away_points else 0
            not_neutral = 0 if is_neutral_site(game.get("neutral_site")) else 1

            rows.append(
                {
                    "game_id": game["game_id"],
                    "week": game["week"],
                    "game_date_utc": game["game_date_utc"],
                    "home_team_id": home_id,
                    "home_team": game["home_team"],
                    "away_team_id": away_id,
                    "away_team": game["away_team"],
                    "home_points": str(home_points),
                    "away_points": str(away_points),
                    "home_win": str(home_win),
                    "fpi_margin": f"{home_fpi - away_fpi:.6f}",
                    "not_neutral": str(not_neutral),
                }
            )

    return rows, input_count


def write_games_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=GAME_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def sigmoid(eta: float) -> float:
    if eta >= 0:
        return 1.0 / (1.0 + math.exp(-eta))
    exp_eta = math.exp(eta)
    return exp_eta / (1.0 + exp_eta)


def mat_vec_mul(matrix: list[list[float]], vector: list[float]) -> list[float]:
    return [sum(row[j] * vector[j] for j in range(len(vector))) for row in matrix]


def mat_transpose(matrix: list[list[float]]) -> list[list[float]]:
    if not matrix:
        return []
    return [[matrix[i][j] for i in range(len(matrix))] for j in range(len(matrix[0]))]


def mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    rows = len(a)
    cols = len(b[0])
    inner = len(b)
    out = [[0.0] * cols for _ in range(rows)]
    for i in range(rows):
        for k in range(inner):
            aik = a[i][k]
            for j in range(cols):
                out[i][j] += aik * b[k][j]
    return out


def invert_matrix(matrix: list[list[float]]) -> list[list[float]]:
    n = len(matrix)
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]

    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot_row][col]) < 1e-12:
            raise ValueError("Singular matrix in logistic regression fit")

        if pivot_row != col:
            aug[col], aug[pivot_row] = aug[pivot_row], aug[col]

        pivot = aug[col][col]
        aug[col] = [value / pivot for value in aug[col]]

        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            if factor == 0.0:
                continue
            aug[row] = [aug[row][j] - factor * aug[col][j] for j in range(2 * n)]

    return [row[n:] for row in aug]


def solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(matrix)
    aug = [matrix[i][:] + [vector[i]] for i in range(n)]

    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot_row][col]) < 1e-12:
            raise ValueError("Singular matrix in weighted least squares")

        if pivot_row != col:
            aug[col], aug[pivot_row] = aug[pivot_row], aug[col]

        pivot = aug[col][col]
        aug[col] = [value / pivot for value in aug[col]]

        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            if factor == 0.0:
                continue
            aug[row] = [aug[row][j] - factor * aug[col][j] for j in range(n + 1)]

    return [aug[i][n] for i in range(n)]


def compute_xtwx(design: list[list[float]], weights: list[float]) -> list[list[float]]:
    n = len(design)
    p = len(design[0])
    xtw = [[0.0] * p for _ in range(p)]
    for i in range(n):
        w = weights[i]
        row = design[i]
        for j in range(p):
            xij = row[j]
            for k in range(p):
                xtw[j][k] += xij * w * row[k]
    return xtw


def compute_xtwz(design: list[list[float]], weights: list[float], z: list[float]) -> list[float]:
    p = len(design[0])
    xtwz = [0.0] * p
    for i in range(n := len(design)):
        w = weights[i]
        zi = z[i]
        row = design[i]
        for j in range(p):
            xtwz[j] += row[j] * w * zi
    return xtwz


def fit_logistic_irls(
    design: list[list[float]],
    y: list[float],
    *,
    max_iter: int = 50,
    tol: float = 1e-8,
) -> tuple[list[float], list[list[float]]]:
    p = len(design[0])
    beta = [0.0] * p
    xtw = [[0.0] * p for _ in range(p)]

    for _ in range(max_iter):
        eta = mat_vec_mul(design, beta)
        mu = [min(max(sigmoid(value), 1e-15), 1.0 - 1e-15) for value in eta]
        z = [eta[i] + (y[i] - mu[i]) / (mu[i] * (1.0 - mu[i])) for i in range(len(y))]
        weights = [mu[i] * (1.0 - mu[i]) for i in range(len(y))]

        xtw = compute_xtwx(design, weights)
        xtwz = compute_xtwz(design, weights, z)

        beta_new = solve_linear_system(xtw, xtwz)
        if max(abs(beta_new[j] - beta[j]) for j in range(p)) < tol:
            beta = beta_new
            break
        beta = beta_new

    cov = invert_matrix(xtw)
    return beta, cov


def standard_errors(cov: list[list[float]]) -> list[float]:
    return [math.sqrt(max(cov[i][i], 0.0)) for i in range(len(cov))]


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def wald_stats(beta: list[float], stderr: list[float]) -> list[tuple[float, float]]:
    stats: list[tuple[float, float]] = []
    for coef, se in zip(beta, stderr):
        if se <= 0.0:
            stats.append((float("nan"), float("nan")))
            continue
        z = coef / se
        p = 2.0 * (1.0 - norm_cdf(abs(z)))
        stats.append((z, p))
    return stats


def build_design(rows: list[dict[str, str]], *, no_intercept: bool) -> list[list[float]]:
    design: list[list[float]] = []
    for row in rows:
        features = [float(row["fpi_margin"]), float(row["not_neutral"])]
        if not no_intercept:
            features = [1.0] + features
        design.append(features)
    return design


def sanity_check(rows: list[dict[str, str]]) -> tuple[float, float]:
    positive = [row for row in rows if float(row["fpi_margin"]) > 0]
    negative = [row for row in rows if float(row["fpi_margin"]) < 0]
    pos_rate = (
        sum(int(row["home_win"]) for row in positive) / len(positive) if positive else float("nan")
    )
    neg_rate = (
        sum(int(row["home_win"]) for row in negative) / len(negative) if negative else float("nan")
    )
    return pos_rate, neg_rate


def format_results(
    *,
    input_count: int,
    rows: list[dict[str, str]],
    beta: list[float],
    stderr: list[float],
    wald: list[tuple[float, float]],
    pos_rate: float,
    neg_rate: float,
    no_intercept: bool,
) -> str:
    home_wins = sum(int(row["home_win"]) for row in rows)
    neutral_count = sum(1 for row in rows if row["not_neutral"] == "0")
    if no_intercept:
        model = "Model: logit(P(home_win)) = beta_1*fpi_margin + beta_2*not_neutral  (intercept fixed at 0)"
        names = ["beta_margin", "beta_not_neutral"]
    else:
        model = "Model: logit(P(home_win)) = beta_0 + beta_1*fpi_margin + beta_2*not_neutral"
        names = ["beta_intercept", "beta_margin", "beta_not_neutral"]

    lines = [
        "2025 home-win logistic regression vs end-of-season 2025 FPI",
        "",
        f"Input games: {input_count}",
        f"Included games: {len(rows)}",
        f"Excluded games: {input_count - len(rows)}",
        f"Home wins: {home_wins}",
        f"Neutral-site games in sample: {neutral_count}",
        "",
        "Sanity check (home win rate by fpi_margin sign):",
        f"  fpi_margin > 0: {pos_rate:.3f}",
        f"  fpi_margin < 0: {neg_rate:.3f}",
        "",
        model,
        "",
    ]
    for name, coef, se, (z, p) in zip(names, beta, stderr, wald):
        lines.append(
            f"{name} = {coef:.6f}  (stderr {se:.6f}, z {z:.3f}, p {p:.4f})"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games-csv", type=Path, default=DEFAULT_GAMES_CSV)
    parser.add_argument("--fpi-csv", type=Path, default=DEFAULT_FPI_CSV)
    parser.add_argument("-o", "--games-out", type=Path, default=DEFAULT_GAMES_OUT)
    parser.add_argument("--results-out", type=Path, default=DEFAULT_RESULTS_OUT)
    parser.add_argument(
        "--no-intercept",
        dest="no_intercept",
        action="store_true",
        default=True,
        help="Fit without intercept (default)",
    )
    parser.add_argument(
        "--with-intercept",
        dest="no_intercept",
        action="store_false",
        help="Include intercept term",
    )
    args = parser.parse_args()

    if not args.games_csv.is_file():
        print(f"Games CSV not found: {args.games_csv}", file=sys.stderr)
        return 1
    if not args.fpi_csv.is_file():
        print(f"FPI CSV not found: {args.fpi_csv}", file=sys.stderr)
        return 1

    fpi_lookup = load_fpi_lookup(args.fpi_csv)
    rows, input_count = build_game_rows(args.games_csv, fpi_lookup)
    if not rows:
        print("No games with complete 2025 FPI for both teams.", file=sys.stderr)
        return 1

    write_games_csv(args.games_out, rows)

    y = [float(row["home_win"]) for row in rows]
    design = build_design(rows, no_intercept=args.no_intercept)
    beta, cov = fit_logistic_irls(design, y)
    stderr = standard_errors(cov)
    wald = wald_stats(beta, stderr)
    pos_rate, neg_rate = sanity_check(rows)

    report = format_results(
        input_count=input_count,
        rows=rows,
        beta=beta,
        stderr=stderr,
        wald=wald,
        pos_rate=pos_rate,
        neg_rate=neg_rate,
        no_intercept=args.no_intercept,
    )
    args.results_out.write_text(report, encoding="utf-8")
    print(report, end="")
    print(f"Wrote games CSV: {args.games_out}")
    print(f"Wrote results: {args.results_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
