"""A 线 · 计划与解析线（A1 计划录入 / A2 提炼确认 + A4 一句话办事 / A5 依据说明）。

对应需求：REQ-001/002/003/014/017（F-101/102/103/104/801/805）
归属文件：backend/plan.py · pages/plan_input.* · pages/plan_confirm.*

落库闭环沿用全局约定：读取 → 校验 → 加锁写 → 刷新展示。
解析与拆解均走 backend/llm.py（规则引擎兜底，可配置大模型），失败不落半成品数据。
接口同时接受 JSON（AI 自测脚本）与表单（页面）两种提交方式。
"""

from datetime import datetime
from importlib import import_module

from flask import flash, g, jsonify, redirect, render_template, request, url_for

auth = import_module("backend.auth")
llm = import_module("backend.llm")
sched = import_module("backend.schedule")
scope = import_module("backend.scope")

EXAMPLES = (
    {"label": "项目立项", "text": "下周前完成项目立项：先整理立项材料，再组织评审会，最后把评审意见落实到方案里。"},
    {"label": "季度运维准备", "text": "月底前完成四季度运维保障准备：梳理值班安排、检查监控告警阈值、更新应急预案并组织一次演练。"},
    {"label": "客户反馈整理", "text": "明天下午前把客户反馈的五个问题整理成清单，标注优先级并同步给产品团队。"},
)

VISIBLE_TASK_STATUS = ("待安排", "待执行", "进行中", "已延期")


def _now():
    return datetime.now().replace(microsecond=0)


def plan_state(plan):
    """种子数据没有 status 字段，一律视为已确认。"""
    return (plan.get("status") or "已确认")


def _next_plan_id(plans, moment):
    prefix = "P%s" % moment.strftime("%Y%m%d")
    used = set()
    for plan in plans:
        plan_id = plan.get("plan_id") or ""
        if plan_id.startswith(prefix) and plan_id[len(prefix):].isdigit():
            used.add(int(plan_id[len(prefix):]))
    index = 1
    while index in used:
        index += 1
    return "%s%03d" % (prefix, index)


def _next_seq_id(items, key, prefix, width=3):
    used = {item.get(key) for item in items}
    index = 1
    while ("%s%0*d" % (prefix, width, index)) in used:
        index += 1
    return "%s%0*d" % (prefix, width, index)


def _time_input_value(value):
    """ISO 时间转 <input type=datetime-local> 需要的 YYYY-MM-DDTHH:MM。"""
    moment = llm.parse_dt(value)
    return moment.strftime("%Y-%m-%dT%H:%M") if moment else ""


def _time_display(value):
    moment = llm.parse_dt(value)
    return moment.strftime("%m-%d %H:%M") if moment else ""


def _recent_plans(store):
    plans = sorted(store.get("plans", []), key=lambda p: p.get("created_at") or "", reverse=True)[:5]
    items = []
    for plan in plans:
        tasks = [t for t in store.get("tasks", []) if t.get("plan_id") == plan.get("plan_id")]
        pending = [t for t in tasks if t.get("status") in VISIBLE_TASK_STATUS]
        items.append({
            "plan_id": plan.get("plan_id") or "",
            "goal": plan.get("goal") or "",
            "state": plan_state(plan),
            "created_at": (plan.get("created_at") or "")[:10],
            "task_total": len(tasks),
            "pending": len(pending),
        })
    return items


def _latest_basis(store):
    plans = sorted(store.get("plans", []), key=lambda p: p.get("created_at") or "", reverse=True)
    for plan in plans:
        if plan.get("basis"):
            return {"goal": plan.get("goal") or "", "lines": plan.get("basis")[:6]}
    return None


def _collect_confirm_payload(data, is_json):
    """收集并校验确认提交；返回 (payload, errors)。"""
    if is_json:
        todos_raw = data.get("todos") or []
    else:
        try:
            import json as _json
            todos_raw = _json.loads(request.form.get("todos") or "[]")
        except ValueError:
            todos_raw = []

    plan_id = str(data.get("plan_id") or "").strip()
    goal = str(data.get("goal") or "").strip()
    errors = []
    if not goal:
        errors.append("目标不能为空。")
    elif len(goal) > 200:
        errors.append("目标不能超过 200 个字符。")
    if not isinstance(todos_raw, list):
        todos_raw = []
        errors.append("待办数据格式无效。")

    todos = []
    for index, item in enumerate(todos_raw, start=1):
        if not isinstance(item, dict):
            errors.append("第 %d 条待办格式无效。" % index)
            continue
        content = str(item.get("content") or "").strip()
        key_time_raw = str(item.get("key_time") or "").strip()
        if not content:
            errors.append("第 %d 条待办内容不能为空。" % index)
            continue
        if len(content) > 120:
            errors.append("第 %d 条待办内容不能超过 120 个字符。" % index)
            continue
        key_time = ""
        if key_time_raw:
            moment = llm.parse_dt(key_time_raw)
            if moment is None:
                errors.append("第 %d 条待办的关键时间格式无效。" % index)
                continue
            key_time = llm.iso(moment)
        todos.append({
            "todo_id": str(item.get("todo_id") or "").strip(),
            "content": content,
            "key_time": key_time,
        })
    if not todos and not errors:
        errors.append("至少保留一条待办。")
    return {"plan_id": plan_id, "goal": goal, "todos": todos}, errors


def register(app, services):
    read_store = services.read_store
    write_store = services.write_store
    read_settings = services.read_settings
    store_lock = services.store_lock

    # ------------------------------------------------------------------
    # A1 计划录入
    # ------------------------------------------------------------------
    @app.get("/plan/input")
    def plan_input():
        store = scope.scope_store(read_store(), g.current_user)
        return render_template(
            "pages/plan_input.html",
            active_page="plan_input",
            page_name="计划录入",
            settings=read_settings(),
            examples=EXAMPLES,
            prefill=request.args.get("raw_text", "")[:2000],
            recent=_recent_plans(store),
            last_basis=_latest_basis(store),
        )

    @app.post("/plan/parse")
    def plan_parse():
        payload = request.get_json(silent=True)
        is_json = payload is not None
        raw_text = str((payload.get("raw_text") if is_json else request.form.get("raw_text")) or "").strip()

        error = None
        if not raw_text:
            error = "原始文本不能为空，请先输入一段计划描述。"
        elif len(raw_text) > 2000:
            error = "原始文本不能超过 2000 个字符（当前 %d 字）。" % len(raw_text)
        if error:
            if is_json:
                return jsonify({"ok": False, "message": error}), 400
            flash(error, "error")
            return redirect(url_for("plan_input"))

        try:
            parsed = llm.parse_plan(raw_text)
        except Exception:
            parsed = None
        if not parsed or not parsed.get("todos"):
            message = "解析失败，请调整描述后重试。"
            if is_json:
                return jsonify({"ok": False, "message": message}), 400
            flash(message, "error")
            return redirect(url_for("plan_input"))

        now = _now()
        with store_lock:
            store = read_store()
            plan_id = _next_plan_id(store.get("plans", []), now)
            store.setdefault("plans", []).append({
                "plan_id": plan_id,
                "user_id": g.current_user["id"],
                "raw_text": raw_text,
                "goal": parsed["goal"],
                "status": "待确认",
                "parse_engine": parsed.get("engine") or "",
                "basis": parsed.get("basis") or [],
                "created_at": llm.iso(now),
            })
            for item in parsed["todos"]:
                todo_id = _next_seq_id(store.setdefault("todos", []), "todo_id", "D")
                store["todos"].append({
                    "todo_id": todo_id,
                    "plan_id": plan_id,
                    "content": item["content"],
                    "key_time": item.get("key_time") or "",
                })
            write_store(store)

        if is_json:
            return jsonify({
                "ok": True,
                "plan_id": plan_id,
                "todo_count": len(parsed["todos"]),
                "redirect": url_for("plan_confirm", plan_id=plan_id),
            })
        flash("解析完成，请核对提炼结果。", "success")
        return redirect(url_for("plan_confirm", plan_id=plan_id))

    # ------------------------------------------------------------------
    # A2 提炼确认
    # ------------------------------------------------------------------
    @app.get("/plan/confirm")
    def plan_confirm():
        plan_id = request.args.get("plan_id", "").strip()
        store = scope.scope_store(read_store(), g.current_user)
        plan = next((p for p in store.get("plans", []) if p.get("plan_id") == plan_id), None) if plan_id else None

        todo_views, key_times = [], []
        task_total = pending_new = 0
        schedule_risks = []
        if plan:
            todos = sorted(
                [t for t in store.get("todos", []) if t.get("plan_id") == plan_id],
                key=lambda t: t.get("todo_id") or "",
            )
            tasks = [t for t in store.get("tasks", []) if t.get("plan_id") == plan_id]
            task_total = len(tasks)
            task_todos = {t.get("todo_id") for t in tasks}
            pending_new = sum(1 for t in todos if t.get("todo_id") not in task_todos)
            for todo in todos:
                todo_views.append({
                    "todo_id": todo.get("todo_id") or "",
                    "content": todo.get("content") or "",
                    "key_time": todo.get("key_time") or "",
                    "key_time_input": _time_input_value(todo.get("key_time")),
                    "has_task": todo.get("todo_id") in task_todos,
                })
                if todo.get("key_time"):
                    key_times.append({
                        "content": todo.get("content") or "",
                        "display": _time_display(todo.get("key_time")),
                    })

            # 排期风险预览：新任务与现有排期一起安排是否会超期 / 冲突（只读，不写库）
            schedule_risks = sched.preview_new_plan_risks(
                scope.scope_store(store, g.current_user), plan, _now())

        return render_template(
            "pages/plan_confirm.html",
            active_page="plan_confirm",
            page_name="提炼确认",
            settings=read_settings(),
            plan=plan,
            plan_status=plan_state(plan) if plan else "",
            todos=todo_views,
            key_times=key_times,
            task_total=task_total,
            pending_new=pending_new,
            schedule_risks=schedule_risks,
            basis=(plan.get("basis") if plan else None) or [],
        )

    @app.post("/plan/confirm")
    def plan_confirm_submit():
        payload = request.get_json(silent=True)
        is_json = payload is not None
        data = payload if is_json else request.form
        collected, errors = _collect_confirm_payload(data, is_json)

        def fail(messages):
            if is_json:
                return jsonify({"ok": False, "message": messages[0], "errors": messages}), 400
            for message in messages:
                flash(message, "error")
            return redirect(url_for("plan_confirm", plan_id=collected["plan_id"]))

        if errors:
            return fail(errors)

        now = _now()
        with store_lock:
            store = read_store()
            plan = next(
                (p for p in store.get("plans", [])
                 if p.get("plan_id") == collected["plan_id"] and scope.owns_plan(p, g.current_user)),
                None,
            )
            if plan is None:
                return fail(["计划不存在或已被删除，请重新录入。"])

            plan["goal"] = collected["goal"]
            plan["status"] = "已确认"
            plan["confirmed_at"] = llm.iso(now)

            existing_todos = [t for t in store.get("todos", []) if t.get("plan_id") == plan.get("plan_id")]
            submitted_ids = {t["todo_id"] for t in collected["todos"] if t["todo_id"]}
            store["todos"] = [
                t for t in store.get("todos", [])
                if not (t.get("plan_id") == plan.get("plan_id") and t.get("todo_id") not in submitted_ids)
            ]

            result_todos = []
            for item in collected["todos"]:
                current = next((t for t in existing_todos if t.get("todo_id") == item["todo_id"]), None) if item["todo_id"] else None
                if current is not None:
                    current["content"] = item["content"]
                    current["key_time"] = item["key_time"]
                    result_todos.append(current)
                else:
                    new_todo = {
                        "todo_id": _next_seq_id(store["todos"], "todo_id", "D"),
                        "plan_id": plan.get("plan_id"),
                        "content": item["content"],
                        "key_time": item["key_time"],
                    }
                    store["todos"].append(new_todo)
                    result_todos.append(new_todo)

            default_due = llm.plan_default_due(result_todos, now)
            created = []
            for todo in result_todos:
                has_task = any(
                    t.get("plan_id") == plan.get("plan_id") and t.get("todo_id") == todo.get("todo_id")
                    for t in store.get("tasks", [])
                )
                if has_task:
                    continue
                for blueprint in llm.decompose_todo(todo["content"], todo.get("key_time"), default_due, now):
                    task_id = _next_seq_id(store.setdefault("tasks", []), "task_id", "T")
                    task = {
                        "task_id": task_id,
                        "plan_id": plan.get("plan_id"),
                        "todo_id": todo.get("todo_id"),
                        "user_id": plan.get("user_id") or g.current_user["id"],
                        "parent_task_id": "",
                        "title": blueprint["title"],
                        "detail": blueprint["detail"],
                        "priority": blueprint["priority"],
                        "estimate_minutes": blueprint["estimate_minutes"],
                        "due_at": blueprint["due_at"],
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
                    created.append(task_id)
            write_store(store)

            # 确认后复检：新任务与现有排期一起安排的超期 / 冲突风险（只提示，不阻断）
            schedule_risks = sched.plan_pending_risks(scope.scope_store(store, g.current_user), plan, now)

        if is_json:
            return jsonify({
                "ok": True,
                "plan_id": plan.get("plan_id"),
                "tasks_created": len(created),
                "warnings": [risk["text"] for risk in schedule_risks[:6]],
                "redirect": url_for("schedule", plan_id=plan.get("plan_id")),
            })
        flash("已确认并生成排期（新建任务 %d 个）。" % len(created), "success")
        if schedule_risks:
            extra = "……" if len(schedule_risks) > 1 else ""
            flash("注意：%d 项新任务在现有排期下无法按截止时间完成，请到智能排期中调整（%s%s）。" % (
                len(schedule_risks), schedule_risks[0]["text"], extra), "info")
        return redirect(url_for("schedule", plan_id=plan.get("plan_id")))

    # ------------------------------------------------------------------
    # A4 一句话办事（F-801 / REQ-014）
    # ------------------------------------------------------------------
    @app.post("/plan/command")
    def plan_command():
        payload = request.get_json(silent=True)
        query = str((payload.get("query") if payload is not None else request.form.get("query")) or "")
        result = llm.handle_command(query, scope.scope_store(read_store(), g.current_user))
        # 普通用户不返回管理页跳转链接（驾驶舱 / 数据管理 / 系统设置仅管理员可用）
        link = result.get("link") or {}
        if not scope.is_admin(g.current_user) and auth.is_admin_path(link.get("path") or ""):
            result = dict(result, link=None)
        if payload is not None:
            return jsonify(result), (200 if result.get("ok", True) else 400)
        if not result.get("ok", True):
            flash(result.get("message") or "指令无效。", "error")
        else:
            flash("%s（%s）" % (result.get("title"), "、".join(result.get("lines") or [])), "success")
        return redirect(url_for("plan_input"))
