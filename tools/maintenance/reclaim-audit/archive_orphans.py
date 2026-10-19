#!/usr/bin/env python3
"""把 raw-materials 中的 327 份 orphan 整理：删垃圾、把真文档移到扁平暂存目录。

- 垃圾判定（与 reader.is_partial 一致 + tmp/bak）：路径任一段以 "." 或 "~$" 开头、
  文件名是 .DS_Store、扩展名在 {tmp,bak} -> 直接删除（这些在 audit 中确认无 originals 副本）。
- 其余（pdf/doc/docx/xlsx/html/json/dwg/ctb/无扩展名真文档）-> 移到 raw-materials/_archive_flat/，
  用 sha256[:12]_清洗名 作唯一名，避免碰撞。之后由 reader scan 经项目自带 _register 入库。
不触碰 originals / library / documents；只整理 raw-materials 内的 orphan。
"""
import hashlib
import os
import shutil
from pathlib import Path

RAW = Path("/home/spark/.local/share/inresearch.ai/raw-materials")
STAGE = RAW / "_archive_flat"
MANIFEST = "/home/spark/reclaim-audit-20261019/keep-orphan.txt"
JUNK_EXT = {".tmp", ".bak"}


def is_junk(path: Path) -> bool:
    rel = path.relative_to(RAW).as_posix()
    parts = Path(rel).parts
    if any(p.startswith(".") or p.startswith("~$") for p in parts):
        return True
    if path.name == ".DS_Store":
        return True
    if path.suffix.lower() in JUNK_EXT:
        return True
    return False


def main():
    STAGE.mkdir(parents=True, exist_ok=True)
    moved = deleted = err = skipped = 0
    log = []
    for line in open(MANIFEST, encoding="utf-8"):
        p = line.rstrip("\n").rpartition("\t")[0]
        src = Path(p)
        if not src.exists() or not src.is_file():
            skipped += 1
            continue
        if is_junk(src):
            try:
                src.unlink()
                deleted += 1
                log.append(("DEL", str(src)))
            except Exception as e:
                err += 1
                log.append(("ERR_DEL", str(src), str(e)))
            continue
        try:
            sha = hashlib.sha256(src.read_bytes()).hexdigest()
        except Exception as e:
            err += 1
            log.append(("ERR_READ", str(src), str(e)))
            continue
        safe = "".join(c if (c.isalnum() or c in "._- ") else "_" for c in src.name)[:120]
        dest = STAGE / (sha[:12] + "_" + safe)
        n = 0
        while dest.exists():
            n += 1
            dest = STAGE / (sha[:12] + "_%d_" % n + safe)
        try:
            shutil.move(str(src), str(dest))
            moved += 1
            log.append(("MOV", str(src), "->", str(dest)))
        except Exception as e:
            err += 1
            log.append(("ERR_MOV", str(src), str(e)))
    print("moved=%d deleted_junk=%d err=%d skipped=%d" % (moved, deleted, err, skipped))
    with open("/home/spark/reclaim-audit-20261019/archive-orphans.log", "w", encoding="utf-8") as fh:
        for row in log:
            fh.write("\t".join(row) + "\n")


if __name__ == "__main__":
    main()
