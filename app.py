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
import os

def get_downloads_dir() -> Path:
    p = Path(os.environ.get("USERPROFILE", "C:/Users/Lenovo")) / "Downloads"
    if p.exists():
        return p
    p = Path.home() / "Downloads"
    if p.exists():
        return p
    return Path("C:/Users/Lenovo/Downloads")

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
        zip_name = f"translated_{target_lang}_{xlf_name_without_ext}"
        zip_out_path = OUTPUT_DIR / f"{zip_name}.zip"
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

        # 7. Unzip the folder to the same location (OUTPUT_DIR)
        unzip_dest = OUTPUT_DIR / zip_name
        if unzip_dest.exists():
            shutil.rmtree(unzip_dest, ignore_errors=True)
        with zipfile.ZipFile(zip_out_path, 'r') as zip_ref:
            zip_ref.extractall(OUTPUT_DIR)

        # Ensure server-side extracted folder has double-nested graphics/text_conversion_file as well
        srv_double_nested = unzip_dest / zip_name
        srv_double_nested.mkdir(parents=True, exist_ok=True)
        if (unzip_dest / "graphics").exists():
            shutil.copytree(unzip_dest / "graphics", srv_double_nested / "graphics", dirs_exist_ok=True)
        if (unzip_dest / "text_conversion_file").exists():
            shutil.copytree(unzip_dest / "text_conversion_file", srv_double_nested / "text_conversion_file", dirs_exist_ok=True)

        # 8. Automatically copy/extract to the local user's Downloads folder
        try:
            downloads_dir = get_downloads_dir()
            if downloads_dir.exists():
                # Copy ZIP file
                shutil.copy2(zip_out_path, downloads_dir / f"{zip_name}.zip")
                
                # Extract ZIP directly to Downloads (this automatically creates the folder)
                dl_unzip_dest = downloads_dir / zip_name
                if dl_unzip_dest.exists():
                    shutil.rmtree(dl_unzip_dest, ignore_errors=True)
                with zipfile.ZipFile(zip_out_path, 'r') as zip_ref:
                    zip_ref.extractall(downloads_dir)
                    
                # Mirror the double-nested graphics/text_conversion_file directories inside Downloads as well
                double_nested_dir = dl_unzip_dest / zip_name
                double_nested_dir.mkdir(parents=True, exist_ok=True)
                if (dl_unzip_dest / "graphics").exists():
                    shutil.copytree(dl_unzip_dest / "graphics", double_nested_dir / "graphics", dirs_exist_ok=True)
                if (dl_unzip_dest / "text_conversion_file").exists():
                    shutil.copytree(dl_unzip_dest / "text_conversion_file", double_nested_dir / "text_conversion_file", dirs_exist_ok=True)
                    
                print(f"Automatically placed ZIP and double-nested folder in: {downloads_dir}")
        except Exception as e:
            print(f"Failed to copy/unzip to local Downloads folder: {e}")

        # Clean up temporary upload session directory and unique_id folder
        shutil.rmtree(session_dir, ignore_errors=True)
        shutil.rmtree(OUTPUT_DIR / unique_id, ignore_errors=True)

        return FileResponse(
            path=str(zip_out_path),
            filename=f"{zip_name}.zip",
            media_type='application/zip'
        )

    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        shutil.rmtree(output_root, ignore_errors=True)
        return {"error": f"Internal error during translation: {str(e)}"}