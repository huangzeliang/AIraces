"""B2 预警与指导（F-206~210 / F-220 / F-413）—— /ns/warning 页面 + 预警 / 提醒 / 指导接口。

口径要点：
- 三色预警与检查点复用 ns_common 的统一计算（列表、拆解树、工作台同一口径）；
- 连续异常复用 ns_scheduler 的判定与 2.7.4 双角色文案；
- AI 过程指导：LLM 可用时走大模型，超时 / 未配置自动降级为规则建议（degraded=true），
  建议只给具体动作（拆小步 / 调优先级 / 找资源 / 下一步），禁止评价性措辞；
- 提醒中心：状态过滤 unread|read|acked|ignored，ack（已知悉）返回 3 天抑制截止日期。
"""

import json
from datetime import timedelta

from flask import request

from backend.core import (
    ALERT_TYPES,
    DATA_LOCK,
    fail,
    f1,
    iso,
    next_id,
    now,
    paginate,
    parse_dt,
    read_store,
    setting_value,
    write_store,
)
from backend.ns_common import (
    api_perm,
    assignee_ids,
    audit,
    can_edit_progress,
    checkpoints_of,
    compute_task_alerts,
    create_reminder,
    direct_manager,
    in_visible,
    is_manager,
    kr_by_id,
    level_label,
    ns_api,
    ns_render,
    ns_user,
    obj_by_id,
    payload,
    require_ns_menu,
    scope_objectives_check,
    streak_alerts,
    task_by_id,
    task_health,
    user_name,
    visible_user_ids,
)

SCENES = ("auto", "lagging", "deviation", "blocked", "streak")
STATUS_FILTER = {"unread", "read", "acked", "ignored"}
STATUS_CODE = {"未读": "unread", "已读": "read", "已忽略": "ignored"}


# ---------------------------------------------------------------------------
# 预警列表（统一口径 + 关键路径标注）
# ---------------------------------------------------------------------------

def _critical_ids(store):
    from backend import ns_deps

    return ns_deps.critical_snapshot(store)["critical_ids"]


def warning_items(store, user, level="", alert_type=""):
    critical = _critical_ids(store)
    items = compute_task_alerts(store, user) + streak_alerts(store, user)
    for item in items:
        item["is_critical"] = item.get("target_type") == "task" and item.get("target_id") in critical
        item["level_label"] = level_label(item.get("level"))
    if level:
        items = [item for item in items if item.get("level") == level]
    if alert_type:
        items = [item for item in items if item.get("alert_type") == alert_type]
    return items


def checkpoint_rows(store, user):
    scope_ids = visible_user_ids(store, user)
    rows = []
    for task in store["work_task"]:
        if task.get("status") in ("done", "cancelled"):
            continue
        if scope_ids is not None and not any(uid in scope_ids for uid in assignee_ids(task)):
            continue
        points = checkpoints_of(store, task)
        if not points:
            continue
        state_label = {"upcoming": "即将到来", "passed": "已完成", "overdue": "已到期未完成"}
        for point in points:
            rows.append({
                **point,
                "title": task["title"],
                "owner_name": "、".join(user_name(store, uid) for uid in assignee_ids(task)),
                "state_label": state_label.get(point["state"], point["state"]),
                "deep_link": "/ns/progress?task_id=%s&from=warning" % task["task_id"],
            })
    order = {"overdue": 0, "upcoming": 1, "passed": 2}
    return sorted(rows, key=lambda row: (order.get(row["state"], 3), row["date"]))


def _summary(items, checkpoints):
    counts = {"red": 0, "yellow": 0, "green": 0}
    for item in items:
        counts[item.get("level", "green")] = counts.get(item.get("level", "green"), 0) + 1
    return {
        **counts, "total": len(items),
        "streak": sum(1 for item in items if item.get("alert_type") == "daily_streak"),
        "checkpoints": len([c for c in checkpoints if c["state"] != "passed"]),
    }


# ---------------------------------------------------------------------------
# 提醒中心
# ---------------------------------------------------------------------------

def _deep_link(store, reminder, self_id):
    target_type, target_id = reminder.get("target_type"), reminder.get("target_id")
    if target_type == "task" and target_id:
        return "/ns/progress?task_id=%s&from=reminder" % target_id
    if target_type == "user" and target_id:
        if target_id == self_id:
            return "/ns/today?from=reminder"
        return "/ns/today?user_id=%s&from=reminder" % target_id
    if target_type == "daily_plan":
        plan = next((p for p in store["daily_plan"] if p["plan_id"] == target_id), None)
        if plan:
            suffix = "" if plan.get("user_id") == self_id else "&user_id=%s" % plan["user_id"]
            return "/ns/today?date=%s%s&from=reminder" % (plan.get("plan_date", ""), suffix)
        return "/ns/today?from=reminder"
    if target_type == "objective" and target_id:
        return "/ns/progress?objective_id=%s&from=reminder" % target_id
    return "/ns/workbench"


def reminder_row(store, reminder, self_id):
    status = reminder.get("status") or "未读"
    code = STATUS_CODE.get(status, "read")
    if reminder.get("ack_at"):
        code = "acked"
    return {
        "reminder_id": reminder["reminder_id"],
        "title": reminder.get("title", ""),
        "body": reminder.get("body", ""),
        "level": reminder.get("level", "yellow"),
        "level_label": level_label(reminder.get("level")),
        "alert_type": reminder.get("alert_type", ""),
        "alert_type_label": ALERT_TYPES.get(reminder.get("alert_type"), reminder.get("alert_type", "")),
        "status": status,
        "status_code": code,
        "role": reminder.get("role", ""),
        "trigger_id": reminder.get("trigger_id", ""),
        "target_type": reminder.get("target_type", ""),
        "target_id": reminder.get("target_id", ""),
        "meta": reminder.get("meta") or {},
        "created_at": reminder.get("created_at", ""),
        "read_at": reminder.get("read_at", ""),
        "ack_at": reminder.get("ack_at", ""),
        "deep_link": _deep_link(store, reminder, self_id),
    }


def _my_reminders(store, user, status="", alert_type="", level=""):
    rows = [r for r in store["reminder"] if r.get("user_id") == user["user_id"]]
    if status in ("read", "acked", "ignored"):
        rows = [r for r in rows
                if (r.get("ack_at") if status == "acked" else STATUS_CODE.get(r.get("status") or "未读") == status)]
        if status == "read":
            rows = [r for r in rows if not r.get("ack_at")]
    elif status == "unread":
        rows = [r for r in rows if (r.get("status") or "未读") == "未读"]
    if alert_type:
        rows = [r for r in rows if r.get("alert_type") == alert_type]
    if level:
        rows = [r for r in rows if r.get("level") == level]
    rows = sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)
    rows.sort(key=lambda r: 0 if (r.get("status") or "未读") == "未读" else 1)
    return rows


# ---------------------------------------------------------------------------
# AI 过程指导：LLM + 规则降级
# ---------------------------------------------------------------------------

def _task_context(store, task):
    metrics = task_health(store, task)
    end = parse_dt(task.get("plan_end"))
    days_left = (end.date() - now().date()).days if end else None
    return {
        "target": "task", "task_id": task["task_id"], "title": task["title"],
        "status": task.get("status"), "progress": f1(task.get("progress")),
        "time_progress": metrics["time_progress"], "delta": metrics["delta"],
        "health": metrics["health"], "plan_end": task.get("plan_end", ""),
        "days_left": days_left, "plan_hours": task.get("plan_hours", 0),
        "blocked_reason": task.get("blocked_reason", ""),
        "owners": [user_name(store, uid) for uid in assignee_ids(task)],
    }


def _objective_context(store, obj):
    from backend.ns_common import objective_health

    metrics = objective_health(store, obj)
    tasks = [t for t in store["work_task"] if t.get("objective_id") == obj["objective_id"]
             and t.get("status") != "cancelled"]
    return {
        "target": "objective", "objective_id": obj["objective_id"], "title": obj["title"],
        "status": obj.get("status"), "progress": f1(obj.get("progress")),
        "time_progress": metrics["time_progress"], "delta": metrics["delta"],
        "health": metrics["health"], "plan_end": obj.get("plan_end", ""),
        "success_criteria": obj.get("success_criteria") or [],
        "task_count": len(tasks),
        "lagging_tasks": [t["title"] for t in tasks if task_health(store, t)["health"] == "lagging"][:3],
    }


def _scene_of(context, scene):
    if scene and scene != "auto":
        return scene
    if context.get("target") == "task":
        if context.get("status") == "blocked":
            return "blocked"
        if context.get("days_left") is not None and context["days_left"] < 0 and context["progress"] < 100:
            return "deviation"
        if context.get("health") == "lagging":
            return "lagging"
        return "deviation"
    if context.get("health") == "lagging":
        return "lagging"
    return "deviation"


def _rule_guidance(store, user, target_type, context, scene):
    rows = []
    if target_type == "task":
        title = context["title"]
        progress = context["progress"]
        if scene == "blocked":
            reason = context.get("blocked_reason") or "阻塞原因待补充"
            rows = [
                {"action": "明确外部依赖的解除条件与对接人，并在任务上更新阻塞原因与预计解除时间",
                 "expect": "2 天内解除阻塞，任务恢复推进",
                 "basis": "任务当前标记为阻塞：%s" % reason},
                {"action": "与上级确认替代路径：先缩小交付范围或切换实现方式，保留可演示部分",
                 "expect": "即使依赖未解除，也能先完成 60%% 主体工作量",
                 "basis": "计划工时 %s h，计划结束 %s" % (context.get("plan_hours"), context.get("plan_end"))},
                {"action": "把不依赖阻塞点的子步骤拆出来并行推进，今天先完成其中 1 步并更新进度",
                 "expect": "当天进度有实际增量，不再原地等待",
                 "basis": "当前完成度 %s%%，等待成本会直接顺延下游任务" % progress},
            ]
        else:
            rows = [
                {"action": "把「%s」拆成 ≤2 天可完成的子步骤，今天先完成第一步并更新进度" % title,
                 "expect": "3 天内把完成度从 %s%% 提升到 %s%%" % (progress, min(100, int(progress + 20))),
                 "basis": "完成进度 %s%% vs 时间进度 %s%%（差值 %s pp）" % (
                     progress, context["time_progress"], context["delta"])},
                {"action": "将「%s」设为今日首要任务，其余非关键事项顺延" % title,
                 "expect": "当日计划完成率回到 100%",
                 "basis": "计划结束 %s，剩余 %s 天" % (
                     context.get("plan_end") or "—", context.get("days_left") if context.get("days_left") is not None else "—")},
                {"action": "评估资源支持：如剩余工作量超出个人产能，使用「需要支持」发起求助，由上级调整派工",
                 "expect": "必要时通过分担工作量降低延期风险",
                 "basis": "计划工时 %s h，责任人：%s" % (
                     context.get("plan_hours"), "、".join(context.get("owners") or []) or "—")},
            ]
    else:
        title = context["title"]
        lagging = context.get("lagging_tasks") or []
        rows = [
            {"action": "对照成功标准逐条盘点未达标条目，为每条补充本周内的补救动作与责任人",
             "expect": "本周内明确每条标准的补齐路径",
             "basis": "目标「%s」完成进度 %s%% vs 时间进度 %s%%" % (
                 title, context["progress"], context["time_progress"])},
            {"action": "把资源集中到滞后的关键任务上：%s" % ("、".join(lagging) if lagging else "先复核任务拆分粒度"),
             "expect": "先解决拖累整体节奏的关键任务",
             "basis": "当前有 %d 个任务滞后" % len(lagging)},
            {"action": "如计划或范围需要调整，通过「变更管理」发起变更并写明原因与影响",
             "expect": "计划调整留痕可追溯，避免临时口头变更",
             "basis": "目标共 %s 个在途任务，成功标准 %s 条" % (
                 context.get("task_count"), len(context.get("success_criteria") or []))},
        ]
    if scene == "streak":
        remaining = context.get("remaining_today")
        rows[0] = {
            "action": "今天先处理「%s」，其余事项按优先级顺延并同步给上级" % (context.get("suggest_task") or "最关键的任务"),
            "expect": "今日计划完成率达到 100%，中断连续异常",
            "basis": "连续 %s 天当日完成率未达 100%%" % context.get("days", 0),
        }
        if remaining is not None:
            rows[1] = {
                "action": "把剩余 %s 项拆小到 1 天内可完成，避免任务持续堆积" % remaining,
                "expect": "每天都能形成可见的完成量",
                "basis": "今日剩余未完成项 %s 项" % remaining,
            }
    return rows


def _llm_guidance(store, context, scene):
    from backend import llm as llm_mod

    if not llm_mod.llm_configured():
        return None
    timeout = int(setting_value(store, "llm_timeout", 8))
    system_prompt = (
        "你是项目管理助手。根据给定的过程数据，输出 3 条具体、可执行的调整建议，"
        "只给动作（拆小步 / 调优先级 / 找资源 / 下一步），禁止评价性、指责性措辞与绩效排名。"
        '只输出 JSON：{"guidance":[{"action":"...","expect":"...","basis":"..."}]}，中文，每条 60 字以内。'
    )
    parsed = llm_mod._llm_chat_json(
        system_prompt,
        json.dumps({"scene": scene, "context": context}, ensure_ascii=False),
        timeout=timeout,
    )
    rows = (parsed or {}).get("guidance") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        return None
    cleaned = []
    for row in rows[:3]:
        if not isinstance(row, dict) or not str(row.get("action") or "").strip():
            continue
        cleaned.append({
            "action": str(row["action"])[:120],
            "expect": str(row.get("expect") or "")[:80],
            "basis": str(row.get("basis") or "")[:120],
        })
    return cleaned or None


def generate_guidance(store, user, target_type, target_id, scene="auto"):
    if target_type not in ("objective", "kr", "task"):
        fail(1001, "target_type 仅支持 objective / kr / task。")
    if scene not in SCENES:
        fail(1001, "scene 仅支持 %s。" % " / ".join(SCENES))
    if target_type == "task":
        task = task_by_id(store, target_id)
        if not task:
            fail(1003, "任务不存在。")
        if not can_edit_progress(store, user, task) and not (
                is_manager(store, user) and any(in_visible(store, user, uid) for uid in assignee_ids(task))):
            fail(1003, "任务不存在或越权。")
        context = _task_context(store, task)
    elif target_type == "objective":
        obj = scope_objectives_check(store, user, target_id)
        context = _objective_context(store, obj)
    else:
        kr = kr_by_id(store, target_id)
        if not kr:
            fail(1003, "KR 不存在。")
        obj = scope_objectives_check(store, user, kr.get("objective_id") or "")
        context = _objective_context(store, obj)
        context["title"] = "%s（%s）" % (kr.get("kr_name", kr["kr_id"]), obj["title"])
    resolved = _scene_of(context, scene)
    guidance = _llm_guidance(store, context, resolved)
    degraded = False
    if not guidance:
        guidance = _rule_guidance(store, user, target_type, context, resolved)
        degraded = True
    for index, item in enumerate(guidance, start=1):
        item["adopt_id"] = "GD-%s-%s-%d" % (target_type, target_id, index)
    return {
        "target_type": target_type, "target_id": target_id,
        "scene": resolved, "scene_requested": scene,
        "degraded": degraded,
        "context": context,
        "guidance": guidance,
    }


def adopt_guidance(store, user, body):
    adopt_id = (body.get("adopt_id") or "").strip()
    if not adopt_id:
        fail(1001, "缺少 adopt_id。")
    target_type = body.get("target_type") or "task"
    target_id = (body.get("target_id") or "").strip()
    if target_type not in ("objective", "kr", "task") or not target_id:
        fail(1001, "target_type / target_id 不合法。")
    if target_type == "objective":
        scope_objectives_check(store, user, target_id)
        current = f1((obj_by_id(store, target_id) or {}).get("progress"))
    elif target_type == "task":
        task = task_by_id(store, target_id)
        if not task:
            fail(1003, "任务不存在。")
        if not can_edit_progress(store, user, task) and not is_manager(store, user):
            fail(1008, "无权操作该任务。")
        current = f1(task.get("progress"))
    else:
        kr = kr_by_id(store, target_id)
        if not kr:
            fail(1003, "KR 不存在。")
        scope_objectives_check(store, user, kr.get("objective_id") or "")
        current = f1(kr.get("current_value"))
    existing = next((log for log in store["progress_log"]
                     if log.get("adopt_id") == adopt_id and log.get("operator_id") == user["user_id"]), None)
    if body.get("undo"):
        if not existing:
            fail(1003, "未找到已采纳记录。")
        store["progress_log"].remove(existing)
        audit(store, user, "撤销采纳建议", target_type, target_id, {"adopt_id": adopt_id}, None)
        return {"adopt_id": adopt_id, "adopted": False, "undone": True}
    if existing:
        return {"adopt_id": adopt_id, "adopted": True, "log_id": existing["log_id"], "duplicated": True}
    action = (body.get("action") or "").strip()
    expect = (body.get("expect") or "").strip()
    basis = (body.get("basis") or "").strip()
    note = "采纳 AI 过程指导：%s" % (action or adopt_id)
    if expect:
        note += "（预期：%s）" % expect
    log = {
        "log_id": next_id(store, "progress_log", "LOG", 4),
        "target_type": target_type,
        "target_id": target_id,
        "progress": current,
        "note": note,
        "operator_id": user["user_id"],
        "logged_at": iso(),
        "source": "guidance",
        "log_type": "guidance",
        "adopt_id": adopt_id,
        "basis": basis,
    }
    store["progress_log"].insert(0, log)
    audit(store, user, "采纳 AI 建议", target_type, target_id, None, {"adopt_id": adopt_id, "action": action})
    return {"adopt_id": adopt_id, "adopted": True, "log_id": log["log_id"], "note": note}


def register(app, services):
    @app.get("/ns/warning")
    @require_ns_menu("warning")
    def ns_warning():
        store = read_store()
        user = ns_user(store)
        level = (request.args.get("level") or "").strip()
        alert_type = (request.args.get("alert_type") or "").strip()
        items = warning_items(store, user, level, alert_type)
        checkpoints = checkpoint_rows(store, user)
        from backend import ns_scheduler

        reminders = _my_reminders(store, user)[:30]
        return ns_render(
            "ns/warning.html", "warning", "预警与指导",
            "三色预警 + 检查点 + 连续异常双角色提醒 + AI 过程指导",
            store=store, user=user,
            alert_items=items[:80],
            alerts_total=len(items),
            summary=_summary(items, checkpoints),
            checkpoints=checkpoints[:40],
            streak_rows=ns_scheduler.streak_overview(store, user),
            reminders=[reminder_row(store, r, user["user_id"]) for r in reminders],
            unread=int(sum(1 for r in store["reminder"]
                           if r.get("user_id") == user["user_id"] and (r.get("status") or "未读") == "未读")),
            level_filter=level,
            type_filter=alert_type,
            alert_types=[{"code": code, "label": label} for code, label in ALERT_TYPES.items()],
            my_tasks=[t for t in store["work_task"]
                      if t.get("status") not in ("done", "cancelled")
                      and (is_manager(store, user) and any(in_visible(store, user, uid) for uid in assignee_ids(t))
                           or user["user_id"] in assignee_ids(t))][:80],
        )

    @app.get("/api/ns/warning/list")
    @ns_api
    def ns_api_warning_list():
        store = read_store()
        user = ns_user(store)
        level = (request.args.get("level") or "").strip()
        alert_type = (request.args.get("alert_type") or "").strip()
        if level and level not in ("green", "yellow", "red"):
            fail(1001, "level 仅支持 green / yellow / red。")
        if alert_type and alert_type not in ALERT_TYPES:
            fail(1001, "alert_type 不合法。")
        items = warning_items(store, user, level, alert_type)
        checkpoints = checkpoint_rows(store, user)
        return {
            "items": items,
            "summary": _summary(items, checkpoints),
            "checkpoints": checkpoints,
            "updated_at": iso(),
        }

    @app.get("/api/ns/reminder/list")
    @ns_api
    def ns_api_reminder_list():
        store = read_store()
        user = ns_user(store)
        status = (request.args.get("status") or "").strip()
        alert_type = (request.args.get("alert_type") or "").strip()
        level = (request.args.get("level") or "").strip()
        if status and status not in STATUS_FILTER:
            fail(1001, "status 仅支持 unread / read / acked / ignored。")
        if alert_type and alert_type not in ALERT_TYPES:
            fail(1001, "alert_type 不合法。")
        if level and level not in ("green", "yellow", "red"):
            fail(1001, "level 仅支持 green / yellow / red。")
        try:
            page = int(request.args.get("page", 1))
            size = int(request.args.get("page_size", 20))
        except (TypeError, ValueError):
            fail(1001, "page / page_size 需为整数。")
        rows = _my_reminders(store, user, status, alert_type, level)
        rows_page, page, total_pages = paginate(rows, max(1, page), min(max(size, 5), 100))
        return {
            "items": [reminder_row(store, r, user["user_id"]) for r in rows_page],
            "page": page, "page_size": size,
            "total": len(rows), "total_pages": total_pages,
            "unread_count": sum(1 for r in store["reminder"]
                                if r.get("user_id") == user["user_id"] and (r.get("status") or "未读") == "未读"),
        }

    @app.post("/api/ns/reminder/read")
    @ns_api
    def ns_api_reminder_read():
        body = payload()
        action = (body.get("action") or "").strip()
        if action not in ("read", "ack", "ignore", "all"):
            fail(1001, "action 仅支持 read / ack / ignore / all。")
        with DATA_LOCK:
            store = read_store()
            user = ns_user(store)
            mine = [r for r in store["reminder"] if r.get("user_id") == user["user_id"]]
            if action == "all":
                affected = 0
                for reminder in mine:
                    if (reminder.get("status") or "未读") == "未读":
                        reminder["status"] = "已读"
                        reminder["read_at"] = iso()
                        affected += 1
                if affected:
                    write_store(store)
                return {"affected": affected, "unread_count": 0}
            ids = body.get("reminder_ids") or []
            if not ids:
                fail(1001, "action ≠ all 时须提供 reminder_ids。")
            affected = 0
            suppress_until = ""
            for reminder_id in ids:
                reminder = next((r for r in mine if r["reminder_id"] == reminder_id), None)
                if not reminder:
                    fail(1003, "提醒 %s 不存在或非本人。" % reminder_id)
                if action == "read":
                    reminder["status"] = "已读"
                    reminder["read_at"] = reminder.get("read_at") or iso()
                elif action == "ack":
                    reminder["status"] = "已读"
                    reminder["read_at"] = reminder.get("read_at") or iso()
                    reminder["ack_at"] = iso()
                    suppress_until = (now().date() + timedelta(days=3)).isoformat()
                else:
                    reminder["status"] = "已忽略"
                affected += 1
            write_store(store)
            unread = sum(1 for r in mine if (r.get("status") or "未读") == "未读")
            result = {"affected": affected, "unread_count": unread}
            if suppress_until:
                result["suppress_until"] = suppress_until
            return result

    @app.post("/api/ns/guidance/generate")
    @ns_api
    def ns_api_guidance_generate():
        body = payload()
        with DATA_LOCK:
            store = read_store()
            user = ns_user(store)
            api_perm(store, user, "action:guidance.generate")
            if body.get("adopt_id"):
                result = adopt_guidance(store, user, body)
                write_store(store)
                return result
            result = generate_guidance(
                store, user,
                (body.get("target_type") or "").strip(),
                (body.get("target_id") or "").strip(),
                (body.get("scene") or "auto").strip(),
            )
            return result

    @app.post("/api/ns/guidance/adopt")
    @ns_api
    def ns_api_guidance_adopt():
        body = payload()
        with DATA_LOCK:
            store = read_store()
            user = ns_user(store)
            api_perm(store, user, "action:guidance.generate")
            result = adopt_guidance(store, user, body)
            write_store(store)
            return result

    @app.post("/api/ns/task/risk")
    @ns_api
    def ns_api_task_risk():
        """F-209 风险上报 / 求助：须填阻塞原因，置任务阻塞并通知直接上级。"""
        body = payload()
        task_id = (body.get("task_id") or "").strip()
        reason = (body.get("reason") or "").strip()
        if not task_id:
            fail(1001, "缺少 task_id。")
        if not reason:
            fail(1001, "阻塞原因必填。")
        if len(reason) > 200:
            fail(1002, "阻塞原因不能超过 200 字符。")
        with DATA_LOCK:
            store = read_store()
            user = ns_user(store)
            task = task_by_id(store, task_id)
            if not task:
                fail(1003, "任务不存在。")
            if user["user_id"] not in assignee_ids(task) and not is_manager(store, user):
                fail(1008, "仅任务执行人或管理者可发起风险上报。")
            if task.get("status") in ("done", "cancelled"):
                fail(1004, "任务已完成或取消，无法上报阻塞。")
            before = {"status": task.get("status"), "blocked_reason": task.get("blocked_reason", "")}
            task["status"] = "blocked"
            task["blocked_reason"] = reason
            task["updated_at"] = iso()
            store["progress_log"].insert(0, {
                "log_id": next_id(store, "progress_log", "LOG", 4),
                "target_type": "task", "target_id": task_id,
                "progress": f1(task.get("progress")),
                "note": "风险上报（阻塞）：%s" % reason,
                "operator_id": user["user_id"], "logged_at": iso(),
                "source": "manual", "log_type": "risk",
            })
            manager = direct_manager(store, user)
            if manager:
                create_reminder(
                    store, manager["user_id"], "task", task_id, "deviation", "red",
                    "风险上报：%s" % task["title"],
                    "%s 上报阻塞：%s。请评估是否需要调整派工或发起变更。" % (user["display_name"], reason),
                    dedupe_key="risk:%s:%s" % (task_id, now().date().isoformat()),
                )
            audit(store, user, "风险上报", "work_task", task_id, before,
                  {"status": "blocked", "reason": reason})
            write_store(store)
            return {
                "task_id": task_id, "status": "blocked", "status_label": "阻塞",
                "blocked_reason": reason,
                "notified_manager": user_name(store, manager["user_id"]) if manager else "",
            }
