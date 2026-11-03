# Design tradeoffs and lessons

## Ingestion

**Firehose with a 60-second buffer, not Snowpipe Streaming.**
Firehose is managed, cheap and works with plain S3 + Snowpipe, but adds about a minute of buffering; Snowpipe (auto-ingest) adds more. Snowpipe Streaming or Kinesis Data Streams would cut that to seconds at the cost of more moving parts. The right choice depends on whether anyone acts on data within minutes; for fleet analytics and maintenance risk, minutes are fine.

**One Firehose record per reading is wasteful.**
Telemetry readings are about 290 bytes, and Firehose rounds each record up to a billing minimum (verify the current figure on the pricing page; it was 5 KB when written). Packing many readings into one record would cut ingestion cost many times over. I kept one reading per record for simplicity and noted it as the first cost optimisation.

**Newline-delimited JSON (GZIP) first, Parquet second, and what the measurement said.**
JSON loads straight into a VARIANT column, so new telemetry fields never break ingestion. Firehose concatenates records without a delimiter, so every record gets a trailing newline; without it Snowflake cannot split the file.
I then added a Parquet path: a second Firehose stream converts JSON to Parquet in flight using a Glue table as the schema, and a second Snowpipe loads those files into the same raw table. Measured from Snowpipe's load history:

| Format | Files | Bytes per record | Average file |
|---|---|---|---|
| JSON.gz | 64 | 27.0 | 0.5 MB |
| Parquet | 6 | 32.8 | 1.2 MB |

Parquet was about 21% **larger**. Likely reasons: Firehose writes Snappy-compressed Parquet (weaker than gzip), the float columns are 8-byte doubles whereas the JSON values are rounded to three decimals and compress well as text, and the timestamp is a string. It is not a controlled benchmark (different runs, 6 Parquet files). The deeper point is that the columnar benefit is only realised if the consumer exploits it: I load Parquet into a VARIANT column to keep downstream models unchanged, so Snowflake sees no gain. To benefit, load into typed columns (`MATCH_BY_COLUMN_NAME`), use a timestamp type, and try GZIP or ZSTD compression. Format conversion also forces a larger Firehose buffer (64 MB minimum at the time of writing), which trades latency for file size.

**Non-blocking sink.**
My first sink made every robot wait for each network call. With slow calls (0.8 s) the simulator produced 3,070 records where fast calls produced 11,400, because simulated time kept running while robots waited. The fix: `write()` only appends to a buffer, a background task ships batches with up to 4 calls in flight, and an overflow policy drops and counts records rather than stalling producers. Result: output stayed at about 11,450 regardless of latency, and `sent == published`.
Consequence: with concurrent batches, **file order is not event order**. Everything downstream keys on `(run_id, robot_id, seq)` and event time.

**Snowpipe and small files.**
A 60-second buffer produces many small files, each with per-file overhead in Snowpipe. At this volume it is negligible; at scale, raise the buffer size and interval.

## Snowflake modelling

- **VARIANT landing + typed staging.** The raw table is append-only with lineage columns. Typing and deduplication happen in a dynamic table, so a schema or logic change is a re-derive, not a reload. This also let the Parquet path join the same pipeline with no downstream changes.
- **Dynamic tables, with lag as the cost knob.** Declarative, no task orchestration. Lag was 1 minute while developing and 5 minutes afterwards. `CREATE OR REPLACE` drops grants, so access uses future grants.
- **Event time, not wall-clock time.** At simulation speed-ups, `ts` runs ahead of load time, so `loaded_at - ts` is not a latency measure. Silence is measured against the latest event time in each run.
- **Data-quality view.** Sequence numbers count published records per robot, so any gap means loss. It caught in-flight files mid-ingest (a gap of exactly one 500-record batch) and returned to zero once loading finished.
- **Scoring in SQL.** Model coefficients live in a registry table and a view computes the logistic score, so no Python runtime is needed at query time. It matched scikit-learn to 1e-15 on 15,936 rows.

## Alerting (EventBridge -> Lambda -> Snowflake -> SNS)

**Why this design.** A Snowflake-native alert publishing to SNS would be simpler. I chose the Lambda path to use EventBridge, IAM, Secrets Manager and a packaged dependency, and to keep notification logic in testable Python.
- **Auth:** a Snowflake service user with key-pair authentication; the private key lives in Secrets Manager, uploaded out-of-band so it never enters Terraform state or Git.
- **Delivery semantics:** at-least-once. The Lambda publishes first and records the robots second, so a failure between the two causes a duplicate email, never a missed alert. A unit test pins this behavior.
- **Idempotency:** `ML.ALERT_LOG` holds one row per `(run_id, robot_id)`. The log is seeded with existing alerts at setup, otherwise the first run would email about all history.
- **Batching:** one email per invocation, capped at 50 robots.
- **Cost control:** each run wakes the warehouse, so the schedule is `DISABLED` by default and enabled while demoing.
- **Packaging:** the Snowflake connector with Linux wheels is about 26 MB zipped, inside Lambda's direct-upload limit, and can be built on a Mac with `pip --platform manylinux2014_x86_64`.
- **Latency budget (from configured lags, not measured end to end):** Firehose 60 s + Snowpipe about 1 min + two dynamic tables at 5 min each + a 5-minute schedule is roughly 10 to 15 minutes, which consumes most of the model's 12 to 27 minute warning. Lowering the lags to 1 minute would leave about 5 minutes of delay. This is the main operational limit of the system.
- **A bug the demo found:** the first email reported the peak temperature across a robot's alert windows (84 °C) instead of the temperature when it was flagged (56 to 76 °C), which made it look like an alert on a nearly failed robot. It now uses the value at the first flag.

## EC2 producer

The simulator can run on EC2 instead of a laptop: Amazon Linux 2023 on a t4g.small, an instance role limited to `firehose:PutRecordBatch` on the two telemetry streams (no access keys on the machine), no SSH, no key pair, no inbound rules (Session Manager for access), IMDSv2 required, an encrypted root volume, and a user-data script that clones the repo, runs the simulation and stops the instance. Changing the run arguments replaces the instance and re-runs. A 2-hour simulated run delivered 209,742 records this way.
What a production version would change: a private subnet with VPC endpoints instead of a public IP, an AMI or container image instead of cloning at boot, a service manager and CloudWatch logs instead of a log file, and an Auto Scaling or container service for restarts.

## Security and cost controls

Storage integration (no AWS keys in Snowflake), a Snowflake trust policy with an external ID, least-privilege policies for the producer, EC2 role and Lambda, dedicated Snowflake roles (`FLEETPULSE_DEV`, `FLEETPULSE_ALERT_ROLE`), public access blocked on the bucket, a 14-day lifecycle rule, a $10 AWS budget alert and a 5-credit Snowflake resource monitor. Credentials come from environment variables or Secrets Manager, never code.

## Evaluation lessons (the ML phase)

The headline result changed several times, which is the point:

1. **Identical robots made the problem trivial.** A fixed temperature threshold matched the model. Adding hot zones and different loads made absolute temperature unreliable and per-robot baselines meaningful.
2. **Split by run, not by row.** Every score comes from a model that never saw that run.
3. **Right-censoring.** For a healthy robot, the last 15 minutes of a run have unobserved outcomes. Labelling them "safe" taught the model that near-failure patterns are harmless, so they are excluded.
4. **Row-based lag is fragile.** `LAG(x, 5)` silently stretches across gaps. Deltas now join to the window exactly 5 minutes earlier.
5. **Transients look like degradation.** Two disturbances produced large temperature deltas on healthy robots and drove false alarms: run-start warm-up (13 false-alarm robots in one run) and cool-down plus re-warm after a charging stop (8 robots flagged at run end). A linear model extrapolates far outside its training range. Fix: state-aware scoring, with no scoring in the first 15 minutes or within 15 minutes of a charging stop. False alarms returned to 2 of 153.
6. **Evaluation can hide problems.** The right-censoring exclusion removed exactly the windows where the charging problem occurred, so the evaluation looked clean while live scoring was wrong. Checking the live view, not just the metric, found it.
7. **The model ties a tuned rule.** Reported as such.
