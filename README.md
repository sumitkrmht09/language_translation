# FrameMaker XLIFF & Graphics Translation Pipeline

An automated translation and graphics processing system tailored for Adobe FrameMaker documents exported to XLIFF format (`.xlf` / `.xliff`). 

This project integrates a FastAPI backend hosting a translation pipeline powered by OpenAI (GPT-4o), an intelligent image search and OCR-based graphic translation locator, and a beautiful Streamlit web interface for easy drag-and-drop translations.

---

## 🏗️ Architecture & Component Overview

The repository consists of the following components:

1. **FastAPI Backend Service (`app.py`)**:
   * Exposes two main endpoints:
     * `POST /translate`: Accepts file download URLs for XLIFF and Graphics ZIP files. Ideal for headless integrations or ChatGPT Custom GPT Actions.
     * `POST /translate/upload`: Accepts direct multipart binary uploads for file translation (Postman, curl, direct frontend uploads).
   * Automatically handles CP437/CP1252 zip filename encoding fixes for zip files uploaded from Windows to Linux.

2. **Core Translation Engine (`translate_xliff_openai_2.py`)**:
   * Parses the `.xlf` XML tree, extracts localizable segments, classifies segments (safety-critical, standard translation, skip), and batch-translates them via OpenAI API.
   * Performs language-header updates, structural safety validation, and checkpoint-saving to allow resuming interrupted jobs.
   * Exports a safety-review Excel spreadsheet (`_safety_review.xlsx`) containing side-by-side source and translated text segments.

3. **Graphics Search & OCR Processor (`image_ocr_translator.py`)**:
   * Parses the device-independent path references (`ImportObFileDI` and `ImportObFile`) inside the FrameMaker document binary block.
   * Scans the uploaded Graphics ZIP archive using a robust case-insensitive, Unicode NFC-normalized matching algorithm.
   * Performs text detection (OCR) and translates embedded text in supported image formats (PNG, JPG, PDF) using the target language.
   * Preserves the exact multi-level folder structure as referenced by the original FrameMaker file.

4. **Streamlit Frontend Application (`frontend/streamlit_app.py`)**:
   * Offers a clean UI with Outfit & Inter typography, dark radial background gradients, and glassmorphic cards.
   * Connects to the FastAPI backend with secure token authorization, progress feedback, and instant output ZIP downloads.

---

## 📂 Deliverable Directory Structure & Path Resolution

To allow Adobe FrameMaker to automatically detect and load images when importing the translated XLIFF file without needing to manually rewrite paths, the final output ZIP is structured to match the document's original relative path references.

### Layout inside the generated ZIP:
Extracting `translated_de.zip` yields the following layout:
```text
translated_de/
 ├── Graphics/
 │    └── Graphics/
 │         ├── Logo.pdf
 │         └── Gerätebuch_gb.pdf
 └── translated_de/
      └── 50128856_C_032019_en_13.mifml.xlf
```

### Path Resolution Logic:
* Inside the original XLIFF document, images are referenced via relative paths starting with a parent directory escape, for example: `../Graphics/Graphics/Logo.pdf`.
* By putting the XLIFF file in a nested `translated_de/` subfolder, its physical path relative to the root is `translated_de/translated_de/50128856_C_032019_en_13.mifml.xlf`.
* When FrameMaker attempts to load `../Graphics/Graphics/Logo.pdf`, it navigates one level up from `translated_de/translated_de/` (which resolves to the ZIP root folder `translated_de/`) and successfully accesses the `Graphics/Graphics/Logo.pdf` folder structure.
* Path-rewriting is commented out by design to rely purely on this relative directory mapping, avoiding compatibility issues with FrameMaker importing.

---

## 🛠️ Key Technical Features & Workarounds

* **Binary Image Embedding inside XLIFF**: For image formats like `.png`, `.jpeg`, `.jpg`, `.pdf`, the pipeline reads the translated files from disk, base64 encodes their binary data, and embeds them directly inside the XLIFF document as standard XLIFF `<bin-unit>` objects inside the XML `<body>`. This provides a unified XLIFF container holding both text translations and binary graphics assets.
* **Resilient Copying Fallback**: If an image or PDF document fails to open, parse, or translate during OCR (due to file corruption or unusual format states), the system gracefully catches the error, blindly copies the original graphic file, and maps it to prevent any pipeline crashes or broken links in FrameMaker.
* **Unicode NFC Normalization**: Prevents composition discrepancies of non-ASCII characters (e.g. German umlauts like `ä`, `ö`, `ü`) during filename matching by normalizing all strings to NFC.
* **CP437 Safe ZIP Extraction**: Windows zip utilities often compress filenames with legacy CP437/CP1252 encodings. The extraction logic auto-detects and decodes these to UTF-8 to prevent filename corruption on Linux servers.
* **Blind-Copy Unknown Formats (The `.pd` bug)**: FrameMaker frequently truncates `.pdf` extension paths to `.pd` inside references. The system automatically performs `.pd` -> `.pdf` fallback matches, and blindly copies unknown file extensions directly to ensure no graphics links break.

---

## 🚀 Local Development Setup

### 1. Prerequisites
Create a `.env` file in the root directory:
```env
OPENAI_API_KEY=your-api-key-here
API_SECRET_KEY=your-authorization-token-here
```

### 2. Set Up Virtual Environment
```bash
# Create environment
python -m venv .venv

# Activate on Windows:
.venv\Scripts\activate

# Install dependencies:
pip install -r requirements.txt
```

### 3. Run Backend (FastAPI)
```bash
uvicorn app:app --reload --port 8000
```
API docs will be available at [http://localhost:8000/docs](http://localhost:8000/docs).

### 4. Run Frontend (Streamlit)
```bash
# Install frontend requirements
pip install -r frontend/requirements.txt

# Run Streamlit
streamlit run frontend/streamlit_app.py --server.port 8501
```
Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## 🌐 Production Deployment Guide

### Deploying the FastAPI Backend to Render
1. Create a **Web Service** on Render pointing to your GitHub repository.
2. Select **Docker** as the Runtime environment (Render will build the provided `Dockerfile`).
3. Add the following **Environment Variables**:
   * `OPENAI_API_KEY`: Your OpenAI API key.
   * `API_SECRET_KEY`: A secure random string used as a Bearer authorization token.

### Deploying the Streamlit Frontend to Render
1. Create another **Web Service** pointing to the same repository.
2. Configure settings:
   * **Runtime**: `Python 3`
   * **Build Command**: `pip install -r frontend/requirements.txt`
   * **Start Command**: `streamlit run frontend/streamlit_app.py --server.port $PORT`
3. Add **Environment Variables**:
   * `FASTAPI_URL`: The URL of your deployed FastAPI backend service.
   * `API_SECRET_KEY`: The matching secret bearer token defined in the backend.
