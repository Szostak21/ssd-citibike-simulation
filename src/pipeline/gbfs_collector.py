import csv
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from common.config import GBFS_STATION_INFO_URL, GBFS_STATION_STATUS_URL, GBFS_STATUS_DIR
from common.geo import in_area

NY = ZoneInfo("America/New_York")
FIELDS = [
    "ts", "last_reported", "station_id", "num_bikes_available", "num_ebikes_available",
    "num_bikes_disabled", "num_docks_available", "num_docks_disabled", "is_renting", "is_returning",
]


def area_stations():
    st = requests.get(GBFS_STATION_INFO_URL, timeout=60).json()["data"]["stations"]
    mask = in_area([s["lat"] for s in st], [s["lon"] for s in st])
    return {s["station_id"]: s["short_name"] for s, m in zip(st, mask) if m}


def poll_once(mapping):
    data = requests.get(GBFS_STATION_STATUS_URL, timeout=60).json()
    ts = int(data["last_updated"])
    rows = [
        [ts, s.get("last_reported", 0), mapping[s["station_id"]]] + [s.get(f, 0) for f in FIELDS[3:]]
        for s in data["data"]["stations"]
        if s["station_id"] in mapping
    ]
    path = GBFS_STATUS_DIR / f"{datetime.fromtimestamp(ts, NY).date().isoformat()}.csv"
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(FIELDS)
        w.writerows(rows)
    return path, len(rows)


def run(interval_s=300):
    GBFS_STATUS_DIR.mkdir(parents=True, exist_ok=True)
    mapping, mapping_day = area_stations(), datetime.now(NY).date()
    while True:
        start = time.time()
        try:
            if datetime.now(NY).date() != mapping_day:
                mapping, mapping_day = area_stations(), datetime.now(NY).date()
            path, n = poll_once(mapping)
            print(f"{datetime.now(NY):%Y-%m-%d %H:%M:%S} {n} stations -> {path.name}", flush=True)
        except (requests.RequestException, OSError, KeyError, ValueError) as e:
            print(f"{datetime.now(NY):%Y-%m-%d %H:%M:%S} error: {e}", flush=True)
        time.sleep(max(0.0, interval_s - (time.time() - start)))
