"""The archive container: a ZIP with per-member SHA-256.

ZIP rather than tar.gz because the inspect step has to read the manifest without scanning a
multi-GB file, and because uploaded PDFs/XLSX are already compressed and must not be squeezed
again. manifest.json is written LAST, so a build that crashes leaves an archive that fails
closed rather than one that looks complete.
"""

import hashlib
import io
import json
import zipfile

FORMAT = "ispend-backup"
FORMAT_VERSION = 1
MANIFEST = "manifest.json"
FILES_INDEX = "files.json"
DB_PREFIX = "db/"
FILES_PREFIX = "files/"

# Already-compressed payloads: deflating them again costs CPU and saves nothing.
STORED_SUFFIXES = (".pdf", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".zip", ".gz")


def _compression(name):
    lowered = name.lower()
    if lowered.endswith(STORED_SUFFIXES):
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


class HashingWriter(io.RawIOBase):
    """File-like sink handed to psycopg2's copy_expert: tees into the ZIP member while hashing
    and counting rows. COPY text format is exactly one row per newline (embedded newlines are
    escaped), so counting newlines is exact and does not depend on cursor.rowcount."""

    def __init__(self, stream):
        self._stream = stream
        self._digest = hashlib.sha256()
        self.bytes = 0
        self.rows = 0

    def writable(self):
        return True

    def write(self, chunk):
        data = bytes(chunk)
        self._stream.write(data)
        self._digest.update(data)
        self.bytes += len(data)
        self.rows += data.count(b"\n")
        return len(data)

    @property
    def sha256(self):
        return self._digest.hexdigest()


class HashVerifyingReader(io.RawIOBase):
    """File-like source handed to copy_expert on restore; finish() raises if the member does not
    match the digest recorded when it was written."""

    def __init__(self, stream, expected_sha):
        self._stream = stream
        self._expected = expected_sha
        self._digest = hashlib.sha256()
        self.bytes = 0

    def readable(self):
        return True

    def read(self, size=-1):
        chunk = self._stream.read(size)
        if chunk:
            self._digest.update(chunk)
            self.bytes += len(chunk)
        return chunk

    def readinto(self, buf):
        chunk = self.read(len(buf))
        buf[: len(chunk)] = chunk
        return len(chunk)

    def finish(self):
        if self._expected and self._digest.hexdigest() != self._expected:
            raise ArchiveError("The archive is damaged: a table's data does not match its checksum.")


class ArchiveError(Exception):
    """User-facing: the message is shown to the admin verbatim."""


class ArchiveWriter:
    def __init__(self, path):
        self._zip = zipfile.ZipFile(path, "w", allowZip64=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._zip.close()
        return False

    def table_writer(self, table):
        """Context manager yielding a HashingWriter for db/<table>.copy."""
        return _MemberWriter(self._zip, f"{DB_PREFIX}{table}.copy")

    def add_file(self, arcname, fileobj):
        """Stream one uploaded statement in, returning (sha256, bytes)."""
        info = zipfile.ZipInfo(arcname)
        info.compress_type = _compression(arcname)
        digest, total = hashlib.sha256(), 0
        with self._zip.open(info, "w", force_zip64=True) as dest:
            while True:
                chunk = fileobj.read(1024 * 1024)
                if not chunk:
                    break
                dest.write(chunk)
                digest.update(chunk)
                total += len(chunk)
        return digest.hexdigest(), total

    def add_json(self, name, payload):
        self._zip.writestr(zipfile.ZipInfo(name), json.dumps(payload, indent=2, default=str),
                           compress_type=zipfile.ZIP_DEFLATED)


class _MemberWriter:
    def __init__(self, zf, arcname):
        self._zf = zf
        self._arcname = arcname
        self._raw = None
        self.writer = None

    def __enter__(self):
        info = zipfile.ZipInfo(self._arcname)
        info.compress_type = _compression(self._arcname)
        self._raw = self._zf.open(info, "w", force_zip64=True)
        self.writer = HashingWriter(self._raw)
        return self.writer

    def __exit__(self, *exc):
        self._raw.close()
        return False


class ArchiveReader:
    def __init__(self, path):
        try:
            self._zip = zipfile.ZipFile(path)
        except zipfile.BadZipFile as e:
            raise ArchiveError("That file is not a readable ZIP archive.") from e
        self._names = set(self._zip.namelist())
        if MANIFEST not in self._names:
            raise ArchiveError("Not an iSpend backup: manifest.json is missing. "
                               "An archive whose build was interrupted looks like this.")
        self.manifest = self._json(MANIFEST)
        if self.manifest.get("format") != FORMAT:
            raise ArchiveError("That archive was not produced by iSpend.")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._zip.close()
        return False

    def _json(self, name):
        try:
            with self._zip.open(name) as fh:
                return json.loads(fh.read().decode("utf-8"))
        except (KeyError, ValueError, UnicodeDecodeError) as e:
            raise ArchiveError(f"The archive is damaged: {name} could not be read.") from e

    def files_index(self):
        return self._json(FILES_INDEX) if FILES_INDEX in self._names else []

    def has_table(self, table):
        return f"{DB_PREFIX}{table}.copy" in self._names

    def table_reader(self, table, expected_sha):
        return _MemberReader(self._zip, f"{DB_PREFIX}{table}.copy", expected_sha)

    def open_file(self, arcname):
        return self._zip.open(arcname)

    def has_file(self, arcname):
        return arcname in self._names


class _MemberReader:
    def __init__(self, zf, arcname, expected_sha):
        self._zf = zf
        self._arcname = arcname
        self._sha = expected_sha
        self._raw = None

    def __enter__(self):
        self._raw = self._zf.open(self._arcname)
        self.reader = HashVerifyingReader(self._raw, self._sha)
        return self.reader

    def __exit__(self, exc_type, *_):
        if exc_type is None:
            self.reader.finish()
        self._raw.close()
        return False
