USE ROLE ACCOUNTADMIN;
USE DATABASE FLEETPULSE;
USE SCHEMA RAW;

SELECT SYSTEM$PIPE_STATUS('TELEMETRY_PIPE');   -- expect executionState RUNNING

SELECT COUNT(*) AS rows_loaded, COUNT(DISTINCT source_file) AS files FROM TELEMETRY_RAW;

SELECT record:robot_id::STRING AS robot_id,
       record:ts::TIMESTAMP_TZ  AS ts,
       record:battery_pct::FLOAT AS battery_pct,
       record:status::STRING     AS status,
       loaded_at
FROM TELEMETRY_RAW
ORDER BY loaded_at DESC
LIMIT 10;

-- Pipe load history (last hour)
SELECT * FROM TABLE(INFORMATION_SCHEMA.COPY_HISTORY(
  TABLE_NAME => 'TELEMETRY_RAW', START_TIME => DATEADD(hour, -1, CURRENT_TIMESTAMP())));
