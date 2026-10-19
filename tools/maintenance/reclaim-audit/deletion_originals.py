#!/usr/bin/env python3
"""彻底删除 library/_review 指向的 6 个已入库文档。

删除范围（每个 sha256 三处一一对应）：
  1) library 全库中所有指向该 sha256 的符号链接
  2) catalog.documents 表中该 sha256 的行
  3) originals/<hh>/<sha256>/ 实体目录（唯一副本）

安全约束：
- SHAS 写死 6 个 sha256；只删这三处，绝不触碰其他任何文件/行。
- 删除前三重校验：originals 目录存在、实体文件数==1、links==1、documents 有记录；任一不过即中止。
- 默认 DRY-RUN；--execute 才真删。
- 删除前导出 documents 行为 TSV 留档（审计用，不保留文件实体）。
"""
import os
import sys
import shutil
import sqlite3
import subprocess
from pathlib import Path

BASE = Path("/home/spark/.local/share/inresearch.ai")
CAT = BASE / "catalog" / "catalog.sqlite"

SHAS = [
    "91e8e655859e5dce21d7a73b3a45df5b4ecdeba4917e78ddf497e90e6cf74d63",  # 华为绿色发展2030
    "a4b8c4b25871d09aef293c81e48dceb7a0344cb40d06bdbb442d7236204ab271",  # Solidigm QLC
    "f30a9d40d3bacbe9926492eaf1b9d37507e22835bfa8a8e0cebde89bb455791d",  # OCP Intro
    "72106e12fb511ed7a5e33650c729ec1616e86c4baf9465463b5aadaeb0a127f9",  # 信达证券 旺能环境
    "c2d9dbe9b1a3c2ae2cdbcb0dc70c4c21683867c14150d1fe5342da8a754fd50c",  # 中信建投 通信算力
    "87256df8352e90288127680e75c404eea662e0e604df67fcdfe98c3d962036a0",  # 南方电网 5G@电力
]

COLS = "doc_id,sha256,original_name,original_rel,suffix,size_bytes,created,library_rel,current_revision_id"


def orig_dir(sha):
    return BASE / "originals" / sha[:2] / sha


def doc_row(sha):
    conn = sqlite3.connect(str(CAT), timeout=30)
    try:
        r = conn.execute("SELECT %s FROM documents WHERE sha256=?" % COLS, (sha,)).fetchone()
    finally:
        conn.close()
    return r


def library_symlinks(sha):
    out = subprocess.run(
        ["find", str(BASE / "library"), "-type", "l", "-lname", "*%s*" % sha],
        capture_output=True, text=True,
    )
    return [Path(x) for x in out.stdout.splitlines() if x.strip()]


def check(sha):
    probs = []
    d = orig_dir(sha)
    if not d.exists():
        probs.append("originals目录不存在")
    else:
        files = [p for p in d.rglob("*") if p.is_file()]
        if len(files) != 1:
            probs.append("实体文件数=%d(期望1)" % len(files))
        elif os.stat(files[0]).st_nlink != 1:
            probs.append("links=%d(期望1)" % os.stat(files[0]).st_nlink)
    if not doc_row(sha):
        probs.append("documents无记录")
    return probs


def main():
    dry = "--execute" not in sys.argv
    print("== 三重校验 ==")
    allok = True
    for sha in SHAS:
        probs = check(sha)
        n = len(library_symlinks(sha))
        print("  %s  refs=%d  %s" % (sha[:12], n, "OK" if not probs else "FAIL: " + "; ".join(probs)))
        if probs:
            allok = False
    if not allok:
        print(">> 校验未全部通过，中止。")
        sys.exit(1)
    if dry:
        print(">> DRY-RUN：未删除任何东西。加 --execute 才真删。")
        return

    # 留档 documents 行
    tsv = BASE / "_delete_20261019_documents.tsv"
    with open(tsv, "w") as f:
        f.write(COLS.replace(",", "\t") + "\n")
        for sha in SHAS:
            r = doc_row(sha)
            f.write("\t".join("" if x is None else str(x) for x in r) + "\n")
    print(">> documents 行已留档: %s" % tsv)

    conn = sqlite3.connect(str(CAT), timeout=30)
    rl = rd = rr = 0
    for sha in SHAS:
        # 1) library 全库符号链接（先收集再逐个删）
        for l in library_symlinks(sha):
            try:
                l.unlink()
                rl += 1
            except FileNotFoundError:
                pass
            except Exception as e:
                print("ERR unlink symlink %s: %s" % (l, e))
        # 2) documents 行
        cur = conn.execute("DELETE FROM documents WHERE sha256=?", (sha,))
        rd += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        # 3) originals 实体目录
        d = orig_dir(sha)
        try:
            shutil.rmtree(d)
            rr += 1
        except Exception as e:
            print("ERR rmtree %s: %s" % (d, e))
    conn.commit()
    conn.close()
    print(">> 已删: 符号链接=%d, documents行=%d, originals目录=%d" % (rl, rd, rr))


if __name__ == "__main__":
    main()
