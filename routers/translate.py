"""
POST /api/translate — Accept XLIFF files and start a translation job.
"""

import logging
import shutil
import zipfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from services.job_manager import job_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["translate"])


@router.post("/translate")
async def translate(
    xlf_files: List[UploadFile] = File(..., description="XLIFF (.xlf/.xliff) files"),
    zip_files: Optional[List[UploadFile]] = File(
        None, description="Graphics ZIP archives"
    ),
    languages: str = Form(..., description="Comma-separated language codes"),
    max_workers: int = Form(2, ge=1, le=3, description="Parallel workers (1-3)"),
):
    """
    Upload XLIFF files, optional graphics ZIPs, and specify target languages.
    Returns a job_id to track progress via SSE.
    """
    # Import LANGUAGES lazily to avoid import-time side effects
    from translate_xliff_openai_2 import LANGUAGES

    # ── Validate languages ───────────────────────────────────────────────
    raw_langs = [l.strip() for l in languages.split(",") if l.strip()]
    if not raw_langs:
        raise HTTPException(status_code=400, detail="No languages specified.")

    valid_langs = []
    invalid_langs = []
    for lang in raw_langs:
        if lang in LANGUAGES:
            valid_langs.append(lang)
        else:
            invalid_langs.append(lang)

    if invalid_langs:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid language codes: {', '.join(invalid_langs)}. "
            f"Valid codes: {', '.join(LANGUAGES.keys())}",
        )

    if not valid_langs:
        raise HTTPException(status_code=400, detail="No valid languages specified.")

    # ── Validate XLF files ───────────────────────────────────────────────
    xlf_uploads = [
        f
        for f in xlf_files
        if f.filename
        and f.filename.lower().endswith((".xlf", ".xliff"))
        and f.size > 0
    ]
    if not xlf_uploads:
        raise HTTPException(
            status_code=400,
            detail="At least one .xlf or .xliff file is required.",
        )

    # ── Create job ───────────────────────────────────────────────────────
    job = job_manager.create_job(valid_langs)
    workspace = job.workspace

    # ── Save XLF files to workspace ──────────────────────────────────────
    xlf_dir = workspace / "xlf_input"
    xlf_dir.mkdir(parents=True, exist_ok=True)

    saved_xlf_paths: List[Path] = []
    for upload in xlf_uploads:
        dest = xlf_dir / upload.filename
        try:
            content = await upload.read()
            dest.write_bytes(content)
            saved_xlf_paths.append(dest)
            logger.info(f"Job {job.id}: saved XLF {upload.filename} ({len(content)} bytes)")
        except Exception as e:
            logger.error(f"Job {job.id}: failed to save {upload.filename}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to save file {upload.filename}: {str(e)}",
            )

    # ── Handle graphics ZIPs ─────────────────────────────────────────────
    graphics_dir: Optional[Path] = None

    if zip_files:
        graphics_dir = workspace / "graphics_input"
        graphics_dir.mkdir(parents=True, exist_ok=True)

        for zf_upload in zip_files:
            if not zf_upload.filename:
                continue
            if not zf_upload.filename.lower().endswith(".zip"):
                logger.warning(
                    f"Job {job.id}: skipping non-ZIP file {zf_upload.filename}"
                )
                continue
            if zf_upload.size == 0:
                continue

            zip_temp = workspace / zf_upload.filename
            try:
                content = await zf_upload.read()
                zip_temp.write_bytes(content)

                # Extract ZIP contents into graphics_dir
                with zipfile.ZipFile(zip_temp, "r") as zf:
                    zf.extractall(graphics_dir)
                logger.info(
                    f"Job {job.id}: extracted {zf_upload.filename} to {graphics_dir}"
                )

                # Remove the temporary ZIP after extraction
                zip_temp.unlink(missing_ok=True)
            except zipfile.BadZipFile:
                zip_temp.unlink(missing_ok=True)
                logger.error(f"Job {job.id}: invalid ZIP file {zf_upload.filename}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid ZIP file: {zf_upload.filename}",
                )
            except Exception as e:
                zip_temp.unlink(missing_ok=True)
                logger.error(
                    f"Job {job.id}: failed to process {zf_upload.filename}: {e}"
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to process ZIP {zf_upload.filename}: {str(e)}",
                )

    # ── Submit job for background processing ─────────────────────────────
    await job_manager.submit(
        job,
        xlf_paths=saved_xlf_paths,
        graphics_dir=graphics_dir,
        max_workers=max_workers,
    )

    logger.info(
        f"Job {job.id}: submitted with {len(saved_xlf_paths)} XLF(s), "
        f"{len(valid_langs)} language(s): {valid_langs}"
    )

    return {
        "job_id": job.id,
        "languages": valid_langs,
        "xlf_files": [u.filename for u in xlf_uploads],
        "message": f"Translation job started. Track progress at /api/jobs/{job.id}/progress",
    }
