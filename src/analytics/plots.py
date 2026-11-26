"""Publication-quality vector (PDF) and raster (PNG) figures for the final report."""

from __future__ import annotations

from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import polars as pl

matplotlib.use("Agg")

SCENARIO_COLORS = {
    "A": "#D97706",  # Amber (Bazowy)
    "B": "#DB2777",  # Magenta (Reaktywny)
    "C": "#0284C7",  # Cyan-Blue (Predykcyjny)
}

SCENARIO_LABELS = {
    "A": "Scenariusz A (Bazowy, N=0)",
    "B": "Scenariusz B (Reaktywny)",
    "C": "Scenariusz C (Predykcyjny)",
}


def _apply_style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.labelsize": 10.5,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "grid.linestyle": "--",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "legend.frameon": True,
        "legend.framealpha": 0.92,
        "figure.dpi": 150,
    })


def _save_fig(fig: plt.Figure, out_dir: Path, stem: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / f"{stem}.pdf", out_dir / f"{stem}.png"]
    fig.savefig(paths[0], bbox_inches="tight")
    fig.savefig(paths[1], dpi=300, bbox_inches="tight")
    plt.close(fig)
    return paths


def plot_hourly_unserved(
    hourly_summary: pl.DataFrame,
    out_dir: Path,
    ref_n_vans: int = 4,
) -> list[Path]:
    """Plot 24h profile of unserved rental demand (%) with 95% CI bands for A, B, C."""
    _apply_style()
    fig, ax = plt.subplots(figsize=(9.5, 4.8))

    labels = {
        "A": "Scenariusz A — Bazowy (N=0)",
        "B": f"Scenariusz B — Reaktywny (N={ref_n_vans})",
        "C": f"Scenariusz C — Predykcyjny (N={ref_n_vans})",
    }

    for scen in ("A", "B", "C"):
        sub = hourly_summary.filter(pl.col("scenario") == scen).sort("hour")
        if sub.height == 0:
            continue
        hr = sub["hour"].to_numpy()
        mean = sub["fail_rent_pct_mean"].to_numpy()
        lo = sub["fail_rent_pct_ci_low"].to_numpy()
        hi = sub["fail_rent_pct_ci_high"].to_numpy()
        color = SCENARIO_COLORS[scen]
        ax.plot(hr, mean, marker="o", markersize=3.5, linewidth=2.2, color=color, label=labels[scen])
        ax.fill_between(hr, lo, hi, color=color, alpha=0.16)

    ax.axvspan(7.5, 9.5, color="#94A3B8", alpha=0.12, label="Szczyt poranny (8:00–9:30)")
    ax.axvspan(16.5, 19.0, color="#64748B", alpha=0.12, label="Szczyt popołudniowy (17:00–19:00)")

    ax.set_xticks(range(0, 24, 2))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 24, 2)])
    ax.set_xlim(0, 23)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Godzina doby")
    ax.set_ylabel("Nieobsłużone wypożyczenia [%]")
    ax.set_title("Dobowy profil nieobsłużonych klientów (średnia z 30 replikacji ± 95% CI)")
    ax.legend(loc="upper left", fontsize=9)

    return _save_fig(fig, out_dir, "fig_hourly_unserved")


def plot_fleet_pareto(
    kpi_summary: pl.DataFrame,
    out_dir: Path,
    weather: str = "dry",
    day_type: str = "weekday",
) -> list[Path]:
    """Plot fleet-size saturation / Pareto curve (H4 & H5): n_vans vs service_rate_pct."""
    _apply_style()
    fig, ax = plt.subplots(figsize=(8.8, 4.8))

    sub = kpi_summary.filter(
        (pl.col("weather") == weather)
        & (pl.col("day_type") == day_type)
        & (pl.col("kpi") == "service_rate_pct")
    )

    base = sub.filter(pl.col("scenario") == "A")
    if base.height > 0:
        b_mean = float(base["mean"][0])
        b_lo = float(base["ci_low"][0])
        b_hi = float(base["ci_high"][0])
        ax.axhline(b_mean, color=SCENARIO_COLORS["A"], linestyle="--", linewidth=1.8,
                   label=f"Scenariusz A — Bazowy (N=0): {b_mean:.2f}%")
        ax.axhspan(b_lo, b_hi, color=SCENARIO_COLORS["A"], alpha=0.10)

    coords: dict[str, dict[int, float]] = {}
    for scen in ("B", "C"):
        sc = sub.filter(pl.col("scenario") == scen).sort("n_vans")
        if sc.height == 0:
            continue
        x = sc["n_vans"].to_numpy()
        y = sc["mean"].to_numpy()
        y_err = sc["ci_half"].to_numpy()
        coords[scen] = dict(zip(x.tolist(), y.tolist(), strict=True))
        color = SCENARIO_COLORS[scen]
        ax.errorbar(
            x, y, yerr=y_err, marker="s" if scen == "B" else "o", markersize=6,
            linewidth=2.3, capsize=4, color=color, label=SCENARIO_LABELS[scen],
        )

    # Annotate H5 comparison (C at N=4 vs B at N=6) if present
    if "C" in coords and "B" in coords and 4 in coords["C"] and 6 in coords["B"]:
        c4, b6 = coords["C"][4], coords["B"][6]
        ax.annotate(
            f"H5: C(4)={c4:.2f}% > B(6)={b6:.2f}%",
            xy=(4, c4),
            xytext=(4.3, c4 + (c4 - b6) * 0.45 + 0.35),
            arrowprops={"arrowstyle": "->", "color": SCENARIO_COLORS["C"], "lw": 1.3},
            fontsize=9,
            fontweight="bold",
            color=SCENARIO_COLORS["C"],
        )

    ax.set_xticks([1, 2, 4, 6, 8])
    ax.set_xlabel("Liczba furgonetek relokacyjnych N")
    ax.set_ylabel("Obsłużony popyt [%]")
    ax.set_title("Krzywa nasycenia floty: jakość obsługi vs liczba furgonetek (95% CI)")
    ax.legend(loc="lower right", fontsize=9)

    return _save_fig(fig, out_dir, "fig_fleet_pareto")


def plot_cost_efficiency(
    kpi_summary: pl.DataFrame,
    out_dir: Path,
    weather: str = "dry",
    day_type: str = "weekday",
) -> list[Path]:
    """Plot cost-efficiency bar chart (H3): saved clients per van km across fleet sizes."""
    _apply_style()
    fig, ax = plt.subplots(figsize=(8.6, 4.6))

    sub = kpi_summary.filter(
        (pl.col("weather") == weather)
        & (pl.col("day_type") == day_type)
        & (pl.col("kpi") == "saved_total_per_km")
        & (pl.col("scenario").is_in(["B", "C"]))
    )

    fleets = sorted(sub["n_vans"].unique().to_list())
    x = np.arange(len(fleets))
    width = 0.36

    for idx, scen in enumerate(("B", "C")):
        sc = sub.filter(pl.col("scenario") == scen).sort("n_vans")
        means = sc["mean"].to_numpy()
        errs = sc["ci_half"].to_numpy()
        offset = (-width / 2) if idx == 0 else (width / 2)
        bars = ax.bar(
            x + offset, means, width, yerr=errs, capsize=4,
            color=SCENARIO_COLORS[scen], alpha=0.88, label=SCENARIO_LABELS[scen],
        )
        for bar, val in zip(bars, means, strict=True):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.3,
                f"{val:.1f}",
                ha="center",
                va="bottom",
                fontsize=8.5,
            )

    ax.set_xticks(x)
    ax.set_xticklabels([f"N={n}" for n in fleets])
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Wielkość floty furgonetek")
    ax.set_ylabel("Uratowani klienci / 1 km vana [os./km]")
    ax.set_title("Efektywność kosztowa relokacji (H3): uniknięte straty popytu na 1 km (95% CI)")
    ax.legend(loc="upper right", fontsize=9)

    return _save_fig(fig, out_dir, "fig_cost_efficiency")


def plot_kpi_summary(
    kpi_summary: pl.DataFrame,
    out_dir: Path,
    ref_n_vans: int = 4,
    weather: str = "dry",
    day_type: str = "weekday",
) -> list[Path]:
    """Plot 2x2 summary of core KPIs for A (N=0), B (N=ref), C (N=ref) with 95% CI."""
    _apply_style()
    fig, axes = plt.subplots(2, 2, figsize=(10.2, 6.8))

    panels = [
        ("fail_rent", "Utracone wypożyczenia [os./dzień]"),
        ("fail_return", "Nieudane zwroty [os./dzień]"),
        ("unavailability_min", "Niedostępność stacji [stacjo-min]"),
        ("van_km", "Dystans furgonetek [km/dzień]"),
    ]

    sub = kpi_summary.filter(
        (pl.col("weather") == weather)
        & (pl.col("day_type") == day_type)
        & (
            ((pl.col("scenario") == "A") & (pl.col("n_vans") == 0))
            | ((pl.col("scenario").is_in(["B", "C"])) & (pl.col("n_vans") == ref_n_vans))
        )
    )

    scen_order = ["A", "B", "C"]
    x_labels = ["A (Bazowy)", f"B (Reaktywny, N={ref_n_vans})", f"C (Predykcyjny, N={ref_n_vans})"]
    colors = [SCENARIO_COLORS[s] for s in scen_order]

    for ax, (kpi, title) in zip(axes.flat, panels, strict=True):
        means, errs = [], []
        for s in scen_order:
            row = sub.filter((pl.col("scenario") == s) & (pl.col("kpi") == kpi))
            means.append(float(row["mean"][0]) if row.height else 0.0)
            errs.append(float(row["ci_half"][0]) if row.height else 0.0)
        bars = ax.bar(range(3), means, yerr=errs, capsize=4, color=colors, alpha=0.88, width=0.55)
        for bar, val in zip(bars, means, strict=True):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                f"{val:,.0f}",
                ha="center",
                va="bottom",
                fontsize=8.5,
            )
        ax.set_xticks(range(3))
        ax.set_xticklabels(x_labels, fontsize=8.5)
        ax.set_ylim(bottom=0)
        ax.set_title(title, fontsize=10.5)

    fig.suptitle(
        f"Porównanie kluczowych KPI dla floty referencyjnej N={ref_n_vans} (średnia ± 95% CI)",
        fontsize=12.5,
        fontweight="bold",
        y=1.01,
    )
    fig.tight_layout()
    return _save_fig(fig, out_dir, "fig_kpi_summary")


def generate_all_figures(
    kpi_summary: pl.DataFrame,
    hourly_summary: pl.DataFrame,
    out_dir: Path,
    ref_n_vans: int = 4,
    weather: str = "dry",
    day_type: str = "weekday",
) -> list[Path]:
    """Generate all Task 4.2 report figures (PDF + PNG) and return created paths."""
    paths: list[Path] = []
    paths.extend(plot_hourly_unserved(hourly_summary, out_dir, ref_n_vans=ref_n_vans))
    paths.extend(plot_fleet_pareto(kpi_summary, out_dir, weather=weather, day_type=day_type))
    paths.extend(plot_cost_efficiency(kpi_summary, out_dir, weather=weather, day_type=day_type))
    paths.extend(plot_kpi_summary(kpi_summary, out_dir, ref_n_vans=ref_n_vans, weather=weather, day_type=day_type))
    return paths
