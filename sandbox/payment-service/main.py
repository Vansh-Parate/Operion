import logging
import os
import socket
import threading
import time

from fastapi import FastAPI, HTTPException


PAYMENT_PROVIDER_URL = os.getenv("PAYMENT_PROVIDER_URL")
INCIDENT_MODE = os.getenv("INCIDENT_MODE", "")
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
logger = logging.getLogger("uvicorn.error")

if not PAYMENT_PROVIDER_URL:
    raise RuntimeError(
        "CONFIG_ERROR: PAYMENT_PROVIDER_URL environment variable is required"
    )

app = FastAPI(title="Payment Service")


def check_redis_connection():
    try:
        with socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=2):
            return True
    except OSError as exc:
        logger.error(
            "REDIS_CONNECTION_ERROR: failed to connect to Redis at %s:%s: %s",
            REDIS_HOST,
            REDIS_PORT,
            exc,
        )
        return False


def exhaust_memory():
    # Touch each page so the allocation is charged to the container's cgroup.
    allocations = []
    while True:
        chunk = bytearray(8 * 1024 * 1024)
        for offset in range(0, len(chunk), 4096):
            chunk[offset] = 1
        allocations.append(chunk)
        time.sleep(0.1)


@app.on_event("startup")
def start_incident_mode():
    if INCIDENT_MODE == "oom":
        logger.warning("INCIDENT_MODE=oom: allocating memory until the container limit is reached")
        threading.Thread(target=exhaust_memory, daemon=True).start()
    elif INCIDENT_MODE == "redis-unavailable":
        check_redis_connection()


@app.get("/health")
def health():
    return {"status": "healthy", "service": "payment-service"}


@app.get("/health/dependency")
def dependency_health():
    connected = check_redis_connection()
    if not connected:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    return {"redis": "connected"}


@app.post("/pay")
def create_payment():
    return {"status": "success", "provider": PAYMENT_PROVIDER_URL}
