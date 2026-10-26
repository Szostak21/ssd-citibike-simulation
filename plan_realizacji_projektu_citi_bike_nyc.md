# Plan Realizacji Projektu: Symulacja Dyskretna i Optymalizacja Relokacji Citi Bike NYC

**Metodyka:** Discrete Event Simulation (DES) + Agent-Based Modeling (ABM)  
**Narzędzia:** Python 3.11+ (`polars`, `pandas`, `numpy`, `scipy`, `pydantic`, `heapq`), Wizualizacja (`streamlit`, `pydeck` z podkładem CARTO `dark`, `plotly`), Zarządzanie pakietami (`uv`), AI (`Cursor` / `Claude Opus` / `Gemini`)

---

## 0. Ustalenia Architektoniczne i Decyzje Zespołowe (Milestone 1 – ZAMROŻONE)

1. **Przetwarzanie danych (Polars + Pandas):** Główny pipeline ETL (`scripts/01_prepare_data.py`, `src/pipeline/`) działa w **Polars** (lazy evaluation na 5 plikach CSV z września `2026-09`, ~4.98 mln surowych wierszy przetwarzane w ~4.5 s). Do wizualizacji i testów statystycznych używamy zamiennie Polars / Pandas.
2. **Miesiąc referencyjny (`2026-09`):** Korzystamy z pełnego września (`202609-citibike-tripdata_*.csv`). Podział czasowy: **1–23 września (Kalibracja, 15 dni roboczych + 8 dni weekendowych/świątecznych z Labor Day `2026-09-07` traktowanym jako weekend)** oraz **24–30 września (Walidacja, Tydzień 4)**.
3. **Filtracja przestrzenna – Wielokąt (Polygon) Dolnego Manhattanu zamiast samego BBoxa:** Zwykły prostokąt `lat 40.7000–40.7650, lon -74.0200 do -73.9600` zahacza przez East River o Brooklyn (DUMBO, Brooklyn Heights, Williamsburg) oraz nie uwzględnia nachylenia siatki ulic Manhattanu (`29°`). Stosujemy 19-wierzchołkowy wielokąt (`MANHATTAN_POLYGON` w `src/common/config.py`) precyzyjnie wycinający Manhattan poniżej 59th St (383 aktywne stacje, 0 stacji z Brooklynu/NJ). Przejazdy wjeżdżające spoza wielokąta lub wyjeżdżające poza niego są mapowane na wirtualny węzeł `"EXT"`.
4. **Łączenie identyfikatorów CSV ↔ GBFS i duplikaty:** CSV używa kodów `short_name` z GBFS (np. `"5788.13"`), przy czym część wierszy w CSV ma obcięte końcowe zero po kropce (`"5470.1"` zamiast `"5470.10"`) lub duplikaty z podkreślnikiem (`"6517.08_"`). Pipeline automatycznie normalizuje ID i scala duplikaty po nazwie stacji (`aliases` w `stations.json`).
5. **Kolektor GBFS (GitHub Actions `.github/workflows/gbfs_cron.yml`):** Główne źródło — workflow co 10 minut odpytuje `station_status.json` (`scripts/harvest_gbfs.py`) i commituje plik `gbfs_status/YYYY-MM-DD/HHMMSS.parquet` na osierocony branch `data-gbfs`; lokalnie dane pobiera `scripts/sync_gbfs.py` do `data/raw/gbfs_status/`. Zapasowo `scripts/collect_gbfs.py` może działać w tle na laptopie (`nohup`/`tmux`), odpytując co 5 minut i zapisując dzienne pliki CSV `data/raw/gbfs_status/YYYY-MM-DD.csv`; pipeline czyta oba formaty i usuwa duplikaty po `(ts, station_id)`. Ponieważ dane historyczne CSV pochodzą z września, a GBFS zbieramy na żywo, z GBFS wyznaczamy **profil ekspozycji** `exposure_frac(station_id, day_type, slot)` (ułamek czasu, gdy stacja miała $>0$ rowerów) i korygujemy nim historyczną intensywność: $\lambda = \lambda_{\text{raw}} / \max(\text{exposure\_frac}, 0.2)$. Dopóki nie zbierzemy min. 7 dni GBFS, pipeline ustawia `exposure_frac = 1.0`, zachowując identyczny schemat pliku `lambda_15min.parquet`.
6. **Zamrożone Kontrakty Danych v1 (`CONTRACTS.md`, `src/common/contracts.py`):**
   - `data/processed/stations.json` (oraz `data/mock/mock_stations.json`)
   - `data/processed/lambda_15min.parquet`
   - `data/processed/od_matrix.parquet`
   - `data/mock/mock_snapshots_24h_{A|C}.json` (docelowo `data/outputs/snapshots/{scenario}_n{vans}_s{seed}.json`)
   - `data/mock/mock_metrics_runs.csv` oraz `mock_metrics_hourly.csv` (docelowo `data/outputs/metrics/metrics_*.csv`)

---

## 1. Filozofia pracy i organizacja zespołu (Model Hybrydowy 2x2)

Ponieważ używamy zaawansowanych modeli AI do generowania kodu, naszym wąskim gardłem nie jest klepanie składni, tylko **spójność danych, brak konfliktów w Git oraz poprawność matematyczna**. 

Dlatego stosujemy luźny podział na **dwie pary (2x2)**, ale z przypisanymi głównymi właścicielami modułów (*Leadami*), aby każdy wiedział, za co odpowiada:

* **ZESPÓŁ A: „Data & Simulation Engine” (2 osoby)**
  * **Osoba 1 (Data & Calibration Lead):** Odpowiada za pobranie danych, wycięcie Manhattanu, policzenie intensywności $\lambda_i(t)$, macierzy przejść OD, korekty cenzurowania popytu oraz danych pogodowych.
  * **Osoba 2 (DES Core & Heuristics Lead):** Odpowiada za pętlę zdarzeń dyskretnych (`heapq`), logikę stacji i klientów oraz implementację algorytmów dyspozytora furgonetek (Scenariusze A, B, C).
* **ZESPÓŁ B: „Visualization, Analytics & Report” (2 osoby)**
  * **Osoba 3 (UI & 3D Map Lead):** Odpowiada za interaktywny odtwarzacz w Streamlit + PyDeck (mapa 3D Manhattanu w Dark Mode, widok Split-Screen, animacja furgonetek, suwak czasu).
  * **Osoba 4 (Experiments, Stats & Report Lead):** Odpowiada za walidację modelu (Tydzień 4), uruchomienie 30 replikacji ze wspólnymi liczbami losowymi (*Common Random Numbers*), testy statystyczne hipotez H0–H6 i skład raportu (LaTeX/PDF).

> **Złota zasada braku przestojów:** W Milestone 1 zamrażamy format plików wymiany danych (`snapshots.json` oraz `metrics.csv`). Dzięki temu Zespół B buduje wizualizację i wykresy na sztucznych danych (mockach) równolegle, nie czekając aż Zespół A skończy pisać silnik symulacji.

---

## 2. Architektura bez Overengineeringu

Nie stawiamy żadnych baz danych, Dockera ani osobnych serwerów API. Cały projekt to **4 czyste skrypty w Pythonie**:
1. `01_prepare_data.py` — mieli surowe CSV w Pandasie i wypluwa lekkie pliki parametrów do katalogu `data/processed/`.
2. `02_run_simulation.py` — wczytuje parametry, odpala pętlę zdarzeń `heapq` dla wybranego scenariusza i zapisuje logi przebiegu.
3. `03_batch_experiments.py` — odpala symulator wielokrotnie (30 replikacji na wielu rdzeniach CPU), liczy testy statystyczne i generuje statyczne wykresy do raportu.
4. `04_dashboard_app.py` — aplikacja Streamlit wczytująca wygenerowany przebieg 24h i wyświetlająca interaktywne porównanie na mapie 3D.

---

## 3. Harmonogram i Milestony

### MILESTONE 1: Fundament, Kontrakt Danych i Pipeline (Czas: 2–3 dni)
**Cel:** Mamy skonfigurowane repozytorium, wyczyszczone dane dla wycinka Manhattanu (300–500 stacji), działający w tle zbieracz GBFS oraz wygenerowany plik testowy (mock), dzięki któremu rusza budowa mapy 3D.

* **[Task 1.1] Inicjalizacja repozytorium i Kontraktu Danych (Wszyscy)**
  * Utworzenie struktury katalogów (`data/raw`, `data/processed`, `src/`, `reports/`).
  * Zdefiniowanie i zapisanie na sztywno schematu `mock_snapshots_24h.json` (lista stacji ze stanem rowerów i pozycje vanów co 15 min) oraz `mock_metrics.csv`.
* **[Task 1.2] Pobranie danych i filtracja przestrzenna w Pandas (Osoba 1)**
  * Pobranie 1 pełnego miesiąca sezonu letniego z *Citi Bike System Data* (np. wrzesień) oraz aktualnego pliku `station_information.json` z API GBFS (pojemności stacji).
  * Odfiltrowanie stacji do obszaru **Manhattan poniżej Central Parku** (`lat`: `40.7000` – `40.7650`, `lon`: `-74.0200` – `-73.9600`).
  * Oczyszczenie anomalii: usunięcie przejazdów $< 60\text{ s}$ na tę samą stację oraz $> 4\text{ h}$.
  * Obsługa „węzła zewnętrznego”: oznaczenie przejazdów wjeżdżających spoza obszaru i wyjeżdżających poza obszar.
* **[Task 1.3] Uruchomienie skryptu zbierającego migawki GBFS (Osoba 2)**
  * Napisanie prostego skryptu odpytującego co 5 minut publiczny endpoint `station_status.json` z GBFS i dopisującego stany stacji do pliku lokalnego (potrzebne do wyznaczenia cenzurowania popytu i krzywych zapełnienia).
* **[Task 1.4] Pobranie danych pogodowych (Osoba 1)**
  * Pobranie danych godzinowych (opad mm/h, temperatura) ze stacji Central Park (NOAA / Open-Meteo Archive API) dla wybranego miesiąca i zapisanie do `weather_hourly.csv`.

---

### MILESTONE 2: Kalibracja Matematyczna i MVP Symulatora + Mapy (Czas: 3–4 dni)
**Cel:** Mamy wyestymowane parametry procesów stochastycznych z Tygodni 1–3, działający rdzeń symulacji ze Scenariuszem A (bazowym) oraz pierwszą wersję mapy Manhattanu wyświetlającą rzeczywiste pustoszenie stacji rano.

* **[Task 2.1] Kalibracja NHPP, Macierzy OD i korekta cenzurowania (Osoba 1)**
  * Podział danych na **Tygodnie 1–3 (Kalibracja)** oraz **Tydzień 4 (Walidacja)**.
  * Wyznaczenie intensywności przybyć $\lambda_i(t)$ w oknach 15-minutowych (osobno dla dni roboczych i weekendów).
  * **Kluczowe na 5.0:** Korekta na cenzurowanie popytu — estymacja $\lambda_i(t)$ wyłącznie z okresów, gdy stacja nie była pusta, i ekstrapolacja na czas niedostępności.
  * Wyznaczenie godzinowych macierzy przejść Origin-Destination $P_{ij}(h)$ oraz rozkładów czasu przejazdu (log-normalny / empiryczny).
  * Wyliczenie mnożników pogodowych (spadek popytu podczas deszczu).
* **[Task 2.2] Silnik DES (`heapq`) i Scenariusz A – Bazowy (Osoba 2)**
  * Implementacja generatora zdarzeń metodą przerzedzania (*thinning*, Lewis–Shedler) dla niejednorodnego procesu Poissona.
  * Implementacja zdarzeń: `CLIENT_ARRIVAL`, `BIKE_RENTAL`, `FAILED_RENTAL` (z prawdopodobieństwem $p$ spacer na sąsiednią stację w promieniu 300–500 m lub rezygnacja), `BIKE_RETURN`, `FAILED_RETURN` (przekierowanie do najbliższego wolnego doku).
  * Zaimplementowanie **Scenariusza A**: brak furgonetek w dzień, nocny reset stacji do poziomu docelowego (np. 50% pojemności).
* **[Task 2.3] Dashboard MVP w Streamlit + PyDeck (Osoba 3)**
  * Zbudowanie widoku ciemnej mapy Manhattanu (`mapbox/dark-v10`) z kolumnami/punktami stacji.
  * Kolorowanie dynamiczne: Zielony (norma), Czerwony ($<10\%$ rowerów – stacja pusta), Niebieski/Fioletowy ($>90\%$ – brak wolnych doków).
  * Dodanie suwaka czasu (06:00 – 22:00) i wykresu narastających nieobsłużonych klientów pod mapą.
* **[Task 2.4] Szkielet raportu i Analiza Eksploracyjna Danych – EDA (Osoba 4)**
  * Przygotowanie dokumentu (LaTeX / Markdown) z rozdziałami 1–4.
  * Wygenerowanie z surowych danych mapy cieplnej i wykresów dobowej asymetrii przepływów (wypływ rano z Upper West/East Side, napływ do Midtown i Financial District).

---

### MILESTONE 3: Walidacja Modelu i Heurystyki Relokacji B oraz C (Czas: 4–5 dni)
**Cel:** Udowadniamy matematycznie, że nasz symulator działa jak prawdziwy Nowy Jork (walidacja na Tygodniu 4) oraz wdrażamy inteligencję furgonetek (Scenariusz Reaktywny i Predykcyjny).

* **[Task 3.1] Walidacja dwuetapowa na Tygodniu 4 – Hipoteza H0 (Osoba 4 + Osoba 1)**
  * Uruchomienie symulatora na Tygodniu 4 bez zmiany parametrów.
  * **Etap 1 (Walidacja popytu):** Porównanie symulowanej i rzeczywistej liczby wypożyczeń/zwrotów (policzenie MAE, RMSE, korelacji profili dobowych oraz testu Kołmogorowa–Smirnowa dla czasów jazdy).
  * **Etap 2 (Walidacja krzywych zapełnienia):** Odtworzenie ukrytej relokacji rzeczywistej z różnicy GBFS a bilansem CSV, wstrzyknięcie jej do modelu i porównanie krzywych zapełnienia oraz czasu niedostępności stacji.
* **[Task 3.2] Model Agenta Furgonetki i Scenariusz B – Reaktywny (Osoba 2)**
  * Klasa `RelocationVan`: pojemność $K=25$ rowerów, prędkość zależna od pory dnia (korki w szczycie), dystans w metryce Manhattan, czas obsługi stacji (stały + per rower), godziny pracy (np. 06:00–22:00).
  * Logika **Scenariusza B**: alarm przy zapełnieniu $>90\%$ lub $<10\%$; dyspozytor wysyła najbliższą wolną furgonetkę w parze „odbiór z pełnej $\to$ dostawa do pustej” (priorytet według bieżącego $\lambda$).
* **[Task 3.3] Scenariusz C – Predykcyjny / Dynamiczny (Osoba 2 + Osoba 1)**
  * Co 15–30 minut dyspozytor liczy prognozę bilansu każdej stacji w horyzoncie $H$ (1–2h) na podstawie skalibrowanych $\lambda_{\text{out}}(t)$ oraz spodziewanych zwrotów $\lambda_{\text{in}}(t)$.
  * Wyprzedzające wysyłanie furgonetek przed szczytem (np. o 07:15 zwalnianie doków w Midtown i dowożenie rowerów do dzielnic mieszkalnych).
  * Zachłanna heurystyka trasowania *Pickup and Delivery*.
* **[Task 3.4] Rozbudowa Wizualizacji do trybu „Split-Screen” i animacji Vanów (Osoba 3)**
  * Zestawienie dwóch map obok siebie: lewa pokazuje Scenariusz A lub B, prawa Scenariusz C w tej samej sekundzie czasu symulacji.
  * Dodanie warstwy przemieszczających się ikon/łuków furgonetek (`ArcLayer` / `ScatterplotLayer`) pokazujących, skąd dokąd aktualnie jadą rowery.

---

### MILESTONE 4: Wielkie Eksperymenty, Weryfikacja Hipotez H1–H6 i Finał (Czas: 3–4 dni)
**Cel:** Przeprowadzamy 30 replikacji ze wspólnymi liczbami losowymi (*Common Random Numbers*), potwierdzamy statystycznie wszystkie hipotezy i zamykamy raport z gotową rekomendacją biznesową.

* **[Task 4.1] Batch Runner z Common Random Numbers – CRN (Osoba 2 + Osoba 4)**
  * Ustawienie deterministycznych ziaren generatora (`seed = 1..30`), aby w każdej replikacji Scenariusze A, B i C dostawały **identyczny strumień klientów**.
  * Odrzucenie okresu rozbiegu (*warm-up period* — np. pierwsza doba symulacji).
  * Przeliczenie macierzy eksperymentów:
    1. Porównanie A vs B vs C przy stałej flocie.
    2. Badanie wielkości floty: $N \in \{1, 2, 4, 6, 8\}$ furgonetek.
    3. Analiza wrażliwości: progi $80/20$ vs $90/10$, horyzont prognozy $H$, pojemność vana $K$, dzień słoneczny vs deszczowy.
* **[Task 4.2] Testy statystyczne i generowanie wykresów do raportu (Osoba 4)**
  * Wyliczenie 95% przedziałów ufności dla wszystkich KPI (utracone wypożyczenia, nieudane zwroty, stacjo-minuty pustki/przepełnienia, km furgonetek, uniknięci utraceni klienci na 1 km vana).
  * Przeprowadzenie sparowanych testów t-Studenta / Wilcoxona dla hipotez **H1–H6**.
  * Wygenerowanie kluczowych figur wektorowych (PDF/PNG):
    * Dobowy profil nieobsłużonych klientów (A vs B vs C),
    * Krzywa Pareto: „Liczba furgonetek a jakość obsługi” (pokazująca punkt nasycenia – H4 oraz przewagę mniejszej floty predykcyjnej – H5).
* **[Task 4.3] Szlif wizualizacji i nagranie gifa/wideo awaryjnego (Osoba 3)**
  * Dodanie kart podsumowujących KPI na górze dashboardu Streamlit.
  * Nagranie 60-sekundowego wideo z działania dashboardu (zabezpieczenie na wypadek problemów z projektorem na obronie).
* **[Task 4.4] Redakcja wniosków końcowych i rekomendacji (Wszyscy)**
  * Sformułowanie konkretnej odpowiedzi inżynierskiej: *„Optymalna konfiguracja dla dolnego Manhattanu to $X$ furgonetek sterowanych strategią predykcyjną z horyzontem $H$, co redukuje nieobsłużony popyt o $Y\%$ przy koszcie $Z$ km dziennie”*.

---

## 4. Checklista Wykresów i Tabel (Co musi znaleźć się w Raporcie na 5.0)

1. **Mapa asymetrii popytu (EDA):** Stacje świecące rano na czerwono (wypływ) vs stacje świecące po południu (napływ).
2. **Wykresy Walidacji (H0):** Nałożone na siebie krzywe z Tygodnia 4 (Rzeczywistość vs Symulacja) + tabela błędów MAE/RMSE i p-value z testu K-S.
3. **Dobowy wykres porównawczy Scenariuszy A, B, C (H1, H2):** Odsetek odrzuconych klientów w funkcji godziny doby z widocznym spłaszczeniem szczytów przez strategię predykcyjną (z 95% wstęgą ufności).
4. **Krzywa nasycenia floty (H4, H5):** Oś X: liczba furgonetek ($1, 2, 4, 6, 8$), Oś Y: % obsłużonego popytu (dwie osobne krzywe dla strategii B i C, pokazujące że np. 4 vany w strategii C dają lepszy wynik niż 6 vanów w strategii B).
5. **Wykres słupkowy efektywności kosztowej (H3):** Liczba uratowanych wypożyczeń/zwrotów przypadająca na 1 przejechany kilometr furgonetki.
6. **Tabela weryfikacji hipotez H0–H6:** Kolumny: *Hipoteza | Metryka | Wynik A | Wynik B | Wynik C | p-value (test sparowany) | Status (Potwierdzona/Odrzucona)*.
