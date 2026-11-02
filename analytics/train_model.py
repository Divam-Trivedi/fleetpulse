"""Train the 15-minute overtemp-risk model from Snowflake data and register it back in Snowflake.

    python -m analytics.train_model [--threshold 0.7]
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone  # timezone: used for the model_id stamp

from .model import FEATURES, LABEL, evaluate, fit_final, insert_sql
from .session import create_session

DDL = f"""
CREATE TABLE IF NOT EXISTS ML.MODEL_REGISTRY (
  model_id STRING, trained_at TIMESTAMP_LTZ, is_active BOOLEAN,
  intercept FLOAT, threshold FLOAT,
  {', '.join(f'{f}_{s} FLOAT' for f in FEATURES for s in ('coef', 'mean', 'std'))},
  metrics_json STRING
)
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.7)
    args = ap.parse_args()

    session = create_session()
    try:
        df = session.table("ML.TRAINING_SET").to_pandas()
        df.columns = [c.lower() for c in df.columns]
        for c in ("window_start", "window_end", "first_overtemp_ts"):
            df[c] = df[c].astype("datetime64[ns]")
        print(f"Loaded {len(df)} rows from {df.run_id.nunique()} runs, {int(df[LABEL].sum())} positive")
        if df.run_id.nunique() < 3:
            raise SystemExit("Need at least 3 runs for a meaningful leave-one-run-out evaluation.")

        metrics = evaluate(df, args.threshold)
        print(json.dumps({k: v for k, v in metrics.items() if k not in ("sweep", "false_alarms")}, indent=2))
        print(f"\n{'method':<18}{'caught':>8}{'median lead':>13}{'false-alarm robots':>20}")
        for r in metrics["sweep"]:
            print(f"{r['method']:<18}{r['detected']:>4}/{r['failing_robots']:<3}{str(r['median_lead_min']):>13}{r['false_alarm_robots']:>12}/{r['healthy_robots']}")

        fa = metrics["false_alarms"]
        print(f"\nHealthy robots flagged at threshold {args.threshold}: {len(fa)}")
        if fa:
            cols = ["run_id", "robot_id", "peak_temp", "peak_risk", "alert_temp", "alert_current_rel", "alert_temp_rise", "min_before_run_end"]
            print("  ".join(f"{c:>17}" for c in cols))
            for r in fa:
                print("  ".join(f"{str(r[c]):>17}" for c in cols))

        params = fit_final(df)
        model_id = datetime.now(timezone.utc).strftime("lr_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:4]
        session.sql(DDL).collect()
        row = {
            "MODEL_ID": model_id, "IS_ACTIVE": False,
            "THRESHOLD": args.threshold, "METRICS_JSON": json.dumps(metrics),
            **{k.upper(): v for k, v in params.items()},
        }
        session.sql(insert_sql("ML.MODEL_REGISTRY", row)).collect()
        # Activate only after the insert succeeded, so a failure never leaves the registry without an active model.
        session.sql(
            "UPDATE ML.MODEL_REGISTRY SET trained_at = COALESCE(trained_at, CURRENT_TIMESTAMP()), "
            f"is_active = (model_id = '{model_id}')"
        ).collect()
        print(f"Registered and activated model {model_id}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
