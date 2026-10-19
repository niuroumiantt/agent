#!/usr/bin/env python3
"""删除 incoming/ + sorted/ 下 20260919-nas-laobing2025 数据集（含 _review）。

安全约束：
- TARGETS 写死为两个精确数据集子目录，绝不触碰 originals/library/catalog。
- 收集文件时对每个路径做 relative_to 越界校验，越界立即抛异常中止。
- 默认 DRY-RUN（不加 --execute 不删任何东西）；--execute 才真删。
- 删前打印待删总数与样例链接数；删后由调用方另行核验 originals/library 不受影响。
"""
import os, sys, shutil
from pathlib import Path

BASE = Path("/home/spark/.local/share/inresearch.ai")
TARGETS = [
    BASE / "incoming" / "20260919-nas-laobing2025",
    BASE / "sorted" / "20260919-nas-laobing2025",
]


def collect():
    files = []
    for t in TARGETS:
        if not t.exists():
            continue
        t_res = t.resolve()
        for root, _, fs in os.walk(t):
            for f in fs:
                p = Path(root) / f
                # 越界校验：必须严格位于 TARGET 之下
                p.resolve().relative_to(t_res)
                files.append(p)
    return files


def main():
    dry = "--execute" not in sys.argv
    files = collect()
    print("目标目录:")
    for t in TARGETS:
        print("   %s  [%s]" % (t, "存在" if t.exists() else "不存在,跳过"))
    print("待移除文件总数: %d" % len(files))
    if files:
        try:
            print("样例链接数(links)=%d -> %s" % (os.stat(files[0]).st_nlink, files[0].name[:60]))
        except Exception as e:
            print("  (链接数读取失败: %s)" % e)
    if dry:
        for f in files[:15]:
            print("  [dry] %s" % f)
        if len(files) > 15:
            print("  ... 共 %d 个文件" % len(files))
        print(">> DRY-RUN：未删除任何文件。加 --execute 才会真正删除。")
        return
    removed = 0
    for f in files:
        try:
            f.unlink()
            removed += 1
        except Exception as e:
            print("ERR unlink %s : %s" % (f, e))
    for t in TARGETS:
        try:
            if t.exists():
                shutil.rmtree(t)
        except Exception as e:
            print("ERR rmtree %s : %s" % (t, e))
    print(">> 已删除文件: %d/%d" % (removed, len(files)))


if __name__ == "__main__":
    main()
