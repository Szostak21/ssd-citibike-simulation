# Obecny Stan Projektu: Symulacja Dyskretna i Optymalizacja Relokacji Citi Bike NYC

**Data aktualizacji:** 2026-11-26 (po ukończeniu wszystkich zadań Milestone 1–4, w tym `Task 4.2` oraz `Task 4.4`)  
**Punkt odniesienia:** [`plan_realizacji_projektu_citi_bike_nyc.md`](./plan_realizacji_projektu_citi_bike_nyc.md) oraz [`CONTRACTS.md`](./CONTRACTS.md)

---

## 1. Podsumowanie Wykonawcze (Executive Summary)

Wszystkie cztery kamienie milowe projektu (**Milestone 1–4**) zostały w pełni zrealizowane (**100%**): komponenty symulatora (Scenariusze A, B i C), walidacja Tygodnia 4 (Hipoteza H0), wielordzeniowy batch runner CRN (`scripts/03_batch_experiments.py`), analiza statystyczna i weryfikacja hipotez H1–H6 wraz z figurami końcowymi (`scripts/analyze_experiments.py`, `src/analytics/`), interaktywny dashboard 3D z nagraniami awaryjnymi (`scripts/04_dashboard_app.py`, `reports/media/demo_dashboard_60s.{mp4,webm}`) oraz kompletny raport końcowy z rekomendacją inżynierską (`reports/raport_citi_bike_nyc.md` — Rozdziały 1–7) są gotowe i przetestowane (**29/29 testów `pytest` przechodzi**, `ruff check` bez błędów).

| Milestone | Status ogólny | Szacowany postęp | Podsumowanie |
| :--- | :---: | :---: | :--- |
| **Milestone 1:** Fundament, Kontrakt Danych i Pipeline | **UKOŃCZONY** | **100%** | Struktura repozytorium, kontrakty Pydantic (`CONTRACTS.md`), pipeline Polars (`01_prepare_data.py`), pobieranie danych i pogody (`00_download_data.py`), mocki (`A`, `B`, `C`) oraz zbieracz GBFS (`data-gbfs` + `sync_gbfs.py`). |
| **Milestone 2:** Kalibracja Matematyczna i MVP Symulatora + Mapy | **UKOŃCZONY** | **100%** | Kalibracja $\lambda_i(t)$, korekta cenzurowania `exposure_frac` ($+19{,}55\%$ ukrytego popytu), macierz OD, mnożnik pogodowy ($0{,}55$), silnik DES (`02_run_simulation.py`), Dashboard 3D (`04_dashboard_app.py`), wykresy EDA (`fig1`–`fig3`) i Rozdziały 1–4 raportu. |
| **Milestone 3:** Walidacja Modelu i Heurystyki Relokacji B oraz C | **UKOŃCZONY** | **100%** | Walidacja Tygodnia 4 (`fig4_week4_validation.{png,pdf}`, `reports/validation_summary.json`, Rozdział 5 raportu, `tests/test_validation.py`), heurystyki furgonetek `ScenarioB` (`src/strategies/reactive.py`) i `ScenarioC` (`src/strategies/predictive.py`) oraz Split-Screen z animacją furgonetek. |
| **Milestone 4:** Wielkie Eksperymenty, Weryfikacja Hipotez H1–H6 i Finał | **UKOŃCZONY** | **100%** | Wielordzeniowy batch runner CRN (`Task 4.1`), testy statystyczne 95% CI + t-Student/Wilcoxon dla H1–H6 i figury raportowe (`Task 4.2`), szlif dashboardu i nagrania wideo (`Task 4.3`) oraz kompletne Rozdziały 6–7 raportu z rekomendacją inżynierską (`Task 4.4`). |

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
4. **EDA, Walidacja, Analiza Statystyczna i Raport (`src/viz/`, `src/analytics/`, `scripts/05_eda_report.py`, `scripts/analyze_experiments.py`, `reports/`):**
   - Figury wektorowe (`.pdf` + `.png`) w `reports/figures/`: `fig1_spatial_asymmetry`, `fig2_diurnal_asymmetry`, `fig3_calibration_diagnostics`, `fig4_week4_validation`, `fig_hourly_unserved`, `fig_fleet_pareto`, `fig_cost_efficiency`, `fig_kpi_summary`.
   - Tabele statystyczne w `reports/`: [`reports/kpi_summary_95ci.csv`](./reports/kpi_summary_95ci.csv), [`reports/hypotheses_h1_h6.csv`](./reports/hypotheses_h1_h6.csv), [`reports/hypotheses_h1_h6.md`](./reports/hypotheses_h1_h6.md), [`reports/hypotheses_h1_h6.tex`](./reports/hypotheses_h1_h6.tex).
   - Kompletny raport końcowy [`reports/raport_citi_bike_nyc.md`](./reports/raport_citi_bike_nyc.md) (Rozdziały 1–7 wraz z weryfikacją hipotez H0–H6 i rekomendacją inżynierską), [`reports/eda_summary.json`](./reports/eda_summary.json), [`reports/validation_summary.json`](./reports/validation_summary.json).
5. **Testy i Jakość Kodu (`tests/`):**
   - 29 testów jednostkowych (`test_analytics.py`, `test_contracts.py`, `test_pipeline.py`, `test_engine.py`, `test_eda.py`, `test_validation.py`) — **29 passed**.
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

### MILESTONE 4: Wielkie Eksperymenty, Weryfikacja Hipotez H1–H6 i Finał — **100% GOTOWE**
- **[Task 4.1] Batch Runner z Common Random Numbers – CRN (`03_batch_experiments.py`):** **GOTOWE (100%)**
- **[Task 4.2] Testy statystyczne i generowanie wykresów do raportu (H1–H6):** **GOTOWE (100% — `src/analytics/`, `scripts/analyze_experiments.py`, `reports/hypotheses_h1_h6.md`, `reports/figures/fig_*`)**
- **[Task 4.3] Szlif wizualizacji i nagranie gifa/wideo awaryjnego:** **GOTOWE (100% — `reports/media/demo_dashboard_60s.{mp4,webm}`)**
- **[Task 4.4] Redakcja wniosków końcowych i rekomendacji:** **GOTOWE (100% — `reports/wnioski_i_rekomendacje.md` oraz Rozdziały 6–7 w `reports/raport_citi_bike_nyc.md`)**
