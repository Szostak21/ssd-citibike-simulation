from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
TRIPDATA_DIR = RAW / "tripdata"
GBFS_STATUS_DIR = RAW / "gbfs_status"
STATION_INFO_RAW = RAW / "station_information.json"
EXTERNAL = DATA / "external"
WEATHER_CSV = EXTERNAL / "weather_hourly.csv"
PROCESSED = DATA / "processed"
MOCK = DATA / "mock"
OUTPUTS = DATA / "outputs"

STATIONS_JSON = PROCESSED / "stations.json"
LAMBDA_PARQUET = PROCESSED / "lambda_15min.parquet"
OD_PARQUET = PROCESSED / "od_matrix.parquet"
TRIPS_CLEAN_PARQUET = PROCESSED / "trips_clean.parquet"

SCHEMA_VERSION = "1.0"
SOURCE_MONTH = "2026-09"
TRIPDATA_URL = "https://s3.amazonaws.com/tripdata/202609-citibike-tripdata.zip"
GBFS_STATION_INFO_URL = "https://gbfs.lyft.com/gbfs/1.1/bkn/en/station_information.json"
GBFS_STATION_STATUS_URL = "https://gbfs.lyft.com/gbfs/1.1/bkn/en/station_status.json"
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
CENTRAL_PARK = (40.7829, -73.9654)

MONTH_START = date(2026, 9, 1)
MONTH_END = date(2026, 9, 30)
VALID_START = date(2026, 9, 24)
HOLIDAYS = {date(2026, 9, 7)}

BBOX = {"lat_min": 40.7000, "lat_max": 40.7650, "lon_min": -74.0200, "lon_max": -73.9600}

MANHATTAN_POLYGON = [
    (-74.0000, 40.7757),
    (-73.9819, 40.7689),
    (-73.9617, 40.7613),
    (-73.9555, 40.7590),
    (-73.9610, 40.7550),
    (-73.9650, 40.7450),
    (-73.9670, 40.7350),
    (-73.9670, 40.7250),
    (-73.9705, 40.7130),
    (-73.9775, 40.7075),
    (-73.9900, 40.7065),
    (-74.0040, 40.7005),
    (-74.0120, 40.6985),
    (-74.0200, 40.6995),
    (-74.0240, 40.7050),
    (-74.0230, 40.7200),
    (-74.0190, 40.7350),
    (-74.0140, 40.7500),
    (-74.0060, 40.7700),
]

EXT = "EXT"
STEP_S = 900
SLOTS = 96
DAY_TYPES = ("weekday", "weekend")
MIN_DURATION_S = 60
MAX_DURATION_S = 4 * 3600
NEIGHBOR_RADIUS_M = 500
OD_ALPHA = 5.0
OD_MIN_DUR_SAMPLES = 5
FALLBACK_SPEED_KMH = 12.0
FALLBACK_SIGMA = 0.35
MIN_EXPOSURE = 0.2
GRID_ANGLE_DEG = 29.0
VAN_STATUSES = ("idle", "driving", "servicing", "off")
SCENARIOS = ("A", "B", "C")
