import subprocess
import time
import json
import threading
from typing import Any

# EVOLVE-BLOCK-START
PLANS: dict[str, dict[str, Any]] = {
    "1": {
        "node-a-8core": {
            "lanes": [
                [
                    {"name": "streamcluster", "threads": 4, "request_cpu": "3500m", "limit_cpu": "4000m", "request_memory": "6Gi", "limit_memory": "8Gi"},
                    {"name": "blackscholes", "threads": 4, "request_cpu": "3500m", "limit_cpu": "4000m", "request_memory": "6Gi", "limit_memory": "8Gi"},
                ],
                [
                    {"name": "canneal", "threads": 2, "request_cpu": "1800m", "limit_cpu": "2000m", "request_memory": "3Gi", "limit_memory": "4Gi"},
                    {"name": "radix", "threads": 4, "request_cpu": "3500m", "limit_cpu": "4000m", "request_memory": "6Gi", "limit_memory": "8Gi"},
                ],
            ],
        },
        "node-b-4core": {
            "lanes": [
                [
                    {"name": "freqmine", "threads": 4, "request_cpu": "3500m", "limit_cpu": "4000m", "request_memory": "3200Mi", "limit_memory": "3500Mi"},
                    {"name": "vips", "threads": 4, "request_cpu": "3500m", "limit_cpu": "4000m", "request_memory": "3200Mi", "limit_memory": "3500Mi"},
                    {"name": "barnes", "threads": 4, "request_cpu": "3500m", "limit_cpu": "4000m", "request_memory": "3200Mi", "limit_memory": "3500Mi"},
                ],
            ],
        },
    },
}
# EVOLVE-BLOCK-END


def launch_job(item):
    job = item["name"]
    node = item["node"]
    threads = item["threads"]

    if threads not in {1, 2, 3, 4, 6}:
        raise ValueError(f"Invalid threads: {threads} for {job}")
    
    splash2x_jobs = {"barnes", "radix"}

    if job in splash2x_jobs:
        image = f"anakli/cca:splash2x_{job}"
        cmd_suite = "splash2x"
    else:
        image = f"anakli/cca:parsec_{job}"
        cmd_suite = "parsec"

    yaml_content = f"""\
apiVersion: batch/v1
kind: Job
metadata:
  name: {job}
spec:
  template:
    spec:
      nodeSelector:
        cca-project-nodetype: "{node}"
      containers:
      - name: {job}
        image: {image}
        command: ["./run", "-a", "run", "-S", "{cmd_suite}",
                  "-p", "{job}", "-i", "native", "-n", "{threads}"]
        resources:
          requests:
            cpu: "{item['request_cpu']}"
            memory: "{item['request_memory']}"
          limits:
            cpu: "{item['limit_cpu']}"
            memory: "{item['limit_memory']}"
      restartPolicy: Never
"""
    
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=yaml_content, text=True, capture_output=True
    )
    print(f"Launched {job} on {node} with {threads} threads: {result.stdout.strip()}")

def wait_for_jobs(jobs, timeout = 900):
    start_time = time.time()
    while time.time() - start_time < timeout:
        result = subprocess.run(
            ["kubectl", "get", "jobs", "-o", "json"],
            capture_output=True, text=True
        )
        data = json.loads(result.stdout)
        done = set()
        for i in data["items"]:
            name = i["metadata"]["name"]
            if name in jobs and i["status"].get("completionTime"):
                done.add(name)
        if done == set(jobs):
            print("All jobs completed.")
            return
        time.sleep(10)
    print(f"Timeout waiting for jobs: {set(jobs) - done} did not complete.")

def parse_duration_to_seconds(t):
    parts = t.strip().split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + int(float(s))
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + int(float(s))
    return float(t)

def get_makespan():
    result = subprocess.run(
        ["kubectl", "get", "pods", "-o", "json"],
        capture_output=True, text=True
    )
    with open("result.json", "w") as f:
        f.write(result.stdout)

    output = subprocess.run(
        ["python3", "../get_time.py", "result.json"],
        capture_output=True, text=True
    )
    print(output.stdout)
    print(output.stderr)

    job_times = {}
    current_job = None
    total_time = 9999

    for line in output.stdout.splitlines():
        line = line.strip()

        if line.startswith("Job:"):
            current_job = line.split("Job:", 1)[1].strip()

        elif line.startswith("Job time:") and current_job and current_job != "memcached":
            t = line.split("Job time:", 1)[1].strip()
            job_times[current_job] = parse_duration_to_seconds(t)

        elif line.startswith("Total time:"):
            t = line.split("Total time:", 1)[1].strip()
            total_time = parse_duration_to_seconds(t)

    with open("job_times.json", "w") as f:
        json.dump(
            {
                "job_times": job_times,
                "mean_batch_job_time": sum(job_times.values()) / len(job_times) if job_times else 9999,
                "total_time": total_time,
            },
            f,
            indent=2
        )

    return total_time

def run_lane(lane, node):
    for item in lane:
        item = dict(item)
        item["node"] = node
        launch_job(item)
        wait_for_jobs({item["name"]})

if __name__ == "__main__":
    subprocess.run(["kubectl", "delete", "jobs", "--all", "--ignore-not-found=true"], capture_output=True)
    result = subprocess.run(["kubectl", "get", "pods", "--no-headers", "-o", "custom-columns=NAME:.metadata.name"], capture_output=True, text=True)
    for pod in result.stdout.strip().splitlines():
        if "memcached" not in pod:
            subprocess.run(["kubectl", "delete", "pod", pod, "--ignore-not-found=true"], capture_output=True)
    time.sleep(5)

    plan = PLANS["1"]
    threads = []
    for node, node_cfg in plan.items():
        for lane in node_cfg["lanes"]:
            t = threading.Thread(target=run_lane, args=(lane, node))
            threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    makespan = get_makespan()
    with open("makespan.txt", "w") as f:
        f.write(str(makespan))
    print(f"Makespan: {makespan}s")