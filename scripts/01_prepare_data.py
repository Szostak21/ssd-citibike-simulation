import time

import polars as pl

from common import contracts
from common.config import LAMBDA_PARQUET, OD_PARQUET, PROCESSED, STATIONS_JSON, TRIPS_CLEAN_PARQUET
from pipeline.demand import estimate_lambda, exposure_from_gbfs
from pipeline.od import estimate_od
from pipeline.stations import build_stations, load_gbfs_info, scan_trips
from pipeline.trips import clean_trips


def main():
    t0 = time.time()
    PROCESSED.mkdir(parents=True, exist_ok=True)
    trips = scan_trips()

    st = build_stations(trips, load_gbfs_info())
    contracts.write_json(st, STATIONS_JSON)
    stations = contracts.validate_stations(STATIONS_JSON).stations
    ids = [s.station_id for s in stations]
    aliases = {a: s.station_id for s in stations for a in s.aliases}

    clean = clean_trips(trips, ids, aliases).collect()
    clean.write_parquet(TRIPS_CLEAN_PARQUET)
    calib = clean.filter(pl.col("split") == "calib")

    exposure = exposure_from_gbfs(ids)
    lam = contracts.validate_lambda(estimate_lambda(calib, ids, exposure), ids)
    contracts.write_parquet(lam, LAMBDA_PARQUET)

    stations_df = pl.DataFrame(st["stations"]).select("station_id", "lat", "lon")
    od = contracts.validate_od(estimate_od(calib, stations_df), ids)
    contracts.write_parquet(od, OD_PARQUET)

    raw_n = trips.select(pl.len()).collect().item()
    n_cap = sum(s["capacity_source"] == "gbfs" for s in st["stations"])
    print(f"stations: {len(ids)} (gbfs capacity: {n_cap})")
    print(f"raw trips: {raw_n:,}  area trips clean: {clean.height:,}")
    print(clean.group_by("split", "day_type").len().sort("split", "day_type"))
    print(f"exposure from gbfs: {exposure is not None}  od rows: {od.height:,}  time: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
