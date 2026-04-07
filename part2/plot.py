import os
import re
import numpy as np
import matplotlib.pyplot as plt

# Update these constants to match your environment
BASE_DIR = "part2/part2b_results" 
WORKLOADS = ["barnes", "blackscholes", "canneal", "freqmine", "radix", "streamcluster", "vips"]
THREADS = [1, 2, 4, 8]

def parse_execution_time(filepath):
    """Extracts 'real' time from PARSEC output and converts to seconds."""
    with open(filepath, "r") as f:
        content = f.read()
        # Matches 'real 0m43.521s' format 
        match = re.search(r"real\s+(?:(\d+)m)?([\d.]+)s", content)
        if match:
            minutes = int(match.group(1))
            seconds = float(match.group(2))
            return minutes * 60 + seconds
    return None

def get_speedup_data():
    all_data = {}
    for wl in WORKLOADS:
        times = {}
        for t in THREADS:
            # Matches filename pattern: parsec-workload_Xthread.txt
            filename = f"parsec-{wl}_{t}thread.txt"
            filepath = os.path.join(BASE_DIR, filename)
            
            if os.path.exists(filepath):
                exec_time = parse_execution_time(filepath)
                if exec_time is not None:
                    times[t] = exec_time
        
        if 1 in times:
            t1 = times[1]
            # Speedup = Time1 / Timen 
            all_data[wl] = {t: t1 / times[t] for t in times}
        else:
            print(f"[WARN] Missing data for {wl}: {times.keys()}")
    return all_data

def plot_parallel_behavior(speedup_results):
    plt.figure(figsize=(10, 7))
    markers = ['o', '^', 's', 'x', 'D', 'v', '*']
    
    # Ideal linear scaling reference
    plt.plot(THREADS, THREADS, 'k--', label="Ideal (Linear)", alpha=0.7)

    for idx, wl in enumerate(WORKLOADS):
        if wl in speedup_results:
            data = speedup_results[wl]
            x_vals = sorted(data.keys())
            y_vals = [data[x] for x in x_vals]
            plt.plot(x_vals, y_vals, marker=markers[idx], label=wl, linewidth=2)

    plt.xlabel("Number of Threads")
    plt.ylabel("Speedup (Time_1 / Time_n)")
    plt.title("PARSEC Workload Scalability")
    plt.xticks(THREADS)
    plt.ylim(0, max(THREADS) + 1)
    plt.grid(True, which='both', linestyle='--', alpha=0.5)
    plt.legend()
    
    plt.savefig("part2/part2_plot.png", dpi=300)

if __name__ == "__main__":
    results = get_speedup_data()
    plot_parallel_behavior(results)

    print("\n--- Workload Speedup Ranking by Thread ---")
    for t in THREADS:
        current_ranking = []
        for wl, data in results.items():
            if t in data:
                current_ranking.append((wl, data[t]))
        
        current_ranking.sort(key=lambda x: x[1], reverse=True)
        
        formatted_list = [f"{wl}({val:.2f})" for wl, val in current_ranking]
        print(f"{t:1d} thread: {', '.join(formatted_list)}")