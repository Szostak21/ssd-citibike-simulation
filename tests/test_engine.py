from pathlib import Path

import numpy as np
import polars as pl

from common.config import EXT, SLOTS
from common.contracts import Station, validate_metrics, validate_snapshots
from engine.demand_gen import EXT_IDX, build_demand_table, generate_customer_stream
from engine.des import SimulationEngine, upsert_metrics
from pipeline.weather import weather_multiplier
from strategies.base import ScenarioA
from strategies.predictive import ScenarioC
from strategies.reactive import ScenarioB, van_speed_mps


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


def test_scenario_b_reactive_relocation(tmp_path: Path):
    assert van_speed_mps(8 * 3600, "weekday") < van_speed_mps(13 * 3600, "weekday")
    assert van_speed_mps(8 * 3600, "weekend") == van_speed_mps(13 * 3600, "weekday")

    stations = [
        Station(
            idx=0,
            station_id="S0",
            gbfs_id="u0",
            name="Residential Origin",
            lat=40.7200,
            lon=-74.0000,
            capacity=20,
            capacity_source="gbfs",
            aliases=[],
            neighbors=[],
        ),
        Station(
            idx=1,
            station_id="S1",
            gbfs_id="u1",
            name="Business Hub",
            lat=40.7350,
            lon=-73.9900,
            capacity=20,
            capacity_source="gbfs",
            aliases=[],
            neighbors=[],
        ),
    ]
    ids = ["S0", "S1"]

    # Asymetryczny strumień: z S0 jadą na S1 przez cały dzień (S0 pustoszeje <10%, S1 przepełnia się >90%)
    n_events = 160
    ts = np.linspace(6 * 3600 + 60, 20 * 3600, n_events).astype(np.int32)
    stream = {
        "t": ts,
        "origin": np.zeros(n_events, dtype=np.int32),
        "dest": np.ones(n_events, dtype=np.int32),
        "duration": np.full(n_events, 400, dtype=np.int32),
        "u_walk": np.full(n_events, 0.9, dtype=np.float64),
    }

    res_a = SimulationEngine(stations, strategy=ScenarioA(), seed=7).run(stream)
    strat_b = ScenarioB(n_vans=1, van_capacity=20, thr_low=0.1, thr_high=0.9)
    res_b = SimulationEngine(stations, strategy=strat_b, seed=7).run(stream)

    assert res_b.run_row["bikes_moved"] > 0
    assert res_b.run_row["van_km"] > 0.0
    assert len(res_b.snapshots["van_legs"]) >= 2
    actions = {leg["action"] for leg in res_b.snapshots["van_legs"]}
    assert actions == {"pickup", "dropoff"}

    # Scenariusz B powinien znacząco zmniejszyć liczbę odrzuceń względem Scenariusza A
    fails_a = res_a.run_row["fail_rent"] + res_a.run_row["fail_return"]
    fails_b = res_b.run_row["fail_rent"] + res_b.run_row["fail_return"]
    assert fails_b < fails_a
    assert res_b.run_row[" rentals_ok" if " rentals_ok" in res_b.run_row else "rentals_ok"] > res_a.run_row["rentals_ok"]

    from common.contracts import write_json

    snap_file = tmp_path / "B_n1_s7.json"
    write_json(res_b.snapshots, snap_file)
    validate_snapshots(snap_file, ids)

    runs_df, hourly_df = upsert_metrics(res_b, tmp_path)
    validate_metrics(runs_df, hourly_df)


def test_scenario_c_predictive_relocation(tmp_path: Path):
    stations = [
        Station(
            idx=0,
            station_id="S0",
            gbfs_id="u0",
            name="Residential Origin",
            lat=40.7200,
            lon=-74.0000,
            capacity=20,
            capacity_source="gbfs",
            aliases=[],
            neighbors=[],
        ),
        Station(
            idx=1,
            station_id="S1",
            gbfs_id="u1",
            name="Business Hub",
            lat=40.7350,
            lon=-73.9900,
            capacity=20,
            capacity_source="gbfs",
            aliases=[],
            neighbors=[],
        ),
    ]
    ids = ["S0", "S1"]

    # Gwałtowna poranna fala wypożyczeń z S0 do S1 startująca o 06:45 (slot 27..35)
    lam_out = np.zeros((2, SLOTS), dtype=np.float64)
    lam_in = np.zeros((2, SLOTS), dtype=np.float64)
    lam_out[0, 24:40] = 28.0  # wysoki wypływ z S0 od 06:00
    lam_in[1, 24:40] = 28.0  # wysoki napływ do S1 od 06:00

    n_events = 140
    ts = np.linspace(6 * 3600 + 45 * 60, 12 * 3600, n_events).astype(np.int32)
    stream = {
        "t": ts,
        "origin": np.zeros(n_events, dtype=np.int32),
        "dest": np.ones(n_events, dtype=np.int32),
        "duration": np.full(n_events, 420, dtype=np.int32),
        "u_walk": np.full(n_events, 0.9, dtype=np.float64),
    }

    strat_b = ScenarioB(n_vans=1, van_capacity=20, thr_low=0.1, thr_high=0.9, lam_out=lam_out, lam_in=lam_in)
    res_b = SimulationEngine(stations, strategy=strat_b, seed=11).run(stream)

    strat_c = ScenarioC(
        n_vans=1,
        van_capacity=20,
        thr_low=0.1,
        thr_high=0.9,
        horizon_min=60,
        lam_out=lam_out,
        lam_in=lam_in,
    )
    res_c = SimulationEngine(stations, strategy=strat_c, seed=11).run(stream)

    assert res_c.run_row["scenario"] == "C"
    assert res_c.run_row["horizon_min"] == 60
    assert res_c.run_row["bikes_moved"] > 0
    assert res_c.run_row["van_km"] > 0.0
    assert len(res_c.snapshots["van_legs"]) >= 2

    # W Scenariuszu C pierwszy kurs startuje wyprzedzająco o 06:00 (21600 s), zanim pojawi się alarm o 06:45+
    first_leg_c = res_c.snapshots["van_legs"][0]
    first_leg_b = res_b.snapshots["van_legs"][0]
    assert first_leg_c["t_start"] == 6 * 3600
    assert first_leg_c["t_start"] < first_leg_b["t_start"]

    # Dzięki wyprzedzającej relokacji Scenariusz C ma mniej odrzuceń niż Scenariusz B
    fails_b = res_b.run_row["fail_rent"] + res_b.run_row["fail_return"]
    fails_c = res_c.run_row["fail_rent"] + res_c.run_row["fail_return"]
    assert fails_c < fails_b
    assert res_c.run_row["rentals_ok"] >= res_b.run_row["rentals_ok"]

    from common.contracts import write_json

    snap_file = tmp_path / "C_n1_s11.json"
    write_json(res_c.snapshots, snap_file)
    validate_snapshots(snap_file, ids)

    runs_df, hourly_df = upsert_metrics(res_c, tmp_path)
    validate_metrics(runs_df, hourly_df)


def test_scenario_c_forecast_horizons_and_edge_cases():
    stations = _toy_stations()
    lam_out = np.full((2, SLOTS), 12.0, dtype=np.float64)
    lam_in = np.full((2, SLOTS), 6.0, dtype=np.float64)

    strat_c = ScenarioC(n_vans=2, van_capacity=15, horizon_min=90, lam_out=lam_out, lam_in=lam_in)
    eng = SimulationEngine(stations, strategy=strat_c, seed=5)
    strat_c.initialize(eng)

    # Sprawdzenie okna prognozy w środku doby (90 min = 1.5h -> exp_out = 18.0, exp_in = 9.0, net = -9.0)
    exp_out, exp_in, net_delta = strat_c._forecast_window(eng, 8 * 3600)
    assert np.allclose(exp_out, 18.0)
    assert np.allclose(exp_in, 9.0)
    assert np.allclose(net_delta, -9.0)

    # Sprawdzenie obcięcia prognozy na końcu doby (t = DAY_S)
    z_out, z_in, z_net = strat_c._forecast_window(eng, 86400)
    assert np.all(z_out == 0.0) and np.all(z_in == 0.0) and np.all(z_net == 0.0)


def test_van_shift_hours_and_return_retries_and_upsert(tmp_path: Path):
    stations = _toy_stations()
    strat_b = ScenarioB(n_vans=2, van_capacity=10)
    # Brak ruchu przed 06:00 i po 22:00: strumień tylko o 02:00 w nocy i 23:00 wieczorem
    stream = {
        "t": np.array([2 * 3600, 23 * 3600], dtype=np.int32),
        "origin": np.array([0, 0], dtype=np.int32),
        "dest": np.array([1, 1], dtype=np.int32),
        "duration": np.array([300, 300], dtype=np.int32),
        "u_walk": np.array([0.9, 0.9], dtype=np.float64),
    }
    eng = SimulationEngine(stations, strategy=strat_b, seed=3)
    eng.bikes[0] = 0  # alarm pustej stacji o 00:00
    res = eng.run(stream)

    # Przed 06:00 (kiedy t=0..5:45, indeksy 0..23) vany mają status "off", o 22:00+ wracają do "off"
    assert all(v["status"] == "off" for v in res.snapshots["frames"][0]["vans"])
    assert all(v["status"] == "off" for v in res.snapshots["frames"][-1]["vans"])

    # Test maksymalnej liczby przekierowań zwrotów (MAX_RETURN_RETRIES), gdy wszystkie stacje są pełne
    eng_full = SimulationEngine(stations, strategy=ScenarioA(), seed=4, init_fill=1.0)
    stream_ret = {
        "t": np.array([1000], dtype=np.int32),
        "origin": np.array([EXT_IDX], dtype=np.int32),
        "dest": np.array([0], dtype=np.int32),
        "duration": np.array([200], dtype=np.int32),
        "u_walk": np.array([0.5], dtype=np.float64),
    }
    res_full = eng_full.run(stream_ret)
    assert res_full.run_row["fail_return"] == 1
    assert res_full.run_row["full_station_min"] > 0

    # Test idempotentności upsert_metrics przy wielokrotnym zapisie tego samego run_id
    upsert_metrics(res_full, tmp_path)
    runs_df, hourly_df = upsert_metrics(res_full, tmp_path)
    assert runs_df.height == 1
    assert hourly_df.height == 24


