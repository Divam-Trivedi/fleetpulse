import asyncio

from fleetpulse import FleetController, load_config
from fleetpulse.sinks import NullSink


def make(n=10, behavior="all_publishing", **params):
    cfg = load_config()
    cfg.seed, cfg.time_scale, cfg.fleet.size = 1, 100.0, n
    cfg.behavior.name, cfg.behavior.params = behavior, params
    return FleetController(cfg, NullSink())


def test_run_publishes():
    ctl = make(5)
    asyncio.run(ctl.run(duration_s=20))
    assert ctl.get_status()["total_published"] > 50


def test_runtime_control():
    async def scenario():
        ctl = make(5)
        await ctl.start()
        await asyncio.sleep(0.05)
        ctl.scale_fleet(12)
        assert ctl.get_status()["fleet_size"] == 12
        ctl.scale_fleet(3)
        assert ctl.get_status()["fleet_size"] == 3
        ctl.set_behavior("mixed_fleet")
        assert ctl.get_status()["behavior"]["name"] == "mixed_fleet"
        ctl.update_params("amr-001", publish_hz=2.0)
        ctl.update_params(None, max_speed=2.0)
        robots = {r["id"]: r for r in ctl.get_status()["robots"]}
        assert robots["amr-001"]["params"]["publish_hz"] == 2.0
        assert robots["amr-000"]["params"]["max_speed"] == 2.0
        await ctl.stop()
    asyncio.run(scenario())


def test_mixed_fleet_silences_abrupt_robots():
    ctl = make(10, "mixed_fleet", abrupt_after_s=[5, 5])
    asyncio.run(ctl.run(duration_s=60))
    st = ctl.get_status()
    dead = [r for r in st["robots"] if r["role"] == "abrupt"]
    assert len(dead) == 1 and not dead[0]["active"]


def test_records_carry_run_id():
    from fleetpulse.sinks import Sink

    class Capture(Sink):
        def __init__(self): self.rows = []
        async def write(self, record): self.rows.append(record)

    cfg = load_config()
    cfg.seed, cfg.time_scale, cfg.fleet.size = 1, 100.0, 3
    cap = Capture()
    ctl = FleetController(cfg, cap)
    asyncio.run(ctl.run(duration_s=10))
    assert cap.rows and {r["run_id"] for r in cap.rows} == {ctl.run_id}
