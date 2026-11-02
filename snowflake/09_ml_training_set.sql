-- Labelled training rows. Label = robot hits overtemp (error 201) within 15 minutes after the window.
-- Windows from robots that have already tripped are excluded: we only predict from a still-working state.
-- Windows in the last 15 minutes of a run, for robots that never tripped, are also excluded: their outcome
-- was never observed, so labelling them 0 would teach the model that near-failure patterns are safe.
USE ROLE ACCOUNTADMIN;
USE DATABASE FLEETPULSE;
USE WAREHOUSE FLEETPULSE_WH;

CREATE OR REPLACE VIEW ML.TRAINING_SET AS
WITH f AS (
  SELECT *,
         window_start + INTERVAL '1 minute' AS window_end,
         MIN(first_overtemp_in_window) OVER (PARTITION BY run_id, robot_id) AS first_overtemp_ts,
         MAX(window_start) OVER (PARTITION BY run_id) + INTERVAL '1 minute'  AS run_end
  FROM ML.FEATURES_1MIN
)
SELECT run_id, robot_id, window_start, window_end, avg_temp,
       current_rel_5m, current_rel_delta_5m, temp_rise_5m, temp_rise_delta_5m, fault_count_10m,
       first_overtemp_ts,
       CASE WHEN first_overtemp_ts IS NOT NULL
             AND first_overtemp_ts <= window_end + INTERVAL '15 minutes' THEN 1 ELSE 0 END AS will_overtemp_15m
FROM f
WHERE current_rel_delta_5m IS NOT NULL
  AND temp_rise_delta_5m IS NOT NULL
  AND n_records >= 30
  AND charge_share_15m = 0          -- not within 15 minutes of a charging stop
  AND minutes_since_start >= 15   -- skip warm-up and baseline period: the transient looks like fast degradation
  AND (first_overtemp_ts IS NULL OR first_overtemp_ts > window_end)
  AND (first_overtemp_ts IS NOT NULL OR window_end + INTERVAL '15 minutes' <= run_end);

GRANT SELECT ON VIEW ML.TRAINING_SET TO ROLE FLEETPULSE_DEV;
