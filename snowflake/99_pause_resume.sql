-- Run when you stop for the day: dynamic tables are the only thing that keeps using compute.
USE ROLE ACCOUNTADMIN;
USE DATABASE FLEETPULSE;
ALTER DYNAMIC TABLE STAGING.STG_TELEMETRY SUSPEND;
ALTER DYNAMIC TABLE MARTS.ROBOT_STATUS    SUSPEND;
ALTER DYNAMIC TABLE MARTS.FLEET_MINUTELY  SUSPEND;
-- To resume: replace SUSPEND with RESUME (upstream first: STG_TELEMETRY, then the marts).
