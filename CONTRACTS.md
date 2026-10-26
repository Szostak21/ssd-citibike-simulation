# Data Contracts v1.0 (`schema_version = "1.0"`)

Sztywne kontrakty wymiany danych pomiędzy **Stream 1 (Data & Engine)** a **Stream 2 (Viz & Experiments)**.
Walidacja programowa znajduje się w `src/common/contracts.py`, a schematy JSON Schema w `data/mock/schemas/`.

## Konwencje globalne
- **Czas symulacji `t`**: sekundy od północy (`0..86400`, `int`).
- **Okno czasowe `slot`**: `t // 900` (`0..95`, `int16`, okna 15-minutowe).
- **Typ dnia `day_type`**: `"weekday"` lub `"weekend"` (święta jak Labor Day `2026-09-07` wliczane do `"weekend"`).
- **Identyfikator stacji `station_id`**: `string` odpowiadający `short_name` z GBFS (z kanoniczną normalizacją uciętych zer końcowych typu `"5470.1"` -> `"5470.10"`).
- **Węzeł zewnętrzny `"EXT"`**: reprezentuje wszystkie stacje spoza polygonu dolnego Manhattanu.

---

## 1. `data/processed/stations.json` (oraz `data/mock/mock_stations.json`)

Lista stacji w obszarze dolnego Manhattanu posortowana po `idx = 0..N-1` (kolejność kanoniczna dla wektorów w `snapshots`).

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-10-26T18:54:42+00:00",
  "source_month": "2026-09",
  "stations": [
    {
      "idx": 0,
      "station_id": "4846.01",
      "gbfs_id": "66db237e-0aca-11e7-82f6-3863bb44ef7c",
      "name": "South St & Whitehall St",
      "lat": 40.701221,
      "lon": -74.012342,
      "capacity": 47,
      "capacity_source": "gbfs",
      "aliases": [],
      "neighbors": ["4962.01", "4962.02"]
    }
  ]
}
```

---

## 2. `data/processed/lambda_15min.parquet`

Pełna siatka `(N + 1) × 2 × 96` (włącznie z węzłem `"EXT"`).

| Kolumna | Typ Polars | Opis |
|---|---|---|
| `station_id` | `String` | ID stacji lub `"EXT"` (rowery wjeżdżające spoza obszaru) |
| `day_type` | `String` | `"weekday"` / `"weekend"` |
| `slot` | `Int16` | `0..95` (okno 15-min) |
| `rent_count` | `Int32` | Liczba wypożyczeń w okresie kalibracji (dni 1–23 września) |
| `n_days` | `Int16` | Liczba dni danego typu w okresie kalibracji |
| `lambda_raw` | `Float64` | Surowa intensywność `rent_count / (n_days * 0.25)` [wypożyczeń / h] |
| `exposure_frac` | `Float64` | Ułamek czasu, gdy stacja miała `>0` rowerów (`(0.2, 1.0]`, domyślnie `1.0` gdy brak 7 dni GBFS) |
| `lambda` | `Float64` | Skorygowana intensywność `lambda_raw / exposure_frac` [wypożyczeń / h] — **używana przez silnik DES** |
| `return_rate` | `Float64` | Empiryczna intensywność zwrotów [zwrotów / h] (dla heurystyki predykcyjnej C) |

---

## 3. `data/processed/od_matrix.parquet`

Rzadka macierz przejść Origin–Destination z wygładzaniem empirycznym Bayesowskim (`alpha = 5.0`) do profilu dobowego oraz rozkładami log-normalnymi czasu przejazdu.

| Kolumna | Typ Polars | Opis |
|---|---|---|
| `day_type` | `String` | `"weekday"` / `"weekend"` |
| `hour` | `Int8` | `0..23` |
| `origin_id` | `String` | Stacja początkowa lub `"EXT"` |
| `dest_id` | `String` | Stacja docelowa lub `"EXT"` (brak par `EXT -> EXT`) |
| `n_trips` | `Int32` | Liczba zaobserwowanych przejazdów w danej godzinie |
| `prob` | `Float64` | Prawdopodobieństwo przejścia `P(dest | origin, day_type, hour)`, suma po `dest_id` = `1.0` |
| `dur_mu` | `Float64` | Parametr $\mu$ rozkładu log-normalnego czasu przejazdu w $\ln(\text{s})$ |
| `dur_sigma` | `Float64` | Parametr $\sigma > 0$ rozkładu log-normalnego czasu przejazdu |
| `dur_median_s` | `Float64` | Mediana czasu przejazdu $\exp(\mu)$ [s] |

---

## 4. `snapshots_24h` (`data/mock/mock_snapshots_24h_{A|C}.json`, `data/outputs/snapshots/{scenario}_n{vans}_s{seed}.json`)

97 klatek (`t = 0, 900, ..., 86400`). Tablice `bikes`, `fail_rent`, `fail_return` mają długość `N` i odpowiadają kolejności `meta.station_ids` (`idx = 0..N-1` z `stations.json`).

- `bikes[i]`: liczba rowerów na stacji `i` w chwili `t`. Liczba wolnych doków to `capacity[i] - bikes[i]`.
- `fail_rent[i]`, `fail_return[i]`: liczba odrzuconych wypożyczeń/zwrotów w oknie `(t - step_s, t]` (w klatce `t = 0` same zera).
- `van_legs`: ciągłe odcinki tras furgonetek (`t_start`, `t_end`, `from_station_id`, `to_station_id`, `load`, `action`, `qty`) do płynnej animacji `ArcLayer` / `TripsLayer`.

---

## 5. `metrics_runs.csv` oraz `metrics_hourly.csv` (`data/mock/mock_metrics_*.csv`, `data/outputs/metrics/metrics_*.csv`)

- **`metrics_runs.csv`**: `run_id, scenario, seed, n_vans, van_capacity, thr_low, thr_high, horizon_min, weather, day_type, demand_total, rentals_ok, fail_rent, fail_return, walk_redirect, empty_station_min, full_station_min, van_km, bikes_moved`
- **`metrics_hourly.csv`**: `run_id, hour, demand, rentals_ok, fail_rent, fail_return, empty_station_min, full_station_min`
