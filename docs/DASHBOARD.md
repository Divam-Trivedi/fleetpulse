# Dashboard

A Streamlit app that runs on your laptop and talks to AWS and Snowflake with your own credentials.

```bash
pip install -r dashboard/requirements.txt
# once, in Snowflake as ACCOUNTADMIN: snowflake/15_dashboard_grants.sql
export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PASSWORD=...
streamlit run dashboard/app.py
```
Optional: `FLEETPULSE_AWS_PROFILE` (default `fleetpulse`), `FLEETPULSE_AWS_REGION` (default `us-west-2`), and `FLEETPULSE_START_PAGE=live` to open on the Live page. The app starts fine with no cloud resources; statuses show "not connected" instead of failing.

## Page 1: Data flow demo
A looping, not-to-scale animation: simulator, batch publisher, Firehose (JSON and Parquet conversion), S3, S3 events to SQS, Snowpipe, RAW, dynamic tables, features, model scores, then EventBridge, Lambda, SNS and the inbox. Switch between the JSON, Parquet and both-paths views and change the speed. It needs no cloud.

## Page 2: Live
**Dashboard tab**, stacked top to bottom:
1. **Fleet:** Robots, Active (sending data), Silent (stopped sending for 30 seconds), Faulted (the latest reading reported a problem), At risk (the model expects an overheating within about 15 minutes).
2. **Is data flowing right now?** Three plain Yes/No lines: *Robots are producing data*, *Data is reaching AWS*, *Snowflake is processing data*.
3. **AWS** and **Snowflake:** one line per part (connection, delivery streams, storage bucket, alert function, alert timer, cloud simulator computer; warehouse, data intake pipes, processing steps).
4. **Readings arriving at AWS** per minute for the last hour.
5. **Controls** (collapsed): start and stop the simulator and change it while it runs, switch cloud parts on or off, run the alert check, and a guarded "remove everything in AWS".

**Tables tab:** four charts, then tables of robots, overheating risk, robots that stopped sending, alerts sent by email, simulation runs and data checks. Each shows the first 5 rows with a "Show all N rows" expander. Headings are in plain words ("Motor temp (°C)", error codes shown as "Overheated", "Wheel slip" and so on).

### Status colors
Green: working. Orange: needs attention. Red: problem. Grey: idle or not connected. Every line also says it in words (Yes, No, Unknown, Problem, Paused), so colour is never the only signal. "Idle" is deliberately not green: the page never reports healthy for something that is not running.

### Where the numbers come from
When the simulator is running inside the dashboard, the Fleet counts and the Robots table come from it directly and update live. Otherwise they come from Snowflake for the selected simulation run.

## How it stays smooth and cheap
- **No fading or blur:** AWS and Snowflake are checked by a background thread (`dashboard/poller.py`). The screen only reads the latest finished answer, which is instant. The thread stops by itself when nobody is looking.
- **Free status checks:** status lines use metadata calls only (`SHOW` commands, the Snowpipe status function, CloudWatch, describe calls), which do not wake the Snowflake warehouse.
- **Robot tables cost credits:** counts, tables and charts run real Snowflake queries, so they refresh when you press "Refresh robot tables now" or on the timer you pick in the sidebar (Off by default). Auto-refresh keeps the warehouse awake, roughly 1 credit per hour on an XS warehouse. If you press Pause under Controls, auto-refresh is held off.

## Editing note
Streamlit prints the value of any bare expression. Never write `st.a() if x else st.b()` as a statement; use `if / else`. A test (`test_app_has_no_bare_expressions...`) fails if one slips in.

## Safety design
- Runs on localhost only, with your AWS profile; do not expose it on a network.
- Teardown empties the bucket and runs `terraform destroy` only after you type the exact phrase, and can show `terraform plan -destroy` first. The Snowflake drop statements are shown for you to run yourself; the dashboard never drops Snowflake objects.
- Setup is not automated: it includes the Snowflake to AWS handshake (see `runbook.md`).

## Testing status
Status logic, collectors (including a check that they issue only metadata queries), the simulator runner, AWS helpers and teardown gating are unit tested with fakes. The app is exercised headless on both pages: with no cloud, with the simulator started from the UI, and with a fake Snowflake session. It has not been run against real AWS or Snowflake from the build environment.
