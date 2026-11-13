import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from dashboard import status  # noqa: E402
from dashboard.poller import Poller  # noqa: E402
from dashboard_fakes import FakeSF, Row, fake_aws  # noqa: E402


def line(result, name):
    return next(i for i in result["items"] if i.name == name)


class GrowingSF(FakeSF):
    """RAW row count grows between polls, like data being loaded."""

    n = 0

    def raw_rows(self):
        self.n += 1000
        return self.n


def make(sf=None, aws=None, aws_factory=None):
    p = Poller(aws_factory=aws_factory or (lambda profile, region: aws))
    p.configure("p", "us-west-2", (lambda: sf) if sf is not None else None, None, 0, False)
    return p


def test_status_is_built_with_aws_and_snowflake_and_detects_growth_between_polls():
    p = make(sf=GrowingSF(), aws=fake_aws(deliveries=50.0, ec2="running"))
    first = p.poll_once()
    assert line(first, "Data is reaching AWS").level == status.OK
    assert line(first, "Cloud simulator computer (EC2)").detail == "running"
    second = p.poll_once()
    assert line(second, "Snowflake is processing data").level == status.OK and "new rows arrived" in line(second, "Snowflake is processing data").detail
    assert list(second["firehose"].columns) == ["JSON", "Parquet"] and len(second["firehose"]) == 1


def test_everything_unknown_when_nothing_is_connected():
    def no_aws(profile, region):
        raise RuntimeError("no credentials")

    r = make(aws_factory=no_aws).poll_once()
    assert line(r, "Data is reaching AWS").level == status.UNKNOWN
    assert line(r, "Snowflake is processing data").level == status.UNKNOWN and len(r["firehose"]) == 0


def test_refresh_data_loads_tables_and_falls_back_to_latest_run():
    p = make(sf=FakeSF(), aws=None)
    assert p.data() == {}
    p.refresh_data("deadbeef")                       # unknown run -> latest
    d = p.data()
    assert d["run_id"] == "abcdef12" and len(d["robots"]) == 6 and len(d["risk"]) == 6 and d["error"] is None
    p.refresh_data("00ff00ff")
    assert p.data()["run_id"] == "00ff00ff"


def test_refresh_data_reports_query_failures_instead_of_raising():
    p = make(sf=FakeSF(fail_on="STG_TELEMETRY GROUP BY"), aws=None)
    p.refresh_data()
    assert "Insufficient privileges" in p.data()["error"]


def test_disconnecting_snowflake_closes_the_session_and_clears_data():
    sf = FakeSF()
    p = make(sf=sf, aws=None)
    p.refresh_data()
    assert p.data()
    p.configure("p", "us-west-2", None, None, 0, False)
    assert sf.closed and p.data() == {}


def test_background_thread_runs_and_stops_when_nobody_is_looking():
    p = make(sf=FakeSF(), aws=fake_aws())
    p.interval, p.IDLE_STOP_S = 0.05, 0.4
    p.touch()
    deadline = time.time() + 5
    while (p.latest() is None or not p.data()) and time.time() < deadline:
        time.sleep(0.05)
    assert p.latest() is not None and p.data()["run_id"] == "abcdef12"     # polled and loaded on its own
    time.sleep(1.2)
    assert not p._thread.is_alive()                                         # stopped itself


def test_a_failing_cycle_does_not_kill_the_thread():
    calls = {"n": 0}

    def flaky(profile, region):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return fake_aws()

    p = make(aws_factory=flaky)
    p._aws_key = None
    p.interval, p.IDLE_STOP_S = 0.05, 1.0
    p.touch()
    time.sleep(0.4)
    assert p.latest() is not None and p._thread.is_alive()
