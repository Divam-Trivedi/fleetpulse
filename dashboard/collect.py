"""Gathers the raw readings that status.evaluate turns into status lines. Everything here is metadata-only
(SHOW commands, SYSTEM$PIPE_STATUS, CloudWatch, describe calls), so polling does not wake the Snowflake warehouse."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from . import infra


def _iso(s: str | None):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def collect_aws(c: SimpleNamespace, bucket: str | None = None) -> dict:
    out: dict = {"firehose": {}, "bucket": None, "lambda": None, "rule": None, "ec2": None, "error": None}

    def guard(fn):
        try:
            return fn()
        except Exception as exc:
            out["error"] = out["error"] or f"{type(exc).__name__}: {str(exc)[:100]}"
            return None

    for stream in infra.STREAMS:
        status = guard(lambda s=stream: c.firehose.describe_delivery_stream(DeliveryStreamName=s)["DeliveryStreamDescription"]["DeliveryStreamStatus"])
        out["firehose"][stream] = {
            "status": status,
            "incoming_5m": guard(lambda s=stream: infra.firehose_metric(c.cw, s, "IncomingRecords", 5)) or 0,
            "delivered_10m": guard(lambda s=stream: infra.firehose_metric(c.cw, s, "DeliveryToS3.Records", 10)) or 0,
        }
    if bucket:
        out["bucket"] = guard(lambda: bool(c.s3.head_bucket(Bucket=bucket))) or False
    out["rule"] = guard(lambda: infra.rule_state(c.events))
    try:
        c.lam.get_function(FunctionName=infra.LAMBDA)
        out["lambda"] = True
    except Exception:
        out["lambda"] = False
    out["ec2"] = guard(lambda: infra.find_instance(c.ec2))
    return out


def collect_snowflake(sf, db: str = "FLEETPULSE") -> dict:
    out: dict = {"warehouse": None, "pipes": {}, "tables": {}, "raw_rows": None, "error": None}

    def rows(sql: str) -> list[dict]:
        return [r.as_dict() for r in sf.sql(sql).collect()]

    try:
        wh = rows(f"SHOW WAREHOUSES LIKE '{db}_WH'")
        out["warehouse"] = wh[0].get("state") if wh else None
        for t in rows(f"SHOW DYNAMIC TABLES IN DATABASE {db}"):
            out["tables"][t["name"]] = {"rows": int(t.get("rows") or 0), "state": t.get("scheduling_state"), "data_ts": t.get("data_timestamp")}
        raw = rows(f"SHOW TABLES LIKE 'TELEMETRY_RAW' IN SCHEMA {db}.RAW")
        out["raw_rows"] = int(raw[0].get("rows") or 0) if raw else None
        for p in rows(f"SHOW PIPES IN DATABASE {db}"):
            name = p["name"]
            try:
                st = json.loads(sf.sql(f"SELECT SYSTEM$PIPE_STATUS('{db}.{p.get('schema_name', 'RAW')}.{name}')").collect()[0][0])
                out["pipes"][name] = {"state": st.get("executionState"), "pending": int(st.get("pendingFileCount") or 0),
                                      "last_ingested": _iso(st.get("lastIngestedTimestamp"))}
            except Exception as exc:       # usually a missing MONITOR grant: affects this pipe only, not the connection
                out["pipes"][name] = {"state": "UNKNOWN", "pending": 0, "last_ingested": None, "error": str(exc).splitlines()[0][:100]}
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:120]}"
    return out


def snapshot(sim_status: dict | None, aws: dict | None, sf: dict | None) -> dict:
    return {
        "ts": datetime.now(timezone.utc).timestamp(),
        "sim": {"running": True, "total": sim_status["total_published"]} if sim_status else {"running": False, "total": None},
        "aws": aws,
        "sf": sf,
    }
