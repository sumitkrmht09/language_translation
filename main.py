"""
FastAPI application entry point. Serves the SPA frontend and hosts API endpoints.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from routers import translate, jobs
from services.job_manager import job_manager
from services.cleanup import cleanup_loop

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

# Ensure workspaces directory exists
Path("workspaces").mkdir(exist_ok=True)
# Ensure static directory exists
Path("static").mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start background cleanup task
    logger.info("Initializing background workspace cleanup loop...")
    cleanup_task = asyncio.create_task(cleanup_loop(job_manager))
    yield
    # Shutdown: Cancel background cleanup task
    logger.info("Stopping background workspace cleanup loop...")
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Clean shutdown completed.")


app = FastAPI(
    title="FrameMaker Translation Studio",
    description="High-performance, low-RAM translation service for FrameMaker XLIFF & OCR graphics",
    version="1.0.0",
    lifespan=lifespan,
)

# Include API routers (prefixes are defined inside each router)
app.include_router(translate.router)
app.include_router(jobs.router)

# Mount static files folder
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def serve_index():
    """Serve the single-page frontend application."""
    index_path = Path("static/index.html")
    if not index_path.exists():
        # Fallback response if index.html is missing
        return {
            "title": "FrameMaker Translation Studio",
            "message": "Frontend index.html is still generating. Please check back in a few seconds.",
        }
    return FileResponse(index_path)
