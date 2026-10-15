"""A 线 · 智能排期（A3）。

对应需求：REQ-004/005/031（F-201/202/203/204/205/206/502）
归属文件：backend/schedule.py · pages/schedule.* · backend/plan.py（拆解在确认时完成）

排期规则（与页面「查看依据」展示的口径一致）：
- 统一视图：所有计划的未完成任务在一张日程表上展示（冲突跨计划统一判定）。
- 生成：待安排任务按截止时间倒推（由晚到早、避让全部既有占用区间，项间留 15 分钟缓冲）；
  倒推空间不足时顺排到最近可用空档；既有安排保持不变。
- 冲突（阻断保存）：任意任务时间段重叠（含跨计划）/ 依赖未满足 / 依赖关系成环。
- 超期（警告，不阻断）：计划完成时间点晚于截止时间；交由重排建议处理。
- 动态重排：已延期或安排已过期的任务给出重排建议，可逐条或批量采纳；建议时段已避让全部安排，采纳后不会产生新冲突。
"""

import csv
import io
from datetime import datetime, timedelta
from importlib import import_module

from flask import Response, flash, jsonify, redirect, render_template, request, url_for

llm = import_module("backend.llm")

VISIBLE_STATUSES = ("待安排", "待执行", "进行中", "已延期")
GAP_MINUTES = 15
PLAN_WIDTH_DAYS_LIMIT = 45

# 可用时间（Q-06 待确认，先按工作时段 09:00-18:00、周一至周五实现；超长任务不限制）
WORK_START_HOUR = 9
WORK_END_HOUR = 18
_WORK_SPAN_MINUTES = (WORK_END_HOUR - WORK_START_HOUR) * 60

CSV_COLUMNS = [
    ("任务ID", "task_id"),
    ("任务标题", "title"),
    ("所属计划", "plan_title"),
    ("优先级", "priority"),
    ("预计耗时（分钟）", "estimate_minutes"),
    ("计划开始时间", "start_disp"),
    ("计划完成时间点", "finish_disp"),
    ("截止时间", "due_disp"),
    ("状态", "status"),
    ("冲突标记", "conflict_flag"),
    ("超期", "overdue_text"),
]


def _now():
    return datetime.now().replace(microsecond=0)


def _ceil_to(moment, unit_minutes=15):
    remainder = moment.minute % unit_minutes
    discard = timedelta(minutes=remainder, seconds=moment.second, microseconds=moment.microsecond)
    moment = moment - discard
    if discard:
        moment = moment + timedelta(minutes=unit_minutes)
    return moment


def _floor_day(moment):
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _ceil_day(moment):
    floored = _floor_day(moment)
    if floored == moment:
        return moment
    return floored + timedelta(days=1)


def _minutes(task):
    try:
        value = int(task.get("estimate_minutes") or 60)
    except (TypeError, ValueError):
        value = 60
    return value if value > 0 else 60


def _overlap(start_a, finish_a, start_b, finish_b):
    return start_a < finish_b and start_b < finish_a


def _within_work_window(start, finish, estimate):
    """任务区间是否完全落在工作日 09:00-18:00 内；超过单日工时上限的长任务不做限制。"""
    if estimate > _WORK_SPAN_MINUTES:
        return True
    return (start.weekday() < 5 and finish.weekday() < 5
            and start.date() == finish.date()
            and start.hour >= WORK_START_HOUR
            and (finish.hour < WORK_END_HOUR or (finish.hour == WORK_END_HOUR and finish.minute == 0)))


def _next_work_moment(moment):
    """把时刻推进到工作时段内（工作日 09:00 起）。"""
    moment = moment.replace(second=0, microsecond=0)
    for _ in range(30):
        if moment.weekday() >= 5:
            moment = (moment + timedelta(days=1)).replace(hour=WORK_START_HOUR, minute=0)
            continue
        if moment.hour < WORK_START_HOUR:
            return moment.replace(hour=WORK_START_HOUR, minute=0)
        if moment.hour >= WORK_END_HOUR:
            moment = (moment + timedelta(days=1)).replace(hour=WORK_START_HOUR, minute=0)
            continue
        return moment
    return moment


def _prev_free_slot(due, estimate, floor, occupied):
    """从 due 向前逐格扫描，找一段落在工作时段、不早于 floor 且不与其他安排重叠的区间。"""
    candidate = _ceil_to(due)
    step = timedelta(minutes=GAP_MINUTES)
    limit = floor - timedelta(days=1)
    for _ in range(8000):
        start = candidate - timedelta(minutes=estimate)
        if start < limit:
            return None
        if (start >= floor and _within_work_window(start, candidate, estimate)
                and not any(_overlap(start, candidate, a, b) for a, b, _ in occupied)):
            return start, candidate
        candidate = candidate - step
    return None


def _next_free_slot(cursor, estimate, occupied):
    """从 cursor 向后找最近可用区间（同样受工作时段约束）。"""
    candidate = _next_work_moment(_ceil_to(cursor))
    step = timedelta(minutes=GAP_MINUTES)
    for _ in range(8000):
        finish = candidate + timedelta(minutes=estimate)
        if (_within_work_window(candidate, finish, estimate)
                and not any(_overlap(candidate, finish, a, b) for a, b, _ in occupied)):
            return candidate, finish
        candidate = candidate + step
    return candidate, candidate + timedelta(minutes=estimate)


def _task_dt(task, field):
    return llm.parse_dt(task.get(field) or "")


def _resolve_rows(tasks, now):
    """把任务解析为带具体时间段的行；待安排任务在内存中生成安排（保存时才落库）。"""
    rows = []
    occupied = []
    for task in tasks:
        start = _task_dt(task, "scheduled_start_at")
        finish = _task_dt(task, "planned_finish_at")
        if start and finish:
            occupied.append((start, finish, task.get("task_id")))
            rows.append({"task": task, "start": start, "finish": finish, "generated": False})

    unscheduled = [t for t in tasks if not (_task_dt(t, "scheduled_start_at") and _task_dt(t, "planned_finish_at"))]
    floor = _ceil_to(now + timedelta(minutes=30))
    for task in sorted(unscheduled, key=lambda t: (t.get("due_at") or "", t.get("task_id") or ""), reverse=True):
        due = _task_dt(task, "due_at") or (now + timedelta(days=7))
        estimate = _minutes(task)
        slot = _prev_free_slot(due, estimate, floor, occupied)
        if slot is None:
            slot = _next_free_slot(floor, estimate, occupied)
        start, finish = slot
        occupied.append((start, finish, task.get("task_id")))
        rows.append({"task": task, "start": start, "finish": finish, "generated": True})

    rows.sort(key=lambda row: (row["task"].get("due_at") or "", row["task"].get("task_id") or ""))
    return rows


def _other_label(other_task, self_task, plan_titles):
    """冲突对方的展示名：同计划用「任务名」，跨计划标注来源计划。"""
    title = other_task.get("title") or other_task.get("task_id") or ""
    if other_task.get("plan_id") != self_task.get("plan_id"):
        plan_title = (plan_titles or {}).get(other_task.get("plan_id")) or ""
        if plan_title:
            return "其他计划「%s」的「%s」" % (plan_title[:14], title)
    return "「%s」" % title


def compute_conflicts(rows, plan_titles=None):
    """返回 {task_id: [冲突原因]}；rows 为 [{task, start, finish}]；跨计划重叠会标注来源计划。"""
    plan_titles = plan_titles or {}
    reasons = {row["task"].get("task_id"): [] for row in rows}
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            if _overlap(a["start"], a["finish"], b["start"], b["finish"]):
                ta, tb = a["task"], b["task"]
                reasons[ta.get("task_id")].append("与%s时间重叠" % _other_label(tb, ta, plan_titles))
                reasons[tb.get("task_id")].append("与%s时间重叠" % _other_label(ta, tb, plan_titles))
    index = {row["task"].get("task_id"): row for row in rows}
    for row in rows:
        task = row["task"]
        for dep_id in task.get("depends_on") or []:
            other = index.get(dep_id)
            if other and other["finish"] > row["start"]:
                reasons[task.get("task_id")].append(
                    "依赖「%s」未满足（其计划完成 %s 晚于本任务开始 %s）" % (
                        other["task"].get("title") or dep_id,
                        other["finish"].strftime("%m-%d %H:%M"),
                        row["start"].strftime("%m-%d %H:%M"),
                    )
                )
    return reasons


def _has_dependency_cycle(tasks):
    ids = {t.get("task_id") for t in tasks}
    graph = {t.get("task_id"): [d for d in (t.get("depends_on") or []) if d in ids] for t in tasks}
    visiting, done = set(), set()

    def walk(node):
        if node in done:
            return False
        if node in visiting:
            return True
        visiting.add(node)
        for nxt in graph.get(node, []):
            if walk(nxt):
                return True
        visiting.discard(node)
        done.add(node)
        return False

    return any(walk(node) for node in graph)


def _build_suggestions(rows, now, ignored=None):
    """重排建议：重叠后移 + 延期/超时重排；建议时段避让全部既有安排与已生成的建议，采纳后不会产生新冲突。
    ignored 为已忽略建议签名集合（task_id|ISO 起始时间），命中的建议不再生成。"""
    ignored = ignored or set()
    suggestions = []
    claimed = set()
    planned = [(row["start"], row["finish"], row["task"].get("task_id")) for row in rows]

    def free_slot(after, estimate, moving_id):
        occupied = [item for item in planned if item[2] != moving_id]
        return _next_free_slot(_ceil_to(after), estimate, occupied)

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if rows[i]["task"]["task_id"] in claimed or rows[j]["task"]["task_id"] in claimed:
                continue
            a, b = rows[i], rows[j]
            if _overlap(a["start"], a["finish"], b["start"], b["finish"]):
                target, anchor = (b, a) if b["start"] >= a["start"] else (a, b)
                estimate = _minutes(target["task"])
                after = max(anchor["finish"] + timedelta(minutes=GAP_MINUTES), now + timedelta(minutes=30))
                start, finish = free_slot(after, estimate, target["task"].get("task_id"))
                signature = "%s|%s" % (target["task"].get("task_id"), llm.iso(start))
                if signature in ignored:
                    continue
                suggestions.append({
                    "task_id": target["task"].get("task_id"),
                    "title": target["task"].get("title") or "",
                    "start": start,
                    "finish": finish,
                    "start_iso": llm.iso(start),
                    "reason": "与「%s」时间重叠，建议顺延到其后最近的可用时段" % (anchor["task"].get("title") or ""),
                    "kind": "overlap",
                })
                claimed.add(target["task"].get("task_id"))
                planned.append((start, finish, target["task"].get("task_id")))

    cursor = _ceil_to(now + timedelta(minutes=30))
    for row in sorted(rows, key=lambda item: item["task"].get("due_at") or ""):
        task = row["task"]
        task_id = task.get("task_id")
        if task_id in claimed:
            continue
        stale = row["finish"] < now
        delayed = task.get("status") == "已延期"
        if not (stale or delayed):
            continue
        estimate = _minutes(task)
        start, finish = free_slot(cursor, estimate, task_id)
        signature = "%s|%s" % (task_id, llm.iso(start))
        if signature in ignored:
            continue
        suggestions.append({
            "task_id": task_id,
            "title": task.get("title") or "",
            "start": start,
            "finish": finish,
            "start_iso": llm.iso(start),
            "reason": ("任务已延期 %d 次，建议重排到最近可用时段" % (task.get("delay_count") or 1)) if delayed
                      else "当前安排已过期，建议重排到最近可用时段",
            "kind": "stale",
        })
        claimed.add(task_id)
        planned.append((start, finish, task_id))
        cursor = _ceil_to(finish + timedelta(minutes=GAP_MINUTES))
    return suggestions[:5]


def _serialize_row(row, reasons, now, plan_titles=None):
    task = row["task"]
    due = _task_dt(task, "due_at")
    overdue = bool(due and row["finish"] > due)
    conflict_list = reasons.get(task.get("task_id")) or []
    status_key = {"待执行": "ready", "进行中": "doing", "已延期": "delayed", "待安排": "todo"}.get(
        task.get("status"), "ready")
    plan_id = task.get("plan_id") or ""
    return {
        "task_id": task.get("task_id") or "",
        "title": task.get("title") or "",
        "plan_id": plan_id,
        "plan_title": (plan_titles or {}).get(plan_id) or plan_id,
        "priority": task.get("priority") or "中",
        "status": task.get("status") or "",
        "status_key": status_key,
        "estimate_minutes": _minutes(task),
        "due_at": task.get("due_at") or "",
        "due_disp": due.strftime("%m-%d %H:%M") if due else "未设置",
        "start": llm.iso(row["start"]),
        "finish": llm.iso(row["finish"]),
        "start_disp": row["start"].strftime("%m-%d %H:%M"),
        "finish_disp": row["finish"].strftime("%m-%d %H:%M"),
        "depends_on": list(task.get("depends_on") or []),
        "conflicts": conflict_list,
        "overdue": overdue,
        "generated": row["generated"],
        "conflict_flag": "冲突" if conflict_list else ("超期" if overdue else ""),
        "overdue_text": "是" if overdue else "",
        "started": row["start"] <= now < row["finish"],
    }


def _schedule_view(store, now=None):
    """排期视图的唯一数据出口：全部计划的未完成任务统一展示（页面 / 导出 / 建议 / 保存兜底都走这里）。"""
    now = now or _now()
    plan_titles = {p.get("plan_id"): (p.get("goal") or "") for p in store.get("plans", [])}
    tasks = [t for t in store.get("tasks", []) if t.get("status") in VISIBLE_STATUSES]

    resolved = _resolve_rows(tasks, now)
    reasons = compute_conflicts(resolved, plan_titles)
    serialized = [_serialize_row(row, reasons, now, plan_titles) for row in resolved]

    span, suggestions = None, []
    stats = {"total": 0, "scheduled": 0, "unscheduled": 0, "conflicts": 0, "overdue": 0, "plans": 0}

    if serialized:
        span_start = _floor_day(min(llm.parse_dt(r["start"]) for r in serialized))
        span_end = _ceil_day(max(
            [llm.parse_dt(r["finish"]) for r in serialized]
            + [llm.parse_dt(r["due_at"]) for r in serialized if r["due_at"]]))
        capped = (span_end - span_start) > timedelta(days=PLAN_WIDTH_DAYS_LIMIT)
        if capped:
            span_end = span_start + timedelta(days=PLAN_WIDTH_DAYS_LIMIT)
        total_minutes = max(1, int((span_end - span_start).total_seconds() // 60))
        days = []
        day = span_start
        while day < span_end:
            days.append({
                "label": day.strftime("%m-%d"),
                "weekday": "一二三四五六日"[day.weekday()],
                "weekend": day.weekday() >= 5,
                "left_pct": round((day - span_start).total_seconds() / 60 / total_minutes * 100, 4),
            })
            day += timedelta(days=1)
        for item in days:
            item["width_pct"] = round(100.0 / max(1, len(days)), 4)
        def position(moment):
            return max(0.0, min(100.0, (moment - span_start).total_seconds() / 60 / total_minutes * 100))
        for item in serialized:
            start, finish = llm.parse_dt(item["start"]), llm.parse_dt(item["finish"])
            item["left_pct"] = round(position(start), 4)
            item["width_pct"] = round(max(0.4, position(finish) - position(start)), 4)
            if item["due_at"]:
                item["due_left_pct"] = round(position(llm.parse_dt(item["due_at"])), 4)
        span = {
            "start": llm.iso(span_start), "end": llm.iso(span_end),
            "days": days, "capped": capped,
        }
        stats = {
            "total": len(serialized),
            "scheduled": sum(1 for r in serialized if not r["generated"]),
            "unscheduled": sum(1 for r in serialized if r["generated"]),
            "conflicts": sum(1 for r in serialized if r["conflicts"]),
            "overdue": sum(1 for r in serialized if r["overdue"]),
            "plans": len({r["plan_id"] for r in serialized if r["plan_id"]}),
        }
        ignored = set(store.get("ignored_suggestions") or [])
        suggestions = _build_suggestions(resolved, now, ignored)

    return {
        "rows": serialized,
        "span": span,
        "stats": stats,
        "suggestions": suggestions,
        "now": now,
    }


# ---------------------------------------------------------------------------
# 计划确认场景：新计划与现有排期的风险预览（只读，不写库）
# ---------------------------------------------------------------------------

PREVIEW_TASK_PREFIX = "PREVIEW-"


def _schedule_risk_warnings(store, plan_id, plan_title, plan_tasks, now):
    """把计划任务与现有排期一起全局安排，返回风险条目 [{kind, title, text}]（只读）。"""
    wanted = {t.get("task_id") for t in plan_tasks}
    others = [t for t in store.get("tasks", []) if t.get("status") in VISIBLE_STATUSES
              and t.get("task_id") not in wanted]
    rows = _resolve_rows(others + list(plan_tasks), now)

    plan_titles = {p.get("plan_id"): (p.get("goal") or "") for p in store.get("plans", [])}
    plan_titles.setdefault(plan_id, plan_title)
    reasons = compute_conflicts(rows, plan_titles)

    warnings = []
    for row in rows:
        task = row["task"]
        if task.get("task_id") not in wanted:
            continue
        title = task.get("title") or ""
        due = _task_dt(task, "due_at")
        if due and row["finish"] > due:
            warnings.append({
                "kind": "overdue",
                "title": title,
                "text": "「%s」预计安排 %s → %s，晚于截止 %s（超期）" % (
                    title, row["start"].strftime("%m-%d %H:%M"), row["finish"].strftime("%m-%d %H:%M"),
                    due.strftime("%m-%d %H:%M")),
            })
        for reason in reasons.get(task.get("task_id")) or []:
            warnings.append({"kind": "conflict", "title": title, "text": "「%s」%s" % (title, reason)})
    return warnings


def preview_new_plan_risks(store, plan, now=None):
    """确认前预览：按确认口径为「尚无任务的待办」生成任务（仅内存），检查与现有排期的超期 / 冲突风险。"""
    now = now or _now()
    plan_id = plan.get("plan_id")
    todos = [t for t in store.get("todos", []) if t.get("plan_id") == plan_id]
    if not todos:
        return []
    has_task = {t.get("todo_id") for t in store.get("tasks", []) if t.get("plan_id") == plan_id}
    pending = [t for t in todos if t.get("todo_id") not in has_task]
    if not pending:
        return []

    default_due = llm.plan_default_due(todos, now)
    pseudo = []
    for todo in pending:
        for index, blueprint in enumerate(
                llm.decompose_todo(todo.get("content") or "", todo.get("key_time"), default_due, now)):
            pseudo.append({
                "task_id": "%s%s-%d" % (PREVIEW_TASK_PREFIX, todo.get("todo_id"), index),
                "plan_id": plan_id,
                "title": blueprint["title"],
                "priority": blueprint["priority"],
                "estimate_minutes": blueprint["estimate_minutes"],
                "due_at": blueprint["due_at"],
                "status": "待安排",
                "depends_on": [],
            })
    return _schedule_risk_warnings(store, plan_id, plan.get("goal") or "", pseudo, now)


def plan_pending_risks(store, plan, now=None):
    """确认后复检：该计划现存「待安排」任务与现有排期一起安排时的超期 / 冲突风险。"""
    now = now or _now()
    pending = [t for t in store.get("tasks", [])
               if t.get("plan_id") == plan.get("plan_id") and t.get("status") == "待安排"]
    if not pending:
        return []
    return _schedule_risk_warnings(store, plan.get("plan_id"), plan.get("goal") or "", pending, now)


def _dep_title_map(store):
    return {t.get("task_id"): (t.get("title") or "") for t in store.get("tasks", [])}


def _basis_lines(settings, rows):
    interval = settings.get("reminder_interval_minutes") or 240
    lines = [
        "统一视图：所有计划的未完成任务在同一张日程表展示；待安排任务按截止时间倒推，安排在可用时间（工作日 09:00–18:00）内，避让全部既有安排，项间留 %d 分钟缓冲；倒推空间不足时顺排到最近可用时段。" % GAP_MINUTES,
        "冲突判定：任意任务时间段重叠（跨计划统一判定，冲突原因会标注来源计划）、依赖未满足或依赖成环 → 标记冲突并阻断保存；计划完成晚于截止时间 → 标记「超期」（不阻断保存，交由重排建议处理）。",
        "提醒依据：后台巡检每 %s 分钟扫描一次（系统设置可修改），命中临期 / 逾期 / 依赖冲突即生成提醒。" % interval,
    ]
    shown = 0
    for row in rows:
        if shown >= 6:
            lines.append("……其余任务依据同上。")
            break
        lines.append("「%s」%s → %s（截止 %s，%s）" % (
            row["title"], row["start_disp"], row["finish_disp"], row["due_disp"],
            "本次按截止倒推自动生成" if row["generated"] else "沿用既有安排"))
        shown += 1
    return lines


def register(app, services):
    read_store = services.read_store
    write_store = services.write_store
    read_settings = services.read_settings
    store_lock = services.store_lock

    @app.get("/schedule")
    def schedule():
        store = read_store()
        settings = read_settings()
        view = _schedule_view(store)
        dep_titles = _dep_title_map(store)
        return render_template(
            "pages/schedule.html",
            active_page="schedule",
            page_name="智能排期",
            settings=settings,
            view=view,
            dep_titles=dep_titles,
            basis_lines=_basis_lines(settings, view["rows"]),
        )

    @app.post("/schedule/save")
    def schedule_save():
        payload = request.get_json(silent=True)
        is_json = payload is not None
        data = payload if is_json else request.form
        plan_id = str(data.get("plan_id") or "").strip()
        raw_items = data.get("items") if is_json else None
        now = _now()

        def fail(messages, code=400, conflicts=None):
            if is_json:
                body = {"ok": False, "message": messages[0], "errors": messages}
                if conflicts:
                    body["conflicts"] = conflicts
                return jsonify(body), code
            for message in messages:
                flash(message, "error")
            return redirect(url_for("schedule"))

        with store_lock:
            store = read_store()
            plan_titles = {p.get("plan_id"): (p.get("goal") or "") for p in store.get("plans", [])}
            visible_tasks = [t for t in store.get("tasks", []) if t.get("status") in VISIBLE_STATUSES]
            if not visible_tasks and not raw_items:
                return fail(["当前没有可排期的任务。"])

            if raw_items:
                if not isinstance(raw_items, list) or not raw_items:
                    return fail(["排期数据格式无效，请刷新页面后重试。"])
                index = {t.get("task_id"): t for t in visible_tasks}
                proposals = []
                errors = []
                for item in raw_items:
                    if not isinstance(item, dict):
                        errors.append("排期数据格式无效，请刷新页面后重试。")
                        continue
                    task = index.get(str(item.get("task_id") or "").strip())
                    if task is None:
                        errors.append("任务 %s 不存在或已不在可排期范围，无法保存。" % item.get("task_id"))
                        continue
                    try:
                        estimate = int(item.get("estimate_minutes"))
                    except (TypeError, ValueError):
                        estimate = None
                    if estimate is None or estimate <= 0:
                        errors.append("预计耗时必须为大于 0 的整数。")
                        continue
                    start = llm.parse_dt(item.get("scheduled_start_at"))
                    if start is None:
                        errors.append("计划开始时间格式无效，无法保存。")
                        continue
                    due = None
                    if item.get("due_at"):
                        due = llm.parse_dt(item.get("due_at"))
                        if due is None:
                            errors.append("截止时间格式无效，无法保存。")
                            continue
                    proposals.append({
                        "task": task,
                        "start": start,
                        "finish": start + timedelta(minutes=estimate),
                        "estimate": estimate,
                        "priority": str(item.get("priority") or task.get("priority") or "中"),
                        "due": due,
                    })
                if errors:
                    return fail(errors)

                # 冲突与依赖成环校验：全部通过后才落库（失败不写任何数据）
                if _has_dependency_cycle(store.get("tasks", [])):
                    return fail(["依赖关系存在循环，无法保存，请先调整依赖。"])

                overrides = {p["task"].get("task_id"): p for p in proposals}
                row_proposals = [{
                    "task": p["task"], "start": p["start"], "finish": p["finish"], "generated": False,
                } for p in proposals]
                # 未包含在提交里的待安排任务：一并自动排期，保证保存后状态一致
                covered = {p["task"].get("task_id") for p in proposals}
                leftovers = [t for t in visible_tasks if t.get("task_id") not in covered]
                row_proposals.extend(_resolve_rows(leftovers, now))
                reasons = compute_conflicts(row_proposals, plan_titles)
                blocking = ["「%s」%s" % (r["task"].get("title"), reason)
                            for r in row_proposals for reason in reasons.get(r["task"].get("task_id"), [])]
                if blocking:
                    message = "存在未处理的冲突，请调整后再保存：" + "；".join(blocking[:3])
                    return fail([message], conflicts=blocking[:8])

                saved = 0
                all_rows = {r["task"].get("task_id"): r for r in row_proposals}
                for task in visible_tasks:
                    row = all_rows.get(task.get("task_id"))
                    if row is None:
                        continue
                    proposal = overrides.get(task.get("task_id"))
                    if proposal:
                        task["estimate_minutes"] = proposal["estimate"]
                        task["priority"] = proposal["priority"]
                        if proposal["due"]:
                            task["due_at"] = llm.iso(proposal["due"])
                    task["scheduled_start_at"] = llm.iso(row["start"])
                    task["planned_finish_at"] = llm.iso(row["finish"])
                    if task.get("status") == "待安排":
                        task["status"] = "待执行"
                    task["updated_at"] = llm.iso(now)
                    saved += 1
                write_store(store)
            else:
                # 无明细（表单直提 / 自测脚本）：按计划整体自动排期落库（跨计划避让）
                if not plan_id:
                    return fail(["缺少计划参数，无法自动排期。"])
                plan = next((p for p in store.get("plans", []) if p.get("plan_id") == plan_id), None)
                if plan is None:
                    return fail(["计划不存在或已被删除，无法保存排期。"])
                plan_tasks = [t for t in visible_tasks if t.get("plan_id") == plan_id]
                if not plan_tasks:
                    return fail(["当前计划没有可排期的任务。"])
                resolved = _resolve_rows(visible_tasks, now)
                saved = 0
                for row in resolved:
                    task = row["task"]
                    if task.get("plan_id") != plan_id or not row["generated"]:
                        continue
                    task["scheduled_start_at"] = llm.iso(row["start"])
                    task["planned_finish_at"] = llm.iso(row["finish"])
                    if task.get("status") == "待安排":
                        task["status"] = "待执行"
                    task["updated_at"] = llm.iso(now)
                    saved += 1
                write_store(store)

        if is_json:
            return jsonify({"ok": True, "saved": saved, "message": "排期已保存（%d 项）。" % saved})
        flash("排期已保存。", "success")
        return redirect(url_for("schedule"))

    @app.post("/schedule/suggest/apply")
    def schedule_suggest_apply():
        payload = request.get_json(silent=True)
        is_json = payload is not None
        data = payload if is_json else request.form
        task_id = str(data.get("task_id") or "").strip()
        apply_all = str(data.get("apply_all") or "").lower() in ("1", "true")

        now = _now()
        applied = 0
        with store_lock:
            store = read_store()
            view = _schedule_view(store, now)
            suggestions = view["suggestions"]
            targets = suggestions if apply_all else [s for s in suggestions if s["task_id"] == task_id]
            index = {t.get("task_id"): t for t in store.get("tasks", [])}
            for suggestion in targets:
                task = index.get(suggestion["task_id"])
                if task is None:
                    continue
                task["scheduled_start_at"] = llm.iso(suggestion["start"])
                task["planned_finish_at"] = llm.iso(suggestion["finish"])
                if task.get("status") == "待安排":
                    task["status"] = "待执行"
                task["updated_at"] = llm.iso(now)
                applied += 1
            if applied:
                write_store(store)

        message = "已采纳 %d 条重排建议。" % applied if applied else "没有需要采纳的建议。"
        if is_json:
            return jsonify({"ok": True, "applied": applied, "message": message})
        flash(message, "success")
        return redirect(url_for("schedule"))

    @app.post("/schedule/suggest/ignore")
    def schedule_suggest_ignore():
        payload = request.get_json(silent=True)
        is_json = payload is not None
        data = payload if is_json else request.form
        task_id = str(data.get("task_id") or "").strip()
        start = llm.parse_dt(data.get("start"))
        if not task_id or start is None:
            if is_json:
                return jsonify({"ok": False, "message": "缺少任务或时间参数，无法忽略。"}), 400
            flash("忽略失败：缺少任务或时间参数。", "error")
            return redirect(url_for("schedule"))

        signature = "%s|%s" % (task_id, llm.iso(start))
        with store_lock:
            store = read_store()
            ignored = [s for s in (store.get("ignored_suggestions") or []) if s != signature]
            ignored.append(signature)
            store["ignored_suggestions"] = ignored[-200:]
            write_store(store)

        if is_json:
            return jsonify({"ok": True, "message": "已忽略该建议。"})
        flash("已忽略该建议。", "success")
        return redirect(url_for("schedule"))

    @app.get("/schedule/export")
    def schedule_export():
        store = read_store()
        view = _schedule_view(store)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([label for label, _ in CSV_COLUMNS])
        for row in view["rows"]:
            writer.writerow([row.get(key, "") for _, key in CSV_COLUMNS])
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        return Response(
            "\ufeff" + buffer.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=schedule_%s.csv" % stamp},
        )
