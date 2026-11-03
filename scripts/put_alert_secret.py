"""Store the Snowflake service credentials in AWS Secrets Manager (the key never enters Terraform state).

    python scripts/put_alert_secret.py --key-file alert_key.p8 --profile fleetpulse --region us-west-2
Reads the account from SNOWFLAKE_ACCOUNT unless --account is given.
"""
import argparse
import json
import os
from pathlib import Path

import boto3


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--key-file", required=True)
    p.add_argument("--account", default=os.environ.get("SNOWFLAKE_ACCOUNT"))
    p.add_argument("--user", default="FLEETPULSE_ALERT_SVC")
    p.add_argument("--secret-id", default="fleetpulse/snowflake-alert")
    p.add_argument("--profile")
    p.add_argument("--region", default="us-west-2")
    a = p.parse_args()
    if not a.account:
        raise SystemExit("Set SNOWFLAKE_ACCOUNT or pass --account")
    secret = {"account": a.account, "user": a.user, "private_key": Path(a.key_file).read_text()}
    client = boto3.Session(profile_name=a.profile, region_name=a.region).client("secretsmanager")
    client.put_secret_value(SecretId=a.secret_id, SecretString=json.dumps(secret))
    print(f"Stored secret {a.secret_id} (account={a.account}, user={a.user}); key not echoed.")


if __name__ == "__main__":
    main()
