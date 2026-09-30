"""Run a list of scenarios several times on the Claude models, in parallel, and summarize the results.

    python3 examples/claude-provider/batch.py run examples/claude-provider/qualification.txt \
        --out /tmp/batch --repeat 3 --jobs 4 --i-authorize-live-model-spend
    python3 examples/claude-provider/batch.py status /tmp/batch

`run` starts only the runs still missing from --out: each scenario needs --repeat finished runs (a
result.json), so after a container restart the same command picks up where the batch stopped. Runs that
were killed mid-way leave no result.json and are started again. `--fake` runs the scripted model instead
(no spend), to rehearse the batch. See CLOUD-SESSION.md.
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROFILE = "claude-tiers"


def scenarios(path: Path) -> list[str]:
    """Scenario ids from a list file: one per line; blank lines and # comments are ignored."""
    lines = (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
    return [line for line in lines if line]


def runs(out: Path) -> dict[str, list[Path]]:
    """Evidence directories under --out by scenario. The harness names them <stamp>-<scenario>-<label>-<random>."""
    found = collections.defaultdict(list)
    for directory in sorted(out.glob("*/")) if out.is_dir() else []:
        name = directory.name.split("-", 1)[-1]
        for label in (f"-{PROFILE}-", "-fake-"):
            if label in name:
                found[name.rsplit(label, 1)[0]].append(directory)
    return found


def missing(ids: list[str], out: Path, repeat: int) -> list[str]:
    """One entry per run still to start: `repeat` finished runs per scenario, minus those already finished."""
    done = {scenario: sum((d / "result.json").is_file() for d in dirs) for scenario, dirs in runs(out).items()}
    return [scenario for scenario in ids for _ in range(max(0, repeat - done.get(scenario, 0)))]


def cost(directory: Path) -> float:
    """Model spend of one run, from the cost the example wrapper writes into each stage's event log."""
    total = 0.0
    for log in directory.glob("project/.autocode/runs/**/*.jsonl"):
        for line in log.read_text(errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") in ("turn.completed", "turn.failed"):
                total += event.get("cost_usd") or 0
    return total


def status(out: Path) -> str:
    rows, verdicts, running, spent = [], collections.Counter(), 0, 0.0
    for scenario, dirs in sorted(runs(out).items()):
        cells = []
        for directory in dirs:
            spent += (money := cost(directory))
            if not (directory / "result.json").is_file():
                running += 1
                cells.append("running")
                continue
            result = json.loads((directory / "result.json").read_text())
            checks = result.get("checks") or []
            verdicts[result["verdict"]] += 1
            cells.append(f"{result['verdict']} {sum(c['ok'] for c in checks)}/{len(checks)} "
                         f"{int(result.get('wall_seconds') or 0)}s ${money:.2f}")
        rows.append(f"{scenario:<30} " + " | ".join(cells))
    summary = ", ".join(f"{count} {verdict}" for verdict, count in sorted(verdicts.items()))
    return "\n".join([f"finished {sum(verdicts.values())} ({summary or 'none'}), running {running}, "
                      f"cost ${spent:.2f}"] + rows)


def launch(scenario: str, args, index: int) -> int:
    command = [sys.executable, str(HERE / "trial.py"), "run", scenario, "--out", str(args.out),
               "--timeout-minutes", str(args.timeout_minutes)]
    command += ["--fake"] if args.fake else ["--profile", PROFILE, "--i-authorize-live-model-spend"]
    log = args.out / "logs" / f"{scenario}-{index}.log"
    with log.open("w") as handle:
        return subprocess.run(command, cwd=REPO, stdout=handle, stderr=subprocess.STDOUT).returncode


def run(args) -> int:
    if not args.fake and not args.i_authorize_live_model_spend:
        sys.exit("A live batch spends real money: add --i-authorize-live-model-spend (or --fake to rehearse)")
    todo = missing(scenarios(args.list), args.out, args.repeat)
    (args.out / "logs").mkdir(parents=True, exist_ok=True)
    print(f"{len(todo)} run(s) to start, {args.jobs} at a time", flush=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(lambda item: launch(item[1], args, item[0]), enumerate(todo, start=1)))
    print(status(args.out))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("run", help="start the runs still missing from --out, then print the status")
    start.add_argument("list", type=Path, help="file with one scenario id per line")
    start.add_argument("--out", type=Path, required=True, help="evidence directory (outside the repository)")
    start.add_argument("--repeat", type=int, default=3, help="finished runs wanted per scenario (default 3)")
    start.add_argument("--jobs", type=int, default=4, help="runs at a time (default 4)")
    start.add_argument("--timeout-minutes", type=int, default=60, help="per run (default 60)")
    start.add_argument("--fake", action="store_true", help="scripted model, no spend")
    start.add_argument("--i-authorize-live-model-spend", action="store_true")
    show = commands.add_parser("status", help="summarize the runs under a directory")
    show.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    if args.command == "status":
        print(status(args.out))
        return 0
    args.out = args.out.resolve()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
