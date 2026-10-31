from pathlib import Path

import numpy as np
import polars as pl

from common.config import EXT, SLOTS
from common.contracts import Station, validate_metrics, validate_snapshots
from engine.demand_gen import EXT_IDX, build_demand_table, generate_customer_stream
from engine.des import SimulationEngine, upsert_metrics
from pipeline.weather import weather_multiplier
from strategies.base import ScenarioA


def _toy_stations():
    return [
        Station(
            idx=0,
            station_id="S0",
            gbfs_id="u0",
            name="Station 0",
            lat=40.7200,
            lon=-74.0000,
            capacity=10,
            capacity_source="gbfs",
            aliases=[],
            neighbors=["S1"],
        ),
        Station(
            idx=1,
            station_id="S1",
            gbfs_id="u1",
            name="Station 1",
            lat=40.7220,
            lon=-74.0000,
            capacity=10,
            capacity_source="gbfs",
            aliases=[],
            neighbors=["S0"],
        ),
    ]


def _toy_tables():
    ids = ["S0", "S1"]
    rows_lam = []
    for sid in [*ids, EXT]:
        for dt in ("weekday", "weekend"):
            for s in range(SLOTS):
                lam_val = 20.0 if (sid == "S0" and 32 <= s < 40) else (8.0 if sid != EXT else 4.0)
                rows_lam.append(
                    {
                        "station_id": sid,
                        "day_type": dt,
                        "slot": s,
                        "rent_count": 10,
                        "n_days": 15,
                        "lambda_raw": lam_val,
                        "exposure_frac": 1.0,
                        "lambda": lam_val,
                        "return_rate": 8.0,
                    }
                )
    lam_df = pl.DataFrame(rows_lam).with_columns(
        pl.col("slot").cast(pl.Int16),
        pl.col("rent_count").cast(pl.Int32),
        pl.col("n_days").cast(pl.Int16),
    )

    rows_od = []
    for dt in ("weekday", "weekend"):
        for hr in range(24):
            rows_od.append(
                {
                    "day_type": dt,
                    "hour": hr,
                    "origin_id": "S0",
                    "dest_id": "S1",
                    "n_trips": 10,
                    "prob": 0.8,
                    "dur_mu": 5.5,
                    "dur_sigma": 0.3,
                    "dur_median_s": float(np.exp(5.5)),
                }
            )
            rows_od.append(
                {
                    "day_type": dt,
                    "hour": hr,
                    "origin_id": "S0",
                    "dest_id": EXT,
                    "n_trips": 2,
                    "prob": 0.2,
                    "dur_mu": 6.0,
                    "dur_sigma": 0.3,
                    "dur_median_s": float(np.exp(6.0)),
                }
            )
            rows_od.append(
                {
                    "day_type": dt,
                    "hour": hr,
                    "origin_id": "S1",
                    "dest_id": "S0",
                    "n_trips": 10,
                    "prob": 1.0,
                    "dur_mu": 5.5,
                    "dur_sigma": 0.3,
                    "dur_median_s": float(np.exp(5.5)),
                }
            )
            rows_od.append(
                {
                    "day_type": dt,
                    "hour": hr,
                    "origin_id": EXT,
                    "dest_id": "S0",
                    "n_trips": 5,
                    "prob": 1.0,
                    "dur_mu": 6.0,
                    "dur_sigma": 0.3,
                    "dur_median_s": float(np.exp(6.0)),
                }
            )
    od_df = pl.DataFrame(rows_od).with_columns(
        pl.col("hour").cast(pl.Int8),
        pl.col("n_trips").cast(pl.Int32),
    )
    return ids, lam_df, od_df


def test_crn_determinism_and_nhpp_rate():
    ids, lam_df, od_df = _toy_tables()
    tbl = build_demand_table(ids, lam_df, od_df, day_type="weekday", weather_mult=1.0)
    s1 = generate_customer_stream(tbl, seed=42)
    s2 = generate_customer_stream(tbl, seed=42)
    s3 = generate_customer_stream(tbl, seed=43)

    for k in s1:
        assert np.array_equal(s1[k], s2[k]), f"CRN mismatch on {k}"
    assert not np.array_equal(s1["t"], s3["t"])

    expected_total = float((tbl.lam * 0.25).sum())
    counts = [generate_customer_stream(tbl, seed=s)["t"].size for s in range(1, 21)]
    mean_cnt = float(np.mean(counts))
    assert abs(mean_cnt - expected_total) / expected_total < 0.05


def test_des_mechanics_and_contracts(tmp_path: Path):
    stations = _toy_stations()
    ids, lam_df, od_df = _toy_tables()
    tbl = build_demand_table(ids, lam_df, od_df, day_type="weekday")
    stream = generate_customer_stream(tbl, seed=7)

    eng1 = SimulationEngine(stations, strategy=ScenarioA(), seed=7)
    res1 = eng1.run(stream)

    eng2 = SimulationEngine(stations, strategy=ScenarioA(), seed=7)
    res2 = eng2.run(stream)
    assert res1.run_row == res2.run_row

    for f in res1.snapshots["frames"]:
        for b, s in zip(f["bikes"], stations):
            assert 0 <= b <= s.capacity

    snap_file = tmp_path / "A_n0_s7.json"
    from common.contracts import write_json

    write_json(res1.snapshots, snap_file)
    validate_snapshots(snap_file, ids)

    runs_df, hourly_df = upsert_metrics(res1, tmp_path)
    validate_metrics(runs_df, hourly_df)
    assert runs_df.height == 1 and hourly_df.height == 24


def test_walk_redirect_and_full_station_redirect():
    stations = _toy_stations()
    # S0 starts with 0 bikes, S1 starts with 10 bikes (full)
    eng = SimulationEngine(stations, strategy=ScenarioA(), seed=1, init_fill=0.0)
    eng.bikes[0] = 0
    eng.bikes[1] = 10

    # Arrival at empty S0 with u_walk=0.1 (<0.4) -> should walk to S1 and rent a bike
    # Then return to full S1 -> should fail_return at S1 and redirect to S0
    stream = {
        "t": np.array([100, 200], dtype=np.int32),
        "origin": np.array([0, EXT_IDX], dtype=np.int32),
        "dest": np.array([0, 1], dtype=np.int32),
        "duration": np.array([600, 100], dtype=np.int32),
        "u_walk": np.array([0.1, 0.9], dtype=np.float64),
    }
    res = eng.run(stream)
    r = res.run_row
    assert r["fail_rent"] == 1
    assert r["walk_redirect"] == 1
    assert r["rentals_ok"] == 1
    assert r["fail_return"] == 1
    assert r["empty_station_min"] > 0


def test_weather_multiplier():
    assert weather_multiplier("dry") == 1.0
    m = weather_multiplier("rain")
    assert 0.5 <= m <= 0.95
