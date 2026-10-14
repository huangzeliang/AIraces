"""B 线 · 反馈与撤销核心（B4 反馈与二次确认）。

对应需求：REQ-010/021（F-501/502/905）
归属文件：backend/feedback.py（被 backend/task_ops.py 复用）

约定：
- 每次会改变任务状态的反馈（完成 / 延期 / 取消）先给任务拍「前态快照」再落库，
  撤销时按快照恢复，保证 AC-10「反馈落库后可以撤销」；
- 校验失败不落任何数据（由调用方在同一个 store_lock 内先校验后调用）；
- 状态日志写入 store["logs"]（有界队列），删除等关键操作可追溯。
"""

from importlib import import_module

llm = import_module("backend.llm")

SNAPSHOT_FIELDS = (
    "status", "progress", "completed_at", "delay_count", "delay_reason",
    "due_at", "scheduled_start_at", "planned_finish_at", "updated_at",
)

FEEDBACK_TYPES = ("完成", "延期", "取消")
MAX_COMMENT = 200
LOG_LIMIT = 400


def _next_seq_id(items, key, prefix, width=3):
    used = {item.get(key) for item in items}
    index = 1
    while ("%s%0*d" % (prefix, width, index)) in used:
        index += 1
    return "%s%0*d" % (prefix, width, index)


def snapshot(task):
    return {field: task.get(field, "") for field in SNAPSHOT_FIELDS}


def restore(task, snap):
    for field in SNAPSHOT_FIELDS:
        if field in snap:
            task[field] = snap[field]


def record(store, task, feedback_type, comment, now):
    """记录一条反馈（携带任务前态快照），返回反馈条目。"""
    feedback = {
        "feedback_id": _next_seq_id(store.setdefault("feedbacks", []), "feedback_id", "F"),
        "task_id": task.get("task_id"),
        "feedback_type": feedback_type,
        "comment": (comment or "").strip()[:MAX_COMMENT],
        "created_at": llm.iso(now),
        "prev_state": snapshot(task),
    }
    store["feedbacks"].append(feedback)
    return feedback


def write_log(store, task_id, action, detail, now):
    """任务状态日志：终态变更与撤销都留痕，便于审计与排查。"""
    logs = store.setdefault("logs", [])
    logs.append({
        "log_id": _next_seq_id(logs, "log_id", "L"),
        "task_id": task_id,
        "action": action,
        "detail": detail or "",
        "created_at": llm.iso(now),
    })
    del logs[:-LOG_LIMIT]


def find(store, feedback_id):
    return next((f for f in store.get("feedbacks", []) if f.get("feedback_id") == feedback_id), None)


def undo(store, feedback, now):
    """按前态快照恢复任务，并从反馈列表移除该条记录；返回恢复后的状态。"""
    snap = feedback.get("prev_state") or {}
    restore_task_state(store, feedback.get("task_id"), snap, now)
    store["feedbacks"] = [f for f in store.get("feedbacks", []) if f is not feedback]
    return snap.get("status") or ""


def restore_task_state(store, task_id, snap, now):
    task = next((t for t in store.get("tasks", []) if t.get("task_id") == task_id), None)
    if task is None:
        return None
    restore(task, snap)
    task["updated_at"] = llm.iso(now)
    return task


def apply_complete(store, task, now, comment=""):
    """完成：反馈 + 状态置为已完成、进度 100%、记录完成时间。"""
    feedback = record(store, task, "完成", comment, now)
    task["status"] = "已完成"
    task["progress"] = 100
    task["completed_at"] = llm.iso(now)
    task["updated_at"] = llm.iso(now)
    write_log(store, task.get("task_id"), "完成", comment or "标记为已完成", now)
    return feedback


def apply_complete_by_progress(store, task, now):
    """进度拖到 100 的自动完成，与手动完成共用同一快照与撤销机制。"""
    return apply_complete(store, task, now, comment="进度达到 100% 自动完成")


def apply_delay(store, task, reason, now):
    """延期：原因必填（调用方先校验），延期次数 +1，写入最近延期原因。"""
    reason = (reason or "").strip()
    feedback = record(store, task, "延期", reason, now)
    task["status"] = "已延期"
    task["delay_count"] = int(task.get("delay_count") or 0) + 1
    task["delay_reason"] = reason[:MAX_COMMENT]
    task["updated_at"] = llm.iso(now)
    write_log(store, task.get("task_id"), "延期", "延期原因：%s" % reason, now)
    return feedback


def apply_cancel(store, task, comment, now):
    """取消：反馈 + 状态置为已取消（原因选填）。"""
    feedback = record(store, task, "取消", comment, now)
    task["status"] = "已取消"
    task["updated_at"] = llm.iso(now)
    write_log(store, task.get("task_id"), "取消", (comment or "").strip() or "取消任务", now)
    return feedback
