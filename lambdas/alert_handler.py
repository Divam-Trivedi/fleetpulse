"""EventBridge -> Lambda -> Snowflake -> SNS.

Finds robots newly flagged by ML.RISK_SCORES, sends ONE SNS message for the batch, then records them
in ML.ALERT_LOG so each robot is announced once. Delivery is at-least-once: if the log insert fails
after publishing, the next run repeats the message (a duplicate is better than a missed alert).
"""
import json
import logging
import os

log = logging.getLogger()
log.setLevel(logging.INFO)

NEW_ALERTS_SQL = """
SELECT s.run_id, s.robot_id, MIN(s.window_end) AS first_alert_ts,
       MIN_BY(s.risk_15m, s.window_end) AS risk_at_flag,
       MIN_BY(s.avg_temp, s.window_end) AS temp_at_flag
FROM ML.RISK_SCORES s
WHERE s.alert
  AND NOT EXISTS (SELECT 1 FROM ML.ALERT_LOG l WHERE l.run_id = s.run_id AND l.robot_id = s.robot_id)
GROUP BY s.run_id, s.robot_id
ORDER BY risk_at_flag DESC, first_alert_ts
LIMIT 50
"""

LOG_SQL = (
    "INSERT INTO ML.ALERT_LOG (run_id, robot_id, first_alert_ts, notified_at, source) "
    "VALUES (%s, %s, %s, CURRENT_TIMESTAMP(), 'lambda')"
)


def format_message(rows) -> str:
    lines = [f"{len(rows)} robot(s) newly flagged as likely to hit overtemp within ~15 minutes:", ""]
    for run_id, robot_id, first_ts, risk, temp in rows:
        lines.append(f"- {robot_id} (run {run_id}): flagged at {first_ts} with risk {float(risk):.2f}, avg temp {float(temp):.1f} C at that time")
    return "\n".join(lines)


def run(conn, sns, topic_arn: str) -> dict:
    """Core logic, with dependencies injected so it can be tested without AWS or Snowflake."""
    cur = conn.cursor()
    try:
        cur.execute(NEW_ALERTS_SQL)
        rows = cur.fetchall()
        if not rows:
            return {"new_alerts": 0}
        sns.publish(
            TopicArn=topic_arn,
            Subject=f"FleetPulse: {len(rows)} robot(s) at risk",
            Message=format_message(rows),
        )
        cur.executemany(LOG_SQL, [(r[0], r[1], r[2]) for r in rows])
        return {"new_alerts": len(rows), "robots": [r[1] for r in rows]}
    finally:
        cur.close()


def _connect(secret: dict):
    import snowflake.connector
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(secret["private_key"].encode(), password=None)
    der = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return snowflake.connector.connect(
        account=secret["account"],
        user=secret["user"],
        private_key=der,
        role=secret.get("role", "FLEETPULSE_ALERT_ROLE"),
        warehouse=secret.get("warehouse", "FLEETPULSE_WH"),
        database=secret.get("database", "FLEETPULSE"),
        schema="ML",
        login_timeout=30,
        network_timeout=30,
    )


def lambda_handler(event, context):
    import boto3

    secret = json.loads(
        boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"]
    )
    conn = _connect(secret)
    try:
        result = run(conn, boto3.client("sns"), os.environ["TOPIC_ARN"])
    finally:
        conn.close()
    log.info("alert run finished: %s", json.dumps(result, default=str))
    return result


