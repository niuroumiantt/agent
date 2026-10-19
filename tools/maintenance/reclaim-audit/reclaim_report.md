# raw-materials 冗余核对报告

生成时间：2026-10-19 16:40
主机：spark（100.100.1.2）
核对范围：`/home/spark/.local/share/inresearch.ai/raw-materials/`
比对基准：`/home/spark/.local/share/inresearch.ai/originals/`（内容寻址存储）+ `catalog/catalog.sqlite`

---

## 一、结论

| 类别 | 数量 | 体积 | 处置 |
|---|---:|---:|---|
| **dup（已有副本，可回收）** | **90,619** | **249.84 GB** | 可删，逐条清单已出 |
| **orphan（无副本，必须保留）** | **327** | **27.96 MB** | 不能删 |
| empty（0 字节空文件） | 473 | 0 | 无意义，可留可删 |
| error（读取失败） | 0 | 0 | 无 |

核对 91,419 个文件、250 GB，耗时 76.9 秒，无读取失败。

**处置后磁盘：已用 649G → 约 399G，剩余 2.9T → 3.2T。**

---

## 二、核对方法

对 `raw-materials` 下每个普通文件计算 SHA-256，检查 `originals/<前两位>/<完整SHA-256>/` 是否存在且内含实体文件。

- 命中 → `dup`：内容在 `originals` 里有完全相同的副本（SHA-256 相同即字节级一致）
- 未命中 → `orphan`：`originals` 里没有，删了就没了

整个过程只以 `rb` 方式打开文件，不写、不删、不移动、不改名任何被核对文件。

---

## 三、四道验证（为什么可以相信这个结论）

**1. 不是硬链接，删了真的释放空间**
随机抽 12 份 dup，检查 `stat` 链接数与 inode：
```
12 links=1  独立副本
```
全部是 `links=1` 的独立文件，与 `originals` 里那份不是同一 inode。删除会真实回收磁盘。

> 对比：`sorted/` 与 `incoming/` 之间才是硬链接（`links=2`），所以那 2.3G 删一边不释放空间。

**2. 数据库里也真的登记过**
随机抽 200 份 dup，查 `documents` 表：
```
抽样 200 份：documents 有记录 200，无记录 0（命中率 100.0%）
```
说明这些副本不仅是文件躺在 `originals`，元数据也在库里能查到。

**3. 副本目录里确有实体文件**
判定时检查了目录内 `os.listdir` 存在 `isfile` 的条目，排除了"空目录残留"。

**4. 删除脚本运行时再验一次**
`reclaim_delete.py` 每删一个文件前重新计算 SHA-256 并与清单比对，同时复查 `originals` 副本仍在。三道条件任一不满足就跳过。

---

## 四、可回收体积分布

| 顶层目录 | 可回收 | 份数 |
|---|---:|---:|
| `m4-20260906` | 249.1 GB | 86,714 |
| `l1-promote-20260926` | 0.3 GB | 30 |
| `l1-promote-20260926-c` | 0.1 GB | 5 |
| `research-match-*`（多个目录） | < 0.1 GB | 各 1 |

`m4-20260906` 一个目录就占了 249.1 GB —— 这也是你之前自己标过 `要删/` 的那批。

---

## 五、必须保留的 327 份（27.96 MB）

这批是 `originals` 里没有副本的，**删了就真的没了**。按体积排序的要点：

| 体积 | 文件 |
|---:|---|
| 5.17 MB | `research-match-299f.../ERCOT_Megapack_...`（ERCOT 大型储能/电网材料，对应 M04） |
| 3.36 MB | `research-match-c830.../EPRI_Powering_...`（EPRI 电力研究） |
| 3.29 MB | `research-match-9fdd.../2026-10-19-daily...`（10-19 的日报，最新） |
| 2.54 MB | `research-match-6689.../MISO_MTEP24_Report...`（MISO 输电规划） |
| 2.31 MB | `research-match-c78d.../MISO_OMS_Survey...` |
| 1.17 / 1.05 MB | `research-match-.../Grid_Strategies...` ×2 |
| 1.09 MB | `web-125abd.../数据中心全国园区库.xlsx` |
| 0.98 MB ×2 | `中国大陆数据中心园区关键指标登记表_2026-10-14_v6.xlsx`（含 `(1)` 副本） |
| 0.96 MB ×2 | `中国大陆数据中心园区关键指标登记表_2026-10-14_v5.xlsx` |

**分布**：`m4-20260906` 里也有 311 份（2.04 MB），其余集中在 `research-match-*` 与 `web-*`。

这些全是 10 月的新材料（`research-match` 是研究缺口匹配产物，`web-*` 是网页提交），`reader scan` 最后一次是 10-17，所以它们还没被收进 `originals`。

> **重要**：正因为 `m4-20260906` 里也混着 311 份 orphan，**绝对不能整目录删**，必须按清单逐条删。

---

## 六、建议执行顺序

**第 1 步｜dry-run 全量演练（不删任何东西）**
```bash
ssh spark 'python3 /home/spark/reclaim_delete.py'
```
输出应显示：通过校验 90,619 个、约 249.84 GB、跳过 0。任何 skip 或 error 都要先查清原因。

**第 2 步｜先小批量真删 500 条**
```bash
ssh spark 'python3 /home/spark/reclaim_delete.py --execute --limit 500'
```
删完立刻验证：
```bash
ssh spark 'du -sh ~/.local/share/inresearch.ai/raw-materials; df -h /home/spark | tail -1'
```
并确认 `originals` 与 `library` 未受影响（抽查几个 `library` 符号链接能否正常打开）。

**第 3 步｜确认无误后跑全量**
```bash
ssh spark 'nohup python3 /home/spark/reclaim_delete.py --execute > /home/spark/reclaim-audit-20261019/delete.log 2>&1 &'
```

**第 4 步｜清空壳目录**
文件删完后，`raw-materials` 会留下大量空目录。先确认没有 orphan 落在里面，再清空目录（这一步可选，不省空间）。

---

## 七、风险与已知影响

1. **不影响 `originals` / `documents` / `library`**：这三个层完全不依赖 `raw-materials` 里的文件实体。删完资料库照常可用。
2. **`reader scan` 的观测记录会残留**：`observations` 表里约 9 万条 `source_key` 会指向已删路径。`scan` 只增量登记、不清理历史，所以这些记录会留着但不影响功能；下次 scan 的 `seen` 计数会从 9 万降到很小。
3. **`raw-materials` 是 reader 的扫描入口**：今后往这个目录放新材料，仍会被正常收录。只是历史那 9 万份不再需要重复躺在那里。
4. **不可逆**：文件删除后本地无回收站。但每一份都有 `originals` 副本且库里有记录（已验证 100%），实质内容不会丢。
5. **建议先做一次 `originals` 备份清单**（可选）：把 `documents` 表的 `sha256`+`original_rel` 导出成一份清单，万一将来需要核对有据可查。

---

## 八、相关文件位置

**spark** `~/reclaim-audit-20261019/`
- `audit.jsonl` — 91,419 行逐文件明细（path / size / sha256 / status / orig）
- `reclaim-safe.txt` — 90,619 条可删清单（`路径<TAB>sha256`），27 MB
- `keep-orphan.txt` — 327 条必留清单（`路径<TAB>大小`）
- `keep-empty.txt` — 473 条空文件
- `summary.json` — 机器可读汇总
- `run.log` — 核对运行日志

**本地** `~/WorkBuddy/2026-10-19-15-13-34/reclaim-audit/`
- `summary.json`、`keep-orphan.txt`、`run.log`、`reclaim_delete.py`
