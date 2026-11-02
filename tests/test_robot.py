import random

from fleetpulse.config import RobotParams
from fleetpulse.robot import Robot


def run(seed, steps=3000, **p):
    r = Robot("r", RobotParams(**p), random.Random(seed))
    return [r.step(1.0, 1_700_000_000 + i) for i in range(steps)]


def test_deterministic_with_seed():
    assert run(7, 200) == run(7, 200)


def test_values_stay_physical():
    for rec in run(3):
        assert 0 <= rec.battery_pct <= 100
        assert rec.motor_current >= 0
        assert rec.speed >= 0


def test_battery_cycle_reaches_charging():
    statuses = {r.status for r in run(1, 6000, initial_battery_pct=25)}
    assert {"moving", "returning", "charging"} <= statuses


def test_high_wear_runs_hotter():
    healthy = run(5, 2000, wear_rate=0, noise_std=0)
    worn = run(5, 2000, wear_rate=0.001, noise_std=0)
    assert max(r.motor_temp for r in worn) > max(r.motor_temp for r in healthy) + 5
