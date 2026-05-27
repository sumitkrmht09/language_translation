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

import os
import shutil
import uuid
import zipfile
import threading
import time
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from dotenv import load_dotenv

load_dotenv()

# Setup paths
UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("output")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Import translation modules
from image_ocr_translator import process_image, process_pdf, process_xlf_references
from translate_xliff_openai_2 import translate_file as run_translation, MODEL as DEFAULT_MODEL, LANGUAGES

app = FastAPI(title="FrameMaker XLIFF Translation Studio")

# Global thread-safe job registry
JOBS = {}
JOBS_LOCK = threading.Lock()

def get_downloads_dir() -> Path:
    p = Path(os.environ.get("USERPROFILE", "C:/Users/Lenovo")) / "Downloads"
    if p.exists():
        return p
    p = Path.home() / "Downloads"
    if p.exists():
        return p
    return Path("C:/Users/Lenovo/Downloads")

# Background thread worker function
def run_translation_job(job_id: str, xlf_path: Path, zip_path: Path, target_lang: str, dry_run: bool):
    with JOBS_LOCK:
        job = JOBS[job_id]
        job["status"] = "running"
        job["message"] = "Initializing environment..."
        job["progress"] = 5

    session_dir = xlf_path.parent
    try:
        # Extract Graphics ZIP
        with JOBS_LOCK:
            job["message"] = "Extracting graphics zip file..."
            job["progress"] = 10

        graphics_src_dir = session_dir / "graphics_src"
        graphics_src_dir.mkdir(parents=True, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(graphics_src_dir)

        xlf_name_without_ext = xlf_path.name.replace('.xlf', '').replace('.xliff', '')
        output_root = OUTPUT_DIR / job_id / f"translated_{target_lang}_{xlf_name_without_ext}"
        output_root.mkdir(parents=True, exist_ok=True)

        # Progress callback to update thread status
        def progress_cb(msg: str, current: int, total: int, stats: dict = None):
            with JOBS_LOCK:
                job["message"] = msg
                # Map segment translation to 15% - 60% progress range
                if "Translating segments" in msg:
                    pct = 15 + int((current / max(1, total)) * 45)
                # Map graphics OCR to 65% - 90% progress range
                elif "Processed graphic" in msg or "Processing graphics" in msg:
                    pct = 65 + int((current / max(1, total)) * 25)
                elif "Writing translation" in msg:
                    pct = 62
                else:
                    pct = job["progress"]

                job["progress"] = min(92, max(job["progress"], pct))
                if stats:
                    for k, v in stats.items():
                        if k in job:
                            job[k] = v

        import argparse
        translation_args = argparse.Namespace(
            resume=False,
            batch_size=40,
            dry_run=dry_run,
            graphics_source_folder=str(graphics_src_dir)
        )

        success = run_translation(
            input_path=xlf_path,
            output_root=output_root,
            target_lang=target_lang,
            args=translation_args,
            model_to_use=DEFAULT_MODEL,
            progress_callback=progress_cb
        )

        if not success:
            with JOBS_LOCK:
                job["status"] = "failed"
                job["message"] = "Translation process returned failure."
                job["progress"] = 100
            shutil.rmtree(session_dir, ignore_errors=True)
            shutil.rmtree(OUTPUT_DIR / job_id, ignore_errors=True)
            return

        with JOBS_LOCK:
            job["message"] = "Packaging translation output package..."
            job["progress"] = 93

        # Create output ZIP containing language-rooted folder prefix
        zip_name = f"translated_{target_lang}_{xlf_name_without_ext}"
        zip_out_path = OUTPUT_DIR / f"{zip_name}.zip"
        
        with zipfile.ZipFile(zip_out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(output_root.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(output_root)
                arcname = f"{output_root.name}/{rel.as_posix()}"
                zf.write(path, arcname=arcname)

        # Unzip folder locally next to ZIP
        unzip_dest = OUTPUT_DIR / zip_name
        if unzip_dest.exists():
            shutil.rmtree(unzip_dest, ignore_errors=True)
        with zipfile.ZipFile(zip_out_path, 'r') as zip_ref:
            zip_ref.extractall(OUTPUT_DIR)

        # Ensure server-side extracted folder has double-nested graphics/text_conversion_file
        srv_double_nested = unzip_dest / zip_name
        srv_double_nested.mkdir(parents=True, exist_ok=True)
        if (unzip_dest / "graphics").exists():
            shutil.copytree(unzip_dest / "graphics", srv_double_nested / "graphics", dirs_exist_ok=True)
        if (unzip_dest / "text_conversion_file").exists():
            shutil.copytree(unzip_dest / "text_conversion_file", srv_double_nested / "text_conversion_file", dirs_exist_ok=True)

        # Automatically copy/extract to local user's Downloads folder
        try:
            downloads_dir = get_downloads_dir()
            if downloads_dir.exists():
                shutil.copy2(zip_out_path, downloads_dir / f"{zip_name}.zip")
                dl_unzip_dest = downloads_dir / zip_name
                if dl_unzip_dest.exists():
                    shutil.rmtree(dl_unzip_dest, ignore_errors=True)
                with zipfile.ZipFile(zip_out_path, 'r') as zip_ref:
                    zip_ref.extractall(downloads_dir)
                    
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
        shutil.rmtree(OUTPUT_DIR / job_id, ignore_errors=True)

        with JOBS_LOCK:
            job["status"] = "completed"
            job["progress"] = 100
            job["message"] = "Translation and OCR completed successfully!"
            job["output_zip_name"] = f"{zip_name}.zip"
            job["output_zip_path"] = str(zip_out_path)

    except Exception as e:
        import traceback
        traceback.print_exc()
        with JOBS_LOCK:
            job["status"] = "failed"
            job["message"] = f"Internal error during execution: {str(e)}"
            job["progress"] = 100
        shutil.rmtree(session_dir, ignore_errors=True)
        if 'output_root' in locals():
            shutil.rmtree(output_root, ignore_errors=True)


# Root Endpoint serving the HTML dashboard UI
@app.get("/", response_class=HTMLResponse)
def home():
    # Render supported languages options
    lang_options = "\n".join([f'<option value="{code}">{label} ({code})</option>' for code, label in LANGUAGES.items()])
    
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FrameMaker XLIFF & OCR Translation Studio</title>
    <!-- Google Fonts -->
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-dark: #080a11;
            --card-bg: rgba(17, 22, 34, 0.7);
            --border-color: rgba(255, 255, 255, 0.08);
            --primary: #6366f1; /* Indigo */
            --primary-glow: rgba(99, 102, 241, 0.15);
            --accent: #a855f7; /* Purple */
            --accent-glow: rgba(168, 85, 247, 0.15);
            --cyan-glow: rgba(6, 182, 212, 0.15);
            --success: #10b981; /* Green */
            --error: #ef4444; /* Red */
            --text-muted: #9ca3af;
        }}

        body {{
            font-family: 'Inter', sans-serif;
            background-color: var(--bg-dark);
            margin: 0;
            padding: 0;
            overflow-x: hidden;
            min-height: 100vh;
            color: #f3f4f6;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            position: relative;
        }}

        /* Glowing background blobs */
        .glow-blob {{
            position: absolute;
            width: 600px;
            height: 600px;
            border-radius: 50%;
            filter: blur(120px);
            opacity: 0.25;
            z-index: -1;
            pointer-events: none;
        }}
        .blob-indigo {{
            background: radial-gradient(circle, var(--primary) 0%, transparent 70%);
            top: -10%;
            left: -10%;
            animation: float-blob-1 25s infinite alternate;
        }}
        .blob-purple {{
            background: radial-gradient(circle, var(--accent) 0%, transparent 70%);
            bottom: -10%;
            right: -10%;
            animation: float-blob-2 20s infinite alternate;
        }}
        @keyframes float-blob-1 {{
            0% {{ transform: translate(0, 0) scale(1); }}
            100% {{ transform: translate(100px, 50px) scale(1.1); }}
        }}
        @keyframes float-blob-2 {{
            0% {{ transform: translate(0, 0) scale(1); }}
            100% {{ transform: translate(-100px, -50px) scale(0.9); }}
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 3rem 1.5rem;
            position: relative;
            flex-grow: 1;
        }}

        .header {{
            text-align: center;
            margin-bottom: 3.5rem;
        }}

        .logo-title {{
            font-size: 2.75rem;
            font-weight: 800;
            letter-spacing: -0.05em;
            background: linear-gradient(135deg, #fff 30%, #818cf8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin: 0;
            font-family: 'Outfit', sans-serif;
        }}

        .subtitle {{
            font-size: 1.125rem;
            color: var(--text-muted);
            margin-top: 0.75rem;
            font-weight: 400;
        }}

        .dashboard-grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 2.5rem;
            align-items: start;
        }}
        @media (min-width: 900px) {{
            .dashboard-grid {{
                grid-template-columns: 5fr 4fr;
            }}
        }}

        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 2rem;
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.4);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
        }}

        .card-title {{
            font-size: 1.25rem;
            font-weight: 600;
            margin-top: 0;
            margin-bottom: 1.5rem;
            color: #ffffff;
            font-family: 'Outfit', sans-serif;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            padding-bottom: 0.75rem;
        }}

        .form-group {{
            margin-bottom: 1.75rem;
        }}

        .form-label {{
            display: block;
            font-weight: 500;
            margin-bottom: 0.75rem;
            color: #e5e7eb;
            font-size: 0.95rem;
        }}

        /* Dropzone styling */
        .dropzone {{
            border: 2px dashed rgba(255, 255, 255, 0.15);
            border-radius: 12px;
            padding: 2.25rem 1.5rem;
            text-align: center;
            background: rgba(255, 255, 255, 0.01);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
        }}
        .dropzone input[type="file"] {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            opacity: 0;
            cursor: pointer;
            z-index: 10;
        }}
        .dropzone:hover, .dropzone.dragover {{
            border-color: var(--primary);
            background: rgba(99, 102, 241, 0.03);
            box-shadow: 0 0 20px var(--primary-glow);
            transform: translateY(-2px);
        }}
        .dropzone-icon {{
            font-size: 2rem;
            margin-bottom: 0.75rem;
            display: inline-block;
            transition: transform 0.3s ease;
        }}
        .dropzone:hover .dropzone-icon {{
            transform: translateY(-4px);
        }}
        .dropzone-text {{
            font-size: 0.9rem;
            color: #9ca3af;
        }}
        .dropzone-text strong {{
            color: #e5e7eb;
        }}

        .file-indicator {{
            margin-top: 0.75rem;
            font-size: 0.85rem;
            color: #34d399;
            font-weight: 500;
            display: none;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
        }}

        select.form-input {{
            width: 100%;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 0.85rem 1.25rem;
            color: #f3f4f6;
            font-size: 0.95rem;
            font-family: inherit;
            outline: none;
            cursor: pointer;
            transition: all 0.3s ease;
        }}
        select.form-input:focus {{
            border-color: var(--primary);
            box-shadow: 0 0 15px var(--primary-glow);
        }}
        /* Customize option colors for dropdown */
        select.form-input option {{
            background-color: #111827;
            color: #f3f4f6;
        }}

        .checkbox-group {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            cursor: pointer;
            user-select: none;
            font-size: 0.95rem;
            color: #d1d5db;
        }}
        .checkbox-input {{
            width: 1.15rem;
            height: 1.15rem;
            accent-color: var(--primary);
            cursor: pointer;
        }}

        .btn-submit {{
            width: 100%;
            background: linear-gradient(135deg, var(--primary) 0%, var(--accent) 100%);
            border: none;
            border-radius: 12px;
            padding: 1.1rem;
            color: white;
            font-weight: 600;
            font-size: 1.05rem;
            font-family: inherit;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: 0 4px 20px rgba(99, 102, 241, 0.3);
            margin-top: 0.5rem;
        }}
        .btn-submit:hover:not(:disabled) {{
            box-shadow: 0 6px 24px rgba(99, 102, 241, 0.5), 0 0 15px rgba(168, 85, 247, 0.3);
            transform: translateY(-2px);
            filter: brightness(1.1);
        }}
        .btn-submit:active:not(:disabled) {{
            transform: translateY(0);
        }}
        .btn-submit:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
            box-shadow: none;
        }}

        /* Process card & progress */
        .status-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 1.5rem;
        }}
        .status-title {{
            margin: 0;
            font-size: 1.1rem;
            font-weight: 600;
        }}
        .status-badge {{
            padding: 0.4rem 0.8rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        .badge-idle {{
            background: rgba(156, 163, 175, 0.1);
            color: var(--text-muted);
        }}
        .badge-running {{
            background: rgba(99, 102, 241, 0.15);
            color: #818cf8;
            box-shadow: 0 0 10px rgba(99, 102, 241, 0.2);
            animation: pulse-badge 1.5s infinite alternate;
        }}
        .badge-completed {{
            background: rgba(16, 185, 129, 0.15);
            color: #34d399;
        }}
        .badge-failed {{
            background: rgba(239, 68, 68, 0.15);
            color: #f87171;
        }}

        @keyframes pulse-badge {{
            from {{ opacity: 0.8; }}
            to {{ opacity: 1; filter: brightness(1.2); }}
        }}

        .progress-container {{
            background: rgba(255, 255, 255, 0.04);
            border-radius: 9999px;
            height: 10px;
            width: 100%;
            margin-bottom: 1.75rem;
            overflow: hidden;
            border: 1px solid rgba(255, 255, 255, 0.02);
        }}
        .progress-bar {{
            background: linear-gradient(90deg, var(--primary) 0%, var(--accent) 50%, #22d3ee 100%);
            height: 100%;
            width: 0%;
            border-radius: 9999px;
            transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: 0 0 10px rgba(99, 102, 241, 0.6);
        }}

        .phase-list {{
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            margin-bottom: 2rem;
        }}
        .phase-item {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.75rem 1rem;
            border-radius: 10px;
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.02);
            font-size: 0.9rem;
            transition: all 0.3s ease;
        }}
        .phase-item.active {{
            background: rgba(99, 102, 241, 0.05);
            border-color: rgba(99, 102, 241, 0.2);
        }}
        .phase-item.pending {{ color: var(--text-muted); opacity: 0.5; }}
        .phase-item.running {{ color: #818cf8; font-weight: 500; border-color: rgba(99, 102, 241, 0.3); }}
        .phase-item.completed {{ color: var(--success); }}
        .phase-item.failed {{ color: var(--error); }}
        .phase-icon {{ font-size: 1.1rem; }}

        /* Stats cards */
        .stats-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1.25rem;
            margin-bottom: 2rem;
        }}
        .stat-card {{
            background: rgba(255, 255, 255, 0.01);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 16px;
            padding: 1.25rem;
            text-align: center;
            transition: all 0.3s ease;
        }}
        .stat-card:hover {{
            background: rgba(255, 255, 255, 0.02);
            border-color: rgba(255, 255, 255, 0.08);
        }}
        .stat-label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.5rem;
        }}
        .stat-value {{
            font-size: 1.75rem;
            font-weight: 700;
            color: #ffffff;
            font-family: 'Outfit', sans-serif;
            margin-bottom: 0.25rem;
        }}
        .stat-detail {{
            font-size: 0.75rem;
            color: var(--text-muted);
        }}

        /* Console log */
        .console-log {{
            background: rgba(0, 0, 0, 0.4);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            padding: 1rem;
            font-family: 'Courier New', Courier, monospace;
            font-size: 0.8rem;
            color: #38bdf8;
            height: 120px;
            overflow-y: auto;
            margin-bottom: 2rem;
            white-space: pre-line;
            scrollbar-width: thin;
        }}

        .download-container {{
            display: none;
            text-align: center;
            animation: fadeInUp 0.5s ease forwards;
        }}
        @keyframes fadeInUp {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        .btn-download {{
            background: linear-gradient(135deg, var(--success) 0%, #059669 100%);
            box-shadow: 0 4px 20px rgba(16, 185, 129, 0.3);
            animation: pulse-green 2s infinite;
        }}
        @keyframes pulse-green {{
            0% {{ box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.6); }}
            70% {{ box-shadow: 0 0 0 15px rgba(16, 185, 129, 0); }}
            100% {{ box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }}
        }}

        .footer {{
            text-align: center;
            padding: 2rem;
            font-size: 0.85rem;
            color: var(--text-muted);
            border-top: 1px solid rgba(255, 255, 255, 0.03);
            background: rgba(8, 10, 17, 0.8);
            backdrop-filter: blur(10px);
        }}
        .footer a {{
            color: var(--primary);
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="glow-blob blob-indigo"></div>
    <div class="glow-blob blob-purple"></div>

    <div class="container">
        <div class="header">
            <h1 class="logo-title">FrameMaker Translation Studio</h1>
            <p class="subtitle">AI-powered XLIFF Document Translation & Referenced Graphics OCR replacement</p>
        </div>

        <div class="dashboard-grid">
            <!-- Upload Card -->
            <div class="card" id="form-card">
                <h2 class="card-title">Initiate New Translation Task</h2>
                <form id="translate-form" onsubmit="submitForm(event)">
                    
                    <!-- XLIFF Upload -->
                    <div class="form-group">
                        <label class="form-label">XLIFF Source Document (.xlf, .xliff)</label>
                        <div class="dropzone" id="xlf-dropzone">
                            <span class="dropzone-icon">📄</span>
                            <div class="dropzone-text">Drag & drop your XLIFF file, or <strong>browse</strong></div>
                            <input type="file" id="xlf-file" name="file" accept=".xlf,.xliff" onchange="handleFileSelected(this, 'xlf-dropzone', 'xlf-indicator')" required>
                            <div class="file-indicator" id="xlf-indicator"></div>
                        </div>
                    </div>

                    <!-- Graphics ZIP Upload -->
                    <div class="form-group">
                        <label class="form-label">Source Graphics ZIP Archive (.zip)</label>
                        <div class="dropzone" id="zip-dropzone">
                            <span class="dropzone-icon">🖼️</span>
                            <div class="dropzone-text">Drag & drop your graphics ZIP archive, or <strong>browse</strong></div>
                            <input type="file" id="zip-file" name="graphics_zip" accept=".zip" onchange="handleFileSelected(this, 'zip-dropzone', 'zip-indicator')" required>
                            <div class="file-indicator" id="zip-indicator"></div>
                        </div>
                    </div>

                    <!-- Target Language Selection -->
                    <div class="form-group">
                        <label class="form-label" for="target_lang">Target Language</label>
                        <select class="form-input" id="target_lang" name="target_lang" required>
                            <option value="" disabled selected>-- Select target language --</option>
                            {lang_options}
                        </select>
                    </div>

                    <!-- Options (Dry Run) -->
                    <div class="form-group">
                        <label class="checkbox-group">
                            <input type="checkbox" class="checkbox-input" id="dry_run" name="dry_run" value="true">
                            <span>Dry Run Mode (Parses structure and identifies segments/graphics without calling OpenAI translation)</span>
                        </label>
                    </div>

                    <!-- Submit Button -->
                    <button type="submit" class="btn-submit" id="btn-translate">Translate & Process Graphics</button>
                </form>
            </div>

            <!-- Monitoring Card (Initially hidden/idle status) -->
            <div class="card" id="status-card">
                <div class="status-header">
                    <h2 class="status-title" id="status-display-title">Execution Monitor</h2>
                    <span class="status-badge badge-idle" id="job-status-badge">Idle</span>
                </div>

                <div class="progress-container">
                    <div class="progress-bar" id="job-progress-bar"></div>
                </div>

                <!-- Statistics Grid -->
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-label">Text Segments</div>
                        <div class="stat-value" id="stat-segments">-</div>
                        <div class="stat-detail" id="stat-segments-detail">Translated / Total</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Graphics Replaced</div>
                        <div class="stat-value" id="stat-graphics">-</div>
                        <div class="stat-detail" id="stat-graphics-detail">Processed / Total</div>
                    </div>
                </div>

                <!-- Phase List -->
                <div class="phase-list">
                    <div class="phase-item pending" id="phase-upload">
                        <span>1. Extracting Source Archives</span>
                        <span class="phase-icon" id="phase-upload-icon">○</span>
                    </div>
                    <div class="phase-item pending" id="phase-translate">
                        <span>2. AI Segment Translation</span>
                        <span class="phase-icon" id="phase-translate-icon">○</span>
                    </div>
                    <div class="phase-item pending" id="phase-ocr">
                        <span>3. Referenced Graphics OCR & Replace</span>
                        <span class="phase-icon" id="phase-ocr-icon">○</span>
                    </div>
                    <div class="phase-item pending" id="phase-package">
                        <span>4. Rebuilding & Packaging Folder Structures</span>
                        <span class="phase-icon" id="phase-package-icon">○</span>
                    </div>
                </div>

                <!-- Console Status Log -->
                <div class="console-log" id="status-console">Waiting to start job...</div>

                <!-- Download Output Block -->
                <div class="download-container" id="download-box">
                    <button class="btn-submit btn-download" onclick="downloadResult()">Download Translated Deliverable ZIP</button>
                    <p style="font-size:0.8rem; color:#10b981; margin-top:0.75rem;">
                        ✓ Files have also been automatically extracted to your local Downloads folder structure!
                    </p>
                </div>
            </div>
        </div>
    </div>

    <div class="footer">
        FrameMaker Translation Studio &copy; 2026. Built with FastAPI and OpenAI GPT-4o.
    </div>

    <script>
        // Setup Drag & Drop behavior using input overlays
        ['xlf-file', 'zip-file'].forEach(id => {{
            const input = document.getElementById(id);
            const dropzone = input.closest('.dropzone');

            input.addEventListener('dragover', (e) => {{
                e.preventDefault();
                dropzone.classList.add('dragover');
            }});

            input.addEventListener('dragleave', () => {{
                dropzone.classList.remove('dragover');
            }});

            input.addEventListener('drop', () => {{
                dropzone.classList.remove('dragover');
            }});
        }});

        function handleFileSelected(input, dropzoneId, indicatorId) {{
            const dropzone = document.getElementById(dropzoneId);
            const indicator = document.getElementById(indicatorId);
            if (input.files.length > 0) {{
                const file = input.files[0];
                indicator.innerText = "✓ " + file.name + " (" + (file.size / 1024 / 1024).toFixed(2) + " MB)";
                indicator.style.display = "flex";
                dropzone.style.borderColor = "var(--success)";
                dropzone.style.background = "rgba(16, 185, 129, 0.03)";
            }} else {{
                indicator.style.display = "none";
                dropzone.style.borderColor = "";
                dropzone.style.background = "";
            }}
        }}

        let activeJobId = null;
        let pollingTimer = null;
        let lastConsoleMessage = "";

        async function submitForm(e) {{
            e.preventDefault();
            
            const btnSubmit = document.getElementById('btn-translate');
            btnSubmit.disabled = true;
            btnSubmit.innerText = "Uploading files...";

            const form = document.getElementById('translate-form');
            const formData = new FormData(form);

            // Set Dry Run explicitly to false if not checked
            if (!document.getElementById('dry_run').checked) {{
                formData.set('dry_run', 'false');
            }}

            try {{
                const response = await fetch('/translate-xliff', {{
                    method: 'POST',
                    body: formData
                }});
                
                const result = await response.json();
                if (result.error) {{
                    alert("Error: " + result.error);
                    btnSubmit.disabled = false;
                    btnSubmit.innerText = "Translate & Process Graphics";
                    return;
                }}

                activeJobId = result.job_id;
                startMonitoring(activeJobId);
            }} catch (error) {{
                alert("Upload failed: " + error.message);
                btnSubmit.disabled = false;
                btnSubmit.innerText = "Translate & Process Graphics";
            }}
        }}

        function startMonitoring(jobId) {{
            // Reset Monitoring UI
            document.getElementById('job-status-badge').className = "status-badge badge-running";
            document.getElementById('job-status-badge').innerText = "Running";
            document.getElementById('status-console').innerText = "Job initiated on server. Starting processing...\n";
            document.getElementById('job-progress-bar').style.width = "5%";
            document.getElementById('download-box').style.display = "none";
            
            // Set phase indicators to pending/running
            setPhaseState('phase-upload', 'running', '⚙️');
            setPhaseState('phase-translate', 'pending', '○');
            setPhaseState('phase-ocr', 'pending', '○');
            setPhaseState('phase-package', 'pending', '○');

            lastConsoleMessage = "";

            // Start Polling Status
            pollingTimer = setInterval(() => pollJobStatus(jobId), 1500);
        }}

        async function pollJobStatus(jobId) {{
            try {{
                const response = await fetch('/status/' + jobId);
                const job = await response.json();
                
                if (job.error) {{
                    handleJobFailure("Server returned error: " + job.error);
                    return;
                }}

                // Update Progress bar
                document.getElementById('job-progress-bar').style.width = job.progress + "%";
                
                // Update console log
                if (job.message && job.message !== lastConsoleMessage) {{
                    const consoleEl = document.getElementById('status-console');
                    consoleEl.innerText += "\\n[" + new Date().toLocaleTimeString() + "] " + job.message;
                    consoleEl.scrollTop = consoleEl.scrollHeight;
                    lastConsoleMessage = job.message;
                }}

                // Update Stats
                const segmentsTotal = job.total_segments || 0;
                const segmentsDone = job.translated_segments || 0;
                const graphicsTotal = job.total_graphics || 0;
                const graphicsDone = job.converted_graphics || 0;

                document.getElementById('stat-segments').innerText = segmentsTotal > 0 ? (segmentsDone + " / " + segmentsTotal) : "-";
                document.getElementById('stat-graphics').innerText = graphicsTotal > 0 ? (graphicsDone + " / " + graphicsTotal) : "-";

                // Update Phases dynamically based on message and progress
                updatePhasesFromState(job);

                if (job.status === 'completed') {{
                    handleJobSuccess(job);
                }} else if (job.status === 'failed') {{
                    handleJobFailure(job.message || "Unknown error during processing.");
                }}

            }} catch (err) {{
                console.error("Polling error:", err);
            }}
        }}

        function updatePhasesFromState(job) {{
            const msg = job.message || "";
            const progress = job.progress || 0;

            // 1. Upload/Extract phase
            if (progress > 10) {{
                setPhaseState('phase-upload', 'completed', '✓');
            }} else {{
                setPhaseState('phase-upload', 'running', '⚙️');
            }}

            // 2. Translation phase
            if (msg.includes("Translating segments") || msg.includes("Writing translation")) {{
                setPhaseState('phase-translate', 'running', '⚙️');
            }} else if (progress > 62 || msg.includes("Processing graphics") || msg.includes("Processed graphic") || job.status === 'completed') {{
                setPhaseState('phase-translate', 'completed', '✓');
            }}

            // 3. OCR Graphic Phase
            if (msg.includes("Processing graphics") || msg.includes("Processed graphic")) {{
                setPhaseState('phase-ocr', 'running', '⚙️');
            }} else if (progress > 91 || msg.includes("Packaging") || msg.includes("zip") || job.status === 'completed') {{
                setPhaseState('phase-ocr', 'completed', '✓');
            }}

            // 4. Rebuild & Package Phase
            if (msg.includes("Packaging") || msg.includes("zip") || msg.includes("unzip")) {{
                setPhaseState('phase-package', 'running', '⚙️');
            }} else if (job.status === 'completed') {{
                setPhaseState('phase-package', 'completed', '✓');
            }}
        }}

        function setPhaseState(id, state, icon) {{
            const el = document.getElementById(id);
            const iconEl = document.getElementById(id + "-icon");
            el.className = "phase-item " + state;
            iconEl.innerText = icon;
        }}

        function handleJobSuccess(job) {{
            clearInterval(pollingTimer);
            document.getElementById('job-status-badge').className = "status-badge badge-completed";
            document.getElementById('job-status-badge').innerText = "Completed";
            document.getElementById('job-progress-bar').style.width = "100%";
            document.getElementById('download-box').style.display = "block";
            
            setPhaseState('phase-upload', 'completed', '✓');
            setPhaseState('phase-translate', 'completed', '✓');
            setPhaseState('phase-ocr', 'completed', '✓');
            setPhaseState('phase-package', 'completed', '✓');

            const consoleEl = document.getElementById('status-console');
            consoleEl.innerText += "\\n\\n[SUCCESS] Translation Deliverables Created successfully!";
            consoleEl.scrollTop = consoleEl.scrollHeight;

            // Reset form button
            const btnSubmit = document.getElementById('btn-translate');
            btnSubmit.disabled = false;
            btnSubmit.innerText = "Translate & Process Graphics";
        }}

        function handleJobFailure(errorMsg) {{
            clearInterval(pollingTimer);
            document.getElementById('job-status-badge').className = "status-badge badge-failed";
            document.getElementById('job-status-badge').innerText = "Failed";
            
            const consoleEl = document.getElementById('status-console');
            consoleEl.innerText += "\\n\\n[FAILED] " + errorMsg;
            consoleEl.scrollTop = consoleEl.scrollHeight;

            // Highlight failed phase
            ['phase-upload', 'phase-translate', 'phase-ocr', 'phase-package'].forEach(id => {{
                const el = document.getElementById(id);
                if (el.classList.contains('running')) {{
                    setPhaseState(id, 'failed', '✗');
                }}
            }});

            // Reset form button
            const btnSubmit = document.getElementById('btn-translate');
            btnSubmit.disabled = false;
            btnSubmit.innerText = "Translate & Process Graphics";
        }}

        function downloadResult() {{
            if (!activeJobId) return;
            window.location.href = '/download/' + activeJobId;
        }}
    </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)


@app.post("/translate")
async def translate_file_endpoint(
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
    file: UploadFile = File(...),
    graphics_zip: UploadFile = File(...),
    target_lang: str = Form(...),
    dry_run: bool = Form(False)
):
    job_id = uuid.uuid4().hex[:8]
    session_dir = UPLOAD_DIR / job_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # 1. Save uploaded XLIFF file
    xlf_path = session_dir / file.filename
    with open(xlf_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 2. Save uploaded Graphics ZIP
    zip_path = session_dir / graphics_zip.filename
    with open(zip_path, "wb") as buffer:
        shutil.copyfileobj(graphics_zip.file, buffer)

    # Register Job
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "pending",
            "progress": 0,
            "message": "Files uploaded successfully. Queuing job...",
            "error": None,
            "total_segments": 0,
            "translated_segments": 0,
            "total_graphics": 0,
            "converted_graphics": 0,
            "output_zip_name": None,
            "output_zip_path": None,
            "target_lang": target_lang,
            "file_name": file.filename,
            "start_time": time.time()
        }

    # Start Background Translation Thread
    t = threading.Thread(
        target=run_translation_job,
        args=(job_id, xlf_path, zip_path, target_lang, dry_run)
    )
    t.daemon = True
    t.start()

    return {"job_id": job_id}


@app.get("/status/{job_id}")
def get_job_status(job_id: str):
    with JOBS_LOCK:
        if job_id not in JOBS:
            raise HTTPException(status_code=404, detail="Job not found")
        # Return a copy of the job status dictionary to prevent mutations during serialization
        return dict(JOBS[job_id])


@app.get("/download/{job_id}")
def download_job_result(job_id: str):
    with JOBS_LOCK:
        if job_id not in JOBS:
            raise HTTPException(status_code=404, detail="Job not found")
        job = JOBS[job_id]
        if job["status"] != "completed":
            raise HTTPException(status_code=400, detail=f"Job is not completed. Current status: {job['status']}")
        zip_path = job["output_zip_path"]
        zip_name = job["output_zip_name"]
    
    if not zip_path or not Path(zip_path).exists():
        raise HTTPException(status_code=404, detail="Deliverable ZIP file not found on server")

    return FileResponse(
        path=zip_path,
        filename=zip_name,
        media_type='application/zip'
    )