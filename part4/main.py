import docker
from scheduler_logger import SchedulerLogger, Job
import psutil
import subprocess
import time


MEMCACHED_LOAD_HIGH = 70
MEMCACHED_LOAD_LOW = 55
JOB_LOAD_THRESHOLD = 50
SCHEDULER_INTERVAL = 0.2

ALL_CORES = [0, 1, 2, 3]
MAX_MEMCACHED_CORES = 3
OVERSUBSCRIPTION_ENABLED = False

PREFERRED_CPUS = {
    Job.BARNES: [1, 2, 3, 4],
    Job.BLACKSCHOLES: [1, 2],
    Job.CANNEAL: [1],
    Job.FREQMINE: [1, 2, 3, 4],
    Job.RADIX: [1, 2, 3, 4],
    Job.STREAMCLUSTER: [1, 2, 3, 4],
    Job.VIPS: [1, 2],
}


HIGH_SCALING = [Job.RADIX, Job.STREAMCLUSTER, Job.FREQMINE, Job.BARNES]
LOW_SCALING = [Job.VIPS, Job.BLACKSCHOLES, Job.CANNEAL]

JOB_COMMANDS = {
    Job.BARNES: "/bin/sh -c './run -a run -S splash2x -p barnes -i native -n 3'",
    Job.BLACKSCHOLES: "/bin/sh -c './run -a run -S parsec -p blackscholes -i native -n 3'",
    Job.CANNEAL: "/bin/sh -c './run -a run -S parsec -p canneal -i native -n 1'",
    Job.FREQMINE: "/bin/sh -c './run -a run -S parsec -p freqmine -i native -n 3'",
    Job.RADIX: "/bin/sh -c './run -a run -S splash2x -p radix -i native -n 8'",
    Job.STREAMCLUSTER: "/bin/sh -c './run -a run -S parsec -p streamcluster -i native -n 3'",
    Job.VIPS: "/bin/sh -c './run -a run -S parsec -p vips -i native -n 2'",
}

CONTAINER_IMAGES = {
    Job.BARNES: "anakli/cca:splash2x_barnes",
    Job.BLACKSCHOLES: "anakli/cca:parsec_blackscholes",
    Job.CANNEAL: "anakli/cca:parsec_canneal",
    Job.FREQMINE: "anakli/cca:parsec_freqmine",
    Job.RADIX: "anakli/cca:splash2x_radix",
    Job.STREAMCLUSTER: "anakli/cca:parsec_streamcluster",
    Job.VIPS: "anakli/cca:parsec_vips",
}

ASSIGNED_CORES = {
    0: set(),
    1: set(),
    2: set(),
    3: set(),
}

CONTAINER_NAMES = {
    Job.BARNES: "barnes",
    Job.BLACKSCHOLES: "blackscholes",
    Job.CANNEAL: "canneal",
    Job.FREQMINE: "freqmine",
    Job.RADIX: "radix",
    Job.STREAMCLUSTER: "streamcluster",
    Job.VIPS: "vips",
}


def get_memcached_cores():
    return [c for c in ASSIGNED_CORES.keys() if Job.MEMCACHED in ASSIGNED_CORES[c]]


def get_job_cores(job):
    return [c for c in ASSIGNED_CORES.keys() if job in ASSIGNED_CORES[c]]


def get_available_cores():
    return [c for c in ASSIGNED_CORES.keys() if Job.MEMCACHED not in ASSIGNED_CORES[c]]


procs = {p.info["name"]: p.pid for p in psutil.process_iter(["name"])}
MEMCACHED_PROC = [
    p for p in psutil.process_iter(["name"]) if p.info["name"] == "memcached"
][0]
MEMCACHED_PID = MEMCACHED_PROC.pid
del procs


def set_mem_cpus(cores):
    cpus = ",".join(str(i) for i in cores)
    subprocess.run(f"taskset -a -cp {cpus} {MEMCACHED_PID}", shell=True, check=True)


def set_cpus_docker(container, cores):
    cpus = ",".join(str(i) for i in cores)
    subprocess.run(
        f"docker update --cpuset-cpus={cpus} {container.id}", shell=True, check=True
    )


def get_load():
    return psutil.cpu_percent(percpu=True, interval=0.2)


class JobTimer:
    def __init__(self):
        self.start_time = None
        self.total_time = 0.0

    def start(self):
        self.start_time = time.time()

    def pause(self):
        if self.start_time is None:
            return
        elapsed = time.time() - self.start_time
        self.start_time = None
        self.total_time += elapsed

    def stop(self):
        if self.start_time is None:
            return 0.0
        elapsed = time.time() - self.start_time
        self.start_time = None
        return self.total_time + elapsed


def adjust_memcached_cores(client, logger, memcached_cores, load):
    original = list(memcached_cores)

    if load >= MEMCACHED_LOAD_HIGH and len(memcached_cores) < MAX_MEMCACHED_CORES:
        free = [c for c in ALL_CORES if c not in memcached_cores]
        if free:
            memcached_cores.append(free[0])
            set_mem_cpus(memcached_cores)
            logger.update_cores(Job.MEMCACHED, memcached_cores)
            print(
                f"[scheduler] memcached: {len(original)} -> {len(memcached_cores)} cores "
                f"(load={load:.2f}, added core {free[0]})"
            )

    elif load <= MEMCACHED_LOAD_LOW and len(memcached_cores) > 1:
        memcached_cores.pop()
        set_mem_cpus(memcached_cores)
        logger.update_cores(Job.MEMCACHED, memcached_cores)
        print(
            f"[scheduler] memcached: {len(original)} -> {len(memcached_cores)} cores "
            f"(load={load:.2f}, removed core)"
        )

    for c in memcached_cores:
        ASSIGNED_CORES[c].add(Job.MEMCACHED)
    for c in [
        c
        for c in ALL_CORES
        if c not in memcached_cores and Job.MEMCACHED in ASSIGNED_CORES[c]
    ]:
        ASSIGNED_CORES[c].remove(Job.MEMCACHED)


def assign_cores_to_job(available_cores, job):
    return [
        available_cores[i]
        for i in range(min(len(available_cores), len(PREFERRED_CPUS[job])))
    ]


def pick_next_job(pending, load):
    if load < JOB_LOAD_THRESHOLD:
        candidates = [j for j in HIGH_SCALING if j in pending]
    else:
        candidates = [j for j in LOW_SCALING if j in pending]

    if not candidates:
        candidates = pending

    return candidates[0]


def start_job(client, logger, job, cores):
    image = CONTAINER_IMAGES[job]
    command = JOB_COMMANDS[job]
    name = CONTAINER_NAMES[job]

    try:
        old = client.containers.get(name)
        old.remove(force=True)
    except docker.errors.NotFound:
        pass

    container = client.containers.run(
        image,
        command,
        name=name,
        cpuset_cpus=",".join(str(i) for i in cores),
        detach=True,
        remove=False,
    )
    logger.job_start(job, cores, len(cores))
    print(f"[scheduler] started {job.value} on cores {cores}")
    return container


def update_job_cores(client, logger, container, job, new_cores):
    set_cpus_docker(container, new_cores)
    logger.update_cores(job, new_cores)


def job_has_finished(container):
    try:
        container.reload()
        return container.status == "exited"
    except Exception:
        return True


def schedule(client):
    logger = SchedulerLogger()
    memcached_cores = [0, 1]
    ASSIGNED_CORES[0].add(Job.MEMCACHED)
    set_mem_cpus(memcached_cores)

    pending = [
        Job.BARNES,
        Job.BLACKSCHOLES,
        Job.CANNEAL,
        Job.FREQMINE,
        Job.RADIX,
        Job.STREAMCLUSTER,
        Job.VIPS,
    ]

    logger.custom_event(Job.MEMCACHED, f"initial cores: {memcached_cores}")

    running_jobs = {}

    finished_jobs = []
    ra = []
    while len(finished_jobs) < 7:
        load = get_load()
        mc_cores = get_memcached_cores()
        load = sum(load[c] for c in mc_cores) / len(mc_cores)
        ra.insert(0, load)
        print(ra)
        load = sum(ra) / len(ra)
        if len(ra) > 4:
            ra.pop()
        print("Load:", load)

        adjust_memcached_cores(client, logger, memcached_cores, load)

        available = get_available_cores()
        if running_jobs == {} and pending:
            next_job = pick_next_job(pending, load)
            job_cores = [
                available[i]
                for i in range(min(len(available), len(PREFERRED_CPUS[next_job])))
            ]
            container = start_job(client, logger, next_job, job_cores)
            timer = JobTimer()
            timer.start()
            pending.remove(next_job)
            running_jobs[next_job] = {
                "timer": timer,
                "container": container,
                "cores": job_cores,
                "paused": False,
            }
        for job in running_jobs.keys():
            container_finished = job_has_finished(running_jobs[job]["container"])
            if container_finished:
                elapsed = running_jobs[job]["timer"].stop()
                logger.job_end(job)
                logger.custom_event(job, f"elapsed={elapsed:.1f}s")
                print(f"[scheduler] {job.value} finished in {elapsed:.1f}s")
                finished_jobs.append(job)
                continue

            new_cores = assign_cores_to_job(available, job)
            if (
                set(running_jobs[job]["cores"]) != set(new_cores)
                and new_cores
                and not container_finished
            ):
                if running_jobs[job]["paused"]:
                    logger.job_unpause(job)
                    running_jobs[job]["container"].unpause()
                    running_jobs[job]["paused"] = False
                    running_jobs[job]["timer"].start()

                update_job_cores(
                    client, logger, running_jobs[job]["container"], job, new_cores
                )
                running_jobs[job]["cores"] = new_cores
            if not new_cores and not running_jobs[job]["paused"]:
                running_jobs[job]["container"].pause()
                running_jobs[job]["paused"] = True
                running_jobs[job]["timer"].pause()
                running_jobs[job]["cores"] = []
                logger.job_pause(job)

            available = [c for c in available if c not in running_jobs[job]["cores"]]

        for j in finished_jobs:
            if j in running_jobs.keys():
                running_jobs.pop(j)
        if available and pending:
            next_job = pick_next_job(pending, load)
            job_cores = [
                available[i]
                for i in range(min(len(available), len(PREFERRED_CPUS[next_job])))
            ]
            container = start_job(client, logger, next_job, job_cores)
            timer = JobTimer()
            timer.start()
            pending.remove(next_job)
            running_jobs[next_job] = {
                "timer": timer,
                "container": container,
                "cores": job_cores,
                "paused": False,
            }
            available = [c for c in available if c not in job_cores]

        time.sleep(SCHEDULER_INTERVAL)

    set_mem_cpus([0, 1, 2, 3])
    logger.end()


def main():
    client = docker.from_env()
    schedule(client)


if __name__ == "__main__":
    main()
