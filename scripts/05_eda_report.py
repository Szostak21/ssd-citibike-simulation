from __future__ import annotations

import time

from viz.eda import run_eda_pipeline
from viz.validation import run_validation_pipeline


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

    t1 = time.time()
    val = run_validation_pipeline()
    dt_v = time.time() - t1
    s1 = val["stage1_demand_and_durations"]
    s2 = val["stage2_occupancy_and_relocation"]
    m_rent = s1["metrics_rentals_dry"]
    m_ret = s1["metrics_returns_dry"]
    m_mid = s2["occupancy_metrics"]["midtown_fill_injected_vs_gbfs"]
    ks = s1["duration_validation"]
    print(
        f"[VALID H0={val['status']}] "
        f"Rent(dry): r={m_rent['pearson_r']:.4f}, MAPE={m_rent['mape_pct']:.2f}%, MAE={m_rent['mae']:.1f}/h | "
        f"Ret(dry): r={m_ret['pearson_r']:.4f}, MAPE={m_ret['mape_pct']:.2f}% | "
        f"KS_D={ks['ks_stat_n500']:.4f} (p={ks['ks_pvalue_n500']:.3f}) | "
        f"Stage2 Midtown r={m_mid['pearson_r']:.4f}, MAE={m_mid['mae']:.2f}pp in {dt_v:.2f}s"
    )


if __name__ == "__main__":
    main()
