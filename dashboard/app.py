"""FleetPulse dashboard (laptop):  streamlit run dashboard/app.py

Page 1  Data flow demo : looping animation of the whole pipeline (needs no cloud).
Page 2  Live           : Dashboard tab (fleet counts, pipeline status, one chart, collapsed controls)
                         and Tables tab (charts plus top-5 tables that expand to show everything).

AWS and Snowflake are checked by a background thread (dashboard/poller.py), so the screen only reads finished answers
and never fades while waiting on the network. Status checks are free; refreshing the robot tables queries Snowflake and
is therefore on demand or on a timer you choose.

Note for editing: Streamlit prints the value of any bare expression, so never write `a() if x else b()` as a statement."""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

from dashboard import infra, status  # noqa: E402
from dashboard.poller import Poller  # noqa: E402
from dashboard.sim_runner import SINK_CHOICES, SimRunner, build_config  # noqa: E402

st.set_page_config(page_title="FleetPulse", layout="wide")
ss = st.session_state
for k, v in {"runner": SimRunner(), "poller": Poller(), "sf": None, "sf_error": None, "log": [], "sf_paused": False}.items():
    ss.setdefault(k, v)
runner: SimRunner = ss["runner"]
poller: Poller = ss["poller"]
AUTO = {"Off": 0, "Every minute": 60, "Every 5 minutes": 300}

PROBLEMS = {0: "OK", 101: "Lidar dropout", 102: "Wheel slip", 201: "Overheated", 301: "Low battery"}
HEADINGS = {
    "robot_id": "Robot", "status": "Status", "is_silent": "Stopped sending", "battery_pct": "Battery %", "motor_temp": "Motor temp (°C)",
    "motor_current": "Motor current (A)", "silent_for_s": "Silent for (s)", "last_seen_ts": "Last seen", "risk_15m": "Overheat risk (0 to 1)",
    "avg_temp": "Avg temp (°C)", "alert": "Flagged", "already_tripped": "Already overheated", "window_end": "As of", "run_id": "Run",
    "records": "Readings", "first_ts": "Started", "last_ts": "Last reading", "first_alert_ts": "First flagged", "notified_at": "Emailed at",
    "source": "Sent by", "check_name": "Check", "value": "Result", "severity": "Type",
}


def note(msg: str) -> None:
    ss["log"] = ([f"{time.strftime('%H:%M:%S')} {msg}"] + ss["log"])[:8]


def default_sf_factory():
    from analytics.session import create_session

    return create_session(role="FLEETPULSE_DEV", schema="ML")


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Connections")
    profile = st.text_input("AWS profile", os.environ.get("FLEETPULSE_AWS_PROFILE", "fleetpulse"), help="The name of your AWS credentials profile.")
    region = st.text_input("AWS region", os.environ.get("FLEETPULSE_AWS_REGION", "us-west-2"))
    if st.button("Connect to Snowflake"):
        try:
            ss["sf"], ss["sf_error"] = default_sf_factory(), None
        except Exception as exc:
            ss["sf"], ss["sf_error"] = None, str(exc).splitlines()[0][:200]
        poller.wake()
    if ss["sf"] is not None:
        st.success("Snowflake connected")
    else:
        st.info(ss["sf_error"] or "Snowflake is not connected. Set the SNOWFLAKE_ variables, then press Connect.")

    st.header("Robot tables")
    st.selectbox("Refresh automatically", list(AUTO), key="auto",
                 help="Status lines update by themselves. Refreshing the robot tables and charts uses Snowflake compute, which costs credits while it runs.")
    st.caption("Off by default so nothing runs up a bill while you are not looking.")
    poller.configure(profile, region, (ss.get("sf_factory") or default_sf_factory) if ss["sf"] is not None else None, runner, AUTO[ss.get("auto", "Off")], ss["sf_paused"])
    poller.touch()
    if st.button("Refresh robot tables now") and ss["sf"] is not None:
        with st.spinner("Asking Snowflake..."):
            poller.refresh_data(ss.get("run_sel"))
    runs_df = poller.data().get("runs")
    if runs_df is not None and len(runs_df):
        counts = runs_df.set_index("run_id")["records"]
        st.selectbox("Simulation run to show", runs_df.run_id.tolist(), key="run_sel", format_func=lambda r: f"{r} ({int(counts[r]):,} readings)")
        if ss.get("run_sel") and ss["run_sel"] != poller.data().get("run_id"):
            poller.request_run(ss["run_sel"])
    if ss["sf_paused"]:
        st.warning("You paused Snowflake from this app, so automatic refresh is held off.")
    if ss["log"]:
        st.caption("Recent actions")
        for line in ss["log"]:
            st.caption(line)


# ---------------------------------------------------------------- helpers
def fleet_kpis() -> dict:
    if runner.running:
        try:
            rs = runner.status()["robots"]
            return {"source": "the simulator running in this app (live)", "robots": len(rs), "active": sum(r["active"] for r in rs),
                    "silent": sum(not r["active"] for r in rs), "faulted": sum(1 for r in rs if r["last"] and r["last"]["error_code"] != 0), "at_risk": None}
        except Exception:
            pass
    d = poller.data()
    df, risk = d.get("robots"), d.get("risk")
    if df is None or df.empty:
        return {}
    silent = int(df.is_silent.fillna(False).astype(bool).sum())
    return {"source": f"Snowflake, simulation run {d['run_id']}, as of {time.strftime('%H:%M:%S', time.localtime(d['fetched_at']))}",
            "robots": len(df), "active": len(df) - silent, "silent": silent, "faulted": int((df.error_code.fillna(0) != 0).sum()),
            "at_risk": int(risk.alert.astype(bool).sum()) if risk is not None and len(risk) else None}


def robots_table() -> tuple[pd.DataFrame | None, str]:
    cols = ["robot_id", "status", "is_silent", "error_code", "battery_pct", "motor_temp", "motor_current", "silent_for_s", "last_seen_ts"]
    if runner.running:
        try:
            rows = []
            for r in runner.status()["robots"]:
                last = r["last"] or {}
                rows.append({"robot_id": r["id"], "status": last.get("status"), "is_silent": not r["active"], "error_code": last.get("error_code", 0),
                             "battery_pct": last.get("battery_pct"), "motor_temp": last.get("motor_temp"), "motor_current": last.get("motor_current"),
                             "silent_for_s": None, "last_seen_ts": None})
            return pd.DataFrame(rows, columns=cols), "the simulator running in this app (live)"
        except Exception:
            pass
    d = poller.data()
    df = d.get("robots")
    if df is None or df.empty:
        return None, ""
    df = df.copy()
    df["error_code"] = df.error_code.fillna(0)
    return df.sort_values(["error_code", "is_silent", "motor_temp"], ascending=False)[cols], f"Snowflake, simulation run {d['run_id']}"


def friendly(df: pd.DataFrame) -> pd.DataFrame:
    """Plain column headings, and error codes shown as words."""
    df = df.copy()
    if "error_code" in df.columns:
        pos = list(df.columns).index("error_code")
        df.insert(pos, "problem", df.pop("error_code").fillna(0).astype(int).map(PROBLEMS).fillna("Other problem"))
        HEADINGS["problem"] = "Problem"
    return df.rename(columns=HEADINGS)


def top5(title: str, df: pd.DataFrame | None, caption: str = "", n: int = 5) -> None:
    st.markdown(f"**{title}**")
    if caption:
        st.caption(caption)
    if df is None or df.empty:
        st.caption("Nothing to show yet.")
        return
    st.dataframe(friendly(df.head(n)), hide_index=True)
    if len(df) > n:
        with st.expander(f"Show all {len(df):,} rows"):
            st.dataframe(friendly(df), hide_index=True)


def chart_or_note(df: pd.DataFrame | None, kind: str = "line") -> None:
    if df is None or len(df) == 0:
        st.caption("Needs Snowflake data: connect in the sidebar and press Refresh robot tables now.")
    elif kind == "bar":
        st.bar_chart(df)
    else:
        st.line_chart(df)


# ---------------------------------------------------------------- page 1
def page_demo() -> None:
    st.title("Data flow demo")
    st.caption("A looping animation of how a robot's reading travels from the simulator, through AWS, into Snowflake, and finally to an email alert. "
               "It is not to scale and needs no cloud resources.")
    flow = ROOT / "dashboard" / "flow_demo.html"
    if hasattr(st, "iframe"):                    # newer Streamlit; components.v1.html is being removed
        st.iframe(flow, height=720)
    else:
        components.html(flow.read_text(), height=720, scrolling=False)


# ---------------------------------------------------------------- page 2 / dashboard
@st.fragment(run_every=5)
def dashboard_live() -> None:
    result = poller.latest()
    if result is None:
        with st.spinner("Checking AWS and Snowflake for the first time..."):
            result = poller.poll_once()

    st.subheader("Fleet")
    k = fleet_kpis()
    if k:
        m = st.columns(5)
        m[0].metric("Robots", k["robots"])
        m[1].metric("Active", k["active"], help="Sending data normally")
        m[2].metric("Silent", k["silent"], help="Stopped sending data for more than 30 seconds")
        m[3].metric("Faulted", k["faulted"], help="The latest reading reported a problem, such as overheating")
        m[4].metric("At risk", "n/a" if k["at_risk"] is None else k["at_risk"], help="The model expects these to overheat within about 15 minutes")
        st.caption(f"Source: {k['source']}. Active means sending data; Silent means stopped sending; Faulted means the last reading reported a problem; "
                   "At risk means the model expects an overheating soon.")
    else:
        st.info("No robot data to show yet. Start the simulator under Controls below, or connect to Snowflake in the sidebar and press Refresh robot tables now.")

    st.caption(status.LEGEND)
    for group in (status.FLOW, status.AWS, status.SNOW):
        st.subheader(group)
        for it in (i for i in result["items"] if i.group == group):
            st.markdown(it.markdown())
    st.caption(f"Status checked {int(time.time() - result['at'])} seconds ago. It updates by itself.")

    fh = result["firehose"]
    if len(fh):
        st.subheader("Readings arriving at AWS, per minute (last hour)")
        st.line_chart(fh)


# ---------------------------------------------------------------- page 2 / tables
@st.fragment(run_every=5)
def tables_live() -> None:
    d = poller.data()
    if d.get("error"):
        st.warning(f"Snowflake could not be queried: {d['error']}")
    robots, src = robots_table()
    if robots is None:
        st.info("No data yet. Connect to Snowflake and press Refresh robot tables now, or start the simulator on the Dashboard tab.")
        return
    fleet, risk = d.get("fleet"), d.get("risk")

    st.subheader("Charts")
    a, b = st.columns(2)
    with a:
        st.markdown("**Robots sending data, per minute**")
        chart_or_note(fleet.set_index("minute_ts")[["reporting_robots"]] if fleet is not None and len(fleet) else None)
    with b:
        st.markdown("**Motor temperature (°C): average and hottest**")
        chart_or_note(fleet.set_index("minute_ts")[["avg_motor_temp", "max_motor_temp"]] if fleet is not None and len(fleet) else None)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Chance each robot overheats soon (top 15)**")
        chart_or_note(risk.set_index("robot_id")["risk_15m"].head(15) if risk is not None and len(risk) else None, "bar")
    with c2:
        st.markdown("**Fleet status**")
        k = fleet_kpis()
        if k:
            st.bar_chart(pd.Series({"active": k["active"], "silent": k["silent"], "faulted": k["faulted"], "at risk": k["at_risk"] or 0}))

    st.subheader("Tables")
    st.caption("Each table shows the first 5 rows. Open 'Show all' under a table to see every row.")
    top5("Robots", robots, f"Robots with problems are listed first. Source: {src}.")
    top5("Overheating risk", risk, "Highest risk first. 'Flagged' means the model expects an overheating within about 15 minutes.")
    top5("Robots that stopped sending data", d.get("silent"))
    top5("Alerts sent by email", d.get("alerts"), "One row per robot that was emailed about. Empty if no alerts were sent, or if snowflake/15_dashboard_grants.sql has not been run.")
    top5("Simulation runs", d.get("runs"))
    top5("Data checks (these should all look normal)", d.get("dq"), "'error' rows should show 0. Any other number means data was lost or malformed.")


# ---------------------------------------------------------------- controls (collapsed, outside the auto-updating parts)
def controls() -> None:
    with st.expander("Controls: start the simulator, switch cloud parts on or off, remove everything"):
        st.markdown("**Simulator**")
        scenarios = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "configs").rglob("*.yaml"))
        if not runner.running:
            c1, c2, c3 = st.columns(3)
            base = c1.selectbox("Settings file", scenarios, index=scenarios.index("configs/default.yaml") if "configs/default.yaml" in scenarios else 0)
            behavior = c1.selectbox("How robots behave", ["all_publishing", "mixed_fleet", "random_on_off"],
                                    help="all_publishing: every robot sends data. mixed_fleet: some always send, some send randomly, some stop suddenly. random_on_off: each robot switches itself on and off.")
            robots = c2.slider("Number of robots", 1, 60, 20)
            speed = c2.select_slider("Speed (simulated seconds per real second)", [1, 5, 10, 30, 60, 100], value=10)
            sink = c3.selectbox("Send the data to", list(SINK_CHOICES))
            params = {}
            if behavior == "mixed_fleet":
                p1, p2, p3 = c3.columns(3)
                params = {"continuous_pct": p1.number_input("Always %", 0, 100, 60), "random_pct": p2.number_input("Random %", 0, 100, 30), "abrupt_pct": p3.number_input("Sudden stop %", 0, 100, 10)}
            if st.button("Start simulator", type="primary"):
                try:
                    runner.start(build_config(str(ROOT / base), behavior, robots, float(speed), sink, region, profile, params))
                    note(f"Simulator started ({behavior}, {robots} robots)")
                except Exception as exc:
                    st.error(str(exc)[:300])
                st.rerun()
        else:
            if st.button("Stop simulator"):
                runner.stop()
                note("Simulator stopped")
                st.rerun()
            k1, k2, k3 = st.columns(3)
            with k1:
                nb = st.selectbox("Change how robots behave", ["all_publishing", "mixed_fleet", "random_on_off"], key="nb")
                if st.button("Apply behavior"):
                    try:
                        runner.call("set_behavior", nb)
                        note(f"Behavior changed to {nb}")
                    except Exception as exc:
                        st.error(str(exc)[:200])
            with k2:
                n = st.number_input("Number of robots", 0, 200, 20)
                if st.button("Change number of robots"):
                    runner.call("scale_fleet", int(n))
                    note(f"Fleet size set to {int(n)}")
            with k3:
                ids = [r["id"] for r in runner.status()["robots"]]
                rid = st.selectbox("Robot", ids) if ids else None
                wear = st.number_input("Wear speed (higher = fails sooner)", 0.0, 0.01, 0.0002, format="%.5f")
                if rid and st.button("Set wear speed"):
                    runner.call("update_params", rid, wear_rate=float(wear))
                    note(f"{rid}: wear speed {wear}")
                if rid and st.button("Bring robot back"):
                    runner.call("reset_robot", rid)
                    note(f"{rid} brought back")

        st.markdown("**Cloud parts**")
        c = poller.aws()
        if c is None:
            st.warning("Not connected to AWS, so these switches are unavailable.")
        else:
            e1, e2, e3, e4 = st.columns(4)
            inst = infra.find_instance(c.ec2)
            if e1.button("Start cloud simulator computer") and inst:
                infra.ec2_set(c.ec2, inst["id"], True)
                note("Cloud simulator computer: start requested")
            if e1.button("Stop cloud simulator computer") and inst:
                infra.ec2_set(c.ec2, inst["id"], False)
                note("Cloud simulator computer: stop requested")
            if e2.button("Turn alert timer on"):
                infra.set_rule(c.events, True)
                note("Alert timer turned on")
            if e2.button("Turn alert timer off"):
                infra.set_rule(c.events, False)
                note("Alert timer turned off")
            if ss["sf"] is not None:
                if e3.button("Pause Snowflake"):
                    res = infra.run_statements(ss["sf"], infra.SNOWFLAKE_PAUSE)
                    ss["sf_paused"] = True
                    note(f"Pause: {sum(r['ok'] for r in res)} of {len(res)} steps succeeded")
                if e3.button("Resume Snowflake"):
                    res = infra.run_statements(ss["sf"], infra.SNOWFLAKE_RESUME)
                    ss["sf_paused"] = False
                    note(f"Resume: {sum(r['ok'] for r in res)} of {len(res)} steps succeeded")
            if e4.button("Run alert check now"):
                try:
                    st.json(infra.invoke_alert_lambda(c.lam))
                except Exception as exc:
                    st.error(str(exc)[:300])
            with st.expander("Remove everything in AWS (cannot be undone)"):
                st.warning("This empties the storage bucket and then deletes the AWS parts of FleetPulse with Terraform. "
                           "Snowflake is not touched by this button; run the SQL below yourself if you want it removed too.")
                if st.button("Show what would be deleted"):
                    with st.spinner("Asking Terraform..."):
                        st.code(infra.terraform_plan_destroy()[-4000:])
                phrase = st.text_input(f'Type "{infra.DESTROY_PHRASE}" to unlock the delete button')
                if st.button("Delete the AWS parts", disabled=phrase.strip() != infra.DESTROY_PHRASE):
                    try:
                        with st.spinner("Deleting..."):
                            out = infra.terraform_destroy(phrase, s3=c.s3, bucket=infra.bucket_name(c))
                        st.success("Deleted")
                        st.code(out)
                        note("AWS parts deleted")
                    except Exception as exc:
                        st.error(str(exc)[:1500])
                st.code(infra.SNOWFLAKE_TEARDOWN_SQL, language="sql")


def page_live() -> None:
    tab_dash, tab_tables = st.tabs(["Dashboard", "Tables"])
    with tab_dash:
        dashboard_live()
        controls()
    with tab_tables:
        tables_live()


start_live = os.environ.get("FLEETPULSE_START_PAGE") == "live"      # lets tests (and you) open straight on the Live page
pg = st.navigation([st.Page(page_demo, title="Data flow demo", url_path="demo", default=not start_live),
                    st.Page(page_live, title="Live", url_path="live", default=start_live)])
pg.run()
