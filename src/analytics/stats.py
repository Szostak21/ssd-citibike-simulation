"""Statistical analysis, 95% confidence intervals, and paired hypothesis tests (H1-H6)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl
from scipy import stats

ALPHA = 0.05

KPI_COLUMNS = (
    "demand_total",
    "rentals_ok",
    "service_rate_pct",
    "fail_rent",
    "fail_rent_pct",
    "fail_return",
    "fail_return_pct",
    "walk_redirect",
    "empty_station_min",
    "full_station_min",
    "unavailability_min",
    "van_km",
    "bikes_moved",
    "saved_rent",
    "saved_total",
    "saved_rent_per_km",
    "saved_total_per_km",
)


def enrich_runs(runs: pl.DataFrame) -> pl.DataFrame:
    """Add derived KPIs and paired baseline (Scenario A) savings per run."""
    enriched = runs.with_columns(
        (pl.col("fail_rent") + pl.col("fail_return")).alias("fail_total"),
        (100.0 * pl.col("rentals_ok") / pl.col("demand_total")).alias("service_rate_pct"),
        (100.0 * pl.col("fail_rent") / pl.col("demand_total")).alias("fail_rent_pct"),
        (100.0 * pl.col("fail_return") / pl.col("demand_total")).alias("fail_return_pct"),
        (pl.col("empty_station_min") + pl.col("full_station_min")).alias("unavailability_min"),
    )

    baseline = (
        enriched.filter(pl.col("scenario") == "A")
        .select(
            "seed",
            "weather",
            "day_type",
            pl.col("fail_rent").alias("_base_fail_rent"),
            pl.col("fail_return").alias("_base_fail_return"),
            pl.col("fail_total").alias("_base_fail_total"),
        )
    )

    enriched = enriched.join(baseline, on=["seed", "weather", "day_type"], how="left")
    enriched = enriched.with_columns(
        (pl.col("_base_fail_rent") - pl.col("fail_rent")).alias("saved_rent"),
        (pl.col("_base_fail_return") - pl.col("fail_return")).alias("saved_return"),
        (pl.col("_base_fail_total") - pl.col("fail_total")).alias("saved_total"),
    ).with_columns(
        pl.when(pl.col("van_km") > 0)
        .then(pl.col("saved_rent") / pl.col("van_km"))
        .otherwise(None)
        .alias("saved_rent_per_km"),
        pl.when(pl.col("van_km") > 0)
        .then(pl.col("saved_total") / pl.col("van_km"))
        .otherwise(None)
        .alias("saved_total_per_km"),
    ).drop("_base_fail_rent", "_base_fail_return", "_base_fail_total")

    return enriched


def ci95(values: np.ndarray | list[float]) -> dict[str, float]:
    """Compute mean, standard deviation, and 95% Student-t confidence interval."""
    arr = np.asarray(values, dtype=float)
    arr = arr[~np.isnan(arr)]
    n = len(arr)
    if n == 0:
        return {"n": 0, "mean": math.nan, "std": math.nan, "ci_low": math.nan, "ci_high": math.nan, "ci_half": math.nan}
    mean = float(np.mean(arr))
    if n == 1:
        return {"n": 1, "mean": mean, "std": 0.0, "ci_low": mean, "ci_high": mean, "ci_half": 0.0}
    std = float(np.std(arr, ddof=1))
    se = std / math.sqrt(n)
    t_crit = float(stats.t.ppf(1.0 - ALPHA / 2.0, df=n - 1))
    half = t_crit * se
    return {
        "n": n,
        "mean": mean,
        "std": std,
        "ci_low": mean - half,
        "ci_high": mean + half,
        "ci_half": half,
    }


def summarize_kpis(
    runs_enriched: pl.DataFrame,
    group_cols: tuple[str, ...] = ("scenario", "n_vans", "weather", "day_type"),
) -> pl.DataFrame:
    """Compute 95% confidence intervals for all KPIs grouped by experimental configuration."""
    rows: list[dict[str, Any]] = []
    for keys, group in runs_enriched.sort(list(group_cols)).group_by(list(group_cols), maintain_order=True):
        key_tuple = keys if isinstance(keys, tuple) else (keys,)
        base_info = dict(zip(group_cols, key_tuple, strict=True))
        for kpi in KPI_COLUMNS:
            st = ci95(group[kpi].to_numpy())
            rows.append({
                **base_info,
                "kpi": kpi,
                "n": st["n"],
                "mean": st["mean"],
                "std": st["std"],
                "ci_low": st["ci_low"],
                "ci_high": st["ci_high"],
                "ci_half": st["ci_half"],
            })
    return pl.DataFrame(rows)


def paired_test(
    x: np.ndarray | list[float],
    y: np.ndarray | list[float],
    alternative: str = "two-sided",
) -> dict[str, Any]:
    """Run paired Student's t-test and Wilcoxon signed-rank test on CRN-aligned samples.

    Tests difference ``d = x - y``.
    Primary test is Student's t-test when Shapiro-Wilk normality on ``d`` holds (p >= 0.05),
    otherwise Wilcoxon signed-rank test.
    """
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    mask = ~(np.isnan(xa) | np.isnan(ya))
    xa, ya = xa[mask], ya[mask]
    diff = xa - ya
    n = len(diff)
    if n < 2:
        raise ValueError(f"Need at least 2 paired observations, got {n}")

    diff_stats = ci95(diff)
    std_d = diff_stats["std"]
    cohens_d = diff_stats["mean"] / std_d if std_d > 0 else 0.0

    if np.allclose(diff, 0.0):
        return {
            "n": n,
            "mean_x": float(np.mean(xa)),
            "mean_y": float(np.mean(ya)),
            "mean_diff": 0.0,
            "ci95_low": 0.0,
            "ci95_high": 0.0,
            "cohens_d": 0.0,
            "shapiro_p": 1.0,
            "t_stat": 0.0,
            "p_ttest": 1.0,
            "w_stat": 0.0,
            "p_wilcoxon": 1.0,
            "primary_test": "t-test",
            "p_primary": 1.0,
        }

    shapiro_p = float(stats.shapiro(diff).pvalue) if 3 <= n <= 5000 and std_d > 0 else 1.0
    t_res = stats.ttest_rel(xa, ya, alternative=alternative)
    w_res = stats.wilcoxon(xa, ya, zero_method="wilcox", alternative=alternative)

    use_ttest = shapiro_p >= ALPHA
    return {
        "n": n,
        "mean_x": float(np.mean(xa)),
        "mean_y": float(np.mean(ya)),
        "mean_diff": diff_stats["mean"],
        "ci95_low": diff_stats["ci_low"],
        "ci95_high": diff_stats["ci_high"],
        "cohens_d": cohens_d,
        "shapiro_p": shapiro_p,
        "t_stat": float(t_res.statistic),
        "p_ttest": float(t_res.pvalue),
        "w_stat": float(w_res.statistic),
        "p_wilcoxon": float(w_res.pvalue),
        "primary_test": "t-test" if use_ttest else "Wilcoxon",
        "p_primary": float(t_res.pvalue if use_ttest else w_res.pvalue),
    }


def _aligned_pair(
    runs: pl.DataFrame,
    scen_x: str,
    n_x: int,
    scen_y: str,
    n_y: int,
    metric: str,
    weather: str = "dry",
    day_type: str = "weekday",
) -> tuple[np.ndarray, np.ndarray]:
    sub_x = (
        runs.filter(
            (pl.col("scenario") == scen_x)
            & (pl.col("n_vans") == n_x)
            & (pl.col("weather") == weather)
            & (pl.col("day_type") == day_type)
        )
        .select("seed", pl.col(metric).alias("x"))
    )
    sub_y = (
        runs.filter(
            (pl.col("scenario") == scen_y)
            & (pl.col("n_vans") == n_y)
            & (pl.col("weather") == weather)
            & (pl.col("day_type") == day_type)
        )
        .select("seed", pl.col(metric).alias("y"))
    )
    joined = sub_x.join(sub_y, on="seed", how="inner").sort("seed")
    return joined["x"].to_numpy(), joined["y"].to_numpy()


def evaluate_hypotheses(
    runs_enriched: pl.DataFrame,
    ref_n_vans: int = 4,
    weather: str = "dry",
    day_type: str = "weekday",
) -> pl.DataFrame:
    """Evaluate hypotheses H1-H6 using CRN-paired statistical tests."""
    records: list[dict[str, Any]] = []

    def _mean_fmt(scen: str, n: int, col: str, fmt: str = ".1f") -> str:
        sub = runs_enriched.filter(
            (pl.col("scenario") == scen)
            & (pl.col("n_vans") == n)
            & (pl.col("weather") == weather)
            & (pl.col("day_type") == day_type)
        )
        if sub.height == 0:
            return "—"
        st = ci95(sub[col].to_numpy())
        if math.isnan(st["mean"]):
            return "—"
        return f"{st['mean']:{fmt}} ± {st['ci_half']:{fmt}}"

    # H1: B vs A reduces fail_rent
    bx, ay = _aligned_pair(runs_enriched, "B", ref_n_vans, "A", 0, "fail_rent", weather, day_type)
    res_h1 = paired_test(bx, ay, alternative="less")
    records.append({
        "Hipoteza": "H1",
        "Opis": f"Reaktywna (B, N={ref_n_vans}) redukuje utracone wypożyczenia względem bazowego (A)",
        "Metryka": "fail_rent [klientów/dzień]",
        "Wynik A": _mean_fmt("A", 0, "fail_rent"),
        "Wynik B": _mean_fmt("B", ref_n_vans, "fail_rent"),
        "Wynik C": _mean_fmt("C", ref_n_vans, "fail_rent"),
        "Test": res_h1["primary_test"],
        "p_value": res_h1["p_primary"],
        "p_ttest": res_h1["p_ttest"],
        "p_wilcoxon": res_h1["p_wilcoxon"],
        "cohens_d": res_h1["cohens_d"],
        "Status": "Potwierdzona" if res_h1["p_primary"] < ALPHA else "Odrzucona",
    })

    # H2: C vs B reduces fail_rent at equal fleet size
    cx, by = _aligned_pair(runs_enriched, "C", ref_n_vans, "B", ref_n_vans, "fail_rent", weather, day_type)
    res_h2 = paired_test(cx, by, alternative="less")
    records.append({
        "Hipoteza": "H2",
        "Opis": f"Predykcyjna (C, N={ref_n_vans}) przewyższa reaktywną (B, N={ref_n_vans}) w redukcji strat popytu",
        "Metryka": "fail_rent [klientów/dzień]",
        "Wynik A": _mean_fmt("A", 0, "fail_rent"),
        "Wynik B": _mean_fmt("B", ref_n_vans, "fail_rent"),
        "Wynik C": _mean_fmt("C", ref_n_vans, "fail_rent"),
        "Test": res_h2["primary_test"],
        "p_value": res_h2["p_primary"],
        "p_ttest": res_h2["p_ttest"],
        "p_wilcoxon": res_h2["p_wilcoxon"],
        "cohens_d": res_h2["cohens_d"],
        "Status": "Potwierdzona" if res_h2["p_primary"] < ALPHA else "Odrzucona",
    })

    # H3: C achieves higher saved_total_per_km than B
    cx3, by3 = _aligned_pair(runs_enriched, "C", ref_n_vans, "B", ref_n_vans, "saved_total_per_km", weather, day_type)
    res_h3 = paired_test(cx3, by3, alternative="greater")
    records.append({
        "Hipoteza": "H3",
        "Opis": f"Predykcyjna (C, N={ref_n_vans}) ma wyższą efektywność kosztową (uratowani klienci / km vana)",
        "Metryka": "saved_total_per_km [os./km]",
        "Wynik A": "—",
        "Wynik B": _mean_fmt("B", ref_n_vans, "saved_total_per_km", ".2f"),
        "Wynik C": _mean_fmt("C", ref_n_vans, "saved_total_per_km", ".2f"),
        "Test": res_h3["primary_test"],
        "p_value": res_h3["p_primary"],
        "p_ttest": res_h3["p_ttest"],
        "p_wilcoxon": res_h3["p_wilcoxon"],
        "cohens_d": res_h3["cohens_d"],
        "Status": "Potwierdzona" if res_h3["p_primary"] < ALPHA else "Odrzucona",
    })

    # H4: Diminishing marginal returns of fleet size in C (gain 1->2 vs gain 6->8)
    c1, c2 = _aligned_pair(runs_enriched, "C", 1, "C", 2, "service_rate_pct", weather, day_type)
    c6, c8 = _aligned_pair(runs_enriched, "C", 6, "C", 8, "service_rate_pct", weather, day_type)
    b1, b2 = _aligned_pair(runs_enriched, "B", 1, "B", 2, "service_rate_pct", weather, day_type)
    b6, b8 = _aligned_pair(runs_enriched, "B", 6, "B", 8, "service_rate_pct", weather, day_type)
    gain_low = c2 - c1
    gain_high = c8 - c6
    res_h4 = paired_test(gain_low, gain_high, alternative="greater")
    records.append({
        "Hipoteza": "H4",
        "Opis": "Malejące przychody krańcowe floty C: przyrost N=1→2 > N=6→8 [% obsłużonego popytu]",
        "Metryka": "Δ service_rate_pct [pp]",
        "Wynik A": _mean_fmt("A", 0, "service_rate_pct", ".2f") + "%",
        "Wynik B": f"+{np.mean(b2 - b1):.2f} pp vs +{np.mean(b8 - b6):.2f} pp",
        "Wynik C": f"+{np.mean(gain_low):.2f} pp vs +{np.mean(gain_high):.2f} pp",
        "Test": res_h4["primary_test"],
        "p_value": res_h4["p_primary"],
        "p_ttest": res_h4["p_ttest"],
        "p_wilcoxon": res_h4["p_wilcoxon"],
        "cohens_d": res_h4["cohens_d"],
        "Status": "Potwierdzona" if res_h4["p_primary"] < ALPHA else "Odrzucona",
    })

    # H5: Smaller predictive fleet C(N=4) outperforms larger reactive fleet B(N=6)
    c4, b6 = _aligned_pair(runs_enriched, "C", 4, "B", 6, "service_rate_pct", weather, day_type)
    res_h5 = paired_test(c4, b6, alternative="greater")
    records.append({
        "Hipoteza": "H5",
        "Opis": "Mniejsza flota predykcyjna C (N=4) przewyższa większą flotę reaktywną B (N=6)",
        "Metryka": "service_rate_pct [%]",
        "Wynik A": _mean_fmt("A", 0, "service_rate_pct", ".2f") + "%",
        "Wynik B": _mean_fmt("B", 6, "service_rate_pct", ".2f") + "% (N=6)",
        "Wynik C": _mean_fmt("C", 4, "service_rate_pct", ".2f") + "% (N=4)",
        "Test": res_h5["primary_test"],
        "p_value": res_h5["p_primary"],
        "p_ttest": res_h5["p_ttest"],
        "p_wilcoxon": res_h5["p_wilcoxon"],
        "cohens_d": res_h5["cohens_d"],
        "Status": "Potwierdzona" if res_h5["p_primary"] < ALPHA else "Odrzucona",
    })

    # H6: Predictive C reduces station unavailability minutes vs B (and A)
    cx6, by6 = _aligned_pair(runs_enriched, "C", ref_n_vans, "B", ref_n_vans, "unavailability_min", weather, day_type)
    res_h6 = paired_test(cx6, by6, alternative="less")
    records.append({
        "Hipoteza": "H6",
        "Opis": f"Predykcyjna (C, N={ref_n_vans}) redukuje stacjo-minuty niedostępności (pustka+przepełnienie) vs B",
        "Metryka": "unavailability_min [stacjo-min]",
        "Wynik A": _mean_fmt("A", 0, "unavailability_min", ".0f"),
        "Wynik B": _mean_fmt("B", ref_n_vans, "unavailability_min", ".0f"),
        "Wynik C": _mean_fmt("C", ref_n_vans, "unavailability_min", ".0f"),
        "Test": res_h6["primary_test"],
        "p_value": res_h6["p_primary"],
        "p_ttest": res_h6["p_ttest"],
        "p_wilcoxon": res_h6["p_wilcoxon"],
        "cohens_d": res_h6["cohens_d"],
        "Status": "Potwierdzona" if res_h6["p_primary"] < ALPHA else "Odrzucona",
    })

    return pl.DataFrame(records)


def summarize_hourly(
    hourly: pl.DataFrame,
    runs: pl.DataFrame,
    ref_n_vans: int = 4,
    weather: str = "dry",
    day_type: str = "weekday",
) -> pl.DataFrame:
    """Aggregate hourly unserved-demand percentage with 95% CI for A (N=0), B (N=ref), C (N=ref)."""
    meta = runs.filter(
        (pl.col("weather") == weather)
        & (pl.col("day_type") == day_type)
        & (
            ((pl.col("scenario") == "A") & (pl.col("n_vans") == 0))
            | ((pl.col("scenario").is_in(["B", "C"])) & (pl.col("n_vans") == ref_n_vans))
        )
    ).select("run_id", "scenario", "seed", "n_vans")

    joined = hourly.join(meta, on="run_id", how="inner").with_columns(
        (100.0 * pl.col("fail_rent") / pl.col("demand")).alias("fail_rent_pct"),
        (100.0 * (pl.col("fail_rent") + pl.col("fail_return")) / pl.col("demand")).alias("fail_total_pct"),
    )

    rows: list[dict[str, Any]] = []
    for (scenario, hour), group in joined.sort(["scenario", "hour"]).group_by(["scenario", "hour"], maintain_order=True):
        st_rent = ci95(group["fail_rent_pct"].to_numpy())
        st_tot = ci95(group["fail_total_pct"].to_numpy())
        rows.append({
            "scenario": scenario,
            "hour": int(hour),
            "n": st_rent["n"],
            "fail_rent_pct_mean": st_rent["mean"],
            "fail_rent_pct_ci_low": st_rent["ci_low"],
            "fail_rent_pct_ci_high": st_rent["ci_high"],
            "fail_total_pct_mean": st_tot["mean"],
            "fail_total_pct_ci_low": st_tot["ci_low"],
            "fail_total_pct_ci_high": st_tot["ci_high"],
        })
    return pl.DataFrame(rows)
