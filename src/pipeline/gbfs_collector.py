import csv
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import requests

from common.config import GBFS_STATION_INFO_URL, GBFS_STATION_STATUS_URL, GBFS_STATUS_DIR
from common.geo import in_area

NY = ZoneInfo("America/New_York")
FIELDS = [
    "ts", "last_reported", "station_id", "num_bikes_available", "num_ebikes_available",
    "num_bikes_disabled", "num_docks_available", "num_docks_disabled", "is_renting", "is_returning",
]
SCHEMA = {
    "ts": pl.Int64,
    "last_reported": pl.Int64,
    "station_id": pl.String,
    "num_bikes_available": pl.Int32,
    "num_ebikes_available": pl.Int32,
    "num_bikes_disabled": pl.Int32,
    "num_docks_available": pl.Int32,
    "num_docks_disabled": pl.Int32,
    "is_renting": pl.Int8,
    "is_returning": pl.Int8,
}


def area_stations():
    st = requests.get(GBFS_STATION_INFO_URL, timeout=60).json()["data"]["stations"]
    mask = in_area([s["lat"] for s in st], [s["lon"] for s in st])
    return {s["station_id"]: s["short_name"] for s, m in zip(st, mask) if m}


def parse_status(data: dict, mapping: dict[str, str]) -> pl.DataFrame:
    ts = int(data["last_updated"])
    rows = [
        [ts, int(s.get("last_reported", 0)), mapping[s["station_id"]]] + [int(s.get(f, 0)) for f in FIELDS[3:]]
        for s in data["data"]["stations"]
        if s["station_id"] in mapping
    ]
    return pl.DataFrame(rows, schema=SCHEMA, orient="row")


def snapshot_time(df: pl.DataFrame) -> datetime:
    return datetime.fromtimestamp(int(df["ts"][0]), NY)


def write_parquet_snapshot(df: pl.DataFrame, out_dir: Path) -> Path:
    t = snapshot_time(df)
    path = Path(out_dir) / t.date().isoformat() / f"{t:%H%M%S}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path, compression="zstd")
    return path


def fetch_status(mapping):
    return parse_status(requests.get(GBFS_STATION_STATUS_URL, timeout=60).json(), mapping)


def harvest_once(out_dir: Path) -> tuple[Path, int]:
    df = fetch_status(area_stations())
    return write_parquet_snapshot(df, out_dir), df.height


def poll_once(mapping):
    df = fetch_status(mapping)
    path = GBFS_STATUS_DIR / f"{snapshot_time(df).date().isoformat()}.csv"
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(FIELDS)
        w.writerows(df.rows())
    return path, df.height


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
