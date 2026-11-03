-- Service identity and alert log for the EventBridge -> Lambda -> Snowflake -> SNS path.
-- 1) Generate a key pair on your laptop (the private key never goes into Git or Terraform):
--      openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -nocrypt -out alert_key.p8
--      openssl rsa -in alert_key.p8 -pubout -out alert_key.pub
-- 2) Paste the public key body (the lines between BEGIN and END, joined into one line) below, then run as ACCOUNTADMIN.
USE ROLE ACCOUNTADMIN;
USE DATABASE FLEETPULSE;

CREATE TABLE IF NOT EXISTS ML.ALERT_LOG (
  run_id STRING, robot_id STRING, first_alert_ts TIMESTAMP_NTZ, notified_at TIMESTAMP_LTZ, source STRING
);

-- Seed: alerts that already exist are history, not news. Without this the first run would email about all of them.
INSERT INTO ML.ALERT_LOG
SELECT s.run_id, s.robot_id, MIN(s.window_end), CURRENT_TIMESTAMP(), 'seed'
FROM ML.RISK_SCORES s
WHERE s.alert
  AND NOT EXISTS (SELECT 1 FROM ML.ALERT_LOG l WHERE l.run_id = s.run_id AND l.robot_id = s.robot_id)
GROUP BY s.run_id, s.robot_id;

CREATE ROLE IF NOT EXISTS FLEETPULSE_ALERT_ROLE;
GRANT USAGE ON WAREHOUSE FLEETPULSE_WH TO ROLE FLEETPULSE_ALERT_ROLE;
GRANT USAGE ON DATABASE FLEETPULSE     TO ROLE FLEETPULSE_ALERT_ROLE;
GRANT USAGE ON SCHEMA FLEETPULSE.ML    TO ROLE FLEETPULSE_ALERT_ROLE;
GRANT SELECT ON ALL VIEWS    IN SCHEMA FLEETPULSE.ML TO ROLE FLEETPULSE_ALERT_ROLE;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA FLEETPULSE.ML TO ROLE FLEETPULSE_ALERT_ROLE;   -- survives CREATE OR REPLACE VIEW
GRANT SELECT, INSERT ON TABLE FLEETPULSE.ML.ALERT_LOG TO ROLE FLEETPULSE_ALERT_ROLE;

CREATE USER IF NOT EXISTS FLEETPULSE_ALERT_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = FLEETPULSE_ALERT_ROLE
  DEFAULT_WAREHOUSE = FLEETPULSE_WH;
GRANT ROLE FLEETPULSE_ALERT_ROLE TO USER FLEETPULSE_ALERT_SVC;
ALTER USER FLEETPULSE_ALERT_SVC SET RSA_PUBLIC_KEY = '<PASTE PUBLIC KEY BODY HERE>';

-- Check: how many robots are already logged
SELECT source, COUNT(*) FROM ML.ALERT_LOG GROUP BY source;
