"""Snowflake queries behind the live pipeline view. Functions take a Snowpark session and return pandas frames."""
from __future__ import annotations

import re

import pandas as pd

_RUN_ID = re.compile(r"^[0-9a-f]{6,16}$")


def _run(run_id: str) -> str:
    if not _RUN_ID.match(run_id or ""):
        raise ValueError(f"Unexpected run_id: {run_id!r}")
    return run_id


def _df(session, sql: str) -> pd.DataFrame:
    df = session.sql(sql).to_pandas()
    df.columns = [c.lower() for c in df.columns]
    return df


def runs(session) -> pd.DataFrame:
    return _df(session, "SELECT run_id, COUNT(*) AS records, MIN(event_ts) AS first_ts, MAX(event_ts) AS last_ts "
                        "FROM STAGING.STG_TELEMETRY GROUP BY run_id ORDER BY last_ts DESC")


def stage_counts(session) -> pd.DataFrame:
    return _df(session, "SELECT (SELECT COUNT(*) FROM RAW.TELEMETRY_RAW) AS raw_rows, "
                        "(SELECT COUNT(*) FROM STAGING.STG_TELEMETRY) AS staged_rows, "
                        "(SELECT COUNT(*) FROM ML.FEATURES_1MIN) AS feature_rows, "
                        "(SELECT COUNT(*) FROM ML.RISK_SCORES) AS scored_rows, "
                        "(SELECT MAX(loaded_at) FROM RAW.TELEMETRY_RAW) AS last_raw_load")


def fleet_minutely(session, run_id: str) -> pd.DataFrame:
    return _df(session, "SELECT minute_ts, reporting_robots, avg_battery_pct, avg_motor_temp, max_motor_temp, error_records "
                        f"FROM MARTS.FLEET_MINUTELY WHERE run_id = '{_run(run_id)}' ORDER BY minute_ts")


def silent_robots(session, run_id: str) -> pd.DataFrame:
    return _df(session, "SELECT robot_id, silent_for_s, battery_pct, motor_temp, status FROM MARTS.ROBOT_STATUS "
                        f"WHERE run_id = '{_run(run_id)}' AND is_silent ORDER BY silent_for_s DESC")


def latest_risk(session, run_id: str) -> pd.DataFrame:
    return _df(session, "SELECT robot_id, window_end, ROUND(risk_15m, 3) AS risk_15m, ROUND(avg_temp, 1) AS avg_temp, alert, already_tripped "
                        f"FROM ML.RISK_SCORES WHERE run_id = '{_run(run_id)}' "
                        "QUALIFY ROW_NUMBER() OVER (PARTITION BY robot_id ORDER BY window_end DESC) = 1 ORDER BY risk_15m DESC")


def data_quality(session) -> pd.DataFrame:
    return _df(session, "SELECT * FROM MARTS.DQ_SUMMARY")


def alert_log(session) -> pd.DataFrame:
    return _df(session, "SELECT run_id, robot_id, first_alert_ts, notified_at, source FROM ML.ALERT_LOG "
                        "ORDER BY notified_at DESC LIMIT 25")
