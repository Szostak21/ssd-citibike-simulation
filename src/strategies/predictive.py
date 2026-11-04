from __future__ import annotations

import math

import numpy as np

from common.config import (
    DAY_S,
    HORIZON_MIN_DEFAULT,
    SLOTS,
    STEP_S,
    THR_HIGH_DEFAULT,
    THR_LOW_DEFAULT,
    VAN_CAPACITY_DEFAULT,
    VAN_SERVICE_BASE_S,
    VAN_SERVICE_PER_BIKE_S,
    VAN_SHIFT_END_S,
    VAN_SHIFT_START_S,
)
from strategies.reactive import EV_VAN_SERVICE, ScenarioB, _VanRuntime


class ScenarioC(ScenarioB):
    """Scenariusz C (Predykcyjny / Dynamiczny):
    Dyspozytor prognozuje bilans każdej stacji w horyzoncie H (domyślnie 60 min) na podstawie
    skalibrowanych intensywności wypożyczeń (lam_out) oraz zwrotów (lam_in), wyprzedzająco
    zwalniając doki przed falą zwrotów i dowożąc rowery przed falą wypożyczeń.
    """

    def __init__(
        self,
        n_vans: int = 4,
        van_capacity: int = VAN_CAPACITY_DEFAULT,
        thr_low: float = THR_LOW_DEFAULT,
        thr_high: float = THR_HIGH_DEFAULT,
        horizon_min: int = HORIZON_MIN_DEFAULT,
        lam_out: np.ndarray | None = None,
        lam_in: np.ndarray | None = None,
    ):
        super().__init__(
            n_vans=n_vans,
            van_capacity=van_capacity,
            thr_low=thr_low,
            thr_high=thr_high,
            lam_out=lam_out,
            lam_in=lam_in,
        )
        self.scenario = "C"
        self.horizon_min = int(horizon_min)
        self._last_sim_t: int = VAN_SHIFT_START_S

    def _forecast_window(
        self,
        sim,
        t: int,
        horizon_s: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Zwraca (exp_out, exp_in, net_delta) w oknie [t, t + horizon_s] dla wszystkich stacji."""
        h_s = max(STEP_S, int(self.horizon_min * 60) if horizon_s is None else int(horizon_s))
        t0 = max(0, min(int(t), DAY_S))
        t1 = min(DAY_S, t0 + h_s)
        if t1 <= t0:
            z = np.zeros(sim.n, dtype=np.float64)
            return z, z, z

        exp_out = np.zeros(sim.n, dtype=np.float64)
        exp_in = np.zeros(sim.n, dtype=np.float64)
        cur = t0
        while cur < t1:
            slot = min(cur // STEP_S, SLOTS - 1)
            nxt = min((slot + 1) * STEP_S, t1)
            dt_hours = (nxt - cur) / 3600.0
            exp_out += self._station_lam_out(sim, slot) * dt_hours
            exp_in += self._station_lam_in(sim, slot) * dt_hours
            cur = nxt

        return exp_out, exp_in, (exp_in - exp_out)

    def on_station_change(self, sim, station_idx: int, t: int) -> None:
        self._last_sim_t = int(t)
        if self.n_vans <= 0 or self._in_dispatch:
            return
        if not (VAN_SHIFT_START_S <= t < VAN_SHIFT_END_S):
            return
        cap = int(sim.capacity[station_idx])
        if cap <= 0:
            return
        fill = float(sim.bikes[station_idx]) / cap
        if (fill <= self.thr_low + 0.05 or fill >= self.thr_high - 0.05) and any(
            v.status == "idle" for v in self.vans
        ):
            self._try_dispatch(sim, t)

    def _pick_dropoff_target(
        self,
        sim,
        cur_idx: int,
        slot: int,
        fill: np.ndarray,
        preferred_idx: int | None = None,
    ) -> int | None:
        t_now = max(self._last_sim_t, slot * STEP_S)
        exp_out, _exp_in, net_delta = self._forecast_window(sim, t_now)
        cap = np.maximum(sim.capacity.astype(np.float64), 1.0)
        bikes = sim.bikes.astype(np.float64)
        pred_bikes = np.clip(bikes + net_delta, 0.0, cap)
        pred_fill = pred_bikes / cap

        if (
            preferred_idx is not None
            and preferred_idx != cur_idx
            and preferred_idx not in self._reserved_dropoff
            and (fill[preferred_idx] < 0.58 or pred_fill[preferred_idx] < 0.42)
            and (sim.capacity[preferred_idx] - sim.bikes[preferred_idx]) >= 4
        ):
            return int(preferred_idx)

        free_docks = sim.capacity - sim.bikes
        valid = (free_docks >= 4) & (np.arange(sim.n) != cur_idx)
        for r_idx in self._reserved_dropoff:
            valid[r_idx] = False

        if not valid.any():
            return None

        shortage = np.maximum(0.0, (self.thr_low + 0.04) * cap - (bikes + net_delta))
        alarm_mask = valid & (
            (sim.bikes == 0)
            | (fill <= self.thr_low)
            | (pred_fill <= self.thr_low + 0.04)
            | (shortage >= 2.0)
        )
        cand_mask = alarm_mask if alarm_mask.any() else (valid & ((fill < 0.48) | (pred_fill < 0.42)))
        if not cand_mask.any():
            cand_mask = valid

        cands = np.where(cand_mask)[0]
        dist_km = sim.dist_m[cur_idx, cands] / 1000.0
        now_deficit = np.clip(1.0 - fill[cands], 0.05, 1.0) ** 2
        pred_deficit = np.clip(1.0 - pred_fill[cands], 0.05, 1.0) ** 2
        zero_bonus = 8.0 * (sim.bikes[cands] == 0) + 4.0 * (pred_bikes[cands] <= 0.5)
        expected_saved = (
            1.5 * shortage[cands]
            + exp_out[cands] * (0.55 * now_deficit + 0.45 * pred_deficit)
            + zero_bonus
        )
        scores = (expected_saved + 1.0) / (dist_km + 0.30)
        return int(cands[int(np.argmax(scores))])

    def _pick_pickup_and_dropoff_pair(
        self,
        sim,
        cur_idx: int,
        slot: int,
        fill: np.ndarray,
    ) -> tuple[int | None, int | None]:
        t_now = max(self._last_sim_t, slot * STEP_S)
        exp_out, exp_in, net_delta = self._forecast_window(sim, t_now)
        cap = np.maximum(sim.capacity.astype(np.float64), 1.0)
        bikes = sim.bikes.astype(np.float64)
        free_docks = sim.capacity - sim.bikes

        pred_bikes = np.clip(bikes + net_delta, 0.0, cap)
        pred_fill = pred_bikes / cap

        # Prognozowany niedobór rowerów (wypożyczenia zagrożone odrzuceniem w oknie H)
        shortage = np.maximum(0.0, (self.thr_low + 0.04) * cap - (bikes + net_delta))
        # Prognozowany nadmiar rowerów (zwroty zagrożone odrzuceniem w oknie H)
        overflow = np.maximum(0.0, (bikes + net_delta) - (self.thr_high - 0.04) * cap)

        need_dropoff = (
            (sim.bikes == 0)
            | (fill <= self.thr_low)
            | (pred_fill <= self.thr_low + 0.04)
            | (shortage >= 2.5)
        )
        need_pickup = (
            (sim.bikes >= sim.capacity)
            | (fill >= self.thr_high)
            | (pred_fill >= self.thr_high - 0.04)
            | (overflow >= 2.5)
        )

        for r_idx in self._reserved_dropoff:
            need_dropoff[r_idx] = False
        for r_idx in self._reserved_pickup:
            need_pickup[r_idx] = False

        if not need_dropoff.any() and not need_pickup.any():
            return None, None

        valid_dst = free_docks >= 4
        for r_idx in self._reserved_dropoff:
            valid_dst[r_idx] = False

        dst_mask = valid_dst & need_dropoff
        if not dst_mask.any():
            dst_mask = valid_dst & ((fill < 0.48) | (pred_fill < 0.42))
        if not dst_mask.any():
            return None, None

        # Bezpieczny zapas na stacji dawcy: nigdy nie schodzimy poniżej 20% pojemności (min. 4 rowery)
        safe_keep = np.clip(
            np.rint(0.42 * cap - 0.60 * np.maximum(0.0, net_delta) + 0.50 * np.maximum(0.0, -net_delta)),
            np.maximum(4.0, np.rint(0.20 * cap)),
            np.rint(0.65 * cap),
        )
        avail_pickup = bikes - safe_keep
        valid_src = (avail_pickup >= 4) & (bikes >= 6)
        for r_idx in self._reserved_pickup:
            valid_src[r_idx] = False

        src_mask = valid_src & need_pickup
        if not src_mask.any():
            src_mask = valid_src & ((fill >= 0.55) | (pred_fill >= 0.60))
        if not src_mask.any():
            return None, None

        cands_d = np.where(dst_mask)[0]
        cands_s = np.where(src_mask)[0]

        urg_now_d = np.clip(1.0 - fill[cands_d], 0.05, 1.0) ** 2
        urg_pred_d = np.clip(1.0 - pred_fill[cands_d], 0.05, 1.0) ** 2
        benefit_d = (
            1.6 * shortage[cands_d]
            + exp_out[cands_d] * (0.55 * urg_now_d + 0.45 * urg_pred_d)
            + 9.0 * (sim.bikes[cands_d] == 0)
            + 5.0 * (fill[cands_d] <= self.thr_low)
        )

        urg_now_s = np.clip(fill[cands_s] - 0.40, 0.05, 1.0) ** 2
        urg_pred_s = np.clip(pred_fill[cands_s] - 0.40, 0.05, 1.0) ** 2
        benefit_s = (
            1.6 * overflow[cands_s]
            + exp_in[cands_s] * (0.45 * urg_now_s + 0.55 * urg_pred_s)
            + 9.0 * (sim.bikes[cands_s] >= sim.capacity[cands_s])
            + 5.0 * (fill[cands_s] >= self.thr_high)
        )

        if cands_d.size > 25:
            top_d = np.argpartition(benefit_d, -25)[-25:]
            cands_d = cands_d[top_d]
            benefit_d = benefit_d[top_d]
        if cands_s.size > 25:
            top_s = np.argpartition(benefit_s, -25)[-25:]
            cands_s = cands_s[top_s]
            benefit_s = benefit_s[top_s]

        d_cur_s = (sim.dist_m[cur_idx, cands_s] / 1000.0)[:, None]
        d_s_d = sim.dist_m[np.ix_(cands_s, cands_d)] / 1000.0

        transfer_qty = np.minimum(
            float(self.van_capacity),
            np.minimum(avail_pickup[cands_s][:, None], free_docks[cands_d][None, :]),
        )
        qty_factor = np.clip(transfer_qty / max(float(self.van_capacity), 1.0), 0.30, 1.0)

        pair_score = ((benefit_s[:, None] + benefit_d[None, :]) * qty_factor) / (
            d_cur_s + 0.65 * d_s_d + 0.45
        )
        same_mask = cands_s[:, None] == cands_d[None, :]
        pair_score = np.where(same_mask, -1.0, pair_score)

        best_flat = int(np.argmax(pair_score))
        if pair_score.flat[best_flat] <= 0.0:
            return None, None
        si, di = np.unravel_index(best_flat, pair_score.shape)
        return int(cands_s[si]), int(cands_d[di])

    def _try_dispatch(self, sim, t: int) -> None:
        self._last_sim_t = int(t)
        super()._try_dispatch(sim, t)

    def _on_arrive_station(self, sim, t: int, van_id: int) -> None:
        self._last_sim_t = int(t)
        v = self.vans[van_id]
        rt: _VanRuntime = self._rt[van_id]
        st_idx = rt.to_idx

        rt.km_completed += rt.leg_km
        rt.leg_km = 0.0
        rt.station_idx = st_idx
        v.lat = float(sim.lat[st_idx])
        v.lon = float(sim.lon[st_idx])
        v.km_total = max(v.km_total, rt.km_completed)

        _, _, net_delta = self._forecast_window(sim, t)
        st_net = float(net_delta[st_idx])
        cap = int(sim.capacity[st_idx])

        action = rt.action
        qty = 0
        self._in_dispatch = True
        try:
            if action == "pickup":
                self._reserved_pickup.discard(st_idx)
                target_keep = max(
                    4,
                    round(
                        float(
                            np.clip(
                                0.44 * cap - 0.60 * max(0.0, st_net) + 0.50 * max(0.0, -st_net),
                                0.22 * cap,
                                0.60 * cap,
                            )
                        )
                    ),
                )
                avail = max(0, int(sim.bikes[st_idx]) - target_keep)
                if avail == 0 and sim.bikes[st_idx] >= 6:
                    avail = int(sim.bikes[st_idx]) - max(4, round(0.35 * cap))
                qty = max(0, min(int(self.van_capacity - v.load), avail))
                if qty > 0:
                    sim.change_bikes(st_idx, -qty, t)
                    v.load += qty
                    self.bikes_moved += qty
            elif action == "dropoff":
                self._reserved_dropoff.discard(st_idx)
                free_docks = max(0, int(cap - sim.bikes[st_idx]))
                fill_cap_ratio = 0.90 if st_net < -2.0 else 0.85
                max_safe = max(1, math.floor(float(cap) * fill_cap_ratio) - int(sim.bikes[st_idx]))
                qty = max(0, min(int(v.load), free_docks, max_safe))
                # Jeśli zostaje mała końcówka ładunku (<=5 rowerów), a stacja zmieści ją poniżej thr_high, zostawiamy całość
                rem = int(v.load) - qty
                if 0 < rem <= 5 and int(sim.bikes[st_idx]) + int(v.load) <= math.floor(cap * self.thr_high):
                    qty = min(int(v.load), free_docks)
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
