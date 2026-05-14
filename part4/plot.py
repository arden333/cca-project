import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import matplotlib.patches as mpatches
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import urllib.parse
import re

# Precise color mapping
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


@dataclass
class CPULoad:
    ts: datetime
    load: list[float]


@dataclass
class SchedulerEvent:
    ts: datetime
    job_name: str
    cores: list[int]
    etype: str


@dataclass
class MemcachedMetric:
    p95_us: float
    qps: float


def parse_mcperf(filepath: Path):
    metrics = []
    if not filepath.exists():
        return metrics
    for line in filepath.read_text().splitlines():
        if not line.startswith("read"):
            continue
        parts = line.split()
        try:
            metrics.append(MemcachedMetric(float(parts[12]), float(parts[16])))
        except (ValueError, IndexError):
            continue
    return metrics


def parse_jobs_advanced(path: Path):
    load_values = []
    global_start = global_end = jobs_finished_ts = None
    raw_events = []

    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        parts = line.split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            ts = datetime.fromisoformat(parts[0])
            if global_start is None:
                global_start = ts
            global_end = ts
            raw_events.append({"ts": ts, "type": parts[1], "content": parts[2]})
        except ValueError:
            continue

    if not raw_events:
        return {}, [], None, None, [], {}, None

    job_cores, job_current_cores, visual_events, pending_unpauses = {}, {}, [], {}
    job_life_cycle = {}

    for event in raw_events:
        ts, etype, content = event["ts"], event["type"], event["content"]

        if "Jobs+Finished" in content:
            jobs_finished_ts = ts

        if etype == "start":
            parts = content.split()
            job_name = parts[0]
            if job_name == "scheduler":
                continue
            cores = json.loads(parts[1]) if len(parts) > 1 else []
            job_cores[job_name] = {c: [(ts, None, "active")] for c in cores}
            job_current_cores[job_name] = set(cores)
            job_life_cycle[job_name] = {
                "start": ts,
                "end": None,
                "reported_elapsed": 0.0,
            }

        elif etype == "custom":
            msg = urllib.parse.unquote_plus(content)
            if "elapsed" in msg:
                job_name = msg.split()[0]
                match = re.search(r"elapsed=(\d+\.?\d*)s", msg)
                if match and job_name in job_life_cycle:
                    job_life_cycle[job_name]["reported_elapsed"] = float(match.group(1))
                    job_life_cycle[job_name]["end"] = ts

            if "load" in msg:
                try:
                    idx = msg.find("[")
                    load_values.append(
                        CPULoad(ts, json.loads(msg[idx : msg.rfind("]") + 1])[:4])
                    )
                except:
                    pass

        elif etype == "update_cores":
            parts = content.split(maxsplit=1)
            job_name, new_cores = parts[0], set(json.loads(parts[1]))
            if job_name in pending_unpauses:
                visual_events.append(
                    SchedulerEvent(
                        pending_unpauses.pop(job_name),
                        job_name,
                        list(new_cores),
                        "unpause",
                    )
                )

            if job_name not in job_cores:
                job_cores[job_name] = {}
            for core in job_current_cores.get(job_name, set()) - new_cores:
                if (
                    core in job_cores[job_name]
                    and job_cores[job_name][core][-1][1] is None
                ):
                    s, _, t = job_cores[job_name][core][-1]
                    job_cores[job_name][core][-1] = (s, ts, t)
            for core in new_cores - job_current_cores.get(job_name, set()):
                if core not in job_cores[job_name]:
                    job_cores[job_name][core] = []
                job_cores[job_name][core].append((ts, None, "active"))
            job_current_cores[job_name] = new_cores

        elif etype == "pause":
            job_name = content.strip()
            visual_events.append(
                SchedulerEvent(
                    ts, job_name, list(job_current_cores.get(job_name, [])), "pause"
                )
            )
            if job_name in job_cores:
                for core in job_cores[job_name]:
                    if (
                        job_cores[job_name][core]
                        and job_cores[job_name][core][-1][1] is None
                    ):
                        s, _, t = job_cores[job_name][core][-1]
                        job_cores[job_name][core][-1] = (s, ts, t)
            job_current_cores[job_name] = set()

        elif etype == "unpause":
            pending_unpauses[content.strip()] = ts

        elif etype == "end":
            job_name = content.strip()
            if job_name in job_cores:
                for core in job_cores[job_name]:
                    if (
                        job_cores[job_name][core]
                        and job_cores[job_name][core][-1][1] is None
                    ):
                        s, _, t = job_cores[job_name][core][-1]
                        job_cores[job_name][core][-1] = (s, ts, t)
            job_current_cores[job_name] = set()

    core_allocs = {i: [] for i in range(4)}
    core_allocs[0].append(
        {"start": global_start, "end": global_end, "job": "memcached"}
    )
    for j_name, c_data in job_cores.items():
        for c_id, intervals in c_data.items():
            for s_ts, e_ts, _ in intervals:
                core_allocs[c_id].append(
                    {"start": s_ts, "end": e_ts or global_end, "job": j_name}
                )

    return (
        core_allocs,
        load_values,
        global_start,
        global_end,
        visual_events,
        job_life_cycle,
        jobs_finished_ts,
    )


def plot_everything(log_file, mc_file):
    core_allocs, load_data, g_start, g_end, vis_events, job_life, exp_end_ts = (
        parse_jobs_advanced(Path(log_file))
    )
    mc_metrics = parse_mcperf(Path(mc_file))
    if g_start is None:
        return

    full_duration = (g_end - g_start).total_seconds()
    exp_duration = (
        (exp_end_ts - g_start).total_seconds() if exp_end_ts else full_duration
    )

    # Calculate SLO Miss Rate
    # Only consider samples between 0s and exp_duration
    active_metrics = [m for i, m in enumerate(mc_metrics) if (i * 15.0) <= exp_duration]
    slo_misses = [m for m in active_metrics if m.p95_us > 800.0]
    miss_rate = (len(slo_misses) / len(active_metrics)) * 100 if active_metrics else 0

    fig, axs = plt.subplots(3, 1, sharex=True)
    fig.set_figwidth(30)
    fig.set_figheight(15)

    # 1. CORE ALLOCATION
    ax_alloc = axs[0]
    ax_alloc.set_title("Core Allocations Over Time", fontsize=18, fontweight="bold")
    ax_alloc.set_yticks([0, 1, 2, 3])
    ax_alloc.set_ylim(-0.6, 3.6)

    bar_h = 0.8
    plotted_jobs = set()
    for cid in range(4):
        for alloc in core_allocs[cid]:
            s = (alloc["start"] - g_start).total_seconds()
            w = (alloc["end"] - g_start).total_seconds() - s
            job_name = alloc["job"]
            ax_alloc.broken_barh(
                [(s, w)],
                (cid - bar_h / 2, bar_h),
                facecolors=COLORS.get(job_name, "#ccc"),
                edgecolor="black",
                linewidth=0.8,
            )
            plotted_jobs.add(job_name)

    # Restore Pause/Unpause Markers
    for ev in vis_events:
        t_s = (ev.ts - g_start).total_seconds()
        color = COLORS.get(ev.job_name, "black")
        for cid in ev.cores:
            ax_alloc.vlines(
                t_s, cid - 0.4, cid + 0.4, colors=color, linewidth=3, alpha=0.9
            )
            ax_alloc.scatter(
                t_s,
                cid + 0.4 if ev.etype == "pause" else cid - 0.4,
                marker="v" if ev.etype == "pause" else "^",
                color=color,
                s=120,
                zorder=10,
            )

    legend_patches = [
        mpatches.Patch(color=COLORS.get(j, "#ccc"), label=j)
        for j in sorted(plotted_jobs)
    ]
    ax_alloc.legend(
        handles=legend_patches,
        loc="upper right",
        bbox_to_anchor=(1.05, 1),
        title="Workloads",
    )

    # 2. MEMCACHED PERFORMANCE
    ax1_left = axs[1]
    ax1_left.set_title(
        f"Memcached Performance",
        fontsize=18,
        fontweight="bold",
    )
    if mc_metrics:
        m_times = [i * 15.0 for i in range(len(mc_metrics))]
        m_qps = [m.qps for m in mc_metrics]
        m_p95_ms = [m.p95_us / 1000.0 for m in mc_metrics]

        ax1_left.plot(m_times, m_qps, color="#2ca02c", linewidth=2.5, label="QPS")
        ax1_left.set_ylabel("QPS", color="#2ca02c", fontsize=14, fontweight="bold")
        ax1_left.yaxis.set_major_formatter(
            FuncFormatter(lambda x, p: f"{x / 1000:.0f}K" if x >= 1000 else f"{x:.0f}")
        )

        ax1_right = ax1_left.twinx()
        ax1_right.plot(
            m_times, m_p95_ms, color="#1f77b4", linewidth=2.5, label="p95 Latency"
        )
        ax1_right.set_ylabel(
            "p95 Latency (ms)", color="#1f77b4", fontsize=14, fontweight="bold"
        )
        ax1_right.axhline(0.8, color="red", linestyle="--", label="0.8ms SLO")
        ax1_right.legend(loc="upper left")

    # 3. CPU UTILIZATION
    ax_load = axs[2]
    ax_load.set_title("Per Core CPU Utilization", fontsize=18, fontweight="bold")
    track_step = 130
    ax_load.set_ylim(-10, 4 * track_step)
    if load_data:
        l_times = [(l.ts - g_start).total_seconds() for l in load_data]
        for i in range(4):
            offset = i * track_step
            vals = [(l.load[i] if i < len(l.load) else 0) + offset for l in load_data]
            ax_load.plot(l_times, vals, linewidth=1.5)
            ax_load.axhline(
                offset, color="gray", linewidth=0.5, linestyle="--", alpha=0.4
            )

    yticks, yticklabels = [], []
    for i in range(4):
        base = i * track_step
        yticks.extend([base, base + 50, base + 100])
        yticklabels.extend([f"C{i}: 0%", "50%", "100%"])
    ax_load.set_yticks(yticks)
    ax_load.set_yticklabels(yticklabels, fontsize=10)

    plt.xlim(0, full_duration)
    if exp_end_ts:
        for ax in axs:
            ax.axvline(exp_duration, color="red", linestyle=":", alpha=0.6)

    ax_load.set_xlabel("Time (s)")
    plt.tight_layout()
    plt.savefig("plot4.3-run-1.png", dpi=600)
    plt.show()

    # --- SUMMARY TABLE ---
    print("\n" + "=" * 85)
    print(
        f"{'Job Name':<15} | {'Start (s)':<10} | {'End (s)':<10} | {'Reported (s)':<15}"
    )
    print("-" * 85)
    for name, data in job_life.items():
        s_rel = (data["start"] - g_start).total_seconds()
        e_rel = (data["end"] - g_start).total_seconds() if data["end"] else exp_duration
        print(
            f"{name:<15} | {s_rel:<10.2f} | {e_rel:<10.2f} | {data['reported_elapsed']:<15.2f}"
        )
    print("-" * 85)
    print(f"Target Duration (Jobs Finished): {exp_duration:.2f} seconds")
    print(f"Total Visual Timeline:          {full_duration:.2f} seconds")
    print("-" * 85)
    print(
        f"SLO Miss Rate (p95 > 0.8ms):    {miss_rate:.2f}% ({len(slo_misses)}/{len(active_metrics)} samples)"
    )
    print("=" * 85)


if __name__ == "__main__":
    plot_everything("./results-part4.4/jobs_3.txt", "./results-part4.4/mcperf_3.txt")
