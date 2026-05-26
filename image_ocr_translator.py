# image_ocr_translator.py
import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import os
import re
import io
import html
import gzip
import json
import base64
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote

import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI
from lxml import etree

try:
    from langdetect import detect as _langdetect
    from langdetect import DetectorFactory
    DetectorFactory.seed = 0
    _LANGDETECT_AVAILABLE = True
except ImportError:
    _LANGDETECT_AVAILABLE = False
    print("[WARN] langdetect not installed — pip install langdetect")

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable not set")
client         = OpenAI(api_key=OPENAI_API_KEY)
MODEL          = "gpt-4o"
API_TIMEOUT    = 120
MAX_IMG_DIM    = 3000
BOX_PADDING    = 2     
MIN_FONT       = 7
MAX_FONT       = 96
_MIN_CHARS_FOR_LANGDETECT = 20

# -----------------------------------------------------------------------------
# Language maps
# -----------------------------------------------------------------------------

LANG_NAMES: Dict[str, str] = {
    "zh-CN": "Simplified Chinese",   "zh-TW": "Traditional Chinese",
    "zh":    "Chinese",
    "ja":    "Japanese",             "ko":    "Korean",
    "de":    "German",               "fr":    "French",
    "es":    "Spanish",              "ar":    "Arabic",
    "pt":    "Portuguese",           "it":    "Italian",
    "vi":    "Vietnamese",           "nl":    "Dutch",
    "pl":    "Polish",               "ru":    "Russian",
    "tr":    "Turkish",              "sv":    "Swedish",
    "da":    "Danish",               "fi":    "Finnish",
    "nb":    "Norwegian",            "cs":    "Czech",
    "en":    "English",
}

_LANG_ROOT: Dict[str, str] = {
    "zh-CN": "zh-cn", "zh-TW": "zh-tw",
    "nb":    "no",    "pt-BR": "pt",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff"}
PDF_EXTENSIONS   = {".pdf", ".pd"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS


def _lang_root(lang_code: str) -> str:
    return _LANG_ROOT.get(lang_code, lang_code.split("-")[0].lower())

def _detect_language(text: str) -> Optional[str]:
    if not _LANGDETECT_AVAILABLE:
        return None
    clean = text.strip()
    if len(clean) < _MIN_CHARS_FOR_LANGDETECT:
        return None
    try:
        return _langdetect(clean)
    except Exception:
        return None

def _already_in_target_language(text: str, target_lang: str) -> bool:
    detected = _detect_language(text)
    if detected is None:
        return False
    return detected.lower().split("-")[0] == _lang_root(target_lang).split("-")[0]

_FONT_PATHS_REGULAR = [
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "arial.ttf",
]

_FONT_PATHS_BOLD = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "arialbd.ttf",
]

def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    size = max(MIN_FONT, size)
    candidates = _FONT_PATHS_BOLD if bold else _FONT_PATHS_REGULAR
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    if bold:
        for p in _FONT_PATHS_REGULAR:
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()

def _encode_pil(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def _cap_image(img: Image.Image) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= MAX_IMG_DIM:
        return img
    scale = MAX_IMG_DIM / longest
    nw, nh = int(w * scale), int(h * scale)
    print(f"      (downscaling {w}×{h} → {nw}×{nh})")
    return img.resize((nw, nh), Image.LANCZOS)

def _sample_bg_color(img: Image.Image, x: int, y: int, w: int, h: int) -> Tuple[int, int, int]:
    img_w, img_h = img.size
    pixels: List[Tuple[int, int, int]] = []

    def _push(px: int, py: int) -> None:
        if 0 <= px < img_w and 0 <= py < img_h:
            p = img.getpixel((px, py))
            if isinstance(p, int):           
                p = (p, p, p)
            pixels.append(p[:3])

    for dx in range(w):
        _push(x + dx, y - 1)                 
        _push(x + dx, y + h)                 
    for dy in range(h):
        _push(x - 1, y + dy)                 
        _push(x + w, y + dy)                 

    if not pixels:
        return (255, 255, 255)
    arr = np.array(pixels, dtype=np.int32)
    return tuple(int(c) for c in np.median(arr, axis=0))

def _sample_text_color(
    img: Image.Image, x: int, y: int, w: int, h: int,
    bg: Tuple[int, int, int],
) -> Tuple[int, int, int]:
    img_w, img_h = img.size
    step_x = max(1, w // 12)
    step_y = max(1, h // 6)

    pixels: List[Tuple[int, int, int]] = []
    for dx in range(0, w, step_x):
        for dy in range(0, h, step_y):
            px, py = x + dx, y + dy
            if 0 <= px < img_w and 0 <= py < img_h:
                p = img.getpixel((px, py))
                if isinstance(p, int):
                    p = (p, p, p)
                pixels.append(p[:3])

    if not pixels:
        return (255, 255, 255) if sum(bg) < 384 else (0, 0, 0)

    arr = np.array(pixels, dtype=np.int32)
    bg_arr = np.array(bg, dtype=np.int32)
    dists = np.linalg.norm(arr - bg_arr, axis=1)

    if float(np.max(dists)) < 25:
        return (255, 255, 255) if sum(bg) < 384 else (0, 0, 0)

    top_k = max(1, len(pixels) // 4)
    top_idx = np.argsort(dists)[-top_k:]
    return tuple(int(c) for c in np.median(arr[top_idx], axis=0))

def _wrap_text(draw: ImageDraw.ImageDraw, text: str, max_w: int, font) -> List[str]:
    if not text:
        return []
    words = text.split()
    if not words:
        return [text]

    lines, cur = [], words[0]
    for word in words[1:]:
        test = cur + " " + word
        if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)

    wrapped: List[str] = []
    for line in lines:
        if draw.textbbox((0, 0), line, font=font)[2] <= max_w:
            wrapped.append(line)
            continue
        buf = ""
        for ch in line:
            if draw.textbbox((0, 0), buf + ch, font=font)[2] <= max_w:
                buf += ch
            else:
                if buf:
                    wrapped.append(buf)
                buf = ch
        if buf:
            wrapped.append(buf)
    return wrapped

def _fits(
    draw: ImageDraw.ImageDraw, text: str, w: int, h: int, font,
) -> Tuple[bool, List[str]]:
    lines = _wrap_text(draw, text, w, font)
    if not lines:
        return True, []
    line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + 1
    if line_h * len(lines) > h:
        return False, lines
    max_w = max(draw.textbbox((0, 0), ln, font=font)[2] for ln in lines)
    return max_w <= w, lines

def _best_font(
    draw: ImageDraw.ImageDraw, text: str, w: int, h: int, initial_size: int,
    bold: bool = False,
) -> Tuple[ImageFont.FreeTypeFont, List[str]]:
    seed = max(MIN_FONT, min(MAX_FONT, initial_size))
    lo, hi = MIN_FONT, min(MAX_FONT, max(seed * 2, MIN_FONT + 1))

    best_font = _get_font(MIN_FONT, bold=bold)
    best_lines = _wrap_text(draw, text, w, best_font)

    while lo <= hi:
        mid = (lo + hi) // 2
        font = _get_font(mid, bold=bold)
        ok, lines = _fits(draw, text, w, h, font)
        if ok:
            best_font, best_lines = font, lines
            lo = mid + 1
        else:
            hi = mid - 1

    return best_font, best_lines

def _ocr_translate(b64_image: str, target_lang: str,
                   img_w: int, img_h: int) -> list:
    lang_name = LANG_NAMES.get(target_lang, target_lang)
    prompt = (
        "You are a precise OCR and translation engine.\n"
        f"The image is {img_w}×{img_h} pixels.\n\n"
        "TASK\n"
        "1. Detect EVERY piece of visible text in the image.\n"
        f"2. Translate EVERY piece into {lang_name}. "
        "Translate ALL text regardless of what language it appears to be in.\n"
        "3. Return the tight bounding box of each text block in pixels AND a\n"
        "   `bold` flag indicating whether the glyphs appear bold/heavy.\n\n"
        "OUTPUT — return ONLY a valid JSON array, no markdown fences:\n"
        '[{"original":"...","translated":"...","x":0,"y":0,"width":0,"height":0,"bold":false}]\n\n'
        "RULES\n"
        f"* x,y = top-left corner in pixels relative to the {img_w}×{img_h} image.\n"
        "* Include ALL text: headers, titles, table labels, cell text, captions.\n"
        "* Preserve numbers, symbols, and product/model codes exactly.\n"
        "* Brand names and proper nouns that do not translate: set translated=original.\n"
        "* `bold` is true ONLY if the glyph strokes are visibly thick/heavy;\n"
        "  otherwise false. Default to false when uncertain.\n"
        "* Do NOT wrap reply in markdown code blocks.\n"
    )
    print(f"      → GPT-4o OCR (timeout={API_TIMEOUT}s) …", flush=True)
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64_image}",
                               "detail": "high"}},
            ]}],
            max_tokens=4096,
            timeout=API_TIMEOUT,
        )
    except Exception as e:
        print(f"      [FAIL] API failed: {e}")
        return []

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(raw)
        print(f"      [OK] {len(result)} block(s) detected")
        return result
    except Exception:
        print(f"      [FAIL] JSON parse failed:\n{raw[:300]}")
        return []

def _translate_texts_batch(texts: List[str], target_lang: str) -> List[str]:
    if not texts:
        return []
    lang_name = LANG_NAMES.get(target_lang, target_lang)
    numbered  = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    prompt = (
        f"Translate each numbered item into {lang_name}.\n"
        "Rules:\n"
        "- Translate ALL items — every label, header, and field name.\n"
        "- Preserve numbers, symbols, and product/model codes exactly.\n"
        "- Words identical in both languages may stay as-is.\n"
        "- Return ONLY the numbered translations, same numbering, no extra text.\n\n"
        + numbered
    )
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4000,
            timeout=API_TIMEOUT,
        )
        raw   = resp.choices[0].message.content.strip()
        lines = raw.splitlines()
        out   = []
        for i, original in enumerate(texts):
            prefix  = f"{i+1}. "          
            matched = next(
                (ln[len(prefix):].strip() for ln in lines if ln.startswith(prefix)),
                None,
            )
            out.append(matched if matched else original)
        return out
    except Exception as e:
        print(f"      [FAIL] batch translate error: {e}")
        return texts

try:
    import cv2 
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

def _detect_alignment(
    img: Image.Image, x: int, y: int, w: int, h: int,
    bg: Tuple[int, int, int],
) -> str:
    img_w, img_h = img.size
    bg_arr = np.array(bg, dtype=np.int32)

    sample_rows = [y + h // 4, y + h // 2, y + 3 * h // 4]
    sample_rows = [r for r in sample_rows if 0 <= r < img_h]
    if not sample_rows:
        return "left"

    step = max(1, w // 40)
    column_has_text: List[bool] = []
    for dx in range(0, w, step):
        px = x + dx
        if px < 0 or px >= img_w:
            column_has_text.append(False)
            continue
        is_text = False
        for py in sample_rows:
            pix = img.getpixel((px, py))
            if isinstance(pix, int):
                pix = (pix, pix, pix)
            dist = float(np.linalg.norm(
                np.array(pix[:3], dtype=np.int32) - bg_arr
            ))
            if dist > 35:
                is_text = True
                break
        column_has_text.append(is_text)

    text_columns = [i for i, t in enumerate(column_has_text) if t]
    if not text_columns:
        return "left"

    total = len(column_has_text)
    left_margin = text_columns[0] / total
    right_margin = (total - 1 - text_columns[-1]) / total

    if left_margin < 0.10 and right_margin < 0.10:
        return "left"
    if abs(left_margin - right_margin) < 0.10 and left_margin > 0.12:
        return "center"
    if left_margin > right_margin + 0.15:
        return "right"
    return "left"

def _find_text_baseline(
    img: Image.Image, x: int, y: int, w: int, h: int,
    bg: Tuple[int, int, int],
) -> int:
    img_w, img_h = img.size
    bg_arr = np.array(bg, dtype=np.int32)
    step_x = max(1, w // 20)

    for py in range(min(y + h, img_h) - 1, max(y - 1, -1), -1):
        if py < 0:
            continue
        count = 0
        for dx in range(0, w, step_x):
            px = x + dx
            if 0 <= px < img_w:
                pix = img.getpixel((px, py))
                if isinstance(pix, int):
                    pix = (pix, pix, pix)
                dist = float(np.linalg.norm(
                    np.array(pix[:3], dtype=np.int32) - bg_arr
                ))
                if dist > 35:
                    count += 1
        if count >= 2:
            return py
    return min(y + int(h * 0.85), img_h - 1)

def _erase_text_regions(pil_img: Image.Image, block_info: list) -> Image.Image:
    if not block_info:
        return pil_img.copy()

    if _CV2_AVAILABLE:
        try:
            return _erase_with_inpaint(pil_img, block_info)
        except Exception as e:
            print(f"      [WARN] cv2.inpaint failed ({e}); falling back to solid fill.")
            return _erase_with_solid_fill(pil_img, block_info)

    print("      [WARN] opencv-python not installed — using solid-fill fallback.")
    return _erase_with_solid_fill(pil_img, block_info)

def _erase_with_inpaint(pil_img: Image.Image, block_info: list) -> Image.Image:
    arr = np.array(pil_img.convert("RGB"))
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    img_h, img_w = bgr.shape[:2]

    mask = np.zeros((img_h, img_w), dtype=np.uint8)
    text_threshold = 35    

    for info in block_info:
        x, y, w, h = info["x"], info["y"], info["w"], info["h"]
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(img_w, x + w)
        y1 = min(img_h, y + h)
        if x1 <= x0 or y1 <= y0:
            continue

        bg_arr = np.array(info["bg"], dtype=np.int32)
        box = arr[y0:y1, x0:x1, :3].astype(np.int32)

        diff = box - bg_arr
        dist = np.linalg.norm(diff, axis=2)
        text_mask = (dist > text_threshold).astype(np.uint8) * 255

        kernel = np.ones((3, 3), np.uint8)
        text_mask = cv2.dilate(text_mask, kernel, iterations=1)

        existing = mask[y0:y1, x0:x1]
        mask[y0:y1, x0:x1] = np.maximum(existing, text_mask)

    cleaned_bgr = cv2.inpaint(bgr, mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
    cleaned_rgb = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(cleaned_rgb)

def _erase_with_solid_fill(pil_img: Image.Image, block_info: list) -> Image.Image:
    img = pil_img.copy()
    draw = ImageDraw.Draw(img)
    for info in block_info:
        x, y, w, h = info["x"], info["y"], info["w"], info["h"]
        draw.rectangle([(x, y), (x + w, y + h)], fill=info["bg"])
    return img

def _draw_blocks(pil_img: Image.Image, blocks: list) -> Image.Image:
    img_w, img_h = pil_img.size
    block_info: List[dict] = []
    for item in blocks:
        try:
            original   = item.get("original", "") or ""
            translated = item.get("translated") or item.get("text", "") or ""
            if not translated.strip() or translated.strip() == original.strip():
                continue

            x = max(0, min(int(item.get("x", 0)),     img_w - 1))
            y = max(0, min(int(item.get("y", 0)),     img_h - 1))
            w = min(int(item.get("width",  100)),      img_w - x)
            h = min(int(item.get("height",  20)),      img_h - y)
            if w <= 4 or h <= 4:
                continue

            bg = _sample_bg_color(pil_img, x, y, w, h)
            fg = _sample_text_color(pil_img, x, y, w, h, bg)
            alignment = _detect_alignment(pil_img, x, y, w, h, bg)
            baseline_y = _find_text_baseline(pil_img, x, y, w, h, bg)
            is_bold = bool(item.get("bold", False))

            block_info.append({
                "translated": translated,
                "x": x, "y": y, "w": w, "h": h,
                "bg": bg, "fg": fg,
                "alignment": alignment,
                "baseline_y": baseline_y,
                "bold": is_bold,
            })
        except Exception as e:
            print(f"      [analyse] skipping block: {e}")

    if not block_info:
        return pil_img.copy()

    cleaned_img = _erase_text_regions(pil_img, block_info)
    draw = ImageDraw.Draw(cleaned_img)

    for info in block_info:
        try:
            x, y, w, h = info["x"], info["y"], info["w"], info["h"]
            translated = info["translated"]
            fg         = info["fg"]
            alignment  = info["alignment"]
            baseline_y = info["baseline_y"]
            is_bold    = info["bold"]

            inner_w = max(1, w - 2 * BOX_PADDING)
            inner_h = max(1, h - 2 * BOX_PADDING)
            initial = max(MIN_FONT, int(h * 0.75))
            font, lines = _best_font(
                draw, translated, inner_w, inner_h, initial, bold=is_bold,
            )
            if not lines:
                continue

            line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + 1
            total_text_h = line_h * len(lines)

            ty = baseline_y - total_text_h + 1
            ty = max(y + BOX_PADDING, min(ty, y + h - total_text_h))

            for line in lines:
                if ty + line_h > y + h:
                    break
                line_w = draw.textbbox((0, 0), line, font=font)[2]

                if alignment == "right":
                    tx = x + w - line_w - BOX_PADDING
                elif alignment == "center":
                    tx = x + (w - line_w) // 2
                else:  
                    tx = x + BOX_PADDING

                draw.text((tx, ty), line, fill=fg, font=font)
                ty += line_h

        except Exception as e:
            print(f"      [draw] skipping block: {e}")

    return cleaned_img

def _extract_pdf_text(doc: fitz.Document, max_chars: int = 2000) -> str:
    parts, total = [], 0
    for page in doc:
        t = page.get_text("text").strip()
        parts.append(t)
        total += len(t)
        if total >= max_chars:
            break
    return " ".join(parts)

def _has_real_text(page: fitz.Page) -> bool:
    return bool(page.get_text("text").strip())

def _get_fitz_fontname(font_name: str) -> str:
    fn = font_name.lower()
    if "bold" in fn and ("italic" in fn or "oblique" in fn):
        return "hebobi" if ("helv" in fn or "arial" in fn) else "tibi"
    if "bold" in fn:
        return "hebo"   if ("helv" in fn or "arial" in fn) else "tibo"
    if "italic" in fn or "oblique" in fn:
        return "hebi"   if ("helv" in fn or "arial" in fn) else "tiit"
    return "helv"

def _process_text_layer_page(page: fitz.Page, target_lang: str) -> bool:
    spans: List[dict] = []
    seen:  set        = set()

    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span["text"].strip()
                if not text:
                    continue
                key = (text, tuple(round(v, 1) for v in span["bbox"]))
                if key in seen:
                    continue
                seen.add(key)
                spans.append({
                    "text": text,
                    "bbox": span["bbox"],
                    "size": span["size"],
                    "font": span.get("font", ""),
                })

    if not spans:
        return False

    all_text = " ".join(s["text"] for s in spans)
    if _already_in_target_language(all_text, target_lang):
        print("      - page already in target language — skipping")
        return False

    originals    = [s["text"] for s in spans]
    translations = _translate_texts_batch(originals, target_lang)

    to_process = [
        (span, tr)
        for span, tr in zip(spans, translations)
        if tr.strip() != span["text"].strip()
    ]

    if not to_process:
        print("      - no text changed after translation")
        return False

    for span, _tr in to_process:
        x0, y0, x1, y1 = span["bbox"]
        page.add_redact_annot(fitz.Rect(x0, y0 - 1, x1, y1 + 1), fill=(1, 1, 1))
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

    any_changed = False
    for span, translated in to_process:
        x0, y0, x1, y1 = span["bbox"]
        rc = page.insert_text(
            (x0, y1 - 1), translated,
            fontsize=span["size"],
            fontname=_get_fitz_fontname(span["font"]),
            color=(0, 0, 0),
        )
        if rc >= 0:
            any_changed = True
            print(f"      [OK] {repr(span['text'][:28])} → {repr(translated[:28])}")
        else:
            print(f"      [FAIL] insert_text rc={rc} for {repr(span['text'][:28])}")

    return any_changed

def _process_image_layer_page(doc: fitz.Document,
                               page: fitz.Page,
                               target_lang: str) -> bool:
    pix     = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    pil_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    pil_img      = _cap_image(pil_img)
    img_w, img_h = pil_img.size

    blocks = _ocr_translate(_encode_pil(pil_img), target_lang, img_w, img_h)
    if not blocks:
        return False

    if all((b.get("translated") or "").strip() == (b.get("original") or "").strip()
           for b in blocks):
        print("      - all OCR blocks unchanged")
        return False

    pil_img = _draw_blocks(pil_img, blocks)
    buf     = io.BytesIO()
    pil_img.save(buf, format="PNG")
    page.clean_contents()
    for item in page.get_images(full=True):
        try:
            page.delete_image(item[0])
        except Exception:
            pass
    page.insert_image(page.rect, stream=buf.getvalue(), keep_proportion=False)
    return True

def process_image(
    source_path: Path,
    target_lang: str,
    out_folder: Path,
    rename_with_lang: bool = True,
) -> str:
    source_path = Path(source_path)
    print(f"\n  [IMG] {source_path.name}", flush=True)

    try:
        pil_img = Image.open(str(source_path)).convert("RGB")
    except Exception as e:
        print(f"  [FAIL] Cannot open image: {e}")
        return ""

    pil_img      = _cap_image(pil_img)        
    img_w, img_h = pil_img.size              
    blocks       = _ocr_translate(_encode_pil(pil_img), target_lang, img_w, img_h)

    new_name = (
        source_path.name if not rename_with_lang
        else f"{source_path.stem}_{target_lang}{source_path.suffix}"
    )
    out_path = out_folder / new_name

    if not blocks:
        print("  - No text detected — copying unchanged.")
        shutil.copy2(str(source_path), str(out_path))
        return new_name

    if all((b.get("translated") or "").strip() == (b.get("original") or "").strip()
           for b in blocks):
        print("  - No text required translation — copying unchanged.")
        shutil.copy2(str(source_path), str(out_path))
        return new_name

    pil_img  = _draw_blocks(pil_img, blocks)
    ext      = source_path.suffix.lower()
    fmt_map  = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
                ".bmp": "BMP", ".gif": "GIF", ".tif": "TIFF", ".tiff": "TIFF"}
    save_fmt = fmt_map.get(ext, "PNG")
    kw       = {"format": save_fmt}
    if save_fmt == "JPEG":
        kw["quality"] = 95
    pil_img.save(str(out_path), **kw)
    print(f"  [OK] Saved → {new_name}")
    return new_name

def process_pdf(
    source_path: Path,
    target_lang: str,
    out_folder: Path,
    rename_with_lang: bool = True,
) -> str:
    source_path = Path(source_path)
    print(f"\n  [PDF] {source_path.name}", flush=True)

    try:
        doc = fitz.open(str(source_path))
    except Exception as e:
        print(f"  [FAIL] Cannot open PDF: {e}")
        return ""

    new_name = (
        source_path.name if not rename_with_lang
        else f"{source_path.stem}_{target_lang}.pdf"
    )
    out_path = out_folder / new_name

    full_text = _extract_pdf_text(doc)
    if full_text.strip() and _already_in_target_language(full_text, target_lang):
        print("  - Document already in target language — copying unchanged.")
        doc.close()
        shutil.copy2(str(source_path), str(out_path))
        print(f"  [OK] Copied → {new_name}")
        return new_name

    if not full_text.strip():
        print("  - No extractable text — will attempt image-layer OCR.")

    any_changed = False
    for idx in range(len(doc)):
        page = doc[idx]
        print(f"      page {idx+1}/{len(doc)}", flush=True)
        if _has_real_text(page):
            changed = _process_text_layer_page(page, target_lang)
            print(f"      {'[OK]' if changed else '-'} "
                  f"{'text-layer translated' if changed else 'text-layer: no changes'}")
        else:
            changed = _process_image_layer_page(doc, page, target_lang)
            print(f"      {'[OK]' if changed else '-'} "
                  f"{'image-OCR translated' if changed else 'image-OCR: no text'}")
        if changed:
            any_changed = True

    if not any_changed:
        print("  - Nothing translated — copying unchanged.")
        doc.close()
        shutil.copy2(str(source_path), str(out_path))
        print(f"  [OK] Copied → {new_name}")
        return new_name

    tmp = str(out_path) + ".tmp.pdf"
    doc.save(tmp, garbage=4, deflate=True)
    doc.close()
    os.replace(tmp, str(out_path))
    print(f"  [OK] Saved → {new_name}")
    return new_name

_DI_RE = re.compile(
    r'<ImportObFileDI[^>]*>([^<]+)</ImportObFileDI>',
    re.IGNORECASE,
)

_OB_RE = re.compile(
    r'(<ImportObFile[^>]*>)([^<]+)(</ImportObFile>)',
    re.IGNORECASE,
)

def _parse_mif_path(raw_di: str) -> str:
    first = html.unescape(raw_di.strip())
    second = html.unescape(first)
    unquoted = unquote(second)
    converted = unquoted.replace("<u>", "../").replace("<c>", "/")
    converted = converted.replace("..//" , "../")   
    return converted

def _decode_internal_file_blob(xlf_path: Path) -> str:
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    try:
        tree = etree.parse(str(xlf_path), parser)
    except Exception as e:
        print(f"  [FAIL] Cannot parse XLF: {e}")
        return ""

    internal_el = None
    for elem in tree.getroot().iter():
        if elem.tag.split("}")[-1] == "internal-file":
            internal_el = elem
            break

    if internal_el is None:
        print("  [WARN] No <internal-file> element in XLF.")
        return ""

    raw_b64 = (internal_el.text or "").strip()
    if not raw_b64:
        print("  [WARN] <internal-file> element is empty.")
        return ""

    try:
        compressed = base64.b64decode(raw_b64)
    except Exception as e:
        print(f"  [FAIL] base64 decode failed: {e}")
        return ""

    if compressed[:2] == b'\x1f\x8b':
        try:
            return gzip.decompress(compressed).decode("utf-8", errors="replace")
        except Exception as e:
            print(f"  [FAIL] gzip decompress failed: {e}")
            return ""

    return compressed.decode("utf-8", errors="replace")

def extract_reference_paths(xlf_path: Path) -> List[Tuple[str, str]]:
    xlf_path = Path(xlf_path)
    base_dir = xlf_path.parent

    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    try:
        tree = etree.parse(str(xlf_path), parser)
    except Exception as e:
        print(f"  [FAIL] Cannot parse XLF: {e}")
        return []

    root = tree.getroot()
    seen: set = set()
    result: List[Tuple[str, str]] = []

    def add_ref(raw_val: str, ref_type: str):
        fs_path_str = _parse_mif_path(raw_val)
        if fs_path_str == "2.0 internal inset" or not fs_path_str.strip():
            return
            
        ext = Path(fs_path_str).suffix.lower()
        if not ext:
            return

        abs_path = (base_dir / fs_path_str).resolve()
        key = str(abs_path)

        if key in seen:
            return
        seen.add(key)

        print(f"    [{ref_type}] Raw ref : {raw_val!r}")
        print(f"    [{ref_type}] FS path: {fs_path_str!r}")
        print(f"    [{ref_type}] Abs    : {abs_path}")
        result.append((raw_val, str(abs_path)))

    # 1. Scan all <internal-file> tags
    internal_elements = []
    for elem in root.iter():
        tag_local = elem.tag.split("}")[-1]
        if tag_local == "internal-file":
            internal_elements.append(elem)

    print(f"  Total <internal-file> tags found: {len(internal_elements)}")
    for idx, elem in enumerate(internal_elements):
        raw_b64 = (elem.text or "").strip()
        if not raw_b64:
            continue
        try:
            compressed = base64.b64decode(raw_b64)
            if compressed[:2] == b'\x1f\x8b':
                mif = gzip.decompress(compressed).decode("utf-8", errors="replace")
            else:
                mif = compressed.decode("utf-8", errors="replace")
            
            di_raws = _DI_RE.findall(mif)
            ob_matches = _OB_RE.findall(mif)
            ob_raws = [match[1] for match in ob_matches]
            
            print(f"    [internal-file {idx}] ImportObFileDI found: {len(di_raws)}, ImportObFile found: {len(ob_raws)}")
            for raw in di_raws:
                add_ref(raw, f"DI-IF-{idx}")
            for raw in ob_raws:
                add_ref(raw, f"OB-IF-{idx}")
        except Exception as e:
            print(f"    [FAIL] Error decoding/parsing internal-file {idx}: {e}")

    # 2. Scan all <file> tags and check the 'original' attribute
    file_elements = []
    for elem in root.iter():
        tag_local = elem.tag.split("}")[-1]
        if tag_local == "file":
            file_elements.append(elem)

    print(f"  Total <file> tags found: {len(file_elements)}")
    for idx, elem in enumerate(file_elements):
        original = elem.get("original")
        if original:
            original_clean = original.strip()
            ext = Path(original_clean).suffix.lower()
            if ext in MEDIA_EXTENSIONS or ext == ".pd":
                print(f"    [file {idx}] Found graphic reference in 'original' attribute: {original_clean}")
                add_ref(original_clean, f"FILE-ATTR-{idx}")

    # 3. Scan all <external-file> tags and check the 'href' attribute
    external_elements = []
    for elem in root.iter():
        tag_local = elem.tag.split("}")[-1]
        if tag_local == "external-file":
            external_elements.append(elem)

    print(f"  Total <external-file> tags found: {len(external_elements)}")
    for idx, elem in enumerate(external_elements):
        href = elem.get("href")
        if href:
            href_clean = href.strip()
            ext = Path(href_clean).suffix.lower()
            if ext in MEDIA_EXTENSIONS or ext == ".pd":
                print(f"    [external-file {idx}] Found graphic reference in 'href' attribute: {href_clean}")
                add_ref(href_clean, f"EXT-HREF-{idx}")

    # 4. Scan any other element with 'href' attribute ending in a media extension
    for elem in root.iter():
        tag_local = elem.tag.split("}")[-1]
        if tag_local == "external-file":
            continue
        href = elem.get("href")
        if href:
            href_clean = href.strip()
            ext = Path(href_clean).suffix.lower()
            if ext in MEDIA_EXTENSIONS or ext == ".pd":
                print(f"    [<{tag_local}>] Found graphic reference in 'href' attribute: {href_clean}")
                add_ref(href_clean, f"HREF-{tag_local}")

    return result

def _to_di_path(fs_path: str) -> str:
    # Convert standard relative path to device-independent path
    # Replace '../' with '<u>' and '/' with '<c>'
    di = fs_path.replace("../", "<u>").replace("/", "<c>").replace("\\", "<c>")
    import html
    return html.escape(di)

def _update_mif_blob(mif_text: str, mapping: Dict[str, str]) -> Tuple[str, int]:
    result  = []
    pos     = 0
    updated = 0

    for di_match in _DI_RE.finditer(mif_text):
        di_raw = di_match.group(1)          

        decoded   = html.unescape(di_raw.strip())
        converted = decoded.replace("<u>", "../").replace("<c>", "/").replace("..//" , "../")
        basename  = Path(converted).name

        new_path = (
            mapping.get(di_raw)    or   
            mapping.get(basename)  or   
            mapping.get(converted)      
        )
        if not new_path:
            continue

        ob_match = _OB_RE.search(mif_text, di_match.end(), di_match.end() + 800)
        if ob_match is None:
            continue

        old_val = ob_match.group(2).strip()
        if old_val == "2.0 internal inset":
            continue          

        # Reconstruct the string, updating BOTH ImportObFileDI and ImportObFile
        result.append(mif_text[pos : di_match.start(1)])
        
        # 1. Update ImportObFileDI to device-independent format
        new_di_path = _to_di_path(new_path)
        result.append(new_di_path)
        
        # 2. Add text between ImportObFileDI end and ImportObFile start
        result.append(mif_text[di_match.end(1) : ob_match.start(2)])
        
        # 3. Update ImportObFile to platform-specific format (with backslashes for local compatibility)
        win_path = new_path.replace("/", "\\")
        result.append(win_path)
        
        pos = ob_match.end(2)
        updated += 1
        print(f"    MIF DI: {decoded!r}  →  {new_di_path!r}")
        print(f"    MIF OB: {old_val!r}  →  {win_path!r}")

    result.append(mif_text[pos:])
    return "".join(result), updated

def _rebuild_xlf_with_updated_paths(
    xlf_path: Path,
    mapping: Dict[str, str],
    out_xlf_path: Path,
) -> bool:
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    try:
        tree = etree.parse(str(xlf_path), parser)
    except Exception as e:
        print(f"  [FAIL] Cannot parse XLF for rebuild: {e}")
        return False

    root = tree.getroot()

    # 1. Update <file> tags original attribute
    for elem in root.iter():
        tag_local = elem.tag.split("}")[-1]
        if tag_local == "file":
            original = elem.get("original")
            if original:
                original_clean = original.strip()
                fs_path_str = _parse_mif_path(original_clean)
                basename = Path(fs_path_str).name
                new_path = (
                    mapping.get(original_clean) or
                    mapping.get(basename) or
                    mapping.get(fs_path_str)
                )
                if new_path:
                    win_path = new_path.replace("/", "\\")
                    elem.set("original", win_path)
                    print(f"    XML <file> original: {original_clean!r} → {win_path!r}")

    # 2. Update <external-file> tags href attribute
    for elem in root.iter():
        tag_local = elem.tag.split("}")[-1]
        if tag_local == "external-file":
            href = elem.get("href")
            if href:
                href_clean = href.strip()
                fs_path_str = _parse_mif_path(href_clean)
                basename = Path(fs_path_str).name
                new_path = (
                    mapping.get(href_clean) or
                    mapping.get(basename) or
                    mapping.get(fs_path_str)
                )
                if new_path:
                    win_path = new_path.replace("/", "\\")
                    elem.set("href", win_path)
                    print(f"    XML <external-file> href: {href_clean!r} → {win_path!r}")

    # 3. Update any other element with href attribute ending in media extension
    for elem in root.iter():
        tag_local = elem.tag.split("}")[-1]
        if tag_local == "external-file":
            continue
        href = elem.get("href")
        if href:
            href_clean = href.strip()
            fs_path_str = _parse_mif_path(href_clean)
            basename = Path(fs_path_str).name
            new_path = (
                mapping.get(href_clean) or
                mapping.get(basename) or
                mapping.get(fs_path_str)
            )
            if new_path:
                win_path = new_path.replace("/", "\\")
                elem.set("href", win_path)
                print(f"    XML <{tag_local}> href: {href_clean!r} → {win_path!r}")

    # 4. Update <internal-file> tags (MIF blob)
    internal_el = None
    for elem in root.iter():
        if elem.tag.split("}")[-1] == "internal-file":
            internal_el = elem
            break

    n_updated = 0
    if internal_el is not None:
        raw_b64 = (internal_el.text or "").strip()
        if raw_b64:
            try:
                compressed = base64.b64decode(raw_b64)
                if compressed[:2] == b'\x1f\x8b':
                    mif_text = gzip.decompress(compressed).decode("utf-8", errors="replace")
                else:
                    mif_text = compressed.decode("utf-8", errors="replace")
                
                print("\n  Rewriting <ImportObFile> & <ImportObFileDI> paths in MIF blob …")
                updated_mif, n_updated = _update_mif_blob(mif_text, mapping)
                
                if n_updated > 0:
                    new_compressed = gzip.compress(updated_mif.encode("utf-8"))
                    internal_el.text = base64.b64encode(new_compressed).decode("ascii")
            except Exception as e:
                print(f"  [FAIL] Internal MIF blob update failed: {e}")

    out_xlf_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tree.write(
            str(out_xlf_path),
            xml_declaration=True,
            encoding="UTF-8",
            pretty_print=False,
        )
        print(f"  [OK] Updated XLF saved → {out_xlf_path}")
        print(f"    ({n_updated} graphic reference(s) rewritten in MIF blob)")
        return True
    except Exception as e:
        print(f"  [FAIL] Failed to write updated XML tree to {out_xlf_path}: {e}")
        return False

def _subfolder_from_di(di_fs_path: str) -> Path:
    p = Path(di_fs_path)
    parts = p.parent.parts
    
    # 1. Search for any part starting with "translated_" (case-insensitive)
    translated_idx = -1
    for idx, part in enumerate(parts):
        if part.lower().startswith("translated_"):
            translated_idx = idx
            
    if translated_idx != -1:
        # Keep everything after the "translated_..." part
        real_parts = parts[translated_idx + 1:]
    else:
        # 2. Search for common media/graphics folder names (case-insensitive)
        media_prefixes = ("graphics", "image", "img", "media", "pic", "photo", "draw")
        media_idx = -1
        for idx, part in enumerate(parts):
            if any(part.lower().startswith(prefix) for prefix in media_prefixes):
                media_idx = idx
                break  # Take the first one to preserve nested structure
                
        if media_idx != -1:
            real_parts = parts[media_idx:]
        else:
            # 3. Fallback for absolute paths: if absolute, only take the last directory
            # to avoid leak/creation of full system path structure (e.g. Users/Lenovo/...)
            is_abs = (
                p.is_absolute() 
                or di_fs_path.startswith('/') 
                or di_fs_path.startswith('\\') 
                or any(':' in part for part in parts)
            )
            if is_abs:
                skip = {'..', '.', '', '/', '\\'}
                last_part = parts[-1] if parts else ''
                if last_part and last_part not in skip and not (len(last_part) == 3 and last_part[1] == ':'):
                    real_parts = [last_part]
                else:
                    real_parts = []
            else:
                # For standard relative paths, just clean up .. and .
                skip = {'..', '.', '', '/', '\\'}
                real_parts = [
                    part for part in parts
                    if part not in skip
                    and not (len(part) == 3 and part[1] == ':')
                ]
                
    if not real_parts:
        return Path('.')
    return Path(*real_parts)

def process_xlf_references(
    xlf_path,
    target_lang: str,
    out_folder: Optional[Path] = None,
    rel_prefix: Optional[str] = None,
    rename_with_lang: bool = True,
    out_xlf_path: Optional[Path] = None,
    src_graphics_folder: Optional[Path] = None,  # Hook for user provided folder
) -> Dict[str, str]:
    xlf_path = Path(xlf_path)
    base_dir = xlf_path.parent

    if out_xlf_path is None:
        xlf_out_dir = base_dir
    else:
        xlf_out_dir = Path(out_xlf_path).parent

    print(f"\n{'='*60}")
    print(f"  process_xlf_references")
    print(f"  XLF      : {xlf_path}")
    print(f"  Target   : {target_lang}")
    print(f"  XLF out dir: {xlf_out_dir}")
    print(f"{'='*60}")

    refs = extract_reference_paths(xlf_path)
    print(f"\n  Total unique graphic refs: {len(refs)}")

    if not refs:
        print("  Nothing to process.")
        return {}

    if out_folder is None:
        out_folder = base_dir / f"Graphics_{target_lang}"
    out_folder = Path(out_folder)

    print(f"  Output root  : {out_folder}")
    print(f"  Lang suffix  : {rename_with_lang}\n")

    mapping: Dict[str, str] = {}

    # Dashboard Tracking Metadata
    details = []
    fulfilled_count = 0
    missing_count = 0

    for di_raw, abs_path_str in refs:
        abs_path = Path(abs_path_str)
        di_fs    = _parse_mif_path(di_raw)      
        print(f"\n  File     : {abs_path.name}")
        print(f"  DI path  : {di_fs!r}")

        # THE CHANGES: Search exclusively inside user uploaded Graphics folder
        found_src_path = None
        if src_graphics_folder:
            src_g_root = Path(src_graphics_folder)
            sub = _subfolder_from_di(di_fs)
            import unicodedata
            target_norm = unicodedata.normalize('NFC', abs_path.name.lower())
            target_pdf = target_norm + "f" if target_norm.endswith(".pd") else None
            
            def matches_target(name: str) -> bool:
                norm_name = unicodedata.normalize('NFC', name.lower())
                return norm_name == target_norm or (target_pdf is not None and norm_name == target_pdf)

            # Check 1: Structure match (Graphics/Graphics/image.pdf) with case-insensitivity, normalization & extension fallback
            c1_dir = src_g_root / sub
            if c1_dir.is_dir():
                for item in c1_dir.iterdir():
                    if item.is_file() and matches_target(item.name):
                        found_src_path = item
                        break
            
            if not found_src_path:
                # Check 2: Direct match inside root with case-insensitivity, normalization & extension fallback
                if src_g_root.is_dir():
                    for item in src_g_root.iterdir():
                        if item.is_file() and matches_target(item.name):
                            found_src_path = item
                            break
            
            if not found_src_path:
                # Check 3: Scan anywhere inside the uploaded directory structure with case-insensitivity, normalization & extension fallback
                if src_g_root.is_dir():
                    for item in src_g_root.rglob("*"):
                        if item.is_file() and matches_target(item.name):
                            found_src_path = item
                            break

            # STEM FALLBACK: If still not found, try matching by stem (base name without extension)
            if not found_src_path:
                target_stem = unicodedata.normalize('NFC', abs_path.stem.lower())
                
                def matches_target_stem(name: str) -> bool:
                    p_item = Path(name)
                    norm_stem = unicodedata.normalize('NFC', p_item.stem.lower())
                    return norm_stem == target_stem and p_item.suffix.lower() in (MEDIA_EXTENSIONS | {".pd"})

                # Check 4: Structure match with stem only
                if c1_dir.is_dir():
                    for item in c1_dir.iterdir():
                        if item.is_file() and matches_target_stem(item.name):
                            found_src_path = item
                            break

                if not found_src_path:
                    # Check 5: Direct match inside root with stem only
                    if src_g_root.is_dir():
                        for item in src_g_root.iterdir():
                            if item.is_file() and matches_target_stem(item.name):
                                found_src_path = item
                                break

                if not found_src_path:
                    # Check 6: Scan anywhere inside uploaded directory structure with stem only
                    if src_g_root.is_dir():
                        for item in src_g_root.rglob("*"):
                            if item.is_file() and matches_target_stem(item.name):
                                found_src_path = item
                                break

        if not found_src_path:
            print(f"  [MISSING] Image file not found inside uploaded folder hierarchy: {abs_path.name}")
            missing_count += 1
            details.append({
                "raw_reference": di_raw,
                "parsed_path": di_fs,
                "status": "Missing",
                "source_file": None,
                "output_path": None,
                "action": "None"
            })
            continue

        print(f"  [OK] Located image in uploaded Graphics folder -> {found_src_path}")
        orig_name = abs_path.name
        abs_path = found_src_path

        sub = _subfolder_from_di(di_fs)
        
        # If the reference subfolder is empty/root, check if the located file in the uploaded
        # graphics folder has a more specific subfolder, and use it instead!
        if sub == Path('.') and found_src_path and src_graphics_folder:
            try:
                rel_found = found_src_path.relative_to(src_graphics_folder)
                sub_found = _subfolder_from_di(str(rel_found))
                if sub_found != Path('.'):
                    sub = sub_found
                    print(f"  [PATH] Reference path had no subfolder, using structure from uploaded ZIP: {sub}")
            except Exception as e:
                print(f"  [PATH] Failed to get relative path of found file: {e}")

        dest_folder = out_folder / sub
        dest_folder.mkdir(parents=True, exist_ok=True)

        ext = abs_path.suffix.lower()
        new_name = None
        action_taken = "None"
        try:
            if ext in IMAGE_EXTENSIONS:
                try:
                    new_name = process_image(
                        abs_path, target_lang, dest_folder,
                        rename_with_lang=rename_with_lang,
                    )
                    action_taken = "Translated (Image)"
                except Exception as e:
                    print(f"  [WARN] process_image failed ({e}) -- falling back to copy")
            elif ext in PDF_EXTENSIONS:
                try:
                    new_name = process_pdf(
                        abs_path, target_lang, dest_folder,
                        rename_with_lang=rename_with_lang,
                    )
                    action_taken = "Translated (PDF)"
                except Exception as e:
                    print(f"  [WARN] process_pdf failed ({e}) -- falling back to copy")
            
            if not new_name:
                print(f"  - Blindly copying fallback/unknown extension file: {abs_path.name}")
                new_name = abs_path.name
                shutil.copy2(str(abs_path), str(dest_folder / new_name))
                action_taken = "Copied (Fallback)"

            saved_abs = dest_folder / new_name
            final_abs = dest_folder / orig_name
            if saved_abs.exists() and saved_abs != final_abs:
                if final_abs.exists():
                    final_abs.unlink()
                saved_abs.rename(final_abs)
            saved_abs = final_abs

            # Calculated exact relative path from nested XLIFF folder out to Graphics folder
            mif_ref = os.path.relpath(str(saved_abs), str(xlf_out_dir)).replace(os.sep, "/")

            print(f"  Saved  -> {saved_abs}")
            print(f"  MIF ref: {mif_ref!r}")

            mapping[abs_path.name]   = mif_ref   
            mapping[di_fs]           = mif_ref   
            mapping[di_raw]          = mif_ref   

            fulfilled_count += 1
            try:
                rel_src = str(found_src_path.relative_to(src_graphics_folder)) if src_graphics_folder else found_src_path.name
            except ValueError:
                rel_src = found_src_path.name
            
            relative_output_path = os.path.relpath(str(saved_abs), str(out_folder)).replace(os.sep, "/")
            details.append({
                "raw_reference": di_raw,
                "parsed_path": di_fs,
                "status": "Fulfilled",
                "source_file": rel_src,
                "output_path": relative_output_path,
                "action": action_taken
            })

        except Exception as e:
            print(f"  [ERROR] Error on {abs_path.name}: {e}")

    # Write dashboard metadata JSON file
    import json
    metadata = {
        "total_references": len(refs),
        "fulfilled_count": fulfilled_count,
        "missing_count": missing_count,
        "details": details
    }
    metadata_path = out_folder / "translation_metadata.json"
    try:
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        print(f"  [OK] Saved translation metadata to: {metadata_path}")
    except Exception as e:
        print(f"  [WARN] Failed to write translation metadata: {e}")

    # Rebuild the translated XLIFF with updated paths if out_xlf_path is provided
    if out_xlf_path and mapping:
        src_xlf = out_xlf_path if out_xlf_path.exists() else xlf_path
        print(f"\n  Rebuilding XLIFF with updated graphic paths -> {out_xlf_path}")
        try:
            _rebuild_xlf_with_updated_paths(
                xlf_path=src_xlf,
                mapping=mapping,
                out_xlf_path=out_xlf_path
            )
        except Exception as e:
            print(f"  [ERROR] Failed to rebuild XLIFF: {e}")

    print(f"\n{'='*60}")
    print(f"  Done. {len(set(mapping.values()))}/{len(refs)} file(s) translated.")
    print(f"{'='*60}")

    print()
    return mapping