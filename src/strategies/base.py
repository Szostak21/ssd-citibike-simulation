from dataclasses import dataclass, field


@dataclass(slots=True)
class VanAgent:
    van_id: int
    lat: float
    lon: float
    load: int = 0
    status: str = "off"
    target_station_id: str | None = None
    km_total: float = 0.0


@dataclass(slots=True)
class BaseStrategy:
    scenario: str = "A"
    n_vans: int = 0
    van_capacity: int = 25
    thr_low: float = 0.1
    thr_high: float = 0.9
    horizon_min: int = 0
    vans: list[VanAgent] = field(default_factory=list)
    van_legs: list[dict] = field(default_factory=list)
    bikes_moved: int = 0

    def initialize(self, sim) -> None:
        pass

    def on_station_change(self, sim, station_idx: int, t: int) -> None:
        pass

    def on_tick(self, sim, t: int) -> None:
        pass

    def van_snapshots(self) -> list[dict]:
        return [
            {
                "van_id": v.van_id,
                "lat": round(v.lat, 6),
                "lon": round(v.lon, 6),
                "load": int(v.load),
                "status": v.status,
                "target_station_id": v.target_station_id,
            }
            for v in self.vans
        ]

    @property
    def total_km(self) -> float:
        return sum(v.km_total for v in self.vans)


class ScenarioA(BaseStrategy):
    def __init__(self, van_capacity: int = 25, thr_low: float = 0.1, thr_high: float = 0.9):
        super().__init__(
            scenario="A",
            n_vans=0,
            van_capacity=van_capacity,
            thr_low=thr_low,
            thr_high=thr_high,
            horizon_min=0,
        )
