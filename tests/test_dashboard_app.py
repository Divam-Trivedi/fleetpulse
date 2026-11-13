import ast
import os
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dashboard_fakes import FakeSF  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard import status  # noqa: E402
from dashboard.poller import Poller  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch):
    """These tests must never reach real AWS or Snowflake, whatever is configured on the machine running them."""
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
              "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD", "FLEETPULSE_START_PAGE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FLEETPULSE_AWS_PROFILE", "no-such-profile-for-tests")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent/aws-credentials")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent/aws-config")
    yield


def no_aws(profile, region):
    raise RuntimeError("AWS is not available in tests")


def live_app(**state):
    os.environ["FLEETPULSE_START_PAGE"] = "live"
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["poller"] = Poller(aws_factory=no_aws)          # injected: never builds a real AWS client
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def metrics(at):
    return {m.label: m.value for m in at.metric}


def page_text(at):
    return " ".join(str(e.value) for coll in (at.markdown, at.caption, at.info, at.success, at.warning) for e in coll)


def assert_clean(at):
    """Nothing technical should ever be printed on screen."""
    assert not at.exception
    assert len(at.get("help")) == 0
    assert "DeltaGenerator" not in page_text(at) and "LockedCursor" not in page_text(at)


def test_app_has_no_bare_expressions_that_streamlit_would_print():
    """Streamlit prints the value of any bare expression (for example `st.x() if a else st.y()` used as a statement)."""
    tree = ast.parse(Path(APP).read_text())
    bad = []
    for node in ast.walk(tree):
        for attr in ("body", "orelse", "finalbody"):
            block = getattr(node, attr, None)
            if not isinstance(block, list):
                continue
            for i, stmt in enumerate(block):
                if isinstance(stmt, ast.Expr) and not isinstance(stmt.value, ast.Call):
                    is_docstring = i == 0 and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)
                    if not is_docstring:
                        bad.append(stmt.lineno)
    assert not bad, f"bare expressions at lines {bad}"


def test_demo_page_renders():
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["poller"] = Poller(aws_factory=no_aws)
    at = at.run()
    assert_clean(at)
    assert "Data flow demo" in [t.value for t in at.title]


def test_live_page_without_cloud_is_plain_and_never_green():
    at = live_app()
    assert_clean(at)
    assert [t.label for t in at.tabs] == ["Dashboard", "Tables"]
    assert [s.value for s in at.subheader][:4] == ["Fleet", status.FLOW, status.AWS, status.SNOW]
    text = page_text(at)
    assert "Unknown: not connected to AWS" in text and "Unknown: not connected to Snowflake" in text
    assert status.LEGEND in text
    assert ":green[●]" not in text.replace(status.LEGEND, "")            # no status line is green when nothing is connected


def test_live_page_starts_simulator_and_shows_live_counts():
    at = live_app()
    next(b for b in at.button if b.label == "Start simulator").click()
    at.run()
    runner = at.session_state["runner"]
    try:
        assert not at.exception and runner.running
        time.sleep(0.6)
        at.poller = at.session_state["poller"]
        at.poller.poll_once()
        at.run()
        assert_clean(at)
        m = metrics(at)
        assert m["Robots"] == "20" and int(m["Active"]) + int(m["Silent"]) == 20 and m["At risk"] == "n/a"
        assert "Yes: the simulator in this app is running" in page_text(at)
    finally:
        runner.stop()


def test_live_page_from_snowflake_shows_counts_and_plain_top5_tables():
    at = live_app(sf=FakeSF(), sf_factory=lambda: FakeSF())
    poller = at.session_state["poller"]
    deadline = time.time() + 10
    while not poller.data().get("robots") is not None and time.time() < deadline:     # the background thread loads the tables
        time.sleep(0.1)
    at.run()
    assert_clean(at)
    m = metrics(at)
    assert (m["Robots"], m["Active"], m["Silent"], m["Faulted"], m["At risk"]) == ("6", "4", "2", "1", "2")
    assert "Snowflake, simulation run abcdef12" in page_text(at)
    assert ":green[●] **Compute (warehouse)**: running" in page_text(at)
    assert any(e.label == "Show all 6 rows" for e in at.expander)               # top 5 shown, the rest behind an expander
    robots = next(d for d in at.dataframe if "Robot" in d.value.columns and "Problem" in d.value.columns and len(d.value) == 5)
    assert robots.value.iloc[0]["Robot"] == "amr-005" and robots.value.iloc[0]["Problem"] == "Overheated"
    assert "motor_temp" not in " ".join(c for d in at.dataframe for c in d.value.columns)   # friendly headings, no raw column names
