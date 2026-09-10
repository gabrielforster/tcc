"""Download helpers for public datasets.

Public data is never committed: it is fetched into `data/raw/<dataset>/` on first use and
reused from there afterwards. Keeping this in one place means each source only has to say
what it needs, not how to get it.
"""

import io
import zipfile
from pathlib import Path

import requests

from collection.config import settings

TIMEOUT_SECONDS = 120


def dataset_dir(name: str) -> Path:
    path = settings.dir_raw / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_and_extract_zip(url: str, name: str, expected: str) -> Path:
    """Fetch a zip into data/raw/<name>/ and return the path to `expected`.

    Nested zips are unpacked recursively, which the UCI archive needs: its bundle contains
    further zips rather than the CSVs directly. A dataset already on disk is not
    re-downloaded.
    """
    target = dataset_dir(name)
    existing = next(target.rglob(expected), None)
    if existing is not None:
        return existing

    response = requests.get(url, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    _extract_recursive(io.BytesIO(response.content), target)

    found = next(target.rglob(expected), None)
    if found is None:
        raise FileNotFoundError(f"{expected} not found in the archive downloaded from {url}")
    return found


def _extract_recursive(buffer: io.BytesIO, target: Path, depth: int = 0) -> None:
    if depth > 3:
        return
    with zipfile.ZipFile(buffer) as archive:
        for member in archive.namelist():
            if member.endswith("/"):
                continue
            data = archive.read(member)
            if member.endswith(".zip"):
                _extract_recursive(io.BytesIO(data), target, depth + 1)
                continue
            destination = target / Path(member).name
            destination.write_bytes(data)


def require_local_files(name: str, files: list[str], instructions: str) -> Path:
    """Return data/raw/<name>/ once every file is present, or explain how to get them.

    Used for datasets behind an account (Kaggle), where downloading on the user's behalf is
    not possible.
    """
    target = dataset_dir(name)
    missing = [f for f in files if not (target / f).exists()]
    if missing:
        raise FileNotFoundError(f"Missing {', '.join(missing)} in {target}.\n\n{instructions}")
    return target
