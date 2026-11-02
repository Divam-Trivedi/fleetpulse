import random
from collections import Counter

from fleetpulse.behaviors import create_behavior


def make(name, **params):
    return create_behavior(name, params, random.Random(1))


def test_all_publishing():
    b = make("all_publishing")
    b.register("a", 0)
    assert all(b.is_active("a", t) for t in range(100))


def test_mixed_fleet_exact_percentages():
    b = make("mixed_fleet")
    for i in range(20):
        b.register(f"r{i}", 0)
    roles = Counter(b.role(f"r{i}") for i in range(20))
    assert roles == {"continuous": 12, "random": 6, "abrupt": 2}


def test_mixed_fleet_percentages_hold_after_scaling():
    b = make("mixed_fleet")
    for i in range(10):
        b.register(f"r{i}", 0)
    for i in range(10, 30):
        b.register(f"r{i}", 0)
    roles = Counter(b.role(f"r{i}") for i in range(30))
    assert roles == {"continuous": 18, "random": 9, "abrupt": 3}


def test_abrupt_stops_and_resets():
    b = make("mixed_fleet", continuous_pct=0, random_pct=0, abrupt_pct=100, abrupt_after_s=(10, 10))
    b.register("a", 0)
    assert b.is_active("a", 5)
    assert not b.is_active("a", 11)
    b.reset("a", 20)
    assert b.is_active("a", 25)
    assert not b.is_active("a", 31)


def test_mixed_fleet_rejects_bad_sum():
    import pytest
    with pytest.raises(ValueError):
        make("mixed_fleet", continuous_pct=50, random_pct=50, abrupt_pct=50)


def test_random_on_off_toggles():
    b = make("random_on_off", on_s=(5, 5), off_s=(5, 5), start_on_prob=1.0)
    b.register("a", 0)
    assert b.is_active("a", 1)
    assert not b.is_active("a", 6)
    assert b.is_active("a", 11)
