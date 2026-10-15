"""工作线 A · 目标与派工线（2/2）：智能派工（A4）。

对应软需：A4 F-116~F-124（验收锚点 AC-07~AC-11）
归属文件：backend/assign.py · pages/assign.*

核心规则：
- 派工对象：挂在「已发布 / 执行中」目标下的任务；无执行人的任务进入「待派工」清单；
- 饱和度（F-117/F-124）：当前负荷 = 在办任务（待开始/进行中/阻塞）计划工时按权重折算，
  saturation = 负荷 / 基准容量 × 100；≤90 绿（推荐）/ 90~110 黄（谨慎）/ >110 红（拦截）；
- 强制下发（F-121）：>110 必须勾选强制下发 + 理由，写审计日志并在任务上标记超载；
- AI 只出建议：派工分析（关联度 / 风险）与验收口径可由 LLM 生成，失败时走确定性规则兜底；
- 计划项（F-120）：时间窗按权重确定性拆分（不交给模型算日期），工时合计 = 任务计划工时；
- 落库闭环：读取 → 校验 → 加锁写 → 刷新展示；失败不写任何数据；写接口支持 Idempotency-Key。

接口契约：
- GET  /assign                         派工工作台（?task_id= 选中任务；?status= / ?objective_id= 过滤）
- POST /api/task/create                新建派工任务（挂已发布 / 执行中目标）
- POST /api/assign/check               饱和度试算（三色 + 三档建议 + 换人推荐 2 人）
- POST /api/assign/analyze             AI 派工分析（关联度 + 风险，规则兜底）
- POST /api/assign/plan                智能计划项（时间窗拆分 + 验收口径，草案不落库）
- POST /api/assign/commit              派工提交（>110 拦截；override 需理由 + 审计）
- GET  /api/staff/profile              员工能力档案 + 实时饱和度
- POST /api/staff/save                 维护档案（岗位 / 能力标签 / 基准容量）
"""

from importlib import import_module

from flask import flash, jsonify, redirect, render_template, request, url_for

from backend import core

llm = import_module("backend.llm")

WORKLOAD_STATUSES = ("todo", "doing", "blocked")     # 计入负荷的在办状态
DONE_STATUSES = ("done", "cancelled")                # 不可再派工
PRIORITIES = ("高", "中", "低")
ROLE_IN_TASK = ("R", "A", "C", "I")
PLAN_HOURS_MIN = 0.5
PLAN_HOURS_MAX = 120
TASK_SOURCE = "dispatch"
ACTION_LOG_NAME = "progress_log"


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def _payload():
    return request.get_json(silent=True) or {}


def _find_task(store, task_id):
    return next((task for task in store["work_task"] if task["task_id"] == task_id), None)


def _find_objective(store, objective_id):
    return next((obj for obj in store["objective"] if obj["objective_id"] == objective_id), None)


def _user_name(store, user_id):
    user = core.user_of(store, user_id)
    return user.get("display_name") if user else user_id


def _org_name(store, org_unit_id):
    org = core.org_of(store, org_unit_id)
    return org.get("org_name") if org else org_unit_id


def _role_code(store, user):
    role = core.role_of_user(store, user["user_id"])
    return (role or {}).get("role_code") or ""


def _is_manager(store, user):
    # 统一账号体系下「管理员」= admin；兼容旧种子角色码 manager / super_admin
    return _role_code(store, user) in ("admin", "manager", "super_admin")


def _is_pure_staff(store, user):
    return _role_code(store, user) == "staff"


def _thresholds(store):
    warn = core.setting_value(store, "warn_thresholds", {"green": 90, "yellow": 110}) or {}
    try:
        green = float(warn.get("green") or 90)
    except (TypeError, ValueError):
        green = 90.0
    try:
        red = float(warn.get("yellow") or 110)
    except (TypeError, ValueError):
        red = 110.0
    try:
        limit = float(core.setting_value(store, "saturation_limit", 90) or 90)
    except (TypeError, ValueError):
        limit = 90.0
    return green, red, limit


def _level_of(saturation, green, red):
    if saturation <= green:
        return "green"
    if saturation <= red:
        return "yellow"
    return "red"


def _advice_of(level):
    return {"green": "recommend", "yellow": "caution", "red": "reject"}[level]


def _advice_text(level, after, green, red):
    if level == "green":
        return "推荐：加入后饱和度 %.1f%%，处于健康区间（≤ %.0f%%）。" % (after, green)
    if level == "yellow":
        return "谨慎：加入后饱和度 %.1f%%，接近上限（> %.0f%%），请确认时间冲突。" % (after, green)
    return "不建议：加入后饱和度 %.1f%%，超过 %.0f%% 上限，提交将被拦截（需强制下发）。" % (after, red)


def _log(store, task_id, note, operator_id, progress=None, source="assign"):
    store[ACTION_LOG_NAME].insert(0, {
        "log_id": core.next_id(store, ACTION_LOG_NAME, "LOG", 4),
        "target_type": "task", "target_id": task_id,
        "progress": progress, "note": note, "operator_id": operator_id,
        "logged_at": core.iso(), "source": source,
    })


# ---------------------------------------------------------------------------
# 负荷与饱和度（F-117 / F-124）
# ---------------------------------------------------------------------------

def _load_of(store, user_id, exclude_task_id=""):
    """当前负荷：在办任务计划工时按执行权重折算；可剔除指定任务（改派场景）。"""
    total = 0.0
    for task in store["work_task"]:
        if task["task_id"] == exclude_task_id:
            continue
        if task.get("status") not in WORKLOAD_STATUSES:
            continue
        for assignee in task.get("assignees") or []:
            if assignee.get("user_id") != user_id:
                continue
            share = float(assignee.get("weight") or 100) / 100.0
            total += float(task.get("plan_hours") or 0) * share
    return round(total, 1)


def _base_capacity(store, user_id):
    profile = next((row for row in store["staff_profile"] if row.get("user_id") == user_id), None)
    try:
        base = float((profile or {}).get("base_capacity_hours") or 480)
    except (TypeError, ValueError):
        base = 480.0
    return base if base > 0 else 480.0


def _profile_of(store, user_id):
    return next((row for row in store["staff_profile"] if row.get("user_id") == user_id), None)


def _open_titles(store, user_id, limit=5):
    rows = []
    for task in store["work_task"]:
        if task.get("status") not in WORKLOAD_STATUSES:
            continue
        if any(assignee.get("user_id") == user_id for assignee in task.get("assignees") or []):
            rows.append(task.get("title") or task["task_id"])
    return rows[:limit]


def _member_row(store, user_id, green, red, new_hours=None, exclude_task_id=""):
    """单个成员的饱和度视图；new_hours 给出时计算加入后的饱和度。"""
    user = core.user_of(store, user_id)
    profile = _profile_of(store, user_id)
    load = _load_of(store, user_id, exclude_task_id=exclude_task_id)
    base = _base_capacity(store, user_id)
    before = core.f1(load / base * 100) if base else 0.0
    after = core.f1((load + float(new_hours or 0)) / base * 100) if base else before
    level = _level_of(after if new_hours is not None else before, green, red)
    return {
        "user_id": user_id,
        "name": (user or {}).get("display_name") or user_id,
        "org_name": _org_name(store, (user or {}).get("org_unit_id")),
        "position_name": core.dict_name(store, "position", (profile or {}).get("position_code") or ""),
        "ability_tags": list((profile or {}).get("ability_tags") or []),
        "open_count": len(_open_titles(store, user_id, limit=99)),
        "open_titles": _open_titles(store, user_id),
        "load_hours": load,
        "base_hours": base,
        "saturation_before": before,
        "saturation_after": after if new_hours is not None else before,
        "level": level,
        "advice": _advice_of(level),
        "advice_text": _advice_text(level, after if new_hours is not None else before, green, red),
    }


def _candidate_ids(store, user):
    if _is_pure_staff(store, user):
        return [user["user_id"]]
    scoped = core.scope_user_ids(store, user)
    rows = []
    for member in store["user"]:
        if member.get("status") != "启用":
            continue
        if scoped is not None and member["user_id"] not in scoped:
            continue
        rows.append(member)
    rows.sort(key=lambda item: (item.get("org_unit_id") or "", item["user_id"]))
    return [member["user_id"] for member in rows]


def _refresh_profiles(store):
    """派工后刷新档案的负荷缓存（展示口径与服务端一致）。"""
    for member in store["user"]:
        profile = _profile_of(store, member["user_id"])
        if profile is None:
            continue
        load = _load_of(store, member["user_id"])
        base = _base_capacity(store, member["user_id"])
        profile["current_load_hours"] = load
        profile["saturation"] = core.f1(load / base * 100) if base else 0.0
        profile["updated_at"] = core.iso()


# ---------------------------------------------------------------------------
# 数据范围
# ---------------------------------------------------------------------------

def _visible_tasks(store, user):
    if _is_pure_staff(store, user):
        return [task for task in store["work_task"]
                if any(assignee.get("user_id") == user["user_id"] for assignee in task.get("assignees") or [])]
    org_ids = core.user_org_ids(store, user)
    if org_ids is None:
        return list(store["work_task"])
    scoped = set(core.scope_user_ids(store, user) or [])
    rows = []
    for task in store["work_task"]:
        obj = _find_objective(store, task.get("objective_id") or "")
        by_org = obj is not None and obj.get("org_unit_id") in org_ids
        by_assignee = any(assignee.get("user_id") in scoped for assignee in task.get("assignees") or [])
        if by_org or by_assignee:
            rows.append(task)
    return rows


def _task_or_fail(store, user, task_id):
    task = _find_task(store, task_id)
    if task is None:
        core.fail(1003, "任务不存在或已删除。")
    visible_ids = {row["task_id"] for row in _visible_tasks(store, user)}
    if task_id not in visible_ids:
        core.fail(1003, "任务不存在或不在你的数据范围内。")
    return task


def _require_dispatchable(store, user, task):
    if not _is_manager(store, user):
        core.fail(1008, "当前角色没有派工权限。")
    if task.get("status") in DONE_STATUSES:
        core.fail(1004, "任务当前状态为「%s」，不可再派工。" % core.TASK_STATUS.get(task.get("status"), task.get("status")))
    obj = _find_objective(store, task.get("objective_id") or "")
    if obj is None:
        core.fail(1005, "任务未挂接目标，无法派工。")
    if obj.get("status") not in ("published", "executing"):
        core.fail(1005, "派工对象必须挂在已发布 / 执行中的目标下（当前目标状态「%s」）。"
                  % core.OBJECTIVE_STATUS.get(obj.get("status"), obj.get("status")))


# ---------------------------------------------------------------------------
# 计划项：时间窗拆分 + 验收口径（F-120）
# ---------------------------------------------------------------------------

def _split_windows(task, assignees):
    """按权重把任务时间窗拆成顺序衔接的每日区间（确定性规则，不交给模型）。"""
    start = core.parse_dt(task.get("plan_start"))
    end = core.parse_dt(task.get("plan_end"))
    if start is None or end is None or end < start:
        return []
    total_days = (end - start).days + 1
    weights = [float(item.get("weight") or 0) for item in assignees]
    count = len(assignees)
    windows = []
    if total_days <= count:
        # 窗口过短无法拆分：全员共享同一窗口（工时仍按权重拆分）
        for _ in assignees:
            windows.append((start, end))
        return windows
    remaining = total_days
    cursor = start
    for index, weight in enumerate(weights):
        if index == count - 1:
            alloc = remaining
        else:
            remaining_slots = count - index - 1
            planned = int(round(total_days * weight / 100.0))
            alloc = max(1, min(remaining - remaining_slots, planned))
        if alloc < 1:
            alloc = 1
        window_end = cursor + core.timedelta(days=alloc - 1)
        windows.append((cursor, window_end))
        cursor = window_end + core.timedelta(days=1)
        remaining -= alloc
    return windows


def _rule_plan_items(task, assignees):
    windows = _split_windows(task, assignees)
    hours_total = float(task.get("plan_hours") or 0)
    counts = len(assignees)
    items = []
    allocated = 0.0
    for index, assignee in enumerate(assignees):
        if index == counts - 1:
            hours = round(hours_total - allocated, 1)
        else:
            hours = round(hours_total * float(assignee.get("weight") or 0) / 100.0, 1)
            allocated += hours
        start, end = windows[index] if index < len(windows) else (None, None)
        items.append({
            "user_id": assignee["user_id"],
            "name": "",
            "plan_hours": hours,
            "start": start.strftime("%Y-%m-%d") if start else "",
            "end": end.strftime("%Y-%m-%d") if end else "",
            "acceptance": "交付「%s」对应产出并通过验收；计划 %.1fh，%s 内完成并同步进度。" % (
                task.get("title") or "", hours,
                ("%s ~ %s" % (start.strftime("%m-%d"), end.strftime("%m-%d"))) if start and end else "任务窗口"),
            "edited": False,
        })
    return items


def _build_plan_items(store, task, assignees):
    """LLM 补验收口径 + 规则兜底；时间窗始终由规则拆分。"""
    items = _rule_plan_items(task, assignees)
    names = {item["user_id"]: _user_name(store, item["user_id"]) for item in assignees}
    for item in items:
        item["name"] = names.get(item["user_id"], item["user_id"])
    engine, degraded = "rule", True
    prompt_assignees = [{"user_id": item["user_id"], "name": names.get(item["user_id"], "")} for item in assignees]
    result = llm.llm_assign_plan(task, prompt_assignees)
    if result:
        accepted = {row["user_id"]: row["acceptance"] for row in result.get("items") or []}
        if accepted:
            engine, degraded = "llm", False
            for item in items:
                if accepted.get(item["user_id"]):
                    item["acceptance"] = accepted[item["user_id"]]
    return items, engine, degraded


# ---------------------------------------------------------------------------
# 校验收集
# ---------------------------------------------------------------------------

def _collect_assignees(store, user, raw, strict=True):
    """解析执行人清单；strict=False（试算 / 分析与计划草案）不强制权重合计 = 100。"""
    if not isinstance(raw, list) or not raw:
        core.fail(1001, "请至少选择一位执行人。")
    if len(raw) > 8:
        core.fail(1002, "单任务执行人最多 8 人。")
    seen = set()
    rows = []
    for item in raw:
        item = item or {}
        user_id = str(item.get("user_id") or "").strip()
        if not user_id:
            core.fail(1001, "执行人参数缺少 user_id。")
        if user_id in seen:
            core.fail(1001, "执行人重复：%s。" % user_id)
        member = core.user_of(store, user_id)
        if member is None or member.get("status") != "启用":
            core.fail(1005, "执行人必须是启用账号（%s）。" % user_id)
        if not core.in_scope(store, user, user_id):
            core.fail(1003, "执行人 %s 不在你的数据范围内。" % user_id)
        try:
            weight = round(float(item.get("weight") or 0), 1)
        except (TypeError, ValueError):
            core.fail(1001, "执行人权重必须是数字。")
        if weight <= 0:
            core.fail(1001, "执行人权重必须大于 0。")
        role = str(item.get("role_in_task") or "R").strip().upper()
        if role not in ROLE_IN_TASK:
            role = "R"
        seen.add(user_id)
        rows.append({"user_id": user_id, "role_in_task": role, "weight": weight,
                     "wants_owner": bool(item.get("is_owner"))})
    total = round(sum(row["weight"] for row in rows), 1)
    if strict and abs(total - 100) > 0.1:
        core.fail(1005, "执行人权重合计须为 100%%（当前 %.1f%%）。" % total, {"weight_sum": total})
    # 主责：显式指定优先（界面单选），未指定时取权重最大者
    flagged = [row["user_id"] for row in rows if row.pop("wants_owner")]
    owner_id = flagged[0] if flagged else max(rows, key=lambda row: row["weight"])["user_id"]
    for row in rows:
        row["is_owner"] = row["user_id"] == owner_id
        row["name"] = _user_name(store, row["user_id"])
    return rows, owner_id


def _saturation_preview(store, task, assignees):
    """加入本次派工后的饱和度清单（剔除任务原有折算，避免改派重复计数）。"""
    green, red, limit = _thresholds(store)
    hours = float(task.get("plan_hours") or 0)
    rows = []
    for assignee in assignees:
        add = hours * float(assignee.get("weight") or 0) / 100.0
        row = _member_row(store, assignee["user_id"], green, red, new_hours=add, exclude_task_id=task["task_id"])
        rows.append(row)
    overload = [row for row in rows if row["saturation_after"] > red]
    warnings = [row for row in rows if row["level"] == "yellow"]
    return rows, overload, warnings


def _parallel_progress(store, task):
    """并行进度汇总（F-123）：按执行人权重加权；未填报者不参与加权并单独提示。"""
    assignees = task.get("assignees") or []
    if len(assignees) < 2:
        return None
    rows = []
    for assignee in assignees:
        logs = [log for log in store["progress_log"]
                if log.get("target_type") == "task" and log.get("target_id") == task["task_id"]
                and log.get("operator_id") == assignee.get("user_id")
                and log.get("progress") is not None]
        latest = max(logs, key=lambda item: item.get("logged_at") or "") if logs else None
        rows.append({
            "user_id": assignee.get("user_id"),
            "name": _user_name(store, assignee.get("user_id")),
            "weight": float(assignee.get("weight") or 0),
            "is_owner": bool(assignee.get("is_owner")),
            "progress": latest.get("progress") if latest else None,
            "logged_at": ((latest or {}).get("logged_at") or "").replace("T", " ")[:16],
        })
    reporters = [row for row in rows if row["progress"] is not None]
    weighted = None
    if reporters:
        weight_sum = sum(row["weight"] for row in reporters)
        if weight_sum > 0:
            weighted = core.f1(sum(row["progress"] * row["weight"] for row in reporters) / weight_sum)
    return {
        "task_progress": float(task.get("progress") or 0),
        "weighted_progress": weighted,
        "rows": rows,
        "missing": [row["name"] for row in rows if row["progress"] is None],
    }


def _collect_plan_items(store, task, assignees, raw):
    items = raw if isinstance(raw, list) and raw else None
    if items is None:
        built, _, _ = _build_plan_items(store, task, assignees)
        return built
    allowed = {row["user_id"] for row in assignees}
    rows = []
    for item in items:
        item = item or {}
        user_id = str(item.get("user_id") or "").strip()
        if user_id not in allowed:
            core.fail(1001, "计划项执行人必须是本次派工的执行人（%s）。" % user_id)
        try:
            hours = round(float(item.get("plan_hours") or 0), 1)
        except (TypeError, ValueError):
            core.fail(1001, "计划项工时必须是数字。")
        if not PLAN_HOURS_MIN <= hours <= PLAN_HOURS_MAX:
            core.fail(1002, "单条计划项工时须在 %.1f~%d 小时之间。" % (PLAN_HOURS_MIN, PLAN_HOURS_MAX))
        start = str(item.get("start") or "").strip()
        end = str(item.get("end") or "").strip()
        start_dt = core.parse_dt(start)
        end_dt = core.parse_dt(end)
        if start_dt is None or end_dt is None or end_dt < start_dt:
            core.fail(1002, "计划项时间窗应为合法日期且开始不晚于结束。")
        window_start = task.get("plan_start") or ""
        window_end = task.get("plan_end") or ""
        if window_start and start_dt.date().isoformat() < window_start:
            core.fail(1005, "计划项时间窗必须落在任务窗口 %s ~ %s 内。" % (window_start, window_end))
        if window_end and end_dt.date().isoformat() > window_end:
            core.fail(1005, "计划项时间窗必须落在任务窗口 %s ~ %s 内。" % (window_start, window_end))
        rows.append({
            "user_id": user_id,
            "plan_hours": hours,
            "start": start_dt.date().isoformat(),
            "end": end_dt.date().isoformat(),
            "acceptance": str(item.get("acceptance") or "").strip()[:200],
            "edited": bool(item.get("edited")),
        })
    total = round(sum(row["plan_hours"] for row in rows), 1)
    task_hours = round(float(task.get("plan_hours") or 0), 1)
    if abs(total - task_hours) > 0.1:
        core.fail(1005, "计划项工时合计 %.1fh 与任务计划工时 %.1fh 不一致。" % (total, task_hours), {"plan_hours": task_hours})
    return rows


# ---------------------------------------------------------------------------
# 路由注册
# ---------------------------------------------------------------------------

def register(app, services):

    # ------------------------------------------------------------- 页面 A4
    @app.get("/assign")
    @core.require_menu("assign")
    def assign():
        store = core.read_store()
        user = core.current_user(store)
        if user is None:
            return redirect(url_for("login", next=request.path))
        green, red, limit = _thresholds(store)

        rows = _visible_tasks(store, user)
        filter_status = request.args.get("status", "").strip()
        filter_objective = request.args.get("objective_id", "").strip()
        if filter_status == "need_assign":
            rows = [task for task in rows if not (task.get("assignees") or [])]
        elif filter_status:
            rows = [task for task in rows if task.get("status") == filter_status]
        if filter_objective:
            rows = [task for task in rows if task.get("objective_id") == filter_objective]

        order = {"doing": 0, "blocked": 1, "todo": 2, "done": 3, "cancelled": 4}
        rows = sorted(rows, key=lambda task: (order.get(task.get("status"), 9), task.get("plan_start") or "", task["task_id"]))

        def task_row(task):
            obj = _find_objective(store, task.get("objective_id") or "")
            assignees = task.get("assignees") or []
            return {
                "task_id": task["task_id"],
                "title": task.get("title") or "",
                "status": task.get("status"),
                "status_label": core.TASK_STATUS.get(task.get("status"), task.get("status")),
                "priority": task.get("priority") or "中",
                "plan_hours": task.get("plan_hours"),
                "plan_start": task.get("plan_start") or "",
                "plan_end": task.get("plan_end") or "",
                "objective_id": task.get("objective_id") or "",
                "objective_title": (obj or {}).get("title") or "未挂接目标",
                "objective_status": (obj or {}).get("status"),
                "assignee_names": "、".join(_user_name(store, item.get("user_id")) for item in assignees),
                "need_assign": not assignees,
                "is_overload_override": bool(task.get("manual_override")),
            }

        task_list = [task_row(task) for task in rows]

        selected = None
        task_id = request.args.get("task_id", "").strip()
        if not task_id and task_list:
            need = [row for row in task_list if row["need_assign"]]
            task_id = (need or task_list)[0]["task_id"]
        if task_id:
            task = next((item for item in rows if item["task_id"] == task_id), None)
            if task is None:
                flash("任务不存在或不在当前筛选结果中。", "error")
                return redirect(url_for("assign"))
            obj = _find_objective(store, task.get("objective_id") or "")
            members = []
            if not _is_pure_staff(store, user):
                for user_id in _candidate_ids(store, user):
                    members.append(_member_row(store, user_id, green, red, exclude_task_id=task["task_id"]))
                members.sort(key=lambda row: ({"green": 0, "yellow": 1, "red": 2}[row["level"]], row["saturation_before"]))
            selected = {
                "task": dict(task),
                "objective": None if obj is None else {
                    "objective_id": obj["objective_id"], "title": obj.get("title") or "",
                    "status": obj.get("status"),
                    "status_label": core.OBJECTIVE_STATUS.get(obj.get("status"), obj.get("status")),
                    "plan_start": obj.get("plan_start") or "", "plan_end": obj.get("plan_end") or "",
                    "org_name": _org_name(store, obj.get("org_unit_id")),
                    "owner_name": _user_name(store, obj.get("owner_id")),
                },
                "assignees": [{
                    "user_id": item.get("user_id"), "name": _user_name(store, item.get("user_id")),
                    "role_in_task": item.get("role_in_task") or "R",
                    "weight": item.get("weight"), "is_owner": bool(item.get("is_owner")),
                } for item in task.get("assignees") or []],
                "plan_items": [{
                    "user_id": item.get("user_id"), "name": _user_name(store, item.get("user_id")),
                    "plan_hours": item.get("plan_hours"), "start": item.get("start") or "",
                    "end": item.get("end") or "", "acceptance": item.get("acceptance") or "",
                    "edited": bool(item.get("edited")),
                } for item in task.get("plan_items") or []],
                "parallel": _parallel_progress(store, task),
                "members": members,
            }

        objective_rows = []
        for obj in store["objective"]:
            if obj.get("status") not in ("published", "executing"):
                continue
            allowed = core.user_org_ids(store, user)
            if allowed is not None and obj.get("org_unit_id") not in allowed:
                continue
            objective_rows.append({
                "objective_id": obj["objective_id"], "title": obj.get("title") or "",
                "org_name": _org_name(store, obj.get("org_unit_id")),
                "plan_start": obj.get("plan_start") or "", "plan_end": obj.get("plan_end") or "",
            })
        objective_rows.sort(key=lambda item: item["objective_id"])

        all_rows = [_member_row(store, user_id, green, red) for user_id in _candidate_ids(store, user)] \
            if not _is_pure_staff(store, user) else []
        overloaded = [row for row in all_rows if row["level"] == "red"]
        stats = {
            "total": len(task_list),
            "need_assign": sum(1 for row in task_list if row["need_assign"]),
            "overloaded": len(overloaded),
            "doing": sum(1 for row in task_list if row["status"] == "doing"),
        }

        context = {
            "active_page": "assign",
            "page_name": "智能派工",
            "is_manager": _is_manager(store, user),
            "is_staff": _is_pure_staff(store, user),
            "task_list": task_list,
            "selected": selected,
            "objective_rows": objective_rows,
            "filter_status": filter_status,
            "filter_objective": filter_objective,
            "stats": stats,
            "thresholds": {"green": green, "red": red, "limit": limit},
            "overloaded_rows": overloaded,
            "can_dispatch": core.has_perm(store, user, "action:assign.dispatch"),
            "can_override": core.has_perm(store, user, "action:assign.override"),
            "can_analyze": core.has_perm(store, user, "action:assign.analyze"),
            "can_plan": core.has_perm(store, user, "action:assign.plan"),
            "can_check": core.has_perm(store, user, "action:assign.check"),
            "can_create_task": core.has_perm(store, user, "action:task.create"),
            "can_profile_edit": core.has_perm(store, user, "action:staff.profile_edit"),
            "priorities": PRIORITIES,
        }
        return render_template("pages/assign.html", **context)

    # ------------------------------------------------- API：新建派工任务
    @app.post("/api/task/create")
    def api_task_create():
        payload = _payload()
        cached = core.idempotency_lookup(core.client_key())
        if cached is not None:
            return jsonify(cached), 200
        store = core.read_store()
        user = core.api_require(store, "action:task.create")
        if not _is_manager(store, user):
            core.fail(1008, "当前角色没有派工任务创建权限。")

        title = str(payload.get("title") or "").strip()
        if not title:
            core.fail(1001, "任务标题不能为空。")
        if len(title) > 100:
            core.fail(1002, "任务标题不能超过 100 个字符。")
        detail = str(payload.get("detail") or "").strip()
        if len(detail) > 500:
            core.fail(1002, "任务说明不能超过 500 个字符。")
        objective_id = str(payload.get("objective_id") or "").strip()
        if not objective_id:
            core.fail(1001, "派工任务必须挂接目标（objective_id）。")
        obj = _find_objective(store, objective_id)
        if obj is None:
            core.fail(1003, "目标不存在。")
        allowed = core.user_org_ids(store, user)
        if allowed is not None and obj.get("org_unit_id") not in allowed:
            core.fail(1003, "目标不在你的数据范围内。")
        if obj.get("status") not in ("published", "executing"):
            core.fail(1005, "任务只能挂在已发布 / 执行中的目标下（当前「%s」）。"
                      % core.OBJECTIVE_STATUS.get(obj.get("status"), obj.get("status")))
        priority = str(payload.get("priority") or "中").strip()
        if priority not in PRIORITIES:
            core.fail(1001, "优先级仅支持 高 / 中 / 低。")
        try:
            hours = round(float(payload.get("plan_hours") or 0), 1)
        except (TypeError, ValueError):
            core.fail(1001, "计划工时必须是数字。")
        if not PLAN_HOURS_MIN <= hours <= PLAN_HOURS_MAX:
            core.fail(1002, "计划工时须在 %.1f~%d 小时之间。" % (PLAN_HOURS_MIN, PLAN_HOURS_MAX))
        start = core.parse_dt(str(payload.get("plan_start") or "").strip())
        end = core.parse_dt(str(payload.get("plan_end") or "").strip())
        if start is None or end is None:
            core.fail(1002, "计划开始 / 结束日期格式应为 YYYY-MM-DD。")
        if end < start:
            core.fail(1001, "计划结束不能早于开始。")
        if obj.get("plan_start") and start.date().isoformat() < obj.get("plan_start"):
            core.fail(1005, "任务开始不得早于目标开始（%s）。" % obj.get("plan_start"), {"field": "plan_start"})
        if obj.get("plan_end") and end.date().isoformat() > obj.get("plan_end"):
            core.fail(1005, "任务结束不得晚于目标截止（%s）。" % obj.get("plan_end"), {"field": "plan_end"})
        kr_id = str(payload.get("kr_id") or "").strip()
        if kr_id:
            kr = next((row for row in store["key_result"]
                       if row.get("kr_id") == kr_id and row.get("objective_id") == objective_id), None)
            if kr is None:
                core.fail(1005, "KR 不属于所选目标。")

        with core.DATA_LOCK:
            locked = core.read_store()
            task_id = core.next_id(locked, "work_task", "T", 3)
            now_iso = core.iso()
            locked["work_task"].append({
                "task_id": task_id, "title": title, "detail": detail,
                "parent_task_id": "", "objective_id": objective_id, "kr_id": kr_id,
                "priority": priority, "plan_hours": hours,
                "plan_start": start.date().isoformat(), "plan_end": end.date().isoformat(),
                "progress": 0, "exec_rate": 0.0, "status": "todo", "source": TASK_SOURCE,
                "is_demo": False, "manual_override": False, "blocked_reason": "",
                "assignees": [], "plan_items": [],
                "created_at": now_iso, "updated_at": now_iso,
            })
            _log(locked, task_id, "新建派工任务（挂载目标「%s」）" % (obj.get("title") or objective_id),
                 user["user_id"], progress=0)
            core.write_store(locked)
        result = core.ok({"task_id": task_id, "redirect": "/assign?task_id=%s" % task_id}, "派工任务已创建")
        core.idempotency_save(core.client_key(), result)
        return jsonify(result)

    # ------------------------------------------------- API：饱和度试算
    @app.post("/api/assign/check")
    def api_assign_check():
        payload = _payload()
        cached = core.idempotency_lookup(core.client_key())
        if cached is not None:
            return jsonify(cached), 200
        store = core.read_store()
        user = core.current_user(store)
        if user is None:
            core.fail(1008, "登录已失效，请重新登录。")
        if not _is_manager(store, user):
            core.fail(1008, "当前角色没有派工试算权限。")
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            core.fail(1001, "缺少任务参数 task_id。")
        task = _task_or_fail(store, user, task_id)
        green, red, limit = _thresholds(store)

        raw_assignees = payload.get("assignees")
        selected_ids = set()
        selected_best = {}
        if isinstance(raw_assignees, list) and raw_assignees:
            rows, _ = _collect_assignees(store, user, raw_assignees, strict=False)
            for row in rows:
                selected_ids.add(row["user_id"])
                selected_best[row["user_id"]] = row["weight"]

        hours = float(task.get("plan_hours") or 0)
        members = []
        for user_id in _candidate_ids(store, user):
            if user_id in selected_ids:
                row = _member_row(store, user_id, green, red,
                                  new_hours=hours * selected_best[user_id] / 100.0,
                                  exclude_task_id=task["task_id"])
                row["selected"] = True
            else:
                row = _member_row(store, user_id, green, red, new_hours=hours,
                                  exclude_task_id=task["task_id"])
                row["selected"] = False
            members.append(row)
        members.sort(key=lambda row: ({"green": 0, "yellow": 1, "red": 2}[row["level"]],
                                      row["saturation_after"], row["saturation_before"]))

        selected_rows = [row for row in members if row["selected"]]
        overload = [row for row in selected_rows if row["saturation_after"] > red]
        warnings = [row for row in selected_rows if row["level"] == "yellow"]
        recommendations = []
        for row in members:
            if row["selected"]:
                continue
            if len(recommendations) >= 2:
                break
            if row["level"] == "red":
                continue
            reason = "加入后饱和度 %.1f%%（当前 %.1f%%）" % (row["saturation_after"], row["saturation_before"])
            if row["ability_tags"]:
                reason += "；能力标签：%s" % "、".join(row["ability_tags"][:3])
            recommendations.append({
                "user_id": row["user_id"], "name": row["name"], "level": row["level"],
                "saturation_after": row["saturation_after"], "reason": reason,
            })
        if selected_rows:
            worst = max(row["saturation_after"] for row in selected_rows)
            overall = "block" if overload else ("warn" if warnings else "ok")
        else:
            overall = "idle"
        result = core.ok({
            "task": {"task_id": task["task_id"], "title": task.get("title") or "",
                     "plan_hours": task.get("plan_hours"),
                     "plan_start": task.get("plan_start"), "plan_end": task.get("plan_end")},
            "thresholds": {"green": green, "red": red, "limit": limit},
            "members": members,
            "overall": overall,
            "overload": [{"user_id": row["user_id"], "name": row["name"],
                          "saturation_after": row["saturation_after"]} for row in overload],
            "warnings": [{"user_id": row["user_id"], "name": row["name"],
                          "saturation_after": row["saturation_after"]} for row in warnings],
            "recommendations": recommendations,
        }, "试算完成")
        core.idempotency_save(core.client_key(), result)
        return jsonify(result)

    # ------------------------------------------------- API：AI 派工分析
    @app.post("/api/assign/analyze")
    def api_assign_analyze():
        payload = _payload()
        cached = core.idempotency_lookup(core.client_key())
        if cached is not None:
            return jsonify(cached), 200
        store = core.read_store()
        user = core.api_require(store, "action:assign.analyze")
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            core.fail(1001, "缺少任务参数 task_id。")
        task = _task_or_fail(store, user, task_id)
        assignees, _ = _collect_assignees(store, user, payload.get("assignees"), strict=False)
        green, red, _limit = _thresholds(store)
        obj = _find_objective(store, task.get("objective_id") or "") or {}

        candidates = []
        for assignee in assignees:
            row = _member_row(store, assignee["user_id"], green, red,
                              new_hours=float(task.get("plan_hours") or 0) * assignee["weight"] / 100.0,
                              exclude_task_id=task["task_id"])
            candidates.append({
                "user_id": assignee["user_id"], "name": row["name"],
                "ability_tags": row["ability_tags"], "open_titles": row["open_titles"],
                "saturation": row["saturation_after"], "weight": assignee["weight"],
            })

        engine, degraded = "rule", True
        people = None
        result = llm.llm_assign_analyze(task, obj, candidates)
        if result:
            engine, degraded = "llm", False
            people = result.get("people")
            risks = list(result.get("risks") or [])
        if people is None:
            people, risks = _rule_analyze(store, task, candidates)
        rows = []
        for candidate in candidates:
            item = people.get(candidate["user_id"]) or {"relation_score": 0, "note": "暂无分析结果"}
            rows.append({
                "user_id": candidate["user_id"], "name": candidate["name"],
                "relation_score": item["relation_score"], "note": item["note"],
                "saturation_after": candidate["saturation"],
            })
        rows.sort(key=lambda row: row["relation_score"], reverse=True)
        best = rows[0] if rows else None
        response = core.ok({
            "task_id": task_id,
            "engine": engine,
            "degraded": degraded,
            "people": rows,
            "risks": risks,
            "best": None if best is None else {
                "user_id": best["user_id"], "name": best["name"],
                "relation_score": best["relation_score"],
            },
        }, "AI 派工分析完成%s" % ("（规则兜底）" if degraded else ""))
        core.idempotency_save(core.client_key(), response)
        return jsonify(response)

    def _rule_analyze(store, task, candidates):
        text = (task.get("title") or "") + " " + (task.get("detail") or "")
        people = {}
        for candidate in candidates:
            score = 40
            notes = []
            hits = [tag for tag in candidate["ability_tags"] if tag and tag.lower() in text.lower()]
            if hits:
                score += min(36, 12 * len(hits))
                notes.append("能力标签命中 %s" % "、".join(hits[:3]))
            same_objective = [title for title in candidate["open_titles"] if title]
            if same_objective:
                score += min(12, 6 * len(same_objective))
                notes.append("同周期在办 %d 项" % len(same_objective))
            if candidate["saturation"] <= 90:
                score += 12
                notes.append("加入后饱和度 %.1f%%（健康）" % candidate["saturation"])
            elif candidate["saturation"] <= 110:
                score += 5
                notes.append("加入后饱和度 %.1f%%（临界）" % candidate["saturation"])
            else:
                notes.append("加入后饱和度 %.1f%%（超载）" % candidate["saturation"])
            people[candidate["user_id"]] = {
                "relation_score": min(100, score),
                "note": "；".join(notes) or "缺少可匹配信息，建议人工确认",
            }
        risks = []
        if float(task.get("plan_hours") or 0) > 40:
            risks.append("任务量偏大")
        if not (task.get("detail") or "").strip():
            risks.append("不确定性偏高")
        weights = [candidate["weight"] for candidate in candidates]
        if len(weights) >= 2 and (max(weights) - min(weights)) > 30:
            risks.append("分配不均")
        return people, risks

    # ------------------------------------------------- API：智能计划项
    @app.post("/api/assign/plan")
    def api_assign_plan():
        payload = _payload()
        cached = core.idempotency_lookup(core.client_key())
        if cached is not None:
            return jsonify(cached), 200
        store = core.read_store()
        user = core.api_require(store, "action:assign.plan")
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            core.fail(1001, "缺少任务参数 task_id。")
        task = _task_or_fail(store, user, task_id)
        assignees, _ = _collect_assignees(store, user, payload.get("assignees"), strict=False)
        items, engine, degraded = _build_plan_items(store, task, assignees)
        response = core.ok({
            "task_id": task_id, "items": items, "engine": engine, "degraded": degraded,
            "plan_hours": task.get("plan_hours"),
        }, "计划项草案生成完成%s（可编辑后提交）" % ("（规则兜底）" if degraded else ""))
        core.idempotency_save(core.client_key(), response)
        return jsonify(response)

    # ------------------------------------------------- API：派工提交
    @app.post("/api/assign/commit")
    def api_assign_commit():
        payload = _payload()
        cached = core.idempotency_lookup(core.client_key())
        if cached is not None:
            return jsonify(cached), 200
        store = core.read_store()
        user = core.api_require(store, "action:assign.dispatch")
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            core.fail(1001, "缺少任务参数 task_id。")
        task = _task_or_fail(store, user, task_id)
        _require_dispatchable(store, user, task)

        assignees, owner_id = _collect_assignees(store, user, payload.get("assignees"))
        plan_items = _collect_plan_items(store, task, assignees, payload.get("plan_items"))
        sat_rows, overload, warnings = _saturation_preview(store, task, assignees)

        override = bool(payload.get("override"))
        override_reason = str(payload.get("override_reason") or "").strip()[:200]
        if overload and not override:
            core.fail(1005, "存在超载执行人（加入后饱和度 > %.0f%%）：%s。请更换执行人或勾选「强制下发」。"
                      % (_thresholds(store)[1],
                         "、".join("%s %.1f%%" % (row["name"], row["saturation_after"]) for row in overload)),
                      {"overload": [{"user_id": row["user_id"], "name": row["name"],
                                     "saturation_after": row["saturation_after"]} for row in overload]})
        if override and overload:
            core.api_require(store, "action:assign.override")
            if len(override_reason) < 4:
                core.fail(1001, "强制下发必须填写理由（至少 4 个字）。")

        with core.DATA_LOCK:
            locked = core.read_store()
            target = _find_task(locked, task_id)
            if target is None:
                core.fail(1003, "任务不存在或已删除。")
            if target.get("status") in DONE_STATUSES:
                core.fail(1004, "任务刚被更新（当前「%s」），不可再派工。"
                          % core.TASK_STATUS.get(target.get("status"), target.get("status")))
            before = {
                "assignees": list(target.get("assignees") or []),
                "plan_items": list(target.get("plan_items") or []),
                "status": target.get("status"),
            }
            target["assignees"] = assignees
            target["plan_items"] = plan_items
            if target.get("status") == "todo":
                target["status"] = "todo"
            target["manual_override"] = bool(override and overload)
            target["overload_marks"] = [{
                "user_id": row["user_id"], "name": row["name"],
                "saturation_after": row["saturation_after"],
            } for row in overload] if (override and overload) else []
            target["updated_at"] = core.iso()

            locked["task_assignee"] = [row for row in locked["task_assignee"] if row.get("task_id") != task_id]
            for assignee in assignees:
                locked["task_assignee"].append({
                    "task_id": task_id, "user_id": assignee["user_id"],
                    "role_in_task": assignee["role_in_task"], "weight": assignee["weight"],
                    "is_owner": assignee["is_owner"],
                })
            names = "、".join("%s（%s%%，%s）" % (row["name"], row["weight"], row["role_in_task"]) for row in assignees)
            note = "智能派工：%s；计划项 %d 条" % (names, len(plan_items))
            if override and overload:
                note += "；强制下发（超载：%s，理由：%s）" % (
                    "、".join("%s %.1f%%" % (row["name"], row["saturation_after"]) for row in overload), override_reason)
            else:
                note += "；饱和度校验通过"
            _log(locked, task_id, note, user["user_id"], target.get("progress"))
            if override and overload:
                core.audit(locked, "assign.force_dispatch", "work_task", task_id,
                           before=before, after={"assignees": assignees, "overload": target["overload_marks"]},
                           operator=user)
            _refresh_profiles(locked)
            core.write_store(locked)

        fresh = core.read_store()
        target = _find_task(fresh, task_id)
        result = core.ok({
            "task_id": task_id,
            "assignees": [{"user_id": row["user_id"], "name": row["name"],
                           "weight": row["weight"], "role_in_task": row["role_in_task"],
                           "saturation_after": sat["saturation_after"]}
                          for row, sat in zip(assignees, sat_rows)],
            "plan_items": plan_items,
            "warnings": [{"user_id": row["user_id"], "name": row["name"],
                          "saturation_after": row["saturation_after"]} for row in warnings],
            "overload": [{"user_id": row["user_id"], "name": row["name"],
                          "saturation_after": row["saturation_after"]} for row in overload] if override else [],
            "override": bool(override and overload),
            "status": target.get("status"),
        }, "派工已下发%s" % ("（含强制下发，已记录审计）" if (override and overload) else ""))
        core.idempotency_save(core.client_key(), result)
        return jsonify(result)

    # ------------------------------------------------- API：员工能力档案
    @app.get("/api/staff/profile")
    def api_staff_profile():
        store = core.read_store()
        user = core.current_user(store)
        if user is None:
            core.fail(1008, "登录已失效，请重新登录。")
        user_id = request.args.get("user_id", "").strip() or user["user_id"]
        if user_id != user["user_id"] and not _is_manager(store, user):
            core.fail(1008, "只能查看自己的档案。")
        if user_id != user["user_id"] and not core.in_scope(store, user, user_id):
            core.fail(1003, "该员工不在你的数据范围内。")
        member = core.user_of(store, user_id)
        if member is None:
            core.fail(1003, "员工不存在。")
        green, red, _limit = _thresholds(store)
        row = _member_row(store, user_id, green, red)
        profile = _profile_of(store, user_id) or {}
        return jsonify(core.ok({
            "user_id": user_id,
            "name": row["name"],
            "org_name": row["org_name"],
            "position_code": profile.get("position_code") or "",
            "position_name": row["position_name"],
            "ability_tags": row["ability_tags"],
            "base_capacity_hours": row["base_hours"],
            "load_hours": row["load_hours"],
            "saturation": row["saturation_before"],
            "level": row["level"],
            "open_titles": row["open_titles"],
            "updated_at": profile.get("updated_at") or "",
            "editable": core.has_perm(store, user, "action:staff.profile_edit")
            and core.has_perm(store, user, "field:staff_profile.capacity"),
        }))

    @app.post("/api/staff/save")
    def api_staff_save():
        payload = _payload()
        cached = core.idempotency_lookup(core.client_key())
        if cached is not None:
            return jsonify(cached), 200
        store = core.read_store()
        user = core.api_require(store, "action:staff.profile_edit")
        user_id = str(payload.get("user_id") or "").strip()
        if not user_id:
            core.fail(1001, "缺少员工参数 user_id。")
        member = core.user_of(store, user_id)
        if member is None:
            core.fail(1003, "员工不存在。")
        if not core.in_scope(store, user, user_id):
            core.fail(1003, "该员工不在你的数据范围内。")
        position_code = str(payload.get("position_code") or "").strip()[:40]
        raw_tags = payload.get("ability_tags")
        tags = []
        if isinstance(raw_tags, list):
            for tag in raw_tags[:8]:
                text = str(tag or "").strip()[:20]
                if text and text not in tags:
                    tags.append(text)
        elif isinstance(raw_tags, str):
            for tag in raw_tags.replace("，", ",").split(","):
                text = tag.strip()[:20]
                if text and text not in tags:
                    tags.append(text)
        capacity = None
        if payload.get("base_capacity_hours") is not None:
            try:
                capacity = round(float(payload.get("base_capacity_hours")), 1)
            except (TypeError, ValueError):
                core.fail(1001, "基准容量必须是数字。")
            if not 1 <= capacity <= 2000:
                core.fail(1002, "基准容量须在 1~2000 小时之间。")
            if not core.has_perm(store, user, "field:staff_profile.capacity"):
                core.fail(1008, "缺少字段权限 field:staff_profile.capacity，不能调整基准容量。")

        with core.DATA_LOCK:
            locked = core.read_store()
            profile = _profile_of(locked, user_id)
            before = dict(profile) if profile else {}
            if profile is None:
                profile = {
                    "profile_id": core.next_id(locked, "staff_profile", "SP", 3),
                    "user_id": user_id, "position_code": position_code,
                    "ability_tags": tags, "base_capacity_hours": capacity or 480,
                    "current_load_hours": 0, "saturation": 0.0,
                    "updated_at": core.iso(),
                }
                locked["staff_profile"].append(profile)
            else:
                if position_code:
                    profile["position_code"] = position_code
                if tags or isinstance(raw_tags, (list, str)):
                    profile["ability_tags"] = tags
                if capacity is not None:
                    profile["base_capacity_hours"] = capacity
                profile["updated_at"] = core.iso()
            _refresh_profiles(locked)
            core.audit(locked, "staff.profile_update", "staff_profile", user_id,
                       before=before, after=dict(profile), operator=user)
            core.write_store(locked)

        fresh = core.read_store()
        profile = _profile_of(fresh, user_id) or {}
        green, red, _limit = _thresholds(fresh)
        row = _member_row(fresh, user_id, green, red)
        result = core.ok({
            "user_id": user_id, "name": row["name"],
            "position_code": profile.get("position_code") or "",
            "position_name": row["position_name"],
            "ability_tags": row["ability_tags"],
            "base_capacity_hours": row["base_hours"],
            "load_hours": row["load_hours"],
            "saturation": row["saturation_before"],
            "level": row["level"],
        }, "档案已更新")
        core.idempotency_save(core.client_key(), result)
        return jsonify(result)
