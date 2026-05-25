import os
import streamlit as st
import httpx
from pathlib import Path

# Load environment variables if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# -------------------------------------------------
# Page Settings & Aesthetic Styling
# -------------------------------------------------
st.set_page_config(
    page_title="FrameMaker XLIFF & Graphics Translator",
    page_icon="🔮",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# Custom premium styling using glassmorphism, Google Fonts, and modern CSS
custom_css = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Inter:wght@300;400;500;600&display=swap');

/* Apply general fonts */
html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

h1, h2, h3, .main-title {
    font-family: 'Outfit', sans-serif;
    font-weight: 800;
}

/* Gradient background for the app */
.stApp {
    background: radial-gradient(circle at 50% 50%, #111827 0%, #030712 100%);
    color: #f3f4f6;
}

/* Main title styling */
.main-title {
    text-align: center;
    background: linear-gradient(135deg, #818cf8 0%, #c084fc 50%, #f472b6 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-size: 3rem;
    margin-bottom: 0.5rem;
    letter-spacing: -0.05em;
    animation: fadeIn 1s ease-out;
}

.sub-title {
    text-align: center;
    color: #9ca3af;
    font-size: 1.1rem;
    margin-bottom: 2.5rem;
    font-weight: 300;
}

/* Glassmorphism Card Container */
.glass-card {
    background: rgba(17, 24, 39, 0.7);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 20px;
    padding: 2.5rem;
    box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
    margin-bottom: 2rem;
}

/* Custom styled inputs & file uploaders */
.stFileUploader section {
    background-color: rgba(31, 41, 55, 0.5) !important;
    border: 2px dashed rgba(129, 140, 248, 0.3) !important;
    border-radius: 12px !important;
    padding: 1.5rem !important;
    transition: all 0.3s ease !important;
}

.stFileUploader section:hover {
    border-color: rgba(168, 85, 247, 0.8) !important;
    background-color: rgba(31, 41, 55, 0.8) !important;
    box-shadow: 0 0 15px rgba(168, 85, 247, 0.15);
}

/* Pulsing loader animation */
@keyframes pulse {
    0% { transform: scale(0.98); opacity: 0.8; box-shadow: 0 0 0 0 rgba(129, 140, 248, 0.4); }
    50% { transform: scale(1); opacity: 1; box-shadow: 0 0 20px 10px rgba(129, 140, 248, 0.1); }
    100% { transform: scale(0.98); opacity: 0.8; box-shadow: 0 0 0 0 rgba(129, 140, 248, 0); }
}

.loading-container {
    padding: 2rem;
    border-radius: 12px;
    background: rgba(99, 102, 241, 0.1);
    border: 1px solid rgba(99, 102, 241, 0.2);
    text-align: center;
    animation: pulse 2s infinite;
}

/* Button overrides for a super-premium look */
div.stButton > button:first-child {
    background: linear-gradient(135deg, #6366f1 0%, #a855f7 100%) !important;
    color: white !important;
    border: none !important;
    padding: 0.75rem 2rem !important;
    font-size: 1.1rem !important;
    font-weight: 600 !important;
    border-radius: 12px !important;
    width: 100% !important;
    box-shadow: 0 4px 15px rgba(99, 102, 241, 0.3) !important;
    transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275) !important;
}

div.stButton > button:first-child:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 25px rgba(168, 85, 247, 0.5) !important;
    background: linear-gradient(135deg, #4f46e5 0%, #9333ea 100%) !important;
}

div.stButton > button:first-child:active {
    transform: translateY(1px) !important;
}

/* Download button styling */
div.stDownloadButton > button:first-child {
    background: linear-gradient(135deg, #10b981 0%, #059669 100%) !important;
    color: white !important;
    border: none !important;
    padding: 0.75rem 2rem !important;
    font-size: 1.1rem !important;
    font-weight: 600 !important;
    border-radius: 12px !important;
    width: 100% !important;
    box-shadow: 0 4px 15px rgba(16, 185, 129, 0.3) !important;
    transition: all 0.3s ease !important;
}

div.stDownloadButton > button:first-child:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 25px rgba(5, 150, 105, 0.5) !important;
}

/* Hide default streamlit menu/footer if desired */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

# -------------------------------------------------
# Configuration & Backend Connection
# -------------------------------------------------
DEFAULT_BACKEND = os.getenv("FASTAPI_URL", "https://language-translation-2323.onrender.com")
# Fallback to local dev if user desires, or render url
env_secret_key = os.getenv("API_SECRET_KEY", "")

# -------------------------------------------------
# Main UI Content
# -------------------------------------------------
st.markdown('<div class="main-title">langconvapi</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Adobe FrameMaker XLIFF & Graphics Translator Interface</div>', unsafe_allow_html=True)

# Wrap inside a glassmorphism card styled structure
with st.container():
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    
    # 1. API Configurations
    st.subheader("🔑 Backend Configuration")
    backend_url = st.text_input(
        "API Base URL",
        value=DEFAULT_BACKEND,
        placeholder="https://language-translation-2323.onrender.com",
        help="Specify the root URL of your deployed FastAPI service."
    )
    
    api_key = st.text_input(
        "API Secret Key (Bearer Token)",
        value=env_secret_key,
        type="password",
        placeholder="Enter your API_SECRET_KEY",
        help="The Bearer token configured on your backend service."
    )
    
    st.divider()
    
    # 2. File Upload Section
    st.subheader("📁 Translation Workspace")
    
    xliff_file = st.file_uploader(
        "Upload FrameMaker XLIFF File (.xlf / .xliff)",
        type=["xlf", "xliff"],
        help="This is the main translation file containing text segments."
    )
    
    zip_file = st.file_uploader(
        "Upload Graphics ZIP Archive (Optional)",
        type=["zip"],
        help="Provide this ZIP folder containing graphics/images referenced by the manual for OCR/translation."
    )
    
    st.divider()
    
    # 3. Parameters
    st.subheader("🌐 Target Language")
    
    LANGUAGES = {
        "de": "German (de)",
        "fr": "French (fr)",
        "es": "Spanish (es)",
        "it": "Italian (it)",
        "zh-CN": "Chinese Simplified (zh-CN)",
        "ja": "Japanese (ja)",
        "ko": "Korean (ko)"
    }
    
    target_lang = st.selectbox(
        "Select target language code:",
        options=list(LANGUAGES.keys()),
        format_func=lambda x: LANGUAGES[x]
    )
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # 4. Action execution
    if st.button("🔮 Run Translation Pipeline"):
        if not xliff_file:
            st.error("❌ Please upload an XLIFF (.xlf or .xliff) file to proceed.")
        else:
            # Prepare files dictionary for multipart/form-data
            files_payload = {
                "file": (xliff_file.name, xliff_file.read(), xliff_file.type or "application/octet-stream")
            }
            if zip_file:
                files_payload["graphics_zip"] = (
                    zip_file.name,
                    zip_file.read(),
                    zip_file.type or "application/zip"
                )
                
            # Prepare headers with Authorization token if key is entered
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
                
            # Endpoint selection
            endpoint = f"{backend_url.rstrip('/')}/translate/upload"
            
            st.markdown(
                '<div class="loading-container">⏳ Processing files & invoking OpenAI translation pipeline...<br>'
                '<small style="color:#a5b4fc">Do not close this tab. Large files or OCR processing might take 2-5 minutes.</small></div>',
                unsafe_allow_html=True
            )
            
            try:
                # Issue synchronous post request with high timeout
                with httpx.Client(timeout=900.0) as client:
                    response = client.post(
                        endpoint,
                        params={"target_lang": target_lang},
                        files=files_payload,
                        headers=headers
                    )
                    
                # Inspect response
                if response.status_code == 200:
                    st.success("🎉 Translation Pipeline completed successfully!")
                    
                    # Store file in memory to expose to download button
                    output_bytes = response.content
                    filename = f"translated_{target_lang}.zip"
                    
                    st.download_button(
                        label="⬇️ Download Translated Manual & Graphics (ZIP)",
                        data=output_bytes,
                        file_name=filename,
                        mime="application/zip"
                    )
                else:
                    # Attempt to extract error details
                    try:
                        err_json = response.json()
                        error_detail = err_json.get("detail", response.text)
                    except Exception:
                        error_detail = response.text or f"HTTP status code {response.status_code}"
                        
                    st.error(f"❌ Translation failed with backend error: {error_detail}")
            except httpx.ConnectError:
                st.error("❌ Connection failed. Please check your API Base URL and verify the backend is running.")
            except httpx.TimeoutException:
                st.error("❌ The request timed out. The backend is taking too long to translate your manual.")
            except Exception as e:
                st.error(f"❌ An unexpected error occurred: {str(e)}")
                
    st.markdown('</div>', unsafe_allow_html=True)
