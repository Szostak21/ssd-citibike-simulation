# ssd-citibike-simulation

## Zbieranie danych GBFS (GitHub Actions)

Workflow `.github/workflows/gbfs_cron.yml` uruchamia się co 5 minut (oraz ręcznie przez *Run workflow*),
pobiera `station_status.json` dla stacji dolnego Manhattanu i commituje jeden plik Parquet
do osieroconego brancha `data-gbfs`:

```
gbfs_status/YYYY-MM-DD/HHMMSS.parquet   # data i czas wg America/New_York
```

Harmonogram (`schedule`) działa tylko z domyślnego brancha (`main`).

Pobranie zebranych danych lokalnie (do `data/raw/gbfs_status/`):

```bash
uv run python scripts/sync_gbfs.py
uv run python scripts/01_prepare_data.py   # exposure_frac liczony po zebraniu >= 7 dni
```

Pojedynczy odczyt ręcznie: `uv run python scripts/harvest_gbfs.py --out /tmp/gbfs`.
Zapasowy kolektor lokalny (CSV co 5 min): `nohup uv run python scripts/collect_gbfs.py > gbfs.log &`.
