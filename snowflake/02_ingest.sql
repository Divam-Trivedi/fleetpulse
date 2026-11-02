-- Run after `terraform apply` #2 (trust policy updated).
USE ROLE ACCOUNTADMIN;
USE DATABASE FLEETPULSE;
USE SCHEMA RAW;

CREATE FILE FORMAT IF NOT EXISTS TELEMETRY_JSON TYPE = JSON COMPRESSION = GZIP;

CREATE STAGE IF NOT EXISTS TELEMETRY_STAGE
  URL = 's3://fleetpulse-raw-859934688085-us-west-2/raw/telemetry/'
  STORAGE_INTEGRATION = FLEETPULSE_S3_INT
  FILE_FORMAT = TELEMETRY_JSON;

-- Connectivity test: errors here mean the trust policy is wrong; empty result means no files yet.
LIST @TELEMETRY_STAGE;

-- Raw landing table: schema-flexible VARIANT plus lineage columns. Typing happens downstream.
CREATE TABLE IF NOT EXISTS TELEMETRY_RAW (
  record          VARIANT,
  source_file     STRING,
  file_row_number NUMBER,
  loaded_at       TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE PIPE IF NOT EXISTS TELEMETRY_PIPE AUTO_INGEST = TRUE AS
  COPY INTO TELEMETRY_RAW (record, source_file, file_row_number)
  FROM (SELECT $1, METADATA$FILENAME, METADATA$FILE_ROW_NUMBER FROM @TELEMETRY_STAGE)
  FILE_FORMAT = (FORMAT_NAME = 'TELEMETRY_JSON');

-- Copy `notification_channel` (an arn:aws:sqs:... value) into terraform.tfvars as snowpipe_sqs_arn
SHOW PIPES;
