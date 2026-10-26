import numpy as np
from matplotlib.path import Path as MplPath

from common.config import GRID_ANGLE_DEG, MANHATTAN_POLYGON

R_EARTH_M = 6_371_000.0
_POLY = MplPath(MANHATTAN_POLYGON)


def in_area(lat, lon):
    pts = np.column_stack([np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)])
    return _POLY.contains_points(pts)


def haversine_m(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * R_EARTH_M * np.arcsin(np.sqrt(a))


def manhattan_m(lat1, lon1, lat2, lon2):
    lat0 = np.radians((np.asarray(lat1) + np.asarray(lat2)) / 2)
    dx = np.radians(np.asarray(lon2) - np.asarray(lon1)) * np.cos(lat0) * R_EARTH_M
    dy = np.radians(np.asarray(lat2) - np.asarray(lat1)) * R_EARTH_M
    a = np.radians(GRID_ANGLE_DEG)
    u = dx * np.cos(a) - dy * np.sin(a)
    v = dx * np.sin(a) + dy * np.cos(a)
    return np.abs(u) + np.abs(v)
