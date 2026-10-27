# ssd-citibike-simulation

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
