import polars as pl

from common.config import (
    EXT,
    HOLIDAYS,
    MAX_DURATION_S,
    MIN_DURATION_S,
    MONTH_END,
    MONTH_START,
    STEP_S,
    VALID_START,
)


def _to_dt(col):
    return pl.col(col).str.to_datetime(time_unit="ms", strict=False)


def _slot(col):
    c = pl.col(col)
    secs = c.dt.hour().cast(pl.Int32) * 3600 + c.dt.minute().cast(pl.Int32) * 60 + c.dt.second().cast(pl.Int32)
    return (secs // STEP_S).cast(pl.Int16)


def day_type_expr(date_col="date"):
    d = pl.col(date_col)
    weekend = (d.dt.weekday() >= 6) | d.is_in(list(HOLIDAYS))
    return pl.when(weekend).then(pl.lit("weekend")).otherwise(pl.lit("weekday"))


def split_expr(date_col="date"):
    return pl.when(pl.col(date_col) < VALID_START).then(pl.lit("calib")).otherwise(pl.lit("valid"))


def clean_trips(trips, station_ids, aliases=None):
    area = list(station_ids)
    aliases = aliases or {}
    o, d = pl.col("start_station_id"), pl.col("end_station_id")
    return (
        trips.select(
            "ride_id",
            "rideable_type",
            "member_casual",
            _to_dt("started_at").alias("started_at"),
            _to_dt("ended_at").alias("ended_at"),
            o.replace(aliases),
            d.replace(aliases),
        )
        .drop_nulls(["started_at", "ended_at", "start_station_id", "end_station_id"])
        .with_columns(
            ((pl.col("ended_at") - pl.col("started_at")).dt.total_seconds()).cast(pl.Int32).alias("duration_s"),
            o.is_in(area).alias("o_in"),
            d.is_in(area).alias("d_in"),
            pl.col("started_at").dt.date().alias("date"),
        )
        .filter(pl.col("o_in") | pl.col("d_in"))
        .filter(pl.col("date").is_between(MONTH_START, MONTH_END))
        .filter(pl.col("duration_s").is_between(0, MAX_DURATION_S))
        .filter(~((o == d) & (pl.col("duration_s") < MIN_DURATION_S)))
        .with_columns(
            pl.when(pl.col("o_in")).then(o).otherwise(pl.lit(EXT)).alias("origin_id"),
            pl.when(pl.col("d_in")).then(d).otherwise(pl.lit(EXT)).alias("dest_id"),
            day_type_expr().alias("day_type"),
            split_expr().alias("split"),
            _slot("started_at").alias("slot"),
            _slot("ended_at").alias("end_slot"),
            pl.col("started_at").dt.hour().cast(pl.Int8).alias("hour"),
        )
        .select(
            "ride_id", "rideable_type", "member_casual", "started_at", "ended_at", "duration_s",
            "origin_id", "dest_id", "date", "day_type", "split", "slot", "end_slot", "hour",
        )
    )


def calendar_days(split="calib"):
    days = pl.date_range(MONTH_START, MONTH_END, eager=True).alias("date").to_frame()
    days = days.with_columns(day_type_expr().alias("day_type"), split_expr().alias("split"))
    return days.filter(pl.col("split") == split)
