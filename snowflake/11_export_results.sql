-- Run each query and use "Download results" (CSV) in Snowsight, before tearing the account down.
USE ROLE FLEETPULSE_DEV;
USE DATABASE FLEETPULSE;
USE WAREHOUSE FLEETPULSE_WH;

-- 1) Data quality
SELECT * FROM MARTS.DQ_SUMMARY;

-- 2) Volume per run
SELECT run_id, COUNT(*) AS records, MIN(event_ts) AS first_ts, MAX(event_ts) AS last_ts
FROM STAGING.STG_TELEMETRY GROUP BY run_id ORDER BY first_ts;

-- 3) Silent robots (mixed-fleet run)
SELECT * FROM MARTS.ROBOT_STATUS WHERE is_silent ORDER BY silent_for_s DESC;

-- 4) Fleet health over time for the mixed-fleet run (reporting_robots drops as robots go silent)
SELECT * FROM MARTS.FLEET_MINUTELY
WHERE run_id = (SELECT run_id FROM MARTS.FLEET_MINUTELY GROUP BY run_id ORDER BY COUNT(*) ASC LIMIT 1)
ORDER BY minute_ts;

-- 5) Scoring coverage
SELECT COUNT(*) AS scored_rows, SUM(alert::INT) AS alert_windows,
       COUNT(DISTINCT CASE WHEN alert THEN run_id || robot_id END) AS robots_ever_alerted,
       COUNT(DISTINCT run_id || robot_id) AS robots_scored
FROM ML.RISK_SCORES;

-- 6) Warning time before each robot's overtemp trip
WITH t AS (
  SELECT run_id, robot_id, MIN(first_overtemp_in_window) AS trip_ts
  FROM ML.FEATURES_1MIN GROUP BY run_id, robot_id HAVING MIN(first_overtemp_in_window) IS NOT NULL),
a AS (
  SELECT run_id, robot_id, MIN(window_end) AS first_alert_ts
  FROM ML.RISK_SCORES WHERE alert GROUP BY run_id, robot_id)
SELECT t.run_id, t.robot_id, a.first_alert_ts, t.trip_ts,
       DATEDIFF('minute', a.first_alert_ts, t.trip_ts) AS warning_min
FROM t LEFT JOIN a ON a.run_id = t.run_id AND a.robot_id = t.robot_id
ORDER BY warning_min;

-- 7) The registered model and its evaluation
SELECT model_id, trained_at, threshold, metrics_json FROM ML.MODEL_REGISTRY WHERE is_active;
