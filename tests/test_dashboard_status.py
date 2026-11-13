import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import collect, status  # noqa: E402

NOW = 1_800_000_000.0


def fh(json_in=0, pq_in=0, deliv=0, st="ACTIVE"):
    return {"fleetpulse-telemetry": {"status": st, "incoming_5m": json_in, "delivered_10m": deliv},
            "fleetpulse-telemetry-parquet": {"status": st, "incoming_5m": pq_in, "delivered_10m": 0}}


def aws(**kw):
    base = {"firehose": fh(), "bucket": True, "lambda": True, "rule": "DISABLED", "ec2": None, "error": None}
    return {**base, **kw}


def sf(**kw):
    base = {"warehouse": "SUSPENDED", "pipes": {"TELEMETRY_PIPE": {"state": "RUNNING", "pending": 0, "last_ingested": None}},
            "tables": {"STG_TELEMETRY": {"rows": 100, "state": "RUNNING", "data_ts": None}}, "raw_rows": 100, "error": None}
    return {**base, **kw}


def get(items, name):
    return next(i for i in items if i.name == name)


def test_everything_idle_is_reported_as_idle_not_ok():
    items = status.evaluate({"ts": NOW, "sim": {"running": False, "total": None}, "aws": aws(), "sf": sf()})
    assert get(items, "Robots are producing data").level == status.IDLE
    assert get(items, "Data is reaching AWS").level == status.IDLE
    assert get(items, "Snowflake is processing data").level == status.IDLE
    assert {i.group for i in items} == {status.FLOW, status.AWS, status.SNOW}  # all three groups present...
    assert len(items) >= 12                                          # ...with many separate lines, not one verdict


def test_simulator_rate_comes_from_the_previous_poll():
    prev = {"ts": NOW - 10, "sim": {"running": True, "total": 1000}}
    items = status.evaluate({"ts": NOW, "sim": {"running": True, "total": 3000}, "aws": aws(), "sf": sf()}, prev)
    g = get(items, "Robots are producing data")
    assert g.level == status.OK and "200 readings per second" in g.detail


def test_firehose_transmitting_and_foreign_producer_detected():
    items = status.evaluate({"ts": NOW, "sim": {"running": False, "total": None}, "aws": aws(firehose=fh(json_in=1200, pq_in=300, deliv=900)), "sf": sf()})
    t = get(items, "Data is reaching AWS")
    assert t.level == status.OK and "1,500" in t.detail and "JSON 1,200" in t.detail and "Parquet 300" in t.detail
    assert get(items, "Robots are producing data").level == status.OK and "another source" in get(items, "Robots are producing data").detail


def test_inactive_stream_is_bad():
    items = status.evaluate({"ts": NOW, "sim": None, "aws": aws(firehose=fh(st="DELETING")), "sf": sf()})
    assert get(items, "Data is reaching AWS").level == status.BAD
    assert get(items, "Delivery stream (JSON format)").level == status.BAD


def test_snowflake_processing_signals():
    pipes = {"TELEMETRY_PIPE": {"state": "RUNNING", "pending": 3, "last_ingested": None}}
    assert get(status.evaluate({"ts": NOW, "aws": aws(), "sf": sf(pipes=pipes)}), "Snowflake is processing data").level == status.OK
    prev = {"ts": NOW - 15, "sf": sf(raw_rows=100)}
    cur = sf(raw_rows=5100, tables={"STG_TELEMETRY": {"rows": 5100, "state": "RUNNING", "data_ts": None}})
    p = get(status.evaluate({"ts": NOW, "aws": aws(), "sf": cur}, prev), "Snowflake is processing data")
    assert p.level == status.OK and "+5,000 new rows arrived" in p.detail and "rows prepared in STG_TELEMETRY" in p.detail


def test_suspended_dynamic_tables_warn_and_idle_shows_last_load_age():
    susp = sf(tables={"STG_TELEMETRY": {"rows": 1, "state": "SUSPENDED", "data_ts": None}})
    assert get(status.evaluate({"ts": NOW, "aws": aws(), "sf": susp}), "Snowflake is processing data").level == status.WARN
    last = datetime.fromtimestamp(NOW - 600, tz=timezone.utc)
    idle = sf(pipes={"P": {"state": "RUNNING", "pending": 0, "last_ingested": last}})
    d = get(status.evaluate({"ts": NOW, "aws": aws(), "sf": idle}), "Snowflake is processing data")
    assert d.level == status.IDLE and "10 minutes ago" in d.detail


def test_not_connected_is_unknown_never_green():
    items = status.evaluate({"ts": NOW, "sim": None, "aws": None, "sf": None})
    assert {get(items, n).level for n in ("Data is reaching AWS", "Snowflake is processing data")} == {status.UNKNOWN}
    assert get(items, "Connection to AWS").level == status.UNKNOWN and get(items, "Connection to Snowflake").level == status.UNKNOWN


def test_component_lines_for_aws_and_snowflake():
    items = status.evaluate({"ts": NOW, "aws": aws(rule="ENABLED", ec2={"state": "running", "id": "i-1"}, lam=None), "sf": sf(warehouse="STARTED")})
    assert get(items, "Alert timer (EventBridge)").level == status.OK
    assert get(items, "Cloud simulator computer (EC2)").level == status.OK
    assert get(items, "Compute (warehouse)").detail == "running"
    assert get(items, "Data intake (Snowpipe TELEMETRY_PIPE)").level == status.OK


def test_markdown_uses_streamlit_color_syntax():
    assert status.Item("g", "Name", status.BAD, "oops").markdown() == ":red[●] **Name**: oops"




def test_flow_lines_start_with_a_plain_yes_or_no():
    for snap in ({"ts": NOW, "sim": None, "aws": None, "sf": None},
                 {"ts": NOW, "sim": None, "aws": aws(), "sf": sf()},
                 {"ts": NOW, "sim": {"running": True, "total": 5}, "aws": aws(firehose=fh(json_in=9)), "sf": sf()}):
        for i in status.evaluate(snap):
            if i.group == status.FLOW:
                assert i.detail.split(":")[0] in ("Yes", "No", "Unknown", "Problem", "Paused"), i.detail


# ---------------- collectors ----------------
class Row:
    def __init__(self, d):
        self.d = d

    def as_dict(self):
        return self.d

    def __getitem__(self, i):
        return list(self.d.values())[i]


class FakeSession:
    def __init__(self, fail_on=None):
        self.queries, self.fail_on = [], fail_on

    def sql(self, q):
        self.queries.append(q)
        if self.fail_on and self.fail_on in q:
            raise RuntimeError("Insufficient privileges\nmore")
        if q.startswith("SHOW WAREHOUSES"):
            res = [Row({"name": "FLEETPULSE_WH", "state": "SUSPENDED"})]
        elif q.startswith("SHOW DYNAMIC TABLES"):
            res = [Row({"name": "STG_TELEMETRY", "rows": 42, "scheduling_state": "RUNNING", "data_timestamp": None})]
        elif q.startswith("SHOW TABLES"):
            res = [Row({"name": "TELEMETRY_RAW", "rows": 99})]
        elif q.startswith("SHOW PIPES"):
            res = [Row({"name": "TELEMETRY_PIPE", "schema_name": "RAW"})]
        else:
            res = [Row({"s": json.dumps({"executionState": "RUNNING", "pendingFileCount": 2, "lastIngestedTimestamp": "2026-11-03T05:10:15.123Z"})})]
        return SimpleNamespace(collect=lambda: res)


def test_collect_snowflake_uses_only_metadata_queries():
    s = FakeSession()
    out = collect.collect_snowflake(s)
    assert out["warehouse"] == "SUSPENDED" and out["raw_rows"] == 99 and out["tables"]["STG_TELEMETRY"]["rows"] == 42
    assert out["pipes"]["TELEMETRY_PIPE"]["pending"] == 2 and out["pipes"]["TELEMETRY_PIPE"]["last_ingested"].year == 2026
    assert all(q.startswith(("SHOW", "SELECT SYSTEM$PIPE_STATUS")) for q in s.queries)     # nothing that needs warehouse compute
    assert not any("COUNT(" in q.upper() for q in s.queries)


def test_collect_snowflake_reports_errors_instead_of_raising():
    out = collect.collect_snowflake(FakeSession(fail_on="SHOW DYNAMIC"))
    assert out["error"] and "Insufficient privileges" in out["error"]


def test_collect_aws_degrades_per_component():
    def boom(**kw):
        raise RuntimeError("denied")

    c = SimpleNamespace(
        firehose=SimpleNamespace(describe_delivery_stream=lambda DeliveryStreamName: {"DeliveryStreamDescription": {"DeliveryStreamStatus": "ACTIVE"}}),
        cw=SimpleNamespace(get_metric_statistics=lambda **kw: {"Datapoints": [{"Sum": 10.0}]}),
        s3=SimpleNamespace(head_bucket=boom), lam=SimpleNamespace(get_function=boom),
        events=SimpleNamespace(exceptions=SimpleNamespace(ResourceNotFoundException=KeyError), describe_rule=lambda Name: {"State": "DISABLED"}),
        ec2=SimpleNamespace(describe_instances=lambda Filters: {"Reservations": []}))
    out = collect.collect_aws(c, "bucket")
    assert out["firehose"]["fleetpulse-telemetry"]["incoming_5m"] == 10 and out["rule"] == "DISABLED"
    assert out["bucket"] is False and out["lambda"] is False and out["error"]


def test_pipe_permission_problem_is_local_and_grey_not_a_red_connection_failure():
    out = collect.collect_snowflake(FakeSession(fail_on="SYSTEM$PIPE_STATUS"))
    assert out["error"] is None and out["pipes"]["TELEMETRY_PIPE"]["state"] == "UNKNOWN"
    items = status.evaluate({"ts": NOW, "aws": aws(), "sf": out})
    assert get(items, "Connection to Snowflake").level == status.OK
    pipe = get(items, "Data intake (Snowpipe TELEMETRY_PIPE)")
    assert pipe.level == status.UNKNOWN and "MONITOR" in pipe.detail
