import argparse
import sys
from pathlib import Path

import requests

from common.config import GBFS_STATUS_DIR
from pipeline.gbfs_collector import harvest_once

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fetch one GBFS station_status snapshot into a Parquet file.")
    ap.add_argument("--out", type=Path, default=GBFS_STATUS_DIR)
    args = ap.parse_args()
    try:
        path, n = harvest_once(args.out)
    except (requests.RequestException, OSError, KeyError, ValueError, IndexError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"{n} stations -> {path}")
