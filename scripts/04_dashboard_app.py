"""Interaktywny odtwarzacz przebiegów symulacji Citi Bike (dolny Manhattan) — Streamlit + PyDeck.

Funkcje:
- mapa 3D w Dark Mode (CARTO Dark Matter) z kolumnami stacji (wysokość = rowery, kolor = zapełnienie),
- widok Split-Screen (np. Scenariusz A vs B w tej samej sekundzie symulacji) lub pojedyncza mapa,
- płynna animacja furgonetek po siatce ulic Manhattanu (TripsLayer + ArcLayer + beacony 3D),
- suwak czasu z odtwarzaczem (play / pauza / skoki ±15 min), interpolacja między klatkami 15-min,
- karty KPI, wykres narastającego nieobsłużonego popytu, ranking stacji krytycznych, dziennik vanów.

Dane wejściowe zgodnie z `CONTRACTS.md` (sekcja 4 `snapshots_24h`):
- `data/outputs/snapshots/{scenario}_n{vans}_s{seed}.json` (silnik DES), a w razie ich braku
- `data/mock/mock_snapshots_24h_{A|B}.json` (mocki).

Uruchomienie (z katalogu głównego repozytorium):
    uv run streamlit run scripts/04_dashboard_app.py
"""

from __future__ import annotations

import functools
import html
import inspect
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st
from pydeck.types import String as JsString

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from common.config import (  # noqa: E402
    GRID_ANGLE_DEG,
    MANHATTAN_POLYGON,
    MOCK,
    OUTPUTS,
    PROCESSED,
    STATIONS_JSON,
)
from common.contracts import validate_snapshots, validate_stations  # noqa: E402

st.set_page_config(
    page_title="Citi Bike NYC · Odtwarzacz symulacji DES — dolny Manhattan",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────────────────────────────────────
# Stałe
# ──────────────────────────────────────────────────────────────────────────────
SNAP_DIR = OUTPUTS / "snapshots"
RUN_RE = re.compile(r"^(?P<scenario>[AB])_n(?P<n>\d+)_s(?P<seed>\d+)\.json$")
DAY_MIN = 24 * 60
SERVICE_GUESS_S = 300  # czas obsługi stacji po ostatnim odcinku (jak w make_mocks.SERVICE_S)
MAX_SERVICE_S = 1800  # dłuższa przerwa między odcinkami = postój (stan bierzemy z klatek)
TRAIL_S = 900  # długość "ogona" furgonetki w TripsLayer [s symulacji]
BEACON_M = 380  # wysokość beaconu furgonetki [m]
CARTO_DARK = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
VIEW_CENTER = (40.7350, -73.9905)

SCENARIOS = {
    "A": {"name": "Bazowy", "desc": "Brak furgonetek w dzień, nocny reset stacji", "accent": "#FFB547"},
    "B": {"name": "Reaktywny", "desc": "Alarm przy przekroczeniu progów zapełnienia", "accent": "#FF6FB5"},
}

C_EMPTY = (255, 69, 96)
C_FULL = (155, 92, 255)
GRAD_X = np.array([0.0, 0.22, 0.78, 1.0])
GRAD_RGB = np.array([[255, 184, 76], [46, 229, 157], [46, 229, 157], [88, 166, 255]], dtype=float)
HEAT_RANGE = [[255, 214, 102], [255, 159, 67], [255, 99, 99], [235, 59, 130], [170, 60, 220], [120, 40, 200]]

VAN_COLORS = {
    "pickup": (255, 170, 60),  # jedzie po rowery
    "dropoff": (61, 220, 255),  # wiezie rowery
    "servicing": (255, 236, 140),
    "idle": (150, 160, 185),
}
VAN_STATUS_PL = {"driving": "w trasie", "servicing": "obsługa stacji", "idle": "postój", "off": "poza zmianą"}
KPI_COLS = ["rentals_cum", "fail_rent_cum", "fail_return_cum", "walk_redirect_cum", "van_km_cum", "n_empty", "n_full"]

TOOLTIP = {
    "html": (
        "<div style='font-family:Inter,sans-serif;min-width:210px'>"
        "<div style='font-weight:700;font-size:13px;margin-bottom:4px;color:#fff'>{title}</div>"
        "<div style='font-size:12px;color:#c9d3f5'>{line1}</div>"
        "<div style='font-size:11px;color:#8a94b8;margin-top:3px'>{line2}</div></div>"
    ),
    "style": {
        "backgroundColor": "rgba(10,14,28,0.92)",
        "border": "1px solid rgba(255,255,255,0.12)",
        "borderRadius": "12px",
        "padding": "10px 12px",
        "boxShadow": "0 12px 32px rgba(0,0,0,.55)",
        "color": "#e6ecff",
    },
}

_ST_VER = tuple(int(x) for x in re.findall(r"\d+", st.__version__)[:2])

# ──────────────────────────────────────────────────────────────────────────────
# Style
# ──────────────────────────────────────────────────────────────────────────────
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap');
:root{--bg0:#05070d;--glass:rgba(255,255,255,0.035);--border:rgba(255,255,255,0.08);--txt:#e6ecff;--muted:#8a94b8;
--cyan:#3DDCFF;--violet:#9B5CFF;--green:#2EE59D;--red:#FF4560;--amber:#FFB547;}
html,body,.stApp,[data-testid="stSidebar"],[data-testid="stMarkdownContainer"]{font-family:'Inter',sans-serif;}
.stApp{background:
 radial-gradient(1100px 520px at 8% -12%,rgba(61,220,255,0.10),transparent 60%),
 radial-gradient(900px 480px at 100% -5%,rgba(155,92,255,0.12),transparent 60%),
 radial-gradient(700px 400px at 50% 110%,rgba(46,229,157,0.05),transparent 60%),var(--bg0);}
[data-testid="stHeader"]{background:transparent;}
.block-container{padding-top:1.4rem;padding-bottom:2rem;max-width:100%;}
[data-testid="stSidebar"]{background:linear-gradient(180deg,rgba(14,18,34,0.97),rgba(7,9,18,0.99));border-right:1px solid var(--border);}
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"]{background:var(--glass);border-radius:14px;}
.sb-brand{display:flex;align-items:center;gap:10px;font-size:1.05rem;color:var(--txt);margin:2px 0 14px 0;}
.sb-brand .logo{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;
 font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:800;letter-spacing:-.04em;color:#fff;
 background:linear-gradient(135deg,rgba(61,220,255,.25),rgba(155,92,255,.25));border:1px solid var(--border);}
.sb-brand small{display:block;color:var(--muted);font-size:.72rem;font-weight:500;letter-spacing:.04em}
.sb-sec{font-size:.68rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:18px 0 6px 2px;}
.side-title{display:flex;align-items:center;gap:8px;font-weight:700;color:var(--txt);font-size:.9rem;margin-bottom:2px}
.side-title i{width:9px;height:9px;border-radius:50%;background:var(--accent);box-shadow:0 0 12px var(--accent)}

.hero{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:14px;
 animation:fadein .7s ease both;}
@keyframes fadein{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.hero h1{font-size:1.85rem;font-weight:800;letter-spacing:-0.025em;margin:0;padding:0;line-height:1.2;
 background:linear-gradient(90deg,#ffffff 0%,#9fe8ff 35%,#b99bff 65%,#ffffff 100%);background-size:200% auto;
 -webkit-background-clip:text;background-clip:text;color:transparent;animation:shine 9s linear infinite;}
@keyframes shine{to{background-position:200% center}}
.hero p{color:var(--muted);margin:3px 0 0 0;font-size:.88rem}
.chips{display:flex;gap:7px;flex-wrap:wrap;align-items:center}
.chip{display:inline-flex;align-items:center;gap:6px;padding:4px 11px;border-radius:999px;font-size:.72rem;font-weight:600;
 color:#cfd8ff;background:rgba(255,255,255,0.04);border:1px solid var(--border);white-space:nowrap;letter-spacing:.01em}
.chip b{color:#fff}
.chip-status.live{background:rgba(46,229,157,0.08);border-color:rgba(46,229,157,0.3);color:#2ee59d}
.chip-status.paused{background:rgba(255,255,255,0.035);border-color:rgba(255,255,255,0.1);color:var(--muted)}
.chip-src{color:var(--cyan);border-color:rgba(61,220,255,0.22)}
.live-dot,.pause-dot{width:7px;height:7px;border-radius:50%;display:inline-block}
.live-dot{background:var(--green);animation:pulse 1.6s infinite}
.pause-dot{background:#6b7393}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(46,229,157,.7)}70%{box-shadow:0 0 0 8px rgba(46,229,157,0)}100%{box-shadow:0 0 0 0 rgba(46,229,157,0)}}

[data-testid="stVerticalBlockBorderWrapper"]{background:var(--glass);border:1px solid var(--border);
 border-radius:16px;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);box-shadow:0 10px 30px rgba(0,0,0,.35);}
.player-hud{display:flex;flex-direction:column;justify-content:center;padding:2px 0 2px 4px}
.player-hud .hud-time{font-family:'JetBrains Mono',monospace;font-size:2.05rem;font-weight:700;color:#fff;
 line-height:1.05;letter-spacing:.02em;text-shadow:0 0 20px rgba(61,220,255,.3)}
.player-hud .hud-sub{display:flex;align-items:center;gap:6px;margin-top:4px;font-size:.68rem;font-weight:700;
 text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.player-hud .hud-phase{color:var(--cyan)}
.player-hud .hud-dot{color:var(--border)}
.player-hud .hud-pct{color:var(--muted);font-weight:600}

.slider-meta{display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;font-size:.74rem}
.slider-meta .lbl{font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#cfd8ff}
.slider-meta .cur{font-family:'JetBrains Mono',monospace;font-size:.85rem;font-weight:700;color:var(--cyan);margin-left:6px}
.slider-meta .rng{font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--muted);letter-spacing:.02em}
div[data-testid="stSlider"],div[data-testid="stSelectSlider"]{padding-top:0 !important;margin-top:0 !important}

.glass{background:var(--glass);border:1px solid var(--border);border-radius:16px;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);}
.clock{padding:10px 16px 12px 16px;position:relative;overflow:hidden}
.clock:after{content:"";position:absolute;inset:-40% -20% auto auto;width:140px;height:140px;border-radius:50%;
 background:radial-gradient(circle,rgba(61,220,255,.18),transparent 70%);pointer-events:none}
.clock-top{display:flex;justify-content:space-between;align-items:center;font-size:.7rem;font-weight:600;color:var(--muted);
 letter-spacing:.06em;text-transform:uppercase}
.clock-top .state{display:inline-flex;align-items:center;gap:6px}
.clock .time{font-family:'JetBrains Mono',monospace;font-size:2.15rem;font-weight:700;color:#fff;line-height:1.1;
 letter-spacing:.02em;text-shadow:0 0 22px rgba(61,220,255,.35)}
.clock .bar{height:4px;border-radius:4px;background:rgba(255,255,255,.07);margin-top:6px;overflow:hidden}
.clock .bar div{height:100%;border-radius:4px;background:linear-gradient(90deg,var(--cyan),var(--violet))}

.banner{display:flex;align-items:center;gap:14px;padding:12px 18px;margin:10px 0 14px 0;
 background:linear-gradient(90deg,rgba(46,229,157,.10),rgba(61,220,255,.06) 50%,rgba(155,92,255,.08));}
.banner.neg{background:linear-gradient(90deg,rgba(255,69,96,.12),rgba(255,181,71,.06) 60%,rgba(155,92,255,.06));}
.banner .big{font-family:'JetBrains Mono',monospace;font-size:1.5rem;font-weight:700;color:#fff;white-space:nowrap}
.banner .txt{color:#cfd8ff;font-size:.9rem}
.banner .txt b{color:#fff}

.scen-head{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 14px;margin-bottom:10px;
 border-left:3px solid var(--accent);flex-wrap:wrap}
.scen-head .ttl{font-weight:800;font-size:1.02rem;color:#fff;letter-spacing:-.01em}
.scen-head .ttl span{color:var(--accent)}
.scen-head .desc{color:var(--muted);font-size:.78rem;margin-top:1px}

.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:9px;margin-bottom:10px}
.kpi{padding:10px 12px 9px 14px;position:relative;overflow:hidden;transition:transform .2s ease,border-color .2s ease,box-shadow .2s ease}
.kpi:hover{transform:translateY(-2px);border-color:rgba(255,255,255,.18);box-shadow:0 10px 26px rgba(0,0,0,.35)}
.kpi:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--accent);opacity:.85}
.kpi .label{font-size:.64rem;font-weight:700;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);white-space:nowrap;
 overflow:hidden;text-overflow:ellipsis}
.kpi .value{font-family:'JetBrains Mono',monospace;font-size:1.25rem;font-weight:700;color:var(--txt);margin-top:2px;white-space:nowrap}
.kpi .sub{font-size:.7rem;font-weight:600;color:var(--muted);margin-top:1px;white-space:nowrap}
.kpi .sub.good{color:var(--green)} .kpi .sub.bad{color:var(--red)}

div[data-testid="stDeckGlJsonChart"]{border-radius:18px;overflow:hidden;border:1px solid var(--border);
 box-shadow:0 22px 60px rgba(0,0,0,.5),0 0 0 1px rgba(61,220,255,.04),inset 0 0 80px rgba(61,220,255,.03)}

.legend{display:flex;flex-wrap:wrap;gap:8px 18px;align-items:center;padding:10px 16px;margin:12px 0 4px 0;font-size:.76rem;color:#cfd8ff}
.legend .it{display:inline-flex;align-items:center;gap:7px}
.legend .dot{width:10px;height:10px;border-radius:3px}
.legend .ring{width:11px;height:11px;border-radius:50%;border:2px solid}
.legend .grad{width:120px;height:8px;border-radius:6px;
 background:linear-gradient(90deg,rgb(255,69,96) 0 10%,rgb(255,184,76) 10%,rgb(46,229,157) 30%,rgb(46,229,157) 70%,rgb(88,166,255) 90%,rgb(155,92,255) 90%)}
.legend .sep{width:1px;height:16px;background:var(--border)}

.feed{display:flex;flex-direction:column;gap:6px}
.feed .row{display:flex;gap:10px;align-items:center;padding:8px 12px;font-size:.8rem;color:#cfd8ff}
.feed .t{font-family:'JetBrains Mono',monospace;color:#fff;font-weight:700;font-size:.78rem}
.feed .badge{padding:2px 8px;border-radius:999px;font-weight:700;font-size:.68rem;color:#05070d}
.feed .empty{color:var(--muted);font-size:.8rem;padding:10px 4px}

.stButton>button{border-radius:10px;border:1px solid var(--border);background:rgba(255,255,255,.04);
 transition:all .15s ease;min-height:2.5rem;color:#e6ecff !important}
.stButton>button p{color:#e6ecff !important;font-family:'JetBrains Mono',monospace,sans-serif !important;
 font-weight:700 !important;font-size:.95rem !important;letter-spacing:-.5px}
.stButton>button:hover{border-color:rgba(61,220,255,.6);background:rgba(61,220,255,.09);transform:translateY(-1px)}
.stButton>button[kind="primary"]{background:linear-gradient(135deg,#1fb6e0,#7b4dff);border:none;color:#fff !important;
 box-shadow:0 6px 20px rgba(61,160,255,.35)}
.stButton>button[kind="primary"] p{color:#fff !important}
.stTabs [data-baseweb="tab-list"]{gap:6px}
.stTabs [data-baseweb="tab"]{border-radius:10px 10px 0 0;padding:6px 14px}
.foot{color:#5d6688;font-size:.72rem;text-align:center;margin-top:18px}
</style>
"""


# ──────────────────────────────────────────────────────────────────────────────
# Pomocnicze
# ──────────────────────────────────────────────────────────────────────────────
def _html(s: str) -> str:
    """Spłaszcza HTML (wcięcia w Markdownie zamieniłyby się w bloki kodu)."""
    return "".join(line.strip() for line in s.splitlines())


@functools.cache
def _params(fn) -> frozenset:
    try:
        return frozenset(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return frozenset()


def _stretch(fn) -> dict:
    """Zgodność API szerokości widżetów między wersjami Streamlit."""
    if "width" in _params(fn) and _ST_VER >= (1, 46):
        return {"width": "stretch"}
    return {"use_container_width": True} if "use_container_width" in _params(fn) else {}


def fmt_hhmm(m) -> str:
    h, mm = divmod(int(round(m)), 60)
    return f"{h:02d}:{mm:02d}"


def fmt_int(v) -> str:
    return f"{int(round(float(v))):,}".replace(",", "\u2009")


def hex_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def day_phase(m: int) -> str:
    h = m / 60
    if h < 5.5:
        return "Noc"
    if h < 7:
        return "Świt"
    if h < 10:
        return "Poranny szczyt"
    if h < 16:
        return "Południe"
    if h < 19.5:
        return "Popołudniowy szczyt"
    if h < 22:
        return "Wieczór"
    return "Noc"


def scen_label(run: dict) -> str:
    s = run["meta"]["scenario"]
    return f"{s} · {SCENARIOS[s]['name']}"


def accent_of(run: dict) -> str:
    return SCENARIOS[run["meta"]["scenario"]]["accent"]


# ──────────────────────────────────────────────────────────────────────────────
# Dane
# ──────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=30, show_spinner=False)
def discover_runs() -> list[dict]:
    """Katalog dostępnych przebiegów: wyniki silnika (data/outputs) + mocki."""
    runs = []
    if SNAP_DIR.exists():
        for p in sorted(SNAP_DIR.glob("*.json")):
            m = RUN_RE.match(p.name)
            if m and m["scenario"] in SCENARIOS:
                runs.append({"source": "engine", "scenario": m["scenario"], "n_vans": int(m["n"]),
                             "seed": int(m["seed"]), "path": str(p)})
    for p in sorted(MOCK.glob("mock_snapshots_24h_*.json")):
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))["meta"]
        except (OSError, KeyError, ValueError):
            continue
        if meta.get("scenario") not in SCENARIOS:
            continue
        runs.append({"source": "mock", "scenario": meta["scenario"], "n_vans": int(meta["n_vans"]),
                     "seed": int(meta["seed"]), "path": str(p)})
    return runs


def _stations_path(stations_file: str, source: str) -> Path:
    name = Path(stations_file).name
    order = [MOCK / name, PROCESSED / name] if source == "mock" else [PROCESSED / name, MOCK / name]
    for p in [*order, STATIONS_JSON, MOCK / "mock_stations.json"]:
        if p.exists():
            return p
    raise FileNotFoundError(f"Nie znaleziono pliku stacji '{stations_file}'")


def _grid_route(lat1, lon1, lat2, lon2, t0, t1):
    """Trasa w kształcie 'L' po siatce ulic Manhattanu (obróconej o GRID_ANGLE_DEG) + znaczniki czasu."""
    k_lat = 110_574.0
    k_lon = 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    dx, dy = (lon2 - lon1) * k_lon, (lat2 - lat1) * k_lat
    a = math.radians(GRID_ANGLE_DEG)
    c, s = math.cos(a), math.sin(a)
    u = c * dx - s * dy  # oś ulic (cross-town)
    v = s * dx + c * dy  # oś alej (up-town)
    total = abs(u) + abs(v)
    if total < 1.0:
        return [lat1, lat2], [lon1, lon2], [float(t0), float(t1)]
    corner_lat = lat1 + (-s * u) / k_lat
    corner_lon = lon1 + (c * u) / k_lon
    t_mid = t0 + (t1 - t0) * abs(u) / total
    return [lat1, corner_lat, lat2], [lon1, corner_lon, lon2], [float(t0), float(t_mid), float(t1)]


@st.cache_data(show_spinner="Wczytywanie przebiegu symulacji…", max_entries=16)
def load_run(path: str, source: str, mtime: float) -> dict:  # noqa: ARG001 (mtime = klucz cache)
    snap = validate_snapshots(path)
    meta = snap.meta
    st_path = _stations_path(meta.stations_file, source)
    stations = validate_stations(st_path).stations
    by_id = {}
    for s in stations:
        by_id[s.station_id] = s
        for alias in s.aliases:
            by_id.setdefault(alias, s)
    missing = [sid for sid in meta.station_ids if sid not in by_id]
    if missing:
        raise ValueError(f"{len(missing)} stacji z przebiegu brak w {st_path.name} (np. {missing[:3]})")
    rows = [by_id[sid] for sid in meta.station_ids]
    stations_df = pd.DataFrame({
        "station_id": meta.station_ids,
        "name": [s.name for s in rows],
        "lat": [s.lat for s in rows],
        "lon": [s.lon for s in rows],
        "capacity": [s.capacity for s in rows],
    })
    pos = {sid: (s.lat, s.lon) for sid, s in zip(meta.station_ids, rows)}
    names = {sid: s.name for sid, s in zip(meta.station_ids, rows)}

    t = np.array([f.t for f in snap.frames], dtype=float)
    bikes = np.array([f.bikes for f in snap.frames], dtype=float)
    fail_rent = np.array([f.fail_rent for f in snap.frames], dtype=float)
    fail_return = np.array([f.fail_return for f in snap.frames], dtype=float)
    kpi = pd.DataFrame([f.kpi.model_dump() for f in snap.frames])
    kpi.insert(0, "t", t)

    legs = []
    for leg in sorted(snap.van_legs, key=lambda x: (x.van_id, x.t_start)):
        (la1, lo1), (la2, lo2) = pos[leg.from_station_id], pos[leg.to_station_id]
        lats, lons, ts = _grid_route(la1, lo1, la2, lo2, leg.t_start, leg.t_end)
        legs.append({
            **leg.model_dump(), "lats": lats, "lons": lons, "ts": ts,
            "from_lat": la1, "from_lon": lo1, "to_lat": la2, "to_lon": lo2,
            "from_name": names[leg.from_station_id], "to_name": names[leg.to_station_id],
        })
    legs_by_van: dict[int, list] = {}
    for leg in legs:
        legs_by_van.setdefault(leg["van_id"], []).append(leg)

    return {
        "meta": meta.model_dump(),
        "source": source,
        "path": path,
        "stations_file": st_path.name,
        "stations": stations_df,
        "names": names,
        "t": t,
        "bikes": bikes,
        "fail_rent": fail_rent,
        "fail_return": fail_return,
        "fail_cum": np.cumsum(fail_rent + fail_return, axis=0),
        "kpi": kpi,
        "kpi_arr": kpi[KPI_COLS].to_numpy(dtype=float),
        "vans": [[v.model_dump() for v in f.vans] for f in snap.frames],
        "legs": legs,
        "legs_by_van": legs_by_van,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Stan w chwili t (interpolacja między klatkami)
# ──────────────────────────────────────────────────────────────────────────────
def frame_pos(ts: np.ndarray, t: float) -> tuple[int, float]:
    if len(ts) < 2:
        return 0, 0.0
    t = float(np.clip(t, ts[0], ts[-1]))
    i = int(np.clip(np.searchsorted(ts, t, side="right") - 1, 0, len(ts) - 2))
    span = ts[i + 1] - ts[i]
    return i, float(np.clip((t - ts[i]) / span, 0.0, 1.0)) if span > 0 else 0.0


def state_at(run: dict, t: float) -> dict:
    ts = run["t"]
    i, a = frame_pos(ts, t)
    j = min(i + 1, len(ts) - 1)
    w = int(min(np.searchsorted(ts, t, side="left"), len(ts) - 1))  # okno (t - step, t]
    kpi = run["kpi_arr"][i] * (1 - a) + run["kpi_arr"][j] * a
    return {
        "i": i,
        "a": a,
        "bikes": np.rint(run["bikes"][i] * (1 - a) + run["bikes"][j] * a),
        "win_rent": run["fail_rent"][w],
        "win_ret": run["fail_return"][w],
        "fail_cum": run["fail_cum"][i] * (1 - a) + run["fail_cum"][j] * a,
        "kpi": dict(zip(KPI_COLS, kpi)),
    }


def van_states(run: dict, t: float, i: int, a: float) -> list[dict]:
    """Pozycje furgonetek: z `van_legs` (płynnie po siatce ulic), a poza odcinkami z klatek."""
    out = []
    frames = run["vans"]
    j = min(i + 1, len(frames) - 1)
    for v in range(run["meta"]["n_vans"]):
        state = None
        legs = run["legs_by_van"].get(v, [])
        for k, leg in enumerate(legs):
            if leg["t_start"] <= t < leg["t_end"]:
                before = leg["load"] - leg["qty"] if leg["action"] == "pickup" else leg["load"] + leg["qty"]
                state = {"status": "driving", "lat": float(np.interp(t, leg["ts"], leg["lats"])),
                         "lon": float(np.interp(t, leg["ts"], leg["lons"])), "load": max(int(before), 0),
                         "action": leg["action"], "leg": leg, "target": leg["to_name"]}
                break
            if leg["t_end"] <= t:
                nxt = legs[k + 1]["t_start"] if k + 1 < len(legs) else leg["t_end"] + SERVICE_GUESS_S
                if t < min(nxt, leg["t_end"] + MAX_SERVICE_S):
                    state = {"status": "servicing", "lat": leg["to_lat"], "lon": leg["to_lon"], "load": leg["load"],
                             "action": leg["action"], "leg": leg, "target": leg["to_name"]}
                    break
            else:
                break
        if state is None:
            if v >= len(frames[i]):
                continue
            f0, f1 = frames[i][v], frames[j][v] if v < len(frames[j]) else frames[i][v]
            lat, lon = f0["lat"], f0["lon"]
            if f0["status"] == "driving":
                lat, lon = lat + a * (f1["lat"] - lat), lon + a * (f1["lon"] - lon)
            state = {"status": f0["status"], "lat": lat, "lon": lon, "load": f0["load"], "action": None,
                     "leg": None, "target": run["names"].get(f0["target_station_id"] or "", None)}
        state["van_id"] = v
        out.append(state)
    return out


def van_color(vs: dict) -> tuple[int, int, int]:
    if vs["status"] == "driving":
        return VAN_COLORS["pickup"] if vs["action"] == "pickup" else VAN_COLORS["dropoff"]
    return VAN_COLORS.get(vs["status"], VAN_COLORS["idle"])


def station_colors(ratio: np.ndarray, lo: float, hi: float) -> np.ndarray:
    x = np.clip((ratio - lo) / max(hi - lo, 1e-6), 0, 1)
    rgb = np.column_stack([np.interp(x, GRAD_X, GRAD_RGB[:, c]) for c in range(3)])
    rgb[ratio < lo] = C_EMPTY
    rgb[ratio > hi] = C_FULL
    return np.hstack([rgb, np.full((len(ratio), 1), 235.0)]).round().astype(int)


# ──────────────────────────────────────────────────────────────────────────────
# Mapa (PyDeck)
# ──────────────────────────────────────────────────────────────────────────────
def build_deck(run: dict, s: dict, vans: list[dict], t: float, o: dict) -> pdk.Deck:
    sdf = run["stations"]
    cap = sdf["capacity"].to_numpy(float)
    bikes = s["bikes"]
    ratio = np.clip(bikes / cap, 0, 1)
    lo, hi = o["thr"]
    scale = o["elev_scale"]
    if o["elev_mode"] == "bikes":
        elev, cap_elev = bikes * scale, cap * scale
    else:
        elev, cap_elev = ratio * 50 * scale, np.full_like(cap, 50 * scale)
    win_r, win_d, cum = s["win_rent"], s["win_ret"], s["fail_cum"]
    names = [html.escape(n) for n in sdf["name"]]

    stations = pd.DataFrame({
        "lon": sdf["lon"], "lat": sdf["lat"], "elev": elev + 4.0, "cap_elev": cap_elev,
        "color": station_colors(ratio, lo, hi).tolist(),
        "title": names,
        "line1": [f"{b:.0f} / {c:.0f} rowerów · wolne doki: {c - b:.0f}" for b, c in zip(bikes, cap)],
        "line2": [f"Zapełnienie {r:.0%} · odrzucenia w oknie {fr:.0f}/{fd:.0f} · narastająco {cu:.0f}"
                  for r, fr, fd, cu in zip(ratio, win_r, win_d, cum)],
    })

    layers = []
    if o["heatmap"]:
        m = cum > 0
        if m.any():
            layers.append(pdk.Layer(
                "HeatmapLayer", id="heat",
                data=pd.DataFrame({"lon": sdf["lon"].to_numpy()[m], "lat": sdf["lat"].to_numpy()[m], "w": cum[m]}),
                get_position="[lon, lat]", get_weight="w", radius_pixels=46, intensity=1.1, threshold=0.04,
                color_range=HEAT_RANGE, aggregation=JsString("SUM"), opacity=0.55,
            ))
    if o["boundary"]:
        ring = [[lon, lat] for lon, lat in MANHATTAN_POLYGON] + [list(MANHATTAN_POLYGON[0])]
        layers += [
            pdk.Layer("PolygonLayer", id="area", data=[{"polygon": ring}], get_polygon="polygon", filled=True,
                      stroked=False, get_fill_color=[61, 220, 255, 9]),
            pdk.Layer("PathLayer", id="area_glow", data=[{"path": ring}], get_path="path",
                      get_color=[61, 220, 255, 38], width_min_pixels=8),
            pdk.Layer("PathLayer", id="area_line", data=[{"path": ring}], get_path="path",
                      get_color=[61, 220, 255, 170], width_min_pixels=1.4),
        ]
    if o["stations"]:
        layers.append(pdk.Layer(
            "ColumnLayer", id="stations", data=stations, get_position="[lon, lat]", get_elevation="elev",
            elevation_scale=1, radius=o["radius"], disk_resolution=14, extruded=True, get_fill_color="color",
            pickable=True, auto_highlight=True, highlight_color=[255, 255, 255, 90],
        ))
        if o["glass"]:  # rysowane po kolumnach, by półprzezroczystość nie zasłaniała wnętrza
            layers.append(pdk.Layer(
                "ColumnLayer", id="capacity", data=stations[["lon", "lat", "cap_elev"]], get_position="[lon, lat]",
                get_elevation="cap_elev", elevation_scale=1, radius=o["radius"] * 1.18, disk_resolution=14,
                extruded=True, get_fill_color=[170, 195, 255, 20],
            ))
    if o["fails"]:
        idx = np.where((win_r + win_d) > 0)[0]
        if len(idx):
            pulse = 0.5 + 0.5 * math.sin(t / 75.0)
            tot = win_r[idx] + win_d[idx]
            rent_dom = win_r[idx] >= win_d[idx]
            layers.append(pdk.Layer(
                "ScatterplotLayer", id="fails",
                data=pd.DataFrame({
                    "lon": sdf["lon"].to_numpy()[idx], "lat": sdf["lat"].to_numpy()[idx],
                    "r": (o["radius"] * 1.6 + 26 * np.sqrt(tot)) * (0.85 + 0.35 * pulse),
                    "line": [[*C_EMPTY, 230] if rd else [*C_FULL, 230] for rd in rent_dom],
                    "fill": [[*C_EMPTY, 40] if rd else [*C_FULL, 40] for rd in rent_dom],
                    "title": [f"Stacja {names[k]}" for k in idx],
                    "line1": [f"Odrzucone wypożyczenia: {win_r[k]:.0f} · zwroty: {win_d[k]:.0f}" for k in idx],
                    "line2": ["w ostatnim oknie 15-min"] * len(idx),
                }),
                get_position="[lon, lat]", get_radius="r", radius_units=JsString("meters"), stroked=True, filled=True,
                get_fill_color="fill", get_line_color="line", line_width_min_pixels=2, pickable=True,
            ))

    if o["vans"] and vans:
        cap_v = run["meta"]["van_capacity"]
        if o["trails"]:
            recent = [lg for lg in run["legs"] if lg["t_start"] <= t <= lg["t_end"] + TRAIL_S]
            if recent:
                layers.append(pdk.Layer(
                    "TripsLayer", id="trails",
                    data=[{"path": [[lo_, la_] for lo_, la_ in zip(lg["lons"], lg["lats"])], "timestamps": lg["ts"],
                           "color": list(VAN_COLORS[lg["action"]])} for lg in recent],
                    get_path="path", get_timestamps="timestamps", get_color="color", width_min_pixels=4,
                    rounded=True, trail_length=TRAIL_S, current_time=float(t), opacity=0.95,
                ))
        driving = [vs for vs in vans if vs["status"] == "driving" and vs["leg"] is not None]
        if o["route"] and driving:
            paths = []
            for vs in driving:
                lg = vs["leg"]
                rest = [[lo_, la_] for lo_, la_, ts_ in zip(lg["lons"], lg["lats"], lg["ts"]) if ts_ > t]
                paths.append({"path": [[vs["lon"], vs["lat"]], *rest], "color": [*van_color(vs), 150]})
            layers.append(pdk.Layer("PathLayer", id="routes", data=paths, get_path="path", get_color="color",
                                    width_min_pixels=2, rounded=True))
        if o["arcs"] and driving:
            layers.append(pdk.Layer(
                "ArcLayer", id="arcs",
                data=[{"from_lon": vs["leg"]["from_lon"], "from_lat": vs["leg"]["from_lat"],
                       "to_lon": vs["leg"]["to_lon"], "to_lat": vs["leg"]["to_lat"],
                       "c0": [*van_color(vs), 60], "c1": [*van_color(vs), 255]} for vs in driving],
                get_source_position="[from_lon, from_lat]", get_target_position="[to_lon, to_lat]",
                get_source_color="c0", get_target_color="c1", get_width=3, width_min_pixels=2, get_height=0.55,
            ))
        rows = []
        for vs in vans:
            if vs["status"] == "off":
                continue
            col = van_color(vs)
            phase = 0.5 + 0.5 * math.sin(t / 40.0 + vs["van_id"] * 1.7)
            beacon = BEACON_M * (0.45 if vs["status"] == "idle" else 1.0)
            lg = vs["leg"]
            if vs["status"] == "driving" and lg:
                what = "jedzie po rowery" if vs["action"] == "pickup" else f"wiezie {vs['load']} rowerów"
                line2 = f"{what} · cel: {html.escape(lg['to_name'])} (za {max(lg['t_end'] - t, 0) / 60:.0f} min)"
            elif vs["status"] == "servicing" and lg:
                verb = "odbiór" if lg["action"] == "pickup" else "dostawa"
                line2 = f"{verb} {lg['qty']} rowerów · {html.escape(lg['to_name'])}"
            else:
                line2 = html.escape(vs["target"]) if vs.get("target") else "—"
            rows.append({
                "lon": vs["lon"], "lat": vs["lat"], "z": beacon + 70, "beacon": beacon,
                "color": [*col, 240], "halo": [*col, 55], "ring": [*col, 210], "r": 55 + 50 * phase,
                "label": f"V{vs['van_id'] + 1} · {vs['load']}/{cap_v}",
                "title": f"Furgonetka V{vs['van_id'] + 1} — {VAN_STATUS_PL.get(vs['status'], vs['status'])}",
                "line1": f"Ładunek: {vs['load']} / {cap_v} rowerów",
                "line2": line2,
            })
        if rows:
            vdf = pd.DataFrame(rows)
            layers += [
                pdk.Layer("ScatterplotLayer", id="van_halo", data=vdf, get_position="[lon, lat]", get_radius="r",
                          radius_units=JsString("meters"), filled=True, stroked=True, get_fill_color="halo",
                          get_line_color="ring", line_width_min_pixels=1.5),
                pdk.Layer("ColumnLayer", id="van_beacon", data=vdf, get_position="[lon, lat]", get_elevation="beacon",
                          elevation_scale=1, radius=16, disk_resolution=10, extruded=True, get_fill_color="color",
                          pickable=True, auto_highlight=True),
                pdk.Layer("TextLayer", id="van_label", data=vdf, get_position="[lon, lat, z]", get_text="label",
                          get_size=13, get_color=[255, 255, 255, 240], get_text_anchor=JsString("middle"),
                          get_alignment_baseline=JsString("bottom"), font_family=JsString("'JetBrains Mono', monospace"),
                          font_weight=700, background=True, get_background_color=[8, 12, 24, 215],
                          background_padding=[6, 3, 6, 3]),
            ]

    view = pdk.ViewState(latitude=VIEW_CENTER[0], longitude=VIEW_CENTER[1], zoom=o["zoom"], pitch=o["pitch"],
                         bearing=o["bearing"], min_zoom=10.5, max_zoom=17.5)
    return pdk.Deck(layers=layers, initial_view_state=view, map_provider="carto", map_style=CARTO_DARK,
                    tooltip=TOOLTIP, height=o["height"])


# ──────────────────────────────────────────────────────────────────────────────
# Komponenty HTML
# ──────────────────────────────────────────────────────────────────────────────
def hero_html(sides: list[dict], playing: bool) -> str:
    r0 = sides[0]["run"]
    meta = r0["meta"]
    n = len(r0["stations"])
    src = {"engine": "silnik DES", "mock": "dane mock"}[r0["source"]]
    day_type_map = {"weekday": "dzień powszedni", "weekend": "weekend"}
    day_str = day_type_map.get(meta.get("day_type", ""), meta.get("day_type", ""))
    state = "<span class='live-dot'></span>LIVE" if playing else "<span class='pause-dot'></span>PAUZA"
    vs = " vs ".join(f"<b style='color:{accent_of(sd['run'])}'>{sd['run']['meta']['scenario']}</b>" for sd in sides)
    return _html(f"""
    <div class="hero">
      <div><h1>Citi Bike NYC · Dolny Manhattan</h1>
        <p>Odtwarzacz symulacji zdarzeń dyskretnych — relokacja rowerów w czasie rzeczywistym</p></div>
      <div class="chips">
        <span class="chip chip-status {'live' if playing else 'paused'}">{state}</span>
        <span class="chip">Scenariusz {vs}</span>
        <span class="chip"><b>{n}</b> stacji</span>
        <span class="chip">{day_str}</span>
        <span class="chip">Δt = {meta['step_s'] // 60} min</span>
        <span class="chip chip-src">{src}</span>
      </div>
    </div>""")


def clock_html(m: int, playing: bool) -> str:  # noqa: ARG001
    label = day_phase(m)
    pct = (m / DAY_MIN) * 100
    return _html(f"""
    <div class="player-hud">
      <div class="hud-time">{fmt_hhmm(m)}</div>
      <div class="hud-sub">
        <span class="hud-phase">{label}</span>
        <span class="hud-dot">·</span>
        <span class="hud-pct">{pct:.0f}% doby</span>
      </div>
    </div>""")


def scen_head_html(run: dict) -> str:
    m = run["meta"]
    info = SCENARIOS[m["scenario"]]
    fleet = f"Flota: {m['n_vans']} × K={m['van_capacity']}" if m["n_vans"] else "Bez furgonetek"
    src = "mock" if run["source"] == "mock" else "silnik DES"
    return _html(f"""
    <div class="scen-head glass" style="--accent:{info['accent']}">
      <div><div class="ttl"><span>Scenariusz {m['scenario']}</span> · {info['name']}</div>
        <div class="desc">{info['desc']}</div></div>
      <div class="chips"><span class="chip">{fleet}</span><span class="chip">seed {m['seed']}</span>
        <span class="chip">{src}</span></div>
    </div>""")


def _delta(val: float, ref: float | None, ref_name: str | None, higher_better: bool = False) -> tuple[str, str]:
    if ref is None or ref <= 0:
        return "", ""
    pct = (val - ref) / ref
    if abs(pct) < 0.005:
        return f"≈ jak {ref_name}", ""
    arrow = "▼" if pct < 0 else "▲"
    good = (pct > 0) if higher_better else (pct < 0)
    return f"{arrow} {abs(pct):.0%} vs {ref_name}", ("good" if good else "bad")


def kpi_html(run: dict, s: dict, vans: list[dict], thr: tuple, ref: dict | None = None) -> str:
    k = s["kpi"]
    cap = run["stations"]["capacity"].to_numpy(float)
    ratio = s["bikes"] / cap
    n_empty, n_full = int((ratio < thr[0]).sum()), int((ratio > thr[1]).sum())
    rk = ref["s"]["kpi"] if ref else None
    rname = ref["run"]["meta"]["scenario"] if ref else None
    r_crit = None
    if ref:
        rr = ref["s"]["bikes"] / ref["run"]["stations"]["capacity"].to_numpy(float)
        r_crit = int((rr < thr[0]).sum() + (rr > thr[1]).sum())
    n_vans = run["meta"]["n_vans"]
    on_road = sum(v["status"] in ("driving", "servicing") for v in vans)
    cards = [
        ("Wypożyczenia", fmt_int(k["rentals_cum"]),
         *(_delta(k["rentals_cum"], rk["rentals_cum"], rname, higher_better=True) if rk else ("narastająco", ""))),
        ("Odrzucone wypoż.", fmt_int(k["fail_rent_cum"]),
         *_delta(k["fail_rent_cum"], rk["fail_rent_cum"] if rk else None, rname)),
        ("Odrzucone zwroty", fmt_int(k["fail_return_cum"]),
         *_delta(k["fail_return_cum"], rk["fail_return_cum"] if rk else None, rname)),
        ("Puste / pełne", f"{n_empty} / {n_full}", *_delta(n_empty + n_full, r_crit, rname)),
        ("Flota aktywna", f"{on_road}/{n_vans}" if n_vans else "—", f"{k['van_km_cum']:.1f} km", ""),
    ]
    body = "".join(
        f"<div class='kpi glass'><div class='label'>{lab}</div><div class='value'>{val}</div>"
        f"<div class='sub {cls}'>{sub or '&nbsp;'}</div></div>"
        for lab, val, sub, cls in cards
    )
    return f"<div class='kpi-grid' style='--accent:{accent_of(run)}'>{body}</div>"


def banner_html(left: dict, right: dict, m: int) -> str:
    lk, rk = left["s"]["kpi"], right["s"]["kpi"]
    ul = lk["fail_rent_cum"] + lk["fail_return_cum"]
    ur = rk["fail_rent_cum"] + rk["fail_return_cum"]
    ln, rn = left["run"]["meta"]["scenario"], right["run"]["meta"]["scenario"]
    if ul <= 0 and ur <= 0:
        return _html(f"""<div class="banner glass"><div class="big">0</div><div class="txt">Do godziny <b>{fmt_hhmm(m)}</b>
            żaden scenariusz nie odnotował jeszcze nieobsłużonych klientów.</div></div>""")
    diff = ul - ur
    pct = diff / ul if ul > 0 else 0.0
    cls = "" if diff >= 0 else " neg"
    verb = "uniknął" if diff >= 0 else "wygenerował dodatkowo"
    return _html(f"""
    <div class="banner glass{cls}">
      <div class="big">{'−' if diff >= 0 else '+'}{fmt_int(abs(diff))}</div>
      <div class="txt">Do godziny <b>{fmt_hhmm(m)}</b> scenariusz <b style="color:{accent_of(right['run'])}">{rn}</b>
        {verb} <b>{fmt_int(abs(diff))}</b> nieobsłużonych klientów względem
        <b style="color:{accent_of(left['run'])}">{ln}</b> ({'−' if diff >= 0 else '+'}{abs(pct):.0%} nieobsłużonego popytu:
        {fmt_int(ur)} vs {fmt_int(ul)}).</div>
    </div>""")


def legend_html(thr: tuple) -> str:
    lo, hi = thr
    vans = "".join(
        f"<span class='it'><span class='dot' style='background:rgb{VAN_COLORS[k]}'></span>{lab}</span>"
        for k, lab in (("pickup", "van → po rowery"), ("dropoff", "van → z rowerami"),
                       ("servicing", "obsługa stacji"), ("idle", "postój"))
    )
    return _html(f"""
    <div class="legend glass">
      <span class="it"><span class="dot" style="background:rgb{C_EMPTY}"></span>pusta &lt;{lo:.0%}</span>
      <span class="it"><span class="dot" style="background:rgb(46,229,157)"></span>norma</span>
      <span class="it"><span class="dot" style="background:rgb{C_FULL}"></span>pełna &gt;{hi:.0%}</span>
      <span class="it"><span class="grad"></span></span>
      <span class="sep"></span>
      <span class="it"><span class="ring" style="border-color:rgb{C_EMPTY}"></span>odrzucone wypożyczenia</span>
      <span class="it"><span class="ring" style="border-color:rgb{C_FULL}"></span>odrzucone zwroty</span>
      <span class="sep"></span>{vans}
      <span class="sep"></span><span class="it" style="color:#8a94b8">wysokość kolumny = liczba rowerów · Shift/Ctrl + przeciągnij = obrót kamery</span>
    </div>""")


def van_feed_html(run: dict, t: float, limit: int = 8) -> str:
    done = sorted((lg for lg in run["legs"] if lg["t_end"] <= t), key=lambda x: x["t_end"], reverse=True)[:limit]
    if not done:
        return "<div class='feed'><div class='empty'>Brak zakończonych kursów do tej pory.</div></div>"
    rows = []
    for lg in done:
        col = VAN_COLORS[lg["action"]]
        verb = "odebrał" if lg["action"] == "pickup" else "dostarczył"
        rows.append(
            f"<div class='row glass'><span class='t'>{fmt_hhmm(lg['t_end'] / 60)}</span>"
            f"<span class='badge' style='background:rgb{col}'>V{lg['van_id'] + 1}</span>"
            f"<span>{verb} <b>{lg['qty']}</b> rowerów · {html.escape(lg['to_name'])}"
            f" <span style='color:#8a94b8'>(ładunek {lg['load']})</span></span></div>"
        )
    return "<div class='feed'>" + "".join(rows) + "</div>"


# ──────────────────────────────────────────────────────────────────────────────
# Wykresy (Plotly)
# ──────────────────────────────────────────────────────────────────────────────
def _style_fig(fig: go.Figure, title: str, rng: tuple[int, int]) -> go.Figure:
    lo_h, hi_h = rng[0] / 60, rng[1] / 60
    step = 1 if hi_h - lo_h <= 8 else 2
    ticks = list(range(int(math.floor(lo_h)), int(math.ceil(hi_h)) + 1, step))
    fig.update_layout(
        height=300, margin=dict(l=8, r=8, t=46, b=8), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", color="#c9d3f5", size=12),
        title=dict(text=title, font=dict(size=14, color="#e6ecff"), x=0.01, y=0.97),
        legend=dict(orientation="h", y=1.13, x=1, xanchor="right", bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        hovermode="x unified", hoverlabel=dict(bgcolor="#0d1224", bordercolor="rgba(255,255,255,0.15)"),
    )
    fig.update_xaxes(range=[lo_h, hi_h], tickvals=ticks, ticktext=[f"{h:02d}:00" for h in ticks],
                     gridcolor="rgba(255,255,255,0.05)", zeroline=False, showline=False)
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.06)", zeroline=False, rangemode="tozero")
    return fig


def chart_unserved(sides: list[dict], t: float, rng: tuple[int, int]) -> go.Figure:
    fig = go.Figure()
    for sd in sides:
        run, acc = sd["run"], accent_of(sd["run"])
        k = run["kpi"]
        y = k["fail_rent_cum"] + k["fail_return_cum"]
        fig.add_trace(go.Scatter(
            x=k["t"] / 3600, y=y, mode="lines", name=scen_label(run), line=dict(color=acc, width=2.6, shape="spline"),
            fill="tozeroy", fillcolor=hex_rgba(acc, 0.08), hovertemplate="%{y:,.0f}",
        ))
        now = sd["s"]["kpi"]["fail_rent_cum"] + sd["s"]["kpi"]["fail_return_cum"]
        fig.add_trace(go.Scatter(x=[t / 3600], y=[now], mode="markers", showlegend=False, hoverinfo="skip",
                                 marker=dict(size=11, color=acc, line=dict(color="#fff", width=2))))
    fig.add_vline(x=t / 3600, line=dict(color="rgba(255,255,255,0.35)", width=1, dash="dot"))
    return _style_fig(fig, "Narastający nieobsłużony popyt (odrzucone wypożyczenia + zwroty)", rng)


def chart_critical(sides: list[dict], t: float, thr: tuple, rng: tuple[int, int]) -> go.Figure:
    fig = go.Figure()
    for sd in sides:
        run, acc = sd["run"], accent_of(sd["run"])
        ratio = run["bikes"] / run["stations"]["capacity"].to_numpy(float)[None, :]
        x = run["t"] / 3600
        s = run["meta"]["scenario"]
        fig.add_trace(go.Scatter(x=x, y=(ratio < thr[0]).sum(1), mode="lines", name=f"{s} · puste",
                                 line=dict(color=acc, width=2.4, shape="spline")))
        fig.add_trace(go.Scatter(x=x, y=(ratio > thr[1]).sum(1), mode="lines", name=f"{s} · pełne",
                                 line=dict(color=acc, width=2, dash="dot", shape="spline")))
    fig.add_vline(x=t / 3600, line=dict(color="rgba(255,255,255,0.35)", width=1, dash="dot"))
    return _style_fig(fig, f"Liczba stacji krytycznych (pusta < {thr[0]:.0%}, pełna > {thr[1]:.0%})", rng)


def critical_table(sd: dict) -> pd.DataFrame:
    run, s = sd["run"], sd["s"]
    sdf = run["stations"]
    cap = sdf["capacity"].to_numpy(float)
    df = pd.DataFrame({
        "Stacja": sdf["name"],
        "Rowery": s["bikes"].astype(int),
        "Pojemność": cap.astype(int),
        "Zapełnienie": np.round(s["bikes"] / cap * 100, 1),
        "Odrzucenia (okno)": (s["win_rent"] + s["win_ret"]).astype(int),
        "Odrzucenia (narast.)": np.rint(s["fail_cum"]).astype(int),
    })
    return df.sort_values(["Odrzucenia (narast.)", "Odrzucenia (okno)"], ascending=False).head(12)


# ──────────────────────────────────────────────────────────────────────────────
# Panel boczny
# ──────────────────────────────────────────────────────────────────────────────
def _pick_run(side: str, title: str, accent: str, cands: list[dict], default_scen: str, source: str) -> dict:
    with st.sidebar.container(border=True):
        st.markdown(f"<div class='side-title' style='--accent:{accent}'><i></i>{title}</div>", unsafe_allow_html=True)
        scen_opts = sorted({r["scenario"] for r in cands})
        idx = scen_opts.index(default_scen) if default_scen in scen_opts else (0 if side == "L" else len(scen_opts) - 1)
        scen = st.selectbox("Scenariusz", scen_opts, index=idx, key=f"{side}_scen_{source}",
                            format_func=lambda s: f"{s} · {SCENARIOS[s]['name']}")
        pool = [r for r in cands if r["scenario"] == scen]
        fleets = sorted({r["n_vans"] for r in pool})
        n = st.selectbox("Flota (furgonetki)", fleets, index=fleets.index(4) if 4 in fleets else len(fleets) - 1,
                         key=f"{side}_fleet_{source}_{scen}", disabled=len(fleets) == 1)
        pool = [r for r in pool if r["n_vans"] == n]
        seeds = sorted({r["seed"] for r in pool})
        seed = st.selectbox("Seed (replikacja CRN)", seeds, key=f"{side}_seed_{source}_{scen}_{n}",
                            disabled=len(seeds) == 1)
        return next(r for r in pool if r["seed"] == seed)


def sidebar() -> dict:
    sb = st.sidebar
    sb.markdown(_html("""
    <div class="sb-brand"><div class="logo">CB</div>
      <div><b>CitiBike DES Player</b><small>UI &amp; 3D Map · Streamlit + PyDeck</small></div></div>"""),
        unsafe_allow_html=True)
    runs = discover_runs()
    if not runs:
        st.error("Brak przebiegów w `data/outputs/snapshots/` oraz `data/mock/`. Uruchom `scripts/make_mocks.py`.")
        st.stop()

    sb.markdown("<div class='sb-sec'>Przebiegi</div>", unsafe_allow_html=True)
    sources = [s for s in ("engine", "mock") if any(r["source"] == s for r in runs)]
    source = sb.radio("Źródło danych", sources, horizontal=True, key="source",
                      format_func={"engine": "Silnik DES", "mock": "Dane mock"}.get)
    cands = [r for r in runs if r["source"] == source]
    mode = sb.radio("Tryb widoku", ["single", "split"], horizontal=True, key="view_mode",
                      format_func={"single": "Pojedyncza", "split": "Split-Screen"}.get)
    if mode == "split":
        picks = [_pick_run("L", "Lewa mapa", "#FFB547", cands, "A", source),
                 _pick_run("R", "Prawa mapa", "#FF6FB5", cands, "B", source)]
    else:
        picks = [_pick_run("S", "Przebieg", "#FFB547", cands, "A", source)]

    sb.markdown("<div class='sb-sec'>Odtwarzanie</div>", unsafe_allow_html=True)
    rng = sb.select_slider("Zakres doby", options=list(range(0, DAY_MIN + 1, 15)), value=(360, 1320),
                           format_func=fmt_hhmm, key="play_range")
    if rng[1] - rng[0] < 15:
        rng = (rng[0], min(rng[0] + 15, DAY_MIN))
    speed = sb.select_slider("Tempo (min symulacji / klatkę)", options=[1, 2, 3, 5, 10, 15, 30], value=5,
                             key="speed")
    interval = sb.select_slider("Odświeżanie klatki [s]", options=[0.25, 0.4, 0.6, 0.8, 1.0, 1.5], value=0.6,
                                key="interval")
    loop = sb.toggle("Zapętlaj odtwarzanie", value=True, key="loop")

    sb.markdown("<div class='sb-sec'>Warstwy mapy</div>", unsafe_allow_html=True)
    c1, c2 = sb.columns(2)
    layers = {
        "stations": c1.toggle("Stacje 3D", True, key="ly_st"),
        "glass": c2.toggle("Pojemność", True, key="ly_glass"),
        "fails": c1.toggle("Odrzucenia", True, key="ly_fail"),
        "heatmap": c2.toggle("Heatmapa", False, key="ly_heat"),
        "vans": c1.toggle("Furgonetki", True, key="ly_van"),
        "trails": c2.toggle("Ślady", True, key="ly_trail"),
        "route": c1.toggle("Trasa", True, key="ly_route"),
        "arcs": c2.toggle("Łuki", True, key="ly_arc"),
        "boundary": c1.toggle("Granica", True, key="ly_bnd"),
    }

    sb.markdown("<div class='sb-sec'>Wygląd</div>", unsafe_allow_html=True)
    elev_mode = sb.radio("Wysokość kolumn", ["bikes", "ratio"], horizontal=True, key="elev_mode",
                         format_func={"bikes": "Rowery", "ratio": "Zapełnienie %"}.get)
    elev_scale = sb.slider("Skala wysokości", 2.0, 20.0, 7.0, 0.5, key="elev_scale")
    radius = sb.slider("Promień kolumn [m]", 15, 80, 36, 1, key="radius")
    thr = sb.slider("Progi: pusta / pełna", 0.0, 1.0, (0.10, 0.90), 0.05, key="thr")
    pitch = sb.slider("Nachylenie kamery", 0, 70, 52, 1, key="pitch")
    zoom = sb.slider("Przybliżenie", 11.0, 15.0, 12.55 if mode == "split" else 12.75, 0.05, key=f"zoom_{mode}")
    align = sb.toggle("Wyrównaj do siatki ulic (29°)", True, key="align")

    return {
        "source": source, "mode": mode, "picks": picks, "range": (int(rng[0]), int(rng[1])), "speed": speed,
        "interval": float(interval), "loop": loop,
        "map": {**layers, "elev_mode": elev_mode, "elev_scale": elev_scale, "radius": radius, "thr": thr,
                "pitch": pitch, "zoom": zoom, "bearing": GRID_ANGLE_DEG if align else 0.0,
                "height": 600 if mode == "split" else 720},
    }


# ──────────────────────────────────────────────────────────────────────────────
# Odtwarzacz (fragment odświeżany cyklicznie podczas animacji)
# ──────────────────────────────────────────────────────────────────────────────
def _seek_to(v: int) -> None:
    st.session_state.t_min = int(v)


def _seek_by(d: int, lo: int, hi: int) -> None:
    st.session_state.t_min = int(min(max(st.session_state.t_min + d, lo), hi))


def _toggle_play(lo: int, hi: int) -> None:
    ss = st.session_state
    ss.playing = not ss.playing
    if ss.playing and ss.t_min >= hi:
        ss.t_min = lo
    ss._last_tick = time.monotonic()
    ss._full_rerun = True  # zmiana run_every fragmentu wymaga pełnego przebiegu skryptu


def player(cfg: dict, runs: list[dict]) -> None:
    ss = st.session_state
    lo, hi = cfg["range"]
    if ss.pop("_full_rerun", False):
        st.rerun()

    ss.t_min = int(min(max(ss.t_min, lo), hi))
    if ss.playing:
        now = time.monotonic()
        if now - ss.get("_last_tick", 0.0) >= cfg["interval"] * 0.7:
            ss._last_tick = now
            nxt = ss.t_min + cfg["speed"]
            if nxt > hi:
                if cfg["loop"]:
                    nxt = lo
                else:
                    ss.t_min, ss.playing = hi, False
                    st.rerun()
            ss.t_min = int(nxt)

    # ── Pasek czasu ──
    with st.container(border=True):
        c_clock, c_btn, c_slider = st.columns([1.5, 2.3, 5.7], vertical_alignment="center", gap="medium")
        with c_clock:
            st.markdown(clock_html(ss.t_min, ss.playing), unsafe_allow_html=True)
        with c_btn:
            b = st.columns([1, 1, 1.25, 1, 1], gap="small")
            kw = _stretch(st.button)
            b[0].button("|◄", key="b_start", help="Początek zakresu", on_click=_seek_to, args=(lo,), **kw)
            b[1].button("◄◄", key="b_back", help="−15 min", on_click=_seek_by, args=(-15, lo, hi), **kw)
            b[2].button("❚❚" if ss.playing else "▶", key="b_play", help="Odtwarzaj / pauza", type="primary",
                        on_click=_toggle_play, args=(lo, hi), **kw)
            b[3].button("►►", key="b_fwd", help="+15 min", on_click=_seek_by, args=(15, lo, hi), **kw)
            b[4].button("►|", key="b_end", help="Koniec zakresu", on_click=_seek_to, args=(hi,), **kw)
        with c_slider:
            slider_kw = _stretch(st.select_slider)
            if "label_visibility" in _params(st.select_slider):
                slider_kw["label_visibility"] = "collapsed"
            st.markdown(_html(f"""
            <div class="slider-meta">
              <span class="lbl">Czas symulacji <span class="cur">{fmt_hhmm(ss.t_min)}</span></span>
              <span class="rng">zakres: {fmt_hhmm(lo)} — {fmt_hhmm(hi)}</span>
            </div>"""), unsafe_allow_html=True)
            st.select_slider("Czas symulacji", options=list(range(lo, hi + 1)), format_func=fmt_hhmm, key="t_min", **slider_kw)

    t = ss.t_min * 60.0
    sides = []
    for run in runs:
        s = state_at(run, t)
        sides.append({"run": run, "s": s, "vans": van_states(run, t, s["i"], s["a"])})

    if len(sides) == 2:
        st.markdown(banner_html(sides[0], sides[1], ss.t_min), unsafe_allow_html=True)

    # ── Mapy ──
    o = cfg["map"]
    deck_kw = {} if _ST_VER >= (1, 46) else {"use_container_width": True}
    if "height" in _params(st.pydeck_chart):
        deck_kw["height"] = o["height"]
    cols = st.columns(len(sides), gap="medium")
    for k, (col, sd) in enumerate(zip(cols, sides)):
        with col:
            st.markdown(scen_head_html(sd["run"]), unsafe_allow_html=True)
            ref = sides[0] if (k == 1) else None
            st.markdown(kpi_html(sd["run"], sd["s"], sd["vans"], o["thr"], ref), unsafe_allow_html=True)
            key_kw = {"key": f"deck_{k}"} if "key" in _params(st.pydeck_chart) else {}
            st.pydeck_chart(build_deck(sd["run"], sd["s"], sd["vans"], t, o), **deck_kw, **key_kw)
    st.markdown(legend_html(o["thr"]), unsafe_allow_html=True)

    # ── Analityka ──
    tab1, tab2, tab3 = st.tabs(["Przebieg doby", "Stacje krytyczne", "Dziennik furgonetek"])
    pconf = {"displayModeBar": False}
    with tab1:
        g1, g2 = st.columns(2, gap="medium")
        with g1:
            st.plotly_chart(chart_unserved(sides, t, cfg["range"]), config=pconf, key="ch_unserved")
        with g2:
            st.plotly_chart(chart_critical(sides, t, o["thr"], cfg["range"]), config=pconf, key="ch_critical")
    with tab2:
        tcols = st.columns(len(sides), gap="medium")
        for k, (col, sd) in enumerate(zip(tcols, sides)):
            with col:
                st.caption(f"Top 12 stacji wg odrzuceń do {fmt_hhmm(ss.t_min)} — {scen_label(sd['run'])}")
                st.dataframe(
                    critical_table(sd), hide_index=True, key=f"tbl_{k}",
                    column_config={"Zapełnienie": st.column_config.ProgressColumn(
                        "Zapełnienie", format="%.0f%%", min_value=0, max_value=100)},
                )
    with tab3:
        fcols = st.columns(len(sides), gap="medium")
        for col, sd in zip(fcols, sides):
            with col:
                st.caption(f"Ostatnie kursy — {scen_label(sd['run'])}")
                if sd["run"]["meta"]["n_vans"] == 0:
                    st.markdown("<div class='feed'><div class='empty'>Ten scenariusz nie używa furgonetek.</div></div>",
                                unsafe_allow_html=True)
                else:
                    st.markdown(van_feed_html(sd["run"], t), unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    cfg = sidebar()

    runs = []
    for pick in cfg["picks"]:
        try:
            runs.append(load_run(pick["path"], pick["source"], Path(pick["path"]).stat().st_mtime))
        except Exception as exc:  # noqa: BLE001 — czytelny komunikat zamiast tracebacku w UI
            st.error(f"Nie udało się wczytać `{Path(pick['path']).name}`: {exc}")
            st.stop()

    ss = st.session_state
    ss.setdefault("playing", False)
    ss.setdefault("t_min", cfg["range"][0])

    st.markdown(hero_html([{"run": r} for r in runs], ss.playing), unsafe_allow_html=True)
    st.fragment(player, run_every=cfg["interval"] if ss.playing else None)(cfg, runs)

    src = ", ".join(sorted({f"{Path(r['path']).name} + {r['stations_file']}" for r in runs}))
    st.markdown(f"<div class='foot'>Źródła: {html.escape(src)} · Podkład © CARTO, © OpenStreetMap contributors"
                "</div>", unsafe_allow_html=True)


main()
