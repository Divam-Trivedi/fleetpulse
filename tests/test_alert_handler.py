import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambdas"))
import alert_handler  # noqa: E402


class FakeCursor:
    def __init__(self, rows):
        self.rows, self.executed, self.logged, self.closed = rows, [], [], False

    def execute(self, sql):
        self.executed.append(sql)

    def fetchall(self):
        return self.rows

    def executemany(self, sql, params):
        self.logged.extend(params)

    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self, rows):
        self.cur = FakeCursor(rows)

    def cursor(self):
        return self.cur


class FakeSns:
    def __init__(self, fail=False):
        self.published, self.fail = [], fail

    def publish(self, **kw):
        if self.fail:
            raise RuntimeError("sns down")
        self.published.append(kw)


ROWS = [("run1", "amr-004", "2026-11-03 10:00:00", 0.97, 71.2), ("run1", "amr-011", "2026-11-03 10:01:00", 0.81, 66.0)]


def test_no_new_alerts_sends_nothing():
    conn, sns = FakeConn([]), FakeSns()
    assert alert_handler.run(conn, sns, "arn:topic") == {"new_alerts": 0}
    assert sns.published == [] and conn.cur.logged == [] and conn.cur.closed


def test_new_alerts_publish_once_then_log():
    conn, sns = FakeConn(ROWS), FakeSns()
    out = alert_handler.run(conn, sns, "arn:topic")
    assert out["new_alerts"] == 2 and out["robots"] == ["amr-004", "amr-011"]
    assert len(sns.published) == 1                       # one batched message, not one per robot
    msg = sns.published[0]
    assert msg["TopicArn"] == "arn:topic" and "amr-004" in msg["Message"] and "0.97" in msg["Message"]
    assert conn.cur.logged == [("run1", "amr-004", "2026-11-03 10:00:00"), ("run1", "amr-011", "2026-11-03 10:01:00")]


def test_publish_failure_does_not_mark_alerts_as_sent():
    conn, sns = FakeConn(ROWS), FakeSns(fail=True)
    try:
        alert_handler.run(conn, sns, "arn:topic")
        raised = False
    except RuntimeError:
        raised = True
    assert raised and conn.cur.logged == []              # not logged, so the next run retries
