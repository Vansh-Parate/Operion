from fastapi import FastAPI
from dotenv import load_dotenv
import os

from app.core.logging import configure_logging

# Load environment variables
load_dotenv()
configure_logging()

app = FastAPI(
    title=os.getenv("APP_NAME"),
    version=os.getenv("APP_VERSION")
)


@app.get("/")
def root():
    return {
        "message": "Welcome to Operion 🚀"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }
