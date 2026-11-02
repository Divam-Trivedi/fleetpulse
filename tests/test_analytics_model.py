import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")
from analytics.model import FEATURES, LABEL, evaluate, fit_final  # noqa: E402


def synthetic(n_runs=4, robots=6, windows=60, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    base = pd.Timestamp("2026-01-01")
    for r in range(n_runs):
        for k in range(robots):
            failing = k < 2
            trip = 45 if failing else None
            for w in range(windows):
                if failing and w >= trip:
                    break
                ramp = max(0, w - 20) / 25 if failing else 0
                end = base + pd.Timedelta(minutes=w + 1)
                rows.append(dict(
                    run_id=f"r{r}", robot_id=f"a{k}", window_end=end, avg_temp=40 + 30 * ramp + rng.normal(0, 2),
                    current_rel_5m=1 + 0.8 * ramp + rng.normal(0, .02), current_rel_delta_5m=0.1 * ramp,
                    temp_rise_5m=30 * ramp, temp_rise_delta_5m=3 * ramp, fault_count_10m=0,
                    first_overtemp_ts=base + pd.Timedelta(minutes=trip) if failing else pd.NaT,
                    **{LABEL: int(failing and trip - (w + 1) <= 15)}))
    return pd.DataFrame(rows)


def test_evaluate_and_fit():
    df = synthetic()
    m = evaluate(df, 0.7)
    assert m["auc"] > 0.95
    assert isinstance(m["false_alarms"], list)
    assert m["model_at_threshold"]["detected"] == m["model_at_threshold"]["failing_robots"]
    p = fit_final(df)
    assert {"intercept", *(f"{f}_coef" for f in FEATURES)} <= set(p)


def test_insert_sql_is_name_based():
    from analytics.model import insert_sql
    q = insert_sql("ML.T", {"MODEL_ID": "m1", "IS_ACTIVE": False, "THRESHOLD": 0.7, "INTERCEPT": 1e-05, "METRICS_JSON": '{"a": 1}'})
    assert q == "INSERT INTO ML.T (MODEL_ID, IS_ACTIVE, THRESHOLD, INTERCEPT, METRICS_JSON) VALUES ($$m1$$, FALSE, 0.7, 1e-05, $${\"a\": 1}$$)".replace('\\"', '"')
