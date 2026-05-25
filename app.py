import os
import shutil
import uuid
import zipfile
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
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
        # If no secret key is set in the environment, bypass verification for easy local testing
        return credentials.credentials
    if credentials.credentials != secret_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Secret Key",
        )
    return credentials.credentials

# Dummy args namespace for pipeline interface
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

@app.get("/")
def home():
    return {
        "status": "online",
        "service": "FrameMaker XLIFF & Graphics Translator API",
        "auth_enabled": bool(API_SECRET_KEY)
    }

@app.post("/translate")
async def translate(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="The FrameMaker manual XLIFF file (.xlf or .xliff)"),
    graphics_zip: Optional[UploadFile] = File(None, description="ZIP archive containing source graphics referenced by the manual"),
    target_lang: str = Form(..., description="Target ISO language code, e.g. 'de', 'fr', 'zh-CN'"),
    credentials: str = Depends(verify_token)
):
    # Validate file extension
    ext = Path(file.filename).suffix.lower()
    if ext not in [".xlf", ".xliff"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid XLIFF file format. Expected .xlf or .xliff"
        )

    # Validate target language
    from translate_xliff_openai_2 import LANGUAGES
    if target_lang not in LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported target language. Supported codes: {', '.join(LANGUAGES.keys())}"
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
        # 2. Save XLIFF file
        xlf_path = input_dir / file.filename
        with open(xlf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 3. Handle graphics ZIP
        graphics_source_folder = None
        if graphics_zip:
            if not graphics_zip.filename.lower().endswith(".zip"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid graphics archive. Expected a .zip file"
                )

            zip_save_path = input_dir / graphics_zip.filename
            with open(zip_save_path, "wb") as buffer:
                shutil.copyfileobj(graphics_zip.file, buffer)

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
            graphics_source_folder=graphics_source_folder
        )

        output_root = output_dir / f"translated_{target_lang}"
        output_root.mkdir(parents=True, exist_ok=True)

        logger.info(f"Executing translation pipeline for {file.filename} -> {target_lang}")
        
        success = run_translation_pipeline(
            input_path=xlf_path,
            output_root=output_root,
            target_lang=target_lang,
            args=args,
            model_to_use="gpt-4o"
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Translation pipeline execution failed."
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
                detail="Failed to generate output ZIP deliverable."
            )

        # 6. Stream file response back to user
        return FileResponse(
            path=str(zip_path),
            filename=f"translated_{target_lang}.zip",
            media_type="application/zip"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.exception("An unhandled exception occurred during translation pipeline:")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": str(e)}
        )