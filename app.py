import os
import shutil
import uuid
import zipfile
import argparse
from pathlib import Path
import streamlit as st
import concurrent.futures
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
    format_func=lambda x: f"{LANGUAGES[x]} ({x})",
    help="Choose multiple languages to translate them all in parallel!"
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
            for dl in st.session_state.downloads:
                st.download_button(
                    label=dl["label"],
                    data=dl["data"],
                    file_name=dl["file_name"],
                    mime=dl["mime"],
                    use_container_width=True,
                    key=dl["key"]
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
            
        # Serve file download
        with open(zip_out_path, "rb") as f:
            zip_data = f.read()
            
        return True, target_lang, zip_out_path, zip_data

    except Exception:
        if 'output_root' in locals():
            shutil.rmtree(output_root, ignore_errors=True)
        return False, target_lang, None, None

# Process logic
if start_btn:
    if not xlf_file:
        st.error("Please upload an XLIFF file first.")
    elif not graphics_zip:
        st.error("Please upload the Graphics ZIP archive.")
    elif not target_langs:
        st.error("Please select at least one target language.")
    else:
        st.session_state.downloads = []
        render_downloads()
        
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
        
        overall_success = True
        
        with st.spinner("Processing selected languages in parallel. This may take a few minutes..."):
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(target_langs))) as executor:
                # Submit all tasks
                future_to_lang = {
                    executor.submit(
                        process_language, 
                        lang, 
                        job_id, 
                        xlf_path, 
                        graphics_src_dir, 
                        xlf_name_without_ext
                    ): lang for lang in target_langs
                }
                
                # Gather results as they complete
                for future in concurrent.futures.as_completed(future_to_lang):
                    success, lang, z_path, z_data = future.result()
                    
                    if success:
                        st.session_state.downloads.append({
                            "label": f"Download {LANGUAGES[lang]} ZIP",
                            "data": z_data,
                            "file_name": Path(z_path).name,
                            "mime": "application/zip",
                            "key": f"dl_{lang}_{job_id}"
                        })
                    else:
                        st.error(f"Execution failed for {LANGUAGES[lang]}.")
                        overall_success = False

        shutil.rmtree(session_dir, ignore_errors=True)
        shutil.rmtree(OUTPUT_DIR / job_id, ignore_errors=True)
        
        if overall_success:
            st.success("All translations and OCR completed successfully!")
            st.balloons()
        else:
            st.warning("Completed with some errors.")
            
        render_downloads()
