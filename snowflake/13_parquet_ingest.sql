-- Parquet ingestion path. Parquet rows land in the SAME RAW.TELEMETRY_RAW table (as VARIANT objects with
-- the same keys as the JSON records), so STG_TELEMETRY and everything downstream stays unchanged.
USE ROLE ACCOUNTADMIN;
USE DATABASE FLEETPULSE;
USE SCHEMA RAW;

-- Allow the second S3 prefix (this REPLACES the list, so both prefixes are included)
ALTER STORAGE INTEGRATION FLEETPULSE_S3_INT SET STORAGE_ALLOWED_LOCATIONS = (
  's3://fleetpulse-raw-859934688085-us-west-2/raw/telemetry/',
  's3://fleetpulse-raw-859934688085-us-west-2/raw_parquet/telemetry/'
);

CREATE FILE FORMAT IF NOT EXISTS TELEMETRY_PARQUET TYPE = PARQUET;

CREATE STAGE IF NOT EXISTS TELEMETRY_PARQUET_STAGE
  URL = 's3://fleetpulse-raw-859934688085-us-west-2/raw_parquet/telemetry/'
  STORAGE_INTEGRATION = FLEETPULSE_S3_INT
  FILE_FORMAT = TELEMETRY_PARQUET;

LIST @TELEMETRY_PARQUET_STAGE;   -- empty until the first Parquet file lands; an error means IAM/integration

CREATE PIPE IF NOT EXISTS TELEMETRY_PIPE_PQ AUTO_INGEST = TRUE AS
  COPY INTO TELEMETRY_RAW (record, source_file, file_row_number)
  FROM (SELECT $1, METADATA$FILENAME, METADATA$FILE_ROW_NUMBER FROM @TELEMETRY_PARQUET_STAGE)
  FILE_FORMAT = (FORMAT_NAME = 'TELEMETRY_PARQUET');

-- Compare notification_channel with the JSON pipe's. If identical, just set snowpipe_parquet_enabled = true
-- in terraform.tfvars; if different, also set snowpipe_sqs_arn_parquet.
SHOW PIPES;
