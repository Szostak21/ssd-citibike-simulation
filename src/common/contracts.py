import json
from pathlib import Path
from typing import Literal

import polars as pl
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, model_validator

from common.config import DAY_TYPES, EXT, SCHEMA_VERSION, SLOTS

Strict = ConfigDict(extra="forbid")


class Station(BaseModel):
    model_config = Strict
    idx: int
    station_id: str
    gbfs_id: str | None
    name: str
    lat: float
    lon: float
    capacity: int
    capacity_source: Literal["gbfs", "imputed"]
    aliases: list[str]
    neighbors: list[str]


class StationsFile(BaseModel):
    model_config = Strict
    schema_version: Literal["1.0"]
    generated_at: str
    source_month: str
    stations: list[Station]

    @model_validator(mode="after")
    def check(self):
        ids = [s.station_id for s in self.stations]
        assert len(ids) == len(set(ids)), "duplicate station_id"
        assert EXT not in ids, "EXT is reserved"
        assert [s.idx for s in self.stations] == list(range(len(ids))), "idx must be 0..N-1 in order"
        known = set(ids)
        aliases = [a for s in self.stations for a in s.aliases]
        assert len(aliases) == len(set(aliases)) and not set(aliases) & known, "aliases must be unique and not ids"
        for s in self.stations:
            assert s.capacity > 0, f"capacity <= 0 for {s.station_id}"
            assert set(s.neighbors) <= known and s.station_id not in s.neighbors, f"bad neighbors {s.station_id}"
        return self


class Van(BaseModel):
    model_config = Strict
    van_id: int
    lat: float
    lon: float
    load: int
    status: Literal["idle", "driving", "servicing", "off"]
    target_station_id: str | None


class Kpi(BaseModel):
    model_config = Strict
    rentals_cum: int
    fail_rent_cum: int
    fail_return_cum: int
    walk_redirect_cum: int
    van_km_cum: float
    n_empty: int
    n_full: int


class Frame(BaseModel):
    model_config = Strict
    t: int
    bikes: list[int]
    fail_rent: list[int]
    fail_return: list[int]
    vans: list[Van]
    kpi: Kpi


class VanLeg(BaseModel):
    model_config = Strict
    van_id: int
    t_start: int
    t_end: int
    from_station_id: str
    to_station_id: str
    load: int
    action: Literal["pickup", "dropoff"]
    qty: int


class SnapshotMeta(BaseModel):
    model_config = Strict
    scenario: Literal["A", "B", "C"]
    seed: int
    n_vans: int
    van_capacity: int
    day_type: Literal["weekday", "weekend"]
    step_s: int
    t_start: int
    t_end: int
    generated_by: Literal["mock", "engine"]
    stations_file: str
    station_ids: list[str]
    params: dict


class SnapshotsFile(BaseModel):
    model_config = Strict
    schema_version: Literal["1.0"]
    meta: SnapshotMeta
    frames: list[Frame]
    van_legs: list[VanLeg]

    @model_validator(mode="after")
    def check(self):
        m = self.meta
        n = len(m.station_ids)
        expected_t = list(range(m.t_start, m.t_end + 1, m.step_s))
        assert [f.t for f in self.frames] == expected_t, "frames must cover t_start..t_end every step_s"
        prev = None
        for f in self.frames:
            assert len(f.bikes) == len(f.fail_rent) == len(f.fail_return) == n, f"array length != N at t={f.t}"
            assert min(f.bikes, default=0) >= 0, f"negative bikes at t={f.t}"
            assert len(f.vans) == m.n_vans, f"vans != n_vans at t={f.t}"
            assert all(0 <= v.load <= m.van_capacity for v in f.vans), f"van load out of range at t={f.t}"
            if prev is not None:
                for k in ("rentals_cum", "fail_rent_cum", "fail_return_cum", "walk_redirect_cum", "van_km_cum"):
                    assert getattr(f.kpi, k) >= getattr(prev.kpi, k), f"{k} decreasing at t={f.t}"
            prev = f
        assert sum(self.frames[0].fail_rent) == sum(self.frames[0].fail_return) == 0, "frame 0 must have zero fails"
        ids = set(m.station_ids)
        for leg in self.van_legs:
            assert leg.t_start <= leg.t_end and leg.van_id < m.n_vans, "bad van_leg"
            assert leg.from_station_id in ids and leg.to_station_id in ids, "van_leg station unknown"
        return self


LAMBDA_SCHEMA = {
    "station_id": pl.String,
    "day_type": pl.String,
    "slot": pl.Int16,
    "rent_count": pl.Int32,
    "n_days": pl.Int16,
    "lambda_raw": pl.Float64,
    "exposure_frac": pl.Float64,
    "lambda": pl.Float64,
    "return_rate": pl.Float64,
}

OD_SCHEMA = {
    "day_type": pl.String,
    "hour": pl.Int8,
    "origin_id": pl.String,
    "dest_id": pl.String,
    "n_trips": pl.Int32,
    "prob": pl.Float64,
    "dur_mu": pl.Float64,
    "dur_sigma": pl.Float64,
    "dur_median_s": pl.Float64,
}

METRICS_RUNS_COLUMNS = [
    "run_id", "scenario", "seed", "n_vans", "van_capacity", "thr_low", "thr_high", "horizon_min",
    "weather", "day_type", "demand_total", "rentals_ok", "fail_rent", "fail_return", "walk_redirect",
    "empty_station_min", "full_station_min", "van_km", "bikes_moved",
]

METRICS_HOURLY_COLUMNS = [
    "run_id", "hour", "demand", "rentals_ok", "fail_rent", "fail_return", "empty_station_min",
    "full_station_min",
]


def _check_schema(df, schema):
    got = dict(df.schema)
    for col, dtype in schema.items():
        assert col in got, f"missing column {col}"
        assert got[col] == dtype, f"column {col}: {got[col]} != {dtype}"
    assert set(got) == set(schema), f"unexpected columns {set(got) - set(schema)}"


def validate_stations(path):
    return StationsFile.model_validate_json(Path(path).read_text())


def validate_lambda(df, station_ids):
    _check_schema(df, LAMBDA_SCHEMA)
    assert df.null_count().sum_horizontal().item() == 0, "nulls in lambda"
    assert set(df["station_id"].unique()) == set(station_ids) | {EXT}, "station set mismatch"
    assert set(df["day_type"].unique()) <= set(DAY_TYPES)
    assert df["slot"].min() >= 0 and df["slot"].max() < SLOTS
    assert df.height == df.select("station_id", "day_type", "slot").unique().height, "duplicate keys"
    assert df.height == (len(station_ids) + 1) * df["day_type"].n_unique() * SLOTS, "grid not full"
    assert (df["lambda_raw"] >= 0).all() and (df["lambda"] >= df["lambda_raw"] - 1e-12).all()
    assert ((df["exposure_frac"] > 0) & (df["exposure_frac"] <= 1)).all()
    assert (df["return_rate"] >= 0).all()
    return df


def validate_od(df, station_ids):
    _check_schema(df, OD_SCHEMA)
    assert df.null_count().sum_horizontal().item() == 0, "nulls in od"
    known = set(station_ids) | {EXT}
    assert set(df["origin_id"].unique()) <= known and set(df["dest_id"].unique()) <= known
    assert df.filter((pl.col("origin_id") == EXT) & (pl.col("dest_id") == EXT)).height == 0, "EXT->EXT"
    assert (df["prob"] > 0).all() and (df["dur_sigma"] > 0).all()
    assert df["hour"].min() >= 0 and df["hour"].max() <= 23
    sums = df.group_by("day_type", "hour", "origin_id").agg(pl.col("prob").sum())
    assert ((sums["prob"] - 1).abs() < 1e-9).all(), "prob rows must sum to 1"
    return df


def validate_snapshots(path, stations_ids=None):
    snap = SnapshotsFile.model_validate_json(Path(path).read_text())
    if stations_ids is not None:
        assert snap.meta.station_ids == list(stations_ids), "station_ids must follow stations.json idx order"
    return snap


def validate_metrics(runs, hourly):
    assert list(runs.columns) == METRICS_RUNS_COLUMNS, "metrics_runs columns"
    assert list(hourly.columns) == METRICS_HOURLY_COLUMNS, "metrics_hourly columns"
    assert runs["run_id"].n_unique() == runs.height
    assert set(runs["scenario"].unique()) <= {"A", "B", "C"}
    assert set(hourly["run_id"].unique()) <= set(runs["run_id"].unique())
    assert hourly["hour"].min() >= 0 and hourly["hour"].max() <= 23
    return runs, hourly


def write_parquet(df, path):
    tbl = df.to_arrow()
    tbl = tbl.replace_schema_metadata({**(tbl.schema.metadata or {}), b"schema_version": SCHEMA_VERSION.encode()})
    pq.write_table(tbl, path)


def write_json(obj, path):
    Path(path).write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def export_json_schemas(out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stations.schema.json").write_text(json.dumps(StationsFile.model_json_schema(), indent=2))
    (out_dir / "snapshots.schema.json").write_text(json.dumps(SnapshotsFile.model_json_schema(), indent=2))
