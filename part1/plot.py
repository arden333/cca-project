import os
import numpy as np
import matplotlib.pyplot as plt

BASE_DIR = "part1/raw_results"
CONFIGS = ["no_interference", "cpu", "l1d", "l1i", "l2", "llc", "membw"]
MARKERS = ['o', '^', 's', 'x', 'D', 'v', '*']

def parse_file(filepath):
    qps_list = []
    p95_list = []

    with open(filepath, "r") as f:
        for line in f:
            if line.startswith("read"):
                parts = line.split()
                try:
                    p95 = float(parts[11])
                    qps = float(parts[-2])
                    qps_list.append(qps)
                    p95_list.append(p95)
                except:
                    continue

    return qps_list, p95_list


def aggregate_config(config):
    files = sorted([
        f for f in os.listdir(BASE_DIR)
        if f.startswith(f"result_{config}_")
    ])

    runs_qps = {}
    runs_p95 = {}

    for i, f in enumerate(files):
        qps, p95 = parse_file(os.path.join(BASE_DIR, f))
        runs_qps[f"run{i}"] = qps
        runs_p95[f"run{i}"] = p95

    n_points = len(runs_qps["run0"])

    mean_qps = []
    std_qps = []
    mean_p95 = []
    std_p95 = []

    for i in range(n_points):
        qps_vals = [runs_qps[r][i] for r in runs_qps]
        p95_vals = [runs_p95[r][i] for r in runs_p95]

        mean_qps.append(np.mean(qps_vals))
        std_qps.append(np.std(qps_vals))

        mean_p95.append(np.mean(p95_vals))
        std_p95.append(np.std(p95_vals))

    return np.array(mean_qps), np.array(std_qps), np.array(mean_p95), np.array(std_p95)


def plot_all():
    plt.figure(figsize=(10, 6))

    for idx, config in enumerate(CONFIGS):
        mean_qps, std_qps, mean_p95, std_p95 = aggregate_config(config)

        mean_p95 /= 1000
        std_p95 /= 1000

        plt.errorbar(
            mean_qps,
            mean_p95,
            xerr=std_qps,
            yerr=std_p95,
            marker=MARKERS[idx],
            capsize=3,
            label=config
        )

    plt.xlim(0, 80000)
    plt.ylim(0, 6)

    plt.xlabel("Achieved QPS")
    plt.ylabel("95th Percentile Latency (ms)")
    plt.title("Memcached Performance under Different Interference Types\n(Averaged over 3 runs)")

    plt.legend(title="Configs")
    plt.grid(True)

    plt.savefig("part1/part1_plot.png", dpi=300)


if __name__ == "__main__":
    plot_all()