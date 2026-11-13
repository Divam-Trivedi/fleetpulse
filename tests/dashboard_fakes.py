"""Shared fakes for the dashboard tests."""
import json
from types import SimpleNamespace

import pandas as pd


class Row:
    def __init__(self, d):
        self.d = d

    def as_dict(self):
        return self.d

    def __getitem__(self, i):
        return list(self.d.values())[i]


T = pd.Timestamp("2026-11-03 10:00:00")


class FakeSF:
    """Answers the metadata queries the collectors run and the data queries the tables run."""

    closed = False

    def __init__(self, fail_on=None):
        self.queries, self.fail_on = [], fail_on

    def close(self):
        self.closed = True

    def raw_rows(self):
        return 500

    def sql(self, q):
        self.queries.append(q)
        if self.fail_on and self.fail_on in q:
            raise RuntimeError("Insufficient privileges\nmore")
        meta = {
            "SHOW WAREHOUSES": [Row({"name": "FLEETPULSE_WH", "state": "STARTED"})],
            "SHOW DYNAMIC TABLES": [Row({"name": "STG_TELEMETRY", "rows": 500, "scheduling_state": "RUNNING", "data_timestamp": None})],
            "SHOW TABLES": [Row({"name": "TELEMETRY_RAW", "rows": self.raw_rows()})],
            "SHOW PIPES": [Row({"name": "TELEMETRY_PIPE", "schema_name": "RAW"})],
            "SELECT SYSTEM$PIPE_STATUS": [Row({"s": json.dumps({"executionState": "RUNNING", "pendingFileCount": 0, "lastIngestedTimestamp": "2026-11-03T05:10:15.123Z"})})],
        }
        for prefix, rows in meta.items():
            if q.startswith(prefix):
                return SimpleNamespace(collect=lambda r=rows: r)
        return SimpleNamespace(to_pandas=lambda: self._frame(q), collect=lambda: [])

    @staticmethod
    def _frame(q):
        if "GROUP BY run_id ORDER BY last_ts" in q:
            return pd.DataFrame({"RUN_ID": ["abcdef12", "00ff00ff"], "RECORDS": [193000, 8000], "FIRST_TS": [T, T], "LAST_TS": [T, T]})
        if "robot_id, status, is_silent" in q:
            return pd.DataFrame({"ROBOT_ID": [f"amr-{i:03d}" for i in range(6)], "STATUS": ["moving"] * 5 + ["fault"],
                                 "IS_SILENT": [False, False, False, False, True, True], "SILENT_FOR_S": [0, 0, 0, 0, 400, 500],
                                 "BATTERY_PCT": [80.0] * 6, "MOTOR_TEMP": [50, 55, 60, 65, 40, 90.0], "MOTOR_CURRENT": [3.0] * 6,
                                 "ERROR_CODE": [0, 0, 0, 0, 0, 201], "RECORDS": [100] * 6, "LAST_SEEN_TS": [T] * 6})
        if "AND is_silent ORDER BY" in q:
            return pd.DataFrame({"ROBOT_ID": ["amr-005", "amr-004"], "SILENT_FOR_S": [500, 400], "BATTERY_PCT": [80.0, 80.0], "MOTOR_TEMP": [90.0, 40.0], "STATUS": ["fault", "moving"]})
        if "FROM ML.RISK_SCORES" in q:
            return pd.DataFrame({"ROBOT_ID": [f"amr-{i:03d}" for i in range(6)], "WINDOW_END": [T] * 6, "RISK_15M": [0.99, 0.95, 0.4, 0.1, 0.05, 0.01],
                                 "AVG_TEMP": [70.0] * 6, "ALERT": [True, True, False, False, False, False], "ALREADY_TRIPPED": [False] * 6})
        if "MARTS.FLEET_MINUTELY" in q:
            return pd.DataFrame({"MINUTE_TS": [T, T + pd.Timedelta(minutes=1)], "REPORTING_ROBOTS": [6, 4], "AVG_BATTERY_PCT": [80.0, 79.0],
                                 "AVG_MOTOR_TEMP": [50.0, 52.0], "MAX_MOTOR_TEMP": [60.0, 90.0], "ERROR_RECORDS": [0, 3]})
        if "DQ_SUMMARY" in q:
            return pd.DataFrame({"CHECK_NAME": ["raw_rows", "missing_sequence_numbers"], "VALUE": [500, 0], "SEVERITY": ["info", "error"]})
        if "ML.ALERT_LOG" in q:
            return pd.DataFrame({"RUN_ID": ["abcdef12"], "ROBOT_ID": ["amr-000"], "FIRST_ALERT_TS": [T], "NOTIFIED_AT": [T], "SOURCE": ["lambda"]})
        raise AssertionError(f"unexpected query: {q[:80]}")


def fake_aws(deliveries=10.0, rule="DISABLED", ec2=None):
    """A stand-in for the namespace of boto3 clients that infra.aws_clients returns."""
    from datetime import datetime, timezone

    class NotFound(Exception):
        pass

    return SimpleNamespace(
        region="us-west-2",
        sts=SimpleNamespace(get_caller_identity=lambda: {"Account": "123456789012"}),
        firehose=SimpleNamespace(describe_delivery_stream=lambda DeliveryStreamName: {"DeliveryStreamDescription": {"DeliveryStreamStatus": "ACTIVE"}}),
        cw=SimpleNamespace(get_metric_statistics=lambda **kw: {"Datapoints": [{"Sum": deliveries, "Timestamp": datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)}]}),
        s3=SimpleNamespace(head_bucket=lambda Bucket: {"ok": 1}),
        lam=SimpleNamespace(get_function=lambda FunctionName: {"ok": 1}),
        events=SimpleNamespace(exceptions=SimpleNamespace(ResourceNotFoundException=NotFound), describe_rule=lambda Name: {"State": rule}),
        ec2=SimpleNamespace(describe_instances=lambda Filters: {"Reservations": [] if ec2 is None else [{"Instances": [{"InstanceId": "i-1", "State": {"Name": ec2}, "InstanceType": "t4g.small"}]}]}),
    )
