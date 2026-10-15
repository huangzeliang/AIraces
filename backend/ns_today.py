"""B6 日计划与任务（F-407~413）：/ns/today 页面与全部接口。

核心规则：
- 审核是硬闸门：day_status ≠ approved 时拉条返回 1011（AC-27）；
- 拉条只写 daily_plan_item.progress → 两级上卷（4.4.4），绝不直接写 objective.progress；
- 区域 A/C/D 读写分离：A 编制、B 视图（3 形态 × 3 粒度）、C 拉条、D 今日聚合与自建；
- 跨日冻结：过去日期只读，拉条仅限当天；
- 连续三日 <100% 由后台巡检按 2.7.4 双角色文案投递（alert-chain）。
"""

from datetime import date as date_type

from flask import request

from backend.core import (
    DATA_LOCK,
    DAILY_STATUS,
    TASK_STATUS,
    fail,
    f1,
    iso,
    next_id,
    now,
    parse_dt,
    read_store,
    write_store,
)
from backend.ns_common import (
    assignee_ids,
    checkpoints_of,
    daily_series,
    dict_name,
    direct_manager,
    in_visible,
    is_manager,
    ns_api,
    ns_render,
    ns_user,
    obj_by_id,
    payload,
    plan_items,
    require_ns_menu,
    rollup_objective,
    rollup_task,
    streak_info,
    task_by_id,
    user_name,
    visible_user_ids,
)


# ---------------------------------------------------------------------------
# 计划口径与数据构建
# ---------------------------------------------------------------------------

def compute_day_progress(items):
    total = sum(float(item.get("plan_hours") or 0) for item in items)
    if total <= 0:
        return 0.0
    weighted = sum(float(item.get("progress") or 0) * float(item.get("plan_hours") or 0) for item in items)
    return f1(weighted / total)


def _find_plan(store, user_id, date_str):
    return next(
        (p for p in store["daily_plan"] if p.get("user_id") == user_id and p.get("plan_date") == date_str),
        None,
    )


def _item_view(store, item):
    task = task_by_id(store, item.get("task_id"))
    return {
        "item_id": item["item_id"],
        "task_id": item.get("task_id"),
        "task_title": task["title"] if task else "（任务已删除）",
        "task_missing": task is None,
        "source": (task or {}).get("source", "assigned"),
        "task_status": (task or {}).get("status", ""),
        "task_status_label": TASK_STATUS.get((task or {}).get("status"), "—"),
        "plan_desc": item.get("plan_desc", ""),
        "plan_hours": float(item.get("plan_hours") or 0),
        "done": bool(item.get("done")),
        "progress": f1(item.get("progress")),
        "sort_no": item.get("sort_no", 0),
        "abnormal_flag": item.get("abnormal_flag", ""),
    }


def plan_payload(store, target_user_id, date_str):
    plan = _find_plan(store, target_user_id, date_str)
    if plan is None:
        return {
            "exists": False, "plan_id": "", "plan_date": date_str,
            "day_status": "draft", "day_status_label": "未编制", "day_progress": None,
            "reject_reason": "", "reviewer_name": "", "submitted_at": "", "reviewed_at": "",
            "items": [], "total_hours": 0.0, "version": 0,
        }
    items = [_item_view(store, item) for item in plan_items(store, plan["plan_id"])]
    return {
        "exists": True,
        "plan_id": plan["plan_id"],
        "plan_date": plan["plan_date"],
        "day_status": plan.get("day_status"),
        "day_status_label": DAILY_STATUS.get(plan.get("day_status"), plan.get("day_status")),
        "day_progress": f1(plan.get("day_progress")),
        "reject_reason": plan.get("reject_reason", ""),
        "reviewer_name": user_name(store, plan.get("reviewer_id")) if plan.get("reviewer_id") else "",
        "submitted_at": plan.get("submitted_at", ""),
        "reviewed_at": plan.get("reviewed_at", ""),
        "items": items,
        "total_hours": f1(sum(item["plan_hours"] for item in items)),
        "version": len(items),
    }


def available_tasks(store, target_user_id):
    rows = []
    for task in store["work_task"]:
        if task.get("status") in ("done", "cancelled"):
            continue
        if target_user_id not in assignee_ids(task):
            continue
        rows.append({
            "task_id": task["task_id"],
            "title": task["title"],
            "status_label": TASK_STATUS.get(task.get("status"), task.get("status")),
            "plan_end": task.get("plan_end", ""),
            "plan_hours": task.get("plan_hours"),
            "progress": f1(task.get("progress")),
            "source": task.get("source", "assigned"),
            "objective_id": task.get("objective_id", ""),
            "overdue": bool(parse_dt(task.get("plan_end")) and parse_dt(task["plan_end"]) < now() and task.get("status") != "done"),
        })
    rows.sort(key=lambda row: (0 if row["overdue"] else 1, row["plan_end"] or "9999"))
    return rows


def today_groups(store, target_user_id, date_str):
    tasks = [
        task for task in store["work_task"]
        if target_user_id in assignee_ids(task) and task.get("status") not in ("cancelled",)
    ]
    due = [
        task for task in tasks
        if task.get("plan_end") == date_str and task.get("status") != "done"
    ]
    self_made = [task for task in tasks if task.get("source") == "self"]
    plan = _find_plan(store, target_user_id, date_str)
    plan_task_ids = {item.get("task_id") for item in plan_items(store, plan["plan_id"])} if plan else set()
    planned = [task for task in tasks if task["task_id"] in plan_task_ids]
    return {
        "due_today": [_task_brief(store, task) for task in due],
        "planned": [_task_brief(store, task) for task in planned],
        "self_made": [_task_brief(store, task) for task in self_made],
    }


def _task_brief(store, task):
    return {
        "task_id": task["task_id"],
        "title": task["title"],
        "status_label": TASK_STATUS.get(task.get("status"), task.get("status")),
        "plan_end": task.get("plan_end", ""),
        "progress": f1(task.get("progress")),
        "source": task.get("source", "assigned"),
        "objective_id": task.get("objective_id", ""),
        "objective_title": (obj_by_id(store, task.get("objective_id")) or {}).get("title", ""),
        "link_status": task.get("link_status", ""),
    }


def _window(base_date, granularity):
    if granularity == "day":
        return [base_date]
    if granularity == "week":
        return [date_type.fromordinal(base_date.toordinal() - offset) for offset in range(6, -1, -1)]
    first = base_date.replace(day=1)
    days = []
    cursor = first
    while cursor.month == first.month:
        days.append(cursor)
        cursor = date_type.fromordinal(cursor.toordinal() + 1)
    return days


HEAT_LEGEND = [
    {"level": 0, "label": "0%", "range": "0"},
    {"level": 1, "label": "1-39%", "range": "1-39"},
    {"level": 2, "label": "40-69%", "range": "40-69"},
    {"level": 3, "label": "70-99%", "range": "70-99"},
    {"level": 4, "label": "100%", "range": "100"},
]


def _heat_level(value):
    if value is None:
        return None
    if value >= 100:
        return 4
    if value >= 70:
        return 3
    if value >= 40:
        return 2
    if value > 0:
        return 1
    return 0


def views_payload(store, target_user_id, base_date, granularity="week"):
    days = _window(base_date, granularity)
    series = daily_series(store, target_user_id, days[-1], days=len(days))
    series_by_date = {row["date"]: row for row in series}

    items_in_window = {}
    for plan in store["daily_plan"]:
        if plan.get("user_id") != target_user_id:
            continue
        if plan.get("plan_date") not in series_by_date:
            continue
        for item in plan_items(store, plan["plan_id"]):
            bucket = items_in_window.setdefault(item.get("task_id"), {"hours": 0.0, "weighted": 0.0, "count": 0})
            hours = float(item.get("plan_hours") or 0)
            bucket["hours"] += hours
            bucket["weighted"] += float(item.get("progress") or 0) * hours
            bucket["count"] += 1
    ring_items = []
    for task_id, bucket in items_in_window.items():
        task = task_by_id(store, task_id)
        rate = f1(bucket["weighted"] / bucket["hours"]) if bucket["hours"] > 0 else None
        ring_items.append({
            "task_id": task_id,
            "title": task["title"] if task else "（任务已删除）",
            "rate": rate,
            "value_text": ("%s%%" % rate) if rate is not None else "—",
            "item_count": bucket["count"],
        })
    ring_items.sort(key=lambda row: (row["rate"] is None, -(row["rate"] or 0)))

    hotmap_cells = []
    for day in days:
        row = series_by_date.get(day.isoformat())
        value = row["day_progress"] if row else None
        hotmap_cells.append({
            "date": day.isoformat(),
            "day_progress": value,
            "level": _heat_level(value),
            "item_count": row["item_count"] if row else 0,
        })

    month_first = base_date.replace(day=1)
    cursor = month_first
    calendar_days = []
    while cursor.month == month_first.month:
        date_str = cursor.isoformat()
        plan = _find_plan(store, target_user_id, date_str)
        row = series_by_date.get(date_str)
        has_block = False
        if plan:
            has_block = any(item.get("abnormal_flag") for item in plan_items(store, plan["plan_id"]))
        calendar_days.append({
            "date": date_str,
            "day": cursor.day,
            "day_progress": row["day_progress"] if row else None,
            "item_count": row["item_count"] if row else 0,
            "day_status": plan.get("day_status") if plan else "",
            "day_status_label": DAILY_STATUS.get(plan.get("day_status"), "未编制") if plan else "未编制",
            "has_block": has_block,
            "is_today": cursor == now().date(),
            "in_window": date_str in series_by_date,
        })
        cursor = date_type.fromordinal(cursor.toordinal() + 1)

    planned_values = [row["day_progress"] for row in series if row["day_progress"] is not None]
    summary = {
        "avg_day_progress": f1(sum(planned_values) / len(planned_values)) if planned_values else None,
        "streak_abnormal": streak_info(store, target_user_id)["days"],
        "granularity": granularity,
        "base_date": base_date.isoformat(),
        "range_label": "%s ~ %s" % (days[0].isoformat(), days[-1].isoformat()),
        "plan_count": len(planned_values),
    }
    return {
        "ring": {"items": ring_items},
        "hotmap": {"cells": hotmap_cells, "legend": HEAT_LEGEND},
        "calendar": {
            "days": calendar_days,
            "month_label": month_first.strftime("%Y-%m"),
            "start_weekday": month_first.weekday(),
        },
        "summary": summary,
    }


def all_views_payload(store, target_user_id, base_date):
    return {
        granularity: views_payload(store, target_user_id, base_date, granularity)
        for granularity in ("day", "week", "month")
    }


# ---------------------------------------------------------------------------
# 写路径
# ---------------------------------------------------------------------------

def _validate_items(store, body_items, target_user_id):
    if not body_items or not isinstance(body_items, list):
        fail(1001, "日计划至少需要 1 个计划项。")
    normalized = []
    for index, raw in enumerate(body_items, start=1):
        task_id = (raw.get("task_id") or "").strip()
        task = task_by_id(store, task_id)
        if not task:
            fail(1003, "第 %d 行关联任务不存在。" % index)
        if target_user_id not in assignee_ids(task):
            fail(1008, "第 %d 行任务不属于该员工，无法编入日计划。" % index)
        if task.get("status") in ("done", "cancelled"):
            fail(1004, "第 %d 行任务已完成或已取消，无法编入今日计划。" % index)
        plan_desc = (raw.get("plan_desc") or "").strip()
        if not plan_desc:
            fail(1001, "第 %d 行请填写「今天具体做什么」。" % index)
        if len(plan_desc) > 80:
            fail(1002, "第 %d 行动作描述超过 80 字。" % index)
        try:
            hours = float(raw.get("plan_hours"))
        except (TypeError, ValueError):
            fail(1002, "第 %d 行计划工时不合法。" % index)
        if hours <= 0 or hours > 12:
            fail(1002, "第 %d 行计划工时须在 0~12 小时之间。" % index)
        normalized.append({
            "task_id": task_id,
            "plan_desc": plan_desc,
            "plan_hours": f1(hours),
            "sort_no": int(raw.get("sort_no") or index),
        })
    return normalized


def register(app, services):
    # ------------------------------------------------------------------ 页面
    @app.get("/ns/today")
    @require_ns_menu("today")
    def ns_today():
        store = read_store()
        user = ns_user(store)
        date_str = request.args.get("date") or now().date().isoformat()
        if not parse_dt(date_str):
            fail(1002, "日期格式不正确。")
        target_id = request.args.get("user_id") or user["user_id"]
        if target_id != user["user_id"] and not (is_manager(store, user) and in_visible(store, user, target_id)):
            fail(1008, "无权查看他人的日计划。")
        target = next((u for u in store["user"] if u["user_id"] == target_id), None)
        if target is None:
            fail(1003, "用户不存在。")

        is_owner = target_id == user["user_id"]
        base_date = parse_dt(date_str).date()
        plan = plan_payload(store, target_id, date_str)
        streak = streak_info(store, target_id)
        views = all_views_payload(store, target_id, base_date)
        reviewer = direct_manager(store, target)
        is_reviewer = bool(reviewer and reviewer["user_id"] == user["user_id"])
        can_review = (
            plan["exists"] and plan["day_status"] == "submitted"
            and (is_reviewer or not is_manager(store, user) is False and _has_review_perm(store, user))
        ) and not is_owner

        config = {
            "urls": {
                "submit": "/api/ns/today/plan-submit",
                "approve": "/api/ns/today/plan-approve",
                "progress": "/api/ns/today/progress",
                "views": "/api/ns/today/views",
                "taskSave": "/api/ns/task/save",
                "taskLink": "/api/ns/task/link-objective",
            },
            "date": date_str,
            "today": now().date().isoformat(),
            "target": {"user_id": target_id, "name": target["display_name"]},
            "is_owner": is_owner,
            "can_review": can_review,
            "plan": plan,
            "streak": streak,
            "views": views,
            "available_tasks": available_tasks(store, target_id) if is_owner else [],
            "today_groups": today_groups(store, target_id, date_str),
            "objectives": [
                {"objective_id": obj["objective_id"], "title": obj["title"]}
                for obj in store["objective"]
                if not obj.get("is_archived") and in_visible(store, user, obj.get("owner_id"))
            ],
            "reviewer_name": reviewer["display_name"] if reviewer else "",
        }
        return ns_render(
            "ns/today.html", "today", "日计划与任务",
            "按天拆分可执行计划 → 提交审核 → 拉条上卷；三视图 × 三粒度随时切换。",
            store=store, user=user,
            config=config,
            target=target, is_owner=is_owner, date_str=date_str,
            streak=streak,
            max_hours_warn=10,
            checkpoints=checkpoints_of,
        )

    # ------------------------------------------------------------------ 列表
    @app.get("/api/ns/today/list")
    @ns_api
    def ns_api_today_list():
        store = read_store()
        user = ns_user(store)
        target_id = request.args.get("user_id") or user["user_id"]
        if target_id != user["user_id"] and not (is_manager(store, user) and in_visible(store, user, target_id)):
            fail(1008, "员工只能查看自己的日计划。")
        date_str = request.args.get("date") or now().date().isoformat()
        date_from = request.args.get("date_from", "")
        date_to = request.args.get("date_to", "")
        status_filter = request.args.get("plan_status", "")

        def match(plan):
            if plan.get("user_id") != target_id:
                return False
            if status_filter and plan.get("day_status") != status_filter:
                return False
            if date_from and plan.get("plan_date", "") < date_from:
                return False
            if date_to and plan.get("plan_date", "") > date_to:
                return False
            return True

        plans = [plan_payload(store, target_id, plan["plan_date"]) for plan in store["daily_plan"] if match(plan)]
        plans.sort(key=lambda row: row["plan_date"])
        data = {"plans": plans, "target_user": user_name(store, target_id)}
        if target_id == user["user_id"]:
            data["available_tasks"] = available_tasks(store, target_id)
            data["today_groups"] = today_groups(store, target_id, date_str)
        return data

    # ------------------------------------------------------------------ 提交 / 撤回
    @app.post("/api/ns/today/plan-submit")
    @ns_api
    def ns_api_today_plan_submit():
        body = payload()
        action = body.get("action") or "submit"
        date_str = body.get("plan_date") or now().date().isoformat()
        plan_date = parse_dt(date_str)
        if not plan_date:
            fail(1002, "日期格式不正确。")
        note = (body.get("submit_note") or "").strip()
        if len(note) > 200:
            fail(1002, "提交备注不能超过 200 字。")

        with DATA_LOCK:
            store = read_store()
            user = ns_user(store)
            uid = user["user_id"]
            plan = _find_plan(store, uid, date_str)

            if action == "withdraw":
                if not plan or plan.get("day_status") != "submitted":
                    fail(1004, "仅「待审核」状态的日计划可以撤回。")
                plan["day_status"] = "draft"
                write_store(store)
                return {"day_status": "draft", "total_hours": compute_day_progress([]) if False else _plan_total(store, plan), "message": "已撤回到草稿。"}

            if action != "submit":
                fail(1001, "action 仅支持 submit / withdraw。")
            if plan_date.date() < now().date():
                fail(1005, "过去日期已定型，不可再提交。")
            items = _validate_items(store, body.get("items"), uid)
            total_hours = f1(sum(item["plan_hours"] for item in items))
            if plan is None:
                plan = {
                    "plan_id": next_id(store, "daily_plan", "DP", 3),
                    "user_id": uid,
                    "plan_date": date_str,
                    "day_status": "draft",
                    "day_progress": 0.0,
                    "streak_abnormal": 0,
                    "reviewer_id": "",
                    "reviewed_at": "",
                    "reject_reason": "",
                    "submitted_at": "",
                    "ack_at": "",
                }
                store["daily_plan"].append(plan)
            elif plan.get("day_status") == "approved":
                fail(1004, "该日计划已通过审核，如需调整请在当天拉条或走变更。")
            else:
                store["daily_plan_item"] = [it for it in store["daily_plan_item"] if it["plan_id"] != plan["plan_id"]]

            for item in items:
                store["daily_plan_item"].append({
                    "item_id": next_id(store, "daily_plan_item", "DIT", 3),
                    "plan_id": plan["plan_id"],
                    "task_id": item["task_id"],
                    "plan_hours": item["plan_hours"],
                    "plan_desc": item["plan_desc"],
                    "done": False,
                    "progress": 0,
                    "sort_no": item["sort_no"],
                    "abnormal_flag": "",
                })
            plan["day_status"] = "submitted"
            plan["submitted_at"] = iso()
            plan["reject_reason"] = ""
            plan["day_progress"] = 0.0
            reviewer = direct_manager(store, user)
            plan["reviewer_id"] = reviewer["user_id"] if reviewer else ""
            from backend.ns_common import create_reminder
            if reviewer:
                create_reminder(
                    store, reviewer["user_id"], "daily_plan", plan["plan_id"],
                    "checkpoint", "yellow", "待审核：%s 的日计划" % user["display_name"],
                    "%s 提交了 %s 的日计划（%d 项 / %s 小时），请审核。" % (
                        user["display_name"], date_str, len(items), total_hours),
                    trigger_id="plan-%s" % plan["plan_id"],
                    role="manager",
                    dedupe_key="plan_review:%s:%s" % (plan["plan_id"], date_str),
                )
            write_store(store)

        return {
            "plan_id": plan["plan_id"],
            "day_status": plan["day_status"],
            "total_hours": total_hours,
            "reviewer_name": reviewer["display_name"] if reviewer else "",
            "near_full_load": total_hours > 10,
        }

    def _plan_total(store, plan):
        return f1(sum(float(it.get("plan_hours") or 0) for it in plan_items(store, plan["plan_id"])))

    # ------------------------------------------------------------------ 审核
    @app.post("/api/ns/today/plan-approve")
    @ns_api
    def ns_api_today_plan_approve():
        body = payload()
        plan_id = (body.get("plan_id") or "").strip()
        action = body.get("action") or "approve"
        reason = (body.get("reason") or "").strip()
        if action not in ("approve", "reject"):
            fail(1001, "action 仅支持 approve / reject。")

        with DATA_LOCK:
            store = read_store()
            user = ns_user(store)
            plan = next((p for p in store["daily_plan"] if p["plan_id"] == plan_id), None)
            if not plan:
                fail(1003, "日计划不存在。")
            owner = next((u for u in store["user"] if u["user_id"] == plan.get("user_id")), None)
            if owner is None:
                fail(1003, "日计划所属用户不存在。")
            reviewer = direct_manager(store, owner)
            if not (user["user_id"] == (reviewer or {}).get("user_id") or _has_review_perm(store, user)):
                fail(1008, "仅直接上级或系统管理员可以审核该日计划。")
            if user["user_id"] == plan.get("user_id"):
                fail(1008, "员工不可自审自己的日计划。")
            if plan.get("day_status") != "submitted":
                fail(1004, "仅「待审核」状态的日计划可以审批。")

            if action == "reject":
                if not reason:
                    fail(1001, "驳回必须填写理由。")
                plan["day_status"] = "rejected"
                plan["reject_reason"] = reason
                plan["reviewer_id"] = user["user_id"]
                plan["reviewed_at"] = iso()
                from backend.ns_common import create_reminder
                create_reminder(
                    store, plan["user_id"], "daily_plan", plan["plan_id"],
                    "checkpoint", "yellow", "日计划被驳回：%s" % plan["plan_date"],
                    "%s 驳回了你的日计划：%s。计划项已保留，可调整后重新提交。" % (user["display_name"], reason),
                    trigger_id="plan-%s" % plan["plan_id"], role="staff",
                    dedupe_key="plan_reject:%s:%s" % (plan["plan_id"], plan["reviewed_at"][:16]),
                )
                write_store(store)
                return {"day_status": "rejected", "reject_reason": reason}

            plan["day_status"] = "approved"
            plan["reviewer_id"] = user["user_id"]
            plan["reviewed_at"] = iso()
            items = plan_items(store, plan["plan_id"])
            plan["day_progress"] = compute_day_progress(items)
            touched = set()
            for item in items:
                task = task_by_id(store, item.get("task_id"))
                if task:
                    rollup_task(store, task)
                    touched.add(task.get("objective_id"))
            for objective_id in touched:
                obj = obj_by_id(store, objective_id)
                if obj:
                    rollup_objective(store, obj)
            from backend.ns_common import create_reminder
            create_reminder(
                store, plan["user_id"], "daily_plan", plan["plan_id"],
                "checkpoint", "green", "日计划已通过：%s" % plan["plan_date"],
                "%s 已通过你的日计划，可以开始拉条更新进度。" % user["display_name"],
                trigger_id="plan-%s" % plan["plan_id"], role="staff",
                dedupe_key="plan_approve:%s:%s" % (plan["plan_id"], plan["reviewed_at"][:16]),
            )
            write_store(store)

        return {"day_status": "approved", "day_progress": plan["day_progress"], "item_count": len(items)}

    # ------------------------------------------------------------------ 拉条
    @app.post("/api/ns/today/progress")
    @ns_api
    def ns_api_today_progress():
        body = payload()
        plan_id = (body.get("plan_id") or "").strip()
        action = body.get("action") or "save"

        with DATA_LOCK:
            store = read_store()
            user = ns_user(store)
            plan = next((p for p in store["daily_plan"] if p["plan_id"] == plan_id), None)
            if not plan:
                fail(1003, "日计划不存在。")
            if plan.get("user_id") != user["user_id"]:
                fail(1008, "管理者不能代改员工的进度条；请在只读视图查看。")
            if plan.get("day_status") != "approved":
                fail(1011, "日计划未通过审核，禁止拉条（当前状态：%s）。" % DAILY_STATUS.get(plan.get("day_status")))
            if plan.get("plan_date") != now().date().isoformat():
                fail(1004, "该日期已定型，仅当天可以拉条；历史修正请走进度更新（F-201）。")

            items = plan_items(store, plan["plan_id"])
            by_id = {item["item_id"]: item for item in items}

            if action == "block":
                item_id = (body.get("item_id") or "").strip()
                block_reason = (body.get("block_reason") or "").strip()
                if not block_reason:
                    fail(1001, "标记阻塞必须填写原因。")
                item = by_id.get(item_id)
                if not item:
                    fail(1003, "计划项不存在。")
                item["abnormal_flag"] = "阻塞"
                task = task_by_id(store, item.get("task_id"))
                if task:
                    task["blocked_reason"] = block_reason
                    if task.get("status") not in ("done", "cancelled"):
                        task["status"] = "blocked"
                reviewer = direct_manager(store, user)
                from backend.ns_common import create_reminder
                if reviewer:
                    create_reminder(
                        store, reviewer["user_id"], "task", item.get("task_id"),
                        "deviation", "red",
                        "风险上报：%s" % (task["title"] if task else item.get("task_id")),
                        "%s 在今日计划中标记阻塞：%s。该日不计入连续异常判定。" % (user["display_name"], block_reason),
                        trigger_id="risk-%s" % item_id, role="manager",
                        dedupe_key="risk:%s:%s" % (item_id, plan["plan_date"]),
                    )
                log_id = next_id(store, "progress_log", "LOG", 4)
                store["progress_log"].insert(0, {
                    "log_id": log_id, "target_type": "task", "target_id": item.get("task_id"),
                    "progress": f1(item.get("progress")), "note": "风险上报：%s" % block_reason,
                    "log_type": "progress", "operator_id": user["user_id"], "logged_at": iso(),
                    "source": "daily",
                })
                plan["day_progress"] = compute_day_progress(items)
                write_store(store)
                return {"day_status": plan["day_status"], "day_progress": plan["day_progress"],
                        "abnormal_flag": "阻塞", "streak": streak_info(store, user["user_id"])}

            updates = body.get("items")
            if not isinstance(updates, list) or not updates:
                fail(1001, "请至少提供一个计划项的进度。")
            note = (body.get("note") or "").strip()
            touched = set()
            for raw in updates:
                item = by_id.get(raw.get("item_id"))
                if not item:
                    fail(1003, "计划项 %s 不存在。" % raw.get("item_id"))
                try:
                    value = float(raw.get("progress"))
                except (TypeError, ValueError):
                    fail(1002, "进度必须是 0~100 的数字。")
                if not 0 <= value <= 100:
                    fail(1002, "进度必须在 0~100 之间。")
                before = f1(item.get("progress"))
                item["progress"] = f1(value)
                item["done"] = value >= 100
                if f1(value) != before:
                    log_id = next_id(store, "progress_log", "LOG", 4)
                    store["progress_log"].insert(0, {
                        "log_id": log_id, "target_type": "task", "target_id": item.get("task_id"),
                        "progress": f1(value),
                        "note": note or ("日计划拉条：%s" % (item.get("plan_desc") or "")),
                        "log_type": "progress", "operator_id": user["user_id"], "logged_at": iso(),
                        "source": "daily",
                    })
                touched.add(item.get("task_id"))
            plan["day_progress"] = compute_day_progress(items)
            if not plan.get("ack_at"):
                plan["ack_at"] = iso()
            objective_ids = set()
            for task_id in touched:
                task = task_by_id(store, task_id)
                if task:
                    rollup_task(store, task)
                    objective_ids.add(task.get("objective_id"))
            for objective_id in objective_ids:
                obj = obj_by_id(store, objective_id)
                if obj:
                    rollup_objective(store, obj)
            write_store(store)

        return {
            "plan_id": plan["plan_id"],
            "day_status": plan["day_status"],
            "day_progress": plan["day_progress"],
            "items": [_item_view(store, by_id[raw["item_id"]]) for raw in updates],
            "streak": streak_info(store, user["user_id"]),
        }

    # ------------------------------------------------------------------ 视图数据
    @app.get("/api/ns/today/views")
    @ns_api
    def ns_api_today_views():
        store = read_store()
        user = ns_user(store)
        granularity = request.args.get("granularity", "week")
        if granularity not in ("day", "week", "month"):
            fail(1001, "granularity 仅支持 day / week / month。")
        base_raw = request.args.get("base_date") or now().date().isoformat()
        base = parse_dt(base_raw)
        if not base:
            fail(1002, "base_date 格式不正确。")
        target_id = request.args.get("user_id") or user["user_id"]
        if target_id != user["user_id"] and not (is_manager(store, user) and in_visible(store, user, target_id)):
            fail(1008, "无权查看他人的视图数据。")
        wanted = request.args.get("views")
        payload_data = views_payload(store, target_id, base.date(), granularity)
        if wanted:
            keep = {v.strip() for v in wanted.split(",") if v.strip()}
            payload_data = {key: value for key, value in payload_data.items() if key in keep or key == "summary"}
        return payload_data

    # ------------------------------------------------------------------ 连续异常巡检（补偿入口）
    @app.post("/api/ns/today/alert-chain")
    @ns_api
    def ns_api_today_alert_chain():
        from backend import ns_scheduler
        body = payload()
        dry_run = bool(body.get("dry_run"))
        user_id = (body.get("user_id") or "").strip()
        as_of = (body.get("as_of_date") or "").strip()
        return ns_scheduler.alert_chain(user_id=user_id, as_of_date=as_of, dry_run=dry_run)

    # ------------------------------------------------------------------ 自建任务 / 挂目标（F-408）
    @app.get("/api/ns/task/list")
    @ns_api
    def ns_api_task_list():
        store = read_store()
        user = ns_user(store)
        target_id = request.args.get("user_id") or user["user_id"]
        if target_id != user["user_id"] and not (is_manager(store, user) and in_visible(store, user, target_id)):
            fail(1008, "无权查看他人的任务。")
        status = request.args.get("status", "")
        rows = []
        for task in store["work_task"]:
            if target_id not in assignee_ids(task):
                continue
            if status and task.get("status") != status:
                continue
            rows.append(_task_brief(store, task) | {
                "status": task.get("status"),
                "plan_start": task.get("plan_start", ""),
                "plan_hours": task.get("plan_hours"),
                "detail": task.get("detail", ""),
                "priority": task.get("priority", "中"),
                "assignees": [user_name(store, uid) for uid in assignee_ids(task)],
            })
        return {"tasks": rows, "available": available_tasks(store, target_id)}

    @app.post("/api/ns/task/save")
    @ns_api
    def ns_api_task_save():
        body = payload()
        with DATA_LOCK:
            store = read_store()
            user = ns_user(store)
            task_id = (body.get("task_id") or "").strip()
            title = (body.get("title") or "").strip()
            plan_start = (body.get("plan_start") or "").strip()
            plan_end = (body.get("plan_end") or "").strip()
            if not title or len(title) > 60:
                fail(1001, "任务标题必填且不超过 60 字。")
            if not plan_start or not plan_end or not parse_dt(plan_start) or not parse_dt(plan_end):
                fail(1001, "任务起止日期必填且格式正确。")
            if plan_end < plan_start:
                fail(1002, "结束日期不能早于开始日期。")
            priority = body.get("priority") or "中"
            if priority not in ("高", "中", "低"):
                fail(1002, "优先级仅支持 高 / 中 / 低。")
            detail = (body.get("detail") or "").strip()

            if task_id:
                task = task_by_id(store, task_id)
                if not task:
                    fail(1003, "任务不存在。")
                if user["user_id"] not in assignee_ids(task):
                    fail(1008, "只有任务执行人可以编辑该任务。")
                before = {"title": task["title"], "plan_end": task.get("plan_end")}
                task.update({"title": title, "detail": detail, "priority": priority,
                             "plan_start": plan_start, "plan_end": plan_end, "updated_at": iso()})
                from backend.ns_common import audit
                audit(store, user, "更新自建任务", "work_task", task_id, before, {"title": title})
                write_store(store)
                return {"task_id": task_id, "updated": True}

            from backend.ns_common import api_perm
            api_perm(store, user, "action:task.create")
            new_id = next_id(store, "work_task", "T", 3)
            store["work_task"].insert(0, {
                "task_id": new_id,
                "title": title,
                "detail": detail,
                "parent_task_id": "",
                "objective_id": "",
                "kr_id": "",
                "priority": priority,
                "plan_hours": float(body.get("plan_hours") or 8),
                "plan_start": plan_start,
                "plan_end": plan_end,
                "progress": 0.0,
                "exec_rate": 0.0,
                "status": "todo",
                "source": "self",
                "is_demo": False,
                "manual_override": False,
                "blocked_reason": "",
                "assignees": [{"user_id": user["user_id"], "role_in_task": "R", "weight": 100, "is_owner": True}],
                "created_at": iso(),
                "updated_at": iso(),
            })
            for a in store["work_task"][0]["assignees"]:
                store["task_assignee"].append({
                    "task_id": new_id, "user_id": a["user_id"], "role_in_task": a["role_in_task"],
                    "weight": a["weight"], "is_owner": a["is_owner"],
                })
            write_store(store)
        return {"task_id": new_id, "created": True, "message": "自建任务已创建，默认进入个人清单，不挂目标树。"}

    @app.post("/api/ns/task/link-objective")
    @ns_api
    def ns_api_task_link_objective():
        body = payload()
        task_id = (body.get("task_id") or "").strip()
        objective_id = (body.get("objective_id") or "").strip()
        with DATA_LOCK:
            store = read_store()
            user = ns_user(store)
            task = task_by_id(store, task_id)
            if not task:
                fail(1003, "任务不存在。")
            if user["user_id"] not in assignee_ids(task):
                fail(1008, "只有任务执行人可以申请挂载目标。")
            obj = obj_by_id(store, objective_id)
            if not obj:
                fail(1003, "目标不存在。")
            if obj.get("status") not in ("published", "executing", "confirmed"):
                fail(1004, "仅执行中的目标可以被挂载。")
            if task.get("objective_id"):
                fail(1004, "该任务已挂载目标：%s。" % task["objective_id"])
            task["pending_objective_id"] = objective_id
            task["link_status"] = "pending_confirm"
            reviewer = direct_manager(store, user)
            from backend.ns_common import create_reminder
            if reviewer:
                create_reminder(
                    store, reviewer["user_id"], "task", task_id, "checkpoint", "yellow",
                    "待确认归属：%s" % task["title"],
                    "%s 申请将自建任务挂到「%s」，请确认。" % (user["display_name"], obj["title"]),
                    trigger_id="link-%s" % task_id, role="manager",
                    dedupe_key="link:%s:%s" % (task_id, objective_id),
                )
            write_store(store)
        return {"task_id": task_id, "pending_objective_id": objective_id, "link_status": "pending_confirm"}


def _has_review_perm(store, user):
    from backend.core import has_perm, role_of_user
    role = role_of_user(store, user["user_id"])
    if (role or {}).get("scope_type") == "ALL":
        return True
    return has_perm(store, user, "action:daily.review")
