# ssd-citibike-simulation

Symulacja zdarzeniowa (DES) oraz wieloagentowa (ABM) systemu rowerów miejskich Citi Bike na dolnym Manhattanie (383 stacje poniżej 59th St, Nowy Jork).

---

## Quick Start

### 1. Przygotowanie środowiska i danych
```bash
# Instalacja zależności
uv sync

# Generacja stacji, parametrów Poisson/OD i plików wejściowych (ETL)
uv run python scripts/01_prepare_data.py
```

### 2. Uruchomienie symulacji (DES 24h)
Główny silnik symulacji uruchamia się za pomocą skryptu `scripts/02_run_simulation.py`:

```bash
uv run python scripts/02_run_simulation.py --scenario A --seed 1 --day-type weekday --weather dry --init-fill 0.5
```

#### Flagi CLI (`scripts/02_run_simulation.py`):
| Flaga | Dopuszczalne wartości | Domyślnie | Opis |
|---|---|---|---|
| `--scenario` | `A`, `B`, `C` | `A` | Scenariusz rebalansingu: `A` (baseline: brak vanów w dzień, reset nocny), `B` (reaktywny), `C` (predykcyjny) |
| `--n-vans` | Liczba całkowita ($\ge 0$) | `0` | Liczba furgonetek rebalansujących (dla scenariusza `A` wymuszone 0) |
| `--seed` | Liczba całkowita | `1` | Ziarno generatora liczb pseudolosowych (CRN – deterministyczny, wspólny strumień klientów) |
| `--day-type` | `weekday`, `weekend` | `weekday` | Profil popytu kalibrowany z danych historycznych (dzień powszedni / weekend) |
| `--weather` | `dry`, `rain` | `dry` | Warunki pogodowe (`rain` uwzględnia empiryczny mnożnik spadku popytu z danych NOAA) |
| `--init-fill` | Ułamek $0.0 - 1.0$ | `0.5` | Początkowe zapełnienie stacji o godzinie 00:00 (np. `0.5` = 50% pojemności docków) |

Wyniki symulacji zapisują się do:
- `data/outputs/snapshots/{scenario}_n{n_vans}_s{seed}.json` – snapshoty stanu co 15 minut dla dashboardu,
- `data/outputs/metrics/metrics_runs.csv` oraz `metrics_hourly.csv` – zintegrowane wskaźniki KPI (globalne i godzinowe).

### 3. Interaktywny Dashboard (Streamlit)
```bash
uv run streamlit run scripts/04_dashboard_app.py
```
Aplikacja otwiera się w przeglądarce pod adresem `http://localhost:8501`. Zawiera wizualizację 2D/3D stacji na mapie Manhattanu, odtwarzacz osi czasu (co 15 min), wskaźniki KPI na żywo oraz porównywarkę wielu scenariuszy obok siebie.

### 4. Testy i kontrola jakości
```bash
uv run pytest -v
uv run ruff check src scripts tests
```

---

## Zbieranie danych GBFS (GitHub Actions + Scheduler cron-job.org)

Workflow `.github/workflows/gbfs_cron.yml` uruchamia się co 15 minut (wyzwalany niezawodnie przez webhook API z serwisu `cron-job.org` via `workflow_dispatch` oraz zapasowo przez wewnętrzny GitHub `schedule`),
pobiera `station_status.json` dla stacji dolnego Manhattanu i commituje jeden plik Parquet
do osieroconego brancha `data-gbfs`:

```
gbfs_status/YYYY-MM-DD/HHMMSS.parquet   # data i czas wg America/New_York
```

Harmonogram webhooka API celuje w `main`. Zewnętrzny scheduler eliminuje problem pomijania i opóźniania zadań przez wewnętrzny mechanizm schedule na bezpłatnych kontach GitHuba.

Pobranie zebranych danych lokalnie (do `data/raw/gbfs_status/`):

```bash
uv run python scripts/sync_gbfs.py
uv run python scripts/01_prepare_data.py   # exposure_frac liczony po zebraniu >= 7 dni
```

Pojedynczy odczyt ręcznie: `uv run python scripts/harvest_gbfs.py --out /tmp/gbfs`.
Zapasowy kolektor lokalny (CSV co 5 min): `nohup uv run python scripts/collect_gbfs.py > gbfs.log &`.
