import os
import shutil
import uuid
import zipfile
import argparse
import time
import concurrent.futures
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv

# Load env variables
load_dotenv()

# Setup paths
UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("output")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Import translation modules
from image_ocr_translator import process_xlf_references
from translate_xliff_openai_2 import translate_file as run_translation, MODEL as DEFAULT_MODEL, LANGUAGES

# Page config
st.set_page_config(
    page_title="FrameMaker Translation Studio",
    page_icon="📝",
    layout="centered"
)

# Premium layout customization using HTML & CSS
st.markdown("""
    <style>
        /* Google Font Import */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap');
        
        .stApp {
            background-color: #080a11;
            color: #f3f4f6;
            font-family: 'Inter', sans-serif;
        }
        
        /* Glowing main header */
        .main-header {
            text-align: center;
            margin-bottom: 2rem;
            padding-top: 1rem;
        }
        
        .main-title {
            font-family: 'Outfit', sans-serif;
            font-size: 2.75rem;
            font-weight: 800;
            letter-spacing: -0.04em;
            background: linear-gradient(135deg, #ffffff 30%, #818cf8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.5rem;
        }
        
        .main-subtitle {
            color: #9ca3af;
            font-size: 1.1rem;
            font-weight: 400;
        }
        
        /* Glassmorphism style cards */
        .glass-card {
            background: rgba(17, 22, 34, 0.7);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            padding: 1.75rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
            backdrop-filter: blur(15px);
            -webkit-backdrop-filter: blur(15px);
        }
        
        .card-header {
            font-family: 'Outfit', sans-serif;
            font-size: 1.35rem;
            font-weight: 600;
            color: #ffffff;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            padding-bottom: 0.5rem;
            margin-bottom: 1.25rem;
        }
        
        /* Styler for multiselect */
        div[data-baseweb="select"] > div {
            background-color: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            color: white;
        }
    </style>
""", unsafe_allow_html=True)

def get_downloads_dir() -> Path:
    p = Path(os.environ.get("USERPROFILE", "C:/Users/Lenovo")) / "Downloads"
    if p.exists():
        return p
    p = Path.home() / "Downloads"
    if p.exists():
        return p
    return Path("C:/Users/Lenovo/Downloads")

# Render Header
st.markdown("""
    <div class="main-header">
        <div class="main-title">FrameMaker Translation Studio</div>
        <div class="main-subtitle">AI-powered XLIFF Document Translation & Referenced Graphics OCR replacement</div>
    </div>
""", unsafe_allow_html=True)


if "downloads" not in st.session_state:
    st.session_state.downloads = []

st.markdown('<div class="glass-card">', unsafe_allow_html=True)
st.markdown('<div class="card-header">Initiate Translation Task</div>', unsafe_allow_html=True)

xlf_files = st.file_uploader(
    "Upload XLIFF Source Document(s) (.xlf, .xliff)", 
    type=["xlf", "xliff"],
    accept_multiple_files=True,
    help="Select one or more FrameMaker-exported XLIFF documents."
)

graphics_zips = st.file_uploader(
    "Upload Source Graphics ZIP Archive(s) (.zip)", 
    type=["zip"],
    accept_multiple_files=True,
    help="Upload one or more ZIP files containing referenced graphics."
)

target_langs = st.multiselect(
    "Select Target Languages",
    options=list(LANGUAGES.keys()),
    format_func=lambda x: f"{LANGUAGES[x]} ({x})",
    help="Choose multiple languages to translate them all."
)

max_workers_slider = st.slider(
    "Parallel Processing Threads",
    min_value=1,
    max_value=10,
    value=2,
    help="Increase this to translate faster. If you encounter 502 Connection Errors, reduce it to 1."
)

st.markdown('<br>', unsafe_allow_html=True)
start_btn = st.button("Translate & Process Graphics", use_container_width=True, type="primary")
st.markdown('</div>', unsafe_allow_html=True)

# Download Button Area
download_area = st.empty()

def render_downloads():
    if st.session_state.downloads:
        with download_area.container():
            st.markdown('<div class="glass-card">', unsafe_allow_html=True)
            st.markdown('<div class="card-header">Available Downloads</div>', unsafe_allow_html=True)
            
            options = [dl["label"] for dl in st.session_state.downloads]
            selected_label = st.selectbox("Select a translated package to download:", options=options)
            
            selected_dl = next((dl for dl in st.session_state.downloads if dl["label"] == selected_label), None)
            
            if selected_dl and os.path.exists(selected_dl["path"]):
                with open(selected_dl["path"], "rb") as f:
                    st.download_button(
                        label=selected_dl["label"],
                        data=f,
                        file_name=selected_dl["file_name"],
                        mime=selected_dl["mime"],
                        use_container_width=True,
                        key="active_download_btn"
                    )
            st.markdown('</div>', unsafe_allow_html=True)
                
render_downloads()

def process_language(target_lang, job_id, xlf_path, graphics_src_dir, xlf_name_without_ext):
    output_root = OUTPUT_DIR / job_id / f"translated_{target_lang}_{xlf_name_without_ext}"
    output_root.mkdir(parents=True, exist_ok=True)
    
    translation_args = argparse.Namespace(
        resume=False,
        batch_size=40,
        dry_run=False,
        graphics_source_folder=str(graphics_src_dir)
    )
    
    def noop_progress(msg: str, current: int, total: int, stats: dict = None):
        # We drop UI updates since they break in Streamlit threads, 
        # and instead rely on the main spinner for UX.
        pass

    try:
        success = run_translation(
            input_path=xlf_path,
            output_root=output_root,
            target_lang=target_lang,
            args=translation_args,
            model_to_use=DEFAULT_MODEL,
            progress_callback=noop_progress
        )
        
        if not success:
            return False, target_lang, None, None
            
        zip_name = f"translated_{target_lang}_{xlf_name_without_ext}_{job_id}"
        zip_out_path = OUTPUT_DIR / f"{zip_name}.zip"
        
        # Zipping output
        with zipfile.ZipFile(zip_out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(output_root.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(output_root)
                arcname = f"{output_root.name}/{rel.as_posix()}"
                zf.write(path, arcname=arcname)
                
        # Unzip folder server-side for delivery
        unzip_dest = OUTPUT_DIR / zip_name
        if unzip_dest.exists():
            shutil.rmtree(unzip_dest, ignore_errors=True)
        with zipfile.ZipFile(zip_out_path, 'r') as zip_ref:
            zip_ref.extractall(OUTPUT_DIR)
            
        srv_double_nested = unzip_dest / zip_name
        srv_double_nested.mkdir(parents=True, exist_ok=True)
        if (unzip_dest / "graphics").exists():
            shutil.copytree(unzip_dest / "graphics", srv_double_nested / "graphics", dirs_exist_ok=True)
        if (unzip_dest / "text_conversion_file").exists():
            shutil.copytree(unzip_dest / "text_conversion_file", srv_double_nested / "text_conversion_file", dirs_exist_ok=True)

        # Mirror copy to local Downloads directory
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
        except Exception:
            pass # Ignore mirroring errors if Downloads folder is tricky
            
        # Do not load file into RAM to prevent server OOM
        return True, target_lang, zip_out_path, None

    except Exception:
        if 'output_root' in locals():
            shutil.rmtree(output_root, ignore_errors=True)
        return False, target_lang, None, None

# Process logic
if start_btn:
    if not xlf_files:
        st.error("Please upload at least one XLIFF file.")
    elif not graphics_zips:
        st.error("Please upload at least one Graphics ZIP archive.")
    elif not target_langs:
        st.error("Please select at least one target language.")
    else:
        st.session_state.downloads = []
        render_downloads()
        
        job_id = uuid.uuid4().hex[:8]
        session_dir = UPLOAD_DIR / job_id
        session_dir.mkdir(parents=True, exist_ok=True)
        
        # Save uploads
        xlf_paths = []
        for f in xlf_files:
            p = session_dir / f.name
            with open(p, "wb") as out:
                out.write(f.getbuffer())
            xlf_paths.append(p)
            
        # Extract graphics
        graphics_src_dir = session_dir / "graphics_src"
        graphics_src_dir.mkdir(parents=True, exist_ok=True)
        for gz in graphics_zips:
            zip_path = session_dir / gz.name
            with open(zip_path, "wb") as out:
                out.write(gz.getbuffer())
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(graphics_src_dir)
                
        overall_success = True
        
        tasks = []
        for xlf_path in xlf_paths:
            xlf_name_without_ext = xlf_path.name.replace('.xlf', '').replace('.xliff', '')
            for target_lang in target_langs:
                tasks.append((target_lang, job_id, xlf_path, graphics_src_dir, xlf_name_without_ext))
                
        with st.spinner(f"Processing {len(tasks)} translation tasks..."):
            progress_bar = st.progress(0)
            status_text = st.empty()
            start_time = time.time()
            completed_tasks = 0
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers_slider, len(tasks))) as executor:
                future_to_task = {
                    executor.submit(
                        process_language, 
                        t[0], 
                        t[1], 
                        t[2], 
                        t[3], 
                        t[4]
                    ): t for t in tasks
                }
                
                for future in concurrent.futures.as_completed(future_to_task):
                    t = future_to_task[future]
                    lang = t[0]
                    xlf_name = t[4]
                    
                    completed_tasks += 1
                    elapsed = time.time() - start_time
                    avg_time = elapsed / completed_tasks
                    remaining = avg_time * (len(tasks) - completed_tasks)
                    mins, secs = divmod(int(remaining), 60)
                    time_str = f"Estimated time left: {mins}m {secs}s"
                    
                    status_text.info(f"**Completed {completed_tasks} of {len(tasks)}** | Just finished: {LANGUAGES[lang]} for *{xlf_name}* | {time_str}")
                    
                    success, returned_lang, z_path, _ = future.result()
                    
                    if success:
                        st.session_state.downloads.append({
                            "label": f"Download {LANGUAGES[lang]} ZIP ({xlf_name})",
                            "path": str(z_path),
                            "file_name": Path(z_path).name,
                            "mime": "application/zip",
                            "key": f"dl_{lang}_{xlf_name}_{job_id}"
                        })
                    else:
                        st.error(f"Execution failed for {LANGUAGES[lang]} on {xlf_name}.")
                        overall_success = False
                        
                    progress_bar.progress(completed_tasks / len(tasks))
                
            status_text.empty()
            progress_bar.empty()

        shutil.rmtree(session_dir, ignore_errors=True)
        shutil.rmtree(OUTPUT_DIR / job_id, ignore_errors=True)
        
        if overall_success:
            st.success("All translations and OCR completed successfully!")
            st.balloons()
        else:
            st.warning("Completed with some errors.")
            
        render_downloads()
