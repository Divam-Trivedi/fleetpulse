"""FleetPulse dashboard (runs on your laptop):  streamlit run dashboard/app.py

Tabs: animated data-flow demo, live pipeline stats, simulator control, infrastructure control.
Nothing here needs the cloud to be up: panels show 'not connected' instead of failing."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

from dashboard import data, infra  # noqa: E402
from dashboard.sim_runner import SINK_CHOICES, SimRunner, build_config  # noqa: E402

st.set_page_config(page_title="FleetPulse", layout="wide")
st.session_state.setdefault("runner", SimRunner())
st.session_state.setdefault("sf", None)
st.session_state.setdefault("sf_error", None)
st.session_state.setdefault("log", [])


def note(msg: str) -> None:
    st.session_state["log"] = ([msg] + st.session_state["log"])[:12]


# ---------------- sidebar: connections ----------------
with st.sidebar:
    st.header("Connections")
    profile = st.text_input("AWS profile", os.environ.get("FLEETPULSE_AWS_PROFILE", "fleetpulse"))
    region = st.text_input("AWS region", os.environ.get("FLEETPULSE_AWS_REGION", "us-west-2"))
    if st.button("Connect to Snowflake"):
        try:
            from analytics.session import create_session

            st.session_state["sf"] = create_session(role="FLEETPULSE_DEV", schema="ML")
            st.session_state["sf_error"] = None
        except Exception as exc:
            st.session_state["sf"], st.session_state["sf_error"] = None, str(exc).splitlines()[0][:200]
    if st.session_state["sf"] is not None:
        st.success("Snowflake connected")
    else:
        st.info(st.session_state["sf_error"] or "Snowflake not connected (set SNOWFLAKE_* variables, then connect)")
    st.caption("Recent actions")
    for line in st.session_state["log"]:
        st.caption(line)


@st.cache_resource(show_spinner=False)
def aws(profile_name: str, region_name: str):
    return infra.aws_clients(profile_name or None, region_name)


def aws_or_none():
    try:
        c = aws(profile, region)
        c.sts.get_caller_identity()
        return c
    except Exception:
        return None


sf = st.session_state["sf"]
runner: SimRunner = st.session_state["runner"]
tab_flow, tab_live, tab_sim, tab_infra = st.tabs(["Data flow demo", "Live pipeline", "Simulator", "Infrastructure"])

# ---------------- tab 1: animated explainer ----------------
with tab_flow:
    st.caption("A looping, not-to-scale animation of the whole pipeline. It runs without any cloud resources.")
    flow = ROOT / "dashboard" / "flow_demo.html"
    if hasattr(st, "iframe"):                      # newer Streamlit; components.v1.html is being removed
        st.iframe(flow, height=720)
    else:
        components.html(flow.read_text(), height=720, scrolling=False)

# ---------------- tab 2: live pipeline ----------------
with tab_live:

    @st.fragment(run_every=15)
    def live_pipeline() -> None:
        c = aws_or_none()
        st.subheader("Records at each stop")
        cols = st.columns(7)
        sim_total = None
        if runner.running:
            try:
                sim_total = runner.status()["total_published"]
            except Exception:
                pass
        cols[0].metric("Simulator (this session)", "-" if sim_total is None else f"{sim_total:,}")
        if c:
            try:
                fh = sum(infra.firehose_incoming(c.cw, s) for s in infra.STREAMS)
                cols[1].metric("Firehose, last 15 min", f"{fh:,}")
                bucket = infra.bucket_name(c)
                nj, _ = infra.count_s3_objects(c.s3, bucket, "raw/telemetry/")
                npq, _ = infra.count_s3_objects(c.s3, bucket, "raw_parquet/telemetry/")
                cols[2].metric("S3 files (json / parquet)", f"{nj} / {npq}")
            except Exception as exc:
                cols[1].metric("Firehose", "n/a")
                st.caption(f"AWS metrics unavailable: {str(exc)[:120]}")
        else:
            cols[1].metric("Firehose, last 15 min", "AWS not connected")
        if sf is not None:
            try:
                sc = data.stage_counts(sf).iloc[0]
                cols[3].metric("Snowflake RAW rows", f"{int(sc.raw_rows):,}")
                cols[4].metric("Staged rows", f"{int(sc.staged_rows):,}")
                cols[5].metric("Feature windows", f"{int(sc.feature_rows):,}")
                cols[6].metric("Scored windows", f"{int(sc.scored_rows):,}")
            except Exception as exc:
                st.caption(f"Snowflake counts unavailable: {str(exc).splitlines()[0][:140]}")
        else:
            cols[3].metric("Snowflake", "not connected")

        if sf is None:
            st.info("Connect to Snowflake in the sidebar to see fleet health, silent robots, risk scores and alerts.")
            return
        try:
            run_df = data.runs(sf)
            if run_df.empty:
                st.info("No data loaded yet.")
                return
            run_id = st.selectbox("Run", run_df.run_id.tolist(), key="live_run",
                                  format_func=lambda r: f"{r}  ({int(run_df.set_index('run_id').loc[r, 'records']):,} records)")
            left, right = st.columns(2)
            fm = data.fleet_minutely(sf, run_id)
            with left:
                st.markdown("**Robots reporting per minute** (drops when robots go silent)")
                st.line_chart(fm.set_index("minute_ts")[["reporting_robots"]])
                st.markdown("**Motor temperature (average and maximum)**")
                st.line_chart(fm.set_index("minute_ts")[["avg_motor_temp", "max_motor_temp"]])
            with right:
                st.markdown("**Latest risk per robot** (alert = flagged and not yet tripped)")
                st.dataframe(data.latest_risk(sf, run_id), hide_index=True, height=240)
                st.markdown("**Silent robots**")
                silent = data.silent_robots(sf, run_id)
                st.dataframe(silent, hide_index=True, height=120) if len(silent) else st.caption("None")
            a, b = st.columns(2)
            with a:
                st.markdown("**Data quality**")
                st.dataframe(data.data_quality(sf), hide_index=True)
            with b:
                st.markdown("**Alert log** (one row per robot emailed)")
                try:
                    st.dataframe(data.alert_log(sf), hide_index=True)
                except Exception:
                    st.caption("Alert log not readable: run snowflake/15_dashboard_grants.sql")
        except Exception as exc:
            st.warning(f"Snowflake query failed: {str(exc).splitlines()[0][:200]}")

    live_pipeline()

# ---------------- tab 3: simulator ----------------
with tab_sim:
    st.caption("Runs the fleet simulator inside this dashboard process, so the control API can be used while it runs.")
    scenarios = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "configs").rglob("*.yaml"))
    if not runner.running:
        c1, c2, c3 = st.columns(3)
        base = c1.selectbox("Config", scenarios, index=scenarios.index("configs/default.yaml") if "configs/default.yaml" in scenarios else 0)
        behavior = c1.selectbox("Behavior", ["all_publishing", "mixed_fleet", "random_on_off"])
        robots = c2.slider("Robots", 1, 60, 20)
        time_scale = c2.select_slider("Speed (sim seconds per real second)", [1, 5, 10, 30, 60, 100], value=10)
        sink_choice = c3.selectbox("Send data to", list(SINK_CHOICES))
        params: dict = {}
        if behavior == "mixed_fleet":
            p1, p2, p3 = c3.columns(3)
            params = {"continuous_pct": p1.number_input("Continuous %", 0, 100, 60), "random_pct": p2.number_input("Random %", 0, 100, 30),
                      "abrupt_pct": p3.number_input("Abrupt %", 0, 100, 10)}
        if st.button("Start simulator", type="primary"):
            try:
                runner.start(build_config(str(ROOT / base), behavior, robots, float(time_scale), sink_choice, region, profile, params))
                note(f"Simulator started: {behavior}, {robots} robots, {sink_choice}")
            except Exception as exc:
                st.error(str(exc)[:300])
            st.rerun()
    else:
        if st.button("Stop simulator"):
            runner.stop()
            note("Simulator stopped")
            st.rerun()

    @st.fragment(run_every=2)
    def sim_live() -> None:
        if not runner.running:
            st.info("Simulator is not running.")
            return
        try:
            s = runner.status()
        except Exception as exc:
            st.warning(f"Status unavailable: {exc}")
            return
        m = st.columns(5)
        m[0].metric("Behavior", s["behavior"]["name"])
        m[1].metric("Simulated time", f"{s['sim_time_s']:.0f} s")
        m[2].metric("Active robots", f"{s['active_count']}/{s['fleet_size']}")
        m[3].metric("Records published", f"{s['total_published']:,}")
        ss = s.get("sink_stats")
        m[4].metric("Sink (sent / dropped)", f"{ss['sent']:,} / {ss['dropped']}" if ss else s["sink_type"])
        rows = [{"robot": r["id"], "role": r["role"], "active": r["active"], "published": r["published"],
                 **({k: r["last"][k] for k in ("battery_pct", "motor_temp", "speed", "status", "error_code")} if r["last"] else {})}
                for r in s["robots"]]
        st.dataframe(pd.DataFrame(rows), hide_index=True, height=300)

    sim_live()

    if runner.running:
        st.markdown("**Control the running fleet**")
        k1, k2, k3 = st.columns(3)
        with k1:
            nb = st.selectbox("Behavior", ["all_publishing", "mixed_fleet", "random_on_off"], key="nb")
            if st.button("Apply behavior"):
                try:
                    runner.call("set_behavior", nb)
                    note(f"Behavior changed to {nb}")
                except Exception as exc:
                    st.error(str(exc)[:200])
        with k2:
            n = st.number_input("Fleet size", 0, 200, 20)
            if st.button("Scale fleet"):
                runner.call("scale_fleet", int(n))
                note(f"Fleet scaled to {int(n)}")
        with k3:
            ids = [r["id"] for r in runner.status()["robots"]]
            rid = st.selectbox("Robot", ids) if ids else None
            wear = st.number_input("wear_rate (per sim second)", 0.0, 0.01, 0.0002, format="%.5f")
            if rid and st.button("Set wear rate"):
                runner.call("update_params", rid, wear_rate=float(wear))
                note(f"{rid}: wear_rate={wear}")
            if rid and st.button("Reset robot (revive)"):
                runner.call("reset_robot", rid)
                note(f"{rid} reset")

# ---------------- tab 4: infrastructure ----------------
with tab_infra:
    c = aws_or_none()
    if not c:
        st.warning("AWS not connected. Check the profile and region in the sidebar and that your credentials are valid.")
    else:
        st.subheader("Controls")
        e1, e2, e3, e4 = st.columns(4)
        with e1:
            inst = infra.find_instance(c.ec2)
            st.metric("EC2 simulator", "not deployed" if inst is None else inst["state"])
            if inst:
                if st.button("Start instance"):
                    infra.ec2_set(c.ec2, inst["id"], True); note("EC2 start requested")
                if st.button("Stop instance"):
                    infra.ec2_set(c.ec2, inst["id"], False); note("EC2 stop requested")
        with e2:
            rs = infra.rule_state(c.events)
            st.metric("Alert schedule (EventBridge)", rs or "not deployed")
            if rs:
                if st.button("Enable schedule"):
                    infra.set_rule(c.events, True); note("Alert schedule enabled")
                if st.button("Disable schedule"):
                    infra.set_rule(c.events, False); note("Alert schedule disabled")
        with e3:
            st.metric("Snowflake pipeline", "connected" if sf is not None else "not connected")
            if sf is not None:
                if st.button("Pause (suspend tables + warehouse)"):
                    res = infra.run_statements(sf, infra.SNOWFLAKE_PAUSE); note(f"Pause: {sum(r['ok'] for r in res)}/{len(res)} ok")
                    st.dataframe(pd.DataFrame(res)[["sql", "ok", "message"]], hide_index=True)
                if st.button("Resume"):
                    res = infra.run_statements(sf, infra.SNOWFLAKE_RESUME); note(f"Resume: {sum(r['ok'] for r in res)}/{len(res)} ok")
                    st.dataframe(pd.DataFrame(res)[["sql", "ok", "message"]], hide_index=True)
        with e4:
            st.metric("Alert check", "manual")
            if st.button("Run alert check now"):
                try:
                    out = infra.invoke_alert_lambda(c.lam); note(f"Alert check: {out}"); st.json(out)
                except Exception as exc:
                    st.error(str(exc)[:300])

        st.subheader("Setup checklist")
        if st.button("Refresh checklist") or True:
            st.dataframe(pd.DataFrame(infra.setup_checklist(c, infra.bucket_name(c), sf)), hide_index=True)
        st.caption("Setup is not automated: it includes the Snowflake to AWS handshake (see docs/runbook.md). The checklist shows what is up.")

        with st.expander("Teardown (destructive)"):
            st.warning("Deletes the AWS stack: it empties the S3 bucket, then runs `terraform destroy` on infra/aws. "
                       "Snowflake objects are not deleted by the dashboard; run the SQL below yourself.")
            if st.button("Show what terraform would destroy"):
                with st.spinner("terraform plan -destroy ..."):
                    st.code(infra.terraform_plan_destroy()[-4000:])
            phrase = st.text_input(f'Type "{infra.DESTROY_PHRASE}" to enable the button')
            if st.button("Destroy AWS stack", disabled=phrase.strip() != infra.DESTROY_PHRASE):
                try:
                    with st.spinner("destroying ..."):
                        out = infra.terraform_destroy(phrase, s3=c.s3, bucket=infra.bucket_name(c))
                    st.success("Destroyed"); st.code(out); note("AWS stack destroyed")
                except Exception as exc:
                    st.error(str(exc)[:1500])
            st.code(infra.SNOWFLAKE_TEARDOWN_SQL, language="sql")
