from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import polars as pl
from matplotlib.colors import TwoSlopeNorm

from common import contracts
from common.config import (
    EXT,
    LAMBDA_PARQUET,
    MANHATTAN_POLYGON,
    OD_PARQUET,
    OUTPUTS,
    ROOT,
    STATIONS_JSON,
    TRIPS_CLEAN_PARQUET,
    WEATHER_CSV,
)
from pipeline.weather import estimate_rain_multiplier

REPORTS_DIR = ROOT / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"

ZONE_BINS = [
    ("Financial District\n(<40.713°N)", 40.695, 40.713),
    ("Tribeca / LES\n(40.713–40.725°N)", 40.713, 40.725),
    ("SoHo / East Village\n(40.725–40.738°N)", 40.725, 40.738),
    ("Chelsea / Gramercy\n(40.738–40.752°N)", 40.738, 40.752),
    ("Midtown (do 59th St)\n(≥40.752°N)", 40.752, 40.780),
]


def _station_table(stations: list) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "station_id": [s.station_id for s in stations],
            "name": [s.name for s in stations],
            "lat": [float(s.lat) for s in stations],
            "lon": [float(s.lon) for s in stations],
            "capacity": [int(s.capacity) for s in stations],
            "capacity_source": [s.capacity_source for s in stations],
            "n_neighbors": [len(s.neighbors) for s in stations],
        }
    )


def compute_station_peak_balance(lambda_df: pl.DataFrame, stations_df: pl.DataFrame) -> pl.DataFrame:
    """Wylicza średni dzienny bilans netto rowerów (zwroty - wypożyczenia) w szczycie porannym i popołudniowym."""
    wk = lambda_df.filter((pl.col("day_type") == "weekday") & (pl.col("station_id") != EXT))

    # Okno 15-min to 0.25h: liczba operacji w oknie = rate * 0.25
    am = (
        wk.filter(pl.col("slot").is_between(28, 39))  # 07:00 - 10:00
        .group_by("station_id")
        .agg(
            (pl.col("lambda").sum() * 0.25).alias("am_out"),
            (pl.col("return_rate").sum() * 0.25).alias("am_in"),
        )
        .with_columns((pl.col("am_in") - pl.col("am_out")).alias("am_net"))
    )

    pm = (
        wk.filter(pl.col("slot").is_between(64, 75))  # 16:00 - 19:00
        .group_by("station_id")
        .agg(
            (pl.col("lambda").sum() * 0.25).alias("pm_out"),
            (pl.col("return_rate").sum() * 0.25).alias("pm_in"),
        )
        .with_columns((pl.col("pm_in") - pl.col("pm_out")).alias("pm_net"))
    )

    daily = (
        wk.group_by("station_id")
        .agg(
            (pl.col("lambda_raw").sum() * 0.25).alias("daily_out_raw"),
            (pl.col("lambda").sum() * 0.25).alias("daily_out_corr"),
            (pl.col("return_rate").sum() * 0.25).alias("daily_in"),
            pl.col("exposure_frac").mean().alias("mean_exposure"),
            pl.col("exposure_frac").min().alias("min_exposure"),
        )
    )

    return (
        stations_df.join(am, on="station_id", how="left")
        .join(pm, on="station_id", how="left")
        .join(daily, on="station_id", how="left")
        .fill_null(0.0)
    )


def plot_spatial_asymmetry(peak_df: pl.DataFrame, out_dir: Path = FIGURES_DIR) -> tuple[Path, Path]:
    """Figura 1: Mapa asymetrii popytu (poranny wypływ vs popołudniowy napływ na dolnym Manhattanie)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    poly = np.array(MANHATTAN_POLYGON)

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 7.2), dpi=300, sharex=True, sharey=True)
    fig.patch.set_facecolor("white")

    lat = peak_df["lat"].to_numpy()
    lon = peak_df["lon"].to_numpy()
    am_net = peak_df["am_net"].to_numpy()
    pm_net = peak_df["pm_net"].to_numpy()

    vmax = float(np.percentile(np.abs(np.concatenate([am_net, pm_net])), 96))
    vmax = max(vmax, 5.0)
    norm = TwoSlopeNorm(vmin=-vmax, vcenter=0.0, vmax=vmax)

    panels = [
        (
            axes[0],
            am_net,
            "A. Poranny szczyt komunikacyjny (07:00–10:00, dni robocze)",
            "Wypływ z dzielnic mieszkalnych (czerwone) → napływ do Midtown i FiDi (niebieskie)",
        ),
        (
            axes[1],
            pm_net,
            "B. Popołudniowy szczyt komunikacyjny (16:00–19:00, dni robocze)",
            "Odwrócenie przepływów: wypływ z biurowców (czerwone) → powrót do stref mieszkalnych",
        ),
    ]

    DistrictLabels = [
        (-74.009, 40.706, "Financial\nDistrict"),
        (-73.984, 40.718, "Lower East\nSide"),
        (-74.002, 40.728, "SoHo /\nWest Village"),
        (-73.982, 40.729, "East\nVillage"),
        (-73.998, 40.746, "Chelsea /\nMidtown South"),
        (-73.979, 40.758, "Midtown\nEast/West"),
    ]

    sc = None
    for ax, net, title, subtitle in panels:
        ax.set_facecolor("#F6F8FB")
        ax.plot(poly[:, 0], poly[:, 1], color="#1E293B", lw=1.4, ls="--", alpha=0.75, label="Granica poligonu (59th St)")
        ax.fill(poly[:, 0], poly[:, 1], color="#FFFFFF", alpha=0.85, zorder=1)

        sizes = 18.0 + 3.2 * np.clip(np.abs(net), 0, vmax)
        order = np.argsort(np.abs(net))
        sc = ax.scatter(
            lon[order],
            lat[order],
            c=net[order],
            s=sizes[order],
            cmap="RdBu",
            norm=norm,
            alpha=0.88,
            edgecolors="#0F172A",
            linewidths=0.35,
            zorder=3,
        )

        for dlon, dlat, dlabel in DistrictLabels:
            ax.text(
                dlon,
                dlat,
                dlabel,
                fontsize=7.5,
                fontweight="bold",
                color="#334155",
                ha="center",
                va="center",
                bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "edgecolor": "none", "alpha": 0.65},
                zorder=4,
            )

        n_out = int((net < -3.0).sum())
        n_in = int((net > 3.0).sum())
        ax.text(
            0.03,
            0.97,
            f"Stacje silnego wypływu (Δ < -3): {n_out}\nStacje silnego napływu (Δ > +3): {n_in}",
            transform=ax.transAxes,
            va="top",
            ha="left",
            fontsize=8.5,
            bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "edgecolor": "#CBD5E1", "alpha": 0.92},
            zorder=5,
        )

        ax.set_title(title, fontsize=11, fontweight="bold", pad=16, color="#0F172A")
        ax.text(0.5, 1.015, subtitle, transform=ax.transAxes, ha="center", va="bottom", fontsize=8.5, color="#475569")
        ax.set_xlabel("Długość geograficzna (°E)", fontsize=9.5)
        ax.grid(True, linestyle=":", alpha=0.45, color="#94A3B8")
        ax.set_xlim(-74.027, -73.952)
        ax.set_ylim(40.696, 40.778)

    axes[0].set_ylabel("Szerokość geograficzna (°N)", fontsize=9.5)

    cbar = fig.colorbar(sc, ax=axes.ravel().tolist(), orientation="horizontal", fraction=0.045, pad=0.09, aspect=42)
    cbar.set_label(
        "Średni bilans netto stacji w 3-godzinnym oknie szczytu: Δ = zwroty − wypożyczenia [rowerów / dzień roboczy]\n"
        "(Czerwony = deficyt / pustoszenie stacji; Niebieski = nadmiar / zapełnianie doków)",
        fontsize=9.5,
    )

    fig.suptitle(
        "Dobowa Asymetria Przestrzenna Popytu Citi Bike NYC — Dolny Manhattan (Kalibracja: 1–23.09.2026)",
        fontsize=13,
        fontweight="bold",
        y=0.98,
        color="#0F172A",
    )

    png_path = out_dir / "fig1_spatial_asymmetry.png"
    pdf_path = out_dir / "fig1_spatial_asymmetry.pdf"
    fig.savefig(png_path, bbox_inches="tight", dpi=300)
    fig.savefig(pdf_path, bbox_inches="tight")
    plt.close(fig)
    return png_path, pdf_path


def plot_diurnal_asymmetry(lambda_df: pl.DataFrame, stations_df: pl.DataFrame, out_dir: Path = FIGURES_DIR) -> tuple[Path, Path]:
    """Figura 2: Dobowy profil popytu (dni robocze vs weekend) oraz mapa cieplna bilansu netto wg stref Manhattanu."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.8, 5.4), dpi=300, gridspec_kw={"width_ratios": [1.1, 1.15]})
    fig.patch.set_facecolor("white")

    internal = lambda_df.filter(pl.col("station_id") != EXT)
    by_slot = (
        internal.group_by("day_type", "slot")
        .agg(
            pl.col("lambda_raw").sum().alias("lam_raw_sum"),
            pl.col("lambda").sum().alias("lam_corr_sum"),
            pl.col("return_rate").sum().alias("ret_sum"),
        )
        .sort("day_type", "slot")
    )

    wk = by_slot.filter(pl.col("day_type") == "weekday")
    we = by_slot.filter(pl.col("day_type") == "weekend")
    hours_x = wk["slot"].to_numpy() * 0.25

    ax1.plot(hours_x, wk["lam_corr_sum"].to_numpy(), color="#D90429", lw=2.2, label="Dzień roboczy — skorygowane λ(t) [wyp./h]")
    ax1.plot(hours_x, wk["lam_raw_sum"].to_numpy(), color="#D90429", lw=1.3, ls="--", alpha=0.65, label="Dzień roboczy — surowe λ_raw(t)")
    ax1.plot(hours_x, we["lam_corr_sum"].to_numpy(), color="#2B9348", lw=2.0, label="Weekend / Święto — skorygowane λ(t) [wyp./h]")
    ax1.fill_between(
        hours_x,
        wk["lam_raw_sum"].to_numpy(),
        wk["lam_corr_sum"].to_numpy(),
        color="#EF233C",
        alpha=0.15,
        label="Korekta cenzurowania popytu (GBFS exposure)",
    )

    ax1.axvspan(7.0, 10.0, color="#FDE68A", alpha=0.28, label="Szczyt poranny (07:00–10:00)")
    ax1.axvspan(16.0, 19.0, color="#BFDBFE", alpha=0.28, label="Szczyt popołudniowy (16:00–19:00)")
    ax1.set_title("A. Dobowy profil natężenia wypożyczeń (383 stacje)", fontsize=11, fontweight="bold")
    ax1.set_xlabel("Godzina doby (America/New_York)", fontsize=9.5)
    ax1.set_ylabel("Sumaryczna intensywność wypożyczeń [rowerów / h]", fontsize=9.5)
    ax1.set_xlim(0, 24)
    ax1.set_xticks(range(0, 25, 2))
    ax1.grid(True, linestyle=":", alpha=0.5)
    ax1.legend(loc="upper left", fontsize=7.8, framealpha=0.92)

    # Panel B: Heatmapa stref szerokości geograficznej vs godzina doby (dni robocze)
    wk_st = (
        internal.filter(pl.col("day_type") == "weekday")
        .with_columns((pl.col("slot") // 4).cast(pl.Int8).alias("hour"))
        .join(stations_df.select("station_id", "lat"), on="station_id", how="inner")
    )

    mat = np.zeros((len(ZONE_BINS), 24), dtype=np.float64)
    zone_labels = []
    for zi, (zname, lat_lo, lat_hi) in enumerate(ZONE_BINS):
        sub = wk_st.filter((pl.col("lat") >= lat_lo) & (pl.col("lat") < lat_hi))
        n_st = max(1, sub["station_id"].n_unique())
        zone_labels.append(f"{zname}\n({n_st} stacji)")
        hr_agg = (
            sub.group_by("hour")
            .agg(((pl.col("return_rate") - pl.col("lambda")) * 0.25).sum().alias("net_bikes"))
            .sort("hour")
        )
        for r in hr_agg.iter_rows(named=True):
            mat[zi, int(r["hour"])] = float(r["net_bikes"]) / n_st

    vlim = max(float(np.abs(mat).max()), 1.0)
    im = ax2.imshow(mat, aspect="auto", cmap="RdBu", vmin=-vlim, vmax=vlim, origin="lower")
    ax2.set_title("B. Mapa cieplna bilansu netto wg strefy i godziny (dni robocze)", fontsize=11, fontweight="bold")
    ax2.set_xlabel("Godzina doby (0–23)", fontsize=9.5)
    ax2.set_xticks(range(0, 24, 2))
    ax2.set_yticks(range(len(ZONE_BINS)))
    ax2.set_yticklabels(zone_labels, fontsize=8.2)
    cbar = fig.colorbar(im, ax=ax2, fraction=0.046, pad=0.04)
    cbar.set_label("Średni bilans netto [rowerów / stację / h]\n(− wypływ / + napływ)", fontsize=8.5)

    fig.suptitle(
        "Dynamika Czasowo-Przestrzenna Popytu i Korekta Cenzurowania (Tygodnie 1–3 Września 2026)",
        fontsize=12.5,
        fontweight="bold",
        y=1.02,
    )
    fig.tight_layout()

    png_path = out_dir / "fig2_diurnal_asymmetry.png"
    pdf_path = out_dir / "fig2_diurnal_asymmetry.pdf"
    fig.savefig(png_path, bbox_inches="tight", dpi=300)
    fig.savefig(pdf_path, bbox_inches="tight")
    plt.close(fig)
    return png_path, pdf_path


def plot_calibration_and_baseline(
    trips_df: pl.DataFrame,
    lambda_df: pl.DataFrame,
    hourly_metrics_df: pl.DataFrame | None = None,
    out_dir: Path = FIGURES_DIR,
) -> tuple[Path, Path]:
    """Figura 3: Rozkład log-normalny czasów jazdy, profil ekspozycji GBFS oraz przebieg bazowy Scenariusza A."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(15.0, 4.6), dpi=300)
    fig.patch.set_facecolor("white")

    # Panel A: Czas przejazdu i dopasowanie log-normalne
    calib_durs = (
        trips_df.filter((pl.col("split") == "calib") & (pl.col("duration_s") >= 60) & (pl.col("duration_s") <= 3600))[
            "duration_s"
        ].to_numpy()
    )
    if calib_durs.size == 0:
        calib_durs = np.array([600.0, 900.0, 1200.0])
    durs_min = calib_durs / 60.0
    log_d = np.log(calib_durs)
    mu, sigma = float(log_d.mean()), max(float(log_d.std()), 0.1)

    ax = axes[0]
    ax.hist(durs_min, bins=48, range=(1, 60), density=True, color="#3B82F6", alpha=0.55, edgecolor="white", label="Dane empiryczne (1–23.09)")
    x_min = np.linspace(1.0, 60.0, 300)
    x_sec = x_min * 60.0
    pdf_sec = (1.0 / (x_sec * sigma * np.sqrt(2 * np.pi))) * np.exp(-((np.log(x_sec) - mu) ** 2) / (2 * sigma**2))
    pdf_min = pdf_sec * 60.0
    ax.plot(x_min, pdf_min, color="#DC2626", lw=2.2, label=f"Log-Normal (μ={mu:.2f}, σ={sigma:.2f})")
    med_min = float(np.median(durs_min))
    ax.axvline(med_min, color="#0F172A", ls="--", lw=1.4, label=f"Mediana = {med_min:.1f} min")
    ax.set_title("A. Rozkład czasu przejazdu (Kalibracja)", fontsize=10.5, fontweight="bold")
    ax.set_xlabel("Czas przejazdu [min]", fontsize=9)
    ax.set_ylabel("Gęstość prawdopodobieństwa", fontsize=9)
    ax.set_xlim(0, 60)
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(fontsize=8)

    # Panel B: Profil ekspozycji GBFS (exposure_frac) w ciągu doby
    ax = axes[1]
    exp_slot = (
        lambda_df.filter((pl.col("station_id") != EXT) & (pl.col("day_type") == "weekday"))
        .group_by("slot")
        .agg(
            pl.col("exposure_frac").mean().alias("mean_exp"),
            pl.col("exposure_frac").quantile(0.10).alias("q10_exp"),
            (pl.col("exposure_frac") < 1.0).mean().alias("frac_censored"),
        )
        .sort("slot")
    )
    hx = exp_slot["slot"].to_numpy() * 0.25
    ax.plot(hx, exp_slot["mean_exp"].to_numpy() * 100, color="#0284C7", lw=2.0, label="Średnia dostępność rowerów (exposure %)")
    ax.plot(hx, exp_slot["q10_exp"].to_numpy() * 100, color="#EA580C", lw=1.6, ls="--", label="10. percentyl stacji (stacje deficytowe)")
    ax.fill_between(hx, exp_slot["q10_exp"].to_numpy() * 100, exp_slot["mean_exp"].to_numpy() * 100, color="#38BDF8", alpha=0.18)
    ax.set_title("B. Profil ekspozycji stacji z GBFS (Dni robocze)", fontsize=10.5, fontweight="bold")
    ax.set_xlabel("Godzina doby", fontsize=9)
    ax.set_ylabel("Odsetek czasu z >0 rowerami [%]", fontsize=9)
    ax.set_xlim(0, 24)
    ax.set_ylim(15, 103)
    ax.set_xticks(range(0, 25, 4))
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(fontsize=8, loc="lower left")

    # Panel C: Przebieg bazowy Scenariusza A (nieobsłużony popyt i niedostępność stacji)
    ax = axes[2]
    if hourly_metrics_df is not None and not hourly_metrics_df.is_empty():
        sub_h = hourly_metrics_df.filter(pl.col("run_id").str.starts_with("A_n0_")).sort("hour")
        if sub_h.is_empty():
            sub_h = hourly_metrics_df.sort("hour").head(24)
        hrs = sub_h["hour"].to_numpy()
        ax.bar(hrs - 0.18, sub_h["fail_rent"].to_numpy(), width=0.36, color="#E11D48", label="Odrzucone wypożyczenia (brak rowerów)")
        ax.bar(hrs + 0.18, sub_h["fail_return"].to_numpy(), width=0.36, color="#7C3AED", label="Odrzucone zwroty (brak wolnych doków)")
        ax.set_title("C. Scenariusz A (Bazowy) — odrzuceni klienci / h", fontsize=10.5, fontweight="bold")
        ax.set_xlabel("Godzina symulacji (0–23)", fontsize=9)
        ax.set_ylabel("Liczba nieudanych operacji / h", fontsize=9)
        ax.set_xticks(range(0, 24, 3))
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.legend(fontsize=8, loc="upper left")
    else:
        ax.text(0.5, 0.5, "Brak danych Scenariusza A", ha="center", va="center", transform=ax.transAxes)

    fig.tight_layout()
    png_path = out_dir / "fig3_calibration_diagnostics.png"
    pdf_path = out_dir / "fig3_calibration_diagnostics.pdf"
    fig.savefig(png_path, bbox_inches="tight", dpi=300)
    fig.savefig(pdf_path, bbox_inches="tight")
    plt.close(fig)
    return png_path, pdf_path


def run_eda_pipeline(
    stations_path: Path = STATIONS_JSON,
    trips_path: Path = TRIPS_CLEAN_PARQUET,
    lambda_path: Path = LAMBDA_PARQUET,
    od_path: Path = OD_PARQUET,
    weather_path: Path = WEATHER_CSV,
    outputs_dir: Path = OUTPUTS,
    out_dir: Path = FIGURES_DIR,
) -> dict:
    """Uruchamia pełną analizę EDA, generuje 3 figury (PNG+PDF) i zapisuje podsumowanie JSON."""
    stations = contracts.validate_stations(stations_path).stations
    stations_df = _station_table(stations)
    trips_df = pl.read_parquet(trips_path)
    lambda_df = pl.read_parquet(lambda_path)
    od_df = pl.read_parquet(od_path)

    hourly_metrics_path = outputs_dir / "metrics" / "metrics_hourly.csv"
    runs_metrics_path = outputs_dir / "metrics" / "metrics_runs.csv"
    hourly_metrics_df = pl.read_csv(hourly_metrics_path) if hourly_metrics_path.exists() else None
    runs_metrics_df = pl.read_csv(runs_metrics_path) if runs_metrics_path.exists() else None

    peak_df = compute_station_peak_balance(lambda_df, stations_df)
    f1_png, _ = plot_spatial_asymmetry(peak_df, out_dir=out_dir)
    f2_png, _ = plot_diurnal_asymmetry(lambda_df, stations_df, out_dir=out_dir)
    f3_png, _ = plot_calibration_and_baseline(trips_df, lambda_df, hourly_metrics_df, out_dir=out_dir)

    calib = trips_df.filter(pl.col("split") == "calib")
    valid = trips_df.filter(pl.col("split") == "valid")
    wk_lam = lambda_df.filter((pl.col("day_type") == "weekday") & (pl.col("station_id") != EXT))

    raw_daily_sum = float((wk_lam["lambda_raw"].sum()) * 0.25)
    corr_daily_sum = float((wk_lam["lambda"].sum()) * 0.25)
    censor_uplift_pct = ((corr_daily_sum / raw_daily_sum) - 1.0) * 100.0 if raw_daily_sum > 0 else 0.0

    top_am_deficit = (
        peak_df.sort("am_net")
        .head(5)
        .select("station_id", "name", "capacity", "am_net", "pm_net")
        .to_dicts()
    )
    top_am_surplus = (
        peak_df.sort("am_net", descending=True)
        .head(5)
        .select("station_id", "name", "capacity", "am_net", "pm_net")
        .to_dicts()
    )

    scenario_a_stats = {}
    if runs_metrics_df is not None and not runs_metrics_df.is_empty():
        a_rows = runs_metrics_df.filter(pl.col("scenario") == "A")
        if not a_rows.is_empty():
            scenario_a_stats = a_rows.row(0, named=True)

    summary = {
        "n_stations": len(stations),
        "total_capacity": int(stations_df["capacity"].sum()),
        "mean_capacity": round(float(stations_df["capacity"].mean()), 1),
        "median_capacity": int(stations_df["capacity"].median()),
        "gbfs_capacity_stations": int((stations_df["capacity_source"] == "gbfs").sum()),
        "mean_neighbors_500m": round(float(stations_df["n_neighbors"].mean()), 1),
        "trips_clean_total": int(trips_df.height),
        "trips_calib_total": int(calib.height),
        "trips_valid_total": int(valid.height),
        "trips_calib_weekday": int(calib.filter(pl.col("day_type") == "weekday").height),
        "trips_calib_weekend": int(calib.filter(pl.col("day_type") == "weekend").height),
        "ext_origin_share_pct": round(float((calib["origin_id"] == EXT).mean() * 100.0), 2),
        "ext_dest_share_pct": round(float((calib["dest_id"] == EXT).mean() * 100.0), 2),
        "duration_median_s": round(float(calib["duration_s"].median()), 1),
        "duration_mean_s": round(float(calib["duration_s"].mean()), 1),
        "duration_log_mu": round(float(calib["duration_s"].cast(pl.Float64).log().mean()), 4),
        "duration_log_sigma": round(float(calib["duration_s"].cast(pl.Float64).log().std()), 4),
        "od_rows": int(od_df.height),
        "rain_multiplier": round(float(estimate_rain_multiplier(trips_path, weather_path)), 4),
        "mean_exposure_frac_weekday": round(float(wk_lam["exposure_frac"].mean()), 4),
        "min_exposure_frac_weekday": round(float(wk_lam["exposure_frac"].min()), 4),
        "censored_slots_share_pct": round(float((wk_lam["exposure_frac"] < 1.0).mean() * 100.0), 2),
        "weekday_expected_demand_raw": round(raw_daily_sum, 1),
        "weekday_expected_demand_corrected": round(corr_daily_sum, 1),
        "censoring_uplift_pct": round(censor_uplift_pct, 2),
        "top_am_deficit_stations": top_am_deficit,
        "top_am_surplus_stations": top_am_surplus,
        "scenario_a_baseline": scenario_a_stats,
        "figures": [
            str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
            for p in (f1_png, f2_png, f3_png)
        ],
    }

    summary_path = out_dir.parent / "eda_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary
