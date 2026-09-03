"""Statement import package.

Pure parsers live in submodules (importer.csv_parser, importer.pdf_lines, ...) and never touch the DB.
The orchestration (importer.pipeline) needs config/db, so it is loaded lazily: `importer.parse_statement`
etc. resolve through __getattr__ on first use.
"""

_PIPELINE_NAMES = {
    "ImportError_", "abs_path", "store_upload", "parse_statement", "reparse_with_mapping", "recompute_dupes",
    "commit_statement", "discard_statement", "recover_interrupted",
}


def __getattr__(name):
    if name in _PIPELINE_NAMES:
        from importer import pipeline
        return getattr(pipeline, name)
    raise AttributeError(f"module 'importer' has no attribute {name!r}")
