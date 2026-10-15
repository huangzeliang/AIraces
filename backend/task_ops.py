"""B 线 · 执行与巡检线后端：B1 任务列表 + B2 提醒中心 + F-116/F-117 拆解详情。

对应需求：REQ-006/007/009/010/019/021/022/032（F-301~307/403~405/501/503/903/905/906）
归属文件：backend/task_ops.py · pages/tasks.* · pages/reminders.* · pages/task_detail.*

接口契约（AI 自测脚本依赖，路径与动词不可改动）：
- GET  /tasks                              列表页（筛选 / 排序 / 分页，URL 保留筛选态）
- POST /tasks/create                       新建任务
- POST /tasks/{task_id}/complete           完成（反馈 + 可撤销）
- POST /tasks/{task_id}/delay              延期（原因必填，写入 delay_reason）
- POST /tasks/{task_id}/cancel             取消（页面二次确认后提交）
- POST /tasks/{task_id}/progress           进度滑杆（100 自动完成）
- POST /tasks/{task_id}/edit               编辑（标题/描述/优先级/耗时/截止/负责人）
- POST /tasks/{task_id}/delete             删除（写状态日志，历史反馈与提醒保留）
- POST /tasks/undo/{feedback_id}           撤销反馈（按前态快照回滚）
- GET  /tasks/export                       导出当前筛选结果（CSV / JSON）
- GET  /tasks/{task_id}                    单任务详情（F-116）：拆解树 + 子任务时间线甘特图
- POST /tasks/{task_id}/decompose          拆解为个人子任务（F-117）：负责人 / 工时 / 起止
- POST /tasks/{child_id}/detach            子任务迁出（上提为顶层任务）
- GET  /reminders                          提醒中心（未读置顶高亮，风险样式）
- POST /reminders/{reminder_id}/read       标记已读
- POST /reminders/{reminder_id}/unread     重新标记未读
- POST /reminders/{reminder_id}/ignore     忽略
- POST /reminders/read-all                 全部已读（页面二次确认后提交）
- GET  /reminders/export                   导出提醒记录 CSV

拆解规则（F-117，提交时强校验，失败不写任何数据）：
- 拆解层级最多 3 层（大任务 → 个人任务 → 再下拆一层）；已完成 / 已取消任务不可再拆；
- 子任务工时合计不得超过父任务预计工时；
- 子任务时间窗必须落在父任务时间窗内（父任务未排期时，首个子任务提交后父窗口自动扩为其并集）；
- 同级子任务时间窗不得互相重叠；
- 父子链由 parent_task_id 自引用构成，页面展示与甘特缩进都基于该链，结构上不存在循环引用。

落库闭环沿用全局约定：读取 → 校验 → 加锁写 → 刷新展示；失败不写任何数据。
接口同时接受 JSON（AI 自测脚本 / fetch）与表单（页面）两种提交方式。
"""

import csv
import io
import json
from datetime import datetime, timedelta
from importlib import import_module
from urllib.parse import urlencode

from flask import Response, flash, jsonify, redirect, render_template, request, url_for

fb = import_module("backend.feedback")
llm = import_module("backend.llm")
sched = import_module("backend.scheduler")

VISIBLE_STATUSES = ("待安排", "待执行", "进行中", "已延期")
ALL_STATUSES = ("待安排", "待执行", "进行中", "已延期", "已完成", "已取消")
UNFINISHED_CHIP = "未完成"
STATUS_KEY = {"待安排": "todo", "待执行": "ready", "进行中": "doing", "已延期": "delayed", "已完成": "done", "已取消": "canceled"}

PRIORITIES = ("高", "中", "低")
PRIORITY_KEY = {"高": "high", "中": "mid", "低": "low"}
PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2}

TITLE_LIMIT = 80
DETAIL_LIMIT = 500
REASON_LIMIT = 200
KEYWORD_LIMIT = 60
ESTIMATE_LIMIT = 100000
OWNER_LIMIT = 20

MAX_DEPTH = 3                 # 拆解树最多 3 层：大任务 → 个人任务 → 再下拆一层
DECOMPOSE_BLOCKED = ("已完成", "已取消")
GANTT_WIDTH_DAYS_LIMIT = 45   # 甘特时间轴最宽 45 天，超出截断（与智能排期口径一致）

PAGINATE_THRESHOLD = 50
PAGE_SIZE = 20

CHANNELS = ("页面弹窗", "桌面通知", "企业微信", "钉钉")
REMINDER_STATUSES = ("未读", "已读", "已忽略")
REMINDER_STATUS_KEY = {"未读": "unread", "已读": "read", "已忽略": "ignored"}
KIND_LABEL = {"overdue": "逾期风险", "due_soon": "临期提醒", "dependency": "依赖冲突", "ready": "待执行", "info": "常规提醒"}

TASK_EXPORT_COLUMNS = [
    ("任务ID", "task_id"),
    ("所属计划ID", "plan_id"),
    ("任务标题", "title"),
    ("优先级", "priority"),
    ("预计耗时（分钟）", "estimate_minutes"),
    ("截止时间", "due_at"),
    ("计划开始时间", "scheduled_start_at"),
    ("计划完成时间点", "planned_finish_at"),
    ("状态", "status"),
    ("进度", "progress"),
    ("延期次数", "delay_count"),
    ("延期原因", "delay_reason"),
    ("完成时间", "completed_at"),
    ("更新时间", "updated_at"),
]

REMINDER_EXPORT_COLUMNS = [
    ("提醒ID", "reminder_id"),
    ("任务ID", "task_id"),
    ("任务标题", "task_title"),
    ("触发时间", "trigger_at"),
    ("渠道", "channel"),
    ("状态", "status"),
    ("类型", "kind_label"),
    ("内容", "content"),
]


def _now():
    return datetime.now().replace(microsecond=0)


def _next_seq_id(items, key, prefix, width=3):
    used = {item.get(key) for item in items}
    index = 1
    while ("%s%0*d" % (prefix, width, index)) in used:
        index += 1
    return "%s%0*d" % (prefix, width, index)


def _fmt_moment(value):
    moment = llm.parse_dt(value)
    return moment.strftime("%m-%d %H:%M") if moment else ""


def _time_input_value(value):
    """ISO 时间转 <input type=datetime-local> 需要的 YYYY-MM-DDTHH:MM。"""
    moment = llm.parse_dt(value)
    return moment.strftime("%Y-%m-%dT%H:%M") if moment else ""


def _parse_date(raw):
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _find_task(store, task_id):
    return next((t for t in store.get("tasks", []) if t.get("task_id") == task_id), None)


def _find_reminder(store, reminder_id):
    return next((r for r in store.get("reminders", []) if r.get("reminder_id") == reminder_id), None)


def _task_index(store):
    return {t.get("task_id"): t for t in store.get("tasks", []) if t.get("task_id")}


def _child_index(store):
    """parent_task_id → 直接子任务列表（按计划开始时间排序，供列表徽标与甘特缩进复用）。"""
    index = {}
    for task in store.get("tasks", []):
        parent_id = task.get("parent_task_id") or ""
        if parent_id:
            index.setdefault(parent_id, []).append(task)
    for children in index.values():
        children.sort(key=lambda t: (t.get("scheduled_start_at") or "9999", t.get("task_id") or ""))
    return index


def _ancestors(task, by_id):
    """自下而上的父链（带 seen 防环兜底），用于面包屑、层级校验与循环引用兜底。"""
    chain, seen = [], set()
    current = task
    while current is not None:
        parent_id = current.get("parent_task_id") or ""
        if not parent_id or parent_id in seen:
            break
        parent = by_id.get(parent_id)
        if parent is None:
            break
        seen.add(parent_id)
        chain.append(parent)
        current = parent
    return chain


def _depth_of(task, by_id):
    """任务在拆解树中的层级：顶层 = 1。"""
    return len(_ancestors(task, by_id)) + 1


def _minutes_of(task):
    try:
        value = int(task.get("estimate_minutes") or 60)
    except (TypeError, ValueError):
        value = 60
    return value if value > 0 else 60


def _subtree_rows(root, child_index):
    """拆解树深度优先展开为 [(task, depth)]，以 root 为第 1 层；防环 + 层级兜底。"""
    rows, seen = [], set()

    def walk(task, depth):
        task_id = task.get("task_id")
        if task_id in seen or depth > MAX_DEPTH + 2:
            return
        seen.add(task_id)
        rows.append((task, depth))
        for child in child_index.get(task_id, []):
            walk(child, depth + 1)

    walk(root, 1)
    return rows


def _effective_window(task, child_index, guard=0):
    """任务在甘特上的有效窗口：优先自身计划起止；未排期的父任务用子任务窗口并集兜底。"""
    start = llm.parse_dt(task.get("scheduled_start_at") or "")
    finish = llm.parse_dt(task.get("planned_finish_at") or "")
    if start and finish and start < finish:
        return start, finish
    if guard < 6:
        windows = [w for w in (_effective_window(child, child_index, guard + 1)
                               for child in child_index.get(task.get("task_id"), [])) if w]
        if windows:
            return min(w[0] for w in windows), max(w[1] for w in windows)
    return None


def _floor_day(moment):
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _ceil_day(moment):
    floored = _floor_day(moment)
    return moment if floored == moment else floored + timedelta(days=1)


def _timeline_span(views, now):
    """为拆解树视图计算甘特刻度与百分比定位；无任何窗口时返回 None（页面显示空态）。"""
    windows = [v.pop("_window", None) for v in views]
    if not any(windows):
        return None
    span_start = _floor_day(min(w[0] for w in windows if w))
    span_end = _ceil_day(max(w[1] for w in windows if w))
    dues = [llm.parse_dt(v.get("due_at") or "") for v in views]
    dues = [d for d in dues if d]
    if dues:
        span_end = _ceil_day(max(span_end, max(dues)))
    capped = (span_end - span_start) > timedelta(days=GANTT_WIDTH_DAYS_LIMIT)
    if capped:
        span_end = span_start + timedelta(days=GANTT_WIDTH_DAYS_LIMIT)
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
    width = round(100.0 / max(1, len(days)), 4)
    for item in days:
        item["width_pct"] = width

    def position(moment):
        return max(0.0, min(100.0, (moment - span_start).total_seconds() / 60 / total_minutes * 100))

    for view, window in zip(views, windows):
        if not window:
            view["unscheduled"] = True
            continue
        start, finish = window
        view["left_pct"] = round(position(start), 4)
        view["width_pct"] = round(max(0.4, position(finish) - position(start)), 4)
        view["window_disp"] = "%s → %s" % (start.strftime("%m-%d %H:%M"), finish.strftime("%m-%d %H:%M"))
        due = llm.parse_dt(view.get("due_at") or "")
        if due:
            view["due_left_pct"] = round(position(due), 4)
    today_pct = round(position(now), 4) if span_start <= now <= span_end else None

    return {
        "start_disp": span_start.strftime("%m-%d"),
        "end_disp": span_end.strftime("%m-%d"),
        "days": days,
        "capped": capped,
        "today_pct": today_pct,
    }


def _respond_task_detail(ok, message, task_id, code=200, **extra):
    """详情页专用响应：JSON 返回 JSON；表单 flash 后回到同一任务详情（或列表）。"""
    if request.get_json(silent=True) is not None:
        body = {"ok": ok, "message": message}
        body.update(extra)
        return jsonify(body), (200 if ok else code)
    flash(message, "success" if ok else "error")
    return redirect(url_for("task_detail", task_id=task_id))


def _next_suffix():
    """从表单里的 next 字段还原当前筛选查询串，操作后跳回同一筛选态。"""
    raw = (request.form.get("next") or "").strip().lstrip("?&")
    if not raw or len(raw) > 600:
        return ""
    return "?" + raw


def _respond(ok, message, code=200, **extra):
    """JSON 请求返回 JSON；页面表单请求 flash + 302 跳回当前筛选态。"""
    if request.get_json(silent=True) is not None:
        body = {"ok": ok, "message": message}
        body.update(extra)
        return jsonify(body), (200 if ok else code)
    flash(message, "success" if ok else "error")
    return redirect(url_for("tasks") + _next_suffix())


def _respond_reminders(ok, message, code=200):
    if request.get_json(silent=True) is not None:
        return jsonify({"ok": ok, "message": message}), (200 if ok else code)
    flash(message, "success" if ok else "error")
    return redirect(url_for("reminders") + _next_suffix())


# ---------------------------------------------------------------------------
# B1 · 任务列表：查询 / 排序 / 分页
# ---------------------------------------------------------------------------

def _build_query(args, drop=(), **overrides):
    """基于当前查询参数构造新查询串（用于排序、分页、导出链接，保持筛选态）。"""
    pairs = []
    for key in args.keys():
        if key in drop:
            continue
        for value in args.getlist(key):
            pairs.append((key, value))
    pairs.extend((key, str(value)) for key, value in overrides.items() if value is not None)
    query = urlencode(pairs)
    return ("?" + query) if query else ""


def _resolve_statuses(args):
    """状态筛选：默认「全部未完成」；f=1 表示用户已主动筛过（允许空选 = 全部）。"""
    raw = args.getlist("status")
    if not raw and "f" not in args:
        return list(VISIBLE_STATUSES)
    statuses = []
    for value in raw:
        if value == UNFINISHED_CHIP:
            statuses.extend(VISIBLE_STATUSES)
        elif value in ALL_STATUSES:
            statuses.append(value)
    return list(dict.fromkeys(statuses))


def _filter_tasks(store, args, now):
    statuses = _resolve_statuses(args)
    priorities = [p for p in args.getlist("priority") if p in PRIORITIES]
    keyword = (args.get("keyword") or "").strip()[:KEYWORD_LIMIT]
    due_from = _parse_date(args.get("due_from"))
    due_to = _parse_date(args.get("due_to"))

    rows = []
    for task in store.get("tasks", []):
        if statuses and task.get("status") not in statuses:
            continue
        if priorities and task.get("priority") not in priorities:
            continue
        if keyword:
            haystack = " ".join((
                task.get("task_id") or "", task.get("title") or "", task.get("detail") or "",
            )).lower()
            if keyword.lower() not in haystack:
                continue
        if due_from or due_to:
            due = llm.parse_dt(task.get("due_at") or "")
            if due is None:
                continue
            if due_from and due.date() < due_from:
                continue
            if due_to and due.date() > due_to:
                continue
        rows.append(task)
    return rows


def _sort_tasks(rows, args):
    sort = args.get("sort") or "due_at"
    if sort not in ("due_at", "priority", "progress", "title", "updated_at"):
        sort = "due_at"
    desc = (args.get("dir") or "asc").lower() == "desc"

    def key(task):
        if sort == "priority":
            return PRIORITY_ORDER.get(task.get("priority"), 9)
        if sort == "progress":
            return int(task.get("progress") or 0)
        if sort == "title":
            return task.get("title") or ""
        if sort == "updated_at":
            return task.get("updated_at") or ""
        return task.get("due_at") or "9999"

    return sorted(rows, key=lambda item: (key(item), item.get("task_id") or ""), reverse=desc)


def _paginate(rows, args):
    total = len(rows)
    if total <= PAGINATE_THRESHOLD:
        return rows, {"enabled": False, "page": 1, "pages": 1, "total": total}
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    try:
        page = int(args.get("page") or 1)
    except ValueError:
        page = 1
    page = min(max(1, page), pages)
    start = (page - 1) * PAGE_SIZE
    return rows[start:start + PAGE_SIZE], {"enabled": True, "page": page, "pages": pages, "total": total}


def _serialize_task(task, now, plan_titles, is_focus=False):
    due = llm.parse_dt(task.get("due_at") or "")
    status = task.get("status") or ""
    overdue = bool(due and due < now and status not in ("已完成", "已取消"))
    plan_id = task.get("plan_id") or ""
    return {
        "task_id": task.get("task_id") or "",
        "title": task.get("title") or "",
        "detail": task.get("detail") or "",
        "priority": task.get("priority") or "中",
        "priority_key": PRIORITY_KEY.get(task.get("priority"), "mid"),
        "status": status,
        "status_key": STATUS_KEY.get(status, "ready"),
        "progress": int(task.get("progress") or 0),
        "estimate_minutes": int(task.get("estimate_minutes") or 60),
        "due_at": task.get("due_at") or "",
        "due_disp": due.strftime("%m-%d %H:%M") if due else "未设置",
        "due_input": _time_input_value(task.get("due_at")),
        "finish_disp": _fmt_moment(task.get("planned_finish_at")) or "未排期",
        "delay_count": int(task.get("delay_count") or 0),
        "delay_reason": task.get("delay_reason") or "",
        "depends_on": list(task.get("depends_on") or []),
        "plan_id": plan_id,
        "plan_title": (plan_titles or {}).get(plan_id) or "",
        "overdue": overdue,
        "completed_at": task.get("completed_at") or "",
        "is_demo": bool(task.get("is_demo")),
        "is_focus": is_focus,
    }


def _task_stats(store, now):
    stats = {"total": 0, "ready": 0, "doing": 0, "delayed": 0, "overdue": 0}
    for task in store.get("tasks", []):
        if not task.get("task_id"):
            continue
        status = task.get("status") or ""
        key = STATUS_KEY.get(status)
        if status in VISIBLE_STATUSES:
            stats["total"] += 1
        if key in ("ready", "doing", "delayed"):
            stats[key] += 1
        due = llm.parse_dt(task.get("due_at") or "")
        if due and due < now and status in VISIBLE_STATUSES:
            stats["overdue"] += 1
    return stats


# ---------------------------------------------------------------------------
# B2 · 提醒中心：视图辅助
# ---------------------------------------------------------------------------

def _serialize_reminder(reminder, task_index, now):
    task = task_index.get(reminder.get("task_id") or "")
    trigger = llm.parse_dt(reminder.get("trigger_at") or "")
    status = reminder.get("status") or "未读"
    kind = sched.reminder_kind(reminder)
    return {
        "reminder_id": reminder.get("reminder_id") or "",
        "task_id": reminder.get("task_id") or "",
        "task_title": (task.get("title") or "") if task else "",
        "task_missing": task is None,
        "trigger_at": reminder.get("trigger_at") or "",
        "trigger_disp": trigger.strftime("%m-%d %H:%M") if trigger else "",
        "channel": reminder.get("channel") or "页面弹窗",
        "status": status,
        "status_key": REMINDER_STATUS_KEY.get(status, "unread"),
        "content": reminder.get("content") or "",
        "kind": kind,
        "kind_label": KIND_LABEL.get(kind, "常规提醒"),
        "is_unread": status == "未读",
        "is_risk": kind in ("overdue", "due_soon", "dependency"),
    }


def register(app, services):
    read_store = services.read_store
    write_store = services.write_store
    read_settings = services.read_settings
    store_lock = services.store_lock

    # ------------------------------------------------------------------
    # B1 · 任务列表页
    # ------------------------------------------------------------------
    @app.get("/tasks")
    def tasks():
        store = read_store()
        settings = read_settings()
        now = _now()
        plan_titles = {p.get("plan_id"): (p.get("goal") or "") for p in store.get("plans", [])}

        rows = _sort_tasks(_filter_tasks(store, request.args, now), request.args)

        focus_id = (request.args.get("focus") or "").strip()
        focus_task = _find_task(store, focus_id) if focus_id else None
        if focus_task is not None and all(t.get("task_id") != focus_id for t in rows):
            rows = [focus_task] + rows

        page_rows, pagination = _paginate(rows, request.args)
        task_views = [_serialize_task(t, now, plan_titles, t is focus_task) for t in page_rows]

        raw_status = request.args.getlist("status")
        checked_status = raw_status if "f" in request.args else [UNFINISHED_CHIP]
        sort_key = request.args.get("sort") or "due_at"
        sort_dir = (request.args.get("dir") or "asc").lower()

        def sort_link(key):
            next_dir = "desc" if (sort_key == key and sort_dir == "asc") else "asc"
            return url_for("tasks") + _build_query(
                request.args, drop=("page", "focus", "sort", "dir"), sort=key, dir=next_dir)

        return render_template(
            "pages/tasks.html",
            active_page="tasks",
            page_name="任务列表",
            settings=settings,
            tasks=task_views,
            stats=_task_stats(store, now),
            plans=[{
                "plan_id": p.get("plan_id"),
                "goal": (p.get("goal") or "")[:28] + ("…" if len(p.get("goal") or "") > 28 else ""),
            } for p in store.get("plans", [])],
            pagination=pagination,
            status_chips=[(UNFINISHED_CHIP, "全部未完成")] + [(s, s) for s in ALL_STATUSES],
            checked_status=checked_status,
            checked_priority=request.args.getlist("priority"),
            priorities=PRIORITIES,
            f_keyword=request.args.get("keyword") or "",
            f_due_from=request.args.get("due_from") or "",
            f_due_to=request.args.get("due_to") or "",
            sort_key=sort_key,
            sort_dir=sort_dir,
            sort_links={
                "due_at": sort_link("due_at"),
                "priority": sort_link("priority"),
                "progress": sort_link("progress"),
            },
            export_urls={
                "csv": url_for("tasks_export") + _build_query(request.args, drop=("page", "focus"), format="csv"),
                "json": url_for("tasks_export") + _build_query(request.args, drop=("page", "focus"), format="json"),
            },
            page_links={
                "prev": url_for("tasks") + _build_query(request.args, drop=("page", "focus"), **{"page": pagination["page"] - 1}),
                "next": url_for("tasks") + _build_query(request.args, drop=("page", "focus"), **{"page": pagination["page"] + 1}),
            },
            next_query=request.query_string.decode()[:600],
            urls={
                "complete": url_for("task_complete", task_id="__ID__"),
                "delay": url_for("task_delay", task_id="__ID__"),
                "cancel": url_for("task_cancel", task_id="__ID__"),
                "progress": url_for("task_progress", task_id="__ID__"),
                "edit": url_for("task_edit", task_id="__ID__"),
                "delete": url_for("task_delete", task_id="__ID__"),
                "undo": url_for("task_undo", feedback_id="__ID__"),
                "create": url_for("task_create"),
            },
        )

    # ------------------------------------------------------------------
    # B1 · 新建 / 编辑
    # ------------------------------------------------------------------
    @app.post("/tasks/create")
    def task_create():
        payload = request.get_json(silent=True)
        is_json = payload is not None
        data = payload if is_json else request.form

        title = str(data.get("title") or "").strip()
        detail = str(data.get("detail") or "").strip()
        priority = str(data.get("priority") or "中").strip()
        plan_id = str(data.get("plan_id") or "").strip()
        errors = []
        if not title:
            errors.append("任务标题不能为空。")
        elif len(title) > TITLE_LIMIT:
            errors.append("任务标题不能超过 %d 个字符。" % TITLE_LIMIT)
        if len(detail) > DETAIL_LIMIT:
            errors.append("任务描述不能超过 %d 个字符。" % DETAIL_LIMIT)
        if priority not in PRIORITIES:
            errors.append("优先级无效。")
        try:
            estimate = int(data.get("estimate_minutes") or 60)
        except (TypeError, ValueError):
            estimate = None
        if estimate is None or estimate <= 0 or estimate > ESTIMATE_LIMIT:
            errors.append("预计耗时必须为大于 0 的整数。")
        due = None
        if str(data.get("due_at") or "").strip():
            due = llm.parse_dt(data.get("due_at"))
            if due is None:
                errors.append("截止时间格式无效。")

        def fail(messages):
            if is_json:
                return jsonify({"ok": False, "message": messages[0], "errors": messages}), 400
            for message in messages:
                flash(message, "error")
            return redirect(url_for("tasks") + _next_suffix())

        if errors:
            return fail(errors)

        now = _now()
        with store_lock:
            store = read_store()
            if plan_id and not any(p.get("plan_id") == plan_id for p in store.get("plans", [])):
                plan_id = ""
            task_id = _next_seq_id(store.setdefault("tasks", []), "task_id", "T")
            task = {
                "task_id": task_id,
                "plan_id": plan_id,
                "todo_id": "",
                "parent_task_id": "",
                "title": title,
                "detail": detail,
                "priority": priority,
                "estimate_minutes": estimate,
                "due_at": llm.iso(due) if due else "",
                "scheduled_start_at": "",
                "planned_finish_at": "",
                "status": "待安排",
                "progress": 0,
                "depends_on": [],
                "delay_count": 0,
                "delay_reason": "",
                "completed_at": "",
                "is_demo": False,
                "created_at": llm.iso(now),
                "updated_at": llm.iso(now),
            }
            store["tasks"].append(task)
            fb.write_log(store, task_id, "新建", "新建任务「%s」" % title, now)
            write_store(store)

        message = "已新建任务 %s（待安排，可到智能排期生成安排）。" % task_id
        if is_json:
            return jsonify({"ok": True, "task_id": task_id, "message": message})
        flash(message, "success")
        return redirect(url_for("tasks") + _next_suffix())

    @app.post("/tasks/<task_id>/edit")
    def task_edit(task_id):
        payload = request.get_json(silent=True)
        is_json = payload is not None
        data = payload if is_json else request.form

        title = str(data.get("title") or "").strip()
        detail = str(data.get("detail") or "").strip()
        priority = str(data.get("priority") or "").strip()
        errors = []
        if not title:
            errors.append("任务标题不能为空。")
        elif len(title) > TITLE_LIMIT:
            errors.append("任务标题不能超过 %d 个字符。" % TITLE_LIMIT)
        if len(detail) > DETAIL_LIMIT:
            errors.append("任务描述不能超过 %d 个字符。" % DETAIL_LIMIT)
        if priority not in PRIORITIES:
            errors.append("优先级无效。")
        try:
            estimate = int(data.get("estimate_minutes"))
        except (TypeError, ValueError):
            estimate = None
        if estimate is None or estimate <= 0 or estimate > ESTIMATE_LIMIT:
            errors.append("预计耗时必须为大于 0 的整数。")
        due_raw = str(data.get("due_at") or "").strip()
        due = llm.parse_dt(due_raw) if due_raw else None
        if due_raw and due is None:
            errors.append("截止时间格式无效。")

        if errors:
            if is_json:
                return jsonify({"ok": False, "message": errors[0], "errors": errors}), 400
            for message in errors:
                flash(message, "error")
            return redirect(url_for("tasks") + _next_suffix())

        now = _now()
        with store_lock:
            store = read_store()
            task = _find_task(store, task_id)
            if task is None:
                if is_json:
                    return jsonify({"ok": False, "message": "任务不存在或已被删除。"}), 404
                flash("任务不存在或已被删除。", "error")
                return redirect(url_for("tasks") + _next_suffix())
            task["title"] = title
            task["detail"] = detail
            task["priority"] = priority
            task["estimate_minutes"] = estimate
            task["due_at"] = llm.iso(due) if due else ""
            task["updated_at"] = llm.iso(now)
            fb.write_log(store, task_id, "编辑", "更新任务「%s」" % title, now)
            write_store(store)

        if is_json:
            return jsonify({"ok": True, "message": "任务已保存。"})
        flash("任务已保存。", "success")
        return redirect(url_for("tasks") + _next_suffix())

    # ------------------------------------------------------------------
    # B1 + B4 · 完成 / 延期 / 取消 / 进度
    # ------------------------------------------------------------------
    @app.post("/tasks/<task_id>/complete")
    def task_complete(task_id):
        payload = request.get_json(silent=True)
        is_json = payload is not None
        comment = str((payload.get("comment") if is_json else request.form.get("comment")) or "").strip()
        if len(comment) > REASON_LIMIT:
            return _respond(False, "备注不能超过 %d 个字符。" % REASON_LIMIT, 400)

        now = _now()
        with store_lock:
            store = read_store()
            task = _find_task(store, task_id)
            if task is None:
                return _respond(False, "任务不存在或已被删除。", 404)
            if task.get("status") == "已完成":
                return _respond(True, "任务已经是「已完成」状态。", status="已完成", status_key="done")
            if task.get("status") == "已取消":
                return _respond(False, "已取消的任务不能标记完成，请先重新打开。", 400)
            feedback = fb.apply_complete(store, task, now, comment)
            write_store(store)
            result = {
                "status": "已完成",
                "status_key": "done",
                "feedback_id": feedback["feedback_id"],
            }
        return _respond(True, "已完成「%s」，反馈已记录，可撤销。" % (task.get("title") or task_id), **result)

    @app.post("/tasks/<task_id>/delay")
    def task_delay(task_id):
        payload = request.get_json(silent=True)
        is_json = payload is not None
        reason = str((payload.get("delay_reason") if is_json else request.form.get("delay_reason")) or "").strip()
        if not reason:
            return _respond(False, "延期原因不能为空（必填），请说明延期原因后再提交。", 400)
        if len(reason) > REASON_LIMIT:
            return _respond(False, "延期原因不能超过 %d 个字符。" % REASON_LIMIT, 400)

        now = _now()
        with store_lock:
            store = read_store()
            task = _find_task(store, task_id)
            if task is None:
                return _respond(False, "任务不存在或已被删除。", 404)
            if task.get("status") in ("已完成", "已取消"):
                return _respond(False, "已完成或已取消的任务不能延期，请先重新打开。", 400)
            feedback = fb.apply_delay(store, task, reason, now)
            write_store(store)
            result = {
                "status": "已延期",
                "status_key": "delayed",
                "delay_count": task.get("delay_count"),
                "feedback_id": feedback["feedback_id"],
            }
        return _respond(True, "已记录延期（第 %s 次）：%s，可撤销。" % (result["delay_count"], reason), **result)

    @app.post("/tasks/<task_id>/cancel")
    def task_cancel(task_id):
        payload = request.get_json(silent=True)
        is_json = payload is not None
        comment = str((payload.get("comment") if is_json else request.form.get("comment")) or "").strip()
        if len(comment) > REASON_LIMIT:
            return _respond(False, "备注不能超过 %d 个字符。" % REASON_LIMIT, 400)

        now = _now()
        with store_lock:
            store = read_store()
            task = _find_task(store, task_id)
            if task is None:
                return _respond(False, "任务不存在或已被删除。", 404)
            if task.get("status") == "已取消":
                return _respond(True, "任务已经是「已取消」状态。", status="已取消", status_key="canceled")
            if task.get("status") == "已完成":
                return _respond(False, "已完成的任务请使用「撤销完成」，不能直接取消。", 400)
            feedback = fb.apply_cancel(store, task, comment, now)
            write_store(store)
            result = {"status": "已取消", "status_key": "canceled", "feedback_id": feedback["feedback_id"]}
        return _respond(True, "已取消「%s」，反馈已记录，可撤销。" % (task.get("title") or task_id), **result)

    @app.post("/tasks/<task_id>/progress")
    def task_progress(task_id):
        payload = request.get_json(silent=True)
        is_json = payload is not None
        raw_value = payload.get("progress") if is_json else request.form.get("progress")
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = None
        if value is None or value < 0 or value > 100:
            return _respond(False, "进度必须是 0-100 的整数。", 400)

        now = _now()
        with store_lock:
            store = read_store()
            task = _find_task(store, task_id)
            if task is None:
                return _respond(False, "任务不存在或已被删除。", 404)
            status = task.get("status")
            if status == "已取消":
                return _respond(False, "已取消的任务不能调整进度。", 400)

            feedback = None
            if value == 100 and status != "已完成":
                feedback = fb.apply_complete_by_progress(store, task, now)
            else:
                if status == "已完成" and value < 100:
                    task["status"] = "进行中"
                    task["completed_at"] = ""
                    fb.write_log(store, task_id, "重开", "进度回退到 %d%%，任务重新打开" % value, now)
                elif status in ("待安排", "待执行") and 0 < value < 100:
                    task["status"] = "进行中"
                task["progress"] = value
                task["updated_at"] = llm.iso(now)
            write_store(store)
            result = {
                "progress": int(task.get("progress") or 0),
                "status": task.get("status"),
                "status_key": STATUS_KEY.get(task.get("status"), "ready"),
                "completed": task.get("status") == "已完成",
                "feedback_id": feedback["feedback_id"] if feedback else "",
            }

        if is_json:
            body = {"ok": True, "message": "进度已更新为 %d%%。" % result["progress"]}
            body.update(result)
            return jsonify(body)
        flash("进度已更新为 %d%%。" % result["progress"], "success")
        return redirect(url_for("tasks") + _next_suffix())

    # ------------------------------------------------------------------
    # B1 · 删除（二次确认由页面承担，删除留痕）
    # ------------------------------------------------------------------
    @app.post("/tasks/<task_id>/delete")
    def task_delete(task_id):
        now = _now()
        with store_lock:
            store = read_store()
            task = _find_task(store, task_id)
            if task is None:
                return _respond(False, "任务不存在或已被删除。", 404)
            title = task.get("title") or task_id
            store["tasks"] = [t for t in store.get("tasks", []) if t.get("task_id") != task_id]
            for other in store.get("tasks", []):
                deps = other.get("depends_on") or []
                if task_id in deps:
                    other["depends_on"] = [d for d in deps if d != task_id]
            fb.write_log(store, task_id, "删除", "删除任务「%s」（历史反馈与提醒保留）" % title, now)
            write_store(store)

        message = "已删除「%s」。历史反馈与提醒仍保留，可追溯。" % title
        if request.get_json(silent=True) is not None:
            return jsonify({"ok": True, "message": message})
        flash(message, "success")
        return redirect(url_for("tasks") + _next_suffix())

    # ------------------------------------------------------------------
    # B4 · 撤销反馈
    # ------------------------------------------------------------------
    @app.post("/tasks/undo/<feedback_id>")
    def task_undo(feedback_id):
        now = _now()
        with store_lock:
            store = read_store()
            feedback = fb.find(store, feedback_id)
            if feedback is None:
                return _respond(False, "撤销失败：该反馈记录不存在或已被撤销。", 404)
            task = _find_task(store, feedback.get("task_id"))
            if task is None:
                return _respond(False, "撤销失败：关联任务已被删除。", 400)
            if not feedback.get("prev_state"):
                return _respond(False, "撤销失败：该历史记录没有可恢复的前态快照。", 400)
            kind = feedback.get("feedback_type")
            restored = fb.undo(store, feedback, now)
            fb.write_log(store, task.get("task_id"), "撤销", "撤销「%s」反馈，任务恢复为「%s」" % (kind, restored), now)
            write_store(store)

        message = "已撤销「%s」反馈，任务恢复为「%s」。" % (kind, restored)
        if request.get_json(silent=True) is not None:
            return jsonify({"ok": True, "message": message, "status": restored})
        flash(message, "success")
        return redirect(url_for("tasks") + _next_suffix())

    # ------------------------------------------------------------------
    # B1 · 导出（跟随当前筛选）
    # ------------------------------------------------------------------
    @app.get("/tasks/export")
    def tasks_export():
        store = read_store()
        now = _now()
        rows = _sort_tasks(_filter_tasks(store, request.args, now), request.args)
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        fmt = (request.args.get("format") or "csv").lower()

        if fmt == "json":
            payload = []
            for task in rows:
                payload.append({label: task.get(key, "") for label, key in TASK_EXPORT_COLUMNS})
            return Response(
                json.dumps({"count": len(payload), "items": payload}, ensure_ascii=False, indent=2),
                mimetype="application/json; charset=utf-8",
                headers={"Content-Disposition": "attachment; filename=tasks_%s.json" % stamp},
            )

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([label for label, _ in TASK_EXPORT_COLUMNS])
        for task in rows:
            writer.writerow([task.get(key, "") for _, key in TASK_EXPORT_COLUMNS])
        return Response(
            "\ufeff" + buffer.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=tasks_%s.csv" % stamp},
        )

    # ------------------------------------------------------------------
    # B2 · 提醒中心
    # ------------------------------------------------------------------
    @app.get("/reminders")
    def reminders():
        store = read_store()
        settings = read_settings()
        now = _now()
        task_index = {t.get("task_id"): t for t in store.get("tasks", [])}

        raw_status = request.args.getlist("status")
        raw_channel = request.args.getlist("channel")
        statuses = [s for s in raw_status if s in REMINDER_STATUSES]
        channels = [c for c in raw_channel if c in CHANNELS]

        rows = []
        for reminder in store.get("reminders", []):
            if statuses and reminder.get("status") not in statuses:
                continue
            if channels and reminder.get("channel") not in channels:
                continue
            rows.append(reminder)

        # 未读置顶：先按触发时间倒序，再稳定排序把未读提到最前
        rows = sorted(rows, key=lambda r: r.get("trigger_at") or "", reverse=True)
        rows = sorted(rows, key=lambda r: 0 if r.get("status") == "未读" else 1)

        all_views = [_serialize_reminder(r, task_index, now) for r in
                     store.get("reminders", [])]
        unread = sum(1 for v in all_views if v["is_unread"])
        risk_unread = [v for v in all_views if v["is_unread"] and v["is_risk"]]
        today = now.date().isoformat()
        today_count = sum(1 for v in all_views if (v["trigger_at"] or "")[:10] == today)

        page_rows, pagination = _paginate(rows, request.args)
        reminder_views = [_serialize_reminder(r, task_index, now) for r in page_rows]

        scan_state = store.get("scan_state") or {}
        last_scan = llm.parse_dt(scan_state.get("last_scan_at") or "")

        return render_template(
            "pages/reminders.html",
            active_page="reminders",
            page_name="提醒中心",
            settings=settings,
            reminders=reminder_views,
            stats={
                "total": len(all_views),
                "unread": unread,
                "risk": len(risk_unread),
                "today": today_count,
                "last_scan_disp": last_scan.strftime("%m-%d %H:%M") if last_scan else "尚未执行",
                "last_created": int(scan_state.get("last_created") or 0),
            },
            risk_preview=risk_unread[:3],
            status_chips=[(s, s) for s in REMINDER_STATUSES],
            checked_status=raw_status,
            channels=CHANNELS,
            checked_channel=raw_channel,
            pagination=pagination,
            page_links={
                "prev": url_for("reminders") + _build_query(request.args, drop=("page",), **{"page": pagination["page"] - 1}),
                "next": url_for("reminders") + _build_query(request.args, drop=("page",), **{"page": pagination["page"] + 1}),
            },
            next_query=request.query_string.decode()[:600],
            urls={
                "read": url_for("reminder_read", reminder_id="__ID__"),
                "unread": url_for("reminder_unread", reminder_id="__ID__"),
                "ignore": url_for("reminder_ignore", reminder_id="__ID__"),
                "scan": url_for("reminders_scan"),
                "read_all": url_for("reminders_read_all"),
            },
        )

    @app.post("/reminders/<reminder_id>/read")
    def reminder_read(reminder_id):
        return _set_reminder_status(reminder_id, "已读", "已标记为已读。")

    @app.post("/reminders/<reminder_id>/unread")
    def reminder_unread(reminder_id):
        return _set_reminder_status(reminder_id, "未读", "已重新标记为未读。")

    @app.post("/reminders/<reminder_id>/ignore")
    def reminder_ignore(reminder_id):
        return _set_reminder_status(reminder_id, "已忽略", "已忽略该提醒。")

    def _set_reminder_status(reminder_id, status, message):
        now = _now()
        with store_lock:
            store = read_store()
            reminder = _find_reminder(store, reminder_id)
            if reminder is None:
                return _respond_reminders(False, "提醒不存在或已被清理。", 404)
            reminder["status"] = status
            reminder["updated_at"] = llm.iso(now)
            write_store(store)
        return _respond_reminders(True, message)

    @app.post("/reminders/read-all")
    def reminders_read_all():
        now = _now()
        with store_lock:
            store = read_store()
            count = 0
            for reminder in store.get("reminders", []):
                if reminder.get("status") == "未读":
                    reminder["status"] = "已读"
                    reminder["updated_at"] = llm.iso(now)
                    count += 1
            if count:
                write_store(store)
        message = ("已将 %d 条未读提醒标记为已读。" % count) if count else "当前没有未读提醒。"
        return _respond_reminders(True, message)

    @app.get("/reminders/export")
    def reminders_export():
        store = read_store()
        now = _now()
        task_index = {t.get("task_id"): t for t in store.get("tasks", [])}
        statuses = [s for s in request.args.getlist("status") if s in REMINDER_STATUSES]
        channels = [c for c in request.args.getlist("channel") if c in CHANNELS]
        rows = []
        for reminder in store.get("reminders", []):
            if statuses and reminder.get("status") not in statuses:
                continue
            if channels and reminder.get("channel") not in channels:
                continue
            rows.append(_serialize_reminder(reminder, task_index, now))
        rows.sort(key=lambda item: item.get("trigger_at") or "", reverse=True)

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([label for label, _ in REMINDER_EXPORT_COLUMNS])
        for row in rows:
            writer.writerow([row.get(key, "") for _, key in REMINDER_EXPORT_COLUMNS])
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        return Response(
            "\ufeff" + buffer.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=reminders_%s.csv" % stamp},
        )
