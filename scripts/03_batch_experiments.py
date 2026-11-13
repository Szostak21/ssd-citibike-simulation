from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl
from scipy import stats

from common import contracts
from common.config import (
    HORIZON_MIN_DEFAULT,
    INIT_FILL,
    LAMBDA_PARQUET,
    OD_PARQUET,
    OUTPUTS,
    SLOTS,
    STATIONS_JSON,
    THR_HIGH_DEFAULT,
    THR_LOW_DEFAULT,
    VAN_CAPACITY_DEFAULT,
)
from engine.demand_gen import DemandTable, build_demand_table, generate_customer_stream
from engine.des import SimResult, SimulationEngine, format_run_id, upsert_metrics_batch
from pipeline.weather import weather_multiplier
from strategies.base import BaseStrategy, ScenarioA
from strategies.predictive import ScenarioC
from strategies.reactive import ScenarioB


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    scenario: str
    n_vans: int
    van_capacity: int = VAN_CAPACITY_DEFAULT
    thr_low: float = THR_LOW_DEFAULT
    thr_high: float = THR_HIGH_DEFAULT
    horizon_min: int = HORIZON_MIN_DEFAULT
    weather: str = "dry"
    day_type: str = "weekday"
    block: str = "core_scaling"

    @property
    def effective_horizon_min(self) -> int:
        return int(self.horizon_min) if self.scenario == "C" else 0

    def is_baseline(self) -> bool:
        return (
            self.day_type == "weekday"
            and self.weather == "dry"
            and int(self.van_capacity) == VAN_CAPACITY_DEFAULT
            and round(float(self.thr_low), 4) == THR_LOW_DEFAULT
            and round(float(self.thr_high), 4) == THR_HIGH_DEFAULT
            and (self.scenario != "C" or int(self.horizon_min) == HORIZON_MIN_DEFAULT)
        )

    def run_id(self, seed: int) -> str:
        return format_run_id(
            self.scenario,
            self.n_vans,
            seed,
            van_capacity=self.van_capacity,
            thr_low=self.thr_low,
            thr_high=self.thr_high,
            horizon_min=self.effective_horizon_min,
            weather=self.weather,
            day_type=self.day_type,
        )


def build_return_rate_matrix(
    station_ids: list[str],
    lambda_df: pl.DataFrame,
    day_type: str,
    weather_mult: float,
) -> np.ndarray:
    n = len(station_ids)
    id_to_idx = {sid: i for i, sid in enumerate(station_ids)}
    sub = lambda_df.filter((pl.col("day_type") == day_type) & pl.col("station_id").is_in(station_ids))
    ret = np.zeros((n, SLOTS), dtype=np.float64)
    if not sub.is_empty():
        s_idx = sub["station_id"].replace_strict(id_to_idx).to_numpy()
        slots = sub["slot"].to_numpy()
        ret[s_idx, slots] = sub["return_rate"].to_numpy() * float(weather_mult)
    return ret


def make_strategy(
    cfg: ExperimentConfig,
    lam_out: np.ndarray | None = None,
    lam_in: np.ndarray | None = None,
) -> BaseStrategy:
    if cfg.scenario == "A":
        return ScenarioA(van_capacity=cfg.van_capacity, thr_low=cfg.thr_low, thr_high=cfg.thr_high)
    if cfg.scenario == "B":
        return ScenarioB(
            n_vans=cfg.n_vans,
            van_capacity=cfg.van_capacity,
            thr_low=cfg.thr_low,
            thr_high=cfg.thr_high,
            lam_out=lam_out,
            lam_in=lam_in,
        )
    if cfg.scenario == "C":
        return ScenarioC(
            n_vans=cfg.n_vans,
            van_capacity=cfg.van_capacity,
            thr_low=cfg.thr_low,
            thr_high=cfg.thr_high,
            horizon_min=cfg.horizon_min,
            lam_out=lam_out,
            lam_in=lam_in,
        )
    return BaseStrategy(
        scenario=cfg.scenario,
        n_vans=cfg.n_vans,
        van_capacity=cfg.van_capacity,
        thr_low=cfg.thr_low,
        thr_high=cfg.thr_high,
        horizon_min=cfg.effective_horizon_min,
    )


def build_experiment_grid(
    suite: str = "all",
    fleet_sizes: tuple[int, ...] = (1, 2, 4, 6, 8),
    rain_fleet_sizes: tuple[int, ...] = (2, 4, 6),
) -> list[ExperimentConfig]:
    """Buduje siatkę konfiguracji eksperymentalnych pod hipotezy H1–H6 oraz analizę wrażliwości."""
    configs: list[ExperimentConfig] = []

    if suite in ("all", "core"):
        # Blok 1 & 2: Porównanie A vs B vs C (N=4) oraz skalowanie floty N in {1, 2, 4, 6, 8} (H1–H5)
        configs.append(ExperimentConfig(scenario="A", n_vans=0, horizon_min=0, block="core_scaling"))
        for n_v in fleet_sizes:
            configs.append(ExperimentConfig(scenario="B", n_vans=n_v, horizon_min=0, block="core_scaling"))
        for n_v in fleet_sizes:
            configs.append(
                ExperimentConfig(
                    scenario="C",
                    n_vans=n_v,
                    horizon_min=HORIZON_MIN_DEFAULT,
                    block="core_scaling",
                )
            )

    if suite in ("all", "sensitivity"):
        # Blok 3a: Wpływ pogody (deszcz vs brak opadów — Hipoteza H6)
        configs.append(
            ExperimentConfig(scenario="A", n_vans=0, horizon_min=0, weather="rain", block="weather_rain")
        )
        for n_v in rain_fleet_sizes:
            configs.append(
                ExperimentConfig(
                    scenario="B",
                    n_vans=n_v,
                    horizon_min=0,
                    weather="rain",
                    block="weather_rain",
                )
            )
        for n_v in rain_fleet_sizes:
            configs.append(
                ExperimentConfig(
                    scenario="C",
                    n_vans=n_v,
                    horizon_min=HORIZON_MIN_DEFAULT,
                    weather="rain",
                    block="weather_rain",
                )
            )

        # Blok 3b: Wrażliwość na progi interwencji thr_low/thr_high (20%/80% vs bazowe 10%/90%, N=4)
        configs.append(
            ExperimentConfig(
                scenario="B",
                n_vans=4,
                thr_low=0.20,
                thr_high=0.80,
                horizon_min=0,
                block="sens_thresholds",
            )
        )
        configs.append(
            ExperimentConfig(
                scenario="C",
                n_vans=4,
                thr_low=0.20,
                thr_high=0.80,
                horizon_min=HORIZON_MIN_DEFAULT,
                block="sens_thresholds",
            )
        )

        # Blok 3c: Wrażliwość na horyzont predykcji H in {30, 120} min vs bazowe 60 min (Scenariusz C, N=4)
        for h_min in (30, 120):
            configs.append(
                ExperimentConfig(
                    scenario="C",
                    n_vans=4,
                    horizon_min=h_min,
                    block="sens_horizon",
                )
            )

        # Blok 3d: Wrażliwość na pojemność furgonetki K in {15, 35} vs bazowe 25 (Scenariusze B i C, N=4)
        for cap_k in (15, 35):
            configs.append(
                ExperimentConfig(
                    scenario="B",
                    n_vans=4,
                    van_capacity=cap_k,
                    horizon_min=0,
                    block="sens_capacity",
                )
            )
            configs.append(
                ExperimentConfig(
                    scenario="C",
                    n_vans=4,
                    van_capacity=cap_k,
                    horizon_min=HORIZON_MIN_DEFAULT,
                    block="sens_capacity",
                )
            )

    return configs


def compute_warmup_init_bikes(
    stations: list,
    table: DemandTable,
    lam_in: np.ndarray,
    seed: int,
    warmup_days: int = 0,
    init_fill: float = INIT_FILL,
    overnight_reset_alpha: float = 0.85,
) -> np.ndarray | None:
    """Wykonuje opcjonalny okres rozbiegu (warm-up) przed dobą pomiarową.
    Gdy warmup_days == 0, zwraca None (rozbieg realizowany naturalnie w oknie nocnym 00:00–06:00 od init_fill).
    Gdy warmup_days >= 1, symuluje poprzedzające doby i stosuje nocne równoważenie zajezdniowe o północy.
    """
    if warmup_days <= 0:
        return None

    cap = np.array([s.capacity for s in stations], dtype=np.float64)
    target_midnight = cap * float(init_fill)
    cur_bikes: np.ndarray | None = None
    lam_out = table.lam[: len(stations)]

    for day_idx in range(warmup_days):
        warmup_seed = 10_000 + int(seed) * 10 + day_idx
        warm_stream = generate_customer_stream(table, seed=warmup_seed)
        warm_strat = ScenarioB(n_vans=4, van_capacity=VAN_CAPACITY_DEFAULT, lam_out=lam_out, lam_in=lam_in)
        eng = SimulationEngine(
            stations=stations,
            strategy=warm_strat,
            seed=warmup_seed,
            day_type="weekday",
            weather="dry",
            init_fill=init_fill,
            init_bikes=cur_bikes,
        )
        eng.run(warm_stream, record_snapshots=False)
        blended = (1.0 - overnight_reset_alpha) * eng.bikes.astype(np.float64) + (
            overnight_reset_alpha * target_midnight
        )
        cur_bikes = np.clip(np.rint(blended).astype(np.int32), 0, eng.capacity)

    return cur_bikes


# Stan globalny procesu roboczego dla ProcessPoolExecutor
_WORKER_CTX: dict = {}


def _init_worker(
    stations_path: Path,
    lambda_path: Path,
    od_path: Path,
    env_keys: list[tuple[str, str]],
) -> None:
    stations = contracts.validate_stations(stations_path).stations
    station_ids = [s.station_id for s in stations]
    lambda_df = pl.read_parquet(lambda_path)
    od_df = pl.read_parquet(od_path)

    tables: dict[tuple[str, str], tuple[DemandTable, np.ndarray, float]] = {}
    for day_type, weather in env_keys:
        w_mult = float(weather_multiplier(weather))
        tbl = build_demand_table(station_ids, lambda_df, od_df, day_type=day_type, weather_mult=w_mult)
        lam_in = build_return_rate_matrix(station_ids, lambda_df, day_type=day_type, weather_mult=w_mult)
        tables[(day_type, weather)] = (tbl, lam_in, w_mult)

    _WORKER_CTX["stations"] = stations
    _WORKER_CTX["station_ids"] = station_ids
    _WORKER_CTX["stations_file"] = stations_path.name
    _WORKER_CTX["tables"] = tables


def _execute_seed_configs(
    seed: int,
    configs: list[ExperimentConfig],
    stations: list,
    station_ids: list[str],
    stations_file: str,
    tables: dict[tuple[str, str], tuple[DemandTable, np.ndarray, float]],
    init_fill: float,
    warmup_days: int,
    save_snapshots_seed: int | None,
    snapshots_dir: Path | None,
) -> list[SimResult]:
    """Uruchamia wszystkie konfiguracje dla pojedynczego ziarna losowości `seed` przy użyciu
    wspólnych strumieni klientów (Common Random Numbers — CRN).
    """
    n_stations = len(station_ids)
    streams_by_env: dict[tuple[str, str], dict[str, np.ndarray]] = {}
    init_bikes_by_env: dict[tuple[str, str], np.ndarray | None] = {}

    for env_key, (tbl, lam_in, _w_mult) in tables.items():
        streams_by_env[env_key] = generate_customer_stream(tbl, seed=seed)
        init_bikes_by_env[env_key] = compute_warmup_init_bikes(
            stations=stations,
            table=tbl,
            lam_in=lam_in,
            seed=seed,
            warmup_days=warmup_days,
            init_fill=init_fill,
        )

    results: list[SimResult] = []
    for cfg in configs:
        env_key = (cfg.day_type, cfg.weather)
        tbl, lam_in, w_mult = tables[env_key]
        stream = streams_by_env[env_key]
        init_bikes = init_bikes_by_env[env_key]

        need_snapshot = (
            save_snapshots_seed is not None
            and int(seed) == int(save_snapshots_seed)
            and cfg.is_baseline()
            and snapshots_dir is not None
        )

        strategy = make_strategy(
            cfg,
            lam_out=tbl.lam[:n_stations],
            lam_in=lam_in,
        )
        engine = SimulationEngine(
            stations=stations,
            strategy=strategy,
            seed=seed,
            day_type=cfg.day_type,
            weather=cfg.weather,
            init_fill=init_fill,
            stations_file=stations_file,
            init_bikes=init_bikes,
        )
        res = engine.run(
            stream,
            extra_params={"weather_mult": round(w_mult, 4), "warmup_days": int(warmup_days)},
            record_snapshots=need_snapshot,
            run_id=cfg.run_id(seed),
        )

        if need_snapshot and snapshots_dir is not None:
            snapshots_dir.mkdir(parents=True, exist_ok=True)
            snap_path = snapshots_dir / f"{cfg.run_id(seed)}.json"
            contracts.write_json(res.snapshots, snap_path)
            contracts.validate_snapshots(snap_path, station_ids)
            # Zwolnienie ciężkich ramek z pamięci przed przesłaniem z procesu roboczego
            res = SimResult(snapshots={}, run_row=res.run_row, hourly_rows=res.hourly_rows)

        results.append(res)

    return results


def _worker_run_seed(
    seed: int,
    configs: list[ExperimentConfig],
    init_fill: float,
    warmup_days: int,
    save_snapshots_seed: int | None,
    snapshots_dir: Path | None,
) -> tuple[int, list[SimResult], float]:
    t0 = time.time()
    results = _execute_seed_configs(
        seed=seed,
        configs=configs,
        stations=_WORKER_CTX["stations"],
        station_ids=_WORKER_CTX["station_ids"],
        stations_file=_WORKER_CTX["stations_file"],
        tables=_WORKER_CTX["tables"],
        init_fill=init_fill,
        warmup_days=warmup_days,
        save_snapshots_seed=save_snapshots_seed,
        snapshots_dir=snapshots_dir,
    )
    return seed, results, time.time() - t0


def summarize_batch_results(runs_df: pl.DataFrame) -> pl.DataFrame:
    """Wylicza średnie i 95% przedziały ufności (rozklad t-Studenta) dla każdej konfiguracji."""
    enriched = runs_df.with_columns(
        (pl.col("fail_rent") + pl.col("fail_return")).alias("fail_total"),
        (pl.col("rentals_ok").cast(pl.Float64) / pl.col("demand_total").clip(lower_bound=1) * 100.0).alias(
            "service_level_pct"
        ),
        (
            pl.col("bikes_moved").cast(pl.Float64)
            / pl.when(pl.col("van_km") > 0).then(pl.col("van_km")).otherwise(1.0)
        ).alias("bikes_per_km"),
    )

    group_cols = [
        "scenario",
        "n_vans",
        "van_capacity",
        "thr_low",
        "thr_high",
        "horizon_min",
        "weather",
        "day_type",
    ]
    agg_df = (
        enriched.group_by(group_cols)
        .agg(
            pl.len().alias("n_reps"),
            pl.col("demand_total").mean().alias("demand_mean"),
            pl.col("rentals_ok").mean().alias("rentals_mean"),
            pl.col("fail_rent").mean().alias("fail_rent_mean"),
            pl.col("fail_return").mean().alias("fail_return_mean"),
            pl.col("fail_total").mean().alias("fail_total_mean"),
            pl.col("fail_total").std().fill_null(0.0).alias("fail_total_std"),
            pl.col("service_level_pct").mean().alias("sl_pct_mean"),
            pl.col("empty_station_min").mean().alias("empty_min_mean"),
            pl.col("empty_station_min").std().fill_null(0.0).alias("empty_min_std"),
            pl.col("full_station_min").mean().alias("full_min_mean"),
            pl.col("van_km").mean().alias("van_km_mean"),
            pl.col("bikes_moved").mean().alias("bikes_moved_mean"),
            pl.col("bikes_per_km").mean().alias("bikes_per_km_mean"),
        )
        .sort("weather", "van_capacity", "thr_low", "horizon_min", "n_vans", "scenario")
    )

    n_arr = agg_df["n_reps"].to_numpy()
    t_crit = np.where(n_arr > 1, stats.t.ppf(0.975, df=np.maximum(1, n_arr - 1)), 0.0)
    fail_ci = t_crit * agg_df["fail_total_std"].to_numpy() / np.sqrt(np.maximum(1, n_arr))
    empty_ci = t_crit * agg_df["empty_min_std"].to_numpy() / np.sqrt(np.maximum(1, n_arr))

    return agg_df.with_columns(
        pl.Series("fail_total_ci95", np.round(fail_ci, 1)),
        pl.Series("empty_min_ci95", np.round(empty_ci, 1)),
    )


def print_batch_summary(summary_df: pl.DataFrame) -> None:
    print("\n" + "=" * 116)
    print(
        f"{'Scen':<4} {'N':>2} {'Wx':<4} {'K':>3} {'Thr':<9} {'H':>3} | "
        f"{'Reps':>4} {'SL [%]':>7} {'Fail Total (±95% CI)':>21} "
        f"{'Empty Min (±95% CI)':>22} {'Full Min':>10} {'Van km':>8} {'Moved':>7}"
    )
    print("-" * 116)
    for row in summary_df.iter_rows(named=True):
        thr_str = f"{round(row['thr_low'] * 100)}/{round(row['thr_high'] * 100)}%"
        fail_str = f"{row['fail_total_mean']:7.0f} ± {row['fail_total_ci95']:5.0f}"
        emp_str = f"{row['empty_min_mean']:8.0f} ± {row['empty_min_ci95']:5.0f}"
        print(
            f"{row['scenario']:<4} {row['n_vans']:>2} {row['weather']:<4} "
            f"{row['van_capacity']:>3} {thr_str:<9} {row['horizon_min']:>3} | "
            f"{row['n_reps']:>4} {row['sl_pct_mean']:>6.2f}% {fail_str:>21} "
            f"{emp_str:>22} {row['full_min_mean']:>10.0f} {row['van_km_mean']:>8.1f} "
            f"{row['bikes_moved_mean']:>7.0f}"
        )
    print("=" * 116 + "\n")


def run_batch_experiments(
    seeds: list[int] | None = None,
    configs: list[ExperimentConfig] | None = None,
    suite: str = "all",
    workers: int | None = None,
    warmup_days: int = 0,
    init_fill: float = INIT_FILL,
    save_snapshots_seed: int | None = 1,
    stations_path: Path = STATIONS_JSON,
    lambda_path: Path = LAMBDA_PARQUET,
    od_path: Path = OD_PARQUET,
    outputs_dir: Path = OUTPUTS,
    replace_all: bool = True,
    verbose: bool = True,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Uruchamia wsadowe eksperymenty symulacyjne z Common Random Numbers (CRN)."""
    t_start = time.time()
    seed_list = list(range(1, 31)) if seeds is None else [int(s) for s in seeds]
    cfg_list = build_experiment_grid(suite=suite) if configs is None else list(configs)
    env_keys = sorted({(c.day_type, c.weather) for c in cfg_list})

    total_runs = len(seed_list) * len(cfg_list)
    max_workers = workers if workers is not None else min(len(seed_list), os.cpu_count() or 4)
    snapshots_dir = outputs_dir / "snapshots" if save_snapshots_seed is not None else None
    metrics_dir = outputs_dir / "metrics"

    if verbose:
        print(
            f"[Batch CRN] Start: {len(seed_list)} replikacji (seeds={seed_list[0]}..{seed_list[-1]}) "
            f"x {len(cfg_list)} konfiguracji = {total_runs} przebiegów 24h | "
            f"workers={max_workers} | warmup_days={warmup_days}"
        )

    all_results: list[SimResult] = []

    if max_workers <= 1 or len(seed_list) == 1:
        _init_worker(stations_path, lambda_path, od_path, env_keys)
        for idx, s in enumerate(seed_list, start=1):
            seed_val, res_bundle, dt_s = _worker_run_seed(
                seed=s,
                configs=cfg_list,
                init_fill=init_fill,
                warmup_days=warmup_days,
                save_snapshots_seed=save_snapshots_seed,
                snapshots_dir=snapshots_dir,
            )
            all_results.extend(res_bundle)
            if verbose:
                print(
                    f"  [{idx:>2}/{len(seed_list)}] seed={seed_val:>2} "
                    f"({len(res_bundle)} przebiegów) ukończony w {dt_s:.2f}s"
                )
    else:
        with ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_init_worker,
            initargs=(stations_path, lambda_path, od_path, env_keys),
        ) as pool:
            futures = {
                pool.submit(
                    _worker_run_seed,
                    s,
                    cfg_list,
                    init_fill,
                    warmup_days,
                    save_snapshots_seed,
                    snapshots_dir,
                ): s
                for s in seed_list
            }
            for done_cnt, fut in enumerate(as_completed(futures), start=1):
                seed_val, res_bundle, dt_s = fut.result()
                all_results.extend(res_bundle)
                if verbose:
                    print(
                        f"  [{done_cnt:>2}/{len(seed_list)}] seed={seed_val:>2} "
                        f"({len(res_bundle)} przebiegów) ukończony w {dt_s:.2f}s"
                    )

    runs_df, hourly_df = upsert_metrics_batch(all_results, metrics_dir, replace_all=replace_all)
    contracts.validate_metrics(runs_df, hourly_df)
    summary_df = summarize_batch_results(runs_df)

    if verbose:
        elapsed = time.time() - t_start
        print(
            f"\n[Batch CRN] Zakończono {len(all_results)} przebiegów w {elapsed:.2f}s "
            f"({elapsed / max(1, len(all_results)):.3f}s / przebieg)."
        )
        print(f"[Batch CRN] Zapisano: {metrics_dir / 'metrics_runs.csv'} ({runs_df.height} wierszy)")
        print(f"[Batch CRN] Zapisano: {metrics_dir / 'metrics_hourly.csv'} ({hourly_df.height} wierszy)")
        print_batch_summary(summary_df)

    return runs_df, hourly_df, summary_df


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wielordzeniowy Batch Runner z Common Random Numbers (CRN) — Task 4.1"
    )
    parser.add_argument(
        "--seeds",
        type=int,
        default=30,
        help="Liczba replikacji CRN (seed = 1..N, domyślnie 30)",
    )
    parser.add_argument(
        "--suite",
        choices=["all", "core", "sensitivity"],
        default="all",
        help="Zestaw eksperymentów do uruchomienia (all=780 przebiegów, core=330, sensitivity=450)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Liczba procesów roboczych (domyślnie os.cpu_count())",
    )
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=0,
        help="Liczba pełnych dób rozbiegu przed dobą pomiarową (domyślnie 0 = rozbieg nocny 00:00–06:00)",
    )
    parser.add_argument(
        "--init-fill",
        type=float,
        default=INIT_FILL,
        help="Początkowy współczynnik zapełnienia stacji (domyślnie 0.5)",
    )
    parser.add_argument(
        "--save-snapshots-seed",
        type=int,
        default=1,
        help="Ziarno losowości, dla którego zapisywane są pełne snapshoty 24h (0 aby wyłączyć)",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Dopisuj/aktualizuj wiersze zamiast nadpisywać cały plik metrics_runs.csv",
    )
    args = parser.parse_args()

    snap_seed = args.save_snapshots_seed if args.save_snapshots_seed > 0 else None
    run_batch_experiments(
        seeds=list(range(1, args.seeds + 1)),
        suite=args.suite,
        workers=args.workers,
        warmup_days=args.warmup_days,
        init_fill=args.init_fill,
        save_snapshots_seed=snap_seed,
        replace_all=not args.append,
        verbose=True,
    )


if __name__ == "__main__":
    main()
