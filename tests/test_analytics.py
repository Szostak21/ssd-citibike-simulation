from pathlib import Path

import polars as pl

from analytics.plots import generate_all_figures
from analytics.stats import (
    ci95,
    enrich_runs,
    evaluate_hypotheses,
    paired_test,
    summarize_hourly,
    summarize_kpis,
)
from common.config import MOCK
from common.contracts import validate_metrics


def test_ci95_and_paired_test():
    res = ci95([10.0, 12.0, 14.0, 12.0, 12.0])
    assert res["n"] == 5
    assert res["mean"] == 12.0
    assert res["ci_low"] < res["mean"] < res["ci_high"]

    x = [100.0, 102.0, 98.0, 105.0, 95.0, 101.0]
    y = [120.0, 121.0, 119.0, 125.0, 115.0, 122.0]
    pt = paired_test(x, y, alternative="less")
    assert pt["p_ttest"] < 0.001
    assert pt["p_wilcoxon"] < 0.05
    assert pt["p_primary"] < 0.05
    assert pt["mean_diff"] < 0

    zero_pt = paired_test([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert zero_pt["p_primary"] == 1.0


def test_analytics_and_plots_on_mock_metrics(tmp_path: Path):
    runs = pl.read_csv(MOCK / "mock_metrics_runs.csv")
    hourly = pl.read_csv(MOCK / "mock_metrics_hourly.csv")
    validate_metrics(runs, hourly)

    enriched = enrich_runs(runs)
    assert "service_rate_pct" in enriched.columns
    assert "saved_total_per_km" in enriched.columns
    assert "unavailability_min" in enriched.columns

    kpi_summary = summarize_kpis(enriched)
    assert kpi_summary.height > 0
    assert {"mean", "ci_low", "ci_high", "ci_half"} <= set(kpi_summary.columns)

    hyp = evaluate_hypotheses(enriched, ref_n_vans=4)
    assert hyp["Hipoteza"].to_list() == ["H1", "H2", "H3", "H4", "H5", "H6"]
    assert all(s == "Potwierdzona" for s in hyp["Status"].to_list())

    hourly_summary = summarize_hourly(hourly, runs, ref_n_vans=4)
    assert hourly_summary.height == 3 * 24

    paths = generate_all_figures(kpi_summary, hourly_summary, tmp_path / "figures", ref_n_vans=4)
    assert len(paths) == 8
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)
