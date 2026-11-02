"""Generate degradation scenarios: same fleet, different seeds, different robots wearing out.

    python scripts/gen_scenarios.py --seeds 21 22 23 24 --failing 5
"""
import argparse
import random
from pathlib import Path

import yaml


def build(seed: int, fleet_size: int, failing: int, lo: float, hi: float, hetero: bool = True) -> dict:
    rng = random.Random(seed)
    ids = [f"amr-{i:03d}" for i in range(fleet_size)]
    overrides: dict[str, dict] = {rid: {} for rid in ids}
    if hetero:  # hot zones and heavy loads: absolute temperature alone stops being a clean signal
        for rid in ids:
            overrides[rid].update(
                ambient_temp=round(rng.uniform(20, 32), 1),
                motor_current_per_speed=round(rng.uniform(1.6, 2.4), 2),
                motor_idle_current=round(rng.uniform(0.8, 1.2), 2),
            )
    for rid in rng.sample(ids, failing):
        overrides[rid]["wear_rate"] = round(rng.uniform(lo, hi), 6)
    return {
        "seed": seed,
        "time_scale": 100.0,
        "fleet": {"size": fleet_size, "defaults": {"wear_rate": 0.00002}, "overrides": overrides},
        "behavior": {"name": "all_publishing"},
        "sink": {"type": "jsonl", "path": f"data/scenario_{seed}.jsonl"},
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", type=int, nargs="+", required=True)
    p.add_argument("--fleet-size", type=int, default=30)
    p.add_argument("--failing", type=int, default=5)
    p.add_argument("--wear-min", type=float, default=0.00010)
    p.add_argument("--wear-max", type=float, default=0.00030)
    p.add_argument("--homogeneous", action="store_true", help="identical robots (no hot zones / loads)")
    p.add_argument("--out-dir", default="configs/scenarios")
    a = p.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for seed in a.seeds:
        path = out / f"scenario_{seed}.yaml"
        path.write_text(yaml.safe_dump(build(seed, a.fleet_size, a.failing, a.wear_min, a.wear_max, not a.homogeneous), sort_keys=False))
        print(path)


if __name__ == "__main__":
    main()
