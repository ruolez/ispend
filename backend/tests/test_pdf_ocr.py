import os
import shutil
import sys
import tempfile
import unittest

from importer import pdf_ocr


class NeedsOcrTest(unittest.TestCase):
    def test_threshold_uses_chars_per_page(self):
        self.assertTrue(pdf_ocr.needs_ocr("x.pdf", char_count_fn=lambda p: (30, 2)))
        self.assertFalse(pdf_ocr.needs_ocr("x.pdf", char_count_fn=lambda p: (500, 2)))

    def test_command_shape(self):
        cmd = pdf_ocr.ocr_command("in.pdf", "out.pdf", "eng")
        self.assertEqual((cmd[:3], cmd[-2:], "--skip-text" in cmd, "-l" in cmd),
                         ([sys.executable, "-m", "ocrmypdf"], ["in.pdf", "out.pdf"], True, True))

    def test_run_ocr_without_binaries_raises(self):
        if shutil.which("tesseract"):
            self.skipTest("tesseract present")
        with self.assertRaises(pdf_ocr.OcrError):
            pdf_ocr.run_ocr("in.pdf", "out.pdf")


@unittest.skipUnless(shutil.which("tesseract") and shutil.which("gs"), "OCR binaries not installed")
class OcrEndToEndTest(unittest.TestCase):
    def test_scanned_page_gets_text_layer(self):
        from PIL import Image, ImageDraw

        from importer import pdf_text

        tmp = tempfile.mkdtemp()
        img = Image.new("RGB", (1200, 400), "white")
        d = ImageDraw.Draw(img)
        d.text((40, 40), "08/02/2024 COFFEE SHOP 4.50", fill="black")
        src, dst = os.path.join(tmp, "scan.pdf"), os.path.join(tmp, "scan.ocr.pdf")
        img.save(src, "PDF", resolution=150)
        self.assertTrue(pdf_ocr.needs_ocr(src))
        pdf_ocr.run_ocr(src, dst, timeout=120)
        self.assertFalse(pdf_ocr.needs_ocr(dst))
        text = pdf_text.page_text(pdf_text.extract_pages(dst))
        self.assertIn("COFFEE", text.upper())


if __name__ == "__main__":
    unittest.main()


class MixedDocumentTest(unittest.TestCase):
    def test_text_pdf_with_an_image_only_page_needs_ocr(self):
        self.assertTrue(pdf_ocr.needs_ocr("x.pdf", char_count_fn=lambda p: (4000, 4), image_pages_fn=lambda p: [4]))
        self.assertFalse(pdf_ocr.needs_ocr("x.pdf", char_count_fn=lambda p: (4000, 4), image_pages_fn=lambda p: []))
