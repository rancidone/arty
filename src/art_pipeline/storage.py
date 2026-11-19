"""Immutable revision directories and portable, content-addressed snapshots."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def now():
    return datetime.now(timezone.utc).isoformat()


WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{n}" for prefix in ("COM", "LPT") for n in "123456789¹²³"}


def portable_filename(value):
    """Reject names that cannot be faithfully represented on Windows."""
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or value.endswith((".", " "))
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in value)
            or value.split('.')[0].upper() in WINDOWS_DEVICES
            or len(value.encode('utf-16-le')) // 2 > 255):
        raise ValueError(f"Unsafe portable filename: {value!r}")
    return value


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError(f"Unsafe identity: {value!r}")
    return portable_filename(value)


def local(root, relative):
    p = (Path(root) / relative).resolve()
    if not p.is_relative_to(Path(root).resolve()):
        raise ValueError("Manifest path escapes revision")
    return p


@contextmanager
def revision(destination):
    """Publish a complete directory; refuse overwrites, including concurrent builds."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.with_name(destination.name + ".lock")
    # Keep the exclusive-create lock file, not an open handle: Windows cannot
    # unlink an open file. Only the process which created it removes it.
    with lock.open("x"):
        pass
    staging = None
    try:
        staging = Path(tempfile.mkdtemp(prefix=".art-stage-", dir=destination.parent))
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"Revision exists: {destination}; choose a new revision")
        yield staging
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)
        os.rename(staging, destination)
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        lock.unlink(missing_ok=True)


def snapshot(path, root, expected=None):
    path = Path(path)
    portable_filename(path.name)
    digest = sha(path)
    if expected and digest != expected:
        raise ValueError(f"Source identity mismatch: {path}")
    relative = f"inputs/{digest}/{path.name}"
    target = local(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    for existing in target.parent.iterdir():
        if existing.name.casefold() == path.name.casefold() and existing.name != path.name:
            raise ValueError("Snapshot filenames collide ignoring case")
    shutil.copyfile(path, target)
    if sha(target) != digest:
        raise ValueError(f"Source changed while copying: {path}")
    return {"file": relative, "original_filename": path.name, "sha256": digest}


def identities(root, manifest):
    records = manifest.get("sources", []) + manifest.get("artifacts", []) + manifest.get("sidecars", [])
    return {r["file"]: sha(local(root, r["file"])) for r in records}
