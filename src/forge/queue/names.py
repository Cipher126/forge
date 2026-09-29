QUEUE_PREFIX = "forge:queue"
PRIORITY_CYCLE = ("high",) * 8 + ("normal",) * 2 + ("low",)


def queue_key(priority: str) -> str:
    return f"{QUEUE_PREFIX}:{priority}"


def processing_queue_key(worker_id: str) -> str:
    return f"forge:processing:{worker_id}"


def worker_heartbeat_key(worker_id: str) -> str:
    return f"forge:worker:{worker_id}:heartbeat"


def job_type_permits_key(job_type: str) -> str:
    return f"forge:permits:{job_type}"


def api_key_rate_limit_key(public_id: str) -> str:
    return f"forge:rate-limit:{public_id}"
