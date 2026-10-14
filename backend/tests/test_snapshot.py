from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from cairn.snapshot import pack, unpack


def test_pack_unpack_roundtrip_is_reproducible(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "raw" / "o__r").mkdir(parents=True)
    (src / "raw" / "o__r" / "prs.jsonl").write_text('{"n": 1}\n', encoding="utf-8")
    (src / "llm-cache" / "ab").mkdir(parents=True)
    (src / "llm-cache" / "ab" / "abc.json").write_text("{}", encoding="utf-8")
    (src / "okf").mkdir()
    (src / "okf" / "index.md").write_text("not packed", encoding="utf-8")

    a, b = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
    assert pack(src, a) == {"raw": 1, "llm-cache": 1}
    pack(src, b)
    assert a.read_bytes() == b.read_bytes(), "archives are byte-for-byte reproducible"

    dst = tmp_path / "dst"
    assert unpack(a, dst) == 2
    assert (dst / "raw" / "o__r" / "prs.jsonl").read_text(encoding="utf-8") == '{"n": 1}\n'
    assert not (dst / "okf").exists()


def test_unpack_rejects_path_traversal(tmp_path: Path) -> None:
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        data = b"pwned"
        info = tarfile.TarInfo("raw/../../escape.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(tarfile.FilterError):
        unpack(evil, tmp_path / "dst")
    assert not (tmp_path / "escape.txt").exists()
