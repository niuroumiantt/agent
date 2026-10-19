#!/usr/bin/env python3
"""按 reclaim-safe.txt 逐条回收 raw-materials 中的冗余文件。

设计原则：每删一个文件之前，重新验证三件事，任何一条不满足就跳过并记入日志。
  1. 文件仍然存在，且是普通文件（不是目录、不是符号链接）
  2. 当前 SHA-256 与清单中记录的完全一致（防止清单生成后文件被改动）
  3. originals/<前两位>/<完整SHA-256>/ 里仍然有可读文件（确保副本还在）

默认 dry-run：只统计、不删除。必须显式加 --execute 才真正执行。
不删目录、不删清单外的任何文件、遇到异常一律跳过而不是继续。
"""
import argparse
import hashlib
import json
import os
import time

BASE = "/home/spark/.local/share/inresearch.ai"
ORIG = os.path.join(BASE, "originals")
AUDIT_DIR = "/home/spark/reclaim-audit-20261019"
MANIFEST = os.path.join(AUDIT_DIR, "reclaim-safe.txt")
REPORT = os.path.join(AUDIT_DIR, "delete-report.jsonl")

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


def orig_ok(digest):
    target = os.path.join(ORIG, digest[:2], digest)
    if not os.path.isdir(target):
        return False
    try:
        return any(os.path.isfile(os.path.join(target, n)) for n in os.listdir(target))
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="真正删除；不加则只做 dry-run")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条，0 表示全部")
    ap.add_argument("--manifest", default=MANIFEST)
    args = ap.parse_args()

    if not os.path.isfile(args.manifest):
        raise SystemExit("清单不存在: %s" % args.manifest)

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    entries = []
    with open(args.manifest, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            path, _, digest = line.rpartition("\t")
            entries.append((path, digest))
            if args.limit and len(entries) >= args.limit:
                break

    print("[%s] 清单 %d 条" % (mode, len(entries)), flush=True)

    stats = {"deleted": 0, "skipped_missing": 0, "skipped_changed": 0,
             "skipped_no_orig": 0, "error": 0}
    freed = 0
    started = time.time()

    with open(REPORT, "a", encoding="utf-8") as log:
        for idx, (path, digest) in enumerate(entries, 1):
            try:
                if not os.path.lexists(path) or os.path.islink(path) or not os.path.isfile(path):
                    stats["skipped_missing"] += 1
                    log.write(json.dumps({"path": path, "action": "skip", "reason": "missing_or_not_file"}, ensure_ascii=False) + "\n")
                    continue
                actual = sha256_file(path)
                if actual != digest:
                    stats["skipped_changed"] += 1
                    log.write(json.dumps({"path": path, "action": "skip", "reason": "sha256_changed", "expected": digest, "actual": actual}, ensure_ascii=False) + "\n")
                    continue
                if not orig_ok(digest):
                    stats["skipped_no_orig"] += 1
                    log.write(json.dumps({"path": path, "action": "skip", "reason": "originals_copy_missing"}, ensure_ascii=False) + "\n")
                    continue
                size = os.path.getsize(path)
                if args.execute:
                    os.remove(path)
                    stats["deleted"] += 1
                    freed += size
                    log.write(json.dumps({"path": path, "action": "deleted", "size": size}, ensure_ascii=False) + "\n")
                else:
                    stats["deleted"] += 1
                    freed += size
            except Exception as exc:
                stats["error"] += 1
                log.write(json.dumps({"path": path, "action": "error", "error": "%s: %s" % (type(exc).__name__, exc)}, ensure_ascii=False) + "\n")

            if idx % 5000 == 0:
                print("  %d/%d  已处理完成 %.1f GB" % (idx, len(entries), freed / 1024.0 ** 3), flush=True)

    elapsed = time.time() - started
    print("\n[%s] 完成 %.1f 秒" % (mode, elapsed))
    print("  通过校验并%s %d 个，合计 %.2f GB" % ("删除" if args.execute else "可删（未删）", stats["deleted"] - (0 if args.execute else 0), freed / 1024.0 ** 3))
    print("  文件已不存在     %d" % stats["skipped_missing"])
    print("  内容已变(跳过)   %d" % stats["skipped_changed"])
    print("  originals缺副本  %d" % stats["skipped_no_orig"])
    print("  异常             %d" % stats["error"])
    print("  日志 %s" % REPORT)


if __name__ == "__main__":
    main()
