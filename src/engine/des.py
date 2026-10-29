import heapq
from dataclasses import dataclass

import numpy as np
import polars as pl

from common.config import (
    BIKE_SPEED_MPS,
    DAY_S,
    INIT_FILL,
    SCHEMA_VERSION,
    STEP_S,
    WALK_PROB,
    WALK_SPEED_MPS,
)
from common.geo import manhattan_m
from engine.demand_gen import EXT_IDX
from strategies.base import BaseStrategy, ScenarioA

EV_SNAPSHOT = 0
EV_DISPATCH = 1
EV_VAN_SERVICE = 2
EV_RETURN = 3
EV_RETURN_REDIR = 4
EV_WALK_ARRIVAL = 5
EV_ARRIVAL = 6

MAX_RETURN_RETRIES = 3


@dataclass(slots=True)
class SimResult:
    snapshots: dict
    run_row: dict
    hourly_rows: list[dict]


class SimulationEngine:
    def __init__(
        self,
        stations: list,
        strategy: BaseStrategy | None = None,
        seed: int = 1,
        day_type: str = "weekday",
        weather: str = "dry",
        init_fill: float = INIT_FILL,
        stations_file: str = "stations.json",
    ):
        self.stations = stations
        self.n = len(stations)
        self.station_ids = [s.station_id for s in stations]
        self.id_to_idx = {sid: i for i, sid in enumerate(self.station_ids)}
        self.lat = np.array([s.lat for s in stations], dtype=np.float64)
        self.lon = np.array([s.lon for s in stations], dtype=np.float64)
        self.capacity = np.array([s.capacity for s in stations], dtype=np.int32)
        self.neighbors = [[self.id_to_idx[nb] for nb in s.neighbors if nb in self.id_to_idx] for s in stations]
        self.dist_m = manhattan_m(self.lat[:, None], self.lon[:, None], self.lat[None, :], self.lon[None, :])

        self.strategy = strategy or ScenarioA()
        self.seed = int(seed)
        self.day_type = day_type
        self.weather = weather
        self.init_fill = float(init_fill)
        self.stations_file = stations_file

        self.bikes = np.clip(np.rint(self.capacity * self.init_fill).astype(np.int32), 0, self.capacity)
        self.last_change_t = np.zeros(self.n, dtype=np.int32)
        self.empty_sec_hourly = np.zeros(24, dtype=np.float64)
        self.full_sec_hourly = np.zeros(24, dtype=np.float64)

        self.win_fail_rent = np.zeros(self.n, dtype=np.int32)
        self.win_fail_return = np.zeros(self.n, dtype=np.int32)

        self.demand_total = 0
        self.rentals_cum = 0
        self.fail_rent_cum = 0
        self.fail_return_cum = 0
        self.walk_redirect_cum = 0

        self.hourly_demand = np.zeros(24, dtype=np.int32)
        self.hourly_rentals_ok = np.zeros(24, dtype=np.int32)
        self.hourly_fail_rent = np.zeros(24, dtype=np.int32)
        self.hourly_fail_return = np.zeros(24, dtype=np.int32)

        self.frames: list[dict] = []
        self.queue: list[tuple] = []
        self._seq = 0

    def schedule(self, t: int, ev_type: int, payload=None) -> None:
        if t <= DAY_S:
            self._seq += 1
            heapq.heappush(self.queue, (int(t), ev_type, self._seq, payload))

    def _accrue_station_time(self, i: int, t_now: int) -> None:
        t0 = int(self.last_change_t[i])
        t1 = min(int(t_now), DAY_S)
        if t1 <= t0:
            self.last_change_t[i] = t_now
            return
        is_empty = self.bikes[i] == 0
        is_full = self.bikes[i] >= self.capacity[i]
        if is_empty or is_full:
            cur = t0
            while cur < t1:
                hr = min(cur // 3600, 23)
                nxt = min((hr + 1) * 3600, t1)
                dt = float(nxt - cur)
                if is_empty:
                    self.empty_sec_hourly[hr] += dt
                if is_full:
                    self.full_sec_hourly[hr] += dt
                cur = nxt
        self.last_change_t[i] = t1

    def change_bikes(self, i: int, delta: int, t_now: int) -> None:
        self._accrue_station_time(i, t_now)
        self.bikes[i] = int(np.clip(self.bikes[i] + delta, 0, self.capacity[i]))
        self.strategy.on_station_change(self, i, t_now)

    def _record_snapshot(self, t: int) -> None:
        self.frames.append(
            {
                "t": int(t),
                "bikes": self.bikes.tolist(),
                "fail_rent": self.win_fail_rent.tolist(),
                "fail_return": self.win_fail_return.tolist(),
                "vans": self.strategy.van_snapshots(),
                "kpi": {
                    "rentals_cum": int(self.rentals_cum),
                    "fail_rent_cum": int(self.fail_rent_cum),
                    "fail_return_cum": int(self.fail_return_cum),
                    "walk_redirect_cum": int(self.walk_redirect_cum),
                    "van_km_cum": round(float(self.strategy.total_km), 3),
                    "n_empty": int((self.bikes == 0).sum()),
                    "n_full": int((self.bikes >= self.capacity).sum()),
                },
            }
        )
        self.win_fail_rent.fill(0)
        self.win_fail_return.fill(0)

    def _handle_arrival(self, t: int, origin: int, dest: int, duration: int, u_walk: float) -> None:
        hr = min(t // 3600, 23)
        if origin == EXT_IDX:
            if dest != EXT_IDX:
                self.schedule(t + duration, EV_RETURN, dest)
            return

        self.demand_total += 1
        self.hourly_demand[hr] += 1

        if self.bikes[origin] > 0:
            self.change_bikes(origin, -1, t)
            self.rentals_cum += 1
            self.hourly_rentals_ok[hr] += 1
            if dest != EXT_IDX:
                self.schedule(t + duration, EV_RETURN, dest)
            return

        self.win_fail_rent[origin] += 1
        self.fail_rent_cum += 1
        self.hourly_fail_rent[hr] += 1

        if u_walk < WALK_PROB:
            for nb in self.neighbors[origin]:
                if self.bikes[nb] > 0:
                    self.walk_redirect_cum += 1
                    walk_s = max(15, round(float(self.dist_m[origin, nb]) / WALK_SPEED_MPS))
                    self.schedule(t + walk_s, EV_WALK_ARRIVAL, (nb, dest, duration))
                    break

    def _handle_walk_arrival(self, t: int, st_idx: int, dest: int, duration: int) -> None:
        if self.bikes[st_idx] > 0:
            hr = min(t // 3600, 23)
            self.change_bikes(st_idx, -1, t)
            self.rentals_cum += 1
            self.hourly_rentals_ok[hr] += 1
            if dest != EXT_IDX:
                self.schedule(t + duration, EV_RETURN, dest)

    def _handle_return(self, t: int, dest: int, retry: int = 0) -> None:
        if dest == EXT_IDX:
            return
        if self.bikes[dest] < self.capacity[dest]:
            self.change_bikes(dest, +1, t)
            return

        if retry == 0:
            hr = min(t // 3600, 23)
            self.win_fail_return[dest] += 1
            self.fail_return_cum += 1
            self.hourly_fail_return[hr] += 1

        if retry < MAX_RETURN_RETRIES:
            for nb in self.neighbors[dest]:
                if self.bikes[nb] < self.capacity[nb]:
                    ride_s = max(15, round(float(self.dist_m[dest, nb]) / BIKE_SPEED_MPS))
                    self.schedule(t + ride_s, EV_RETURN_REDIR, (nb, retry + 1))
                    return
            best_nb = int(np.argmin(np.where(self.bikes < self.capacity, self.dist_m[dest], np.inf)))
            if self.bikes[best_nb] < self.capacity[best_nb]:
                ride_s = max(30, round(float(self.dist_m[dest, best_nb]) / BIKE_SPEED_MPS))
                self.schedule(t + ride_s, EV_RETURN_REDIR, (best_nb, retry + 1))

    def run(self, stream: dict[str, np.ndarray], extra_params: dict | None = None) -> SimResult:
        self.strategy.initialize(self)

        for k in range(0, DAY_S + 1, STEP_S):
            self.schedule(k, EV_SNAPSHOT, None)

        ts = stream["t"]
        origins = stream["origin"]
        dests = stream["dest"]
        durations = stream["duration"]
        u_walks = stream["u_walk"]
        for idx in range(ts.size):
            self.schedule(
                int(ts[idx]),
                EV_ARRIVAL,
                (int(origins[idx]), int(dests[idx]), int(durations[idx]), float(u_walks[idx])),
            )

        while self.queue:
            t, ev_type, _, payload = heapq.heappop(self.queue)
            if ev_type == EV_SNAPSHOT:
                self.strategy.on_tick(self, t)
                self._record_snapshot(t)
            elif ev_type == EV_ARRIVAL:
                o_i, d_i, dur, uw = payload
                self._handle_arrival(t, o_i, d_i, dur, uw)
            elif ev_type == EV_WALK_ARRIVAL:
                nb_i, d_i, dur = payload
                self._handle_walk_arrival(t, nb_i, d_i, dur)
            elif ev_type == EV_RETURN:
                self._handle_return(t, int(payload), retry=0)
            elif ev_type == EV_RETURN_REDIR:
                nb_i, r_cnt = payload
                self._handle_return(t, nb_i, retry=r_cnt)
            elif ev_type in (EV_DISPATCH, EV_VAN_SERVICE) and callable(payload):
                payload(self, t)

        for i in range(self.n):
            self._accrue_station_time(i, DAY_S)

        scen = self.strategy.scenario
        n_vans = self.strategy.n_vans
        run_id = f"{scen}_n{n_vans}_s{self.seed}"
        empty_min_hourly = np.round(self.empty_sec_hourly / 60.0, 2)
        full_min_hourly = np.round(self.full_sec_hourly / 60.0, 2)

        snapshots = {
            "schema_version": SCHEMA_VERSION,
            "meta": {
                "scenario": scen,
                "seed": self.seed,
                "n_vans": n_vans,
                "van_capacity": int(self.strategy.van_capacity),
                "day_type": self.day_type,
                "step_s": STEP_S,
                "t_start": 0,
                "t_end": DAY_S,
                "generated_by": "engine",
                "stations_file": self.stations_file,
                "station_ids": self.station_ids,
                "params": {"weather": self.weather, "init_fill": self.init_fill, **(extra_params or {})},
            },
            "frames": self.frames,
            "van_legs": self.strategy.van_legs,
        }

        run_row = {
            "run_id": run_id,
            "scenario": scen,
            "seed": self.seed,
            "n_vans": n_vans,
            "van_capacity": int(self.strategy.van_capacity),
            "thr_low": float(self.strategy.thr_low),
            "thr_high": float(self.strategy.thr_high),
            "horizon_min": int(self.strategy.horizon_min),
            "weather": self.weather,
            "day_type": self.day_type,
            "demand_total": int(self.demand_total),
            "rentals_ok": int(self.rentals_cum),
            "fail_rent": int(self.fail_rent_cum),
            "fail_return": int(self.fail_return_cum),
            "walk_redirect": int(self.walk_redirect_cum),
            "empty_station_min": round(float(empty_min_hourly.sum()), 2),
            "full_station_min": round(float(full_min_hourly.sum()), 2),
            "van_km": round(float(self.strategy.total_km), 2),
            "bikes_moved": int(self.strategy.bikes_moved),
        }

        hourly_rows = [
            {
                "run_id": run_id,
                "hour": hr,
                "demand": int(self.hourly_demand[hr]),
                "rentals_ok": int(self.hourly_rentals_ok[hr]),
                "fail_rent": int(self.hourly_fail_rent[hr]),
                "fail_return": int(self.hourly_fail_return[hr]),
                "empty_station_min": float(empty_min_hourly[hr]),
                "full_station_min": float(full_min_hourly[hr]),
            }
            for hr in range(24)
        ]

        return SimResult(snapshots=snapshots, run_row=run_row, hourly_rows=hourly_rows)


def upsert_metrics(result: SimResult, metrics_dir) -> tuple[pl.DataFrame, pl.DataFrame]:
    metrics_dir.mkdir(parents=True, exist_ok=True)
    runs_path = metrics_dir / "metrics_runs.csv"
    hourly_path = metrics_dir / "metrics_hourly.csv"
    run_id = result.run_row["run_id"]

    new_run = pl.DataFrame([result.run_row])
    new_hourly = pl.DataFrame(result.hourly_rows)

    if runs_path.exists():
        old_runs = pl.read_csv(runs_path).filter(pl.col("run_id") != run_id)
        runs_df = pl.concat([old_runs, new_run], how="diagonal_relaxed").sort("run_id")
    else:
        runs_df = new_run

    if hourly_path.exists():
        old_hourly = pl.read_csv(hourly_path).filter(pl.col("run_id") != run_id)
        hourly_df = pl.concat([old_hourly, new_hourly], how="diagonal_relaxed").sort("run_id", "hour")
    else:
        hourly_df = new_hourly

    runs_df.write_csv(runs_path)
    hourly_df.write_csv(hourly_path)
    return runs_df, hourly_df
