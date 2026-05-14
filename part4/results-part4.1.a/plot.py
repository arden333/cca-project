import os
import numpy as np
import matplotlib.pyplot as plt

BASE_DIR = "./"
CONFIGS = [
    "t-1_c-1",
    "t-1_c-2",
    "t-1_c-3",
    "t-2_c-1",
    "t-2_c-2",
    "t-2_c-3",
    "t-3_c-1",
    "t-3_c-2",
    "t-3_c-3",
]
MARKERS = ["o", "^", "s", "x", "D", "v", "*"]

LABELS = {
    "t-1_c-1": "$T=1$, $C=1$",
    "t-1_c-2": "$T=1$, $C=2$",
    "t-1_c-3": "$T=1$, $C=3$",
    "t-2_c-1": "$T=2$, $C=1$",
    "t-2_c-2": "$T=2$, $C=2$",
    "t-2_c-3": "$T=2$, $C=3$",
    "t-3_c-1": "$T=3$, $C=1$",
    "t-3_c-2": "$T=3$, $C=2$",
    "t-3_c-3": "$T=3$, $C=3$",
}


def parse_file(filepath):
    qps_list = []
    p95_list = []

    with open(filepath, "r") as f:
        for line in f:
            if line.startswith("read"):
                parts = line.split()
                try:
                    p95 = float(parts[13])
                    qps = float(parts[-4])
                    qps_list.append(qps)
                    p95_list.append(p95)
                except:
                    continue

    return qps_list, p95_list


def aggregate_config(config):
    files = sorted(
        [f for f in os.listdir(BASE_DIR) if f.startswith(f"result_{config}_")]
    )

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
            # marker=MARKERS[idx],
            capsize=3,
            label=LABELS[config],
        )

    plt.xlim(0, 125000)
    plt.ylim(0, 6)

    plt.xlabel("Achieved QPS")
    plt.ylabel("95th Percentile Latency (ms)")
    plt.title(
        "Memcached Performance with different core ($C$) and thread ($T$) allocations\n(Averaged over 3 runs)"
    )

    plt.legend(title="Configs")
    plt.grid(True)

    plt.savefig("./part4-1_plot.png", dpi=300)


if __name__ == "__main__":
    plot_all()
