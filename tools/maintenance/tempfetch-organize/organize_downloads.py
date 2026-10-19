#!/usr/bin/env python3
"""整理 /Users/m4/Downloads：根目录散落文件按类别归位 + 垃圾文件统一进 99-待删除。

范围（只动这些，其余一律不碰）：
  1. 根目录 20 个散落文件：
     - dmg/zip            -> 01-安装包/
     - md/html/pdf 工作文档 -> 02-工作文档/（model-governance 保留规范名 + 内容不同的版本）
     - pem                -> 03-凭据/
     - mp4                -> 04-录屏视频/
     - .DS_Store/.localized/重复 PDF 副本 -> 99-待删除/
  2. 全库垃圾文件（.DS_Store / .localized / *.tmp / *.bak）-> 99-待删除/
     （实际全部位于 inresearch资料库/_to_delete、_drawings_unread、_junk_review 内）
不碰：inresearch资料库 的 M01-M15 等既有结构、tempfetch/、*.app bundle。
不删除任何文件；全程写 _整理映射.csv 可回溯。默认 dry-run，--execute 才执行。
"""
import csv
import hashlib
import os
import re
import shutil
import sys

ROOT = "/Users/m4/Downloads"
CAT_INSTALL = "01-安装包"
CAT_DOCS = "02-工作文档"
CAT_SECRET = "03-凭据"
CAT_VIDEO = "04-录屏视频"
CAT_TRASH = "99-待删除"
MAP_CSV = os.path.join(ROOT, "_整理映射_Downloads.csv")

JUNK_NAMES = {".DS_Store", ".localized"}
JUNK_EXTS = {".tmp", ".bak"}
# 受保护的大目录：只在其中挑垃圾文件，不移动其正常内容
PROTECTED_DIRS = ("inresearch资料库",)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(4 * 1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def uniq_name(dst_dir, base, tag, assigned=None):
    """在 dst_dir 下生成不重名文件名：同名时插入 tag（源路径短哈希 / 内容哈希）。
    assigned 为本批次已分配的目标路径集合，防止同批次内撞名。"""
    assigned = assigned if assigned is not None else set()
    stem, ext = os.path.splitext(base)
    cand = base
    n = 0
    while os.path.exists(os.path.join(dst_dir, cand)) or os.path.join(dst_dir, cand) in assigned:
        n += 1
        cand = "%s__%s%s%s" % (stem[:120], tag, ("_%d" % n) if n > 1 else "", ext)
    return os.path.join(dst_dir, cand)


def classify_root_file(name, path):
    low = name.lower()
    if name in JUNK_NAMES:
        return (CAT_TRASH, name)
    if low.endswith((".dmg", ".zip", ".pkg")):
        return (CAT_INSTALL, name)
    if low.endswith(".pem"):
        return (CAT_SECRET, name)
    if low.endswith((".mp4", ".mov", ".mkv")):
        return (CAT_VIDEO, name)
    if low.endswith((".md", ".html", ".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".csv")):
        return (CAT_DOCS, name)
    return (CAT_DOCS, name)  # 其余根目录文件一律先进工作文档，避免滞留根级


def plan_root():
    out = []
    for name in sorted(os.listdir(ROOT)):
        p = os.path.join(ROOT, name)
        if not os.path.isfile(p):
            continue
        cat, newname = classify_root_file(name, p)
        out.append((p, os.path.join(ROOT, cat, newname)))
    return out


def plan_junk(already=None):
    """垃圾文件扫描；already 为已收录的源路径集合，避免与根目录计划重复。"""
    already = already or set()
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # 不进入我们将要创建的类别目录（若重跑）
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(("01-", "02-", "03-", "04-", "99-"))]
        for name in filenames:
            if name in JUNK_NAMES or os.path.splitext(name)[1].lower() in JUNK_EXTS:
                p = os.path.join(dirpath, name)
                if p in already:
                    continue
                if os.path.isfile(p) and not os.path.islink(p):
                    out.append(p)
    return out


def dedupe_pdf_plans(moves, assigned):
    """根目录 model-governance 等同类 PDF：sha256 相同只留一个（规范名优先），其余进 99-待删除。"""
    docs = [(s, d) for s, d in moves if d and d.split(os.sep)[-2] == CAT_DOCS and s.lower().endswith(".pdf")]
    if not docs:
        return moves
    groups = {}
    for s, d in docs:
        groups.setdefault(sha256_file(s), []).append((s, d))
    trash_dir = os.path.join(ROOT, CAT_TRASH)
    trash = []
    keep = []
    for sha, items in groups.items():
        if len(items) == 1:
            keep += items
            continue
        # 规范名优先：不含 "副本"、"(1)"、"(2)"、copy 等字样
        def score(it):
            n = os.path.basename(it[0])
            return (0 if not re.search(r"副本|copy|\(\d+\)", n, re.IGNORECASE) else 1, len(n))
        items.sort(key=score)
        keep.append(items[0])
        for s, d in items[1:]:
            base = os.path.basename(s)
            dst = uniq_name(trash_dir, "%s__dup%s" % (os.path.splitext(base)[0], os.path.splitext(base)[1]),
                            sha[:8], assigned)
            assigned.add(dst)
            trash.append((s, dst))
    others = [m for m in moves if not (m[1] and m[1].split(os.sep)[-2] == CAT_DOCS and m[0].lower().endswith(".pdf"))]
    return others + keep + trash


def main():
    execute = "--execute" in sys.argv
    mode = "EXECUTE" if execute else "DRY-RUN"

    assigned = set()
    final = []

    # 1. 根目录散落文件 -> 各类别目录（目标名保证同批次唯一）
    for name in sorted(os.listdir(ROOT)):
        p = os.path.join(ROOT, name)
        if not os.path.isfile(p):
            continue
        cat, newname = classify_root_file(name, p)
        dst_dir = os.path.join(ROOT, cat)
        tag = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        dst = uniq_name(dst_dir, newname, tag, assigned)
        assigned.add(dst)
        final.append((p, dst))

    final = dedupe_pdf_plans(final, assigned)

    # 2. 垃圾文件 -> 99-待删除（按源路径短哈希去撞名）
    trash_dir = os.path.join(ROOT, CAT_TRASH)
    for s in plan_junk(already={m[0] for m in final}):
        rel = os.path.relpath(s, ROOT)
        tag = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8]
        dst = uniq_name(trash_dir, os.path.basename(s), tag, assigned)
        assigned.add(dst)
        final.append((s, dst))

    # 冲突检查（理论上不该再出现）
    seen = {}
    conflicts = 0
    for s, d in final:
        if d in seen:
            conflicts += 1
            if conflicts <= 5:
                print("  冲突: %s 与 %s 都想去 %s" % (s, seen[d], d))
        seen[d] = s
    if conflicts:
        raise SystemExit("目标冲突 %d 处，已中止" % conflicts)

    by_cat = {}
    for _s, d in final:
        cat = d.split(os.sep)[-2]
        by_cat[cat] = by_cat.get(cat, 0) + 1
    print("[%s] 计划移动 %d 个文件" % (mode, len(final)))
    for cat, n in sorted(by_cat.items()):
        print("   %-12s %d" % (cat, n))

    print("\n样例（每类前 3 条）:")
    shown = {}
    for s, d in final:
        cat = d.split(os.sep)[-2]
        if shown.get(cat, 0) < 3:
            shown[cat] = shown.get(cat, 0) + 1
            print("  [%s] %s\n      -> %s" % (cat, os.path.relpath(s, ROOT), os.path.relpath(d, ROOT)))

    if not execute:
        print("\n[DRY-RUN] 未移动任何文件。加 --execute 执行。")
        return

    for cat in (CAT_INSTALL, CAT_DOCS, CAT_SECRET, CAT_VIDEO, CAT_TRASH):
        os.makedirs(os.path.join(ROOT, cat), exist_ok=True)

    done = errors = 0
    with open(MAP_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["旧路径", "新路径"])
        for s, d in final:
            try:
                os.makedirs(os.path.dirname(d), exist_ok=True)
                if os.path.exists(d):
                    raise FileExistsError("目标已存在: %s" % d)
                shutil.move(s, d)
                w.writerow([os.path.relpath(s, ROOT), os.path.relpath(d, ROOT)])
                done += 1
            except Exception as exc:
                print("  [错误] %s -> %s: %s" % (s, d, exc))
                errors += 1
    print("\n[EXECUTE] 完成：移动 %d，失败 %d" % (done, errors))
    print("映射表: %s" % MAP_CSV)


if __name__ == "__main__":
    main()
