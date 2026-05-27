import sys
import os
import zipfile
import httpx
import tempfile
import base64
import gzip
import re
from pathlib import Path
from lxml import etree

# --- Config ---
API_URL = "http://127.0.0.1:8000/translate-xliff"
XLF_PATH = Path("C:/Users/Lenovo/Desktop/GRAPHIC_FOLDER_UPLOAD/50128856_C_032019_en_Title.mifml.xlf")
GRAPHICS_DIR = Path("C:/Users/Lenovo/Desktop/HEALTHARK/ALL_FILES/fm_file/Graphics")
TARGET_LANG = "de"
DRY_RUN = True  # Set to False to call OpenAI live translation API

DOWNLOAD_DIR = Path("C:/Users/Lenovo/Downloads")
OUTPUT_ZIP_NAME = f"translated_{TARGET_LANG}_{XLF_PATH.name.replace('.xlf', '').replace('.xliff', '')}.zip"
OUTPUT_ZIP_PATH = DOWNLOAD_DIR / OUTPUT_ZIP_NAME
EXTRACTION_DIR = DOWNLOAD_DIR / OUTPUT_ZIP_NAME.replace(".zip", "")

def create_temp_graphics_zip(source_dir: Path) -> Path:
    print(f"Zipping graphics folder {source_dir} ...")
    temp_zip = Path(tempfile.gettempdir()) / "temp_graphics.zip"
    if temp_zip.exists():
        temp_zip.unlink()
        
    # Use ZIP_STORED (no compression) so that zipping large directories is instant
    with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_STORED) as zf:
        for root, _, files in os.walk(source_dir):
            for file in files:
                file_path = Path(root) / file
                rel_path = file_path.relative_to(source_dir)
                # Keep it under graphics/ inside the zip to match standard uploads
                zf.write(file_path, arcname=f"graphics/{rel_path.as_posix()}")
    print(f"Created temp graphics zip: {temp_zip} ({temp_zip.stat().st_size} bytes)")
    return temp_zip

def test_api():
    if not XLF_PATH.exists():
        print(f"Error: XLIFF file not found at {XLF_PATH}")
        sys.exit(1)
    if not GRAPHICS_DIR.exists():
        print(f"Error: Graphics directory not found at {GRAPHICS_DIR}")
        sys.exit(1)
        
    # 1. Zip the graphics
    temp_zip_path = create_temp_graphics_zip(GRAPHICS_DIR)
    
    try:
        # 2. Call the FastAPI endpoint
        print(f"Sending POST request to {API_URL} (dry_run={DRY_RUN}) ...")
        
        with open(XLF_PATH, "rb") as xlf_file, open(temp_zip_path, "rb") as zip_file:
            files = {
                "file": (XLF_PATH.name, xlf_file, "application/xml"),
                "graphics_zip": (temp_zip_path.name, zip_file, "application/zip")
            }
            data = {
                "target_lang": TARGET_LANG,
                "dry_run": str(DRY_RUN)
            }
            
            # Disable timeout limit for long OCR tasks
            response = httpx.post(API_URL, files=files, data=data, timeout=None)
            
        if response.status_code != 200:
            print(f"API Error (Status {response.status_code}):")
            try:
                print(response.json())
            except Exception:
                print(response.text[:1000])
            return
            
        print("API Response received successfully!")
        
        # 3. Save the response ZIP
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_ZIP_PATH.write_bytes(response.content)
        print(f"Saved deliverable zip to: {OUTPUT_ZIP_PATH}")
        
        # 4. Extract the ZIP directly to DOWNLOAD_DIR
        if EXTRACTION_DIR.exists():
            import shutil
            shutil.rmtree(EXTRACTION_DIR)
        
        print(f"Extracting zip directly to: {DOWNLOAD_DIR} ...")
        with zipfile.ZipFile(OUTPUT_ZIP_PATH, 'r') as zf:
            zf.extractall(DOWNLOAD_DIR)
            
        # Replicate graphics and text_conversion_file to a double-nested path
        double_nested_dir = EXTRACTION_DIR / EXTRACTION_DIR.name
        double_nested_dir.mkdir(parents=True, exist_ok=True)
        if (EXTRACTION_DIR / "graphics").exists():
            import shutil
            shutil.copytree(EXTRACTION_DIR / "graphics", double_nested_dir / "graphics", dirs_exist_ok=True)
        if (EXTRACTION_DIR / "text_conversion_file").exists():
            import shutil
            shutil.copytree(EXTRACTION_DIR / "text_conversion_file", double_nested_dir / "text_conversion_file", dirs_exist_ok=True)
            
        # 5. List the extracted directory contents
        print("\nExtracted ZIP Files:")
        for root, _, files in os.walk(EXTRACTION_DIR):
            for f in files:
                full_p = Path(root) / f
                print(f"  - {full_p.relative_to(EXTRACTION_DIR)}")
                
        # 6. Read and print the references inside the decoded MIF blob
        translated_xlf_file = list(EXTRACTION_DIR.glob("**/text_conversion_file/*.xlf"))
        if translated_xlf_file:
            xlf_file_path = translated_xlf_file[0]
            print(f"\nDecoding XLIFF internal-file blob from: {xlf_file_path.name}")
            
            tree = etree.parse(str(xlf_file_path))
            internal_el = None
            for elem in tree.getroot().iter():
                if elem.tag.split("}")[-1] == "internal-file":
                    internal_el = elem
                    break
                    
            if internal_el is not None and internal_el.text:
                compressed = base64.b64decode(internal_el.text.strip())
                if compressed[:2] == b'\x1f\x8b':
                    mif = gzip.decompress(compressed).decode("utf-8", errors="replace")
                else:
                    mif = compressed.decode("utf-8", errors="replace")
                    
                print("\nVerification of 'Titel_new1.jpg' inside MIF:")
                found = False
                for match in re.finditer(r'Titel_new1\.jpg', mif, re.IGNORECASE):
                    found = True
                    start = max(0, match.start() - 150)
                    end = min(len(mif), match.end() + 150)
                    print(f"\n[MIF snippet around Match]:\n{mif[start:end]}")
                if not found:
                    print("  Warning: 'Titel_new1.jpg' not found in translated MIF blob.")
            else:
                print("  Error: No internal-file found in the translated XLIFF.")
        else:
            print("  Error: No XLIFF file found in text_conversion_file/ directory.")
            
    finally:
        # Clean up temp ZIP
        if temp_zip_path.exists():
            temp_zip_path.unlink()

if __name__ == "__main__":
    test_api()
