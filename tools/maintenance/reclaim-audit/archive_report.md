# raw-materials 收尾：空文件清理 + orphan 归档

执行时间：2026-10-19

## 一、空文件清理（安全）
- raw-materials 中 473 个 0 字节文件，全部确认无 originals 副本、无研究价值。
- 按清单（empty-files.txt）逐条删除：removed=473，skipped=0。
- 删除后 raw-materials 剩 327 个非空文件。

## 二、orphan 分类与处置
327 份 orphan 经内容寻址核对，无一在 originals 有副本。按类型拆分：

| 类别 | 数量 | 处置 |
|---|---:|---|
| .tmp 临时文件 | 154 | 删除（垃圾） |
| .DS_Store（macOS 元数据） | 100 | 删除（垃圾） |
| `._*` macOS 资源分支（在 `__MACOSX/` 内，含 ._pdf/._doc/._dwg/._ctb） | 45 | 删除（垃圾） |
| .bak 备份 | 2 | 删除（垃圾） |
| **真文档（pdf/doc/docx/xlsx/html）** | **16** | **归档进 originals + documents** |

- 垃圾合计删除 **311 份**，0 错误。
- 关键确认：被判垃圾的 .dwg/.ctb 全是 `__MACOSX/` 里的 `._01目录.dwg`、`._其余打印样式.ctb` 这类 macOS 伴随文件，**真 CAD 图纸不在其中**，未误删。

## 三、归档方式（用项目自带逻辑，零手写 SQL）
1. 16 份真文档移到 `raw-materials/_archive_flat/`（扁平、sha256 前缀唯一名），使 reader scan 能 walk 到（嵌套的 m4-20260906 树 scan 走不进去）。
2. 循环 `reader scan`（stable_seconds=60 稳定门，需两轮 ≥60s 间隔）：经项目自带 `Reader._register` 把文件内容寻址写入 `originals/<hh>/<sha>/<名>`，并 INSERT `documents`。
3. 核验 16/16 均 originals 副本 + documents 记录齐全，删冗余的 `_archive_flat`，清 17,462 个空目录。

16 份已归档的高价值材料（节选）：
- ERCOT_Megapack_Data_Centers_2025.pdf、MISO_MTEP24_Report_Preview.pdf、MISO_OMS_Survey_Results_2024.pdf
- Grid_Strategies_National_Load_Growth_2023/2024.pdf、EPRI_Powering_Intelligence.pdf
- Supermicro_Liquid_Cooling.pdf、Georgia_PSC_Data_Center_Power_Terms_20250123.pdf
- 中国大陆数据中心园区关键指标登记表 v5/v6.xlsx、数据中心全国园区库.xlsx、dahai1.xlsx
- 2026-10-12 / 2026-10-19 daily-wechat.html（每日简报）

## 四、最终状态
| 项 | 值 |
|---|---|
| raw-materials 文件数 | **0**（完全清空） |
| originals | 含 16 份新归档，副本完好 |
| documents 总数 | 44,830（+16 归档） |
| library 符号链接 | 67,748，断链 0 |
| 磁盘已用 | 399G（剩 3.1T） |

注：16 份真文档的 library 分类符号链接需等 reader 后端（当前 degraded）恢复后由 classify 阶段生成；文件本体与 documents 记录已落地，数据零风险。
