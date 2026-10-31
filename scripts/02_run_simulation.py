import argparse
import time

import polars as pl

from common import contracts
from common.config import (
    INIT_FILL,
    LAMBDA_PARQUET,
    OD_PARQUET,
    OUTPUTS,
    STATIONS_JSON,
    THR_HIGH_DEFAULT,
    THR_LOW_DEFAULT,
    VAN_CAPACITY_DEFAULT,
)
from engine.demand_gen import build_demand_table, generate_customer_stream
from engine.des import SimulationEngine, upsert_metrics
from pipeline.weather import weather_multiplier
from strategies.base import BaseStrategy, ScenarioA


def make_strategy(scenario: str, n_vans: int, van_capacity: int, thr_low: float, thr_high: float) -> BaseStrategy:
    if scenario == "A":
        return ScenarioA(van_capacity=van_capacity, thr_low=thr_low, thr_high=thr_high)
    return BaseStrategy(
        scenario=scenario,
        n_vans=n_vans,
        van_capacity=van_capacity,
        thr_low=thr_low,
        thr_high=thr_high,
        horizon_min=60 if scenario == "C" else 0,
    )


def run_single(
    scenario: str = "A",
    n_vans: int = 0,
    seed: int = 1,
    day_type: str = "weekday",
    weather: str = "dry",
    init_fill: float = INIT_FILL,
    van_capacity: int = VAN_CAPACITY_DEFAULT,
    thr_low: float = THR_LOW_DEFAULT,
    thr_high: float = THR_HIGH_DEFAULT,
):
    t0 = time.time()
    stations = contracts.validate_stations(STATIONS_JSON).stations
    station_ids = [s.station_id for s in stations]
    lambda_df = pl.read_parquet(LAMBDA_PARQUET)
    od_df = pl.read_parquet(OD_PARQUET)

    w_mult = weather_multiplier(weather)
    table = build_demand_table(station_ids, lambda_df, od_df, day_type=day_type, weather_mult=w_mult)
    stream = generate_customer_stream(table, seed=seed)

    eff_vans = 0 if scenario == "A" else int(n_vans)
    strategy = make_strategy(scenario, eff_vans, van_capacity, thr_low, thr_high)
    engine = SimulationEngine(
        stations=stations,
        strategy=strategy,
        seed=seed,
        day_type=day_type,
        weather=weather,
        init_fill=init_fill,
        stations_file=STATIONS_JSON.name,
    )
    res = engine.run(stream, extra_params={"weather_mult": round(w_mult, 4)})

    snap_dir = OUTPUTS / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / f"{scenario}_n{eff_vans}_s{seed}.json"
    contracts.write_json(res.snapshots, snap_path)
    contracts.validate_snapshots(snap_path, station_ids)

    runs_df, hourly_df = upsert_metrics(res, OUTPUTS / "metrics")
    contracts.validate_metrics(runs_df, hourly_df)

    r = res.run_row
    dt = time.time() - t0
    print(
        f"[{r['run_id']}] day={day_type} wx={weather}(x{w_mult:.2f}) | "
        f"demand={r['demand_total']:,} ok={r['rentals_ok']:,} "
        f"fail_rent={r['fail_rent']:,} (walk={r['walk_redirect']:,}) "
        f"fail_ret={r['fail_return']:,} | "
        f"empty_min={r['empty_station_min']:,.1f} full_min={r['full_station_min']:,.1f} | "
        f"{dt:.2f}s -> {snap_path.name}"
    )
    return res


def main():
    p = argparse.ArgumentParser(description="Run Citi Bike DES simulation (24h)")
    p.add_argument("--scenario", choices=["A", "B", "C"], default="A")
    p.add_argument("--n-vans", type=int, default=0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--day-type", choices=["weekday", "weekend"], default="weekday")
    p.add_argument("--weather", choices=["dry", "rain"], default="dry")
    p.add_argument("--init-fill", type=float, default=INIT_FILL)
    args = p.parse_args()
    run_single(
        scenario=args.scenario,
        n_vans=args.n_vans,
        seed=args.seed,
        day_type=args.day_type,
        weather=args.weather,
        init_fill=args.init_fill,
    )


if __name__ == "__main__":
    main()
