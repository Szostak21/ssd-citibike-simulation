import polars as pl

from common.config import DAY_TYPES, EXT, GBFS_STATUS_DIR, MIN_EXPOSURE, SLOTS, STEP_S
from pipeline.trips import calendar_days, day_type_expr

NY = "America/New_York"
MIN_GBFS_DAYS = 7


def scan_gbfs_status(gbfs_dir=GBFS_STATUS_DIR):
    csvs = sorted(gbfs_dir.glob("*.csv"))
    parquets = sorted(gbfs_dir.glob("**/*.parquet"))
    frames = []
    if csvs:
        frames.append(pl.scan_csv([str(f) for f in csvs], schema_overrides={"station_id": pl.String}))
    if parquets:
        frames.append(pl.scan_parquet([str(f) for f in parquets]))
    if not frames:
        return None
    cols = [
        pl.col("ts").cast(pl.Int64),
        pl.col("station_id").cast(pl.String),
        pl.col("num_bikes_available").cast(pl.Int32),
    ]
    return pl.concat([f.select(cols) for f in frames]).unique(["ts", "station_id"])


def exposure_from_gbfs(station_ids, gbfs_dir=GBFS_STATUS_DIR):
    raw = scan_gbfs_status(gbfs_dir)
    if raw is None:
        return None
    st = (
        raw.select(
            "station_id",
            pl.from_epoch("ts", time_unit="s").dt.replace_time_zone("UTC").dt.convert_time_zone(NY).alias("t"),
            (pl.col("num_bikes_available") > 0).alias("available"),
        )
        .with_columns(pl.col("t").dt.date().alias("date"))
        .filter(pl.col("station_id").is_in(list(station_ids)))
        .collect()
    )
    if st["date"].n_unique() < MIN_GBFS_DAYS:
        return None
    return (
        st.with_columns(
            day_type_expr().alias("day_type"),
            ((pl.col("t").dt.hour().cast(pl.Int32) * 3600 + pl.col("t").dt.minute().cast(pl.Int32) * 60) // STEP_S)
            .cast(pl.Int16)
            .alias("slot"),
        )
        .group_by("station_id", "day_type", "slot")
        .agg(pl.col("available").mean().alias("exposure_frac"))
    )


def estimate_lambda(trips_calib, station_ids, exposure=None):
    days = calendar_days("calib").group_by("day_type").agg(pl.len().cast(pl.Int16).alias("n_days"))
    grid = (
        pl.DataFrame({"station_id": list(station_ids) + [EXT]})
        .join(pl.DataFrame({"day_type": list(DAY_TYPES)}), how="cross")
        .join(pl.DataFrame({"slot": pl.arange(0, SLOTS, eager=True).cast(pl.Int16)}), how="cross")
        .join(days, on="day_type", how="inner")
    )
    rents = (
        trips_calib.group_by(pl.col("origin_id").alias("station_id"), "day_type", "slot")
        .agg(pl.len().cast(pl.Int32).alias("rent_count"))
    )
    returns = (
        trips_calib.group_by(pl.col("dest_id").alias("station_id"), "day_type", pl.col("end_slot").alias("slot"))
        .agg(pl.len().alias("return_count"))
    )
    df = (
        grid.join(rents, on=["station_id", "day_type", "slot"], how="left")
        .join(returns, on=["station_id", "day_type", "slot"], how="left")
        .with_columns(pl.col("rent_count").fill_null(0), pl.col("return_count").fill_null(0))
    )
    hours = pl.col("n_days").cast(pl.Float64) * (STEP_S / 3600)
    if exposure is not None:
        df = df.join(exposure, on=["station_id", "day_type", "slot"], how="left")
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias("exposure_frac"))
    return (
        df.with_columns(
            (pl.col("rent_count") / hours).alias("lambda_raw"),
            pl.when(pl.col("station_id") == EXT).then(1.0).otherwise(pl.col("exposure_frac")).fill_null(1.0)
            .clip(MIN_EXPOSURE, 1.0).alias("exposure_frac"),
            (pl.col("return_count") / hours).alias("return_rate"),
        )
        .with_columns((pl.col("lambda_raw") / pl.col("exposure_frac")).alias("lambda"))
        .select("station_id", "day_type", "slot", "rent_count", "n_days", "lambda_raw", "exposure_frac", "lambda", "return_rate")
        .sort("station_id", "day_type", "slot")
    )
