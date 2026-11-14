"""Turns raw readings of the system into plain-language status lines. Pure logic: no AWS, Snowflake or Streamlit calls."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

OK, WARN, BAD, IDLE, UNKNOWN = "ok", "warn", "bad", "idle", "unknown"
COLOR = {OK: "green", WARN: "orange", BAD: "red", IDLE: "gray", UNKNOWN: "gray"}
FLOW, AWS, SNOW = "Is data flowing right now?", "AWS (cloud delivery and storage)", "Snowflake (data warehouse)"
LEGEND = ":green[●] working   :orange[●] needs attention   :red[●] problem   :gray[●] idle or not connected"


@dataclass
class Item:
    group: str
    name: str
    level: str
    detail: str

    def markdown(self) -> str:
        return f":{COLOR[self.level]}[●] **{self.name}**: {self.detail}"


def age(now: float, then) -> str | None:
    if then is None:
        return None
    ts = then.timestamp() if isinstance(then, datetime) else float(then)
    s = max(0, int(now - ts))
    return f"{s} seconds" if s < 90 else f"{s // 60} minutes" if s < 5400 else f"{s // 3600} hours"


def _n(d: dict, key: str) -> int:
    return sum(int(v.get(key) or 0) for v in d.values())


def _kind(stream: str) -> str:
    return "Parquet" if stream.endswith("parquet") else "JSON"


def evaluate(snap: dict, prev: dict | None = None) -> list[Item]:
    now = snap.get("ts") or datetime.now(timezone.utc).timestamp()
    sim, aws, sf = snap.get("sim"), snap.get("aws"), snap.get("sf")
    prev = prev or {}
    dt = max(1.0, now - prev["ts"]) if prev.get("ts") else None
    items: list[Item] = []

    # ---------------- Is data flowing right now? ----------------
    fh = (aws or {}).get("firehose", {})
    incoming = _n(fh, "incoming_5m")
    ec2 = (aws or {}).get("ec2")
    sources = []
    if sim and sim.get("running"):
        rate = ""
        if dt and prev.get("sim") and prev["sim"].get("total") is not None:
            rate = f" (about {(sim['total'] - prev['sim']['total']) / dt:,.0f} readings per second)"
        sources.append("the simulator in this app is running" + rate)
    if ec2 and ec2.get("state") == "running":
        sources.append("the cloud simulator computer (EC2) is running")
    if incoming > 0 and not sources:
        sources.append("readings are arriving from another source")
    if sources:
        items.append(Item(FLOW, "Robots are producing data", OK, "Yes: " + "; ".join(sources)))
    else:
        items.append(Item(FLOW, "Robots are producing data", IDLE, "No: nothing is producing data right now"))

    if aws is None:
        items.append(Item(FLOW, "Data is reaching AWS", UNKNOWN, "Unknown: not connected to AWS"))
    elif any(v.get("status") not in (None, "ACTIVE", "NOT_FOUND") for v in fh.values()):
        items.append(Item(FLOW, "Data is reaching AWS", BAD, "Problem: a delivery stream is not ready"))
    elif incoming > 0:
        per = ", ".join(f"{_kind(k)} {int(v.get('incoming_5m') or 0):,}" for k, v in fh.items())
        items.append(Item(FLOW, "Data is reaching AWS", OK,
                          f"Yes: {incoming:,} readings arrived in the last 5 minutes ({per}); {_n(fh, 'delivered_10m'):,} were stored in S3 in the last 10 minutes"))
    else:
        items.append(Item(FLOW, "Data is reaching AWS", IDLE, "No: nothing arrived in the last 5 minutes"))

    if sf is None:
        items.append(Item(FLOW, "Snowflake is processing data", UNKNOWN, "Unknown: not connected to Snowflake"))
    else:
        signals, pipes, tables = [], sf.get("pipes", {}), sf.get("tables", {})
        pending = sum(int(p.get("pending") or 0) for p in pipes.values())
        if pending > 0:
            signals.append(f"{pending} file(s) are being loaded")
        old = prev.get("sf") or {}
        if sf.get("raw_rows") is not None and old.get("raw_rows") is not None and sf["raw_rows"] > old["raw_rows"]:
            signals.append(f"+{sf['raw_rows'] - old['raw_rows']:,} new rows arrived")
        for name, t in tables.items():
            before = (old.get("tables", {}).get(name) or {}).get("rows")
            if before is not None and t.get("rows") is not None and t["rows"] > before:
                signals.append(f"+{t['rows'] - before:,} rows prepared in {name}")
        last = max((p.get("last_ingested") for p in pipes.values() if p.get("last_ingested")), default=None)
        if signals:
            items.append(Item(FLOW, "Snowflake is processing data", OK, "Yes: " + "; ".join(signals)))
        elif tables and all(t.get("state") == "SUSPENDED" for t in tables.values()):
            items.append(Item(FLOW, "Snowflake is processing data", WARN, "Paused: the processing steps are switched off, so new data will not be prepared"))
        else:
            a = age(now, last)
            items.append(Item(FLOW, "Snowflake is processing data", IDLE, "No: nothing is being processed right now" + (f" (last file loaded {a} ago)" if a else "")))

    # ---------------- AWS ----------------
    if aws is None:
        items.append(Item(AWS, "Connection to AWS", UNKNOWN, "not connected (check the profile and region in the sidebar)"))
    else:
        items.append(Item(AWS, "Connection to AWS", BAD if aws.get("error") else OK, aws.get("error") or "connected"))
        for k, v in fh.items():
            st_ = v.get("status")
            if st_ == "ACTIVE":
                items.append(Item(AWS, f"Delivery stream ({_kind(k)} format)", OK, "ready"))
            elif st_ == "NOT_FOUND":
                items.append(Item(AWS, f"Delivery stream ({_kind(k)} format)", IDLE, "not deployed (removed, or never created)"))
            elif st_:
                items.append(Item(AWS, f"Delivery stream ({_kind(k)} format)", BAD, f"not ready ({st_.lower()})"))
            else:
                items.append(Item(AWS, f"Delivery stream ({_kind(k)} format)", UNKNOWN, "unknown"))
        if aws.get("bucket") is not None:
            items.append(Item(AWS, "Storage bucket (S3)", OK if aws["bucket"] else IDLE,
                              "exists (still holds data, so it may need emptying)" if aws["bucket"] else "not deployed"))
        if aws.get("lambda") is not None:
            items.append(Item(AWS, "Alert function (Lambda)", OK if aws["lambda"] else IDLE,
                              "deployed" if aws["lambda"] else "not deployed (removed, or never created)"))
        rule = aws.get("rule")
        items.append(Item(AWS, "Alert timer (EventBridge)", OK if rule == "ENABLED" else IDLE,
                          {"ENABLED": "on: at-risk robots are checked automatically", "DISABLED": "off: alerts are only sent when you press Run alert check now"}.get(rule, "not set up")))
        if ec2:
            s = ec2.get("state")
            items.append(Item(AWS, "Cloud simulator computer (EC2)", OK if s == "running" else WARN if s in ("pending", "stopping") else IDLE,
                              {"running": "running", "stopped": "stopped (not costing compute money)"}.get(s, s)))
        else:
            items.append(Item(AWS, "Cloud simulator computer (EC2)", IDLE, "not set up"))

    # ---------------- Snowflake ----------------
    if sf is None:
        items.append(Item(SNOW, "Connection to Snowflake", UNKNOWN, "not connected (use Connect to Snowflake in the sidebar)"))
    else:
        items.append(Item(SNOW, "Connection to Snowflake", BAD if sf.get("error") else OK, sf.get("error") or "connected"))
        w = sf.get("warehouse")
        if w:
            items.append(Item(SNOW, "Compute (warehouse)", OK if w == "STARTED" else WARN if w in ("RESUMING", "SUSPENDING") else IDLE,
                              {"STARTED": "running", "SUSPENDED": "asleep (wakes up automatically when needed)"}.get(w, w.lower())))
        for name, p in sf.get("pipes", {}).items():
            label = f"Data intake (Snowpipe {name})"
            if p.get("state") == "UNKNOWN":
                items.append(Item(SNOW, label, UNKNOWN, "status unavailable: grant MONITOR on this pipe (snowflake/15_dashboard_grants.sql)"))
            elif p.get("state") == "RUNNING":
                n = int(p.get("pending") or 0)
                items.append(Item(SNOW, label, OK, f"ready, {n} file(s) waiting" if n == 0 else f"loading {n} file(s)"))
            else:
                items.append(Item(SNOW, label, BAD, (p.get("state") or "unknown").lower()))
        for name, t in tables.items():
            a = age(now, t.get("data_ts"))
            label = f"Processing step ({name})"
            if t.get("state") == "RUNNING":
                items.append(Item(SNOW, label, OK, f"running, {int(t.get('rows') or 0):,} rows" + (f", updated {a} ago" if a else "")))
            else:
                items.append(Item(SNOW, label, WARN, f"paused, {int(t.get('rows') or 0):,} rows"))
    return items
