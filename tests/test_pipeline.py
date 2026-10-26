from pathlib import Path

import polars as pl

from common.config import EXT
from common.contracts import validate_lambda, validate_od
from common.geo import haversine_m, in_area, manhattan_m
from pipeline.demand import estimate_lambda
from pipeline.od import estimate_od
from pipeline.stations import build_stations, load_gbfs_info, scan_trips
from pipeline.trips import clean_trips

FIXTURES = Path(__file__).parent / "fixtures"


def test_polygon_boundaries():
    inside = [
        (40.7580, -73.9855),  # Times Square
        (40.7060, -74.0088),  # Wall Street
        (40.7680, -73.9819),  # Columbus Circle (59th St)
    ]
    outside = [
        (40.7033, -73.9881),  # DUMBO Brooklyn
        (40.7143, -73.9614),  # Williamsburg Brooklyn
        (40.7178, -74.0339),  # Jersey City
        (40.7812, -73.9665),  # Central Park (above 59th St)
    ]
    assert all(in_area([p[0] for p in inside], [p[1] for p in inside]))
    assert not any(in_area([p[0] for p in outside], [p[1] for p in outside]))


def test_distances():
    d_hav = haversine_m(40.706, -74.011, 40.758, -73.985)
    d_man = manhattan_m(40.706, -74.011, 40.758, -73.985)
    assert 5500 < d_hav < 6800
    assert d_man >= d_hav


def test_pipeline_on_fixtures():
    trips = scan_trips(FIXTURES)
    gbfs = load_gbfs_info(FIXTURES / "mini_station_information.json")
    st = build_stations(trips, gbfs)
    ids = [s["station_id"] for s in st["stations"]]
    assert ids == ["5001.01", "5002.10", "5003.05"]
    assert "9999.01" not in ids

    clean = clean_trips(trips, ids).collect()
    ride_ids = set(clean["ride_id"].to_list())
    assert "BAD_SHORT_SAME" not in ride_ids
    assert "BAD_LONG" not in ride_ids
    assert "BAD_OUT_OUT" not in ride_ids
    assert "OK_SHORT_DIFF" in ride_ids
    assert "OK_EXT_IN" in ride_ids

    ext_in = clean.filter(pl.col("ride_id") == "OK_EXT_IN").row(0, named=True)
    assert ext_in["origin_id"] == EXT and ext_in["dest_id"] == "5001.01"

    ext_out = clean.filter(pl.col("ride_id") == "E000").row(0, named=True)
    assert ext_out["origin_id"] == "5003.05" and ext_out["dest_id"] == EXT

    # Labor Day 2026-09-07 treated as weekend
    labor = clean.filter(pl.col("ride_id") == "S000").row(0, named=True)
    assert labor["day_type"] == "weekend" and labor["split"] == "calib"

    # Sep 25 in validation split
    valid_row = clean.filter(pl.col("ride_id") == "S006").row(0, named=True)
    assert valid_row["split"] == "valid"

    calib = clean.filter(pl.col("split") == "calib")
    lam = validate_lambda(estimate_lambda(calib, ids), ids)
    total_expected = (lam["lambda_raw"] * lam["n_days"] * 0.25).sum()
    assert abs(total_expected - calib.height) < 1e-6

    st_df = pl.DataFrame(st["stations"]).select("station_id", "lat", "lon")
    od = validate_od(estimate_od(calib, st_df), ids)
    assert od.height > 0
