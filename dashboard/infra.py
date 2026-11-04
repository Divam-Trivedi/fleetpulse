"""AWS and Snowflake control helpers for the dashboard. Every function takes its client as an argument so it can be
tested with fakes. Destructive actions are gated behind a typed confirmation phrase."""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
TF_DIR = ROOT / "infra" / "aws"
STREAMS = ("fleetpulse-telemetry", "fleetpulse-telemetry-parquet")
RULE = "fleetpulse-alert-schedule"
LAMBDA = "fleetpulse-alerts"
DESTROY_PHRASE = "destroy fleetpulse"

# Suspend downstream first, resume upstream first
SNOWFLAKE_PAUSE = [
    "ALTER DYNAMIC TABLE FLEETPULSE.ML.FEATURES_1MIN SUSPEND",
    "ALTER DYNAMIC TABLE FLEETPULSE.MARTS.ROBOT_STATUS SUSPEND",
    "ALTER DYNAMIC TABLE FLEETPULSE.MARTS.FLEET_MINUTELY SUSPEND",
    "ALTER DYNAMIC TABLE FLEETPULSE.STAGING.STG_TELEMETRY SUSPEND",
    "ALTER WAREHOUSE FLEETPULSE_WH SUSPEND",
]
SNOWFLAKE_RESUME = [
    "ALTER WAREHOUSE FLEETPULSE_WH RESUME IF SUSPENDED",
    "ALTER DYNAMIC TABLE FLEETPULSE.STAGING.STG_TELEMETRY RESUME",
    "ALTER DYNAMIC TABLE FLEETPULSE.MARTS.ROBOT_STATUS RESUME",
    "ALTER DYNAMIC TABLE FLEETPULSE.MARTS.FLEET_MINUTELY RESUME",
    "ALTER DYNAMIC TABLE FLEETPULSE.ML.FEATURES_1MIN RESUME",
]
SNOWFLAKE_TEARDOWN_SQL = """-- Run as ACCOUNTADMIN in a Snowsight worksheet (the dashboard never runs these)
DROP USER IF EXISTS FLEETPULSE_ALERT_SVC;
DROP ROLE IF EXISTS FLEETPULSE_ALERT_ROLE;
DROP DATABASE IF EXISTS FLEETPULSE;
DROP WAREHOUSE IF EXISTS FLEETPULSE_WH;
DROP RESOURCE MONITOR IF EXISTS FLEETPULSE_RM;
DROP STORAGE INTEGRATION IF EXISTS FLEETPULSE_S3_INT;
DROP ROLE IF EXISTS FLEETPULSE_DEV;"""


def aws_clients(profile: str | None, region: str) -> SimpleNamespace:
    import boto3

    s = boto3.Session(profile_name=profile, region_name=region)
    return SimpleNamespace(
        ec2=s.client("ec2"), events=s.client("events"), lam=s.client("lambda"), firehose=s.client("firehose"),
        s3=s.client("s3"), cw=s.client("cloudwatch"), sts=s.client("sts"), region=region,
    )


def bucket_name(c: SimpleNamespace) -> str:
    return f"fleetpulse-raw-{c.sts.get_caller_identity()['Account']}-{c.region}"


# ---- EC2 ----
def find_instance(ec2) -> dict | None:
    resp = ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": ["fleetpulse-simulator"]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
    ])
    for r in resp.get("Reservations", []):
        for i in r.get("Instances", []):
            return {"id": i["InstanceId"], "state": i["State"]["Name"], "type": i.get("InstanceType")}
    return None


def ec2_set(ec2, instance_id: str, running: bool) -> None:
    (ec2.start_instances if running else ec2.stop_instances)(InstanceIds=[instance_id])


# ---- EventBridge schedule and alert Lambda ----
def rule_state(events, name: str = RULE) -> str | None:
    try:
        return events.describe_rule(Name=name)["State"]
    except events.exceptions.ResourceNotFoundException:
        return None


def set_rule(events, enabled: bool, name: str = RULE) -> None:
    (events.enable_rule if enabled else events.disable_rule)(Name=name)


def invoke_alert_lambda(lam, name: str = LAMBDA) -> dict:
    import json

    resp = lam.invoke(FunctionName=name)
    payload = json.loads(resp["Payload"].read() or b"{}")
    if resp.get("FunctionError"):
        raise RuntimeError(f"Lambda error: {payload}")
    return payload


# ---- Flow counters ----
def firehose_incoming(cw, stream: str, minutes: int = 15, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    resp = cw.get_metric_statistics(
        Namespace="AWS/Firehose", MetricName="IncomingRecords",
        Dimensions=[{"Name": "DeliveryStreamName", "Value": stream}],
        StartTime=now - timedelta(minutes=minutes), EndTime=now, Period=60, Statistics=["Sum"],
    )
    return int(sum(p["Sum"] for p in resp.get("Datapoints", [])))


def count_s3_objects(s3, bucket: str, prefix: str, max_keys: int = 5000) -> tuple[int, int]:
    n = size = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix, PaginationConfig={"MaxItems": max_keys}):
        for obj in page.get("Contents", []):
            n += 1
            size += obj["Size"]
    return n, size


# ---- Snowflake ----
def run_statements(session, statements: list[str]) -> list[dict]:
    out = []
    for sql in statements:
        try:
            session.sql(sql).collect()
            out.append({"sql": sql, "ok": True, "message": "ok"})
        except Exception as exc:
            out.append({"sql": sql, "ok": False, "message": str(exc).splitlines()[0][:160]})
    return out


def dynamic_table_states(session) -> dict[str, str]:
    rows = session.sql("SHOW DYNAMIC TABLES IN DATABASE FLEETPULSE").collect()
    out = {}
    for r in rows:
        d = r.as_dict()
        out[d.get("name")] = d.get("scheduling_state")
    return out


# ---- Setup checklist (read-only) ----
def setup_checklist(c: SimpleNamespace, bucket: str, snowflake_session=None) -> list[dict]:
    items: list[dict] = []

    def check(name: str, fn: Callable[[], tuple[bool, str]]) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {str(exc)[:90]}"
        items.append({"component": name, "ok": ok, "detail": detail})

    check("S3 bucket", lambda: (bool(c.s3.head_bucket(Bucket=bucket)), bucket))
    for stream in STREAMS:
        check(f"Firehose: {stream}", lambda s=stream: (
            (st := c.firehose.describe_delivery_stream(DeliveryStreamName=s)["DeliveryStreamDescription"]["DeliveryStreamStatus"]) == "ACTIVE", st))
    check("Alert Lambda", lambda: (bool(c.lam.get_function(FunctionName=LAMBDA)), LAMBDA))
    check("EventBridge schedule", lambda: ((s := rule_state(c.events)) is not None, str(s)))
    check("EC2 simulator", lambda: ((i := find_instance(c.ec2)) is not None, "not deployed" if i is None else f"{i['id']} ({i['state']})"))
    if snowflake_session is not None:
        def pipes() -> tuple[bool, str]:
            rows = snowflake_session.sql("SHOW PIPES IN DATABASE FLEETPULSE").collect()
            names = sorted(r.as_dict().get("name") for r in rows)
            return len(names) >= 1, ", ".join(names) or "none"
        check("Snowpipe pipes", pipes)
        check("Dynamic tables", lambda: ((st := dynamic_table_states(snowflake_session)) != {}, ", ".join(f"{k}={v}" for k, v in st.items())))
    return items


# ---- Teardown (destructive, gated) ----
def empty_bucket(s3, bucket: str) -> int:
    deleted = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            deleted += len(keys)
    return deleted


def terraform_plan_destroy(runner: Callable[..., Any] = subprocess.run) -> str:
    r = runner(["terraform", f"-chdir={TF_DIR}", "plan", "-destroy", "-no-color", "-input=false"],
               capture_output=True, text=True, timeout=300)
    return (r.stdout or "") + (r.stderr or "")


def terraform_destroy(confirm: str, s3=None, bucket: str | None = None, runner: Callable[..., Any] = subprocess.run) -> str:
    """Empties the bucket, then runs `terraform destroy`. Refuses unless the exact phrase is typed."""
    if confirm.strip() != DESTROY_PHRASE:
        raise ValueError(f'Type "{DESTROY_PHRASE}" exactly to confirm')
    if s3 is not None and bucket:
        empty_bucket(s3, bucket)
    r = runner(["terraform", f"-chdir={TF_DIR}", "destroy", "-auto-approve", "-no-color", "-input=false"],
               capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError((r.stdout or "")[-1500:] + (r.stderr or "")[-1500:])
    return (r.stdout or "")[-1500:]
