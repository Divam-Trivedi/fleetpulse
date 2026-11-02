"""Snowpark session factory. Credentials come from environment variables, never from code.

    export SNOWFLAKE_ACCOUNT=...   # e.g. orgname-accountname
    export SNOWFLAKE_USER=...
    export SNOWFLAKE_PASSWORD=...
"""
from __future__ import annotations

import os

from snowflake.snowpark import Session

_REQUIRED = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD")


def create_session(
    role: str = "FLEETPULSE_DEV",
    warehouse: str = "FLEETPULSE_WH",
    database: str = "FLEETPULSE",
    schema: str = "ML",
) -> Session:
    missing = [k for k in _REQUIRED if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Set these environment variables first: {', '.join(missing)}")
    return Session.builder.configs(
        {
            "account": os.environ["SNOWFLAKE_ACCOUNT"],
            "user": os.environ["SNOWFLAKE_USER"],
            "password": os.environ["SNOWFLAKE_PASSWORD"],
            "role": role,
            "warehouse": warehouse,
            "database": database,
            "schema": schema,
        }
    ).create()


if __name__ == "__main__":
    s = create_session()
    print(s.sql("SELECT CURRENT_ROLE(), CURRENT_DATABASE(), CURRENT_SCHEMA()").collect())
    s.close()
