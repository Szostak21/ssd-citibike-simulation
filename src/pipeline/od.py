import numpy as np
import polars as pl

from common.config import EXT, FALLBACK_SIGMA, FALLBACK_SPEED_KMH, OD_ALPHA, OD_MIN_DUR_SAMPLES
from common.geo import manhattan_m

MIN_SIGMA = 0.05


def _log_stats(df):
    return df.agg(
        pl.len().alias("n"),
        pl.col("ld").mean().alias("mu"),
        pl.col("ld").std().fill_null(FALLBACK_SIGMA).alias("sigma"),
    )


def duration_params(trips_calib, stations):
    t = trips_calib.with_columns(pl.col("duration_s").clip(30, None).cast(pl.Float64).log().alias("ld"))
    pairs = _log_stats(t.group_by("origin_id", "dest_id"))
    pool_in = _log_stats(t.filter(pl.col("origin_id") == EXT).group_by(pl.lit(1)))
    pool_out = _log_stats(t.filter(pl.col("dest_id") == EXT).group_by(pl.lit(1)))
    pool_loop = _log_stats(t.filter(pl.col("origin_id") == pl.col("dest_id")).group_by(pl.lit(1)))

    coords = stations.select("station_id", "lat", "lon")
    pairs = (
        pairs.join(coords.rename({"station_id": "origin_id", "lat": "o_lat", "lon": "o_lon"}), on="origin_id", how="left")
        .join(coords.rename({"station_id": "dest_id", "lat": "d_lat", "lon": "d_lon"}), on="dest_id", how="left")
    )
    dist = manhattan_m(pairs["o_lat"].to_numpy(), pairs["o_lon"].to_numpy(), pairs["d_lat"].to_numpy(), pairs["d_lon"].to_numpy())
    fb_mu = np.log(np.maximum(np.nan_to_num(dist, nan=0.0) / (FALLBACK_SPEED_KMH / 3.6), 120.0))
    pairs = pairs.with_columns(pl.Series("fb_mu", fb_mu))

    few = pl.col("n") < OD_MIN_DUR_SAMPLES
    is_in, is_out, is_loop = pl.col("origin_id") == EXT, pl.col("dest_id") == EXT, pl.col("origin_id") == pl.col("dest_id")

    def pick(field, fb):
        return (
            pl.when(~few).then(pl.col(field))
            .when(is_in).then(pool_in[field].item())
            .when(is_out).then(pool_out[field].item())
            .when(is_loop).then(pool_loop[field].item())
            .otherwise(fb)
        )

    return pairs.select(
        "origin_id",
        "dest_id",
        pick("mu", pl.col("fb_mu")).alias("dur_mu"),
        pick("sigma", pl.lit(FALLBACK_SIGMA)).clip(MIN_SIGMA, None).alias("dur_sigma"),
    ).with_columns(pl.col("dur_mu").exp().alias("dur_median_s"))


def estimate_od(trips_calib, stations):
    keys = ["day_type", "origin_id", "dest_id"]
    hourly = trips_calib.group_by("day_type", "hour", "origin_id", "dest_id").agg(pl.len().cast(pl.Int32).alias("n_trips"))
    daily = (
        hourly.group_by(keys)
        .agg(pl.col("n_trips").sum().alias("n_day"))
        .with_columns((pl.col("n_day") / pl.col("n_day").sum().over("day_type", "origin_id")).alias("p_day"))
    )
    hours = pl.DataFrame({"hour": pl.arange(0, 24, eager=True).cast(pl.Int8)})
    od = (
        daily.select(*keys, "p_day")
        .join(hours, how="cross")
        .join(hourly, on=["day_type", "hour", "origin_id", "dest_id"], how="left")
        .with_columns(pl.col("n_trips").fill_null(0))
        .with_columns(pl.col("n_trips").sum().over("day_type", "hour", "origin_id").alias("n_origin_h"))
        .with_columns(((pl.col("n_trips") + OD_ALPHA * pl.col("p_day")) / (pl.col("n_origin_h") + OD_ALPHA)).alias("prob"))
        .join(duration_params(trips_calib, stations), on=["origin_id", "dest_id"], how="left")
        .select("day_type", "hour", "origin_id", "dest_id", "n_trips", "prob", "dur_mu", "dur_sigma", "dur_median_s")
        .sort("day_type", "hour", "origin_id", "dest_id")
    )
    return od
