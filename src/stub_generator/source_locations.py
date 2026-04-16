import os
from typing import Optional


def is_builtin_source_location(file_path: str) -> bool:
    """Return True for CBMC/clang pseudo-files like ``<builtin-...>``."""
    file_path = (file_path or "").strip()
    return bool(file_path) and file_path.startswith("<") and file_path.endswith(">")


def resolve_source_path(file_path: str, base_path: str = "") -> Optional[str]:
    """
    Resolve a source location to an absolute path when possible.

    Builtin pseudo-files and empty locations return ``None``.
    """
    file_path = (file_path or "").strip()
    base_path = (base_path or "").strip()

    if not file_path or is_builtin_source_location(file_path):
        return None

    if os.path.isabs(file_path):
        return os.path.normpath(file_path)

    if base_path:
        return os.path.normpath(os.path.join(base_path, file_path))

    return os.path.normpath(file_path)
