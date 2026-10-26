import io
import zipfile

import polars as pl
import requests

from common.config import (
    CENTRAL_PARK,
    GBFS_STATION_INFO_URL,
    MONTH_END,
    MONTH_START,
    STATION_INFO_RAW,
    TRIPDATA_DIR,
    TRIPDATA_URL,
    WEATHER_CSV,
    WEATHER_URL,
)


def download_tripdata(force=False):
    TRIPDATA_DIR.mkdir(parents=True, exist_ok=True)
    if list(TRIPDATA_DIR.glob("*.csv")) and not force:
        return
    r = requests.get(TRIPDATA_URL, timeout=600)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for name in z.namelist():
            if name.endswith(".csv") and not name.startswith("__MACOSX"):
                (TRIPDATA_DIR / name.split("/")[-1]).write_bytes(z.read(name))


def download_station_information(force=False):
    if STATION_INFO_RAW.exists() and not force:
        return
    r = requests.get(GBFS_STATION_INFO_URL, timeout=60)
    r.raise_for_status()
    STATION_INFO_RAW.parent.mkdir(parents=True, exist_ok=True)
    STATION_INFO_RAW.write_bytes(r.content)


def download_weather(force=False):
    if WEATHER_CSV.exists() and not force:
        return
    params = {
        "latitude": CENTRAL_PARK[0],
        "longitude": CENTRAL_PARK[1],
        "start_date": MONTH_START.isoformat(),
        "end_date": MONTH_END.isoformat(),
        "hourly": "temperature_2m,precipitation",
        "timezone": "America/New_York",
    }
    r = requests.get(WEATHER_URL, params=params, timeout=60)
    r.raise_for_status()
    h = r.json()["hourly"]
    df = pl.DataFrame({"time": h["time"], "temp_c": h["temperature_2m"], "precip_mm": h["precipitation"]})
    WEATHER_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(WEATHER_CSV)
