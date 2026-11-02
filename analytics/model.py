"""Pure-pandas model logic (no Snowflake I/O), so it is unit-testable and reusable."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

FEATURES = [
    "current_rel_5m",
    "current_rel_delta_5m",
    "temp_rise_5m",
    "temp_rise_delta_5m",
    "fault_count_10m",
]
LABEL = "will_overtemp_15m"


def _fit(df: pd.DataFrame):
    scaler = StandardScaler().fit(df[FEATURES])
    clf = LogisticRegression(class_weight="balanced", max_iter=2000)
    clf.fit(scaler.transform(df[FEATURES]), df[LABEL])
    return scaler, clf


def _predict(scaler, clf, df: pd.DataFrame) -> np.ndarray:
    return clf.predict_proba(scaler.transform(df[FEATURES]))[:, 1]


def alert_metrics(d: pd.DataFrame, flag: pd.Series) -> dict:
    """Per-robot view: did we alert before overtemp, how early, and on how many healthy robots."""
    d = d.assign(_flag=np.asarray(flag))
    leads, false_alarms, healthy = [], 0, 0
    for _, g in d.groupby(["run_id", "robot_id"]):
        alerts = g[g["_flag"]].sort_values("window_end")
        trips = g["first_overtemp_ts"].dropna()
        if len(trips):
            leads.append((trips.iloc[0] - alerts["window_end"].iloc[0]).total_seconds() / 60 if len(alerts) else math.nan)
        else:
            healthy += 1
            false_alarms += int(len(alerts) > 0)
    leads = np.array(leads, dtype=float)
    return {
        "failing_robots": int(len(leads)),
        "detected": int(np.sum(~np.isnan(leads))),
        "median_lead_min": None if np.all(np.isnan(leads)) else round(float(np.nanmedian(leads)), 1),
        "min_lead_min": None if np.all(np.isnan(leads)) else round(float(np.nanmin(leads)), 1),
        "healthy_robots": healthy,
        "false_alarm_robots": false_alarms,
    }


def false_alarm_detail(d: pd.DataFrame, p, threshold: float) -> list[dict]:
    """Healthy (never-tripped) robots that were flagged, with enough context to see why."""
    d = d.assign(p=np.asarray(p))
    d["run_end"] = d.groupby("run_id")["window_end"].transform("max")
    rows = []
    for (run, robot), g in d[d["first_overtemp_ts"].isna()].groupby(["run_id", "robot_id"]):
        a = g[g["p"] >= threshold].sort_values("window_end")
        if a.empty:
            continue
        first = a.iloc[0]
        rows.append({
            "run_id": run, "robot_id": robot,
            "peak_temp": round(float(g["avg_temp"].max()), 1),
            "peak_risk": round(float(g["p"].max()), 3),
            "alert_temp": round(float(first["avg_temp"]), 1),
            "alert_current_rel": round(float(first["current_rel_5m"]), 2),
            "alert_temp_rise": round(float(first["temp_rise_5m"]), 1),
            "min_before_run_end": round((first["run_end"] - first["window_end"]).total_seconds() / 60),
        })
    return sorted(rows, key=lambda r: -r["peak_temp"])


def evaluate(df: pd.DataFrame, threshold: float, rule_temp_c: float = 70.0) -> dict:
    """Leave-one-run-out: every run is scored by a model that never saw it."""
    df = df.reset_index(drop=True)
    oof = pd.Series(index=df.index, dtype=float)
    for run in sorted(df["run_id"].unique()):
        train, test = df[df["run_id"] != run], df[df["run_id"] == run]
        scaler, clf = _fit(train)
        oof[test.index] = _predict(scaler, clf, test)
    sweep = [{"method": f"model p>={t}", **alert_metrics(df, oof >= t)} for t in (0.5, 0.7, 0.8, 0.9, 0.95)]
    sweep += [{"method": f"rule temp>={T}", **alert_metrics(df, df["avg_temp"] >= T)} for T in (60, 65, 70, 75, 80)]
    return {
        "false_alarms": false_alarm_detail(df, oof, threshold),
        "sweep": sweep,
        "auc": round(float(roc_auc_score(df[LABEL], oof)), 4),
        "average_precision": round(float(average_precision_score(df[LABEL], oof)), 4),
        "model_at_threshold": {"threshold": threshold, **alert_metrics(df, oof >= threshold)},
        "baseline_rule": {"rule": f"avg_temp >= {rule_temp_c}", **alert_metrics(df, df["avg_temp"] >= rule_temp_c)},
        "rows": int(len(df)),
        "positive_rows": int(df[LABEL].sum()),
        "runs": int(df["run_id"].nunique()),
    }


def fit_final(df: pd.DataFrame) -> dict:
    """Train on everything; return parameters that plain SQL can score with."""
    scaler, clf = _fit(df)
    params = {"intercept": float(clf.intercept_[0])}
    for i, f in enumerate(FEATURES):
        params[f"{f}_coef"] = float(clf.coef_[0][i])
        params[f"{f}_mean"] = float(scaler.mean_[i])
        params[f"{f}_std"] = float(scaler.scale_[i])
    return params


def insert_sql(table: str, row: dict) -> str:
    """Name-based INSERT with literal values (Snowpark's append is positional and breaks on column-order drift)."""
    def lit(v):
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return repr(float(v))
        return "$$" + str(v) + "$$"
    return f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join(lit(v) for v in row.values())})"
