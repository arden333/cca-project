
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import matplotlib.pyplot as plt
from matplotlib.patches import Patch


RESULTS_ROOT = Path("part_3_1_results_group_030")
SLO_MS = 1.0
MEMCACHED_NODE = "node-a-8core"
MEMCACHED_CORES = "0-1"

JOB_ORDER = [
    "barnes",
    "blackscholes",
    "canneal",
    "freqmine",
    "radix",
    "streamcluster",
    "vips",
]

COLORS = {
    "memcached": "#7f7f7f",
    "barnes": "#AACCCA",
    "blackscholes": "#CCA000",
    "canneal": "#CCCCAA",
    "freqmine": "#0CCA00",
    "radix": "#00CCA0",
    "streamcluster": "#CCACCA",
    "vips": "#CC0A00",
}

NODE_CORE_COUNTS = {
    "node-a-8core": 8,
    "node-b-4core": 4,
}


@dataclass
class JobRun:
    run_idx: int
    workload: str
    pod: str
    node_name: str
    node_label: str
    cores: list[int]
    started_at: datetime
    finished_at: datetime

    @property
    def execution_time_s(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


@dataclass
class McperfPoint:
    p95_us: float
    qps: float
    target_qps: float
    ts_start_ms: int
    ts_end_ms: int

    @property
    def start_s_epoch(self) -> float:
        return self.ts_start_ms / 1000.0

    @property
    def end_s_epoch(self) -> float:
        return self.ts_end_ms / 1000.0

    @property
    def width_s(self) -> float:
        return max(0.0, self.end_s_epoch - self.start_s_epoch)


def parse_k8s_time(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def datetime_to_epoch_s(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def node_label_from_name(node_name: str) -> str:
    if node_name.startswith("node-a-8core"):
        return "node-a-8core"
    if node_name.startswith("node-b-4core"):
        return "node-b-4core"
    return node_name


def expand_cores(core_spec: str) -> list[int]:
    cores: list[int] = []
    for part in core_spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            cores.extend(range(int(lo), int(hi) + 1))
        else:
            cores.append(int(part))
    return sorted(set(cores))


def extract_taskset_cores(args: list[str]) -> list[int]:
    cmd = " ".join(args)
    match = re.search(r"taskset\s+-c\s+([^\s]+)", cmd)
    return expand_cores(match.group(1)) if match else []


def discover_result_dirs(root: Path) -> list[Path]:
    """Return directories under root that contain pods_<i>.json files."""
    dirs = sorted({p.parent for p in root.rglob("pods_*.json")})
    return dirs


def run_indices(result_dir: Path) -> list[int]:
    indices: list[int] = []
    for path in result_dir.glob("pods_*.json"):
        match = re.fullmatch(r"pods_(\d+)\.json", path.name)
        if match and (result_dir / f"mcperf_{match.group(1)}.txt").exists():
            indices.append(int(match.group(1)))
    return sorted(indices)


def load_jobs(pods_path: Path, run_idx: int) -> list[JobRun]:
    data = json.loads(pods_path.read_text())
    jobs: list[JobRun] = []

    for pod in data.get("items", []):
        labels = pod.get("metadata", {}).get("labels", {})
        workload = labels.get("workload")
        if not workload:
            continue

        statuses = pod.get("status", {}).get("containerStatuses", [])
        if not statuses:
            continue

        terminated = statuses[0].get("state", {}).get("terminated", {})
        if "startedAt" not in terminated or "finishedAt" not in terminated:
            continue

        container = pod.get("spec", {}).get("containers", [{}])[0]
        node_name = pod.get("spec", {}).get("nodeName", "unknown")
        jobs.append(
            JobRun(
                run_idx=run_idx,
                workload=workload,
                pod=pod.get("metadata", {}).get("name", "unknown"),
                node_name=node_name,
                node_label=node_label_from_name(node_name),
                cores=extract_taskset_cores(container.get("args", [])),
                started_at=parse_k8s_time(terminated["startedAt"]),
                finished_at=parse_k8s_time(terminated["finishedAt"]),
            )
        )

    return sorted(jobs, key=lambda j: j.started_at)


def load_mcperf(mcperf_path: Path) -> list[McperfPoint]:
    points: list[McperfPoint] = []
    header: list[str] | None = None

    for raw in mcperf_path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#type"):
            header = line.lstrip("#").split()
            continue
        if line.startswith("#") or not line.startswith("read"):
            continue

        parts = line.split()
        if header and len(parts) >= len(header):
            row = dict(zip(header, parts))
            points.append(
                McperfPoint(
                    p95_us=float(row["p95"]),
                    qps=float(row["QPS"]),
                    target_qps=float(row["target"]),
                    ts_start_ms=int(float(row["ts_start"])),
                    ts_end_ms=int(float(row["ts_end"])),
                )
            )
        elif len(parts) >= 20:
            # type avg std min p5 p10 p50 p67 p75 p80 p85 p90 p95 p99 p999 p9999 QPS target ts_start ts_end
            points.append(
                McperfPoint(
                    p95_us=float(parts[12]),
                    qps=float(parts[16]),
                    target_qps=float(parts[17]),
                    ts_start_ms=int(float(parts[18])),
                    ts_end_ms=int(float(parts[19])),
                )
            )

    return points


def batch_window(jobs: list[JobRun]) -> tuple[datetime, datetime, float, float, float]:
    first = min(j.started_at for j in jobs)
    last = max(j.finished_at for j in jobs)
    first_epoch = datetime_to_epoch_s(first)
    last_epoch = datetime_to_epoch_s(last)
    makespan_s = last_epoch - first_epoch
    return first, last, first_epoch, last_epoch, makespan_s


def filter_mcperf_to_window(points: list[McperfPoint], first_epoch: float, last_epoch: float) -> list[McperfPoint]:
    return [p for p in points if p.end_s_epoch >= first_epoch and p.start_s_epoch <= last_epoch]


def slo_violation_ratio(points: list[McperfPoint], slo_ms: float) -> tuple[int, int, float]:
    threshold_us = slo_ms * 1000.0
    total = len(points)
    violations = sum(1 for p in points if p.p95_us > threshold_us)
    return violations, total, violations / total if total else math.nan


def make_core_lanes() -> list[tuple[str, int]]:
    lanes: list[tuple[str, int]] = []
    for node in ["node-a-8core", "node-b-4core"]:
        for core in range(NODE_CORE_COUNTS[node]):
            lanes.append((node, core))
    return lanes


def plot_run(
    result_dir: Path,
    run_idx: int,
    jobs: list[JobRun],
    mcperf_points: list[McperfPoint],
    slo_ms: float,
    memcached_node: str,
    memcached_cores: list[int],
) -> Path:
    first, _, first_epoch, _, makespan_s = batch_window(jobs)
    lanes = make_core_lanes()
    lane_index = {lane: i for i, lane in enumerate(lanes)}

    fig, (ax_lat, ax_core) = plt.subplots(
        2,
        1,
        figsize=(14, 7),
        sharex=True,
        gridspec_kw={"height_ratios": [2.0, 1.4]},
    )

    # Memcached p95 latency bars. Height is p95 latency; width is mcperf measurement interval.
    for p in mcperf_points:
        x = p.start_s_epoch - first_epoch
        ax_lat.bar(
            x,
            p.p95_us / 1000.0,
            width=p.width_s,
            align="edge",
            edgecolor="black",
            color="#4C78A8",
            linewidth=0.2,
            alpha=0.75,
        )

    ax_lat.axhline(slo_ms, linestyle="--", linewidth=1.2, label=f"SLO = {slo_ms:g} ms")
    ax_lat.set_ylabel("Memcached p95 latency [ms]")
    ax_lat.set_title(f"Run {run_idx}: memcached p95 latency and batch schedule")
    ax_lat.grid(True, axis="y", alpha=0.3)
    ax_lat.legend(loc="upper right")

    y_top = max([p.p95_us / 1000.0 for p in mcperf_points] + [slo_ms])
    for j in jobs:
        start = datetime_to_epoch_s(j.started_at) - first_epoch
        end = datetime_to_epoch_s(j.finished_at) - first_epoch
        mid = (start + end) / 2.0
        ax_lat.axvline(start, color=COLORS.get(j.workload, "black"), alpha=0.25, linewidth=0.8)
        ax_lat.axvline(end, color=COLORS.get(j.workload, "black"), alpha=0.25, linewidth=0.8)
        ax_lat.text(mid, y_top * 1.03, f"{j.workload}\n{j.node_label}", ha="center", va="bottom", fontsize=7)

    # Memcached placement across entire makespan.
    for core in memcached_cores:
        key = (memcached_node, core)
        if key in lane_index:
            ax_core.broken_barh(
                [(0, makespan_s)],
                (lane_index[key] - 0.4, 0.8),
                facecolors=COLORS["memcached"],
                edgecolors="black",
                linewidth=0.3,
            )

    # Batch job placement.
    for j in jobs:
        start = datetime_to_epoch_s(j.started_at) - first_epoch
        for core in j.cores:
            key = (j.node_label, core)
            if key not in lane_index:
                continue
            ax_core.broken_barh(
                [(start, j.execution_time_s)],
                (lane_index[key] - 0.4, 0.8),
                facecolors=COLORS.get(j.workload, "tab:gray"),
                edgecolors="black",
                linewidth=0.3,
            )

    ax_core.set_yticks(range(len(lanes)))
    ax_core.set_yticklabels([f"{node} c{core}" for node, core in lanes], fontsize=8)
    ax_core.set_xlabel("Time since first batch container start [s]")
    ax_core.set_ylabel("Core")
    ax_core.set_xlim(0, makespan_s)
    ax_core.grid(True, axis="x", alpha=0.3)

    legend_items = [Patch(facecolor=COLORS[name], label=name) for name in ["memcached", *JOB_ORDER]]
    ax_core.legend(handles=legend_items, loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=4, fontsize=8)

    fig.tight_layout()
    plot_path = result_dir / f"plot_{run_idx}.png"
    fig.savefig(plot_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return plot_path


def print_job_times(jobs: list[JobRun], title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    print(f"{'run':>3}  {'job':<14} {'node':<12} {'cores':<9} {'time [s]':>9}  {'startedAt':<25} {'finishedAt':<25}")
    for j in sorted(jobs, key=lambda x: (x.run_idx, x.started_at)):
        cores = ",".join(map(str, j.cores))
        print(
            f"{j.run_idx:>3}  {j.workload:<14} {j.node_label:<12} {cores:<9} "
            f"{j.execution_time_s:>9.3f}  {j.started_at.isoformat():<25} {j.finished_at.isoformat():<25}"
        )


def print_summary(all_jobs: list[JobRun], makespans: list[float]) -> None:
    values: dict[str, list[float]] = {name: [] for name in JOB_ORDER}
    for job in all_jobs:
        values.setdefault(job.workload, []).append(job.execution_time_s)

    print("\nReport table: execution time across runs")
    print("----------------------------------------")
    print(f"{'job name':<16} {'mean time [s]':>14} {'std [s]':>10}")
    for name in JOB_ORDER:
        vals = values.get(name, [])
        if not vals:
            print(f"{name:<16} {'missing':>14} {'missing':>10}")
            continue
        std = stdev(vals) if len(vals) >= 2 else 0.0
        print(f"{name:<16} {mean(vals):>14.3f} {std:>10.3f}")

    total_std = stdev(makespans) if len(makespans) >= 2 else 0.0
    print(f"{'total time':<16} {mean(makespans):>14.3f} {total_std:>10.3f}")


def print_slo(run_summaries: list[dict[str, Any]]) -> None:
    print("\nMemcached SLO violation ratio during batch window")
    print("-------------------------------------------------")
    print(f"{'run':>3} {'makespan [s]':>13} {'points':>8} {'violations':>11} {'ratio':>10}")
    for r in run_summaries:
        ratio = r["slo_violation_ratio"]
        ratio_s = "nan" if math.isnan(ratio) else f"{ratio:.6f}"
        print(f"{r['run']:>3} {r['makespan_s']:>13.3f} {r['mcperf_points']:>8} {r['violations']:>11} {ratio_s:>10}")


def analyze_result_dir(result_dir: Path) -> None:
    indices = run_indices(result_dir)
    if not indices:
        return

    print(f"\n=== Analyzing {result_dir} ===")
    all_jobs: list[JobRun] = []
    makespans: list[float] = []
    run_summaries: list[dict[str, Any]] = []
    memcached_cores = expand_cores(MEMCACHED_CORES)

    for idx in indices:
        pods_path = result_dir / f"pods_{idx}.json"
        mcperf_path = result_dir / f"mcperf_{idx}.txt"

        jobs = load_jobs(pods_path, idx)
        if not jobs:
            print(f"[WARN] No completed batch jobs found in {pods_path}")
            continue

        expected = set(JOB_ORDER)
        seen = {j.workload for j in jobs}
        missing = expected - seen
        extra = seen - expected

        if missing:
            print(f"[WARN] Run {idx}: missing jobs: {sorted(missing)}")
        if extra:
            print(f"[WARN] Run {idx}: unexpected jobs: {sorted(extra)}")

        mcperf = load_mcperf(mcperf_path)
        first, last, first_epoch, last_epoch, makespan_s = batch_window(jobs)
        mcperf_window = filter_mcperf_to_window(mcperf, first_epoch, last_epoch)
        violations, total_points, ratio = slo_violation_ratio(mcperf_window, SLO_MS)
        plot_path = plot_run(result_dir, idx, jobs, mcperf_window, SLO_MS, MEMCACHED_NODE, memcached_cores)

        all_jobs.extend(jobs)
        makespans.append(makespan_s)
        run_summaries.append(
            {
                "run": idx,
                "first_batch_start": first.isoformat(),
                "last_batch_end": last.isoformat(),
                "makespan_s": makespan_s,
                "mcperf_points": total_points,
                "violations": violations,
                "slo_violation_ratio": ratio,
                "plot": plot_path,
            }
        )
        print(f"[PLOT] {plot_path}")

    if not all_jobs:
        return

    print_job_times(all_jobs, "Per-job execution times")
    print_summary(all_jobs, makespans)
    print_slo(run_summaries)


def main() -> None:
    if not RESULTS_ROOT.exists():
        raise FileNotFoundError(f"Missing results directory: {RESULTS_ROOT}")

    dirs = discover_result_dirs(RESULTS_ROOT)
    if not dirs:
        raise FileNotFoundError("No pods_<i>.json files found under results/")

    for result_dir in dirs:
        analyze_result_dir(result_dir)


if __name__ == "__main__":
    main()
