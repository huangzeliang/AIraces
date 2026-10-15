"""B1 进度更新（F-201~205）：/ns/progress 页面 + 进度 / 健康度 / 时间线接口。

规则要点：
- 不得回退：进度小于上次值必须填「回退原因」（1005），管理者修正同理并写审计；
- 手动更新（source=manual）优先于日进度上卷：写 manual_override=true，可显式回退覆盖；
- 目标处于 pending_review / reviewed / archived 时只读，接口返回 1004；
- 证据上传失败或超限 → 进度不保存（1009），避免「有进度没证据」；
- 保存成功后异步触发巡检（不阻塞响应）。
"""

import os
import re
import threading
from datetime import datetime

from flask import request, send_from_directory

from backend.core import (
    DATA_LOCK,
    OBJECTIVE_STATUS,
    TASK_STATUS,
    UPLOAD_DIR,
    fail,
    f1,
    iso,
    next_id,
    read_store,
    write_store,
)
from backend.ns_common import (
    assignee_ids,
    audit,
    can_edit_progress,
    checkpoints_of,
    health_label,
    in_visible,
    is_manager,
    is_task_assignee,
    ns_api,
    ns_current_user,
    ns_render,
    ns_user,
    obj_by_id,
    objective_health,
    payload,
    require_ns_menu,
    rollup_objective,
    rollup_task,
    scope_objectives_check,
    task_by_id,
    task_health,
    user_name,
    visible_user_ids,
)

MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
ALLOWED_EVIDENCE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".txt", ".md", ".doc", ".docx", ".xlsx", ".csv"}


# ---------------------------------------------------------------------------
# 共享数据构建
# ---------------------------------------------------------------------------

def scope_objectives(store, user):
    scope_ids = visible_user_ids(store, user)
    rows = []
    for obj in store["objective"]:
        if scope_ids is not None and obj.get("owner_id") not in scope_ids:
            has_task = any(
                task.get("objective_id") == obj["objective_id"]
                and any(uid in scope_ids for uid in assignee_ids(task))
                for task in store["work_task"]
            )
            if not has_task:
                continue
        if obj.get("is_archived"):
            continue
        rows.append(obj)
    return rows


def scope_tasks(store, user):
    scope_ids = visible_user_ids(store, user)
    return [
        task for task in store["work_task"]
        if scope_ids is None or any(uid in scope_ids for uid in assignee_ids(task))
    ]


def task_card(store, task):
    metrics = task_health(store, task)
    obj = obj_by_id(store, task.get("objective_id"))
    return {
        "task_id": task["task_id"],
        "title": task["title"],
        "detail": task.get("detail", ""),
        "objective_id": task.get("objective_id", ""),
        "objective_title": obj["title"] if obj else "",
        "kr_id": task.get("kr_id", ""),
        "status": task.get("status"),
        "status_label": TASK_STATUS.get(task.get("status"), task.get("status")),
        "priority": task.get("priority", "中"),
        "progress": f1(task.get("progress")),
        "exec_rate": f1(task.get("exec_rate")),
        "manual_override": bool(task.get("manual_override")),
        "plan_hours": task.get("plan_hours"),
        "plan_start": task.get("plan_start", ""),
        "plan_end": task.get("plan_end", ""),
        "blocked_reason": task.get("blocked_reason", ""),
        "owners": [user_name(store, uid) for uid in assignee_ids(task)],
        "owner_ids": assignee_ids(task),
        "health": metrics["health"],
        "health_label": health_label(metrics["health"]),
        "time_progress": metrics["time_progress"],
        "delta": metrics["delta"],
        "source": task.get("source", "assigned"),
        "checkpoints": checkpoints_of(store, task),
        "version": int(task.get("version") or 1),
        "updated_at": task.get("updated_at", ""),
    }


def objective_card(store, obj):
    metrics = objective_health(store, obj)
    tasks = [t for t in store["work_task"] if t.get("objective_id") == obj["objective_id"]]
    return {
        "objective_id": obj["objective_id"],
        "title": obj["title"],
        "status": obj.get("status"),
        "status_label": OBJECTIVE_STATUS.get(obj.get("status"), obj.get("status")),
        "weight": obj.get("weight"),
        "progress": f1(obj.get("progress")),
        "attainment": obj.get("attainment"),
        "plan_start": obj.get("plan_start", ""),
        "plan_end": obj.get("plan_end", ""),
        "owner_name": user_name(store, obj.get("owner_id")),
        "health": metrics["health"],
        "health_label": health_label(metrics["health"]),
        "time_progress": metrics["time_progress"],
        "delta": metrics["delta"],
        "readonly": obj.get("status") in ("pending_review", "reviewed", "archived", "draft"),
        "task_count": len(tasks),
        "task_done": sum(1 for t in tasks if t.get("status") == "done"),
        "criteria": obj.get("success_criteria") or [],
    }


def timeline_for(store, objective_id="", task_id="", limit=60):
    task_ids = set()
    kr_ids = set()
    if task_id:
        task_ids.add(task_id)
        task = task_by_id(store, task_id)
        if task and task.get("objective_id"):
            objective_id = objective_id or task["objective_id"]
    if objective_id:
        obj = obj_by_id(store, objective_id)
        task_ids |= {t["task_id"] for t in store["work_task"] if t.get("objective_id") == objective_id}
        if obj:
            kr_ids = {k["kr_id"] for k in store["key_result"] if k.get("objective_id") == objective_id}
    rows = []
    for log in store["progress_log"]:
        target_type, target_id = log.get("target_type"), log.get("target_id")
        if target_type == "task" and target_id not in task_ids:
            continue
        if target_type == "objective" and target_id != objective_id:
            continue
        if target_type == "kr" and target_id not in kr_ids:
            continue
        if not task_ids and not objective_id:
            continue
        task = task_by_id(store, target_id) if target_type == "task" else None
        rows.append({
            "type": log.get("log_type") or ("milestone" if "里程碑" in (log.get("note") or "") else "progress"),
            "title": (task["title"] if task else (obj_by_id(store, target_id)["title"] if target_type == "objective" and obj_by_id(store, target_id) else target_id)),
            "value": log.get("progress"),
            "note": log.get("note", ""),
            "operator_name": user_name(store, log.get("operator_id")),
            "created_at": log.get("logged_at", ""),
            "source": log.get("source", ""),
        })
    for ch in store["change_request"]:
        if ch.get("objective_id") != objective_id or not objective_id:
            continue
        rows.append({
            "type": "change",
            "title": "变更 · %s（%s）" % (ch.get("change_type"), {"pending": "待审批", "approved": "已通过", "rejected": "已驳回"}.get(ch.get("status"), ch.get("status"))),
            "value": None,
            "note": "%s：%s" % (ch.get("reason_code"), ch.get("reason_text")),
            "operator_name": user_name(store, ch.get("applicant_id")),
            "created_at": ch.get("applied_at", ""),
            "source": "change",
        })
    rows.sort(key=lambda row: row["created_at"] or "", reverse=True)
    return rows[:limit]


def summary_for(store, user):
    objectives = scope_objectives(store, user)
    by_health = {"ahead": 0, "normal": 0, "lagging": 0}
    covered = 0
    ids = {obj["objective_id"] for obj in objectives}
    touched = {log.get("target_id") for log in store["progress_log"] if log.get("target_type") == "objective"}
    plan_touched = {
        task.get("objective_id") for task in store["work_task"]
        if task.get("objective_id") in ids and any(
            item.get("task_id") == task["task_id"] for item in store["daily_plan_item"]
        )
    }
    for obj in objectives:
        metrics = objective_health(store, obj)
        by_health[metrics["health"]] += 1
        if obj["objective_id"] in touched or obj["objective_id"] in plan_touched:
            covered += 1
    return {
        "by_health": by_health,
        "coverage_rate": f1(covered / len(objectives) * 100) if objectives else 0.0,
        "objective_count": len(objectives),
    }


# ---------------------------------------------------------------------------
# 写路径
# ---------------------------------------------------------------------------

def _validate_evidence_files(files):
    for file in files:
        name = file.filename or ""
        _, ext = os.path.splitext(name.lower())
        if ext not in ALLOWED_EVIDENCE_EXT:
            fail(1009, "证据文件「%s」类型不支持（支持图片 / PDF / 压缩包 / 文档）。" % name)
        try:
            file.stream.seek(0, os.SEEK_END)
            size = file.stream.tell()
            file.stream.seek(0)
        except OSError:
            size = 0
        if size > MAX_EVIDENCE_BYTES:
            fail(1009, "证据文件「%s」超过 10MB 上限，请压缩后重试。" % name)
        if size == 0:
            fail(1009, "证据文件「%s」为空文件。" % name)


def _save_evidence(store, files, target_type, target_id, user, note):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    try:
        for file in files:
            name = file.filename or "evidence"
            _, ext = os.path.splitext(name.lower())
            stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5_-]+", "_", os.path.splitext(name)[0])[:40] or "evidence"
            stored = "%s_%s%s" % (datetime.now().strftime("%Y%m%d%H%M%S"), stem, ext)
            path = UPLOAD_DIR / stored
            file.save(str(path))
            evidence_id = next_id(store, "evidence", "EV", 3)
            row = {
                "evidence_id": evidence_id,
                "target_type": target_type,
                "target_id": target_id,
                "file_name": name,
                "file_path": "data/uploads/%s" % stored,
                "note": note or "",
                "uploaded_by": user["user_id"],
                "uploaded_at": iso(),
                "ai_review_result": None,
            }
            store["evidence"].insert(0, row)
            saved.append({"evidence_id": evidence_id, "file_name": name, "path": path})
    except OSError:
        for item in saved:
            try:
                item["path"].unlink()
            except OSError:
                pass
        fail(1009, "证据写入失败，进度未保存，请重试。")
    return [item["evidence_id"] for item in saved]


def _trigger_scan():
    """更新即触发巡检：后台线程重算预警，不阻塞响应。"""
    try:
        def run():
            try:
                from backend import ns_scheduler
                ns_scheduler.scan_once()
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=run, daemon=True).start()
    except RuntimeError:
        pass


def register(app, services):
    @app.get("/ns/progress")
    @require_ns_menu("progress")
    def ns_progress():
        store = read_store()
        user = ns_user(store)
        objectives = [objective_card(store, obj) for obj in scope_objectives(store, user)]
        objectives.sort(key=lambda row: ({"lagging": 0, "normal": 1, "ahead": 2}.get(row["health"], 3), row["plan_end"]))
        tasks = [task_card(store, task) for task in scope_tasks(store, user) if task.get("status") != "cancelled"]
        tasks.sort(key=lambda row: ({"lagging": 0, "normal": 1, "ahead": 2}.get(row["health"], 3), row["plan_end"]))

        focus_objective = request.args.get("objective_id", "")
        focus_task = request.args.get("task_id", "")
        objective = None
        task = None
        if focus_task:
            raw = task_by_id(store, focus_task)
            if raw and (is_task_assignee(raw, user) or is_manager(store, user)):
                task = task_card(store, raw)
                focus_objective = focus_objective or task["objective_id"]
        if not task and focus_objective:
            raw_obj = obj_by_id(store, focus_objective)
            if raw_obj and raw_obj["objective_id"] in {o["objective_id"] for o in scope_objectives(store, user)}:
                objective = objective_card(store, raw_obj)
        if not task and not objective and objectives:
            objective = objectives[0]
            focus_objective = objective["objective_id"]

        objective_tasks = []
        if objective:
            objective_tasks = [
                task_card(store, t) for t in store["work_task"]
                if t.get("objective_id") == objective["objective_id"]
            ]
        timeline = timeline_for(store, focus_objective, focus_task) if (focus_objective or focus_task) else []
        evidence = [
            e for e in store["evidence"]
            if (focus_task and e.get("target_type") == "task" and e.get("target_id") == focus_task)
            or (focus_objective and e.get("target_type") == "objective" and e.get("target_id") == focus_objective)
        ]
        evidence.sort(key=lambda e: e.get("uploaded_at", ""), reverse=True)

        return ns_render(
            "ns/progress.html", "progress", "进度更新",
            "目标 / KR / 任务的进度与健康度；时间线合并进度、变更与里程碑。",
            store=store, user=user,
            objectives=objectives, tasks=tasks,
            objective=objective, task=task, objective_tasks=objective_tasks,
            timeline=timeline, evidence=evidence,
            summary=summary_for(store, user),
            can_edit=bool(task and can_edit_progress(store, user, task_by_id(store, task["task_id"]))),
        )

    @app.post("/api/ns/task/progress")
    @ns_api
    def ns_api_task_progress():
        body = payload()
        files = [f for f in request.files.getlist("evidence") if f and f.filename]
        _validate_evidence_files(files)

        task_id = (body.get("task_id") or "").strip()
        if not task_id:
            fail(1001, "缺少 task_id。")
        try:
            progress = float(body.get("progress"))
        except (TypeError, ValueError):
            fail(1002, "进度必须是 0~100 的数字。")
        if not 0 <= progress <= 100:
            fail(1002, "进度必须在 0~100 之间。")
        note = (body.get("note") or "").strip()
        back_reason = (body.get("back_reason") or "").strip()
        milestone = bool(body.get("milestone"))
        release = body.get("manual_override") is False
        client_version = body.get("version")

        with DATA_LOCK:
            store = read_store()
            user = ns_user(store)
            task = task_by_id(store, task_id)
            if not task:
                fail(1003, "任务不存在或已删除。")
            if not can_edit_progress(store, user, task):
                fail(1008, "只有任务执行人（或范围内的负责人）可以更新该任务进度。")
            obj = obj_by_id(store, task.get("objective_id"))
            if obj and obj.get("status") in ("pending_review", "reviewed", "archived"):
                fail(1004, "所属目标已进入验收 / 归档阶段，进度只读。")
            current = float(task.get("progress") or 0)
            if client_version not in (None, "") and int(task.get("version") or 1) != int(client_version):
                fail(1004, "该任务已被他人更新，请刷新后重试。")
            if progress < current and not back_reason:
                fail(1005, "进度不得回退：请填写回退原因。")
            if progress != current and not note and not back_reason:
                note = "进度由 %s%% 更新为 %s%%" % (f1(current), f1(progress))

            evidence_ids = _save_evidence(store, files, "task", task_id, user, note) if files else []
            before = {"progress": f1(current), "manual_override": bool(task.get("manual_override"))}
            task["progress"] = f1(progress)
            task["manual_override"] = False if release else True
            task["exec_rate"] = f1(progress) if task["manual_override"] else f1(task.get("exec_rate"))
            task["version"] = int(task.get("version") or 1) + 1
            if progress >= 100 and task.get("status") in ("todo", "doing"):
                task["status"] = "done"
            elif progress < 100 and task.get("status") == "done":
                task["status"] = "doing"
            rollup_task(store, task)
            if obj:
                rollup_objective(store, obj)

            log_id = next_id(store, "progress_log", "LOG", 4)
            store["progress_log"].insert(0, {
                "log_id": log_id,
                "target_type": "task",
                "target_id": task_id,
                "progress": f1(progress),
                "note": note or ("里程碑打卡" if milestone else ""),
                "log_type": "milestone" if milestone else "progress",
                "operator_id": user["user_id"],
                "logged_at": iso(),
                "source": "manual",
                "evidence_ids": evidence_ids,
                "back_reason": back_reason,
            })
            if is_manager(store, user) and not is_task_assignee(task, user):
                audit(store, user, "修正任务进度", "work_task", task_id, before,
                      {"progress": f1(progress), "reason": note or back_reason})
            write_store(store)

        _trigger_scan()
        metrics = task_health(store, task)
        return {
            "task_id": task_id,
            "progress": f1(task.get("progress")),
            "exec_rate": f1(task.get("exec_rate")),
            "health": metrics["health"],
            "health_label": health_label(metrics["health"]),
            "delta": metrics["delta"],
            "time_progress": metrics["time_progress"],
            "process_rate": f1(obj.get("progress")) if obj else None,
            "manual_override": bool(task.get("manual_override")),
            "version": task.get("version"),
            "evidence_ids": evidence_ids,
        }

    @app.get("/api/ns/task/health")
    @ns_api
    def ns_api_task_health():
        store = read_store()
        user = ns_user(store)
        objective_id = request.args.get("objective_id", "")
        task_id = request.args.get("task_id", "")
        with_timeline = request.args.get("with_timeline") in ("1", "true", "yes")
        data = {"summary": summary_for(store, user)}
        if task_id:
            task = task_by_id(store, task_id)
            if not task or not (is_task_assignee(task, user) or is_manager(store, user)):
                fail(1003, "任务不存在或超出数据范围。")
            metrics = task_health(store, task)
            data.update({
                "target_type": "task", "target_id": task_id, "title": task["title"],
                "time_progress": metrics["time_progress"], "done_progress": metrics["done_progress"],
                "delta": metrics["delta"], "health": metrics["health"], "health_label": health_label(metrics["health"]),
                "process_rate": f1((obj_by_id(store, task.get("objective_id")) or {}).get("progress")),
            })
        elif objective_id:
            obj = obj_by_id(store, objective_id)
            if not obj or objective_id not in {o["objective_id"] for o in scope_objectives(store, user)}:
                fail(1003, "目标不存在或超出数据范围。")
            metrics = objective_health(store, obj)
            data.update({
                "target_type": "objective", "target_id": objective_id, "title": obj["title"],
                "time_progress": metrics["time_progress"], "done_progress": metrics["done_progress"],
                "delta": metrics["delta"], "health": metrics["health"], "health_label": health_label(metrics["health"]),
                "process_rate": f1(obj.get("progress")),
            })
        else:
            objectives = scope_objectives(store, user)
            lagging = [objective_card(store, o) for o in objectives if objective_health(store, o)["health"] == "lagging"]
            data.update({"target_type": "summary", "health": "lagging" if lagging else "normal",
                         "lagging": lagging[:5]})
        if with_timeline and (task_id or objective_id):
            data["timeline"] = timeline_for(store, objective_id, task_id)
        return data

    @app.get("/api/ns/task/timeline")
    @ns_api
    def ns_api_task_timeline():
        store = read_store()
        user = ns_user(store)
        objective_id = request.args.get("objective_id", "")
        task_id = request.args.get("task_id", "")
        if task_id:
            task = task_by_id(store, task_id)
            if not task or not (is_task_assignee(task, user) or is_manager(store, user)):
                fail(1003, "任务不存在或超出数据范围。")
        elif objective_id:
            if objective_id not in {o["objective_id"] for o in scope_objectives(store, user)}:
                fail(1003, "目标不存在或超出数据范围。")
        else:
            fail(1001, "请至少提供 objective_id 或 task_id。")
        return {"timeline": timeline_for(store, objective_id, task_id)}

    @app.get("/api/ns/evidence/<evidence_id>/download")
    def ns_evidence_download(evidence_id):
        """证据下载：不走 ns_api（需要文件流），单独做登录 + 数据范围校验。"""
        store = read_store()
        user = ns_current_user(store)
        if user is None:
            fail(1008, "登录已失效，请重新登录。")
        row = next((e for e in store["evidence"] if e.get("evidence_id") == evidence_id), None)
        if not row:
            fail(1003, "证据不存在。")
        if row.get("target_type") == "task":
            task = task_by_id(store, row.get("target_id"))
            allowed = bool(task) and (
                is_task_assignee(task, user)
                or (is_manager(store, user) and any(in_visible(store, user, uid) for uid in assignee_ids(task)))
            )
        elif row.get("target_type") == "objective":
            allowed = False
            try:
                scope_objectives_check(store, user, row.get("target_id") or "")
                allowed = True
            except Exception:  # noqa: BLE001 — 越权统一按 1003 处理
                allowed = False
        else:
            allowed = True
        if not allowed:
            fail(1003, "证据不存在或超出数据范围。")
        stored_name = os.path.basename(row.get("file_path") or "")
        if not stored_name or not (UPLOAD_DIR / stored_name).exists():
            fail(1003, "证据文件未落盘（演示占位记录），可在进度页重新上传。")
        return send_from_directory(
            str(UPLOAD_DIR), stored_name,
            as_attachment=True, download_name=row.get("file_name") or stored_name,
        )
