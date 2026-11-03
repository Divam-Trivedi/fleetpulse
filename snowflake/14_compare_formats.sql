-- JSON.gz vs Parquet for the same kind of data (10-day window covers your earlier JSON runs).
USE ROLE ACCOUNTADMIN;
USE DATABASE FLEETPULSE;

-- Bytes per record as loaded by Snowpipe (file names start with the delivery stream name)
SELECT CASE WHEN file_name LIKE '%telemetry-parquet-%' THEN 'parquet' ELSE 'json_gz' END AS format,
       COUNT(*)                                          AS files,
       SUM(row_count)                                    AS records,
       SUM(file_size)                                    AS bytes,
       ROUND(SUM(file_size) / SUM(row_count), 1)         AS bytes_per_record,
       ROUND(AVG(file_size) / 1048576, 1)                AS avg_file_mb
FROM TABLE(FLEETPULSE.INFORMATION_SCHEMA.COPY_HISTORY(
       TABLE_NAME => 'RAW.TELEMETRY_RAW', START_TIME => DATEADD(day, -10, CURRENT_TIMESTAMP())))
GROUP BY 1;

-- Load latency is visible too (time between last file arriving and being loaded)
SELECT CASE WHEN file_name LIKE '%telemetry-parquet-%' THEN 'parquet' ELSE 'json_gz' END AS format,
       MIN(last_load_time) AS first_load, MAX(last_load_time) AS last_load
FROM TABLE(FLEETPULSE.INFORMATION_SCHEMA.COPY_HISTORY(
       TABLE_NAME => 'RAW.TELEMETRY_RAW', START_TIME => DATEADD(day, -10, CURRENT_TIMESTAMP())))
GROUP BY 1;
