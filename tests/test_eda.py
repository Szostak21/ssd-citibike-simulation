from pathlib import Path

import numpy as np
import polars as pl

from common.config import EXT, SLOTS
from common.contracts import write_json, write_parquet
from viz.eda import run_eda_pipeline


def _sample_eda_inputs(tmp_path: Path):
    stations_payload = {
        "schema_version": "1.0",
        "generated_at": "2026-10-31T00:00:00+00:00",
        "source_month": "2026-09",
        "stations": [
            {
                "idx": 0,
                "station_id": "5001.01",
                "gbfs_id": "g0",
                "name": "Financial Dist St",
                "lat": 40.7080,
                "lon": -74.0100,
                "capacity": 35,
                "capacity_source": "gbfs",
                "aliases": [],
                "neighbors": ["5002.01"],
            },
            {
                "idx": 1,
                "station_id": "5002.01",
                "gbfs_id": "g1",
                "name": "East Village Ave",
                "lat": 40.7280,
                "lon": -73.9850,
                "capacity": 28,
                "capacity_source": "gbfs",
                "aliases": [],
                "neighbors": ["5001.01"],
            },
        ],
    }
    st_path = tmp_path / "stations.json"
    write_json(stations_payload, st_path)

    rows_lam = []
    for sid in ("5001.01", "5002.01", EXT):
        for dt in ("weekday", "weekend"):
            for s in range(SLOTS):
                is_am = 28 <= s <= 39
                is_pm = 64 <= s <= 75
                if sid == "5002.01":
                    l_raw = 18.0 if is_am else (6.0 if is_pm else 4.0)
                    r_rate = 5.0 if is_am else (16.0 if is_pm else 4.0)
                    exp_f = 0.75 if is_am else 0.95
                elif sid == "5001.01":
                    l_raw = 5.0 if is_am else (19.0 if is_pm else 4.0)
                    r_rate = 17.0 if is_am else (6.0 if is_pm else 4.0)
                    exp_f = 0.85 if is_pm else 0.98
                else:
                    l_raw, r_rate, exp_f = 3.0, 3.0, 1.0
                rows_lam.append(
                    {
                        "station_id": sid,
                        "day_type": dt,
                        "slot": s,
                        "rent_count": 10,
                        "n_days": 15,
                        "lambda_raw": l_raw,
                        "exposure_frac": exp_f,
                        "lambda": l_raw / exp_f,
                        "return_rate": r_rate,
                    }
                )
    lam_df = pl.DataFrame(rows_lam).with_columns(
        pl.col("slot").cast(pl.Int16),
        pl.col("rent_count").cast(pl.Int32),
        pl.col("n_days").cast(pl.Int16),
    )
    lam_path = tmp_path / "lambda_15min.parquet"
    write_parquet(lam_df, lam_path)

    trips_df = pl.DataFrame(
        {
            "ride_id": [f"r{i}" for i in range(40)],
            "rideable_type": ["classic_bike"] * 40,
            "member_casual": ["member"] * 40,
            "duration_s": np.linspace(180, 1800, 40).astype(np.int32),
            "origin_id": ["5002.01"] * 25 + ["5001.01"] * 10 + [EXT] * 5,
            "dest_id": ["5001.01"] * 25 + ["5002.01"] * 10 + ["5001.01"] * 5,
            "day_type": ["weekday"] * 30 + ["weekend"] * 10,
            "split": ["calib"] * 32 + ["valid"] * 8,
            "slot": [32] * 20 + [68] * 20,
            "end_slot": [33] * 20 + [69] * 20,
            "hour": [8] * 20 + [17] * 20,
        }
    )
    trips_path = tmp_path / "trips_clean.parquet"
    write_parquet(trips_df, trips_path)

    od_df = pl.DataFrame(
        {
            "day_type": ["weekday", "weekday"],
            "hour": pl.Series([8, 17], dtype=pl.Int8),
            "origin_id": ["5002.01", "5001.01"],
            "dest_id": ["5001.01", "5002.01"],
            "n_trips": pl.Series([20, 15], dtype=pl.Int32),
            "prob": [1.0, 1.0],
            "dur_mu": [6.4, 6.4],
            "dur_sigma": [0.35, 0.35],
            "dur_median_s": [600.0, 600.0],
        }
    )
    od_path = tmp_path / "od_matrix.parquet"
    write_parquet(od_df, od_path)

    return st_path, trips_path, lam_path, od_path


def test_eda_peak_balance_and_plots(tmp_path: Path):
    st_path, trips_path, lam_path, od_path = _sample_eda_inputs(tmp_path)
    out_figs = tmp_path / "reports" / "figures"
    summary = run_eda_pipeline(
        stations_path=st_path,
        trips_path=trips_path,
        lambda_path=lam_path,
        od_path=od_path,
        weather_path=tmp_path / "missing_wx.csv",
        outputs_dir=tmp_path / "outputs",
        out_dir=out_figs,
    )
    assert summary["n_stations"] == 2
    assert summary["censoring_uplift_pct"] > 0
    assert (out_figs / "fig1_spatial_asymmetry.png").exists()
    assert (out_figs / "fig1_spatial_asymmetry.pdf").exists()
    assert (out_figs / "fig2_diurnal_asymmetry.png").exists()
    assert (out_figs / "fig3_calibration_diagnostics.png").exists()
    assert (tmp_path / "reports" / "eda_summary.json").exists()
