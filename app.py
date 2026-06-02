import os
import shutil
import uuid
import zipfile
import argparse
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
    layout="wide"
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

# Grid Layout
col_setup, col_monitor = st.columns([5, 4], gap="large")

with col_setup:
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-header">1. Initiate Translation Task</div>', unsafe_allow_html=True)
    
    xlf_file = st.file_uploader(
        "Upload XLIFF Source Document (.xlf, .xliff)", 
        type=["xlf", "xliff"],
        help="Select the FrameMaker-exported XLIFF document."
    )
    
    graphics_zip = st.file_uploader(
        "Upload Source Graphics ZIP Archive (.zip)", 
        type=["zip"],
        help="Upload the ZIP file containing referenced graphics."
    )
    
    target_langs = st.multiselect(
        "Select Target Languages",
        options=list(LANGUAGES.keys()),
        format_func=lambda x: f"{LANGUAGES[x]} ({x})"
    )
    

    
    st.markdown('<br>', unsafe_allow_html=True)
    start_btn = st.button("Translate & Process Graphics", use_container_width=True, type="primary")
    st.markdown('</div>', unsafe_allow_html=True)

with col_monitor:
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown('<div class="card-header">2. Execution Monitor</div>', unsafe_allow_html=True)
    
    # Progress and status elements
    progress_bar = st.progress(0)
    status_text = st.empty()
    status_text.text("Waiting to start task...")
    
    # Statistics metrics
    st.markdown("### Metrics")
    m_col1, m_col2 = st.columns(2)
    with m_col1:
        seg_metric = st.metric("Segments Translated", "0 / 0")
    with m_col2:
        img_metric = st.metric("Graphics Replaced", "0 / 0")
        
    st.markdown("### Console Log")
    log_area = st.empty()
    log_area.text_area("Logs Console", value="Initialize a task to view execution logs.", height=150, disabled=True)
    
    if "downloads" not in st.session_state:
        st.session_state.downloads = []

    # Download Button Area
    download_area = st.empty()
    
    def render_downloads():
        if st.session_state.downloads:
            with download_area.container():
                st.markdown("### Available Downloads")
                for dl in st.session_state.downloads:
                    st.download_button(
                        label=dl["label"],
                        data=dl["data"],
                        file_name=dl["file_name"],
                        mime=dl["mime"],
                        use_container_width=True,
                        key=dl["key"]
                    )
                    
    render_downloads()
    st.markdown('</div>', unsafe_allow_html=True)

# Process logic
if start_btn:
    if not xlf_file:
        st.error("Please upload an XLIFF file first.")
    elif not graphics_zip:
        st.error("Please upload the Graphics ZIP archive.")
    elif not target_langs:
        st.error("Please select at least one target language.")
    else:
        # Initializing job
        st.session_state.downloads = []
        render_downloads()
        status_text.info("Saving uploads and extracting source assets...")
        progress_bar.progress(5)
        
        job_id = uuid.uuid4().hex[:8]
        session_dir = UPLOAD_DIR / job_id
        session_dir.mkdir(parents=True, exist_ok=True)
        
        # Save uploads
        xlf_path = session_dir / xlf_file.name
        with open(xlf_path, "wb") as f:
            f.write(xlf_file.getbuffer())
            
        zip_path = session_dir / graphics_zip.name
        with open(zip_path, "wb") as f:
            f.write(graphics_zip.getbuffer())
            
        # Extract graphics
        graphics_src_dir = session_dir / "graphics_src"
        graphics_src_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(graphics_src_dir)
            
        xlf_name_without_ext = xlf_file.name.replace('.xlf', '').replace('.xliff', '')
        
        # Console Log Stream
        console_logs = [f"[{job_id}] Task initialized successfully."]
        log_area.text_area("Logs Console", value="\n".join(console_logs), height=150, disabled=True)

        # Start Processing
        translation_args = argparse.Namespace(
            resume=False,
            batch_size=40,
            dry_run=False,
            graphics_source_folder=str(graphics_src_dir)
        )
        
        overall_success = True
        
        for target_lang in target_langs:
            output_root = OUTPUT_DIR / job_id / f"translated_{target_lang}_{xlf_name_without_ext}"
            output_root.mkdir(parents=True, exist_ok=True)
            
            console_logs.append(f"[{job_id}] Starting translation for language: {target_lang}")
            log_area.text_area("Logs Console", value="\n".join(console_logs[-8:]), height=150, disabled=True)
            status_text.info(f"Processing language: {LANGUAGES[target_lang]} ({target_lang})")
            
            # Progress updates callback
            def progress_cb(msg: str, current: int, total: int, stats: dict = None):
                console_logs.append(f"[{job_id}] [{target_lang}] {msg}")
                # Keep logs container scrolling
                log_area.text_area("Logs Console", value="\n".join(console_logs[-8:]), height=150, disabled=True)
                status_text.info(f"[{target_lang}] {msg}")
                
                # Update metrics
                if stats:
                    seg_total = stats.get("total_segments", 0)
                    seg_done = stats.get("translated_segments", 0)
                    img_total = stats.get("total_graphics", 0)
                    img_done = stats.get("converted_graphics", 0)
                    
                    if seg_total > 0:
                        seg_metric.metric(f"Segments ({target_lang})", f"{seg_done} / {seg_total}")
                    if img_total > 0:
                        img_metric.metric(f"Graphics ({target_lang})", f"{img_done} / {img_total}")
                        
                # Update progress bar
                if "Translating segments" in msg:
                    pct = 15 + int((current / max(1, total)) * 45)
                elif "Processed graphic" in msg or "Processing graphics" in msg:
                    pct = 65 + int((current / max(1, total)) * 25)
                elif "Writing translation" in msg:
                    pct = 62
                else:
                    pct = 10
                progress_bar.progress(min(92, pct))

            try:
                success = run_translation(
                    input_path=xlf_path,
                    output_root=output_root,
                    target_lang=target_lang,
                    args=translation_args,
                    model_to_use=DEFAULT_MODEL,
                    progress_callback=progress_cb
                )
                
                if not success:
                    st.error(f"Translation logic failed for {target_lang}. Check console logs.")
                    status_text.error(f"Failed for {target_lang}.")
                    overall_success = False
                else:
                    progress_bar.progress(93)
                    status_text.info(f"Packaging translated assets into ZIP deliverable for {target_lang}...")
                    
                    zip_name = f"translated_{target_lang}_{xlf_name_without_ext}"
                    zip_out_path = OUTPUT_DIR / f"{zip_name}.zip"
                    
                    # Zipping output
                    with zipfile.ZipFile(zip_out_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        for path in sorted(output_root.rglob("*")):
                            if not path.is_file():
                                continue
                            rel = path.relative_to(output_root)
                            arcname = f"{output_root.name}/{rel.as_posix()}"
                            zf.write(path, arcname=arcname)
                            
                    # Unzip folder server-side
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
                    downloads_mirrored = False
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
                            downloads_mirrored = True
                            console_logs.append(f"[{job_id}] Successfully extracted double-nested folder structure to local Downloads folder for {target_lang}.")
                    except Exception as e:
                        console_logs.append(f"[{job_id}] Mirroring download warning for {target_lang}: {e}")
                        
                    # Serve file download
                    with open(zip_out_path, "rb") as f:
                        zip_data = f.read()
                    
                    st.session_state.downloads.append({
                        "label": f"Download {LANGUAGES[target_lang]} ZIP",
                        "data": zip_data,
                        "file_name": f"{zip_name}.zip",
                        "mime": "application/zip",
                        "key": f"dl_{target_lang}_{job_id}"
                    })
                    render_downloads()
                    
            except Exception as e:
                st.error(f"Execution Error for {target_lang}: {e}")
                status_text.error(f"Execution failed for {target_lang}.")
                overall_success = False
                if 'output_root' in locals():
                    shutil.rmtree(output_root, ignore_errors=True)

        shutil.rmtree(session_dir, ignore_errors=True)
        shutil.rmtree(OUTPUT_DIR / job_id, ignore_errors=True)
        
        if overall_success:
            progress_bar.progress(100)
            status_text.success("All translations and OCR completed successfully!")
            console_logs.append(f"[{job_id}] Process Completed successfully.")
            log_area.text_area("Logs Console", value="\n".join(console_logs[-8:]), height=150, disabled=True)
            st.balloons()
        else:
            status_text.warning("Completed with some errors. Check console logs.")