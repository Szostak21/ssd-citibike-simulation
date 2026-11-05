from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from common.config import EXT, SLOTS
from common.contracts import Station, write_json, write_parquet
from engine.des import SimulationEngine
from pipeline.gbfs_collector import parse_status, write_parquet_snapshot
from viz.validation import (
    EmpiricalRelocationStrategy,
    _compute_profile_errors,
    reconstruct_gbfs_and_hidden_relocation,
    run_validation_pipeline,
    validate_stage1_demand_and_durations,
    validate_stage2_occupancy_and_relocation,
)


def _sample_validation_inputs(tmp_path: Path):
    stations_payload = {
        "schema_version": "1.0",
        "generated_at": "2026-10-31T00:00:00+00:00",
        "source_month": "2026-09",
        "stations": [
            {
                "idx": 0,
                "station_id": "5001.01",
                "gbfs_id": "g0",
                "name": "Residential South",
                "lat": 40.7220,
                "lon": -74.0000,
                "capacity": 30,
                "capacity_source": "gbfs",
                "aliases": [],
                "neighbors": ["5002.01"],
            },
            {
                "idx": 1,
                "station_id": "5002.01",
                "gbfs_id": "g1",
                "name": "Midtown North",
                "lat": 40.7580,
                "lon": -73.9800,
                "capacity": 30,
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
                hr = s // 4
                is_am = 7 <= hr < 10
                is_pm = 16 <= hr < 19
                if sid == "5001.01":
                    l_raw = 16.0 if is_am else (6.0 if is_pm else 4.0)
                    r_rate = 5.0 if is_am else (15.0 if is_pm else 4.0)
                elif sid == "5002.01":
                    l_raw = 5.0 if is_am else (16.0 if is_pm else 4.0)
                    r_rate = 15.0 if is_am else (5.0 if is_pm else 4.0)
                else:
                    l_raw, r_rate = 2.0, 2.0
                rows_lam.append(
                    {
                        "station_id": sid,
                        "day_type": dt,
                        "slot": s,
                        "rent_count": 12,
                        "n_days": 15,
                        "lambda_raw": l_raw,
                        "exposure_frac": 1.0,
                        "lambda": l_raw,
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

    rows_od = []
    for dt in ("weekday", "weekend"):
        for hr in range(24):
            rows_od.append(
                {
                    "day_type": dt,
                    "hour": hr,
                    "origin_id": "5001.01",
                    "dest_id": "5002.01",
                    "n_trips": 15,
                    "prob": 1.0,
                    "dur_mu": 6.2,
                    "dur_sigma": 0.3,
                    "dur_median_s": float(np.exp(6.2)),
                }
            )
            rows_od.append(
                {
                    "day_type": dt,
                    "hour": hr,
                    "origin_id": "5002.01",
                    "dest_id": "5001.01",
                    "n_trips": 15,
                    "prob": 1.0,
                    "dur_mu": 6.2,
                    "dur_sigma": 0.3,
                    "dur_median_s": float(np.exp(6.2)),
                }
            )
            rows_od.append(
                {
                    "day_type": dt,
                    "hour": hr,
                    "origin_id": EXT,
                    "dest_id": "5001.01",
                    "n_trips": 5,
                    "prob": 1.0,
                    "dur_mu": 6.2,
                    "dur_sigma": 0.3,
                    "dur_median_s": float(np.exp(6.2)),
                }
            )
    od_df = pl.DataFrame(rows_od).with_columns(
        pl.col("hour").cast(pl.Int8),
        pl.col("n_trips").cast(pl.Int32),
    )
    od_path = tmp_path / "od_matrix.parquet"
    write_parquet(od_df, od_path)

    # Syntetyczne przejazdy z podziałem na calib i valid (Tydzień 4: 2026-09-24 i 2026-09-28)
    rng = np.random.default_rng(123)
    trip_rows = []
    idx_counter = 0
    for split_name, date_str, mult in [("calib", "2026-09-10", 1.0), ("valid", "2026-09-24", 1.0), ("valid", "2026-09-28", 0.55)]:
        for hr in range(24):
            n_hr = max(2, round((14 if 7 <= hr <= 19 else 5) * mult))
            for _ in range(n_hr):
                idx_counter += 1
                dur_s = int(np.clip(rng.lognormal(6.2, 0.3), 90, 3200))
                start_dt = datetime(2026, 9, int(date_str.split("-")[2]), hr, 10, 0)
                end_hr = min(23, hr + max(0, dur_s // 3600))
                end_dt = datetime(2026, 9, int(date_str.split("-")[2]), end_hr, 25, 0)
                orig = "5001.01" if hr < 12 else "5002.01"
                dest = "5002.01" if hr < 12 else "5001.01"
                trip_rows.append(
                    {
                        "ride_id": f"r_{idx_counter}",
                        "rideable_type": "classic_bike",
                        "member_casual": "member",
                        "started_at": start_dt,
                        "ended_at": end_dt,
                        "duration_s": dur_s,
                        "origin_id": orig,
                        "dest_id": dest,
                        "date": start_dt.date(),
                        "day_type": "weekday",
                        "split": split_name,
                        "slot": hr * 4,
                        "end_slot": end_hr * 4,
                        "hour": hr,
                    }
                )

    trips_df = pl.DataFrame(trip_rows).with_columns(
        pl.col("duration_s").cast(pl.Int32),
        pl.col("slot").cast(pl.Int16),
        pl.col("end_slot").cast(pl.Int16),
        pl.col("hour").cast(pl.Int8),
    )
    trips_path = tmp_path / "trips_clean.parquet"
    write_parquet(trips_df, trips_path)

    # Plik pogodowy wskazujący deszcz w dniu 2026-09-28
    wx_rows = []
    for d_str in ("2026-09-10", "2026-09-24", "2026-09-28"):
        for hr in range(24):
            precip = 2.0 if (d_str == "2026-09-28" and 8 <= hr <= 16) else 0.0
            wx_rows.append({"time": f"{d_str}T{hr:02d}:00", "temp_c": 20.0, "precip_mm": precip})
    wx_path = tmp_path / "weather_hourly.csv"
    pl.DataFrame(wx_rows).write_csv(wx_path)

    # Syntetyczne migawki GBFS w dzień roboczy (2026-10-28, środa)
    gbfs_dir = tmp_path / "gbfs_status"
    mapping = {"g0": "5001.01", "g1": "5002.01"}
    for h_utc in (12, 13, 14, 15):
        ts = int(datetime(2026, 10, 28, h_utc, 0, tzinfo=UTC).timestamp())
        payload = {
            "last_updated": ts,
            "data": {
                "stations": [
                    {
                        "station_id": "g0",
                        "last_reported": ts,
                        "num_bikes_available": 14,
                        "num_docks_available": 16,
                        "is_renting": 1,
                        "is_returning": 1,
                    },
                    {
                        "station_id": "g1",
                        "last_reported": ts,
                        "num_bikes_available": 18,
                        "num_docks_available": 12,
                        "is_renting": 1,
                        "is_returning": 1,
                    },
                ]
            },
        }
        write_parquet_snapshot(parse_status(payload, mapping), gbfs_dir)

    return st_path, trips_path, lam_path, od_path, wx_path, gbfs_dir


def test_compute_profile_errors_metrics():
    x = np.array([10.0, 20.0, 30.0, 40.0])
    y = np.array([10.0, 20.0, 30.0, 40.0])
    err_zero = _compute_profile_errors(x, y)
    assert err_zero["mae"] == 0.0
    assert err_zero["rmse"] == 0.0
    assert err_zero["mape_pct"] == 0.0
    assert err_zero["pearson_r"] == 1.0

    x2 = np.array([12.0, 22.0, 28.0, 38.0])
    err = _compute_profile_errors(x2, y)
    assert err["mae"] == 2.0
    assert err["rmse"] == 2.0
    assert err["pearson_r"] > 0.98


def test_stage1_and_stage2_validation_functions(tmp_path: Path):
    st_path, trips_path, lam_path, od_path, wx_path, gbfs_dir = _sample_validation_inputs(tmp_path)
    from common.contracts import validate_stations

    stations = validate_stations(st_path).stations
    trips_df = pl.read_parquet(trips_path)
    lam_df = pl.read_parquet(lam_path)
    od_df = pl.read_parquet(od_path)

    s1 = validate_stage1_demand_and_durations(
        stations=stations,
        trips_df=trips_df,
        lambda_df=lam_df,
        od_df=od_df,
        weather_path=wx_path,
        n_reps=3,
    )
    assert s1["n_weekdays_total"] == 2
    assert s1["n_weekdays_dry"] == 1
    assert s1["n_weekdays_rain"] == 1
    assert "mae" in s1["metrics_rentals_dry"]
    assert "pearson_r" in s1["metrics_returns_dry"]
    assert 0.0 <= s1["duration_validation"]["ks_stat_n500"] <= 1.0

    recon = reconstruct_gbfs_and_hidden_relocation(stations, lam_df, gbfs_dir=gbfs_dir)
    assert recon["has_gbfs"] is True
    assert recon["gbfs_bikes"].shape == (2, SLOTS)
    assert recon["hidden_reloc"].shape == (2, SLOTS)
    # Sprawdzenie tożsamości bilansu: hidden_reloc + csv_net_slot == gbfs_delta
    assert np.allclose(recon["hidden_reloc"] + recon["csv_net_slot"], recon["gbfs_delta"])

    s2 = validate_stage2_occupancy_and_relocation(
        stations=stations,
        lambda_df=lam_df,
        od_df=od_df,
        gbfs_dir=gbfs_dir,
        seed=7,
    )
    assert len(s2["curves_15min"]["hours_x"]) == SLOTS
    assert s2["occupancy_metrics"]["midtown_fill_injected_vs_gbfs"]["mae"] <= (
        s2["occupancy_metrics"]["midtown_fill_no_reloc_vs_gbfs"]["mae"] + 1e-6
    )


def test_empirical_relocation_strategy_tracks_target():
    stations = [
        Station(
            idx=0,
            station_id="S0",
            gbfs_id="g0",
            name="S0",
            lat=40.72,
            lon=-74.00,
            capacity=20,
            capacity_source="gbfs",
            aliases=[],
            neighbors=[],
        )
    ]
    init_bikes = np.array([5], dtype=np.int32)
    target = np.full((1, SLOTS), 15.0, dtype=np.float64)
    delta = np.zeros((1, SLOTS), dtype=np.float64)
    delta[0, 0] = 10.0

    strat = EmpiricalRelocationStrategy(init_bikes=init_bikes, slot_reloc_delta=delta, target_gbfs_bikes=target)
    empty_stream = {
        "t": np.empty(0, dtype=np.int32),
        "origin": np.empty(0, dtype=np.int32),
        "dest": np.empty(0, dtype=np.int32),
        "duration": np.empty(0, dtype=np.int32),
        "u_walk": np.empty(0, dtype=np.float64),
    }
    res = SimulationEngine(stations, strategy=strat, seed=1).run(empty_stream)
    assert res.snapshots["frames"][0]["bikes"][0] == 5
    assert res.snapshots["frames"][2]["bikes"][0] == 15
    assert res.run_row["bikes_moved"] == 10


def test_validation_pipeline_end_to_end(tmp_path: Path):
    st_path, trips_path, lam_path, od_path, wx_path, gbfs_dir = _sample_validation_inputs(tmp_path)
    out_figs = tmp_path / "reports" / "figures"
    summary = run_validation_pipeline(
        stations_path=st_path,
        trips_path=trips_path,
        lambda_path=lam_path,
        od_path=od_path,
        weather_path=wx_path,
        gbfs_dir=gbfs_dir,
        out_dir=out_figs,
        n_reps=2,
    )
    assert summary["hypothesis"] == "H0"
    assert (out_figs / "fig4_week4_validation.png").exists()
    assert (out_figs / "fig4_week4_validation.pdf").exists()
    assert (tmp_path / "reports" / "validation_summary.json").exists()
