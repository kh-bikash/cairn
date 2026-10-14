"""Pack and unpack a reproducible demo snapshot: raw GitHub data plus the LLM response cache.

With a snapshot, anyone can rebuild the exact demo graph with no GitHub token and no LLM keys:
loading is deterministic and extraction/drift replay cached model responses.
"""

from __future__ import annotations

import gzip
import tarfile
from pathlib import Path

DEFAULT_SNAPSHOT = Path("demo") / "cairn-snapshot.tar.gz"
_PARTS = ("raw", "llm-cache")


def pack(data_dir: Path, out: Path) -> dict[str, int]:
    out.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    # gzip headers embed a filename and timestamp; blank both so identical data gives identical bytes.
    with out.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        for part in _PARTS:
            root = data_dir / part
            files = sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []
            for f in files:
                info = tar.gettarinfo(str(f), arcname=f.relative_to(data_dir).as_posix())
                info.mtime = 0  # reproducible archive bytes
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with f.open("rb") as fh:
                    tar.addfile(info, fh)
            counts[part] = len(files)
    return counts


def unpack(src: Path, data_dir: Path) -> int:
    if not src.exists():
        raise FileNotFoundError(f"snapshot {src} not found")
    data_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(src, "r:gz") as tar:
        members = [m for m in tar.getmembers() if m.name.split("/", 1)[0] in _PARTS]
        # The 'data' filter rejects absolute paths, '..' traversal, links and device files.
        tar.extractall(data_dir, members=members, filter="data")
    return len(members)
