import os
import shutil
import uuid
import zipfile
import logging
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, UploadFile, File, Form, Query, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("api_server")

# Load configuration and validate
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY environment variable is not set in this context!")

API_SECRET_KEY = os.environ.get("API_SECRET_KEY")
if not API_SECRET_KEY:
    logger.warning("API_SECRET_KEY environment variable is not set. API is running WITHOUT AUTHENTICATION!")

app = FastAPI(
    title="FrameMaker XLIFF & Graphics Translator API",
    description="Automated technical manual translation and graphics OCR processing endpoint for FrameMaker technical manuals.",
    version="1.0.0"
)

security = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    secret_key = os.environ.get("API_SECRET_KEY")
    if not secret_key:
        return credentials.credentials
    if credentials.credentials != secret_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Secret Key",
        )
    return credentials.credentials


# ── Dummy args namespace for pipeline interface ──────────────────────────────
class PipelineArgs:
    def __init__(self, batch_size=40, dry_run=False, resume=False, graphics_source_folder=None):
        self.batch_size = batch_size
        self.dry_run = dry_run
        self.resume = resume
        self.graphics_source_folder = graphics_source_folder


def cleanup_workspace(workspace_dir: Path):
    try:
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir)
            logger.info(f"Cleaned up temporary workspace: {workspace_dir}")
    except Exception as e:
        logger.error(f"Error cleaning up workspace {workspace_dir}: {e}")


def _download_file(url: str, dest: Path, label: str) -> Path:
    """Download a file from a URL to a local path."""
    logger.info(f"Downloading {label} from: {url}")
    try:
        with httpx.Client(follow_redirects=True, timeout=300) as client:
            resp = client.get(url)
            resp.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            logger.info(f"Downloaded {label}: {len(resp.content)} bytes -> {dest}")
            return dest
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=400, detail=f"Failed to download {label}: HTTP {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to download {label}: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  ENDPOINT 1: URL-based (for ChatGPT Custom GPT Actions)
# ═══════════════════════════════════════════════════════════════════════════════

class TranslateRequest(BaseModel):
    xlf_url: str
    graphics_zip_url: Optional[str] = None
    target_lang: str


@app.get("/")
def home():
    return {
        "status": "online",
        "service": "FrameMaker XLIFF & Graphics Translator API",
        "auth_enabled": bool(API_SECRET_KEY)
    }


@app.post("/translate")
async def translate_via_urls(
    body: TranslateRequest,
    background_tasks: BackgroundTasks,
    credentials: str = Depends(verify_token),
):
    """
    Accepts download URLs for the XLIFF file and optional graphics ZIP.
    Downloads them server-side, runs the translation pipeline, and returns
    the translated ZIP archive.
    """
    target_lang = body.target_lang
    xlf_url = body.xlf_url
    graphics_zip_url = body.graphics_zip_url

    # Validate target language
    from translate_xliff_openai_2 import LANGUAGES
    if target_lang not in LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported target language '{target_lang}'. Supported: {', '.join(LANGUAGES.keys())}"
        )

    # 1. Create secure workspace
    request_id = str(uuid.uuid4())
    workspace_dir = Path("workspaces") / request_id
    input_dir = workspace_dir / "input"
    output_dir = workspace_dir / "output"

    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Register workspace cleanup task
    background_tasks.add_task(cleanup_workspace, workspace_dir)

    try:
        # 2. Download XLIFF file
        xlf_filename = xlf_url.rstrip("/").split("/")[-1].split("?")[0]
        if not xlf_filename:
            xlf_filename = "manual.xlf"
        # Ensure it has .xlf extension
        if not xlf_filename.lower().endswith((".xlf", ".xliff")):
            xlf_filename += ".xlf"

        xlf_path = input_dir / xlf_filename
        _download_file(xlf_url, xlf_path, "XLIFF file")

        # 3. Download and extract graphics ZIP (optional)
        graphics_source_folder = None
        if graphics_zip_url:
            zip_filename = graphics_zip_url.rstrip("/").split("/")[-1].split("?")[0]
            if not zip_filename:
                zip_filename = "graphics.zip"

            zip_save_path = input_dir / zip_filename
            _download_file(graphics_zip_url, zip_save_path, "Graphics ZIP")

            graphics_source_folder = workspace_dir / "extracted_graphics"
            graphics_source_folder.mkdir(exist_ok=True)

            logger.info(f"Extracting graphics archive to {graphics_source_folder}")
            with zipfile.ZipFile(zip_save_path, "r") as zf:
                zf.extractall(graphics_source_folder)

        # 4. Import and execute the pipeline
        from translate_xliff_openai_2 import translate_file as run_translation_pipeline

        args = PipelineArgs(
            batch_size=40,
            dry_run=False,
            resume=False,
            graphics_source_folder=graphics_source_folder,
        )

        output_root = output_dir / f"translated_{target_lang}"
        output_root.mkdir(parents=True, exist_ok=True)

        logger.info(f"Executing translation pipeline for {xlf_filename} -> {target_lang}")

        success = run_translation_pipeline(
            input_path=xlf_path,
            output_root=output_root,
            target_lang=target_lang,
            args=args,
            model_to_use="gpt-4o",
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Translation pipeline execution failed.",
            )

        # 5. Pack the output directory into a standard deliverable ZIP archive
        zip_path = output_dir / f"translated_{target_lang}.zip"
        logger.info(f"Creating final deliverable ZIP archive at {zip_path}")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(output_root.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(output_root)
                arcname = f"translated_{target_lang}/{rel.as_posix()}"
                zf.write(path, arcname=arcname)

        if not zip_path.exists():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to generate output ZIP deliverable.",
            )

        # 6. Stream file response back to user
        return FileResponse(
            path=str(zip_path),
            filename=f"translated_{target_lang}.zip",
            media_type="application/zip",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("An unhandled exception occurred during translation pipeline:")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": str(e)},
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  ENDPOINT 2: Direct file upload (for Postman / curl / direct API testing)
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/translate/upload")
async def translate_via_upload(
    background_tasks: BackgroundTasks,
    target_lang: str = Query(..., description="Target ISO language code"),
    file: UploadFile = File(..., description="The FrameMaker XLIFF file (.xlf or .xliff)"),
    graphics_zip: Optional[UploadFile] = File(None, description="ZIP archive of source graphics"),
    credentials: str = Depends(verify_token),
):
    """
    Direct multipart/form-data file upload endpoint for testing via
    curl, Postman, or any HTTP client that supports binary uploads.
    """
    from translate_xliff_openai_2 import LANGUAGES

    ext = Path(file.filename).suffix.lower()
    if ext not in [".xlf", ".xliff"]:
        raise HTTPException(status_code=400, detail="Expected .xlf or .xliff file")

    if target_lang not in LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language. Supported: {', '.join(LANGUAGES.keys())}")

    request_id = str(uuid.uuid4())
    workspace_dir = Path("workspaces") / request_id
    input_dir = workspace_dir / "input"
    output_dir = workspace_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    background_tasks.add_task(cleanup_workspace, workspace_dir)

    try:
        xlf_path = input_dir / file.filename
        with open(xlf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        graphics_source_folder = None
        if graphics_zip:
            zip_save_path = input_dir / graphics_zip.filename
            with open(zip_save_path, "wb") as buffer:
                shutil.copyfileobj(graphics_zip.file, buffer)
            graphics_source_folder = workspace_dir / "extracted_graphics"
            graphics_source_folder.mkdir(exist_ok=True)
            with zipfile.ZipFile(zip_save_path, "r") as zf:
                zf.extractall(graphics_source_folder)

        from translate_xliff_openai_2 import translate_file as run_translation_pipeline
        args = PipelineArgs(batch_size=40, dry_run=False, resume=False, graphics_source_folder=graphics_source_folder)
        output_root = output_dir / f"translated_{target_lang}"
        output_root.mkdir(parents=True, exist_ok=True)

        success = run_translation_pipeline(
            input_path=xlf_path, output_root=output_root,
            target_lang=target_lang, args=args, model_to_use="gpt-4o",
        )
        if not success:
            raise HTTPException(status_code=500, detail="Pipeline failed.")

        zip_path = output_dir / f"translated_{target_lang}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(output_root.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(output_root)
                arcname = f"translated_{target_lang}/{rel.as_posix()}"
                zf.write(path, arcname=arcname)

        return FileResponse(path=str(zip_path), filename=f"translated_{target_lang}.zip", media_type="application/zip")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Upload endpoint error:")
        return JSONResponse(status_code=500, content={"error": str(e)})