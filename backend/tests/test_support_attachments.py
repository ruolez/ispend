import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

_stubs.install()

from PIL import Image  # noqa: E402

import config  # noqa: E402
import support_attachments as sa  # noqa: E402

GPS_TAG = 0x8825


def image_bytes(fmt, size=(40, 30), mode="RGB", exif=False):
    img = Image.new(mode, size, (200, 30, 30) if mode == "RGB" else 128)
    buf = io.BytesIO()
    kwargs = {}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "PhoneMaker"          # Make
        ex[GPS_TAG] = {1: "N", 2: (41.0, 52.0, 0.0)}
        kwargs["exif"] = ex.tobytes()
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


class SniffTest(unittest.TestCase):
    def test_magic_bytes_decide_not_the_name(self):
        self.assertEqual([sa.sniff(image_bytes(f)) for f in ("PNG", "JPEG", "WEBP")], ["png", "jpeg", "webp"])
        self.assertEqual([sa.sniff(b"%PDF-1.7 fake"), sa.sniff(b"")], [None, None])
        self.assertIsNone(sa.sniff(b"GIF89a" + b"\0" * 20))


class PrepareTest(unittest.TestCase):
    def test_each_format_is_re_encoded_with_its_size(self):
        for fmt, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")):
            with self.subTest(fmt=fmt):
                out = sa.prepare(image_bytes(fmt, size=(64, 48)))
                self.assertEqual((out["mime"], out["width"], out["height"], out["bytes"]),
                                 (mime, 64, 48, len(out["data"])))
                self.assertEqual(Image.open(io.BytesIO(out["data"])).format, fmt)

    def test_exif_and_location_are_stripped(self):
        raw = image_bytes("JPEG", exif=True)
        self.assertIn(GPS_TAG, Image.open(io.BytesIO(raw)).getexif())
        out = Image.open(io.BytesIO(sa.prepare(raw)["data"]))
        self.assertEqual((dict(out.getexif()), "exif" in out.info), ({}, False))

    def test_rejections_explain_themselves(self):
        cases = [
            (b"", "The screenshot is empty"),
            (b"%PDF-1.7 not an image", "Screenshots must be PNG, JPEG or WebP images"),
            (b"\x89PNG\r\n\x1a\n" + b"garbage" * 10, "That image could not be read"),
            (b"\xff\xd8\xff" + b"x" * sa.MAX_BYTES, f"Each screenshot must be under {sa.MAX_BYTES // (1024 * 1024)} MB"),
        ]
        for data, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(sa.AttachmentError) as ctx:
                    sa.prepare(data)
                self.assertEqual(str(ctx.exception), message)

    def test_a_decompression_bomb_is_refused(self):
        big = image_bytes("PNG", size=(sa.MAX_SIDE + 1, 2), mode="L")
        with self.assertRaises(sa.AttachmentError) as ctx:
            sa.prepare(big)
        self.assertEqual(str(ctx.exception), "That image is too large")


class StoreTest(unittest.TestCase):
    def test_files_land_in_the_users_support_folder_under_random_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = config.STATEMENTS_DIR
            config.STATEMENTS_DIR = tmp
            try:
                prepared = sa.prepare(image_bytes("PNG"))
                a, b = sa.store(7, prepared), sa.store(7, prepared)
                self.assertNotEqual(a, b)
                for rel in (a, b):
                    self.assertRegex(rel, r"^7/support/[0-9a-f]{32}\.png$")
                    with open(os.path.join(tmp, rel), "rb") as f:
                        self.assertEqual(f.read(), prepared["data"])
                self.assertEqual(sorted(os.listdir(os.path.join(tmp, "7", "support"))), sorted(os.path.basename(r) for r in (a, b)))
            finally:
                config.STATEMENTS_DIR = old


class FromRequestTest(unittest.TestCase):
    def test_more_than_the_limit_is_refused_before_reading(self):
        files = [io.BytesIO(image_bytes("PNG")) for _ in range(sa.MAX_FILES + 1)]
        with self.assertRaises(sa.AttachmentError) as ctx:
            sa.prepare_all(files)
        self.assertEqual(str(ctx.exception), f"Attach at most {sa.MAX_FILES} screenshots")

    def test_every_file_is_prepared(self):
        out = sa.prepare_all([io.BytesIO(image_bytes("PNG")), io.BytesIO(image_bytes("JPEG"))])
        self.assertEqual([p["mime"] for p in out], ["image/png", "image/jpeg"])


if __name__ == "__main__":
    unittest.main()
