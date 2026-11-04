import time
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")


def test_app_renders_without_cloud_and_runs_simulator():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Data flow demo", "Live pipeline", "Simulator", "Infrastructure"]
    start = next(b for b in at.button if b.label == "Start simulator")
    start.click()
    at.run()
    runner = at.session_state["runner"]
    try:
        assert not at.exception and runner.running
        time.sleep(0.5)
        assert runner.status()["total_published"] > 0
    finally:
        runner.stop()
