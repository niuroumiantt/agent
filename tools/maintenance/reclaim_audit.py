#!/usr/bin/env python3
"""只读核对 raw-materials 与 originals 的重复关系。

严格只读：仅以 'rb' 打开文件计算 SHA-256，不写、不删、不移动、不改名任何被核对文件。
输出两个文件到 OUTDIR（家目录，不落在 data root 内，避免被 reader scan 扫到）：
  audit.jsonl   逐文件一行：path / size / sha256 / status / orig
  summary.json  汇总：dup、orphan、error、empty 的数量与字节数，以及按顶层子目录的可回收体积

status 语义：
  dup      originals/<hh>/<sha256>/ 已有同名目录且内含文件 -> 该 raw 文件冗余
  orphan   originals 中无对应内容 -> 删除会丢失，必须保留
  empty    0 字节文件，单独统计
  error    读取失败（权限、损坏等），保守视为不可回收
"""
import os
import sys
import json
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "/home/spark/.local/share/inresearch.ai"
RAW = os.path.join(BASE, "raw-materials")
ORIG = os.path.join(BASE, "originals")
OUTDIR = "/home/spark/reclaim-audit-20261019"
OUT = os.path.join(OUTDIR, "audit.jsonl")
SUMMARY = os.path.join(OUTDIR, "summary.json")

WORKERS = 8
CHUNK = 4 * 1024 * 1024


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def orig_has(digest):
    """originals/<前两位>/<完整sha256>/ 是否存在且内含至少一个文件。"""
    target = os.path.join(ORIG, digest[:2], digest)
    if not os.path.isdir(target):
        return None
    try:
        for name in os.listdir(target):
            candidate = os.path.join(target, name)
            if os.path.isfile(candidate):
                return candidate
    except OSError:
        return None
    return None


def classify(path):
    try:
        size = os.path.getsize(path)
        if size == 0:
            return {"path": path, "size": 0, "sha256": None, "status": "empty", "orig": None}
        digest = sha256_file(path)
        hit = orig_has(digest)
        return {
            "path": path,
            "size": size,
            "sha256": digest,
            "status": "dup" if hit else "orphan",
            "orig": hit,
        }
    except Exception as exc:
        return {
            "path": path,
            "size": None,
            "sha256": None,
            "status": "error",
            "orig": None,
            "error": "%s: %s" % (type(exc).__name__, exc),
        }


def collect():
    paths = []
    for base, dirs, names in os.walk(RAW, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith(".Trash") and d != ".DS_Store"]
        for name in names:
            p = os.path.join(base, name)
            if os.path.islink(p):
                continue
            if not os.path.isfile(p):
                continue
            paths.append(p)
    return paths


def top_group(path):
    rel = os.path.relpath(path, RAW)
    head = rel.split(os.sep, 1)
    return head[0] if len(head) > 1 else "（顶层散文件）"


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    started = time.time()
    paths = collect()
    total = len(paths)
    print("待核对文件 %d 个" % total, flush=True)

    counts = {"dup": 0, "orphan": 0, "error": 0, "empty": 0}
    bytes_by = {"dup": 0, "orphan": 0, "error": 0, "empty": 0}
    group_dup_bytes = {}
    group_dup_count = {}
    group_orphan_bytes = {}
    errors = []

    done = 0
    with open(OUT, "w", encoding="utf-8") as out:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(classify, p) for p in paths]
            for fut in as_completed(futures):
                row = fut.result()
                done += 1
                st = row["status"]
                counts[st] += 1
                if row.get("size"):
                    bytes_by[st] += row["size"]
                g = top_group(row["path"])
                if st == "dup":
                    group_dup_bytes[g] = group_dup_bytes.get(g, 0) + (row.get("size") or 0)
                    group_dup_count[g] = group_dup_count.get(g, 0) + 1
                elif st == "orphan":
                    group_orphan_bytes[g] = group_orphan_bytes.get(g, 0) + (row.get("size") or 0)
                if st == "error":
                    errors.append({"path": row["path"], "error": row.get("error")})
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                if done % 2000 == 0:
                    rate = done / max(time.time() - started, 0.01)
                    print(
                        "进度 %d/%d  dup=%d orphan=%d error=%d  %.0f 文件/秒"
                        % (done, total, counts["dup"], counts["orphan"], counts["error"], rate),
                        flush=True,
                    )

    elapsed = time.time() - started
    top_dup = sorted(group_dup_bytes.items(), key=lambda kv: kv[1], reverse=True)[:25]
    summary = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "raw_root": RAW,
        "originals_root": ORIG,
        "files_total": total,
        "counts": counts,
        "bytes": bytes_by,
        "elapsed_seconds": round(elapsed, 1),
        "reclaimable_bytes": bytes_by["dup"],
        "reclaimable_human": "%.1f GB" % (bytes_by["dup"] / 1024.0 ** 3),
        "top_reclaimable_groups": [
            {
                "group": g,
                "dup_bytes": b,
                "dup_human": "%.1f GB" % (b / 1024.0 ** 3),
                "dup_files": group_dup_count.get(g, 0),
                "orphan_bytes": group_orphan_bytes.get(g, 0),
            }
            for g, b in top_dup
        ],
        "error_samples": errors[:50],
        "error_total": len(errors),
    }
    with open(SUMMARY, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print("\n=== 完成 %.1f 秒 ===" % elapsed)
    print("dup(可回收)   %6d 个  %.1f GB" % (counts["dup"], bytes_by["dup"] / 1024.0 ** 3))
    print("orphan(必留)  %6d 个  %.1f GB" % (counts["orphan"], bytes_by["orphan"] / 1024.0 ** 3))
    print("empty(空文件) %6d 个" % counts["empty"])
    print("error(读失败) %6d 个" % counts["error"])
    print("清单 %s" % OUT)
    print("汇总 %s" % SUMMARY)


if __name__ == "__main__":
    main()
