import json
from datetime import UTC, datetime

import numpy as np
import polars as pl

from common.config import EXT, NEIGHBOR_RADIUS_M, SCHEMA_VERSION, SOURCE_MONTH, STATION_INFO_RAW, TRIPDATA_DIR
from common.geo import haversine_m, in_area

TRIP_SCHEMA = {
    "ride_id": pl.String,
    "rideable_type": pl.String,
    "started_at": pl.String,
    "ended_at": pl.String,
    "start_station_name": pl.String,
    "start_station_id": pl.String,
    "end_station_name": pl.String,
    "end_station_id": pl.String,
    "start_lat": pl.Float64,
    "start_lng": pl.Float64,
    "end_lat": pl.Float64,
    "end_lng": pl.Float64,
    "member_casual": pl.String,
}

MIN_MONTHLY_TRIPS = 30


def _norm_id(col):
    c = pl.col(col)
    return pl.when(c.str.contains(r"^\d+\.\d$")).then(c + "0").otherwise(c).alias(col)


def scan_trips(tripdata_dir=TRIPDATA_DIR):
    files = sorted(str(p) for p in tripdata_dir.glob("*.csv"))
    return pl.scan_csv(files, schema=TRIP_SCHEMA).with_columns(_norm_id("start_station_id"), _norm_id("end_station_id"))


def load_gbfs_info(path=STATION_INFO_RAW):
    st = json.loads(path.read_text())["data"]["stations"]
    return pl.DataFrame(
        {
            "station_id": [s.get("short_name") for s in st],
            "gbfs_id": [s["station_id"] for s in st],
            "gbfs_name": [s["name"] for s in st],
            "gbfs_lat": [float(s["lat"]) for s in st],
            "gbfs_lon": [float(s["lon"]) for s in st],
            "gbfs_capacity": [int(s.get("capacity") or 0) for s in st],
        }
    ).filter(pl.col("station_id").is_not_null()).unique("station_id", keep="first")


def station_catalog(trips, gbfs):
    ends = [
        trips.select(
            pl.col(f"{p}_station_id").alias("station_id"),
            pl.col(f"{p}_station_name").alias("name"),
            pl.col(f"{p}_lat").alias("lat"),
            pl.col(f"{p}_lng").alias("lon"),
        )
        for p in ("start", "end")
    ]
    cat = (
        pl.concat(ends)
        .filter(pl.col("station_id").is_not_null())
        .group_by("station_id")
        .agg(
            pl.col("name").mode().first().alias("name"),
            pl.col("lat").median().alias("trip_lat"),
            pl.col("lon").median().alias("trip_lon"),
            pl.len().alias("n_trips"),
        )
        .collect()
        .join(gbfs, on="station_id", how="left")
        .with_columns(
            pl.coalesce("gbfs_lat", "trip_lat").alias("lat"),
            pl.coalesce("gbfs_lon", "trip_lon").alias("lon"),
        )
    )
    return cat


def build_stations(trips, gbfs):
    cat = station_catalog(trips, gbfs).filter(pl.col("station_id") != EXT)
    cat = cat.filter(pl.Series(in_area(cat["lat"].to_numpy(), cat["lon"].to_numpy())))
    cat = (
        cat.with_columns(pl.col("gbfs_capacity").fill_null(0))
        .sort([pl.col("gbfs_capacity") > 0, "n_trips"], descending=True)
        .group_by("name", maintain_order=True)
        .agg(
            pl.col("station_id", "gbfs_id", "gbfs_name", "lat", "lon").first(),
            pl.col("gbfs_capacity").sum(),
            pl.col("n_trips").sum(),
            pl.col("station_id").slice(1).sort().alias("aliases"),
        )
        .filter(pl.col("n_trips") >= MIN_MONTHLY_TRIPS)
    )
    median_cap = int(cat.filter(pl.col("gbfs_capacity") > 0)["gbfs_capacity"].median() or 20)
    cat = cat.with_columns(
        pl.when(pl.col("gbfs_capacity") > 0).then(pl.lit("gbfs")).otherwise(pl.lit("imputed")).alias("capacity_source"),
        pl.when(pl.col("gbfs_capacity") > 0).then(pl.col("gbfs_capacity")).otherwise(median_cap).alias("capacity"),
    ).sort("station_id")

    lat, lon = cat["lat"].to_numpy(), cat["lon"].to_numpy()
    d = haversine_m(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
    ids = cat["station_id"].to_list()
    neighbors = []
    for i in range(len(ids)):
        order = np.argsort(d[i])
        neighbors.append([ids[j] for j in order if j != i and d[i, j] <= NEIGHBOR_RADIUS_M])

    stations = [
        {
            "idx": i,
            "station_id": r["station_id"],
            "gbfs_id": r["gbfs_id"],
            "name": r["gbfs_name"] or r["name"],
            "lat": round(r["lat"], 6),
            "lon": round(r["lon"], 6),
            "capacity": int(r["capacity"]),
            "capacity_source": r["capacity_source"],
            "aliases": r["aliases"],
            "neighbors": neighbors[i],
        }
        for i, r in enumerate(cat.iter_rows(named=True))
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_month": SOURCE_MONTH,
        "stations": stations,
    }
