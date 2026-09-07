"""OCR for scanned PDFs via OCRmyPDF (Tesseract + Ghostscript), one job at a time per process."""
import shutil
import subprocess
import sys
import threading

OCR_LOCK = threading.BoundedSemaphore(1)
MIN_CHARS_PER_PAGE = 40


class OcrError(Exception):
    pass


def needs_ocr(path, char_count_fn=None, image_pages_fn=None):
    """True for scanned documents, and for text PDFs that embed scanned pages (ocrmypdf's
    --skip-text then OCRs only the pages without a text layer)."""
    from importer.pdf_text import char_count, image_only_pages

    chars, pages = (char_count_fn or char_count)(path)
    if chars < MIN_CHARS_PER_PAGE * max(pages, 1):
        return True
    try:
        return bool((image_pages_fn or image_only_pages)(path))
    except Exception:
        return False


def ocr_available():
    return shutil.which("tesseract") is not None


def ocr_command(src, dst, langs="eng"):
    return [sys.executable, "-m", "ocrmypdf", "--skip-text", "--rotate-pages", "--deskew", "--output-type", "pdf",
            "--optimize", "0", "--jobs", "1", "-l", langs, src, dst]


def run_ocr(src, dst, langs="eng", timeout=600):
    if not ocr_available():
        raise OcrError("OCR is not available on this server (tesseract missing)")
    with OCR_LOCK:
        try:
            proc = subprocess.run(ocr_command(src, dst, langs), capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise OcrError(f"OCR timed out after {timeout}s") from None
        except OSError as e:
            raise OcrError(f"Could not start OCR: {e}") from e
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise OcrError("OCR failed: " + " | ".join(tail))
    return dst
