# Design tradeoffs and lessons

## Ingestion

**Firehose with a 60-second buffer, not Snowpipe Streaming.**
Firehose is managed, cheap and works with plain S3 + Snowpipe, but adds about a minute of buffering; Snowpipe (auto-ingest) adds more, so end-to-end latency was roughly 2 to 3 minutes. Snowpipe Streaming or Kinesis Data Streams would cut that to seconds at the cost of more moving parts (a client SDK, or a stream plus consumer). The right choice depends on whether anyone acts on data within minutes; for fleet analytics and maintenance risk, minutes are fine.

**One Firehose record per reading is wasteful.**
Telemetry readings are about 290 bytes, and Firehose rounds each record up to a billing minimum (verify the current figure on the pricing page; it was 5 KB when written). Packing many readings into one record would cut ingestion cost many times over. I kept one reading per record for simplicity and noted it as the first cost optimisation.

**Newline-delimited JSON (GZIP), not Parquet.**
Parquet would need a Glue schema table and would make schema changes harder. JSON loads straight into a VARIANT column, so new telemetry fields never break ingestion. Parquet would shrink storage and load cost at scale. Firehose concatenates records without a delimiter, so every record gets a trailing newline; without it Snowflake cannot split the file.

**Non-blocking sink.**
My first sink made every robot wait for each network call. With slow calls (0.8 s) the simulator produced 3,070 records where fast calls produced 11,400, because simulated time kept running while robots waited. The fix: `write()` only appends to a buffer, a background task ships batches with up to 4 calls in flight, and an overflow policy drops and counts records rather than stalling producers. Result: output stayed at about 11,450 regardless of latency, and `sent == published`.
Consequence: with concurrent batches, **file order is not event order**. Nothing downstream may rely on it; everything keys on `(run_id, robot_id, seq)` and event time.

**Snowpipe and small files.**
A 60-second buffer produces many small files, each with per-file overhead in Snowpipe. At this volume it is negligible; at scale, raise the buffer size and interval.

## Snowflake modelling

- **VARIANT landing + typed staging.** The raw table is append-only with lineage columns (source file, row number, load time). Typing and deduplication happen in a dynamic table, so a schema or logic change is a re-derive, not a reload.
- **Dynamic tables, with lag as the cost knob.** Declarative, no task orchestration. Lag was 1 minute while developing and 5 minutes afterwards. Idle cost is small because refreshes only run when upstream data changes, but they were still suspended at the end of each session. `CREATE OR REPLACE` drops grants, so access uses future grants.
- **Event time, not wall-clock time.** At simulation speed-ups, `ts` runs ahead of load time, so `loaded_at - ts` is not a latency measure. Silence is measured against the latest event time in each run.
- **Data-quality view.** Sequence numbers count published records per robot, so any gap means loss. It caught in-flight files mid-ingest (a gap of exactly one 500-record batch) and returned to zero once loading finished.
- **Scoring in SQL.** Model coefficients live in a registry table and a view computes the logistic score, so no Python runtime is needed at query time. It matched scikit-learn to 1e-15 on 15,936 rows.

## Security and cost controls

Storage integration (no AWS keys in Snowflake), a Snowflake trust policy with an external ID, a least-privilege producer policy, a dedicated `FLEETPULSE_DEV` role, public access blocked on the bucket, a 14-day lifecycle rule, a $10 AWS budget alert and a 5-credit Snowflake resource monitor. Credentials come from environment variables, never code.

## Evaluation lessons (the ML phase)

The headline result changed several times, which is the point:

1. **Identical robots made the problem trivial.** A fixed temperature threshold matched the model. Adding hot zones and different loads made absolute temperature unreliable and per-robot baselines meaningful.
2. **Split by run, not by row.** Every score comes from a model that never saw that run.
3. **Right-censoring.** For a healthy robot, the last 15 minutes of a run have unobserved outcomes. Labelling them "safe" taught the model that near-failure patterns are harmless, so they are excluded.
4. **Row-based lag is fragile.** `LAG(x, 5)` silently stretches across gaps. Deltas now join to the window exactly 5 minutes earlier.
5. **Transients look like degradation.** Two disturbances produced large temperature deltas on healthy robots and drove false alarms: run-start warm-up (13 false-alarm robots in one run) and cool-down plus re-warm after a charging stop (8 robots flagged at run end). A linear model extrapolates far outside its training range. Fix: state-aware scoring, with no scoring in the first 15 minutes or within 15 minutes of a charging stop. False alarms returned to 2 of 153.
6. **Evaluation can hide problems.** The right-censoring exclusion removed exactly the windows where the charging problem occurred, so the evaluation looked clean while live scoring was wrong. Checking the live view, not just the metric, found it.
7. **The model ties a tuned rule.** Reported as such.
