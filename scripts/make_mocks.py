import json
from datetime import UTC, datetime

import numpy as np
import polars as pl

from common import contracts
from common.config import MANHATTAN_POLYGON, MOCK, SCHEMA_VERSION, STATIONS_JSON, STEP_S
from common.geo import haversine_m, in_area

SEEDS = range(1, 31)
FLEETS = (1, 2, 4, 6, 8)
VAN_SPEED_MPS = 15 / 3.6
SERVICE_S = 300


def mock_stations(rng):
    if STATIONS_JSON.exists():
        st = json.loads(STATIONS_JSON.read_text())
        st["source_month"] = "mock"
        return st
    poly = np.array(MANHATTAN_POLYGON)
    pts = []
    while len(pts) < 380:
        lon = rng.uniform(poly[:, 0].min(), poly[:, 0].max())
        lat = rng.uniform(poly[:, 1].min(), poly[:, 1].max())
        if in_area([lat], [lon])[0]:
            pts.append((lat, lon))
    lat, lon = np.array(pts).T
    d = haversine_m(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
    ids = [f"M{i:04d}" for i in range(len(pts))]
    stations = [
        {
            "idx": i,
            "station_id": ids[i],
            "gbfs_id": None,
            "name": f"Mock Station {i}",
            "lat": round(lat[i], 6),
            "lon": round(lon[i], 6),
            "capacity": int(rng.choice([19, 23, 31, 39, 47, 55])),
            "capacity_source": "imputed",
            "aliases": [],
            "neighbors": [ids[j] for j in np.argsort(d[i]) if j != i and d[i, j] <= 500],
        }
        for i in range(len(pts))
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_month": "mock",
        "stations": stations,
    }


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def station_amp(lat, lon):
    business = (lat > 40.745) | (lat < 40.712)
    residential = (lat > 40.712) & (lat < 40.735) & (lon > -73.995)
    return np.where(business, 0.42, np.where(residential, -0.42, 0.0))


def van_plan(rng, st, n_vans, amp):
    lat, lon = np.array([s["lat"] for s in st]), np.array([s["lon"] for s in st])
    ids = [s["station_id"] for s in st]
    full, empty = np.where(amp > 0)[0], np.where(amp < 0)[0]
    legs = []
    for v in range(n_vans):
        t, cur, load = 6 * 3600, int(rng.choice(empty)), 0
        while True:
            pickup = load == 0
            nxt = int(rng.choice(full if pickup else empty))
            dist = haversine_m(lat[cur], lon[cur], lat[nxt], lon[nxt]) * 1.3
            t_end = t + int(dist / VAN_SPEED_MPS)
            if t_end + SERVICE_S > 22 * 3600:
                break
            qty = int(rng.integers(8, 20)) if pickup else load
            load = load + qty if pickup else 0
            legs.append(
                {
                    "van_id": v, "t_start": t, "t_end": t_end, "from_station_id": ids[cur],
                    "to_station_id": ids[nxt], "load": load, "action": "pickup" if pickup else "dropoff", "qty": qty,
                    "_km": dist / 1000,
                }
            )
            t, cur = t_end + SERVICE_S, nxt
    return legs


def van_state(legs, v, t, pos):
    own = [leg for leg in legs if leg["van_id"] == v]
    km = sum(leg["_km"] for leg in own if leg["t_end"] <= t)
    if not own or t < own[0]["t_start"] or t >= own[-1]["t_end"] + SERVICE_S:
        sid = own[0]["from_station_id"] if own else None
        return {"van_id": v, "lat": pos[sid][0] if sid else 40.73, "lon": pos[sid][1] if sid else -73.99,
                "load": 0, "status": "off", "target_station_id": None}, km
    for leg in own:
        a, b = pos[leg["from_station_id"]], pos[leg["to_station_id"]]
        load_before = leg["load"] - leg["qty"] if leg["action"] == "pickup" else leg["qty"]
        if leg["t_start"] <= t < leg["t_end"]:
            f = (t - leg["t_start"]) / max(1, leg["t_end"] - leg["t_start"])
            return {"van_id": v, "lat": a[0] + f * (b[0] - a[0]), "lon": a[1] + f * (b[1] - a[1]), "load": load_before,
                    "status": "driving", "target_station_id": leg["to_station_id"]}, km
        if leg["t_end"] <= t < leg["t_end"] + SERVICE_S:
            return {"van_id": v, "lat": b[0], "lon": b[1], "load": leg["load"], "status": "servicing",
                    "target_station_id": leg["to_station_id"]}, km
    last = [leg for leg in own if leg["t_end"] <= t][-1]
    b = pos[last["to_station_id"]]
    return {"van_id": v, "lat": b[0], "lon": b[1], "load": last["load"], "status": "idle", "target_station_id": None}, km


def mock_snapshots(stations, scenario, n_vans, seed=1, stations_file="mock_stations.json"):
    rng = np.random.default_rng(seed)
    st = stations["stations"]
    cap = np.array([s["capacity"] for s in st])
    lat, lon = np.array([s["lat"] for s in st]), np.array([s["lon"] for s in st])
    amp = station_amp(lat, lon) * (0.45 if scenario == "C" else 1.0) * rng.uniform(0.6, 1.4, len(st))
    pos = {s["station_id"]: (s["lat"], s["lon"]) for s in st}
    legs = van_plan(rng, st, n_vans, station_amp(lat, lon)) if n_vans else []
    noise = np.zeros(len(st))
    frames, cum = [], {"rentals_cum": 0, "fail_rent_cum": 0, "fail_return_cum": 0, "walk_redirect_cum": 0}
    for t in range(0, 86400 + 1, STEP_S):
        h = t / 3600
        noise += rng.normal(0, 0.02, len(st))
        fill = 0.5 + amp * (sigmoid((h - 8) * 2.5) - sigmoid((h - 17.5) * 2.5)) + noise
        bikes = np.clip(np.rint(fill * cap), 0, cap).astype(int)
        activity = 0.0 if t == 0 else 2.5 * (np.exp(-((h - 8.5) ** 2) / 2) + np.exp(-((h - 17.5) ** 2) / 3)) + 0.3
        fail_rent = rng.poisson(activity, len(st)) * (bikes == 0)
        fail_return = rng.poisson(activity, len(st)) * (bikes == cap)
        cum["rentals_cum"] += int(rng.poisson(activity * 4 * len(st))) if t else 0
        cum["fail_rent_cum"] += int(fail_rent.sum())
        cum["fail_return_cum"] += int(fail_return.sum())
        cum["walk_redirect_cum"] += int(rng.binomial(int(fail_rent.sum()), 0.4))
        vans, km = [], 0.0
        for v in range(n_vans):
            state, k = van_state(legs, v, t, pos)
            vans.append(state)
            km += k
        frames.append(
            {
                "t": t,
                "bikes": bikes.tolist(),
                "fail_rent": fail_rent.tolist(),
                "fail_return": fail_return.tolist(),
                "vans": vans,
                "kpi": {**cum, "van_km_cum": round(km, 3), "n_empty": int((bikes == 0).sum()),
                        "n_full": int((bikes == cap).sum())},
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "scenario": scenario, "seed": seed, "n_vans": n_vans, "van_capacity": 25, "day_type": "weekday",
            "step_s": STEP_S, "t_start": 0, "t_end": 86400, "generated_by": "mock", "stations_file": stations_file,
            "station_ids": [s["station_id"] for s in st], "params": {},
        },
        "frames": frames,
        "van_legs": [{k: v for k, v in leg.items() if not k.startswith("_")} for leg in legs],
    }


def mock_metrics(rng):
    hours = np.arange(24)
    profile = 0.3 + np.exp(-((hours - 8.5) ** 2) / 2) + 1.2 * np.exp(-((hours - 17.5) ** 2) / 3)
    profile = profile / profile.sum()
    configs = [("A", 0)] + [(s, n) for s in ("B", "C") for n in FLEETS]
    runs, hourly = [], []
    for seed in SEEDS:
        demand = int(rng.normal(75000, 2500))
        base_fail = rng.normal(0.11, 0.008)
        for scenario, n in configs:
            gain = 0 if scenario == "A" else (1 - np.exp(-n / (2.5 if scenario == "C" else 4))) * (0.75 if scenario == "C" else 0.55)
            fr = max(0.005, base_fail * (1 - gain) + rng.normal(0, 0.002))
            fret = fr * 0.6
            fail_rent, fail_return = int(demand * fr), int(demand * fret)
            van_km = 0.0 if n == 0 else float(n * rng.normal(95 if scenario == "B" else 80, 5))
            run_id = f"{scenario}_n{n}_s{seed}"
            runs.append({
                "run_id": run_id, "scenario": scenario, "seed": seed, "n_vans": n, "van_capacity": 25,
                "thr_low": 0.1, "thr_high": 0.9, "horizon_min": 60 if scenario == "C" else 0, "weather": "dry",
                "day_type": "weekday", "demand_total": demand, "rentals_ok": demand - fail_rent,
                "fail_rent": fail_rent, "fail_return": fail_return, "walk_redirect": int(fail_rent * 0.4),
                "empty_station_min": round(fr * 383 * 960 * 0.8, 1), "full_station_min": round(fret * 383 * 960 * 0.8, 1),
                "van_km": round(van_km, 2), "bikes_moved": int(van_km * 3.5),
            })
            for hr in hours:
                d = int(demand * profile[hr])
                w = profile[hr] / profile.max()
                hourly.append({
                    "run_id": run_id, "hour": int(hr), "demand": d, "rentals_ok": d - int(d * fr * w * 1.6),
                    "fail_rent": int(d * fr * w * 1.6), "fail_return": int(d * fret * w * 1.6),
                    "empty_station_min": round(fr * w * 383 * 60, 1), "full_station_min": round(fret * w * 383 * 60, 1),
                })
    return pl.DataFrame(runs), pl.DataFrame(hourly)


def main():
    rng = np.random.default_rng(0)
    MOCK.mkdir(parents=True, exist_ok=True)
    stations = mock_stations(rng)
    contracts.write_json(stations, MOCK / "mock_stations.json")
    ids = [s.station_id for s in contracts.validate_stations(MOCK / "mock_stations.json").stations]
    for scenario, n_vans in (("A", 0), ("C", 4)):
        path = MOCK / f"mock_snapshots_24h_{scenario}.json"
        contracts.write_json(mock_snapshots(stations, scenario, n_vans), path)
        contracts.validate_snapshots(path, ids)
    runs, hourly = mock_metrics(rng)
    contracts.validate_metrics(runs, hourly)
    runs.write_csv(MOCK / "mock_metrics_runs.csv")
    hourly.write_csv(MOCK / "mock_metrics_hourly.csv")
    contracts.export_json_schemas(MOCK / "schemas")


if __name__ == "__main__":
    main()
