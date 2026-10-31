from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from common.config import (
    SLOTS,
    STEP_S,
    THR_HIGH_DEFAULT,
    THR_LOW_DEFAULT,
    VAN_CAPACITY_DEFAULT,
    VAN_SERVICE_BASE_S,
    VAN_SERVICE_PER_BIKE_S,
    VAN_SHIFT_END_S,
    VAN_SHIFT_START_S,
    VAN_SPEED_OFFPEAK_MPS,
    VAN_SPEED_PEAK_MPS,
)
from strategies.base import BaseStrategy, VanAgent

EV_DISPATCH = 1
EV_VAN_SERVICE = 2


def van_speed_mps(t: int, day_type: str = "weekday") -> float:
    """Prędkość furgonetki zależna od pory dnia (korki w szczycie porannym i popołudniowym)."""
    if day_type == "weekday":
        hr = (t % 86400) / 3600.0
        if (7.0 <= hr < 10.0) or (16.0 <= hr < 19.0):
            return VAN_SPEED_PEAK_MPS
    return VAN_SPEED_OFFPEAK_MPS


@dataclass(slots=True)
class _VanRuntime:
    van_id: int
    station_idx: int
    from_idx: int = 0
    to_idx: int = 0
    t_dep: int = 0
    t_arr: int = 0
    t_free: int = 0
    leg_km: float = 0.0
    km_completed: float = 0.0
    action: str | None = None
    planned_dst_idx: int | None = None


class ScenarioB(BaseStrategy):
    """Scenariusz B (Reaktywny): interwencja po przekroczeniu progów (<thr_low lub >thr_high)
    w parach 'odbiór z pełnej (pickup) -> dostawa do pustej (dropoff)' z priorytetem wg bieżącego lambda.
    """

    def __init__(
        self,
        n_vans: int = 4,
        van_capacity: int = VAN_CAPACITY_DEFAULT,
        thr_low: float = THR_LOW_DEFAULT,
        thr_high: float = THR_HIGH_DEFAULT,
        lam_out: np.ndarray | None = None,
        lam_in: np.ndarray | None = None,
    ):
        super().__init__(
            scenario="B",
            n_vans=int(n_vans),
            van_capacity=int(van_capacity),
            thr_low=float(thr_low),
            thr_high=float(thr_high),
            horizon_min=0,
        )
        self.lam_out = lam_out  # shape (N, SLOTS) lub None
        self.lam_in = lam_in  # shape (N, SLOTS) lub None
        self._rt: list[_VanRuntime] = []
        self._reserved_pickup: set[int] = set()
        self._reserved_dropoff: set[int] = set()
        self._in_dispatch: bool = False
        self._last_alarm_check_t: int = -1

    def initialize(self, sim) -> None:
        self.vans.clear()
        self.van_legs.clear()
        self._rt.clear()
        self._reserved_pickup.clear()
        self._reserved_dropoff.clear()
        self.bikes_moved = 0
        self._in_dispatch = False
        self._last_alarm_check_t = -1

        if self.n_vans <= 0 or sim.n == 0:
            return

        # Rozmieszczenie początkowych baz furgonetek równomiernie po szerokości geograficznej
        order = np.argsort(sim.lat)
        depots = [int(order[min(int((k + 0.5) * sim.n / self.n_vans), sim.n - 1)]) for k in range(self.n_vans)]

        for v_id, st_idx in enumerate(depots):
            self.vans.append(
                VanAgent(
                    van_id=v_id,
                    lat=float(sim.lat[st_idx]),
                    lon=float(sim.lon[st_idx]),
                    load=0,
                    status="off",
                    target_station_id=None,
                    km_total=0.0,
                )
            )
            self._rt.append(_VanRuntime(van_id=v_id, station_idx=st_idx, from_idx=st_idx, to_idx=st_idx))

        sim.schedule(VAN_SHIFT_START_S, EV_DISPATCH, self._on_shift_start)
        sim.schedule(VAN_SHIFT_END_S, EV_DISPATCH, self._on_shift_end)

    def _slot(self, t: int) -> int:
        return min(max(int(t) // STEP_S, 0), SLOTS - 1)

    def _station_lam_out(self, sim, slot: int) -> np.ndarray:
        if self.lam_out is not None and self.lam_out.shape[0] >= sim.n:
            return self.lam_out[: sim.n, slot]
        return sim.capacity.astype(np.float64) * 0.25

    def _station_lam_in(self, sim, slot: int) -> np.ndarray:
        if self.lam_in is not None and self.lam_in.shape[0] >= sim.n:
            return self.lam_in[: sim.n, slot]
        return self._station_lam_out(sim, slot)

    def _sync_driving_positions(self, sim, t: int) -> None:
        for v, rt in zip(self.vans, self._rt):
            if v.status == "driving" and rt.t_arr > rt.t_dep:
                frac = float(np.clip((t - rt.t_dep) / (rt.t_arr - rt.t_dep), 0.0, 1.0))
                la0, lo0 = float(sim.lat[rt.from_idx]), float(sim.lon[rt.from_idx])
                la1, lo1 = float(sim.lat[rt.to_idx]), float(sim.lon[rt.to_idx])
                v.lat = la0 + frac * (la1 - la0)
                v.lon = lo0 + frac * (lo1 - lo0)
                v.km_total = max(v.km_total, rt.km_completed + rt.leg_km * frac)
            else:
                v.km_total = max(v.km_total, rt.km_completed)

    def _on_shift_start(self, sim, t: int) -> None:
        for v, rt in zip(self.vans, self._rt):
            if v.status == "off":
                v.status = "idle"
                v.lat = float(sim.lat[rt.station_idx])
                v.lon = float(sim.lon[rt.station_idx])
                v.target_station_id = None
        self._try_dispatch(sim, t)

    def _on_shift_end(self, sim, t: int) -> None:
        for v, rt in zip(self.vans, self._rt):
            if v.status == "idle":
                v.status = "off"
                v.target_station_id = None
                rt.planned_dst_idx = None

    def on_tick(self, sim, t: int) -> None:
        if self.n_vans <= 0:
            return
        if VAN_SHIFT_START_S <= t < VAN_SHIFT_END_S:
            self._try_dispatch(sim, t)
        self._sync_driving_positions(sim, t)

    def on_station_change(self, sim, station_idx: int, t: int) -> None:
        if self.n_vans <= 0 or self._in_dispatch:
            return
        if not (VAN_SHIFT_START_S <= t < VAN_SHIFT_END_S):
            return
        cap = int(sim.capacity[station_idx])
        if cap <= 0:
            return
        fill = float(sim.bikes[station_idx]) / cap
        if (fill <= self.thr_low or fill >= self.thr_high) and any(v.status == "idle" for v in self.vans):
            self._try_dispatch(sim, t)

    def _pick_dropoff_target(
        self,
        sim,
        cur_idx: int,
        slot: int,
        fill: np.ndarray,
        preferred_idx: int | None = None,
    ) -> int | None:
        if (
            preferred_idx is not None
            and preferred_idx != cur_idx
            and preferred_idx not in self._reserved_dropoff
            and fill[preferred_idx] < 0.55
            and (sim.capacity[preferred_idx] - sim.bikes[preferred_idx]) >= 4
        ):
            return int(preferred_idx)

        free_docks = sim.capacity - sim.bikes
        valid = (free_docks >= 4) & (np.arange(sim.n) != cur_idx)
        for r_idx in self._reserved_dropoff:
            valid[r_idx] = False

        if not valid.any():
            return None

        # Najpierw stacje w alarmie pustki (<= thr_low), a w razie braku — stacje z zapełnieniem < 0.45
        alarm_mask = valid & (fill <= self.thr_low)
        cand_mask = alarm_mask if alarm_mask.any() else (valid & (fill < 0.45))
        if not cand_mask.any():
            cand_mask = valid

        cands = np.where(cand_mask)[0]
        lam_out = self._station_lam_out(sim, slot)[cands]
        dist_km = sim.dist_m[cur_idx, cands] / 1000.0
        urgency = np.clip(1.0 - fill[cands], 0.05, 1.0) ** 2
        scores = ((lam_out + 1.0) * urgency) / (dist_km + 0.35)
        return int(cands[int(np.argmax(scores))])

    def _pick_pickup_and_dropoff_pair(
        self,
        sim,
        cur_idx: int,
        slot: int,
        fill: np.ndarray,
    ) -> tuple[int | None, int | None]:
        # W Scenariuszu B interweniujemy tylko, gdy w sieci występuje alarm (<thr_low lub >thr_high)
        empty_alarms = [
            i for i in range(sim.n) if fill[i] <= self.thr_low and i not in self._reserved_dropoff
        ]
        full_alarms = [
            i for i in range(sim.n) if fill[i] >= self.thr_high and i not in self._reserved_pickup
        ]
        if not empty_alarms and not full_alarms:
            return None, None

        lam_out = self._station_lam_out(sim, slot)
        lam_in = self._station_lam_in(sim, slot)

        # 1. Wybór docelowej stacji biorcy (dropoff)
        dst_idx: int | None = None
        if empty_alarms:
            cands_d = np.array(empty_alarms, dtype=np.int32)
            dist_km = sim.dist_m[cur_idx, cands_d] / 1000.0
            scores_d = ((lam_out[cands_d] + 1.0) * (1.0 - fill[cands_d])) / (dist_km + 0.4)
            dst_idx = int(cands_d[int(np.argmax(scores_d))])
        else:
            # Alarm wywołany przepełnieniem stacji (>thr_high) -> szukamy najlepszego biorcy poniżej 45%
            low_cands = [
                i
                for i in range(sim.n)
                if fill[i] < 0.45
                and (sim.capacity[i] - sim.bikes[i]) >= 5
                and i not in self._reserved_dropoff
            ]
            if low_cands:
                cands_d = np.array(low_cands, dtype=np.int32)
                dist_km = sim.dist_m[cur_idx, cands_d] / 1000.0
                scores_d = ((lam_out[cands_d] + 1.0) * (1.0 - fill[cands_d])) / (dist_km + 0.4)
                dst_idx = int(cands_d[int(np.argmax(scores_d))])

        # 2. Wybór stacji dawcy (pickup)
        donor_cands = [i for i in full_alarms if i != cur_idx and i != dst_idx]
        if not donor_cands:
            donor_cands = [i for i in full_alarms if i != dst_idx]
        if not donor_cands:
            # Jeśli alarm dotyczy pustej stacji, a żadna stacja nie ma >= thr_high, bierzemy stację z nadwyżką > 55%
            donor_cands = [
                i
                for i in range(sim.n)
                if i != cur_idx
                and i != dst_idx
                and i not in self._reserved_pickup
                and fill[i] >= 0.55
                and (sim.bikes[i] - round(float(0.45 * sim.capacity[i]))) >= 4
            ]
        if not donor_cands:
            donor_cands = [
                i
                for i in range(sim.n)
                if i != dst_idx
                and i not in self._reserved_pickup
                and fill[i] >= 0.55
                and (sim.bikes[i] - round(float(0.45 * sim.capacity[i]))) >= 4
            ]
        if not donor_cands:
            return None, None

        cands_s = np.array(donor_cands, dtype=np.int32)
        d_to_src = sim.dist_m[cur_idx, cands_s] / 1000.0
        d_src_to_dst = (sim.dist_m[cands_s, dst_idx] / 1000.0) if dst_idx is not None else 0.0
        surplus_ratio = np.clip(fill[cands_s] - 0.45, 0.05, 1.0)
        scores_s = ((lam_in[cands_s] + 1.0) * surplus_ratio) / (d_to_src + 0.5 * d_src_to_dst + 0.35)
        src_idx = int(cands_s[int(np.argmax(scores_s))])
        return src_idx, dst_idx

    def _dispatch_leg(
        self,
        sim,
        v: VanAgent,
        rt: _VanRuntime,
        to_idx: int,
        action: str,
        t: int,
        speed: float,
    ) -> bool:
        dist_m = float(sim.dist_m[rt.station_idx, to_idx])
        drive_s = max(60, round(dist_m / max(speed, 0.5)))
        t_arr = int(t) + drive_s
        if t_arr + VAN_SERVICE_BASE_S > VAN_SHIFT_END_S:
            return False

        rt.from_idx = rt.station_idx
        rt.to_idx = int(to_idx)
        rt.t_dep = int(t)
        rt.t_arr = t_arr
        rt.leg_km = dist_m / 1000.0
        rt.action = action

        v.status = "driving"
        v.target_station_id = sim.station_ids[to_idx]

        if action == "pickup":
            self._reserved_pickup.add(to_idx)
        else:
            self._reserved_dropoff.add(to_idx)

        sim.schedule(
            t_arr,
            EV_VAN_SERVICE,
            lambda s, ts, vid=v.van_id: self._on_arrive_station(s, ts, vid),
        )
        return True

    def _try_dispatch(self, sim, t: int) -> None:
        if self._in_dispatch or not (VAN_SHIFT_START_S <= t < VAN_SHIFT_END_S):
            return
        self._in_dispatch = True
        try:
            slot = self._slot(t)
            speed = van_speed_mps(t, sim.day_type)
            fill = sim.bikes.astype(np.float64) / np.maximum(sim.capacity, 1)

            for v, rt in zip(self.vans, self._rt):
                if v.status != "idle":
                    continue

                if v.load > 0:
                    dst_idx = self._pick_dropoff_target(
                        sim, rt.station_idx, slot, fill, preferred_idx=rt.planned_dst_idx
                    )
                    rt.planned_dst_idx = None
                    if dst_idx is not None:
                        self._dispatch_leg(sim, v, rt, dst_idx, "dropoff", t, speed)
                else:
                    src_idx, dst_idx = self._pick_pickup_and_dropoff_pair(sim, rt.station_idx, slot, fill)
                    if src_idx is not None:
                        rt.planned_dst_idx = dst_idx
                        if not self._dispatch_leg(sim, v, rt, src_idx, "pickup", t, speed):
                            rt.planned_dst_idx = None
        finally:
            self._in_dispatch = False

    def _on_arrive_station(self, sim, t: int, van_id: int) -> None:
        v = self.vans[van_id]
        rt = self._rt[van_id]
        st_idx = rt.to_idx

        rt.km_completed += rt.leg_km
        rt.leg_km = 0.0
        rt.station_idx = st_idx
        v.lat = float(sim.lat[st_idx])
        v.lon = float(sim.lon[st_idx])
        v.km_total = max(v.km_total, rt.km_completed)

        action = rt.action
        qty = 0
        self._in_dispatch = True
        try:
            if action == "pickup":
                self._reserved_pickup.discard(st_idx)
                target_keep = round(float(0.45 * sim.capacity[st_idx]))
                avail = max(0, int(sim.bikes[st_idx]) - target_keep)
                if avail == 0 and sim.bikes[st_idx] >= 4:
                    avail = int(sim.bikes[st_idx]) // 2
                qty = min(int(self.van_capacity - v.load), avail)
                if qty > 0:
                    sim.change_bikes(st_idx, -qty, t)
                    v.load += qty
                    self.bikes_moved += qty
            elif action == "dropoff":
                self._reserved_dropoff.discard(st_idx)
                free_docks = max(0, int(sim.capacity[st_idx] - sim.bikes[st_idx]))
                max_safe = max(1, math.floor(float(sim.capacity[st_idx]) * 0.85) - int(sim.bikes[st_idx]))
                qty = max(0, min(int(v.load), free_docks, max_safe))
                if qty == 0 and free_docks > 0 and v.load > 0:
                    qty = min(int(v.load), free_docks)
                if qty > 0:
                    sim.change_bikes(st_idx, +qty, t)
                    v.load -= qty
                    self.bikes_moved += qty
        finally:
            self._in_dispatch = False

        if qty > 0 and action in ("pickup", "dropoff"):
            self.van_legs.append(
                {
                    "van_id": int(van_id),
                    "t_start": int(rt.t_dep),
                    "t_end": int(t),
                    "from_station_id": sim.station_ids[rt.from_idx],
                    "to_station_id": sim.station_ids[st_idx],
                    "load": int(v.load),
                    "action": action,
                    "qty": int(qty),
                }
            )

        srv_s = (VAN_SERVICE_BASE_S + VAN_SERVICE_PER_BIKE_S * qty) if qty > 0 else 30
        v.status = "servicing"
        rt.t_free = int(t) + srv_s
        sim.schedule(
            rt.t_free,
            EV_VAN_SERVICE,
            lambda s, ts, vid=van_id: self._on_finish_service(s, ts, vid),
        )

    def _on_finish_service(self, sim, t: int, van_id: int) -> None:
        v = self.vans[van_id]
        rt = self._rt[van_id]
        rt.action = None
        if t >= VAN_SHIFT_END_S:
            v.status = "off"
            v.target_station_id = None
            rt.planned_dst_idx = None
            return
        v.status = "idle"
        v.target_station_id = None
        self._try_dispatch(sim, t)
