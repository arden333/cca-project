import os
import numpy as np
import matplotlib.pyplot as plt

BASE_DIR = "./"
CORES = [1, 2, 3]
MAX_CPU = {1: 110, 2: 220, 3: 330}


def parse_qps(filepath):
    """Parse QPS file: returns list of (ts_start_s, p95_us, qps)."""
    rows = []
    with open(filepath, "r") as f:
        for line in f:
            if line.startswith("#") or not line.startswith("read"):
                continue
            parts = line.split()
            # try:
            p95 = float(parts[12])  # p95 column
            qps = float(parts[16])  # QPS column
            ts_start = int(parts[18])  # ts_start in milliseconds
            ts_start_s = ts_start // 1000
            rows.append((ts_start_s, p95, qps))
            # except (ValueError, IndexError):
            #     continue
    return rows


def parse_load(filepath):
    """Parse load file: returns dict {timestamp_s: cpu_usage_pct}."""
    load = {}
    with open(filepath, "r") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                ts = int(float(parts[0]))
                cpu = float(parts[1])
                load[ts] = cpu
            except (ValueError, IndexError):
                continue
    return load


def align_qps_with_load(qps_rows, load_dict):
    """
    Align QPS measurements with CPU load using timestamps.
    For each QPS row, find the CPU load at floor(ts_start_s).
    Returns (qps_list, p95_list, cpu_list).
    """
    qps_list = []
    p95_list = []
    cpu_list = []

    for ts_s, ts_end, p95, qps in qps_rows:
        ts_int = int(ts_s)
        cpu = np.sum([load_dict.get(ts_int + i, np.nan) for i in range(ts_end - ts_s)])
        cpu

        if np.isnan(cpu):
            # Try nearest second
            cpu = load_dict.get(ts_int - 1, np.nan)
        if not np.isnan(cpu):
            qps_list.append(qps)
            p95_list.append(p95)
            cpu_list.append(cpu)

    return np.array(qps_list), np.array(p95_list), np.array(cpu_list)


def plot_all():
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharey=True)

    for idx, c in enumerate(CORES):
        ax = axes[idx]
        qps_file = f"{c}-core-qps.txt"
        load_file = f"{c}-core-load.txt"

        qps_rows = parse_qps(qps_file)
        load_dict = parse_load(load_file)
        print(qps_rows)
        qps, p95, cpu = align_qps_with_load(qps_rows, load_dict)

        # Convert p95 from microseconds to milliseconds
        p95_ms = p95 / 1000.0

        # Plot p95 latency on left y-axis
        print(qps)
        ax.plot(qps, p95_ms, "b-o", markersize=3, label="P95 Latency", zorder=3)
        ax.set_xlabel("Achieved QPS")
        ax.set_ylabel("95th Percentile Latency (ms)", color="b")
        ax.tick_params(axis="y", labelcolor="b")
        ax.set_ylim(bottom=0)

        # Plot CPU utilization on right y-axis
        ax2 = ax.twinx()
        ax2.plot(qps, cpu, "r-s", markersize=3, label="CPU Utilization", zorder=3)
        ax2.set_ylabel(f"CPU Utilization (%)", color="r")
        ax2.tick_params(axis="y", labelcolor="r")
        ax2.set_ylim(0, MAX_CPU[c])

        ax.set_title(f"$C={c}$")
        ax.grid(True, alpha=0.3)

        ax.hlines(0.8, xmin=5000, xmax=125000, color="gray", linestyles="dotted")
        ax.set_xlim(5000, 125000)
        # Combine legends from both axes
        lines1, labels1 = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=8)

    fig.suptitle("Memcached Performance: Latency & CPU vs QPS")
    fig.tight_layout()
    plt.savefig("./part4-1d_plot.png", dpi=300)
    print("Plot saved to ./part4-1d_plot.png")


if __name__ == "__main__":
    plot_all()
