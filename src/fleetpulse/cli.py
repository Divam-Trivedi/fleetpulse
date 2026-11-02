from __future__ import annotations

import argparse
import asyncio
import sys

import yaml

from .behaviors import BEHAVIORS
from .config import load_config
from .controller import FleetController


def _parse_params(items: list[str]) -> dict:
    out = {}
    for item in items:
        key, _, value = item.partition("=")
        out[key.strip()] = yaml.safe_load(value)
    return out


async def _status_loop(ctl: FleetController, every_s: float) -> None:
    while True:
        await asyncio.sleep(every_s)
        s = ctl.get_status()
        print(
            f"[t={s['sim_time_s']:>7.1f}s] behavior={s['behavior']['name']} "
            f"active={s['active_count']}/{s['fleet_size']} published={s['total_published']}",
            file=sys.stderr,
        )


async def _run(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    if args.robots is not None:
        cfg.fleet.size = args.robots
    if args.seed is not None:
        cfg.seed = args.seed
    if args.time_scale is not None:
        cfg.time_scale = args.time_scale
    if args.behavior:
        cfg.behavior.name = args.behavior
        cfg.behavior.params = {}
    if args.param:
        cfg.behavior.params.update(_parse_params(args.param))
    if args.sink:
        cfg.sink.type = args.sink
    if args.out:
        cfg.sink.path = args.out
    if args.stream_name:
        cfg.sink.stream_name = args.stream_name
    if args.region:
        cfg.sink.region = args.region
    if args.profile:
        cfg.sink.profile = args.profile

    ctl = FleetController(cfg)
    status = asyncio.create_task(_status_loop(ctl, args.status_every)) if args.status_every else None
    try:
        await ctl.run(args.duration)
    finally:
        if status:
            status.cancel()
        s = ctl.get_status()
        print(f"Done. Published {s['total_published']} records.", file=sys.stderr)
        stats = getattr(ctl.sink, "stats", None)
        if stats:
            print(f"Sink stats: {stats}", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="fleetpulse", description="Robot fleet telemetry simulator")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run the simulator")
    run.add_argument("-c", "--config", help="YAML config file")
    run.add_argument("-n", "--robots", type=int, help="fleet size")
    run.add_argument("-b", "--behavior", choices=sorted(BEHAVIORS))
    run.add_argument("-p", "--param", action="append", default=[], metavar="KEY=VAL",
                     help="behavior param, repeatable (e.g. -p continuous_pct=50)")
    run.add_argument("-d", "--duration", type=float, help="simulated seconds (default: until Ctrl-C)")
    run.add_argument("-t", "--time-scale", type=float, help="sim seconds per real second")
    run.add_argument("-s", "--sink", choices=["stdout", "jsonl", "null", "firehose"])
    run.add_argument("-o", "--out", help="output path for jsonl sink")
    run.add_argument("--stream-name", help="Firehose delivery stream name")
    run.add_argument("--region")
    run.add_argument("--profile", help="AWS CLI profile")
    run.add_argument("--seed", type=int)
    run.add_argument("--status-every", type=float, default=5.0, help="real seconds between status lines (0=off)")

    sub.add_parser("behaviors", help="list available behaviors")

    args = p.parse_args(argv)
    if args.cmd == "behaviors":
        for name, cls in sorted(BEHAVIORS.items()):
            print(f"{name}: {cls.__doc__.strip().splitlines()[0]}")
            for field, info in cls.params_model.model_fields.items():
                print(f"    {field} (default {info.default})")
        return
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
