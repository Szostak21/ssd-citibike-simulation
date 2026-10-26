import polars as pl

from common.config import MOCK, OUTPUTS, PROCESSED, STATIONS_JSON
from common.contracts import validate_metrics, validate_snapshots, validate_stations


def load_stations(use_mock=False):
    path = (MOCK / "mock_stations.json") if use_mock or not STATIONS_JSON.exists() else STATIONS_JSON
    return validate_stations(path).stations


def load_snapshots(scenario="A", n_vans=None, seed=1, use_mock=False):
    if not use_mock:
        n = 0 if scenario == "A" else (n_vans or 4)
        cand = OUTPUTS / "snapshots" / f"{scenario}_n{n}_s{seed}.json"
        if cand.exists():
            return validate_snapshots(cand)
    return validate_snapshots(MOCK / f"mock_snapshots_24h_{scenario}.json")


def load_metrics(use_mock=False):
    r_path = OUTPUTS / "metrics" / "metrics_runs.csv"
    h_path = OUTPUTS / "metrics" / "metrics_hourly.csv"
    if use_mock or not (r_path.exists() and h_path.exists()):
        r_path = MOCK / "mock_metrics_runs.csv"
        h_path = MOCK / "mock_metrics_hourly.csv"
    return validate_metrics(pl.read_csv(r_path), pl.read_csv(h_path))


def load_processed():
    return {
        "stations": validate_stations(STATIONS_JSON).stations,
        "lambda": pl.read_parquet(PROCESSED / "lambda_15min.parquet"),
        "od": pl.read_parquet(PROCESSED / "od_matrix.parquet"),
    }
