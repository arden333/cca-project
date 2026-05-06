import subprocess
import time
import json
from itertools import groupby

# memcached already running before the experiment (not included in the batch policy)
# MEMCACHED_NODE = "node-a-8core"
# MEMCACHED_CORES = "0-1"
# MEMCACHED_THREADS = 2

# EVOLVE-BLOCK-START
batch_policy = [
    {"job": "radix",         "node": "node-a-8core", "threads": 2, "order": 1},
    {"job": "barnes",        "node": "node-b-4core", "threads": 3, "order": 1},

    {"job": "freqmine",      "node": "node-a-8core", "threads": 2, "order": 2},
    {"job": "streamcluster", "node": "node-b-4core", "threads": 1, "order": 2},
    {"job": "vips",          "node": "node-b-4core", "threads": 1, "order": 2},
    {"job": "blackscholes",  "node": "node-a-8core", "threads": 1, "order": 2},
    {"job": "canneal",       "node": "node-b-4core", "threads": 1, "order": 2},
]
# EVOLVE-BLOCK-END

#498
# batch_policy = [
#     {"job": "barnes",        "node": "node-a-8core", "threads": 4, "order": 1},
#     {"job": "vips",          "node": "node-b-4core", "threads": 3, "order": 1},
#     {"job": "radix",         "node": "node-a-8core", "threads": 2, "order": 1},

#     {"job": "freqmine",      "node": "node-a-8core", "threads": 4, "order": 2},
#     {"job": "streamcluster", "node": "node-a-8core", "threads": 2, "order": 2},
#     {"job": "canneal",       "node": "node-b-4core", "threads": 3, "order": 2},
#     {"job": "blackscholes",  "node": "node-b-4core", "threads": 1, "order": 2},
# ]

# batch_policy = [
#     {"job": "barnes",        "node": "node-a-8core", "threads": 4, "order": 1},
#     {"job": "vips",          "node": "node-b-4core", "threads": 3, "order": 1},
#     {"job": "radix",         "node": "node-a-8core", "threads": 2, "order": 1},
#     {"job": "blackscholes",  "node": "node-a-8core", "threads": 2, "order": 2},  # after barnes 
#     {"job": "canneal",       "node": "node-b-4core", "threads": 3, "order": 2},  # after vips
#     {"job": "freqmine",      "node": "node-a-8core", "threads": 4, "order": 2},
#     {"job": "streamcluster", "node": "node-a-8core", "threads": 6, "order": 3},
# ]
def launch_job(item):
    job = item["job"]
    node = item["node"]
    threads = item["threads"]

    if threads not in {1, 2, 3, 4, 6}:
        raise ValueError(f"Invalid threads: {threads} for {job}")
    # splash2x_jobs = {"barnes", "radix", "vips"}
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
            cpu: "{threads}"
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

def get_makespan():
    result = subprocess.run(
        # ["kubectl", "get", "jobs", "-o", "json"],
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

    # convert time string to seconds for score calculation
    for line in output.stdout.splitlines():
        if "Total time:" in line:
            t = line.split("Total time:", 1)[1].strip()
            h, m, s = t.split(":")
            return int(h) * 3600 + int(m) * 60 + int(float(s))
        # if "Total time:" in line:
        #     t = line.split("Total time: ")[1].strip()
        #     parts = t.split(":")
        #     if len(parts) == 3:
        #         h, m, s = parts
        #         return int(h)*3600 + int(m)*60 + int(s)
        #     elif len(parts) == 2:
        #         m, s = parts
        #         return int(m)*60 + int(s)
    return 9999

if __name__ == "__main__":
    # clean up any existing jobs
    # subprocess.run(["kubectl", "delete", "jobs", "--all", "--ignore-not-found=true"], capture_output=True)
    # subprocess.run(["kubectl", "delete", "pods", "--all", "--ignore-not-found=true"], capture_output=True)
    # time.sleep(5)
    subprocess.run(["kubectl", "delete", "jobs", "--all", "--ignore-not-found=true"], capture_output=True)
    result = subprocess.run(["kubectl", "get", "pods", "--no-headers", "-o", "custom-columns=NAME:.metadata.name"], capture_output=True, text=True)
    for pod in result.stdout.strip().splitlines():
        if "memcached" not in pod:
            subprocess.run(["kubectl", "delete", "pod", pod, "--ignore-not-found=true"], capture_output=True)
    time.sleep(5)

    # follow the batch policy to launch jobs in order
    sorted_policy = sorted(batch_policy, key=lambda x: x["order"])
    # group = tasks with same order
    for order, group in groupby(sorted_policy, key=lambda x: x["order"]):
        group_list = list(group)
        for item in group_list:
            launch_job(item)

        group_names = {item["job"] for item in group_list}
        wait_for_jobs(group_names)

    makespan = get_makespan()
    with open("makespan.txt", "w") as f:
        f.write(str(makespan))
    print(f"Makespan: {makespan}s")    