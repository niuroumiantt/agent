#!/usr/bin/env python3
"""整理 /Users/m4/Downloads/tempfetch（NVIDIA 爬取数据集）。

原则：
  - 不删除任何文件；垃圾文件只移入 99-待删除/ 归位
  - blobs 的 721 个哈希名 HTML 从 <title> 提取产品名改为可读文件名（保留 hash 前 8 位防重名）
  - ledger 的 3 个哈希名 XML 按内容类型改名（sitemapindex / urlset）
  - 所有移动与改名记录进 _整理映射.csv，可完全回溯
默认 dry-run 只打印计划；加 --execute 才执行。
"""
import csv
import html
import os
import re
import shutil
import sys

ROOT = "/Users/m4/Downloads/tempfetch"
CAT_SNAP = "01-产品页快照"
CAT_CATALOG = "02-产品目录数据"
CAT_LEDGER = "03-抓取记录"
CAT_TRASH = "99-待删除"
MAP_CSV = os.path.join(ROOT, "_整理映射.csv")

BRAND_TAIL = re.compile(r"\s*[|｜]\s*(NVIDIA|GeForce|NVIDIA\.com|Features)\s*$", re.IGNORECASE)


def sanitize(name: str, maxlen: int = 90) -> str:
    name = html.unescape(name or "")
    name = re.sub(r"[\\/\0]", "_", name)
    name = re.sub(r"[\r\n\t]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    while BRAND_TAIL.search(name):
        name = BRAND_TAIL.sub("", name)
    name = name.strip().strip(".")
    if len(name) > maxlen:
        name = name[:maxlen].rstrip()
    return name


def html_title(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            head = fh.read(200000).decode("utf-8", "ignore")
    except OSError:
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return sanitize(m.group(1))


def xml_kind(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            head = fh.read(3000).decode("utf-8", "ignore")
    except OSError:
        return "xml"
    m = re.search(r"<loc>\s*([^<]+)</loc>", head)
    first = m.group(1).strip() if m else ""
    if "<sitemapindex" in head:
        tag = "sitemapindex-全球索引"
    elif "<urlset" in head:
        tag = "urlset-" + (re.sub(r"[^a-z0-9]+", "-", first.lower().split("/")[2] if "://" in first else first.lower()) or "unknown")
    else:
        tag = "xml"
    return tag


def plan_blobs(mapping):
    src = os.path.join(ROOT, "blobs")
    out = []
    if not os.path.isdir(src):
        return out
    for dirpath, _dirnames, filenames in os.walk(src):
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            if not os.path.isfile(p):
                continue
            low = name.lower()
            if low.endswith(".html"):
                h8 = os.path.splitext(name)[0][:8]
                title = html_title(p) or "未命名页面"
                new = os.path.join(ROOT, CAT_SNAP, "%s__%s.html" % (sanitize(title), h8))
            elif name == ".DS_Store":
                new = os.path.join(ROOT, CAT_TRASH, "blobs-DS_Store")
            else:
                new = os.path.join(ROOT, CAT_SNAP, name)
            out.append((p, new))
    return out


def plan_catalog(mapping):
    src = os.path.join(ROOT, "product-catalog", "nvidia")
    out = []
    if not os.path.isdir(src):
        return out
    for name in sorted(os.listdir(src)):
        p = os.path.join(src, name)
        if not os.path.isfile(p):
            continue
        if name == "worker.lock":
            new = os.path.join(ROOT, CAT_TRASH, "product-catalog-worker.lock")
        else:
            new = os.path.join(ROOT, CAT_CATALOG, name)
        out.append((p, new))
    return out


def plan_ledger():
    src = os.path.join(ROOT, "ledger")
    out = []
    if not os.path.isdir(src):
        return out
    for dirpath, _dirnames, filenames in os.walk(src):
        for name in filenames:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, src)
            if name.lower().endswith(".xml"):
                h8 = os.path.splitext(name)[0][:8]
                new = os.path.join(ROOT, CAT_LEDGER, os.path.dirname(rel),
                                   "%s__%s.xml" % (xml_kind(p), h8))
            else:
                new = os.path.join(ROOT, CAT_LEDGER, rel)
            out.append((p, new))
    return out


def plan_root_junk():
    out = []
    for name in os.listdir(ROOT):
        p = os.path.join(ROOT, name)
        if os.path.isfile(p) and name == ".DS_Store":
            out.append((p, os.path.join(ROOT, CAT_TRASH, "tempfetch-DS_Store")))
    return out


def main():
    execute = "--execute" in sys.argv
    mode = "EXECUTE" if execute else "DRY-RUN"

    moves = []
    moves += plan_blobs(None)
    moves += plan_catalog(None)
    moves += plan_ledger()
    moves += plan_root_junk()

    # 目标名冲突检查（同目录下不允许两个文件同目标名）
    seen = {}
    conflicts = []
    for src, dst in moves:
        if dst in seen:
            conflicts.append((seen[dst], src, dst))
        seen[dst] = src
    if conflicts:
        print("[%s] 目标名冲突 %d 处，示例:" % (mode, len(conflicts)))
        for a, b, d in conflicts[:5]:
            print("   %s\n   %s\n -> %s" % (a, b, d))
        raise SystemExit("已中止，未移动任何文件")

    print("[%s] 计划移动 %d 个文件" % (mode, len(moves)))
    by_cat = {}
    for _s, d in moves:
        cat = d.split(os.sep)[-2] if d.count(os.sep) >= 2 else "?"
        by_cat[cat] = by_cat.get(cat, 0) + 1
    for cat, n in sorted(by_cat.items()):
        print("   %-14s %d" % (cat, n))

    print("\n样例（前 8 条快照改名 + 全部 XML 改名 + 全部待删除）:")
    shown = 0
    for src, dst in moves:
        tag = None
        if CAT_SNAP in dst and shown < 8:
            tag, shown = "快照", shown + 1
        elif dst.endswith(".xml"):
            tag = "XML"
        elif CAT_TRASH in dst:
            tag = "待删除"
        if tag:
            print("  [%s] %s\n      -> %s" % (tag, os.path.relpath(src, ROOT), os.path.relpath(dst, ROOT)))

    if not execute:
        print("\n[DRY-RUN] 未移动任何文件。确认无误后加 --execute 执行。")
        return

    # 执行
    for cat in (CAT_SNAP, CAT_CATALOG, CAT_LEDGER, CAT_TRASH):
        os.makedirs(os.path.join(ROOT, cat), exist_ok=True)

    done = errors = 0
    with open(MAP_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["旧路径", "新路径"])
        for src, dst in moves:
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.exists(dst):
                    raise FileExistsError("目标已存在: %s" % dst)
                shutil.move(src, dst)
                w.writerow([os.path.relpath(src, ROOT), os.path.relpath(dst, ROOT)])
                done += 1
            except Exception as exc:
                print("  [错误] %s -> %s: %s" % (src, dst, exc))
                errors += 1
    print("\n[EXECUTE] 完成：移动 %d，失败 %d" % (done, errors))
    print("映射表: %s" % MAP_CSV)


if __name__ == "__main__":
    main()
