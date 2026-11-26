# Obecny Stan Projektu: Symulacja Dyskretna i Optymalizacja Relokacji Citi Bike NYC

**Data aktualizacji:** 2026-11-26 (po merge'u PR #3–#7 do `main` oraz ukończeniu Milestone 3 i zadań `Task 4.1`, `Task 4.3`)  
**Punkt odniesienia:** [`plan_realizacji_projektu_citi_bike_nyc.md`](./plan_realizacji_projektu_citi_bike_nyc.md) oraz [`CONTRACTS.md`](./CONTRACTS.md)

---

## 1. Podsumowanie Wykonawcze (Executive Summary)

Wszystkie komponenty symulatora (Scenariusze A, B i C), walidacja Tygodnia 4 (Hipoteza H0), wielordzeniowy batch runner CRN (`scripts/03_batch_experiments.py`), dopracowany dashboard 3D z trybem jasnym/ciemnym i wariantami pogodowymi (`scripts/04_dashboard_app.py`) oraz gotowe nagrania awaryjne (`reports/media/demo_dashboard_60s.{mp4,webm}`) są zaimplementowane i przetestowane (**27/27 testów `pytest` przechodzi**, `ruff check` bez błędów). Do domknięcia projektu pozostały już tylko **Task 4.2** (analiza statystyczna i wykresy hipotez H1–H6) oraz **Task 4.4** (Rozdziały 6–7 raportu i wnioski końcowe).

| Milestone | Status ogólny | Szacowany postęp | Podsumowanie |
| :--- | :---: | :---: | :--- |
| **Milestone 1:** Fundament, Kontrakt Danych i Pipeline | **UKOŃCZONY** | **100%** | Struktura repozytorium, kontrakty Pydantic (`CONTRACTS.md`), pipeline Polars (`01_prepare_data.py`), pobieranie danych i pogody (`00_download_data.py`), mocki (`A`, `B`, `C`) oraz zbieracz GBFS (`data-gbfs` + `sync_gbfs.py`). |
| **Milestone 2:** Kalibracja Matematyczna i MVP Symulatora + Mapy | **UKOŃCZONY** | **100%** | Kalibracja $\lambda_i(t)$, korekta cenzurowania `exposure_frac` ($+19{,}55\%$ ukrytego popytu), macierz OD, mnożnik pogodowy ($0{,}55$), silnik DES (`02_run_simulation.py`), Dashboard 3D (`04_dashboard_app.py`), wykresy EDA (`fig1`–`fig3`) i Rozdziały 1–4 raportu. |
| **Milestone 3:** Walidacja Modelu i Heurystyki Relokacji B oraz C | **UKOŃCZONY** | **100%** | Walidacja Tygodnia 4 (`fig4_week4_validation.{png,pdf}`, `reports/validation_summary.json`, Rozdział 5 raportu, `tests/test_validation.py`), heurystyki furgonetek `ScenarioB` (`src/strategies/reactive.py`) i `ScenarioC` (`src/strategies/predictive.py`) oraz Split-Screen z animacją furgonetek. |
| **Milestone 4:** Wielkie Eksperymenty, Weryfikacja Hipotez H1–H6 i Finał | **W TRAKCIE** | **~50%** | Gotowy wielordzeniowy batch runner CRN (`Task 4.1` — `scripts/03_batch_experiments.py`) oraz szlif dashboardu i nagrania awaryjne (`Task 4.3` — `reports/media/demo_dashboard_60s.{mp4,webm}`). Do wykonania pozostają `Task 4.2` (testy H1–H6 + wykresy końcowe) i `Task 4.4` (Rozdziały 6–7 raportu). |

---

## 2. Co znajduje się obecnie w repozytorium

1. **Pipeline ETL i Kalibracja (`src/pipeline/`, `scripts/00_download_data.py`, `scripts/01_prepare_data.py`):**
   - `stations.json` — 383 stacje w wielokącie `MANHATTAN_POLYGON` (pojemność łączna $22\,401$ doków).
   - `trips_clean.parquet` — $2\,685\,269$ oczyszczonych przejazdów ($2\,183\,859$ kalibracja `1–23.09`, $501\,410$ walidacja `24–30.09`).
   - `lambda_15min.parquet`, `od_matrix.parquet`, `weather.py` — parametry NHPP z korektą `exposure_frac`, macierz OD z wygładzaniem Bayesowskim i empiryczny mnożnik deszczowy $w_{\text{rain}} = 0{,}55$.
2. **Silnik Symulacji DES i Strategie (`src/engine/`, `src/strategies/`, `scripts/02_run_simulation.py`, `scripts/03_batch_experiments.py`):**
   - `demand_gen.py` + `des.py` — generator NHPP z *Common Random Numbers* (CRN), przekierowania piesze i pełne metryki KPI; `02_run_simulation.py` zapisuje snapshoty wg pełnego `run_id` (warianty `wx-rain`, `dt-weekend` nie nadpisują bazowych przebiegów).
   - `src/strategies/reactive.py` (`ScenarioB`) oraz `src/strategies/predictive.py` (`ScenarioC`).
   - `scripts/03_batch_experiments.py` — wielordzeniowy runner eksperymentów CRN (flota, wrażliwość, pogoda).
3. **Interaktywny Dashboard 3D i Media (`scripts/04_dashboard_app.py`, `reports/media/`):**
   - Mapa 3D w Streamlit + PyDeck, przełącznik motywu ciemny/jasny, domyślny tryb `Split-Screen` (`A` vs `C`), wybór wariantów (`deszcz`/`weekend`/progi/horyzont), karta KPI `Poziom obsługi` (`%` i `pp`), baner efektywności (`uratowanych / km`), animacja furgonetek i 3 zakładki analityczne.
   - Działa od razu po sklonowaniu repozytorium na wbudowanych danych `data/mock/` lub na wynikach silnika z `data/outputs/snapshots/`.
   - Gotowe nagrania awaryjne na prezentację (`1920×1102`, `30 fps`, `47 s`, wykadrowane bez paska przeglądarki i docka):
     - [`reports/media/demo_dashboard_60s.mp4`](./reports/media/demo_dashboard_60s.mp4) (`7.4 MB`, H.264 `+faststart` — PowerPoint / Keynote / Windows / macOS / Linux)
     - [`reports/media/demo_dashboard_60s.webm`](./reports/media/demo_dashboard_60s.webm) (`5.9 MB`, VP9 — przeglądarki / Linux)
4. **EDA, Walidacja i Raport (`src/viz/`, `scripts/05_eda_report.py`, `reports/`):**
   - Figury wektorowe (`.pdf` + `.png`) w `reports/figures/`: `fig1_spatial_asymmetry`, `fig2_diurnal_asymmetry`, `fig3_calibration_diagnostics`, `fig4_week4_validation`.
   - Raport [`reports/raport_citi_bike_nyc.md`](./reports/raport_citi_bike_nyc.md) (kompletne Rozdziały 1–5 + szkielet Rozdziałów 6–7), [`reports/eda_summary.json`](./reports/eda_summary.json), [`reports/validation_summary.json`](./reports/validation_summary.json).
5. **Testy i Jakość Kodu (`tests/`):**
   - 27 testów jednostkowych (`test_contracts.py`, `test_pipeline.py`, `test_engine.py`, `test_eda.py`, `test_validation.py`) — **27 passed**.
   - `ruff check src scripts tests` — **0 błędów**.

---

## 3. Mapowanie Stanu Projektu na Plan Realizacji (`plan_realizacji_projektu_citi_bike_nyc.md`)

### MILESTONE 1: Fundament, Kontrakt Danych i Pipeline — **100% GOTOWE**
- **[Task 1.1] Inicjalizacja repozytorium i Kontraktu Danych:** **GOTOWE (100%)**
- **[Task 1.2] Pobranie danych i filtracja przestrzenna w Polars:** **GOTOWE (100%)**
- **[Task 1.3] Uruchomienie skryptu zbierającego migawki GBFS:** **GOTOWE (100%)**
- **[Task 1.4] Pobranie danych pogodowych:** **GOTOWE (100%)**

### MILESTONE 2: Kalibracja Matematyczna i MVP Symulatora + Mapy — **100% GOTOWE**
- **[Task 2.1] Kalibracja NHPP, Macierzy OD i korekta cenzurowania:** **GOTOWE (100%)**
- **[Task 2.2] Silnik DES (`heapq`) i Scenariusz A – Bazowy:** **GOTOWE (100%)**
- **[Task 2.3] Dashboard MVP w Streamlit + PyDeck:** **GOTOWE (100%)**
- **[Task 2.4] Szkielet raportu (Rozdziały 1–4) i Analiza Eksploracyjna Danych – EDA:** **GOTOWE (100%)**

### MILESTONE 3: Walidacja Modelu i Heurystyki Relokacji B oraz C — **100% GOTOWE**
- **[Task 3.1] Walidacja dwuetapowa na Tygodniu 4 – Hipoteza H0:** **GOTOWE (100%)**
- **[Task 3.2] Model Agenta Furgonetki i Scenariusz B – Reaktywny:** **GOTOWE (100%)**
- **[Task 3.3] Scenariusz C – Predykcyjny / Dynamiczny:** **GOTOWE (100%)**
- **[Task 3.4] Rozbudowa Wizualizacji do trybu „Split-Screen” i animacji Vanów:** **GOTOWE (100%)**

### MILESTONE 4: Wielkie Eksperymenty, Weryfikacja Hipotez H1–H6 i Finał — **~50% GOTOWE**
- **[Task 4.1] Batch Runner z Common Random Numbers – CRN (`03_batch_experiments.py`):** **GOTOWE (100%)**
- **[Task 4.2] Testy statystyczne i generowanie wykresów do raportu (H1–H6):** **DO ZROBIENIA (0%)**
- **[Task 4.3] Szlif wizualizacji i nagranie gifa/wideo awaryjnego:** **GOTOWE (100% — `reports/media/demo_dashboard_60s.{mp4,webm}`)**
- **[Task 4.4] Redakcja wniosków końcowych i rekomendacji:** **DO ZROBIENIA (0%)**

---

## 4. Pozostałe Kroki do Domknięcia Projektu

1. **[Task 4.2] Testy statystyczne i wykresy końcowe (H1–H6):**
   - Uruchomienie pełnej serii 30 replikacji CRN przez `scripts/03_batch_experiments.py`.
   - Sparowane testy t-Studenta / Wilcoxona dla hipotez **H1–H6** oraz wygenerowanie wykresów porównawczych (A vs B vs C, skalowanie floty $N$, wrażliwość progów/horyzontu/pojemności/pogody) i tabeli zbiorczej do `reports/figures/`.
2. **[Task 4.4] Finalizacja raportu (`reports/raport_citi_bike_nyc.md`):**
   - Uzupełnienie **Rozdziału 6** (opis zaimplementowanych algorytmów B i C) oraz **Rozdziału 7** (wyniki eksperymentów, weryfikacja H1–H6, rekomendacje operacyjne).
