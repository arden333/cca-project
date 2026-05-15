import subprocess
import json
import os
import re, time, threading
from openevolve.evaluation_result import EvaluationResult

# IPs
def get_memcached_ip():
    result = subprocess.run(
        ["kubectl", "get", "pods", "-l", "name=some-memcached", "-o", "json"],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    items = data.get("items", [])
    if items and items[0].get("status", {}).get("podIP"):
        return items[0]["status"]["podIP"]
    raise RuntimeError("cannot find memcached Pod!")

CLIENT_MEASURE_EXTERNAL = "34.76.61.166"
CLIENT_AGENT_A_INTERNAL = "10.0.16.3"
CLIENT_AGENT_B_INTERNAL = "10.0.16.4"
CLIENT_AGENT_A_EXTERNAL = "34.62.29.115"
CLIENT_AGENT_B_EXTERNAL = "34.77.92.112"
# CLIENT_MEASURE_EXTERNAL = "35.190.209.247" 
# CLIENT_AGENT_A_INTERNAL = "10.0.16.7"
# CLIENT_AGENT_B_INTERNAL = "10.0.16.4"
# MEMCACHED_IP = get_memcached_ip()  
SSH_KEY = os.path.expanduser("~/.ssh/cloud-computing")
SSH_OPTS = ["-i", SSH_KEY, "-o", "StrictHostKeyChecking=no"]

PROGRAM_TIMEOUT = 650  # evaluator timeout(800)보다 작게 → OpenEvolve handler 절대 안 발동
FAIL_RESULT = EvaluationResult(metrics={
        "combined_score": 0.0,
        "score": 0.0,
        "makespan": 9999.0,
        "mean_batch_job_time": 9999.0,
        "slo_violations": 999.0,
    })  # 실패 통일

# ── helpers ──────────────────────────────────────────────────────────────────
def ssh_run(host, cmd):
    return subprocess.run(
        ["ssh"] + SSH_OPTS + [f"ubuntu@{host}", cmd],
        capture_output=True, text=True
    )

def ssh_bg(host, cmd):
    return subprocess.Popen(
        ["ssh"] + SSH_OPTS + [f"ubuntu@{host}", cmd],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )

def kill_remote_mcperf(host):
    ssh_run(host, "pkill -9 -f mcperf || true")

def cleanup_k8s_jobs():
    subprocess.run(
        ["kubectl", "delete", "jobs", "--all", "--ignore-not-found=true"],
        capture_output=True
    )
    result = subprocess.run(
        ["kubectl", "get", "pods", "--no-headers",
         "-o", "custom-columns=NAME:.metadata.name"],
        capture_output=True, text=True
    )
    for pod in result.stdout.strip().splitlines():
        if "memcached" not in pod:
            subprocess.run(
                ["kubectl", "delete", "pod", pod, "--ignore-not-found=true"],
                capture_output=True
            )
# ──────────────────────────────────────────────────────────────────

def p95_violations(mcperf_file = "mcperf_output.txt"):
    if not os.path.exists(mcperf_file):
        print("Warning: mcperf_output.txt")
        return 0

    violation = 0
    # find P95 value via column name
    p95_idx = None
    p95_pattern = re.compile(r"p95", re.IGNORECASE)
    
    with open(mcperf_file, "r") as f:
        for line in f:
            row = line.split()
            if not row:
                continue

            if row[0].startswith("#"):
                for i, col in enumerate(row):
                    if p95_pattern.search(col):
                        p95_idx = i
                        print(f"p95 column idx: {p95_idx}")
                        break
                continue

            if p95_idx is None or row[0] != "read":
                continue

            try:
                p95_value = float(row[p95_idx])
                if p95_value > 1000: # 1ms = 1000us
                    violation += 1
            except (ValueError, IndexError) as e:
                continue
    return violation

def score(makespan, slo_violations):
    if slo_violations > 0 or makespan > 800:
        return 0.0
    # return max (0.0, 1.0 - max(0.0, makespan - 200) / 200.0)
    return max (0.0, 1.0 - max(0.0, makespan - 200) / 600.0)

    
def evaluate(program_path: str) -> EvaluationResult:
    """
    Evaluate the evolved program at "program_path" and return an EvaluationResult object containing the evaluation metrics.
    """
    MEMCACHED_IP = get_memcached_ip()  

    # new. 이전 mcperf output 삭제
    # if os.path.exists("mcperf_output.txt"):
    #     os.remove("mcperf_output.txt")
    for f in ["mcperf_output.txt", "makespan.txt", "result.json", "job_times.json"]:
        if os.path.exists(f):
            os.remove(f)

    # 1. 잔여 mcperf 전부 kill
    print("Killing existing mcperf processes...")
    kill_remote_mcperf(CLIENT_MEASURE_EXTERNAL)
    kill_remote_mcperf(CLIENT_AGENT_A_EXTERNAL)
    kill_remote_mcperf(CLIENT_AGENT_B_EXTERNAL)
    time.sleep(1)

    # 2. agent-a, agent-b 시작
    print("Starting mcperf agents...")
    agent_a_proc = ssh_bg(CLIENT_AGENT_A_EXTERNAL, "cd memcache-perf-dynamic && ./mcperf -T 2 -A")
    agent_b_proc = ssh_bg(CLIENT_AGENT_B_EXTERNAL, "cd memcache-perf-dynamic && ./mcperf -T 4 -A")
    time.sleep(2)

    # 3. loadonly
    print("Running loadonly...")
    load_result = ssh_run(
        CLIENT_MEASURE_EXTERNAL,
        f"cd memcache-perf-dynamic && ./mcperf -s {MEMCACHED_IP} --loadonly"
    )
    if load_result.returncode != 0:
        print(f"loadonly failed: {load_result.stderr}")
        kill_remote_mcperf(CLIENT_AGENT_A_EXTERNAL)
        kill_remote_mcperf(CLIENT_AGENT_B_EXTERNAL)
        agent_a_proc.terminate()
        agent_b_proc.terminate()
        return FAIL_RESULT

    # 4. 본 mcperf 시작
    print("Starting main mcperf measurement...")
    mcperf_proc = ssh_bg(
        CLIENT_MEASURE_EXTERNAL,
        f"cd memcache-perf-dynamic && "
        f"./mcperf -s {MEMCACHED_IP} "
        f"-a {CLIENT_AGENT_A_INTERNAL} -a {CLIENT_AGENT_B_INTERNAL} "
        f"--noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 "
        f"--scan 30000:30500:5 > ~/mcperf_output.txt 2>&1"
    )

    # 5. batch program 시작 (Popen — 모니터링 스레드가 kill 가능하도록)
    program_proc = subprocess.Popen(
        ["python3", program_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # 6. mcperf 조기 종료 감지 스레드
    batch_done = threading.Event()
    mcperf_failed = threading.Event()

    def monitor_mcperf():
        mcperf_proc.wait()
        if not batch_done.is_set():
            print("mcperf exited early — invalid test, cleaning up...")
            mcperf_failed.set()
            try:
                program_proc.kill()
            except Exception:
                pass
            kill_remote_mcperf(CLIENT_MEASURE_EXTERNAL)
            kill_remote_mcperf(CLIENT_AGENT_A_EXTERNAL)
            kill_remote_mcperf(CLIENT_AGENT_B_EXTERNAL)
            agent_a_proc.terminate()
            agent_b_proc.terminate()
            cleanup_k8s_jobs()

    monitor_thread = threading.Thread(target=monitor_mcperf, daemon=True)
    monitor_thread.start()

    # 7. batch program 완료 대기
    try:
        stdout, stderr = program_proc.communicate(timeout=PROGRAM_TIMEOUT)
    except subprocess.TimeoutExpired:
        print("Program timed out.")
        program_proc.kill()
        mcperf_proc.terminate()
        kill_remote_mcperf(CLIENT_MEASURE_EXTERNAL)
        kill_remote_mcperf(CLIENT_AGENT_A_EXTERNAL)
        kill_remote_mcperf(CLIENT_AGENT_B_EXTERNAL)
        agent_a_proc.terminate()
        agent_b_proc.terminate()
        cleanup_k8s_jobs()
        return FAIL_RESULT

    # batch 완료 시점에 반드시 set
    batch_done.set() 

    # 8. mcperf 조기 종료 여부 확인
    if mcperf_failed.is_set():
        print("Invalid test: mcperf died during batch execution.")
        return FAIL_RESULT

    if program_proc.returncode != 0:
        print(f"Program error:\n{stderr}")
        mcperf_proc.terminate()
        kill_remote_mcperf(CLIENT_MEASURE_EXTERNAL)
        kill_remote_mcperf(CLIENT_AGENT_A_EXTERNAL)
        kill_remote_mcperf(CLIENT_AGENT_B_EXTERNAL)
        agent_a_proc.terminate()
        agent_b_proc.terminate()
        return FAIL_RESULT

    print(stdout)

    # 9. mcperf 정상 종료 처리
    time.sleep(5)
    mcperf_failed.set()  # 모니터 스레드가 중복 동작하지 않도록
    mcperf_proc.terminate()
    mcperf_proc.wait()
    kill_remote_mcperf(CLIENT_MEASURE_EXTERNAL)
    kill_remote_mcperf(CLIENT_AGENT_A_EXTERNAL)
    kill_remote_mcperf(CLIENT_AGENT_B_EXTERNAL)
    agent_a_proc.terminate()
    agent_b_proc.terminate()

    # 10. scp mcperf output
    scp_result = subprocess.run(
        ["scp"] + SSH_OPTS + [
            f"ubuntu@{CLIENT_MEASURE_EXTERNAL}:~/mcperf_output.txt",
            "./mcperf_output.txt"
        ],
        capture_output=True, text=True
    )
    if scp_result.returncode != 0:
        print(f"scp failed: {scp_result.stderr}")
        return FAIL_RESULT

    # # new. client-measure에서 mcperf 백그라운드 실행
    # mcperf_cmd = (
    #     f"cd memcache-perf-dynamic && "
    #     f"./mcperf -s {MEMCACHED_IP} "
    #     f"-a {CLIENT_AGENT_A_INTERNAL} -a {CLIENT_AGENT_B_INTERNAL} "
    #     f"--noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 600 "
    #     f"--scan 30000:30500:5 > ~/mcperf_output.txt 2>&1"
    # )
    # mcperf_proc = subprocess.Popen(
    #     ["ssh", "-i", SSH_KEY,
    #      "-o", "StrictHostKeyChecking=no",
    #      f"ubuntu@{CLIENT_MEASURE_EXTERNAL}",
    #      mcperf_cmd]
    # )

    # # 1. run the program
    # # result = subprocess.run(["python3", program_path], capture_output=True, text=True)
    # try:
    #     result = subprocess.run(
    #         ["python3", program_path],
    #         capture_output=True,
    #         text=True,
    #         timeout=PROGRAM_TIMEOUT
    #     )
    # except subprocess.TimeoutExpired:
    #     mcperf_proc.terminate()
    #     return FAIL_RESULT
    #     # return EvaluationResult(
    #     #     metrics={"combined_score": 0.0, "score": 0.0, "makespan": 9999, "slo_violations": 999},
    #     #     # artifacts="Program timed out."
    #     # )
    # finally:
    #     mcperf_proc.terminate()

    # print(result.stdout)

    # if result.returncode != 0:
    #     print(f"Program error:\n{result.stderr}")
    #     return FAIL_RESULT
    #     # return EvaluationResult(
    #     #     metrics={"combined_score": 0.0,"score": 0.0, "makespan": 9999, "slo_violations": 999},
    #     #     # artifacts=f"Program crashed: {result.stderr[:500]}"
    #     # )

    # # new. 2. scp mcperf output
    # scp_result = subprocess.run(
    #     ["scp", "-i", SSH_KEY,
    #      "-o", "StrictHostKeyChecking=no",
    #      f"ubuntu@{CLIENT_MEASURE_EXTERNAL}:~/mcperf_output.txt",
    #      "./mcperf_output.txt"],
    #     capture_output=True, text=True
    # )
    # if scp_result.returncode != 0:
    #     print(f"scp failed: {scp_result.stderr}")


    # 2. read makespan
    try:
        makespan = float(open("makespan.txt").read().strip())
    except (FileNotFoundError, ValueError) as e:
        return FAIL_RESULT
        #     return EvaluationResult(
        #     metrics={"combined_score": 0.0, "score": 0.0, "makespan": 9999, "slo_violations": 999},
        #     # artifacts=f"makespan.txt not found: {e}"
        # )
    try:
        with open("job_times.json") as f:
            job_info = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        job_info = {
            "job_times": {},
            "mean_batch_job_time": 9999.0,
            "total_time": makespan,
        }

    mean_batch_job_time = float(job_info.get("mean_batch_job_time", 9999.0))

    # 3. check slo violations
    violation = p95_violations("mcperf_output.txt")

    # 4. calculate score
    fin_score = score(makespan, violation)

    # 5. save per-evaluation artifacts
    artifact_dir = "eval_artifacts"
    os.makedirs(artifact_dir, exist_ok=True)

    program_id = os.path.splitext(os.path.basename(program_path))[0]

    record = {
        "program_id": program_id,
        "program_path": program_path,
        "makespan": makespan,
        "mean_batch_job_time": mean_batch_job_time,
        "job_times": job_info.get("job_times", {}),
        "slo_violations": violation,
        "score": fin_score,
    }

    with open(os.path.join(artifact_dir, f"{program_id}_job_times.json"), "w") as f:
        json.dump(record, f, indent=2)

    with open(os.path.join(artifact_dir, "eval_history.jsonl"), "a") as f:
        f.write(json.dumps(record) + "\n")

    print(
        f"Evaluation result - Makespan: {makespan}s, "
        f"Mean batch job time: {mean_batch_job_time}s, "
        f"SLO Violations: {violation}, Score: {fin_score:.4f}"
    )
    # print(f"Evaluation result - Makespan: {makespan}s, SLO Violations: {violation}, Score: {fin_score:.4f}")

    return EvaluationResult(
        metrics={
            "combined_score": fin_score,
            "score": fin_score,
            "makespan": makespan,
            "mean_batch_job_time": mean_batch_job_time,
            "slo_violations": violation,
        }
        # artifacts=f"Makespan: {makespan:.1f}s, SLO violations: {violation}, Score: {fin_score:.4f}"
    )