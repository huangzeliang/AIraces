"""B5 个人工作台（F-401~403）：/ns/workbench 页面 + /api/ns/workbench/overview。

三区聚合：我的目标卡 / 待我处理（五类分组，不混成一条）/ 我的提醒；
按角色叠加差异化区块：管理者 = 团队健康度 + 团队负荷；员工 = 今日计划摘要。
分区降级：任一区块异常只标 partial + failed_sections，不整页报错。
"""

from flask import request

from backend.core import (
    DAILY_STATUS,
    OBJECTIVE_STATUS,
    TASK_STATUS,
    f1,
    now,
    parse_dt,
    read_store,
    setting_value,
)
from backend.ns_common import (
    assignee_ids,
    in_visible,
    is_manager,
    next_action,
    ns_api,
    ns_render,
    ns_user,
    objective_health,
    plan_items,
    require_ns_menu,
    streak_info,
    task_health,
    unread_count,
    user_name,
    visible_user_ids,
)

GROUP_NAMES = {
    "pending_countersign": "待承接目标",
    "pending_change": "待审批变更",
    "pending_change_own": "我提交的变更",
    "pending_review_self": "待自评",
    "pending_review_grade": "待终评",
    "pending_daily_plan": "待审核日计划",
    "pending_daily_submit": "待提交今日计划",
}


def _my_objectives(store, user, limit=8):
    uid = user["user_id"]
    scope_ids = visible_user_ids(store, user)
    my_task_obj_ids = {
        task.get("objective_id") for task in store["work_task"]
        if task.get("objective_id") and uid in assignee_ids(task)
    }
    rows = []
    for obj in store["objective"]:
        owner = obj.get("owner_id")
        in_scope = scope_ids is None or owner in scope_ids
        related = owner == uid or obj.get("objective_id") in my_task_obj_ids
        if not in_scope or (not related and not is_manager(store, user)):
            continue
        if obj.get("status") in ("draft",):
            continue
        metrics = objective_health(store, obj)
        action = next_action(store, user, obj)
        rows.append({
            "objective_id": obj["objective_id"],
            "title": obj["title"],
            "owner_name": user_name(store, owner),
            "period_id": obj.get("period_id", ""),
            "progress": f1(obj.get("progress")),
            "status": obj.get("status"),
            "status_label": OBJECTIVE_STATUS.get(obj.get("status"), obj.get("status")),
            "health": metrics["health"],
            "health_label": {"ahead": "超前", "normal": "正常", "lagging": "滞后"}[metrics["health"]],
            "time_progress": metrics["time_progress"],
            "delta": metrics["delta"],
            "plan_end": obj.get("plan_end", ""),
            "next_action": action["text"],
            "deep_link": action["url"],
        })
    order = {"lagging": 0, "ahead": 1, "normal": 2}
    rows.sort(key=lambda row: (order.get(row["health"], 3), row["plan_end"] or "9999"))
    return rows[:limit], max(0, len(rows) - limit)


def _todo_groups(store, user):
    uid = user["user_id"]
    scope_ids = visible_user_ids(store, user)
    groups = []

    countersign = [
        obj for obj in store["objective"]
        if obj.get("owner_id") == uid and obj.get("status") == "pending_confirm"
    ]
    groups.append(("pending_countersign", [
        {"id": obj["objective_id"], "title": obj["title"], "meta": "周期 %s · 权重 %s%%" % (obj.get("period_id"), obj.get("weight")),
         "link": "/ns/goal/tree?focus_id=%s" % obj["objective_id"]}
        for obj in countersign
    ], "去拆解树确认"))

    if is_manager(store, user):
        changes = [
            ch for ch in store["change_request"]
            if ch.get("status") == "pending" and (
                ch.get("approver_id") == uid or (scope_ids is not None and ch.get("applicant_id") in scope_ids)
            )
        ]
        groups.append(("pending_change", [
            {"id": ch["change_id"], "title": "%s · %s" % (ch.get("change_type"), _obj_title(store, ch.get("objective_id"))),
             "meta": "申请人 %s · %s" % (user_name(store, ch.get("applicant_id")), ch.get("reason_code")),
             "link": "/ns/changes?focus_id=%s" % ch["change_id"]}
            for ch in changes
        ], "去审批"))
    else:
        own_changes = [
            ch for ch in store["change_request"]
            if ch.get("status") == "pending" and ch.get("applicant_id") == uid
        ]
        groups.append(("pending_change_own", [
            {"id": ch["change_id"], "title": "%s · %s" % (ch.get("change_type"), _obj_title(store, ch.get("objective_id"))),
             "meta": "提交于 %s · 待 %s 审批" % (str(ch.get("applied_at"))[:10], user_name(store, ch.get("approver_id"))),
             "link": "/ns/changes?focus_id=%s" % ch["change_id"]}
            for ch in own_changes
        ], "去查看"))

    my_obj_ids = {obj["objective_id"] for obj in store["objective"] if obj.get("owner_id") == uid}
    self_assess = [
        a for a in store["assessment"]
        if a.get("status") == "pending_self" and a.get("objective_id") in my_obj_ids
    ]
    groups.append(("pending_review_self", [
        {"id": a["assess_id"], "title": _obj_title(store, a.get("objective_id")),
         "meta": "周期 %s · 待自评" % a.get("period_id"),
         "link": "/ns/review?objective_id=%s" % a.get("objective_id")}
        for a in self_assess
    ], "去自评"))

    manager_assess = [
        a for a in store["assessment"]
        if a.get("status") == "pending_manager" and a.get("assessor_id") == uid
    ]
    groups.append(("pending_review_grade", [
        {"id": a["assess_id"], "title": _obj_title(store, a.get("objective_id")),
         "meta": "自评分 %s · 待终评" % a.get("self_score"),
         "link": "/ns/review?objective_id=%s" % a.get("objective_id")}
        for a in manager_assess
    ], "去验收"))

    if is_manager(store, user):
        submitted = [
            plan for plan in store["daily_plan"]
            if plan.get("day_status") == "submitted" and in_visible(store, user, plan.get("user_id"))
        ]
        groups.append(("pending_daily_plan", [
            {"id": plan["plan_id"], "title": "%s 的日计划" % user_name(store, plan.get("user_id")),
             "meta": "%s · %d 个计划项" % (plan.get("plan_date"), len(plan_items(store, plan["plan_id"]))),
             "link": "/ns/today?user_id=%s&date=%s" % (plan.get("user_id"), plan.get("plan_date"))}
            for plan in submitted
        ], "去审核"))
    else:
        today = now().date().isoformat()
        my_today = next(
            (plan for plan in store["daily_plan"]
             if plan.get("user_id") == uid and plan.get("plan_date") == today),
            None,
        )
        items = []
        if my_today is None:
            items.append({"id": today, "title": "今日日计划尚未编制", "meta": today, "link": "/ns/today"})
        elif my_today.get("day_status") == "draft":
            items.append({"id": my_today["plan_id"], "title": "今日日计划仍是草稿", "meta": "%s · 待提交审核" % today, "link": "/ns/today"})
        elif my_today.get("day_status") == "rejected":
            items.append({"id": my_today["plan_id"], "title": "今日日计划被驳回", "meta": my_today.get("reject_reason") or "", "link": "/ns/today"})
        groups.append(("pending_daily_submit", items, "去处理"))

    result = []
    for key, items, action_text in groups:
        if not items:
            continue
        result.append({
            "group_key": key,
            "group_name": GROUP_NAMES.get(key, key),
            "count": len(items),
            "items": items[:6],
            "more_count": max(0, len(items) - 6),
            "action_text": action_text,
        })
    return result


def _my_reminders(store, user, limit=10):
    rows = []
    for item in store["reminder"]:
        if item.get("user_id") != user["user_id"]:
            continue
        rows.append({
            "reminder_id": item.get("reminder_id"),
            "title": item.get("title"),
            "body": item.get("body"),
            "level": item.get("level"),
            "alert_type": item.get("alert_type"),
            "status": item.get("status"),
            "created_at": item.get("created_at", ""),
            "deep_link": _reminder_link(item),
        })
    rows.sort(key=lambda row: row["created_at"], reverse=True)
    rows.sort(key=lambda row: 0 if row["status"] == "未读" else 1)
    return rows[:limit], len(rows)


def _reminder_link(item):
    target_type = item.get("target_type")
    if target_type == "task" and item.get("target_id"):
        return "/ns/progress?task_id=%s&from=workbench" % item["target_id"]
    if target_type == "daily_plan":
        return "/ns/today?from=workbench"
    if target_type == "user":
        return "/ns/today?user_id=%s&from=workbench" % item.get("target_id")
    return "/ns/warning?from=workbench"


def _manager_block(store, user):
    scope_ids = visible_user_ids(store, user)
    tasks = [
        task for task in store["work_task"]
        if task.get("status") not in ("done", "cancelled")
        and (scope_ids is None or any(uid in scope_ids for uid in assignee_ids(task)))
    ]
    health_count = {"green": 0, "yellow": 0, "red": 0}
    for task in tasks:
        metrics = task_health(store, task)
        end = parse_dt(task.get("plan_end"))
        progress = float(task.get("progress") or 0)
        if task.get("status") == "blocked" or (end and end < now() and progress < 100):
            health_count["red"] += 1
        elif metrics["health"] == "lagging":
            health_count["yellow"] += 1
        else:
            health_count["green"] += 1

    load_rows = []
    targets = scope_ids if scope_ids is not None else [u["user_id"] for u in store["user"]]
    for uid in targets:
        if uid == user["user_id"]:
            continue
        profile = next((p for p in store["staff_profile"] if p.get("user_id") == uid), None)
        if not profile:
            continue
        open_hours = sum(
            float(task.get("plan_hours") or 0) for task in store["work_task"]
            if task.get("status") not in ("done", "cancelled") and uid in assignee_ids(task)
        )
        capacity = float(profile.get("base_capacity_hours") or 480)
        saturation = f1(open_hours / capacity * 100) if capacity else 0.0
        limit_value = float(setting_value(store, "saturation_limit", 90))
        level = "green" if saturation < limit_value else ("yellow" if saturation < limit_value + 20 else "red")
        load_rows.append({
            "user_id": uid,
            "name": user_name(store, uid),
            "position": profile.get("position_code", ""),
            "open_hours": f1(open_hours),
            "capacity": f1(capacity),
            "saturation": saturation,
            "level": level,
        })
    load_rows.sort(key=lambda row: row["saturation"], reverse=True)
    pending_approval = sum(
        1 for ch in store["change_request"]
        if ch.get("status") == "pending" and ch.get("approver_id") == user["user_id"]
    )
    return {"health_count": health_count, "load_rows": load_rows, "pending_approval": pending_approval}


def _staff_block(store, user, streak):
    uid = user["user_id"]
    today = now().date().isoformat()
    plan = next(
        (p for p in store["daily_plan"] if p.get("user_id") == uid and p.get("plan_date") == today),
        None,
    )
    open_tasks = [
        task for task in store["work_task"]
        if uid in assignee_ids(task) and task.get("status") not in ("done", "cancelled")
    ]
    nearest = None
    for task in open_tasks:
        end = task.get("plan_end") or "9999"
        if nearest is None or end < nearest[0]:
            nearest = (end, task)
    return {
        "today": today,
        "plan": {
            "exists": plan is not None,
            "day_status": plan.get("day_status") if plan else "draft",
            "day_status_label": DAILY_STATUS.get(plan.get("day_status"), "未编制") if plan else "未编制",
            "day_progress": f1(plan.get("day_progress")) if plan else None,
            "item_count": len(plan_items(store, plan["plan_id"])) if plan else 0,
        },
        "open_count": len(open_tasks),
        "nearest": None if nearest is None else {
            "task_id": nearest[1]["task_id"],
            "title": nearest[1]["title"],
            "plan_end": nearest[0],
            "status_label": TASK_STATUS.get(nearest[1].get("status"), nearest[1].get("status")),
        },
        "streak": streak,
    }


def overview_data(store, user, objective_limit=8):
    partial = False
    failed = []
    sections = {}
    builders = {
        "my_objectives": lambda: _my_objectives(store, user, objective_limit),
        "todo_groups": lambda: _todo_groups(store, user),
        "my_reminders": lambda: _my_reminders(store, user),
    }
    for key, builder in builders.items():
        try:
            sections[key] = builder()
        except Exception as exc:  # noqa: BLE001 — 分区降级
            import traceback
            traceback.print_exc()
            partial = True
            failed.append(key)
            sections[key] = ([], 0) if key in ("my_objectives", "my_reminders") else []
    objectives, objectives_more = sections["my_objectives"]
    reminders, reminders_total = sections["my_reminders"]
    streak = streak_info(store, user["user_id"])
    data = {
        "my_objectives": objectives,
        "my_objectives_total_more": objectives_more,
        "todo_groups": sections["todo_groups"],
        "my_reminders": reminders,
        "my_reminders_total": reminders_total,
        "unread_count": unread_count(store, user["user_id"]),
        "streak": streak,
        "manager_block": None,
        "staff_block": None,
        "partial": partial,
        "failed_sections": failed,
    }
    try:
        if is_manager(store, user):
            data["manager_block"] = _manager_block(store, user)
        else:
            data["staff_block"] = _staff_block(store, user, streak)
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        data["partial"] = True
        data["failed_sections"].append("role_block")
    return data


def register(app, services):
    @app.get("/ns/workbench")
    @require_ns_menu("workbench")
    def ns_workbench():
        store = read_store()
        user = ns_user(store)
        data = overview_data(store, user)
        return ns_render(
            "ns/workbench.html", "workbench", "个人工作台",
            "我的目标、待我处理与提醒；按角色呈现不同区块。",
            store=store, user=user, overview=data,
        )

    @app.get("/api/ns/workbench/overview")
    @ns_api
    def ns_api_workbench_overview():
        store = read_store()
        user = ns_user(store)
        try:
            limit = max(1, min(50, int(request.args.get("objective_limit", 8))))
        except (TypeError, ValueError):
            limit = 8
        return overview_data(store, user, limit)
