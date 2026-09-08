"""Whole-application backup and restore.

The archive is a migration tool: one ZIP holding every table's rows and every uploaded
statement file, so an admin can move an install to a new server. Schema comes from the
migrations on the target, data comes from the archive.
"""
