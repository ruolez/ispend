"""OCR for scanned PDFs via OCRmyPDF (Tesseract + Ghostscript), one job at a time per process."""
import shutil
import subprocess
import sys
import threading

OCR_LOCK = threading.BoundedSemaphore(1)
MIN_CHARS_PER_PAGE = 40


class OcrError(Exception):
    pass


def needs_ocr(path, char_count_fn=None):
    from importer.pdf_text import char_count

    chars, pages = (char_count_fn or char_count)(path)
    return chars < MIN_CHARS_PER_PAGE * max(pages, 1)


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
            proc = subprocess.run(ocr_command(src, dst, langs), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise OcrError(f"OCR timed out after {timeout}s")
        except OSError as e:
            raise OcrError(f"Could not start OCR: {e}")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise OcrError("OCR failed: " + " | ".join(tail))
    return dst
