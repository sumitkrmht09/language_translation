"""
In-memory job queue with background workers for XLIFF translation jobs.
"""

import asyncio
import gc
import os
import uuid
import shutil
import zipfile
import argparse
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

WORKSPACES_DIR = Path("workspaces")
WORKSPACES_DIR.mkdir(exist_ok=True)


@dataclass
class Job:
    id: str
    status: str = "pending"  # pending, processing, completed, failed
    progress: float = 0.0
    message: str = "Queued..."
    languages: List[str] = field(default_factory=list)
    completed_languages: List[str] = field(default_factory=list)
    failed_languages: List[str] = field(default_factory=list)
    downloads: List[Dict[str, str]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    events: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=100))
    workspace: Optional[Path] = None
    _loop: Optional[asyncio.AbstractEventLoop] = None


class JobManager:
    def __init__(self):
        self.jobs: Dict[str, Job] = {}

    def create_job(self, languages: List[str]) -> Job:
        job_id = uuid.uuid4().hex[:8]
        job = Job(id=job_id, languages=languages)
        job.workspace = WORKSPACES_DIR / job_id
        job.workspace.mkdir(parents=True, exist_ok=True)
        self.jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    async def submit(
        self,
        job: Job,
        xlf_paths: List[Path],
        graphics_dir: Optional[Path],
        max_workers: int = 2,
    ):
        """Submit job for background processing."""
        job._loop = asyncio.get_event_loop()
        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            None, self._run_job, job, xlf_paths, graphics_dir, max_workers
        )

    def _send_event(self, job: Job, event: dict):
        """Thread-safe: push event to job's asyncio queue."""
        if job._loop and not job._loop.is_closed():
            try:
                job._loop.call_soon_threadsafe(job.events.put_nowait, event)
            except asyncio.QueueFull:
                logger.warning(f"Event queue full for job {job.id}, dropping event")

    def _run_job(
        self,
        job: Job,
        xlf_paths: List[Path],
        graphics_dir: Optional[Path],
        max_workers: int,
    ):
        """Main worker: runs in a background thread."""
        try:
            from translate_xliff_openai_2 import (
                translate_file as run_translation,
                MODEL as DEFAULT_MODEL,
            )

            job.status = "processing"
            self._send_event(
                job,
                {
                    "type": "progress",
                    "status": "processing",
                    "message": "Starting translation...",
                    "progress": 0.0,
                    "languages_done": [],
                    "languages_total": job.languages,
                },
            )

            # Build task list: (lang, xlf_path, xlf_name) for each xlf × lang combo
            tasks = []
            for xlf_path in xlf_paths:
                xlf_name = xlf_path.stem
                for lang in job.languages:
                    tasks.append((lang, xlf_path, xlf_name, graphics_dir))

            completed = 0
            total = len(tasks)
            start_time = time.time()

            actual_workers = min(max_workers, total, 2)  # Cap at 2 for memory

            with ThreadPoolExecutor(max_workers=actual_workers) as pool:
                futures = {
                    pool.submit(
                        self._process_language,
                        job,
                        lang,
                        xlf_path,
                        xlf_name,
                        gfx_dir,
                        DEFAULT_MODEL,
                    ): (lang, xlf_name)
                    for lang, xlf_path, xlf_name, gfx_dir in tasks
                }

                for future in as_completed(futures):
                    lang, xlf_name = futures[future]
                    try:
                        success, zip_path = future.result()
                        if success and zip_path:
                            job.completed_languages.append(lang)
                            job.downloads.append(
                                {
                                    "name": zip_path.name,
                                    "path": str(zip_path),
                                    "filename": zip_path.name,
                                }
                            )
                        else:
                            job.failed_languages.append(lang)
                    except Exception as e:
                        logger.error(f"Task {lang}/{xlf_name} failed: {e}")
                        job.failed_languages.append(lang)

                    completed += 1
                    elapsed = time.time() - start_time
                    eta = (
                        (elapsed / completed) * (total - completed)
                        if completed > 0
                        else 0
                    )
                    job.progress = completed / total
                    job.message = f"Completed {lang} ({completed}/{total})"

                    self._send_event(
                        job,
                        {
                            "type": "progress",
                            "status": "processing",
                            "message": job.message,
                            "progress": job.progress,
                            "languages_done": list(job.completed_languages),
                            "languages_total": job.languages,
                            "eta_seconds": round(eta),
                        },
                    )
                    gc.collect()

            job.status = "completed" if job.completed_languages else "failed"
            job.progress = 1.0
            job.message = (
                f"Done — {len(job.completed_languages)} succeeded, {len(job.failed_languages)} failed"
                if job.failed_languages
                else f"All {len(job.completed_languages)} translations completed!"
            )

            self._send_event(
                job,
                {
                    "type": "complete",
                    "status": job.status,
                    "message": job.message,
                    "downloads": [
                        {
                            "name": d["name"],
                            "url": f"/api/jobs/{job.id}/download/{d['filename']}",
                        }
                        for d in job.downloads
                    ],
                },
            )

        except Exception as e:
            logger.exception(f"Job {job.id} failed: {e}")
            job.status = "failed"
            job.message = f"Job failed: {str(e)}"
            self._send_event(job, {"type": "error", "message": job.message})

    def _process_language(
        self,
        job: Job,
        target_lang: str,
        xlf_path: Path,
        xlf_name: str,
        graphics_dir: Optional[Path],
        model: str,
    ):
        """Process one (xlf, language) pair. Runs in thread pool."""
        from translate_xliff_openai_2 import translate_file as run_translation

        output_root = job.workspace / f"translated_{target_lang}_{xlf_name}"
        output_root.mkdir(parents=True, exist_ok=True)

        # Build args namespace matching what translate_file expects
        args = argparse.Namespace(
            resume=False,
            batch_size=40,
            dry_run=False,
            graphics_source_folder=str(graphics_dir) if graphics_dir else None,
        )

        # Progress callback that pushes SSE events
        def progress_callback(msg, current, total, stats=None):
            pct = current / total if total > 0 else 0
            self._send_event(
                job,
                {
                    "type": "progress",
                    "status": "processing",
                    "message": f"[{target_lang}] {msg}",
                    "progress": job.progress,  # Overall progress stays the same
                    "languages_done": list(job.completed_languages),
                    "languages_total": job.languages,
                    "detail": {
                        "lang": target_lang,
                        "step_progress": pct,
                        "step_message": msg,
                    },
                },
            )

        try:
            success = run_translation(
                str(xlf_path),
                output_root,
                target_lang,
                args,
                model,
                progress_callback=progress_callback,
            )

            if success:
                # Create ZIP of the output
                zip_dir = job.workspace / "downloads"
                zip_dir.mkdir(exist_ok=True)
                zip_name = f"translated_{target_lang}_{xlf_name}"
                zip_path = zip_dir / f"{zip_name}.zip"

                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for file_path in output_root.rglob("*"):
                        if file_path.is_file():
                            arcname = (
                                f"{zip_name}/{file_path.relative_to(output_root)}"
                            )
                            zf.write(file_path, arcname)

                return True, zip_path
            else:
                return False, None

        except Exception as e:
            logger.exception(f"Translation failed for {target_lang}/{xlf_name}: {e}")
            return False, None


# Singleton
job_manager = JobManager()
