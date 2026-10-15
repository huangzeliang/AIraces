"""C 线 · 数据管理（C2）：本地持久化、导入导出、合规脱敏与异常数据处置。

对应需求：REQ-018 / 020 / 023 / 024（F-901/902/904/906 · F-1001~1003）
归属文件：backend/data_io.py · templates/pages/data_mgmt.html · css|js/pages/data_mgmt.*

落库闭环沿用全局约定：读取 → 校验 → 加锁写 → 刷新展示。
导入为「事务性」：先整体解析与校验，合法行一次性写入，异常行进异常数据区，中途失败不落半成品。
"""

import csv
import io
import json
import re
from datetime import datetime

from flask import Response, flash, jsonify, redirect, render_template, request, url_for

PRIORITY_ORDER = ("高", "中", "低")
STATUS_ORDER = ("待安排", "待执行", "进行中", "已完成", "已延期", "已取消")

# 列表分页：软需 1.4「列表页 >50 条分页」，此处统一每页 20 条
PAGE_SIZE = 20

# 导出列 = 数据字典（软需说明书 1.3），顺序即表格与 CSV 顺序
EXPORT_COLUMNS = [
    ("任务ID", "task_id"),
    ("所属计划ID", "plan_id"),
    ("任务标题", "title"),
    ("优先级", "priority"),
    ("预计耗时（分钟）", "estimate_minutes"),
    ("截止时间", "due_at"),
    ("计划完成时间点", "planned_finish_at"),
    ("状态", "status"),
    ("进度", "progress"),
    ("延期次数", "delay_count"),
    ("完成时间", "completed_at"),
]

IMPORT_COLUMNS = ["title", "priority", "estimate_minutes", "due_at", "detail"]

# 脱敏规则（Q-12 待确认，先按下列示例规则实现）
_MASK_RULES = (
    (re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)"), r"\1****\2"),
    (re.compile(r"(?<!\d)(\d{6})\d{8}(\d{4})(?!\d)"), r"\1********\2"),
    (re.compile(r"([\w.+-])[\w.+-]*(@[\w-]+\.[\w.-]+)"), r"\1***\2"),
)


def mask_text(value):
    """对文本做敏感信息脱敏：手机号 / 身份证 / 邮箱（保留可识别首尾字符）。"""
    text = "" if value is None else str(value)
    for pattern, replacement in _MASK_RULES:
        text = pattern.sub(replacement, text)
    return text


def _parse_datetime(raw):
    """兼容 ISO8601 与常见中文录入格式。"""
    if not raw:
        return None
    cleaned = str(raw).strip().replace("/", "-")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _decode(raw):
    """按 UTF-8 / GBK 依次尝试解码，返回 (text, encoding)。"""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文件编码，请另存为 UTF-8 或 GBK 后重试。")


def _parse_upload(file_storage):
    """解析上传的 CSV，返回 (表头, 行列表, 编码)。"""
    text, encoding = _decode(file_storage.read())
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or []) if h and h.strip()]
    if not headers:
        raise ValueError("文件缺少表头，请先下载模板对照填写。")
    rows = []
    for index, row in enumerate(reader, start=2):
        cleaned = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if not any(cleaned.values()):
            continue
        cleaned["__line__"] = index
        rows.append(cleaned)
    return headers, rows, encoding


def _validate_row(row):
    """校验单行，返回错误信息列表（空列表 = 合法）。"""
    errors = []
    title = row.get("title", "")
    if not title:
        errors.append("缺少必填字段：任务标题（title）")
    elif len(title) > 80:
        errors.append("任务标题超过 80 字符")

    priority = row.get("priority") or "中"
    if priority not in PRIORITY_ORDER:
        errors.append("优先级取值非法：%s（允许值：高 / 中 / 低）" % priority)

    estimate_raw = row.get("estimate_minutes") or ""
    if not estimate_raw:
        errors.append("缺少必填字段：预计耗时（estimate_minutes）")
    else:
        try:
            if int(float(estimate_raw)) <= 0:
                errors.append("预计耗时必须大于 0，实际为 %s" % estimate_raw)
        except ValueError:
            errors.append("预计耗时必须是整数，实际为「%s」" % estimate_raw)

    if not _parse_datetime(row.get("due_at")):
        errors.append("缺少或无法解析：截止时间（due_at）")
    return errors


def _next_task_id(tasks):
    used = {task.get("task_id") for task in tasks}
    index = 1
    while "T%03d" % index in used:
        index += 1
    return "T%03d" % index


def _build_task(row, task_id, now):
    due = _parse_datetime(row.get("due_at"))
    estimate = int(float(row.get("estimate_minutes") or 60))
    return {
        "task_id": task_id,
        "plan_id": row.get("plan_id", ""),
        "todo_id": row.get("todo_id", ""),
        "parent_task_id": "",
        "title": row.get("title", ""),
        "detail": row.get("detail", ""),
        "priority": row.get("priority") or "中",
        "estimate_minutes": estimate,
        "due_at": due.replace(microsecond=0).isoformat(),
        "scheduled_start_at": "",
        "planned_finish_at": "",
        "status": "待安排",
        "progress": 0,
        "depends_on": [],
        "delay_count": 0,
        "delay_reason": "",
        "completed_at": "",
        "is_demo": False,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def _visible_tasks(store, args, masked):
    """按查询参数过滤任务，并可选择是否脱敏。"""
    keyword = (args.get("q") or "").strip().lower()
    status = args.get("status") or "全部"
    priority = args.get("priority") or "全部"

    rows = []
    for task in store.get("tasks", []):
        if status != "全部" and task.get("status") != status:
            continue
        if priority != "全部" and task.get("priority") != priority:
            continue
        haystack = "%s %s" % (task.get("title", ""), task.get("detail", ""))
        if keyword and keyword not in haystack.lower():
            continue
        rows.append(_mask_task(task) if masked else dict(task))
    rows.sort(key=lambda item: (item.get("due_at") or "", item.get("task_id") or ""))
    return rows


def _mask_task(task):
    masked = dict(task)
    for field in ("title", "detail", "delay_reason"):
        if masked.get(field):
            masked[field] = mask_text(masked[field])
    return masked


def _csv_text(rows, columns):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for label, _ in columns])
    for row in rows:
        writer.writerow([row.get(key, "") for _, key in columns])
    return "\ufeff" + buffer.getvalue()  # BOM：让 Excel 正确识别 UTF-8


def register(app, services):
    read_store = services.read_store
    write_store = services.write_store
    read_settings = services.read_settings
    store_lock = services.store_lock

    @app.get("/data")
    def data_manage():
        store = read_store()
        masked = request.args.get("mask") == "1"
        rows = _visible_tasks(store, request.args, masked)

        # 分页：越界页码自动收敛到有效区间；导出仍取「当前筛选」全量，不受分页影响
        total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        try:
            page = int(request.args.get("page", 1))
        except (TypeError, ValueError):
            page = 1
        page = min(max(page, 1), total_pages)
        start = (page - 1) * PAGE_SIZE

        return render_template(
            "pages/data_mgmt.html",
            active_page="data",
            page_name="数据管理",
            settings=read_settings(),
            tasks=rows[start:start + PAGE_SIZE],
            total=len(store.get("tasks", [])),
            filtered_total=len(rows),
            page=page,
            total_pages=total_pages,
            page_size=PAGE_SIZE,
            exceptions=store.get("exceptions", []),
            export_columns=EXPORT_COLUMNS,
            import_columns=IMPORT_COLUMNS,
            status_options=STATUS_ORDER,
            priority_options=PRIORITY_ORDER,
            masked=masked,
            keyword=request.args.get("q", "").strip(),
            status_filter=request.args.get("status", "全部"),
            priority_filter=request.args.get("priority", "全部"),
        )

    @app.get("/data/template")
    def import_template():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(IMPORT_COLUMNS)
        writer.writerow(["整理季度复盘材料", "高", "90", datetime.now().strftime("%Y-%m-%d 18:00"), "汇总三个项目进展"])
        return Response(
            "\ufeff" + buffer.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=template_import.csv"},
        )

    @app.get("/data/export")
    def export_data():
        store = read_store()
        masked = request.args.get("mask") == "1"
        rows = _visible_tasks(store, request.args, masked)
        stamp = datetime.now().strftime("%Y%m%d-%H%M")

        if request.args.get("format", "csv") == "json":
            payload = json.dumps(
                {"exported_at": datetime.now().replace(microsecond=0).isoformat(),
                 "masked": masked, "count": len(rows), "columns": [k for _, k in EXPORT_COLUMNS], "tasks": rows},
                ensure_ascii=False, indent=2,
            )
            return Response(
                payload, mimetype="application/json",
                headers={"Content-Disposition": "attachment; filename=data_%s.json" % stamp},
            )

        return Response(
            _csv_text(rows, EXPORT_COLUMNS), mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=data_%s.csv" % stamp},
        )

    @app.post("/data/import")
    def import_data():
        upload = request.files.get("import_file")
        dry_run = request.form.get("dry_run", "true") == "true"
        if upload is None or not upload.filename:
            return jsonify({"ok": False, "message": "请先选择要导入的 CSV 文件。"}), 400
        if not upload.filename.lower().endswith(".csv"):
            return jsonify({"ok": False, "message": "仅支持 CSV 文件，请另存为 .csv 后重试。"}), 400

        try:
            headers, rows, encoding = _parse_upload(upload)
        except ValueError as error:
            return jsonify({"ok": False, "message": str(error)}), 400

        missing = [column for column in ("title", "due_at") if column not in headers]
        if missing:
            return jsonify({
                "ok": False,
                "message": "表头缺少必填列：%s。请对照模板调整。" % "、".join(missing),
            }), 400

        valid, invalid = [], []
        for row in rows:
            errors = _validate_row(row)
            if errors:
                invalid.append({
                    "line": row["__line__"],
                    "raw": {k: v for k, v in row.items() if k != "__line__"},
                    "reason": "；".join(errors),
                })
            else:
                valid.append(row)

        preview = {
            "ok": True,
            "dry_run": dry_run,
            "filename": upload.filename,
            "encoding": encoding,
            "headers": headers,
            "total": len(rows),
            "valid_count": len(valid),
            "invalid_count": len(invalid),
            "invalid": invalid[:50],
            "mapped": [{"source": column, "target": column} for column in headers],
        }

        if dry_run:
            return jsonify(preview)

        now = datetime.now().replace(microsecond=0)
        with store_lock:
            store = read_store()
            tasks = store.setdefault("tasks", [])
            added = []
            for row in valid:
                task = _build_task(row, _next_task_id(tasks + added), now)
                added.append(task)
            tasks.extend(added)

            exceptions = store.setdefault("exceptions", [])
            base = len(exceptions)
            for offset, item in enumerate(invalid, start=1):
                exceptions.append({
                    "exception_id": "E%03d" % (base + offset),
                    "source": upload.filename,
                    "raw": item["raw"],
                    "reason": item["reason"],
                    "status": "待处置",
                    "created_at": now.isoformat(),
                })
            write_store(store)

        preview.update({"dry_run": False, "imported": len(added), "exceptions_added": len(invalid)})
        return jsonify(preview)

    @app.post("/data/exception/<exception_id>/resolve")
    def resolve_exception(exception_id):
        """异常处置：补录 / 修正（入库）· 忽略（标记）· 删除（丢弃该行）。"""
        action = request.form.get("action", "ignore")
        with store_lock:
            store = read_store()
            exceptions = store.setdefault("exceptions", [])
            item = next((e for e in exceptions if e.get("exception_id") == exception_id), None)
            if item is None:
                flash("该异常项已不存在，可能已被处理。", "error")
                return redirect(url_for("data_manage"))

            if action == "delete":
                exceptions.remove(item)
                write_store(store)
                flash("已删除该条异常数据。", "success")
                return redirect(url_for("data_manage"))

            if action == "ignore":
                item["status"] = "已忽略"
                write_store(store)
                flash("已忽略该条异常数据。", "success")
                return redirect(url_for("data_manage"))

            # 补录 / 修正：以表单值为准，校验通过后入任务库
            row = dict(item.get("raw") or {})
            for column in IMPORT_COLUMNS:
                value = request.form.get(column)
                if value is not None:
                    row[column] = value.strip()
            errors = _validate_row(row)
            if errors:
                flash("补录未通过校验：" + "；".join(errors), "error")
                return redirect(url_for("data_manage"))

            now = datetime.now().replace(microsecond=0)
            tasks = store.setdefault("tasks", [])
            tasks.append(_build_task(row, _next_task_id(tasks), now))
            item["status"] = "已处置"
            write_store(store)

        flash("异常数据已补录入库。", "success")
        return redirect(url_for("data_manage"))
