from dataclasses import dataclass

import numpy as np
import polars as pl

from common.config import DAY_S, EXT, MAX_DURATION_S, SLOTS, STEP_S

EXT_IDX = -1
MIN_DUR_S = 30


@dataclass(slots=True)
class DemandTable:
    station_ids: list[str]
    lam: np.ndarray  # shape: (N + 1, SLOTS), index N is EXT
    lam_max: np.ndarray  # shape: (N + 1,)
    od_dest: list[list[np.ndarray]]  # [origin_idx][hour] -> int32 array of dest_idx (-1 for EXT)
    od_cdf: list[list[np.ndarray]]  # [origin_idx][hour] -> float64 cumulative prob
    od_mu: list[list[np.ndarray]]  # [origin_idx][hour] -> float64 lognormal mu
    od_sigma: list[list[np.ndarray]]  # [origin_idx][hour] -> float64 lognormal sigma


def build_demand_table(
    station_ids: list[str],
    lambda_df: pl.DataFrame,
    od_df: pl.DataFrame,
    day_type: str = "weekday",
    weather_mult: float = 1.0,
) -> DemandTable:
    n = len(station_ids)
    id_to_idx = {sid: i for i, sid in enumerate(station_ids)}
    id_to_idx[EXT] = n

    sub_lam = lambda_df.filter(pl.col("day_type") == day_type)
    lam = np.zeros((n + 1, SLOTS), dtype=np.float64)
    s_idx = sub_lam["station_id"].replace_strict(id_to_idx).to_numpy()
    slots = sub_lam["slot"].to_numpy()
    lam[s_idx, slots] = sub_lam["lambda"].to_numpy() * float(weather_mult)
    lam_max = lam.max(axis=1)

    dest_map = {sid: i for i, sid in enumerate(station_ids)}
    dest_map[EXT] = EXT_IDX
    sub_od = (
        od_df.filter(pl.col("day_type") == day_type)
        .with_columns(
            pl.col("origin_id").replace_strict(id_to_idx).cast(pl.Int32).alias("o_idx"),
            pl.col("dest_id").replace_strict(dest_map).cast(pl.Int32).alias("d_idx"),
        )
        .sort("o_idx", "hour", "d_idx")
    )

    od_dest = [[np.empty(0, dtype=np.int32) for _ in range(24)] for _ in range(n + 1)]
    od_cdf = [[np.empty(0, dtype=np.float64) for _ in range(24)] for _ in range(n + 1)]
    od_mu = [[np.empty(0, dtype=np.float64) for _ in range(24)] for _ in range(n + 1)]
    od_sigma = [[np.empty(0, dtype=np.float64) for _ in range(24)] for _ in range(n + 1)]

    for (o_i, h), grp in sub_od.group_by(["o_idx", "hour"], maintain_order=True):
        oi, hr = int(o_i), int(h)
        probs = grp["prob"].to_numpy()
        cdf = np.cumsum(probs)
        cdf[-1] = 1.0
        od_dest[oi][hr] = grp["d_idx"].to_numpy()
        od_cdf[oi][hr] = cdf
        od_mu[oi][hr] = grp["dur_mu"].to_numpy()
        od_sigma[oi][hr] = grp["dur_sigma"].to_numpy()

    return DemandTable(
        station_ids=list(station_ids),
        lam=lam,
        lam_max=lam_max,
        od_dest=od_dest,
        od_cdf=od_cdf,
        od_mu=od_mu,
        od_sigma=od_sigma,
    )


def _thin_station(rng: np.random.Generator, lam_row: np.ndarray, lmax: float) -> np.ndarray:
    if lmax <= 0.0:
        return np.empty(0, dtype=np.float64)
    rate_s = lmax / 3600.0
    expected = int(rate_s * DAY_S * 1.25) + 32
    chunks = []
    t_cur = 0.0
    while t_cur < DAY_S:
        dt = rng.exponential(1.0 / rate_s, size=expected)
        t_cand = t_cur + np.cumsum(dt)
        t_cur = float(t_cand[-1])
        valid = t_cand[t_cand < DAY_S]
        if valid.size:
            chunks.append(valid)
    if not chunks:
        return np.empty(0, dtype=np.float64)
    cand = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    slots = np.minimum((cand // STEP_S).astype(np.int32), SLOTS - 1)
    accept_prob = lam_row[slots] / lmax
    keep = rng.random(cand.size) < accept_prob
    return cand[keep]


def generate_customer_stream(table: DemandTable, seed: int = 1) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    n_stations = len(table.station_ids)
    ext_row = n_stations

    t_list, o_list, d_list, dur_list, walk_list = [], [], [], [], []
    for oi in range(n_stations + 1):
        ts = _thin_station(rng, table.lam[oi], float(table.lam_max[oi]))
        if ts.size == 0:
            continue
        hours = np.minimum((ts // 3600).astype(np.int32), 23)
        u_od = rng.random(ts.size)
        z_dur = rng.standard_normal(ts.size)
        u_walk = rng.random(ts.size)

        dests = np.empty(ts.size, dtype=np.int32)
        durs = np.empty(ts.size, dtype=np.int32)
        keep_mask = np.ones(ts.size, dtype=bool)

        for hr in np.unique(hours):
            m = hours == hr
            cdf = table.od_cdf[oi][int(hr)]
            if cdf.size == 0:
                keep_mask[m] = False
                continue
            idx = np.searchsorted(cdf, u_od[m], side="right")
            idx = np.minimum(idx, cdf.size - 1)
            dests[m] = table.od_dest[oi][int(hr)][idx]
            mu = table.od_mu[oi][int(hr)][idx]
            sigma = table.od_sigma[oi][int(hr)][idx]
            raw_dur = np.exp(mu + sigma * z_dur[m])
            durs[m] = np.clip(np.rint(raw_dur), MIN_DUR_S, MAX_DURATION_S).astype(np.int32)

        if not keep_mask.all():
            ts = ts[keep_mask]
            dests = dests[keep_mask]
            durs = durs[keep_mask]
            u_walk = u_walk[keep_mask]
        if ts.size == 0:
            continue

        origin_code = EXT_IDX if oi == ext_row else oi
        t_list.append(np.rint(ts).astype(np.int32))
        o_list.append(np.full(ts.size, origin_code, dtype=np.int32))
        d_list.append(dests)
        dur_list.append(durs)
        walk_list.append(u_walk)

    if not t_list:
        empty_i = np.empty(0, dtype=np.int32)
        return {"t": empty_i, "origin": empty_i, "dest": empty_i, "duration": empty_i, "u_walk": np.empty(0)}

    t_all = np.concatenate(t_list)
    o_all = np.concatenate(o_list)
    d_all = np.concatenate(d_list)
    dur_all = np.concatenate(dur_list)
    w_all = np.concatenate(walk_list)

    order = np.lexsort((o_all, t_all))
    return {
        "t": np.clip(t_all[order], 0, DAY_S - 1),
        "origin": o_all[order],
        "dest": d_all[order],
        "duration": dur_all[order],
        "u_walk": w_all[order],
    }
