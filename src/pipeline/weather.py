import polars as pl

from common.config import TRIPS_CLEAN_PARQUET, WEATHER_CSV

RAIN_THR_MM = 0.5
DEFAULT_RAIN_MULT = 0.72


def estimate_rain_multiplier(trips_path=TRIPS_CLEAN_PARQUET, weather_path=WEATHER_CSV) -> float:
    if not (trips_path.exists() and weather_path.exists()):
        return DEFAULT_RAIN_MULT
    wx = (
        pl.read_csv(weather_path)
        .with_columns(pl.col("time").str.to_datetime(strict=False).alias("dt"))
        .select(
            pl.col("dt").dt.date().alias("date"),
            pl.col("dt").dt.hour().cast(pl.Int8).alias("hour"),
            pl.col("precip_mm").cast(pl.Float64),
        )
    )
    hourly = (
        pl.scan_parquet(trips_path)
        .filter((pl.col("split") == "calib") & pl.col("hour").is_between(6, 21))
        .group_by("date", "day_type", "hour")
        .agg(pl.len().alias("n"))
        .collect()
        .join(wx, on=["date", "hour"], how="inner")
        .with_columns((pl.col("precip_mm") >= RAIN_THR_MM).alias("rain"))
    )
    if hourly.filter(pl.col("rain")).height < 3:
        return DEFAULT_RAIN_MULT
    by_slot = (
        hourly.group_by("day_type", "hour", "rain")
        .agg(pl.col("n").mean().alias("mean_n"))
        .pivot(on="rain", index=["day_type", "hour"], values="mean_n")
    )
    if "true" not in by_slot.columns or "false" not in by_slot.columns:
        return DEFAULT_RAIN_MULT
    ratios = by_slot.drop_nulls(["true", "false"]).filter(pl.col("false") > 0)
    if ratios.is_empty():
        return DEFAULT_RAIN_MULT
    mult = float((ratios["true"] / ratios["false"]).median())
    return float(min(max(mult, 0.55), 0.90))


def weather_multiplier(weather: str = "dry", trips_path=TRIPS_CLEAN_PARQUET, weather_path=WEATHER_CSV) -> float:
    if weather == "dry":
        return 1.0
    if weather == "rain":
        return estimate_rain_multiplier(trips_path, weather_path)
    raise ValueError(f"Unknown weather: {weather}")
