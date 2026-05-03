"""
Usage example:
  python3 part_3_1_scheduler.py --plan 1 --runs 3 \
    --measure-host CLIENT_MEASURE_EXTERNAL_IP_OR_NAME \
    --ssh-key ~/.ssh/cloud-computing \
    --mcperf-dir /home/ubuntu/memcache-perf-dynamic \
    --memcached-ip MEMCACHED_IP \
    --agent-a-ip INTERNAL_AGENT_A_IP \
    --agent-b-ip INTERNAL_AGENT_B_IP
"""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
import shlex

try:
    from scheduler_logger import SchedulerLogger, Job as LogJob
except Exception:
    SchedulerLogger = None
    LogJob = None

RESULTS_ROOT = Path("part_3_1_results_group_030")
POD_START_TIMEOUT_S = 180
POLL_INTERVAL_S = 5
JOB_TIMEOUT = "15m"
MCPERF_WARMUP_S = 5
REMOTE_MCPERF_LOG_DIR = "/home/ubuntu"

WORKLOADS: dict[str, dict[str, str]] = {
    "barnes":        {"image": "anakli/cca:splash2x_barnes",        "suite": "splash2x"},
    "radix":         {"image": "anakli/cca:splash2x_radix",         "suite": "splash2x"},
    "blackscholes":  {"image": "anakli/cca:parsec_blackscholes",    "suite": "parsec"},
    "canneal":       {"image": "anakli/cca:parsec_canneal",         "suite": "parsec"},
    "freqmine":      {"image": "anakli/cca:parsec_freqmine",        "suite": "parsec"},
    "streamcluster": {"image": "anakli/cca:parsec_streamcluster",   "suite": "parsec"},
    "vips":          {"image": "anakli/cca:parsec_vips",            "suite": "parsec"},
}

PLANS: dict[str, dict[str, Any]] = {
    "1": {
        "node-a-8core": {
            "lanes": [
                [
                    {"name": "streamcluster", "threads": 4, "cores": "2-5", "request_cpu": "3500m", "limit_cpu": "4000m", "request_memory": "6Gi", "limit_memory": "8Gi"},
                    {"name": "blackscholes", "threads": 4, "cores": "2-5", "request_cpu": "3500m", "limit_cpu": "4000m", "request_memory": "6Gi", "limit_memory": "8Gi"},
                    {"name": "radix", "threads": 4, "cores": "2-5", "request_cpu": "3500m", "limit_cpu": "4000m", "request_memory": "6Gi", "limit_memory": "8Gi"},
                ],
                [
                    {"name": "canneal", "threads": 2, "cores": "6-7", "request_cpu": "1800m", "limit_cpu": "2000m", "request_memory": "3Gi", "limit_memory": "4Gi"},
                ],
            ],
        },
        "node-b-4core": {
            "lanes": [
                [
                    {"name": "freqmine", "threads": 4, "cores": "0-3", "request_cpu": "3500m", "limit_cpu": "4000m", "request_memory": "3200Mi", "limit_memory": "3500Mi"},
                    {"name": "vips", "threads": 4, "cores": "0-3", "request_cpu": "3500m", "limit_cpu": "4000m", "request_memory": "3200Mi", "limit_memory": "3500Mi"},
                    {"name": "barnes", "threads": 4, "cores": "0-3", "request_cpu": "3500m", "limit_cpu": "4000m", "request_memory": "3200Mi", "limit_memory": "3500Mi"},
                ],
            ],
        },
    },
}

def out_dir(plan_name: str) -> Path:
    return RESULTS_ROOT

def sh(cmd: list[str], input_text: str | None = None) -> str:
    print("$", " ".join(cmd), flush=True)
    p = subprocess.run(cmd, input=input_text, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    return p.stdout


def kubectl(args: list[str], input_text: str | None = None) -> str:
    return sh(["kubectl", *args], input_text)


def job_yaml(spec: dict[str, Any], node_label: str, run_id: str) -> str:
    job = spec["name"]
    cfg = WORKLOADS[job]
    
    job_name = f"parsec-{job}"
 
    threads = int(spec["threads"])
    cores = str(spec["cores"])
    request_cpu = str(spec["request_cpu"])
    limit_cpu = str(spec["limit_cpu"])
    request_memory = str(spec["request_memory"])
    limit_memory = str(spec["limit_memory"])
 
    command = f"taskset -c {cores} ./run -a run -S {cfg['suite']} -p {job} -i native -n {threads}"

    return f"""
apiVersion: batch/v1
kind: Job
metadata:
  name: {job_name}
  labels:
    name: {job_name}
    app: part-3-1
    run: "{run_id}"
    workload: "{job}"
spec:
  backoffLimit: 0
  template:
    metadata:
      labels:
        name: {job_name}
        app: part-3-1
        run: "{run_id}"
        workload: "{job}"
    spec:
      restartPolicy: Never
      nodeSelector:
        cca-project-nodetype: "{node_label}"
      containers:
      - image: {cfg["image"]}
        name: parsec-{job}
        imagePullPolicy: Always
        command: ["/bin/sh"]
        args: ["-c", "{command}"]
        resources:
          requests:
            cpu: "{request_cpu}"
            memory: "{request_memory}"
          limits:
            cpu: "{limit_cpu}"
            memory: "{limit_memory}"
"""


def check_job_started(job_name: str) -> None:
    deadline = time.time() + POD_START_TIMEOUT_S
    last_pod_name = None
    last_phase = None

    while time.time() < deadline:
        pods = json.loads(
            kubectl(["get", "pods", "-l", f"job-name={job_name}", "-o", "json"])
        )["items"]

        if pods:
            pod = pods[0]
            last_pod_name = pod["metadata"]["name"]
            last_phase = pod["status"].get("phase", "")

            if last_phase in ("Running", "Succeeded"):
                return

            if last_phase == "Failed":
                print(kubectl(["describe", "pod", last_pod_name]))
                raise RuntimeError(f"{job_name} pod failed before running.")

        time.sleep(POLL_INTERVAL_S)

    if last_pod_name:
        print(kubectl(["get", "pod", last_pod_name, "-o", "wide"]))
        print(kubectl(["describe", "pod", last_pod_name]))
        raise RuntimeError(
            f"{job_name} did not reach Running/Succeeded within "
            f"{POD_START_TIMEOUT_S}s. Last phase: {last_phase}. "
            "See the Events section above for the reason."
        )

    print(kubectl(["describe", "job", job_name]))
    raise RuntimeError(f"No pod was created for {job_name} within {POD_START_TIMEOUT_S}s.")


def start_job(spec: dict[str, Any], node_label: str, run_id: str, logger: Any = None) -> str:
    job = spec["name"]
    job_name = f"parsec-{job}"

    print(
        f"[START] {job} on {node_label} | cores={spec['cores']} "
        f"threads={spec['threads']} cpu={spec['request_cpu']}/{spec['limit_cpu']} "
        f"mem={spec['request_memory']}/{spec['limit_memory']}",
        flush=True,
    )

    kubectl(["create", "-f", "-"], job_yaml(spec, node_label, run_id))
    check_job_started(job_name)

    if logger and LogJob:
        logger.job_start(LogJob[job.upper()], [str(spec["cores"])], int(spec["threads"]))

    return job_name


def wait_job(job_name: str, workload: str, logger: Any = None) -> None:
    print(f"[WAIT] {workload} waiting for completion...", flush=True)
    try:
        kubectl([
            "wait",
            f"job/{job_name}",
            "--for=condition=complete",
            f"--timeout={JOB_TIMEOUT}",
        ])
    except Exception:
        print(kubectl(["get", "pods", "-l", f"job-name={job_name}", "-o", "wide"]))
        print(kubectl(["describe", "job", job_name]))
        raise

    print(f"[DONE] {workload} ({job_name}) completed.", flush=True)

    if logger and LogJob:
        logger.job_end(LogJob[workload.upper()])


def run_lane(node_label: str, lane: list[dict[str, Any]], run_id: str, logger: Any) -> None:
    for spec in lane:
        job_name = start_job(spec, node_label, run_id, logger)
        wait_job(job_name, spec["name"], logger)


def run_lanes(node_label: str, lanes: list[list[dict[str, Any]]], run_id: str, logger: Any) -> None:
    errors: list[BaseException] = []
    threads = []

    def worker(lane: list[dict[str, Any]]) -> None:
        try:
            run_lane(node_label, lane, run_id, logger)
        except BaseException as e:
            errors.append(e)

    for lane in lanes:
        t = threading.Thread(target=worker, args=(lane,), daemon=False)
        t.start()
        threads.append(t)
        time.sleep(0.2)

    for t in threads:
        t.join()

    if errors:
        raise RuntimeError(f"Lane failed on {node_label}: {errors[0]}") from errors[0]


def run_node_queue(node_label: str, queue: dict[str, Any], run_id: str, logger: Any) -> None:
    if "phases" in queue:
        for phase in queue["phases"]:
            run_lanes(node_label, phase["lanes"], run_id, logger)
        return

    run_lanes(node_label, queue["lanes"], run_id, logger)


def save_pods(plan_name: str, run_idx: int, run_id: str) -> Path:
    out = out_dir(plan_name)
    out.mkdir(parents=True, exist_ok=True)

    pods = json.loads(kubectl(["get", "pods", "-l", f"run={run_id}", "-o", "json"]))
    pods_path = out / f"pods_{run_idx}.json"
    pods_path.write_text(json.dumps(pods, indent=2))

    print(f"[SAVE] {pods_path}", flush=True)
    return pods_path


def ssh_cmd(measure_host: str, ssh_key: str, remote_cmd: str) -> str:
    wrapped_cmd = f"bash -lc {shlex.quote(remote_cmd)}"
    return sh([
        "ssh",
        "-i", ssh_key,
        "-o", "StrictHostKeyChecking=no",
        f"ubuntu@{measure_host}",
        wrapped_cmd,
    ])


def scp_from_measure(measure_host: str, ssh_key: str, remote_path: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    sh([
        "scp",
        "-i", ssh_key,
        "-o", "StrictHostKeyChecking=no",
        f"ubuntu@{measure_host}:{remote_path}",
        str(local_path),
    ])


def start_remote_mcperf(
    run_id: str,
    measure_host: str,
    ssh_key: str,
    mcperf_dir: str,
    memcached_ip: str,
    agent_a_ip: str,
    agent_b_ip: str,
) -> tuple[str, str]:
    remote_log = f"{REMOTE_MCPERF_LOG_DIR}/mcperf_{run_id}.txt"

    inner_cmd = (
        f"cd {shlex.quote(mcperf_dir)} && "
        f"exec ./mcperf "
        f"-s {shlex.quote(memcached_ip)} "
        f"-a {shlex.quote(agent_a_ip)} "
        f"-a {shlex.quote(agent_b_ip)} "
        f"--noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 "
        f"--scan 30000:30500:5 "
        f"> {shlex.quote(remote_log)} 2>&1 < /dev/null"
    )

    mcperf_cmd = (
        f"setsid bash -lc {shlex.quote(inner_cmd)} "
        f"> /dev/null 2>&1 < /dev/null & "
        f"echo $!"
    )

    print(f"[MCPERF START] {run_id} on {measure_host}, log={remote_log}", flush=True)
    pid = ssh_cmd(measure_host, ssh_key, mcperf_cmd).strip()

    if not pid:
        raise RuntimeError("Failed to start remote mcperf: no PID returned")

    print(f"[MCPERF PID] {pid}", flush=True)
    return pid, remote_log

def stop_remote_mcperf(measure_host: str, ssh_key: str, pid: str) -> None:
    print(f"[MCPERF STOP] pid={pid}", flush=True)
    remote_cmd = (
        f"kill -TERM -{shlex.quote(pid)} >/dev/null 2>&1 || "
        f"kill {shlex.quote(pid)} >/dev/null 2>&1 || true"
    )
    ssh_cmd(measure_host, ssh_key, remote_cmd)


def download_mcperf_log(
    measure_host: str,
    ssh_key: str,
    remote_log: str,
    plan_name: str,
    run_idx: int,
) -> Path:
    local_log = out_dir(plan_name) / f"mcperf_{run_idx}.txt"
    print(f"[MCPERF DOWNLOAD] {remote_log} -> {local_log}", flush=True)
    scp_from_measure(measure_host, ssh_key, remote_log, local_log)
    return local_log


def run_batch_jobs(plan_name: str, run_id: str, logger: Any = None) -> None:
    if plan_name not in PLANS:
        raise ValueError(f"Unknown plan {plan_name!r}. Available: {', '.join(PLANS)}")

    plan = PLANS[plan_name]
    errors: list[BaseException] = []

    def worker(node_label: str, queue: dict[str, Any]) -> None:
        try:
            run_node_queue(node_label, queue, run_id, logger)
        except BaseException as e:
            errors.append(e)

    threads = []
    for node_label, queue in plan.items():
        t = threading.Thread(target=worker, args=(node_label, queue), daemon=False)
        t.start()
        threads.append(t)
        time.sleep(0.5)

    for t in threads:
        t.join()

    if errors:
        raise RuntimeError(f"Scheduler failed in worker thread: {errors[0]}") from errors[0]


def cleanup() -> None:
    print("[CLEANUP] deleting old part-3-1 jobs/pods", flush=True)

    kubectl([
        "delete", "jobs",
        "-l", "app=part-3-1",
        "--ignore-not-found=true",
    ])

    kubectl([
        "delete", "pods",
        "-l", "app=part-3-1",
        "--ignore-not-found=true",
    ])

def run_once(
    plan_name: str,
    run_idx: int,
    measure_host: str,
    ssh_key: str,
    mcperf_dir: str,
    memcached_ip: str,
    agent_a_ip: str,
    agent_b_ip: str,
) -> dict[str, str]:
    run_id = f"run{run_idx}"
    print(f"\n========== {run_id} START ==========", flush=True)

    cleanup()

    logger = SchedulerLogger() if SchedulerLogger else None
    mcperf_pid = None
    remote_log = None

    try:
        mcperf_pid, remote_log = start_remote_mcperf(
            run_id=run_id,
            measure_host=measure_host,
            ssh_key=ssh_key,
            mcperf_dir=mcperf_dir,
            memcached_ip=memcached_ip,
            agent_a_ip=agent_a_ip,
            agent_b_ip=agent_b_ip,
        )

        print(f"[MCPERF WARMUP] sleeping {MCPERF_WARMUP_S}s", flush=True)
        time.sleep(MCPERF_WARMUP_S)

        run_batch_jobs(plan_name, run_id, logger)

    finally:
        if mcperf_pid:
            stop_remote_mcperf(measure_host, ssh_key, mcperf_pid)
        if logger:
            logger.end()

    if remote_log is None:
        raise RuntimeError("remote mcperf log path was not set")

    mcperf_path = download_mcperf_log(
        measure_host, ssh_key, remote_log, plan_name, run_idx
    )
    pods_path = save_pods(plan_name, run_idx, run_id)

    print(f"========== {run_id} DONE ==========", flush=True)

    return {
        "run_id": run_id,
        "pods_json": str(pods_path),
        "mcperf_txt": str(mcperf_path),
    }


def run_many(
    plan_name: str,
    runs: int,
    measure_host: str,
    ssh_key: str,
    mcperf_dir: str,
    memcached_ip: str,
    agent_a_ip: str,
    agent_b_ip: str,
) -> list[dict[str, str]]:
    if plan_name not in PLANS:
        raise ValueError(f"Unknown plan {plan_name!r}. Available: {', '.join(PLANS)}")

    out_dir(plan_name).mkdir(parents=True, exist_ok=True)

    results = []
    for i in range(1, runs + 1):
        results.append(
            run_once(
                plan_name=plan_name,
                run_idx=i,
                measure_host=measure_host,
                ssh_key=ssh_key,
                mcperf_dir=mcperf_dir,
                memcached_ip=memcached_ip,
                agent_a_ip=agent_a_ip,
                agent_b_ip=agent_b_ip,
            )
        )

    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="1", choices=sorted(PLANS))
    ap.add_argument("--runs", type=int, default=3)

    ap.add_argument("--measure-host", required=True, help="client-measure VM external IP or hostname")
    ap.add_argument("--ssh-key", default=str(Path.home() / ".ssh/cloud-computing"))
    ap.add_argument("--mcperf-dir", default="/home/ubuntu/memcache-perf-dynamic")

    ap.add_argument("--memcached-ip", required=True)
    ap.add_argument("--agent-a-ip", required=True)
    ap.add_argument("--agent-b-ip", required=True)

    args = ap.parse_args()

    results = run_many(
        plan_name=args.plan,
        runs=args.runs,
        measure_host=args.measure_host,
        ssh_key=args.ssh_key,
        mcperf_dir=args.mcperf_dir,
        memcached_ip=args.memcached_ip,
        agent_a_ip=args.agent_a_ip,
        agent_b_ip=args.agent_b_ip,
    )

    print("\nSaved raw results:")
    for r in results:
        print(f"  {r['run_id']}:")
        print(f"    pods:   {r['pods_json']}")
        print(f"    mcperf: {r['mcperf_txt']}")


if __name__ == "__main__":
    main()
