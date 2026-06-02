"""
Periodic cleanup of expired job workspaces.
"""

import asyncio
import shutil
import time
import logging
import os

logger = logging.getLogger(__name__)

JOB_TTL = int(os.getenv("JOB_TTL_SECONDS", "3600"))  # 1 hour default


async def cleanup_loop(job_manager):
    """Periodic cleanup of expired jobs. Runs every 10 minutes."""
    while True:
        await asyncio.sleep(600)  # 10 minutes
        try:
            now = time.time()
            expired = [
                jid
                for jid, job in job_manager.jobs.items()
                if now - job.created_at > JOB_TTL
            ]
            for jid in expired:
                job = job_manager.jobs.pop(jid, None)
                if job and job.workspace and job.workspace.exists():
                    shutil.rmtree(job.workspace, ignore_errors=True)
                    logger.info(f"Cleaned up expired job {jid}")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
