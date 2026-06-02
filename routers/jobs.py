"""
Endpoints for tracking translation jobs (SSE progress stream) and downloading results.
"""

import asyncio
import json
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from services.job_manager import job_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/languages")
async def get_languages():
    """Return all supported language codes and their human-readable labels."""
    from translate_xliff_openai_2 import LANGUAGES
    return LANGUAGES


@router.get("/jobs/{job_id}/progress")
async def get_progress(job_id: str):
    """
    Stream job progress using Server-Sent Events (SSE).
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        # If job has already finished, push final event immediately
        if job.status in ("completed", "failed"):
            event_type = "complete" if job.status == "completed" else "error"
            data = {
                "type": event_type,
                "status": job.status,
                "message": job.message,
                "progress": job.progress,
                "downloads": [
                    {
                        "name": d["name"],
                        "url": f"/api/jobs/{job.id}/download/{d['filename']}",
                    }
                    for d in job.downloads
                ],
            }
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
            return

        # Otherwise, stream events from the asyncio.Queue
        while True:
            try:
                # Use a timeout to occasionally send keep-alive and handle disconnections
                event = await asyncio.wait_for(job.events.get(), timeout=2.0)
                event_type = event.get("type", "progress")
                yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n"

                if event_type in ("complete", "error"):
                    break
            except asyncio.TimeoutError:
                # Send keep-alive to prevent client connection timeout
                yield ": keep-alive\n\n"
            except asyncio.CancelledError:
                logger.info(f"SSE client disconnected for job {job.id}")
                break
            except Exception as e:
                logger.error(f"Error in SSE stream for job {job.id}: {e}")
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable buffering for Nginx/Reverse Proxies
        }
    )


@router.get("/jobs/{job_id}/download/{filename}")
async def download_file(job_id: str, filename: str):
    """Download a translated ZIP package."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    file_path = job.workspace / "downloads" / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/zip",
    )
