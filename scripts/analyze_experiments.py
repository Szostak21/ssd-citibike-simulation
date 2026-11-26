"""Task 4.2: Statistical tests (95% CI, paired t-test/Wilcoxon H1-H6) and report figures.

Reads `data/outputs/metrics/metrics_{runs,hourly}.csv` if present, otherwise falls back
to `data/mock/mock_metrics_{runs,hourly}.csv`.

Usage:
    uv run python scripts/analyze_experiments.py
    uv run python scripts/analyze_experiments.py --mock --ref-vans 4
"""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

from analytics.plots import generate_all_figures
from analytics.stats import enrich_runs, evaluate_hypotheses, summarize_hourly, summarize_kpis
from common.config import OUTPUTS, ROOT
from viz.loaders import load_metrics


def _format_p(p: float) -> str:
    return "< 0.0001" if p < 1e-4 else f"{p:.4f}"


def _to_markdown_table(hyp: pl.DataFrame) -> str:
    cols = ["Hipoteza", "Metryka", "Wynik A", "Wynik B", "Wynik C", "Test", "p_value", "Status"]
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    lines = [header, sep]
    for row in hyp.iter_rows(named=True):
        vals = [
            row["Hipoteza"],
            row["Metryka"],
            row["Wynik A"],
            row["Wynik B"],
            row["Wynik C"],
            row["Test"],
            _format_p(row["p_value"]),
            row["Status"],
        ]
        lines.append("| " + " | ".join(str(v) for v in vals) + " |")
    return "\n".join(lines) + "\n"


def _to_latex_table(hyp: pl.DataFrame) -> str:
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{Weryfikacja statystyczna hipotez badawczych H1--H6 (testy sparowane CRN, $\alpha = 0{,}05$).}",
        r"\label{tab:hypotheses}",
        r"\begin{tabular}{lllllll}",
        r"\toprule",
        r"Hipoteza & Metryka & Wynik A & Wynik B & Wynik C & $p$-value & Status \\",
        r"\midrule",
    ]
    for row in hyp.iter_rows(named=True):
        p_str = _format_p(row["p_value"]).replace("<", "$<$")
        lines.append(
            f"{row['Hipoteza']} & {row['Metryka']} & {row['Wynik A']} & "
            f"{row['Wynik B']} & {row['Wynik C']} & {p_str} ({row['Test']}) & {row['Status']} \\\\"
        )
    lines.extend([r"\bottomrule", r"\end{tabular}", r"\end{table}"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Task 4.2 statistical analysis and figure generation.")
    parser.add_argument("--mock", action="store_true", help="Force loading mock metrics instead of outputs.")
    parser.add_argument("--ref-vans", type=int, default=4, help="Reference fleet size for A vs B vs C (default: 4).")
    parser.add_argument("--weather", default="dry", help="Weather condition to analyze (default: dry).")
    parser.add_argument("--day-type", default="weekday", help="Day type to analyze (default: weekday).")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "reports", help="Output directory (default: reports/).")
    args = parser.parse_args()

    real_exists = (OUTPUTS / "metrics" / "metrics_runs.csv").exists()
    source_kind = "mock" if (args.mock or not real_exists) else "outputs"
    runs, hourly = load_metrics(use_mock=args.mock)
    print(f"Loaded {runs.height} runs and {hourly.height} hourly rows (source: {source_kind}).")

    enriched = enrich_runs(runs)
    kpi_summary = summarize_kpis(enriched)
    hypotheses = evaluate_hypotheses(
        enriched,
        ref_n_vans=args.ref_vans,
        weather=args.weather,
        day_type=args.day_type,
    )
    hourly_summary = summarize_hourly(
        hourly,
        runs,
        ref_n_vans=args.ref_vans,
        weather=args.weather,
        day_type=args.day_type,
    )

    out_dir: Path = args.out_dir
    fig_dir = out_dir / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)

    kpi_summary.write_csv(out_dir / "kpi_summary_95ci.csv")
    hypotheses.write_csv(out_dir / "hypotheses_h1_h6.csv")
    (out_dir / "hypotheses_h1_h6.md").write_text(_to_markdown_table(hypotheses), encoding="utf-8")
    (out_dir / "hypotheses_h1_h6.tex").write_text(_to_latex_table(hypotheses), encoding="utf-8")

    fig_paths = generate_all_figures(
        kpi_summary,
        hourly_summary,
        fig_dir,
        ref_n_vans=args.ref_vans,
        weather=args.weather,
        day_type=args.day_type,
    )

    print("\n=== Weryfikacja hipotez H1-H6 ===")
    print(_to_markdown_table(hypotheses))
    print(f"Saved tables to {out_dir} and {len(fig_paths)} figure files to {fig_dir}.")


if __name__ == "__main__":
    main()
