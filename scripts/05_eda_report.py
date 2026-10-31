from __future__ import annotations

import time

from viz.eda import run_eda_pipeline


def main() -> None:
    t0 = time.time()
    summary = run_eda_pipeline()
    dt = time.time() - t0
    print(
        f"[EDA] stations={summary['n_stations']} (cap={summary['total_capacity']:,}) | "
        f"clean_trips={summary['trips_clean_total']:,} (calib={summary['trips_calib_total']:,}, valid={summary['trips_valid_total']:,}) | "
        f"rain_mult={summary['rain_multiplier']:.3f} | "
        f"censoring_uplift=+{summary['censoring_uplift_pct']:.2f}% | "
        f"figures={len(summary['figures'])} in {dt:.2f}s"
    )


if __name__ == "__main__":
    main()
