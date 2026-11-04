# Dashboard

A Streamlit app that runs on your laptop and talks to AWS and Snowflake with your own credentials.

```bash
pip install -r dashboard/requirements.txt
# once, in Snowflake as ACCOUNTADMIN: snowflake/15_dashboard_grants.sql
export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PASSWORD=...
streamlit run dashboard/app.py
```
Optional: `FLEETPULSE_AWS_PROFILE` (default `fleetpulse`) and `FLEETPULSE_AWS_REGION` (default `us-west-2`). The app starts fine with no cloud resources; panels say "not connected" instead of failing.

## Tabs
- **Data flow demo.** A looping, not-to-scale animation of the whole path: simulator, batch publisher, Firehose (JSON and Parquet conversion), S3, S3 events to SQS, Snowpipe, RAW, dynamic tables, features, model scores, then EventBridge, Lambda, SNS and the inbox. Switch between the JSON, Parquet and both-paths views and change the speed. It needs no cloud.
- **Live pipeline.** Counters at each stop (simulator, Firehose incoming records, S3 files, Snowflake RAW, staged, feature and scored rows), then fleet health, risk per robot, silent robots, data quality and the alert log for a chosen run. It refreshes every 15 seconds. "Live" is near-live: data reaches Snowflake minutes after it is produced.
- **Simulator.** Starts the fleet simulator inside the dashboard process and controls it while it runs: change behavior, scale the fleet, set a robot's wear rate, revive a stopped robot, with a live table of every robot. The simulator keeps running if you close the browser tab; use Stop or Ctrl-C.
- **Infrastructure.** Start or stop the EC2 simulator, enable or disable the alert schedule, pause or resume the Snowflake tables and warehouse, run the alert check now, a read-only setup checklist, and a guarded teardown.

## Safety design
- Runs on localhost only, with your AWS profile; do not expose it on a network.
- Teardown empties the bucket and runs `terraform destroy` only after you type the exact phrase, and shows `terraform plan -destroy` first. The Snowflake drop statements are shown for you to run yourself; the dashboard never drops Snowflake objects.
- Setup is not automated: it includes the Snowflake to AWS handshake (see `runbook.md`). The checklist shows what exists.

## Testing status
The logic layer (runner, AWS helpers, queries, teardown gating) is unit tested with fakes, and the app is exercised headless, including starting the simulator. It has not been run against real AWS or Snowflake from the build environment, so expect to fix small issues on first use.
