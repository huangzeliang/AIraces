"""NS 线（目标拆解与验证闭环）公共服务层 —— 工作线 B 各模块共用。

职责：
- 会话：统一账号体系（session 键与 backend.core.SESSION_KEY 一致，两线共用同一登录身份）；
- 数据范围：超管 ALL / 负责人 DEPT（组织子树）/ 员工 SELF（仅本人）；
- 导航：5 组 18 个二级菜单，按 menu: 权限点过滤，整组无权限则不渲染；
- 进度上卷：4.4.4 两级公式（日计划项 → 任务执行度 → 目标过程完成率）；
- 健康度：完成进度 vs 时间进度，阈值读 setting.warn_thresholds；
- 预警：超期 / 临期 / 偏差 / 检查点 / 连续异常的统一口径（列表与页面共用一套）；
- 渲染与响应：NS 页面壳上下文 + NS API 装饰器（错误包 / 幂等 / 登录校验）。

约定：写路径一律「with DATA_LOCK: store = read_store() → 校验 → 修改 → write_store(store)」。
"""

import json
from datetime import timedelta
from functools import wraps

from flask import g, jsonify, redirect, render_template, request, session, url_for

from backend.core import (
    ALERT_TYPES,
    BizError,
    DATA_LOCK,
    ERROR_HTTP,
    SESSION_KEY,
    audit as core_audit,
    client_key,
    dict_name,
    f1,
    fail,
    has_perm,
    idempotency_lookup,
    idempotency_save,
    iso,
    level_label,
    next_id,
    now,
    ok,
    org_of,
    org_subtree_ids,
    parse_dt,
    read_store,
    role_of_user,
    setting_value,
    write_store,
)

# 单系统合并：NS 会话与统一账号共用同一 session 键（users.json 的 id ≡ system.json 的 user_id），
# 从目标闭环登录页或智能计划管理登录页任一入口登录，都可直达两线全部页面。
NS_SESSION_KEY = SESSION_KEY
NS_DEMO_PASSWORD = "123456"

ROLE_SCOPE_LABEL = {"ALL": "全公司", "DEPT": "本部门及下级", "SELF": "仅本人"}


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

def ns_current_user(store=None):
    user_id = session.get(NS_SESSION_KEY)
    if not user_id:
        return None
    store = store or read_store()
    return next((u for u in store["user"] if u["user_id"] == user_id and u.get("status") == "启用"), None)


def ns_login_session(user_id):
    session[NS_SESSION_KEY] = user_id


def ns_logout_session():
    # 统一会话：退出即两线同时退出（与旧系统 logout 行为一致）
    session.pop(NS_SESSION_KEY, None)


def ns_user(store=None):
    """接口里取当前用户：优先用守卫写入的 g.ns_user，兜底重读。"""
    user = getattr(g, "ns_user", None)
    if user is not None:
        return user
    user = ns_current_user(store)
    if user is None:
        fail(1008, "登录已失效，请重新登录。")
    return user


# ---------------------------------------------------------------------------
# 数据范围
# ---------------------------------------------------------------------------

def role_scope(store, user):
    role = role_of_user(store, user["user_id"])
    return (role or {}).get("scope_type") or "SELF"


def visible_user_ids(store, user):
    """None = 不限制；否则为可见 user_id 列表。"""
    scope = role_scope(store, user)
    if scope == "ALL":
        return None
    if scope == "DEPT":
        org_ids = set(org_subtree_ids(store, user.get("org_unit_id")))
        return [u["user_id"] for u in store["user"] if u.get("org_unit_id") in org_ids]
    return [user["user_id"]]


def in_visible(store, user, target_user_id):
    ids = visible_user_ids(store, user)
    return ids is None or target_user_id in ids


def is_manager(store, user):
    return role_scope(store, user) in ("ALL", "DEPT")


def direct_manager(store, user):
    """沿组织树向上找第一个负责人（跳过自己）；找不到返回 None。"""
    org = org_of(store, user.get("org_unit_id"))
    seen = set()
    while org and org.get("org_unit_id") not in seen:
        seen.add(org["org_unit_id"])
        manager_id = org.get("manager_user_id")
        if manager_id and manager_id != user["user_id"]:
            manager = user_by_id(store, manager_id)
            if manager and manager.get("status") == "启用":
                return manager
        org = org_of(store, org.get("parent_id"))
    return None


# ---------------------------------------------------------------------------
# 数据读取小工具
# ---------------------------------------------------------------------------

def user_by_id(store, user_id):
    return next((u for u in store["user"] if u["user_id"] == user_id), None)


def user_name(store, user_id):
    user = user_by_id(store, user_id)
    return user["display_name"] if user else user_id or "—"


def task_by_id(store, task_id):
    return next((t for t in store["work_task"] if t["task_id"] == task_id), None)


def obj_by_id(store, objective_id):
    return next((o for o in store["objective"] if o["objective_id"] == objective_id), None)


def kr_by_id(store, kr_id):
    return next((k for k in store["key_result"] if k["kr_id"] == kr_id), None)


def plan_by_id(store, plan_id):
    return next((p for p in store["daily_plan"] if p["plan_id"] == plan_id), None)


def plan_items(store, plan_id):
    return sorted(
        (item for item in store["daily_plan_item"] if item["plan_id"] == plan_id),
        key=lambda item: item.get("sort_no", 0),
    )


def assignee_ids(task):
    return [a["user_id"] for a in (task.get("assignees") or [])]


def is_task_assignee(task, user):
    return user and user["user_id"] in assignee_ids(task)


def owns_task(store, user, task):
    if not task:
        return False
    if role_scope(store, user) == "ALL":
        return True
    return is_task_assignee(task, user)


def can_edit_progress(store, user, task):
    """B1 手动更新：执行人可改自己的部分；管理者在范围内可修正（走审计）。"""
    if not task:
        return False
    if is_task_assignee(task, user):
        return True
    return is_manager(store, user) and any(in_visible(store, user, uid) for uid in assignee_ids(task))


def in_scope_user(store, user, target_user_id):
    """管理者或超管在数据范围内；员工仅自己。"""
    if user["user_id"] == target_user_id:
        return True
    return is_manager(store, user) and in_visible(store, user, target_user_id)


def scope_objectives_check(store, user, objective_id):
    """目标读写守卫：不存在或不在数据范围 → 1003（对象不存在或越权）。"""
    obj = obj_by_id(store, objective_id)
    ids = visible_user_ids(store, user)
    if obj and ids is not None and obj.get("owner_id") not in ids:
        has_task = any(
            t.get("objective_id") == objective_id and any(uid in ids for uid in assignee_ids(t))
            for t in store["work_task"]
        )
        if not has_task:
            obj = None
    if obj is None:
        fail(1003, "目标不存在或越权。")
    return obj


# ---------------------------------------------------------------------------
# 进度与健康度（4.4.4）
# ---------------------------------------------------------------------------

def time_progress(obj, moment=None):
    """时间进度（0~100）：已过去时长 / 计划总时长。"""
    start = parse_dt(obj.get("plan_start"))
    end = parse_dt(obj.get("plan_end"))
    moment = moment or now()
    if not start or not end or end <= start:
        return 0.0
    ratio = (moment - start).total_seconds() / (end - start).total_seconds()
    return f1(max(0.0, min(1.0, ratio)) * 100)


def health_tag(store, done_progress, time_percent):
    """三档健康度：ratio ≥ yellow 超前 / ≥ green 正常 / 其余滞后（阈值可配）。"""
    thresholds = setting_value(store, "warn_thresholds", {"green": 90, "yellow": 110})
    green = float(thresholds.get("green", 90))
    yellow = float(thresholds.get("yellow", 110))
    done = float(done_progress or 0)
    if time_percent <= 0:
        return "ahead" if done >= 100 else "normal"
    ratio = done / float(time_percent) * 100
    if ratio >= yellow:
        return "ahead"
    if ratio >= green:
        return "normal"
    return "lagging"


HEALTH_META = {
    "ahead": {"label": "超前", "level": "green"},
    "normal": {"label": "正常", "level": "green"},
    "lagging": {"label": "滞后", "level": "red"},
}


def health_label(tag):
    return HEALTH_META.get(tag, HEALTH_META["normal"])["label"]


def objective_health(store, obj):
    time_percent = time_progress(obj)
    done = float(obj.get("progress") or 0)
    return {
        "time_progress": time_percent,
        "done_progress": f1(done),
        "delta": f1(done - time_percent),
        "health": health_tag(store, done, time_percent),
    }


def task_weight(task):
    weights = [a.get("weight") or 0 for a in (task.get("assignees") or [])]
    total = float(sum(weights))
    return total if total > 0 else 100.0


def task_health(store, task):
    time_percent = time_progress(task)
    done = float(task.get("progress") or 0)
    return {
        "time_progress": time_percent,
        "done_progress": f1(done),
        "delta": f1(done - time_percent),
        "health": health_tag(store, done, time_percent),
    }


def compute_task_exec_rate(store, task):
    """4.4.4 第 1 级：日计划项（仅 approved）按工时加权；手动覆盖优先。"""
    if task.get("manual_override"):
        return f1(task.get("progress"))
    approved_plan_ids = {p["plan_id"] for p in store["daily_plan"] if p["day_status"] == "approved"}
    items = [
        item for item in store["daily_plan_item"]
        if item.get("task_id") == task["task_id"] and item.get("plan_id") in approved_plan_ids
    ]
    total_hours = sum(float(item.get("plan_hours") or 0) for item in items)
    if total_hours <= 0:
        return f1(task.get("progress"))
    weighted = sum(float(item.get("progress") or 0) * float(item.get("plan_hours") or 0) for item in items)
    return f1(weighted / total_hours)


def rollup_task(store, task):
    rate = compute_task_exec_rate(store, task)
    task["exec_rate"] = rate
    if not task.get("manual_override"):
        task["progress"] = rate
    task["updated_at"] = iso()
    return rate


def compute_objective_process_rate(store, obj):
    """4.4.4 第 2 级：任务执行度按任务权重（执行人权重和）加权。"""
    tasks = [
        t for t in store["work_task"]
        if t.get("objective_id") == obj["objective_id"] and t.get("status") != "cancelled"
    ]
    total_weight = sum(task_weight(t) for t in tasks)
    if total_weight <= 0:
        return f1(obj.get("progress"))
    weighted = sum(float(t.get("exec_rate") or t.get("progress") or 0) * task_weight(t) for t in tasks)
    return f1(weighted / total_weight)


def rollup_objective(store, obj):
    obj["progress"] = compute_objective_process_rate(store, obj)
    return obj["progress"]


def rollup_for_task(store, task):
    """任务进度变化后的两级上卷（不写库，由调用方在锁内持久化）。"""
    rollup_task(store, task)
    obj = obj_by_id(store, task.get("objective_id"))
    if obj:
        rollup_objective(store, obj)
    return obj


def next_action(store, user, obj):
    """工作台目标卡的「下一步」。"""
    status = obj.get("status")
    if status == "pending_confirm":
        return {"text": "确认承接目标", "url": "/ns/goal/tree?focus_id=%s" % obj["objective_id"]}
    if status == "pending_review":
        return {"text": "补充验收证据", "url": "/ns/review?objective_id=%s" % obj["objective_id"]}
    if status in ("reviewed", "archived"):
        return {"text": "查看复盘记录", "url": "/ns/retro?objective_id=%s" % obj["objective_id"]}
    tasks = [t for t in store["work_task"] if t.get("objective_id") == obj["objective_id"]]
    blocked = next((t for t in tasks if t.get("status") == "blocked"), None)
    if blocked:
        return {"text": "处理阻塞任务：%s" % blocked["title"], "url": "/ns/progress?task_id=%s" % blocked["task_id"]}
    for task in tasks:
        if task.get("status") in ("todo", "doing") and float(task.get("progress") or 0) < 100:
            return {"text": "更新进度：%s" % task["title"], "url": "/ns/progress?task_id=%s" % task["task_id"]}
    return {"text": "查看任务时间线", "url": "/ns/progress?objective_id=%s" % obj["objective_id"]}


# ---------------------------------------------------------------------------
# 连续三日异常（F-413 / 2.7.4）
# ---------------------------------------------------------------------------

def daily_series(store, user_id, base_date=None, days=14):
    """按日期聚合该员工的已通过日计划：date → {day_progress, item_count, has_block}。"""
    approved = {
        plan["plan_date"]: plan
        for plan in store["daily_plan"]
        if plan.get("user_id") == user_id and plan.get("day_status") == "approved"
    }
    blocked_days = {
        plan["plan_id"]
        for plan in store["daily_plan"]
        if plan.get("user_id") == user_id
        for item in store["daily_plan_item"]
        if item.get("plan_id") == plan["plan_id"]
    }
    start = (base_date or now().date()) - timedelta(days=days - 1)
    series = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        plan = approved.get(day.isoformat())
        if not plan:
            series.append({"date": day.isoformat(), "day_progress": None, "item_count": 0, "has_block": False})
            continue
        items = plan_items(store, plan["plan_id"])
        has_block = any(item.get("abnormal_flag") for item in items)
        series.append({
            "date": day.isoformat(),
            "day_progress": f1(plan.get("day_progress")),
            "item_count": len(items),
            "has_block": has_block and plan["plan_id"] in blocked_days,
        })
    return series


def streak_info(store, user_id, base_date=None):
    """连续异常天数：连续日历日中 day_progress < 100 且当日有已通过日计划；无计划日不计入。"""
    threshold = int(setting_value(store, "streak_days", 3))
    approved = {
        plan["plan_date"]: plan
        for plan in store["daily_plan"]
        if plan.get("user_id") == user_id and plan.get("day_status") == "approved"
    }
    end = (base_date or now().date()) - timedelta(days=1)
    days = 0
    min_progress = 100.0
    window_start = ""
    plan_count = 0
    for offset in range(14):
        day = end - timedelta(days=offset)
        plan = approved.get(day.isoformat())
        if plan is None:
            if days == 0:
                continue  # 最近没有计划日则继续向前找，最多回溯 14 天
            break
        items = plan_items(store, plan["plan_id"])
        if any(item.get("abnormal_flag") for item in items):
            break  # 当日已按 F-209 上报阻塞，豁免异常判定
        value = f1(plan.get("day_progress"))
        if value >= 100:
            break
        days += 1
        plan_count += len(items)
        min_progress = min(min_progress, value)
        window_start = day.isoformat()
    return {
        "days": days,
        "threshold": threshold,
        "triggered": days >= threshold,
        "window_start": window_start,
        "window_end": end.isoformat() if days else "",
        "min_progress": f1(min_progress) if days else 0.0,
        "plan_count": plan_count,
    }


# ---------------------------------------------------------------------------
# 三色预警统一口径（F-206~209 / F-220）
# ---------------------------------------------------------------------------

def checkpoints_of(store, task):
    """任务周期超过 checkpoint_days 时自动生成中间检查点（F-220）。"""
    offsets = setting_value(store, "checkpoint_days", [7, 3, 1])
    start = parse_dt(task.get("plan_start"))
    end = parse_dt(task.get("plan_end"))
    if not start or not end:
        return []
    span = (end - start).days
    points = []
    for offset in sorted((int(v) for v in offsets), reverse=True):
        if span <= offset:
            continue
        point_date = end - timedelta(days=offset)
        if point_date < start:
            continue
        overdue = now() > point_date and float(task.get("progress") or 0) < 100
        points.append({
            "task_id": task["task_id"],
            "date": point_date.date().isoformat(),
            "offset_days": offset,
            "state": "overdue" if overdue else ("passed" if now() > point_date else "upcoming"),
        })
    return points


def compute_task_alerts(store, user, extra_user_ids=None):
    """当前范围任务的三色预警（列表 / 树 / 工作台共用一套口径）。"""
    scope_ids = visible_user_ids(store, user)
    if extra_user_ids is not None:
        scope_ids = extra_user_ids if scope_ids is None else [uid for uid in scope_ids if uid in extra_user_ids]
    due_soon_days = int(setting_value(store, "due_soon_days", 2))
    moment = now()
    alerts = []
    for task in store["work_task"]:
        if task.get("status") in ("done", "cancelled"):
            continue
        owners = assignee_ids(task)
        if scope_ids is not None and not any(uid in scope_ids for uid in owners):
            continue
        progress = float(task.get("progress") or 0)
        end = parse_dt(task.get("plan_end"))
        metrics = task_health(store, task)
        level = ""
        alert_type = ""
        if end and end < moment and progress < 100:
            level, alert_type = "red", "overdue"
        elif end and end - moment <= timedelta(days=due_soon_days) and progress < 90:
            level, alert_type = "yellow", "due_soon"
        elif metrics["health"] == "lagging":
            level, alert_type = "yellow", "deviation"
        elif task.get("status") == "blocked":
            level, alert_type = "red", "deviation"
        if not level:
            continue
        owner_name = "、".join(user_name(store, uid) for uid in owners)
        alerts.append({
            "alert_id": "AL-%s-%s" % (alert_type, task["task_id"]),
            "target_type": "task",
            "target_id": task["task_id"],
            "title": task["title"],
            "owner_name": owner_name,
            "level": level,
            "alert_type": alert_type,
            "alert_type_label": ALERT_TYPES.get(alert_type, alert_type),
            "progress": f1(progress),
            "health": metrics["health"],
            "health_label": health_label(metrics["health"]),
            "plan_end": task.get("plan_end", ""),
            "blocked_reason": task.get("blocked_reason", ""),
            "body": alert_body(store, task, alert_type, moment),
            "deep_link": "/ns/progress?task_id=%s&from=warning" % task["task_id"],
        })
        if alert_type in ("overdue", "due_soon"):
            checkpoints = checkpoints_of(store, task)
            overdue_points = [c for c in checkpoints if c["state"] == "overdue"]
            due_checkpoint = overdue_points[-1] if overdue_points else None
            if due_checkpoint:
                alerts.append({
                    "alert_id": "AL-checkpoint-%s" % task["task_id"],
                    "target_type": "task",
                    "target_id": task["task_id"],
                    "title": task["title"],
                    "owner_name": owner_name,
                    "level": "yellow",
                    "alert_type": "checkpoint",
                    "alert_type_label": ALERT_TYPES["checkpoint"],
                    "progress": f1(progress),
                    "health": metrics["health"],
                    "health_label": health_label(metrics["health"]),
                    "plan_end": task.get("plan_end", ""),
                    "blocked_reason": "",
                    "body": "检查点 %s 已到，请上传阶段性成果并更新进度。" % due_checkpoint["date"],
                    "deep_link": "/ns/progress?task_id=%s&from=warning" % task["task_id"],
                })
    return sorted(alerts, key=lambda a: ({"red": 0, "yellow": 1, "green": 2}.get(a["level"], 3), a["plan_end"]))


def alert_body(store, task, alert_type, moment):
    if alert_type == "overdue":
        days = max(1, (moment - parse_dt(task["plan_end"])).days) if parse_dt(task["plan_end"]) else 1
        return "已超期 %d 天，当前进度 %s%%，请更新进度或发起变更。" % (days, f1(task.get("progress")))
    if alert_type == "due_soon":
        days = max(0, (parse_dt(task["plan_end"]) - moment).days)
        return "距计划结束仅剩 %d 天，当前进度 %s%%。" % (days, f1(task.get("progress")))
    if task.get("status") == "blocked":
        return "任务阻塞：%s" % (task.get("blocked_reason") or "原因待补充")
    metrics = task_health(store, task)
    return "完成进度 %s%% 落后时间进度 %s%%（差值 %s），请关注节奏。" % (
        metrics["done_progress"], metrics["time_progress"], metrics["delta"])


def streak_alerts(store, user, base_date=None):
    """连续异常预警（员工看自己 / 管理者看范围内成员），文案按角色不同。"""
    alerts = []
    scope_ids = visible_user_ids(store, user)
    targets = [user["user_id"]] if scope_ids is None else scope_ids
    for user_id in targets:
        info = streak_info(store, user_id, base_date)
        if not info["triggered"]:
            continue
        person = user_by_id(store, user_id)
        if not person:
            continue
        is_self = user_id == user["user_id"]
        level = "red" if info["days"] >= 5 else "yellow"
        if is_self:
            title = "连续 %d 天未完成当日计划" % info["days"]
            body = "你已连续 %d 天（%s ~ %s）当日完成率未达 100%%，最低 %s%%。建议先处理关键路径上的任务；连续 ≥5 天将升级展示。" % (
                info["days"], info["window_start"], info["window_end"], info["min_progress"])
        else:
            title = "%s 连续 %d 天进度异常" % (person["display_name"], info["days"])
            body = "%s（%s）连续 %d 天（%s ~ %s）当日完成率未达 100%%，最低 %s%%，请关注并跟进。" % (
                person["display_name"], person.get("position", ""), info["days"],
                info["window_start"], info["window_end"], info["min_progress"])
        alerts.append({
            "alert_id": "AL-streak-%s" % user_id,
            "target_type": "user",
            "target_id": user_id,
            "title": title,
            "owner_name": person["display_name"],
            "level": level,
            "alert_type": "daily_streak",
            "alert_type_label": ALERT_TYPES["daily_streak"],
            "progress": info["min_progress"],
            "health": "lagging",
            "health_label": health_label("lagging"),
            "plan_end": info["window_end"],
            "blocked_reason": "",
            "body": body,
            "streak": info,
            "deep_link": "/ns/today?user_id=%s&from=warning" % user_id if not is_self else "/ns/today?from=warning",
        })
    return alerts


# ---------------------------------------------------------------------------
# 提醒
# ---------------------------------------------------------------------------

def unread_count(store, user_id):
    return sum(
        1 for item in store["reminder"]
        if item.get("user_id") == user_id and item.get("status") == "未读"
    )


def create_reminder(store, user_id, target_type, target_id, alert_type, level, title, body,
                    trigger_id="", role="", dedupe_key=""):
    """写一条提醒；dedupe_key 命中已有未读提醒时跳过（窗口去重）。"""
    if dedupe_key:
        exists = any(
            item.get("dedupe_key") == dedupe_key and item.get("user_id") == user_id
            for item in store["reminder"]
        )
        if exists:
            return None
    entry = {
        "reminder_id": next_id(store, "reminder", "RM", 3),
        "user_id": user_id,
        "target_type": target_type,
        "target_id": target_id,
        "alert_type": alert_type,
        "level": level,
        "title": title,
        "body": body,
        "status": "未读",
        "created_at": iso(),
        "read_at": "",
        "ack_at": "",
        "trigger_id": trigger_id,
        "role": role,
    }
    if dedupe_key:
        entry["dedupe_key"] = dedupe_key
    store["reminder"].insert(0, entry)
    return entry


# ---------------------------------------------------------------------------
# 导航与渲染
# ---------------------------------------------------------------------------

NS_NAV = [
    ("工作台", [
        ("workbench", "个人工作台", "ns_workbench", "◉"),
        ("today", "日计划与任务", "ns_today", "✓"),
        ("dashboard", "部门驾驶舱", "ns_dashboard", "▦"),
    ]),
    ("目标拆解", [
        ("goal_dept", "部门目标", "ns_goal_dept", "◎"),
        ("goal_smart", "SMART 校验", "ns_goal_smart", "✦"),
        ("goal_tree", "拆解树与发布", "ns_goal_tree", "▣"),
        ("assign", "智能派工", "ns_assign", "⚙"),
    ]),
    ("执行跟踪", [
        ("progress", "进度更新", "ns_progress", "◍"),
        ("warning", "预警与指导", "ns_warning", "⚠"),
        ("changes", "变更管理", "ns_changes", "⇄"),
        ("critical_path", "关键路径与阻塞", "ns_critical_path", "∿"),
    ]),
    ("验收评估", [
        ("review", "完成与验收", "ns_review", "☑"),
        ("retro", "复盘", "ns_retro", "↺"),
        ("compare", "对比分析", "ns_compare", "⇆"),
    ]),
    ("数据与设置", [
        ("settings_org", "组织与角色", "ns_settings_org", "⌗"),
        ("settings_dict", "周期与字典", "ns_settings_dict", "▥"),
        ("data", "数据管理", "ns_data", "▧"),
        ("settings", "系统设置", "ns_settings", "✱"),
    ]),
]


def nav_groups(store, user):
    """按 menu: 权限点过滤；一组全无权限则整组不渲染。"""
    groups = []
    for group_name, items in NS_NAV:
        visible_items = []
        for menu_key, label, endpoint, icon in items:
            if has_perm(store, user, "menu:" + menu_key):
                visible_items.append({
                    "menu": menu_key, "label": label, "icon": icon,
                    "url": url_for(endpoint), "endpoint": endpoint,
                })
        if visible_items:
            groups.append({"name": group_name, "items": visible_items})
    return groups


def landing_url(store, user):
    if has_perm(store, user, "menu:workbench"):
        return url_for("ns_workbench")
    return url_for("ns_today")


def ns_shell(store, user, active_menu, page_title, page_subtitle=""):
    role = role_of_user(store, user["user_id"])
    org = org_of(store, user.get("org_unit_id"))
    return {
        "ns_user": user,
        "ns_org_name": org["org_name"] if org else "",
        "ns_position_label": dict_name(store, "position", user.get("position") or ""),
        "ns_role_name": (role or {}).get("role_name") or "员工",
        "ns_scope_label": ROLE_SCOPE_LABEL.get(role_scope(store, user), "仅本人"),
        "ns_is_manager": is_manager(store, user),
        "ns_nav": nav_groups(store, user),
        "ns_active": active_menu,
        "ns_page_title": page_title,
        "ns_page_subtitle": page_subtitle,
        "ns_unread": unread_count(store, user["user_id"]),
        "ns_level_label": level_label,
    }


def ns_render(template, active_menu, page_title, page_subtitle="", store=None, user=None, **extra):
    store = store or read_store()
    user = user or ns_user(store)
    context = ns_shell(store, user, active_menu, page_title, page_subtitle)
    context.update(extra)
    return render_template(template, **context)


def require_ns_menu(menu_key):
    """NS 页面守卫：未登录 → NS 登录页；无权限 → NS 403 页。"""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            store = read_store()
            user = ns_current_user(store)
            if user is None:
                target = request.full_path.rstrip("?")
                return redirect(url_for("ns_login", next=target))
            if menu_key and not has_perm(store, user, "menu:" + menu_key):
                return ns_render(
                    "ns/forbidden.html", "", "无权访问", "", store=store, user=user,
                    missing_menu=menu_key,
                ), 403
            return view(*args, **kwargs)
        return wrapper
    return decorator


def api_perm(store, user, code):
    if not has_perm(store, user, code):
        fail(1008, "当前角色缺少权限点 %s，操作被拒绝。" % code)
    return user


def payload():
    """同时接受 JSON 与 multipart（文件上传时把 JSON 放在 payload 字段）。"""
    if request.files:
        raw = request.form.get("payload") or "{}"
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def ns_api(view):
    """NS API 统一包装：登录校验 + 幂等 + {code, message, data} 响应包。"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        key = client_key()
        cached = idempotency_lookup(key)
        if cached is not None:
            return jsonify(cached)
        try:
            result = view(*args, **kwargs)
            body = result if isinstance(result, dict) and "code" in result else ok(result)
            status = 200
        except BizError as err:
            body = {"code": err.code, "message": err.message, "data": err.data}
            status = ERROR_HTTP.get(err.code, 500)
        except Exception:  # noqa: BLE001 — 兜底转 1006，避免把堆栈返回给前端
            import traceback
            traceback.print_exc()
            body = {"code": 1006, "message": "服务端异常，请稍后重试。", "data": None}
            status = 500
        if status == 200:
            idempotency_save(key, body)
        return jsonify(body), status
    return wrapper


def audit(store, user, action, target_type="", target_id="", before=None, after=None):
    return core_audit(store, action, target_type, target_id, before, after, operator=user)


def dict_label(store, dict_type, code):
    return dict_name(store, dict_type, code)
