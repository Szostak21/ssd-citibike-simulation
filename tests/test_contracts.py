import json

import polars as pl
import pytest
from pydantic import ValidationError

from common.config import MOCK
from common.contracts import (
    SnapshotsFile,
    StationsFile,
    validate_lambda,
    validate_metrics,
    validate_od,
    validate_snapshots,
    validate_stations,
)


def test_mock_contracts_valid():
    st = validate_stations(MOCK / "mock_stations.json")
    ids = [s.station_id for s in st.stations]
    assert len(ids) >= 300
    snap_a = validate_snapshots(MOCK / "mock_snapshots_24h_A.json", ids)
    snap_c = validate_snapshots(MOCK / "mock_snapshots_24h_C.json", ids)
    assert len(snap_a.frames) == 97 and len(snap_c.frames) == 97
    assert snap_c.meta.n_vans == 4 and len(snap_c.van_legs) > 0
    runs = pl.read_csv(MOCK / "mock_metrics_runs.csv")
    hourly = pl.read_csv(MOCK / "mock_metrics_hourly.csv")
    validate_metrics(runs, hourly)


def test_bad_stations_rejected():
    raw = json.loads((MOCK / "mock_stations.json").read_text())
    raw["stations"][0]["idx"] = 99
    with pytest.raises(ValidationError):
        StationsFile.model_validate(raw)


def test_bad_snapshots_rejected():
    raw = json.loads((MOCK / "mock_snapshots_24h_A.json").read_text())
    raw["frames"][1]["bikes"].pop()
    with pytest.raises(ValidationError):
        SnapshotsFile.model_validate(raw)


def test_bad_od_prob_sum_rejected():
    df = pl.DataFrame(
        {
            "day_type": ["weekday"],
            "hour": pl.Series([8], dtype=pl.Int8),
            "origin_id": ["5001.01"],
            "dest_id": ["5002.10"],
            "n_trips": pl.Series([5], dtype=pl.Int32),
            "prob": [0.5],
            "dur_mu": [6.0],
            "dur_sigma": [0.3],
            "dur_median_s": [400.0],
        }
    )
    with pytest.raises(AssertionError):
        validate_od(df, ["5001.01", "5002.10"])


def test_bad_lambda_grid_rejected():
    df = pl.DataFrame(
        {
            "station_id": ["5001.01"],
            "day_type": ["weekday"],
            "slot": pl.Series([0], dtype=pl.Int16),
            "rent_count": pl.Series([1], dtype=pl.Int32),
            "n_days": pl.Series([15], dtype=pl.Int16),
            "lambda_raw": [0.2],
            "exposure_frac": [1.0],
            "lambda": [0.2],
            "return_rate": [0.1],
        }
    )
    with pytest.raises(AssertionError):
        validate_lambda(df, ["5001.01"])
