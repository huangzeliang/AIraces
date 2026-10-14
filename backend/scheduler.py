"""B 线 · 后台定时巡检（B3 定时任务与提醒生成）。

对应需求：REQ-008/019（F-401/402/903）
归属文件：backend/scheduler.py（线程在 register 时启动，daemon 随进程退出）

巡检规则（与提醒中心页面「依据」口径一致）：
- 每 N 分钟扫描一次（N = 系统设置 reminder_interval_minutes，改设置下一轮生效）；
- 命中四类风险生成提醒（默认未读、页面弹窗渠道）：
  已逾期 / 24 小时内临期 / 依赖未满足 / 24 小时内开始（待执行）；
- 同一任务同一轮只生成一条（按 逾期 > 临期 > 依赖 > 待执行 取最高优先级）；
- 跨轮去重：同任务同类型若已有未读提醒、或 24 小时内已生成过，不再重复生成；
- 单轮生成总量上限 MAX_NEW_PER_SCAN，避免极端数据刷屏；
- 线程异常全部吞掉并在下一轮自愈，绝不影响 Web 请求；与请求线程共用同一把 DATA_LOCK。
"""

import os
import threading
import time
from datetime import datetime, timedelta
from importlib import import_module

from flask import flash, jsonify, redirect, request, url_for

llm = import_module("backend.llm")

VISIBLE_STATUSES = ("待安排", "待执行", "进行中", "已延期")
CLOSED_STATUSES = ("已完成", "已取消")

FIRST_SCAN_DELAY_SECONDS = 4
MIN_INTERVAL_MINUTES = 1
DEFAULT_INTERVAL_MINUTES = 240
DUE_SOON_HOURS = 24
READY_HOURS = 24
MAX_NEW_PER_SCAN = 12
DEDUPE_HOURS = 24
SCAN_STATE_KEY = "scan_state"

KIND_PRIORITY = {"overdue": 0, "due_soon": 1, "dependency": 2, "ready": 3}
RISK_KINDS = ("overdue", "due_soon", "dependency")

_started = False
_thread_guard = threading.Lock()


def _now():
    return datetime.now().replace(microsecond=0)


def reminder_kind(reminder):
    """风险类型：巡检生成时带 kind；种子数据按内容关键词推断（与页面展示共用）。"""
    kind = reminder.get("kind")
    if kind in KIND_PRIORITY:
        return kind
    content = reminder.get("content") or ""
    if "逾期" in content or "已延期" in content:
        return "overdue"
    if "依赖" in content:
        return "dependency"
    if any(word in content for word in ("到期", "临期", "还剩", "还有", "今晚", "今日")):
        return "due_soon"
    return "info"


def _hours_text(hours):
    if hours < 1:
        return "不到 1 小时"
    if hours < 24:
        return "%d 小时" % hours
    return "%d 天" % (hours // 24)


def _task_hits(task, index, now):
    """返回任务命中的风险 [(kind, content)]，按 kind 优先级排序；未命中返回 []。"""
    if task.get("status") not in VISIBLE_STATUSES:
        return []
    title = task.get("title") or task.get("task_id") or ""
    progress = int(task.get("progress") or 0)
    due = llm.parse_dt(task.get("due_at") or "")
    start = llm.parse_dt(task.get("scheduled_start_at") or "")
    hits = []

    if due and due < now:
        hours = int((now - due).total_seconds() // 3600)
        hits.append(("overdue", "任务「%s」已逾期 %s（截止 %s），当前进度 %d%%，请尽快处理并说明延期原因" % (
            title, _hours_text(hours), due.strftime("%m-%d %H:%M"), progress)))
    elif due and due <= now + timedelta(hours=DUE_SOON_HOURS):
        hours = max(0, int((due - now).total_seconds() // 3600))
        hits.append(("due_soon", "任务「%s」将于 %s 到期，还剩 %s，当前进度 %d%%，请确认能否按时完成" % (
            title, due.strftime("%m-%d %H:%M"), _hours_text(hours), progress)))

    for dep_id in task.get("depends_on") or []:
        dep = index.get(dep_id)
        if dep is None or dep.get("status") in CLOSED_STATUSES:
            continue
        dep_finish = llm.parse_dt(dep.get("planned_finish_at") or "") or llm.parse_dt(dep.get("due_at") or "")
        task_start = start or due
        blocking = (bool(dep_finish and task_start) and dep_finish > task_start) or \
                   (not dep_finish and bool(task_start) and task_start >= now)
        if blocking:
            hits.append(("dependency", "任务「%s」依赖「%s」未完成，请先推进前置任务或调整排期" % (
                title, dep.get("title") or dep_id)))
            break

    if task.get("status") == "待执行" and start and now <= start <= now + timedelta(hours=READY_HOURS):
        hits.append(("ready", "任务「%s」计划 %s 开始，请预留时间" % (title, start.strftime("%m-%d %H:%M"))))

    hits.sort(key=lambda item: KIND_PRIORITY.get(item[0], 9))
    return hits


def _recent_keys(store, now):
    """跨轮去重键：(task_id, kind)；未读的、或 24 小时内触发过的都算「已提醒」。"""
    keys = set()
    for reminder in store.get("reminders", []):
        key = (reminder.get("task_id") or "", reminder_kind(reminder))
        if reminder.get("status") == "未读":
            keys.add(key)
            continue
        moment = llm.parse_dt(reminder.get("trigger_at") or "")
        if moment and moment <= now and now - moment < timedelta(hours=DEDUPE_HOURS):
            keys.add(key)
    return keys


def build_new_reminders(store, now=None):
    """纯函数：根据当前任务状态构造本轮应新增的提醒列表（不修改 store）。"""
    now = now or _now()
    index = {t.get("task_id"): t for t in store.get("tasks", [])}
    existing_ids = {r.get("reminder_id") for r in store.get("reminders", [])}

    def next_id():
        index_no = 1
        while ("R%03d" % index_no) in existing_ids:
            index_no += 1
        reminder_id = "R%03d" % index_no
        existing_ids.add(reminder_id)
        return reminder_id

    recent = _recent_keys(store, now)
    candidates = []
    for task in store.get("tasks", []):
        hits = _task_hits(task, index, now)
        if not hits:
            continue
        kind, content = hits[0]
        if (task.get("task_id"), kind) in recent:
            continue
        candidates.append((KIND_PRIORITY.get(kind, 9), task.get("due_at") or "9999", task, kind, content))

    candidates.sort(key=lambda item: (item[0], item[1]))
    return [
        {
            "reminder_id": next_id(),
            "task_id": task.get("task_id"),
            "trigger_at": llm.iso(now),
            "channel": "页面弹窗",
            "status": "未读",
            "content": content,
            "kind": kind,
            "source": "scheduler",
        }
        for _, _, task, kind, content in candidates[:MAX_NEW_PER_SCAN]
    ]


def scan_once(services, now=None):
    """执行一轮巡检：加锁读 → 生成 → 写 → 刷新，返回新增提醒列表。"""
    now = now or _now()
    with services.store_lock:
        store = services.read_store()
        created = build_new_reminders(store, now)
        if created:
            store.setdefault("reminders", []).extend(created)
        state = store.setdefault(SCAN_STATE_KEY, {})
        state["last_scan_at"] = llm.iso(now)
        state["last_created"] = len(created)
        state["scans"] = int(state.get("scans") or 0) + 1
        services.write_store(store)
    return created


def _interval_minutes(services):
    try:
        value = int(services.read_settings().get("reminder_interval_minutes") or DEFAULT_INTERVAL_MINUTES)
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_MINUTES
    return max(MIN_INTERVAL_MINUTES, value)


def _loop(services):
    time.sleep(FIRST_SCAN_DELAY_SECONDS)
    while True:
        try:
            scan_once(services)
        except Exception:
            pass
        time.sleep(_interval_minutes(services) * 60)


def start(services):
    """启动巡检线程（幂等）；开发模式 reloader 下只在子进程启动。"""
    global _started
    with _thread_guard:
        if _started:
            return False
        _started = True
    threading.Thread(target=_loop, args=(services,), name="reminder-scan", daemon=True).start()
    return True


def register(app, services):
    @app.post("/reminders/scan")
    def reminders_scan():
        try:
            created = scan_once(services)
            ok = True
            message = ("巡检完成，新增 %d 条提醒。" % len(created)) if created else "巡检完成，没有新的风险提醒。"
        except Exception as error:
            created, ok = [], False
            message = "巡检失败：%s" % error
        if request.get_json(silent=True) is not None:
            return jsonify({"ok": ok, "created": len(created), "message": message}), (200 if ok else 500)
        flash(message, "success" if ok else "error")
        raw = (request.form.get("next") or "").strip().lstrip("?&")
        suffix = ("?" + raw) if raw and len(raw) <= 600 else ""
        return redirect(url_for("reminders") + suffix)

    if not (app.config.get("IS_DEVELOPMENT") and os.environ.get("WERKZEUG_RUN_MAIN") != "true"):
        start(services)
