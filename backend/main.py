import os
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.investigations import router as investigations_router
from app.core.logging import configure_logging

# Load environment variables
load_dotenv()
configure_logging()

app = FastAPI(
    title=os.getenv("APP_NAME", "Operion"),
    version=os.getenv("APP_VERSION", "1.0.0"),
)

# Allow requests from frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(investigations_router, prefix="/api")


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
