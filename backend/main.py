from fastapi import FastAPI
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

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