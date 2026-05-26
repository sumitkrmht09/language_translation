import sys
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stderr.encoding != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse
from pathlib import Path
import shutil
import uuid
import zipfile

from image_ocr_translator import process_image, process_pdf

app = FastAPI()

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("output")

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


@app.get("/")
def home():
    return {"message": "Translation API Running"}


@app.post("/translate")
async def translate_file(
    file: UploadFile = File(...),
    target_lang: str = Form(...)
):

    unique_id = uuid.uuid4().hex[:8]

    input_path = UPLOAD_DIR / f"{unique_id}_{file.filename}"

    with open(input_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    ext = input_path.suffix.lower()

    try:

        if ext == ".pdf":
            output_name = process_pdf(
                input_path,
                target_lang,
                OUTPUT_DIR
            )

        else:
            output_name = process_image(
                input_path,
                target_lang,
                OUTPUT_DIR
            )

        output_path = OUTPUT_DIR / output_name

        return FileResponse(
            path=str(output_path),
            filename=output_name,
            media_type='application/octet-stream'
        )

    except Exception as e:
        return {"error": str(e)}


@app.post("/translate-xliff")
async def translate_xliff_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    graphics_zip: UploadFile = File(...),
    target_lang: str = Form(...),
    dry_run: bool = Form(False)
):
    unique_id = uuid.uuid4().hex[:8]
    session_dir = UPLOAD_DIR / unique_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # 1. Save uploaded XLIFF file
    xlf_path = session_dir / file.filename
    with open(xlf_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 2. Save uploaded Graphics ZIP
    zip_path = session_dir / graphics_zip.filename
    with open(zip_path, "wb") as buffer:
        shutil.copyfileobj(graphics_zip.file, buffer)

    # 3. Extract Graphics ZIP
    graphics_src_dir = session_dir / "graphics_src"
    graphics_src_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(graphics_src_dir)
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        return {"error": f"Failed to extract graphics zip: {str(e)}"}

    # 4. Set up output root
    xlf_name_without_ext = file.filename.replace('.xlf', '').replace('.xliff', '')
    output_root = OUTPUT_DIR / unique_id / f"translated_{target_lang}_{xlf_name_without_ext}"
    output_root.mkdir(parents=True, exist_ok=True)

    # 5. Run translation
    import argparse
    from translate_xliff_openai_2 import translate_file as run_translation
    
    translation_args = argparse.Namespace(
        resume=False,
        batch_size=40,
        dry_run=dry_run,
        graphics_source_folder=str(graphics_src_dir)
    )

    try:
        from translate_xliff_openai_2 import MODEL as DEFAULT_MODEL
        
        success = run_translation(
            input_path=xlf_path,
            output_root=output_root,
            target_lang=target_lang,
            args=translation_args,
            model_to_use=DEFAULT_MODEL
        )
        
        if not success:
            shutil.rmtree(session_dir, ignore_errors=True)
            shutil.rmtree(OUTPUT_DIR / unique_id, ignore_errors=True)
            return {"error": "Translation failed. Check backend logs."}

        # 6. Create output ZIP containing the language-rooted folder prefix
        zip_out_path = OUTPUT_DIR / f"{unique_id}_translated.zip"
        zip_root = output_root
        count = 0
        with zipfile.ZipFile(zip_out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(zip_root.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(zip_root)
                arcname = f"{output_root.name}/{rel.as_posix()}"
                zf.write(path, arcname=arcname)
                count += 1

        # Clean up temporary upload session directory
        shutil.rmtree(session_dir, ignore_errors=True)
        shutil.rmtree(OUTPUT_DIR / unique_id, ignore_errors=True)

        # Register cleanup of the zip file in background task
        def cleanup_file(path: Path):
            if path.exists():
                try:
                    path.unlink()
                except Exception:
                    pass

        background_tasks.add_task(cleanup_file, zip_out_path)

        return FileResponse(
            path=str(zip_out_path),
            filename=f"translated_{target_lang}_{file.filename.replace('.xlf', '').replace('.xliff', '')}.zip",
            media_type='application/zip'
        )

    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        shutil.rmtree(output_root, ignore_errors=True)
        return {"error": f"Internal error during translation: {str(e)}"}