import os
from fastapi import FastAPI

# ----- Application configuration -----

PAYMENT_PROVIDER_URL = os.getenv("PAYMENT_PROVIDER_URL")

if not PAYMENT_PROVIDER_URL:
    raise RuntimeError(
        "CONFIG_ERROR: PAYMENT_PROVIDER_URL environment variable is required"
    )

# ----- Application -----

app = FastAPI(title="Payment Service")

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "payment-service"
    }

@app.post("/pay")
def create_payment():
    return {
        "status": "success",
        "provider": PAYMENT_PROVIDER_URL
    }