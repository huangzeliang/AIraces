"""C 线 · 周期与字典（C6）。

对应需求：AC-33 字典引用完整性 · 周期与验收口径对齐
归属文件：backend/cycle_dict.py · templates/pages/cycle_dict.html · css/pages/cycle_dict.css

要点：
- 周期是验收与对比分析的时间基准，必须先于目标存在；
- 被目标引用的周期不可删除，只能改状态（避免历史数据失去归属）；
- 字典项在此集中展示「被引用即不可删」的口径；评分档位直接来自 c_metrics，保证与验收定级一致。
"""

import re
from datetime import datetime
from importlib import import_module

from flask import flash, redirect, render_template, request, url_for

c_metrics = import_module("backend.c_metrics")

CYCLE_TYPES = ("季度", "月度")
CYCLE_STATUSES = ("未开始", "进行中", "已结束")

DICT_DEFS = (
    ("岗位", ("研发", "产品", "运营", "职能")),
    ("能力标签", ("业务理解", "数据洞察", "协作推动", "交付质量")),
    ("差异原因", ("目标偏高", "资源不足", "依赖未打通", "外部变化", "执行偏差")),
    ("通知渠道", ("页面弹窗", "桌面通知", "企业微信", "钉钉")),
    ("评分档位", ("卓越", "达标", "待改进", "未达标")),
)


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def cycle_usage(store):
    usage = {}
    for objective in store.get("objectives", []):
        cycle_id = objective.get("cycle_id")
        if cycle_id:
            usage[cycle_id] = usage.get(cycle_id, 0) + 1
    return usage


def next_cycle_id(store, cycle_type, name):
    """按类型生成周期编号：季度 CY-YYYYQn，月度 CY-YYYYMM。"""
    existing = {item.get("cycle_id") for item in store.get("cycles", [])}
    today = datetime.now()
    if cycle_type == "月度":
        match = re.search(r"(\d{4})\D*(\d{1,2})", name or "")
        year = match.group(1) if match else str(today.year)
        month = int(match.group(2)) if match else today.month
        candidate = "CY-%s%02d" % (year, month)
    else:
        match = re.search(r"(\d{4})\D*Q?([1-4])", name or "", re.IGNORECASE)
        year = match.group(1) if match else str(today.year)
        quarter = int(match.group(2)) if match else (today.month - 1) // 3 + 1
        candidate = "CY-%sQ%d" % (year, quarter)
    suffix = 1
    base = candidate
    while candidate in existing:
        suffix += 1
        candidate = "%s-%d" % (base, suffix)
    return candidate


def payload(store):
    usage = cycle_usage(store)
    cycles = []
    for cycle in sorted(store.get("cycles", []), key=lambda item: item.get("start_at") or "", reverse=True):
        row = dict(cycle)
        row["objective_count"] = usage.get(cycle.get("cycle_id"), 0)
        row["locked"] = row["objective_count"] > 0
        cycles.append(row)
    return {
        "cycles": cycles,
        "cycle_types": CYCLE_TYPES,
        "cycle_statuses": CYCLE_STATUSES,
        "dicts": [{"name": name, "items": list(items)} for name, items in DICT_DEFS],
        "grade_bands": c_metrics.grade_bands_text(),
        "diff_limit": c_metrics.SELF_FINAL_DIFF_LIMIT,
        "metric_labels": c_metrics.METRIC_LABELS,
        "metric_formulas": c_metrics.METRIC_FORMULA,
    }


def register(app, services):
    read_store = services.read_store
    write_store = services.write_store
    lock = services.store_lock

    @app.get("/settings/dict")
    def cycle_dict():
        return render_template(
            "pages/cycle_dict.html",
            active_page="dict",
            page_name="周期与字典",
            **payload(read_store())
        )

    @app.post("/settings/cycle/save")
    def cycle_save():
        cycle_id = (request.form.get("cycle_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        cycle_type = (request.form.get("type") or "季度").strip()
        status = (request.form.get("status") or "未开始").strip()
        start_at = (request.form.get("start_at") or "").strip()
        end_at = (request.form.get("end_at") or "").strip()
        note = (request.form.get("note") or "").strip()

        if not name:
            flash("请填写周期名称。", "error")
            return redirect(url_for("cycle_dict"))
        if cycle_type not in CYCLE_TYPES or status not in CYCLE_STATUSES:
            flash("周期类型或状态无效。", "error")
            return redirect(url_for("cycle_dict"))
        if start_at and end_at and start_at > end_at:
            flash("开始时间不能晚于结束时间。", "error")
            return redirect(url_for("cycle_dict"))

        with lock:
            store = read_store()
            cycles = store.setdefault("cycles", [])
            record = next((item for item in cycles if item.get("cycle_id") == cycle_id), None)
            if record is None:
                cycle_id = next_cycle_id(store, cycle_type, name)
                record = {"cycle_id": cycle_id, "created_at": now_iso()}
                cycles.append(record)
            record.update({
                "name": name,
                "type": cycle_type,
                "status": status,
                "start_at": start_at,
                "end_at": end_at,
                "note": note,
            })
            write_store(store)

        flash("周期已保存：%s" % name, "ok")
        return redirect(url_for("cycle_dict"))

    @app.post("/settings/cycle/delete")
    def cycle_delete():
        cycle_id = (request.form.get("cycle_id") or "").strip()
        with lock:
            store = read_store()
            usage = cycle_usage(store)
            if usage.get(cycle_id):
                flash("该周期已被 %d 个目标引用，不能删除；如需停用请改状态为「已结束」。"
                      % usage[cycle_id], "error")
                return redirect(url_for("cycle_dict"))
            cycles = store.get("cycles", [])
            kept = [item for item in cycles if item.get("cycle_id") != cycle_id]
            if len(kept) == len(cycles):
                flash("周期不存在。", "error")
                return redirect(url_for("cycle_dict"))
            store["cycles"] = kept
            write_store(store)

        flash("周期已删除。", "ok")
        return redirect(url_for("cycle_dict"))
