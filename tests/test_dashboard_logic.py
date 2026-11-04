import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import data, infra  # noqa: E402
from dashboard.sim_runner import SimRunner, build_config  # noqa: E402


# ---------- SimRunner ----------
def test_sim_runner_start_control_stop():
    cfg = build_config(str(ROOT / "configs/default.yaml"), "all_publishing", 5, 200.0, "None (discard)")
    r = SimRunner()
    r.start(cfg)
    try:
        time.sleep(0.6)
        s = r.status()
        assert r.running and s["fleet_size"] == 5 and s["total_published"] > 0
        r.call("set_behavior", "mixed_fleet", continuous_pct=50, random_pct=30, abrupt_pct=20)
        r.call("scale_fleet", 8)
        r.call("update_params", "amr-001", wear_rate=0.0003)
        s = r.status()
        assert s["behavior"]["name"] == "mixed_fleet" and s["fleet_size"] == 8
        assert {x["id"]: x for x in s["robots"]}["amr-001"]["params"]["wear_rate"] == 0.0003
        with pytest.raises(Exception):
            r.call("set_behavior", "mixed_fleet", continuous_pct=10, random_pct=10, abrupt_pct=10)  # sums to 30
    finally:
        r.stop()
    assert not r.running


def test_sim_runner_surfaces_start_errors():
    cfg = build_config(str(ROOT / "configs/default.yaml"), "all_publishing", 2, 10.0, "Firehose: JSON stream",
                       profile="no-such-profile-xyz")
    r = SimRunner()
    with pytest.raises(RuntimeError):
        r.start(cfg)
    assert not r.running


# ---------- infra helpers ----------
class FakePager:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kw):
        return self.pages


class FakeS3:
    def __init__(self, pages):
        self.pages, self.deleted = pages, []

    def get_paginator(self, _):
        return FakePager(self.pages)

    def delete_objects(self, Bucket, Delete):
        self.deleted += [o["Key"] for o in Delete["Objects"]]


PAGES = [{"Contents": [{"Key": "a", "Size": 10}, {"Key": "b", "Size": 20}]}, {"Contents": [{"Key": "c", "Size": 5}]}]


def test_count_and_empty_bucket():
    assert infra.count_s3_objects(FakeS3(PAGES), "b", "raw/") == (3, 35)
    s3 = FakeS3(PAGES)
    assert infra.empty_bucket(s3, "b") == 3 and s3.deleted == ["a", "b", "c"]


def test_terraform_destroy_requires_exact_phrase():
    calls = []
    runner = lambda *a, **k: calls.append(a) or SimpleNamespace(returncode=0, stdout="Destroy complete", stderr="")  # noqa: E731
    s3 = FakeS3(PAGES)
    for bad in ("", "destroy", "DESTROY FLEETPULSE", "yes"):
        with pytest.raises(ValueError):
            infra.terraform_destroy(bad, s3=s3, bucket="b", runner=runner)
    assert calls == [] and s3.deleted == []                      # nothing ran, nothing deleted
    out = infra.terraform_destroy("destroy fleetpulse", s3=s3, bucket="b", runner=runner)
    assert "Destroy complete" in out and s3.deleted == ["a", "b", "c"] and "destroy" in calls[0][0]


def test_terraform_destroy_failure_is_raised():
    runner = lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="boom")  # noqa: E731
    with pytest.raises(RuntimeError, match="boom"):
        infra.terraform_destroy(infra.DESTROY_PHRASE, runner=runner)


def test_firehose_incoming_sums_datapoints():
    cw = SimpleNamespace(get_metric_statistics=lambda **kw: {"Datapoints": [{"Sum": 100.0}, {"Sum": 250.0}]})
    assert infra.firehose_incoming(cw, "s", now=datetime(2026, 1, 1, tzinfo=timezone.utc)) == 350


def test_rule_and_lambda_helpers():
    class NotFound(Exception):
        pass

    ev = SimpleNamespace(exceptions=SimpleNamespace(ResourceNotFoundException=NotFound),
                         describe_rule=lambda Name: {"State": "DISABLED"}, enable_rule=lambda Name: ev.__dict__.update(last=("on", Name)),
                         disable_rule=lambda Name: ev.__dict__.update(last=("off", Name)))
    assert infra.rule_state(ev) == "DISABLED"
    infra.set_rule(ev, True)
    assert ev.last == ("on", infra.RULE)
    lam = SimpleNamespace(invoke=lambda FunctionName: {"Payload": io.BytesIO(json.dumps({"new_alerts": 5}).encode())})
    assert infra.invoke_alert_lambda(lam) == {"new_alerts": 5}
    bad = SimpleNamespace(invoke=lambda FunctionName: {"FunctionError": "Unhandled", "Payload": io.BytesIO(b'{"errorMessage":"x"}')})
    with pytest.raises(RuntimeError):
        infra.invoke_alert_lambda(bad)


def test_run_statements_reports_failures_without_stopping():
    class Sess:
        def sql(self, q):
            if "BAD" in q:
                raise RuntimeError("insufficient privileges\nmore")
            return SimpleNamespace(collect=lambda: [])

    out = infra.run_statements(Sess(), ["ALTER OK", "ALTER BAD", "ALTER OK2"])
    assert [o["ok"] for o in out] == [True, False, True] and out[1]["message"] == "insufficient privileges"


def test_setup_checklist_marks_missing_pieces():
    def boom(**kw):
        raise RuntimeError("not found")

    c = SimpleNamespace(
        s3=SimpleNamespace(head_bucket=lambda Bucket: {"ok": 1}),
        firehose=SimpleNamespace(describe_delivery_stream=lambda DeliveryStreamName: {"DeliveryStreamDescription": {"DeliveryStreamStatus": "ACTIVE"}}),
        lam=SimpleNamespace(get_function=boom),
        events=SimpleNamespace(exceptions=SimpleNamespace(ResourceNotFoundException=KeyError), describe_rule=lambda Name: {"State": "ENABLED"}),
        ec2=SimpleNamespace(describe_instances=lambda Filters: {"Reservations": []}),
    )
    items = {i["component"]: i for i in infra.setup_checklist(c, "b")}
    assert items["S3 bucket"]["ok"] and items["Firehose: fleetpulse-telemetry"]["ok"]
    assert not items["Alert Lambda"]["ok"] and items["EventBridge schedule"]["detail"] == "ENABLED"
    assert not items["EC2 simulator"]["ok"] and items["EC2 simulator"]["detail"] == "not deployed"


# ---------- queries ----------
def test_run_id_is_validated_against_injection():
    for bad in ("x'; DROP TABLE t;--", "", "ZZZZ", "abc"):
        with pytest.raises(ValueError):
            data.fleet_minutely(None, bad)
