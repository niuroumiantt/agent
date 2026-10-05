"""Sequential analysis of every extracted block, with persisted coverage."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import asdict

from .extract import extract
from .files import CatalogError, FileCatalog
from .model import ModelError

CHUNK_CHARACTERS = 8_000
CHUNK_BLOCKS = 200
DIGEST_CHARACTERS = 2_000


def chunks(blocks: list[dict]) -> list[list[dict]]:
    """Split without dropping text, preserving original source locators."""
    result, current = [], []
    characters = 0
    for block in blocks:
        offset = 0
        while offset < len(block["text"]):
            if characters == CHUNK_CHARACTERS or len(current) == CHUNK_BLOCKS:
                result.append(current)
                current, characters = [], 0
            text = block["text"][offset:offset + CHUNK_CHARACTERS - characters]
            current.append({"locator": block["locator"], "text": text})
            characters += len(text)
            offset += len(text)
    if current:
        result.append(current)
    return result


def run_batch(settings, instruction: str, catalog: FileCatalog, file_ids: list[str],
              analyzer, cancelled: threading.Event, on_progress):
    sources = [{
        "source_id": f"S{index}", "file": catalog.get(file_id), "sha256": "",
        "status": "pending", "warnings": [], "extractor": "none",
        "analysis_status": "pending", "segments_total": 0, "segments_done": 0,
    } for index, file_id in enumerate(file_ids, 1)]
    result = {
        "summary": "分析进行中；请查看每份文件的读取范围。",
        "documents": [], "facts": [], "recommendations": [], "warnings": [],
        "model": settings.model, "provider": settings.provider, "sources": sources,
    }
    progress = {"files_total": len(sources), "files_done": 0, "files_analyzed": 0,
                "files_skipped": 0, "model_calls": 0, "phase": "reading",
                "current_file": "", "current_segment": 0, "segments_total": 0}
    summaries = []
    last_error = None

    def emit():
        progress["files_done"] = sum(
            source["analysis_status"] not in {"pending", "analyzing", "not_processed"}
            for source in sources
        )
        progress["files_analyzed"] = sum(source["segments_done"] > 0 for source in sources)
        progress["files_skipped"] = sum(
            source["analysis_status"] in {"skipped", "failed"} for source in sources
        )
        on_progress(dict(progress), result if summaries else None)

    def merge(reply, source, segment):
        summaries.append(
            f'{source["source_id"]} · {source["file"]["relative_path"]} '
            f'· 分段 {segment}/{source["segments_total"]}\n{reply["summary"]}'
        )
        notes = reply["documents"] or [{
            "source_id": source["source_id"], "category": "分段归纳", "summary": reply["summary"],
        }]
        result["documents"].extend({**note, "segment": segment} for note in notes)
        result["facts"].extend(reply["facts"])
        result["recommendations"].extend(reply["recommendations"])
        result["warnings"].extend(reply["warnings"])

    emit()
    for file_id, source in zip(file_ids, sources, strict=True):
        if cancelled.is_set():
            break
        progress.update(current_file=source["file"]["relative_path"], current_segment=0,
                        segments_total=0, phase="reading")
        emit()
        try:
            _, content = catalog.read(file_id)
            parsed = asdict(extract(source["file"]["name"], content))
            source.update(sha256=hashlib.sha256(content).hexdigest(),
                          status=parsed["status"], warnings=parsed["warnings"],
                          extractor=parsed["extractor"])
        except CatalogError as exc:
            source.update(status="blocked" if str(exc) == "sensitive_file_blocked" else "failed",
                          warnings=[str(exc)], analysis_status="skipped")
            emit()
            continue
        if not parsed["blocks"]:
            source["analysis_status"] = "skipped"
            if source["status"] == "ok":
                source["status"] = "empty"
            emit()
            continue
        segments = chunks(parsed["blocks"])
        source.update(analysis_status="analyzing", segments_total=len(segments))
        for number, blocks in enumerate(segments, 1):
            if cancelled.is_set():
                break
            progress.update(current_segment=number, segments_total=len(segments), phase="analyzing")
            emit()
            model_source = {**source, "blocks": blocks, "segment_index": number}
            task_instruction = (
                instruction + f"\n这是该文件第 {number}/{len(segments)} 段。"
                "只分析本段内容；不要将本段缺失信息判定为整个文件缺失。"
            )
            progress["model_calls"] += 1
            try:
                reply = analyzer(settings, task_instruction, [model_source])
            except ModelError as exc:
                last_error = str(exc)
                source["analysis_status"] = "failed"
                source["warnings"].append("model_analysis_failed")
                break
            merge(reply, source, number)
            source["segments_done"] = number
            emit()
        if source["analysis_status"] != "failed":
            source["analysis_status"] = (
                ("completed" if source["status"] == "ok" else "partial")
                if source["segments_done"] == len(segments) else "partial"
            )
        emit()
        if last_error or cancelled.is_set():
            break

    # Reduce every segment summary in bounded groups. New citations from derived
    # notes are deliberately discarded; original citations above remain intact.
    if summaries and not cancelled.is_set() and not last_error:
        digest = "\n\n".join(summaries)
        if len(summaries) == 1:
            result["summary"] = digest
        else:
            progress["phase"] = "summarizing"
            result["warnings"].append("summary_based_on_segment_notes")
            while True:
                groups = [digest[offset:offset + CHUNK_CHARACTERS]
                          for offset in range(0, len(digest), CHUNK_CHARACTERS)]
                reduced = []
                for group in groups:
                    if cancelled.is_set():
                        break
                    progress.update(current_file="综合归纳", current_segment=0, segments_total=0)
                    emit()
                    progress["model_calls"] += 1
                    try:
                        reply = analyzer(settings, instruction + (
                            "\n以下是已分析文件各段的派生摘要，含来源编号。"
                            "综合归纳并比较与任务有关的信息，保留编号和不确定性。"
                            "summary 最多 2000 字；documents 和 facts 必须为空。"
                            "派生摘要不能充当原文件证据。"
                        ), [{"source_id": "S0", "file": {"name": "分段摘要"},
                             "status": "derived", "blocks": [{"locator": "notes", "text": group}]}])
                    except ModelError as exc:
                        last_error = str(exc)
                        result["warnings"].append("summary_generation_failed")
                        break
                    summary = reply["summary"]
                    if len(summary) > DIGEST_CHARACTERS:
                        result["warnings"].append("summary_length_limit_reached")
                    reduced.append(summary[:DIGEST_CHARACTERS])
                    if len(groups) == 1:
                        result["recommendations"].extend(reply["recommendations"])
                if cancelled.is_set() or last_error:
                    break
                digest = "\n\n".join(reduced)
                if len(groups) == 1:
                    result["summary"] = digest
                    break

    if cancelled.is_set():
        status = "cancelled"
    elif last_error:
        status = "partial" if summaries else "failed"
    elif all(source["analysis_status"] == "completed" for source in sources):
        status = "completed"
    else:
        status = "partial"
    for source in sources:
        if source["analysis_status"] in {"pending", "analyzing"}:
            source["analysis_status"] = "not_processed"
            source["warnings"].append("not_processed")
        if source["segments_done"] < source["segments_total"]:
            source["warnings"].append("segments_incomplete")
    if not summaries:
        result["summary"] = "未完成模型分析；请查看文件读取状态和任务提示。"
    elif status != "completed" and result["summary"].startswith("分析进行中"):
        result["summary"] = "任务未完整完成；已处理内容见逐文件、分段归纳。"
    result["warnings"].extend(
        f'{source["source_id"]}：{warning}' for source in sources for warning in source["warnings"]
    )
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    result["recommendations"] = list(dict.fromkeys(result["recommendations"]))
    progress["phase"] = "finished"
    emit()
    result["coverage"] = dict(progress)
    return status, result if status != "failed" else None, dict(progress), last_error
