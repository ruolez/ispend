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
        from PIL import Image, ImageDraw, ImageFont

        from importer import pdf_text

        tmp = tempfile.mkdtemp()
        img = Image.new("RGB", (1600, 700), "white")
        d = ImageDraw.Draw(img)
        font = ImageFont.load_default(size=44)
        lines = ["08/02/2024  COFFEE SHOP DOWNTOWN        4.50",
                 "08/03/2024  GROCERY MARKET NORTH       62.18",
                 "08/05/2024  PAYMENT THANK YOU        -200.00"]
        for i, line in enumerate(lines):
            d.text((60, 80 + i * 120), line, fill="black", font=font)
        src, dst = os.path.join(tmp, "scan.pdf"), os.path.join(tmp, "scan.ocr.pdf")
        img.save(src, "PDF", resolution=150)
        self.assertTrue(pdf_ocr.needs_ocr(src))
        pdf_ocr.run_ocr(src, dst, timeout=180)
        self.assertFalse(pdf_ocr.needs_ocr(dst), "the OCR text layer must clear the chars-per-page floor")
        text = pdf_text.page_text(pdf_text.extract_pages(dst)).upper()
        self.assertTrue("COFFEE" in text and "GROCERY" in text, text[:200])


if __name__ == "__main__":
    unittest.main()


class MixedDocumentTest(unittest.TestCase):
    def test_text_pdf_with_an_image_only_page_needs_ocr(self):
        self.assertTrue(pdf_ocr.needs_ocr("x.pdf", char_count_fn=lambda p: (4000, 4), image_pages_fn=lambda p: [4]))
        self.assertFalse(pdf_ocr.needs_ocr("x.pdf", char_count_fn=lambda p: (4000, 4), image_pages_fn=lambda p: []))
