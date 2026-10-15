"""C 线 · 结构化复盘（F-305 结构化复盘 / F-306 改进项转化）。

对应需求：AC-23 AI 初稿只填不覆盖 · 未验收不得写复盘 · 转出的目标必须走完整发布流程
归属文件：backend/retro.py · templates/pages/retro.html|retro_detail.html · css|js/pages/retro.*

硬约束：
- 目标未完成验收（状态不在「已验收 / 已复盘」）时，不允许创建或保存复盘；
- AI 初稿只填空字段，人工已填内容一律保留（AC-23）；
- 改进项转出的目标固定为「草稿」状态，必须走完整发布流程才会进入执行；
- 四段结构与改进项转化都落库到 store["retros"]，可回溯。
"""

import json
from datetime import datetime, timedelta
from importlib import import_module

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for

c_metrics = import_module("backend.c_metrics")

SECTIONS = (
    ("review", "目标回顾", "当初设定这个目标想解决什么问题、完成什么结果"),
    ("compare", "结果对照", "目标值、实际结果与达成度，逐条对照"),
    ("reason", "差异原因", "为什么达成或未达成，区分主观与客观、可控与不可控"),
    ("improve", "改进项", "下一个周期具体要改什么，尽量可执行、可验收"),
)
SECTION_KEYS = tuple(key for key, _label, _hint in SECTIONS)
IMPROVE_KINDS = ("目标", "任务")


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def next_id(prefix, records, key):
    number = len(records) + 1
    existing = {str(item.get(key)) for item in records}
    while "%s-%03d" % (prefix, number) in existing:
        number += 1
    return "%s-%03d" % (prefix, number)


def me():
    return g.get("current_user")


def is_admin(user=None):
    user = user if user is not None else me()
    return bool(user) and user.get("role") == "管理员"


def can_view(user, objective):
    if is_admin(user):
        return True
    return bool(user) and objective.get("user_id") == user.get("id")


def find_objective(store, objective_id):
    for item in store.get("objectives", []):
        if item.get("objective_id") == objective_id:
            return item
    return None


def find_review(store, objective_id):
    for item in store.get("reviews", []):
        if item.get("objective_id") == objective_id:
            return item
    return None


def find_retro(store, objective_id):
    for item in store.get("retros", []):
        if item.get("objective_id") == objective_id:
            return item
    return None


def next_cycle(store, cycle_id):
    """取当前周期之后的下一个周期（按开始时间排序）。"""
    cycles = sorted(store.get("cycles", []), key=lambda item: item.get("start_at") or "")
    for index, cycle in enumerate(cycles):
        if cycle.get("cycle_id") == cycle_id and index + 1 < len(cycles):
            return cycles[index + 1]
    return None


def review_highlights(review):
    """从验收记录里抽出复盘需要的对照要点。"""
    if not review:
        return {"achievement": None, "grade": "", "final_score": None, "self_score": None,
                "weak": [], "strong": [], "diff_note": ""}
    items = review.get("items") or []
    weak = [item for item in items if (item.get("achievement") or 0) < 75]
    strong = [item for item in items if (item.get("achievement") or 0) >= 95]
    return {
        "achievement": review.get("achievement"),
        "grade": review.get("grade") or "",
        "final_score": review.get("final_score"),
        "self_score": review.get("self_score"),
        "diff_note": review.get("diff_note") or "",
        "weak": [{"kr_id": item.get("kr_id"), "title": item.get("title"),
                  "achievement": item.get("achievement"), "actual": item.get("actual_text"),
                  "target": item.get("target_text")} for item in weak],
        "strong": [{"kr_id": item.get("kr_id"), "title": item.get("title"),
                    "achievement": item.get("achievement")} for item in strong],
    }


def rule_draft(objective, review):
    """规则兜底的四段初稿：只依据验收数据生成，不编造。"""
    data = review_highlights(review)
    owner = objective.get("user_id")
    target_text = "、".join(
        "%s（目标 %s）" % (item.get("title"), item.get("target_text"))
        for item in (review or {}).get("items", [])
    ) or "以发布快照为准"
    weak_text = "；".join(
        "%s 达成度 %.1f，实际 %s" % (item["title"], item["achievement"], item["actual"])
        for item in data["weak"]
    ) or "无未达标项"
    回顾 = "本周期目标是「%s」，关键结果包括：%s。" % (objective.get("title"), target_text)
    对照 = "系统达成度 %.1f（%s），自评 %s，终评 %s。未达标项：%s。" % (
        data["achievement"] if data["achievement"] is not None else 0,
        data["grade"] or "未评级",
        data["self_score"] if data["self_score"] is not None else "—",
        data["final_score"] if data["final_score"] is not None else "—",
        weak_text,
    )
    原因 = "未达标项集中在上述关键结果，需进一步说明是目标定得偏高、投入不足，还是存在外部依赖未打通。"
    改进 = "把未达标的关键结果拆成下一周期的具体动作，优先解决依赖与资源问题，并在周期前两周设置检查点。"
    return {"source": "rule", "sections": {"回顾": 回顾, "对照": 对照, "原因": 原因, "改进": 改进},
            "generated_at": now_iso()}


def ai_draft(objective, review):
    """AI 复盘初稿：只填不覆盖（AC-23）；失败回退规则稿。"""
    fallback = rule_draft(objective, review)
    try:
        llm = import_module("backend.llm")
        if not llm.llm_configured():
            return fallback
        data = review_highlights(review)
        brief = {
            "目标": objective.get("title"),
            "周期": objective.get("cycle_id"),
            "系统达成度": data["achievement"],
            "档位": data["grade"],
            "自评分": data["self_score"],
            "终评分": data["final_score"],
            "验收差异说明": data["diff_note"],
            "关键结果": [
                {"内容": item.get("title"), "目标": item.get("target_text"),
                 "实际": item.get("actual_text"), "达成度": item.get("achievement")}
                for item in (review or {}).get("items", [])
            ],
        }
        result = llm._llm_chat_json(
            "你是绩效复盘教练。只依据给定的验收数据写复盘初稿，不得编造事实或数字。"
            "输出 JSON：{\"回顾\": \"...\", \"对照\": \"...\", \"原因\": \"...\", \"改进\": \"...\"}，"
            "每段 1-2 句中文，改进段要给出可执行动作。",
            "验收数据：\n" + json.dumps(brief, ensure_ascii=False),
            timeout=25,
        )
        if not isinstance(result, dict):
            return fallback
        sections = {}
        for key, label, _hint in SECTIONS:
            text = str(result.get(label) or "").strip()
            if text:
                sections[label] = text
        if len(sections) < 2:
            return fallback
        merged = dict(fallback["sections"])
        merged.update(sections)
        return {"source": "llm", "sections": merged, "generated_at": now_iso()}
    except Exception:
        return fallback


def blank_sections():
    return {label: "" for _key, label, _hint in SECTIONS}


def normalize_improvements(raw):
    rows = []
    for item in raw or []:
        if isinstance(item, str):
            text = item.strip()
            if text:
                rows.append({"text": text, "owner_id": "", "kind": "目标", "converted_to": ""})
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        rows.append({
            "text": text,
            "owner_id": item.get("owner_id") or "",
            "kind": item.get("kind") if item.get("kind") in IMPROVE_KINDS else "目标",
            "converted_to": item.get("converted_to") or "",
        })
    return rows


# ---------------------------------------------------------------------------
# 页面数据
# ---------------------------------------------------------------------------

def list_payload(store, users, user):
    users = {item["id"]: item for item in users}
    cycles = {item["cycle_id"]: item for item in store.get("cycles", [])}
    reviews = {item.get("objective_id"): item for item in store.get("reviews", [])}
    retros = {item.get("objective_id"): item for item in store.get("retros", [])}

    todo, done, blocked = [], [], []
    for objective in store.get("objectives", []):
        if not can_view(user, objective):
            continue
        if objective.get("status") == c_metrics.OBJ_DRAFT:
            continue
        review = reviews.get(objective.get("objective_id"))
        retro = retros.get(objective.get("objective_id"))
        row = {
            "objective_id": objective.get("objective_id"),
            "title": objective.get("title"),
            "level_label": objective.get("level_label") or "",
            "owner": (users.get(objective.get("user_id")) or {}).get("name") or "未指派",
            "cycle": (cycles.get(objective.get("cycle_id")) or {}).get("name") or "—",
            "status": objective.get("status"),
            "achievement": (review or {}).get("achievement"),
            "final_score": (review or {}).get("final_score"),
            "grade": (review or {}).get("grade") or "",
            "improve_count": len((retro or {}).get("improvements") or []),
            "converted": sum(1 for item in (retro or {}).get("improvements") or [] if item.get("converted_to")),
            "updated_at": (retro or {}).get("updated_at") or "",
        }
        if objective.get("status") in c_metrics.VERIFIED_STATUSES and not retro:
            todo.append(row)
        elif retro:
            done.append(row)
        else:
            blocked.append(row)

    done.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {
        "groups": {"todo": todo, "done": done, "blocked": blocked},
        "summary": {
            "todo_count": len(todo),
            "done_count": len(done),
            "blocked_count": len(blocked),
            "converted_count": sum(item["converted"] for item in done),
        },
        "section_defs": [{"key": key, "label": label, "hint": hint} for key, label, hint in SECTIONS],
        "improve_kinds": IMPROVE_KINDS,
    }


def detail_payload(store, users, objective, review, retro):
    users = {item["id"]: item for item in users}
    cycles = {item["cycle_id"]: item for item in store.get("cycles", [])}
    merged = {
        "objective": objective,
        "review": review,
        "retro": retro,
        "highlights": review_highlights(review),
        "rows": [
            {
                "kr_id": item.get("kr_id"),
                "title": item.get("title"),
                "metric_label": item.get("metric_label"),
                "target_text": item.get("target_text"),
                "actual_text": item.get("actual_text"),
                "achievement": item.get("achievement"),
                "weight": item.get("weight"),
            }
            for item in (review or {}).get("items", [])
        ],
        "sections": (retro or {}).get("sections") or blank_sections(),
        "section_defs": [{"key": key, "label": label, "hint": hint} for key, label, hint in SECTIONS],
        "improvements": (retro or {}).get("improvements") or [],
        "improve_kinds": IMPROVE_KINDS,
        "owner": users.get(objective.get("user_id")) or {},
        "cycle": cycles.get(objective.get("cycle_id")) or {},
        "next_cycle": next_cycle(store, objective.get("cycle_id")),
        "verified": objective.get("status") in c_metrics.VERIFIED_STATUSES,
        "can_edit": (is_admin() or objective.get("user_id") == (me() or {}).get("id"))
                    and objective.get("status") in c_metrics.VERIFIED_STATUSES,
        "users": users,
    }
    return merged


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

def register(app, services):
    read_store = services.read_store
    write_store = services.write_store
    read_users = services.read_users
    lock = services.store_lock

    @app.get("/retro")
    def retro_list():
        return render_template(
            "pages/retro.html",
            active_page="retro",
            page_name="结构化复盘",
            **list_payload(read_store(), read_users(), me())
        )

    @app.get("/retro/<objective_id>")
    def retro_detail(objective_id):
        store = read_store()
        objective = find_objective(store, objective_id)
        if not objective:
            abort(404)
        if not can_view(me(), objective):
            abort(403)
        review = find_review(store, objective_id)
        retro = find_retro(store, objective_id)
        return render_template(
            "pages/retro_detail.html",
            active_page="retro",
            page_name="复盘详情",
            **detail_payload(store, read_users(), objective, review, retro)
        )

    @app.post("/retro/<objective_id>/save")
    def retro_save(objective_id):
        with lock:
            store = read_store()
            objective = find_objective(store, objective_id)
            if not objective:
                abort(404)
            if not (is_admin() or objective.get("user_id") == (me() or {}).get("id")):
                abort(403)
            if objective.get("status") not in c_metrics.VERIFIED_STATUSES:
                flash("目标尚未完成验收，不能写复盘。", "error")
                return redirect(url_for("retro_detail", objective_id=objective_id))

            retro = find_retro(store, objective_id)
            sections = {label: (request.form.get("section_%s" % key) or "").strip()
                        for key, label, _hint in SECTIONS}
            improvements = []
            texts = request.form.getlist("improve_text")
            owners = request.form.getlist("improve_owner")
            kinds = request.form.getlist("improve_kind")
            previous = (retro or {}).get("improvements") or []
            for index, text in enumerate(texts):
                if not text.strip():
                    continue
                kept = previous[index] if index < len(previous) else {}
                improvements.append({
                    "text": text.strip(),
                    "owner_id": owners[index] if index < len(owners) else "",
                    "kind": kinds[index] if index < len(kinds) and kinds[index] in IMPROVE_KINDS else "目标",
                    "converted_to": kept.get("converted_to") or "",
                })

            if retro is None:
                retro = {
                    "retro_id": next_id("RT", store.get("retros", []), "retro_id"),
                    "objective_id": objective_id,
                    "review_id": (find_review(store, objective_id) or {}).get("review_id", ""),
                    "owner_id": objective.get("user_id"),
                    "ai_draft": {"source": "", "generated_at": ""},
                    "created_at": now_iso(),
                }
                store.setdefault("retros", []).append(retro)
            retro.update({
                "sections": sections,
                "improvements": improvements,
                "updated_at": now_iso(),
            })
            objective["status"] = c_metrics.OBJ_RETROED
            objective["updated_at"] = now_iso()
            write_store(store)

        flash("复盘已保存。", "ok")
        return redirect(url_for("retro_detail", objective_id=objective_id))

    @app.post("/retro/<objective_id>/draft")
    def retro_draft(objective_id):
        store = read_store()
        objective = find_objective(store, objective_id)
        if not objective:
            abort(404)
        if not can_view(me(), objective):
            abort(403)
        review = find_review(store, objective_id)
        result = ai_draft(objective, review)
        # 初稿只回传给前端填充空白框，是否采用由人工决定（AC-23：不覆盖人工文本）
        filled = [label for key, label, _hint in SECTIONS if result["sections"].get(label)]
        return jsonify({
            "source": result["source"],
            "filled": filled,
            "sections": result["sections"],
            "generated_at": result["generated_at"],
        })

    @app.post("/retro/<objective_id>/convert")
    def retro_convert(objective_id):
        index = int(request.form.get("index") or -1)
        with lock:
            store = read_store()
            objective = find_objective(store, objective_id)
            if not objective:
                abort(404)
            if not (is_admin() or objective.get("user_id") == (me() or {}).get("id")):
                abort(403)
            retro = find_retro(store, objective_id)
            if not retro:
                flash("请先保存复盘。", "error")
                return redirect(url_for("retro_detail", objective_id=objective_id))
            improvements = retro.get("improvements") or []
            if index < 0 or index >= len(improvements):
                flash("改进项不存在。", "error")
                return redirect(url_for("retro_detail", objective_id=objective_id))
            item = improvements[index]
            if item.get("converted_to"):
                flash("该改进项已转化，无需重复操作。", "error")
                return redirect(url_for("retro_detail", objective_id=objective_id))

            owner_id = item.get("owner_id") or objective.get("user_id")
            if item.get("kind") == "任务":
                item["converted_to"] = _create_task(store, objective, item, owner_id)
                flash("已转为自建任务，可在任务列表继续排期。", "ok")
            else:
                item["converted_to"] = _create_objective(store, objective, item, owner_id)
                flash("已转为下周期目标（草稿），需走发布流程才会生效。", "ok")
            retro["updated_at"] = now_iso()
            write_store(store)

        return redirect(url_for("retro_detail", objective_id=objective_id))

    @app.post("/retro/<objective_id>/delete-improve")
    def retro_delete_improve(objective_id):
        index = int(request.form.get("index") or -1)
        with lock:
            store = read_store()
            objective = find_objective(store, objective_id)
            if not objective:
                abort(404)
            if not (is_admin() or objective.get("user_id") == (me() or {}).get("id")):
                abort(403)
            retro = find_retro(store, objective_id)
            if retro:
                improvements = retro.get("improvements") or []
                if 0 <= index < len(improvements):
                    improvements.pop(index)
                retro["updated_at"] = now_iso()
                write_store(store)
        return redirect(url_for("retro_detail", objective_id=objective_id))


# ---------------------------------------------------------------------------
# 改进项转化
# ---------------------------------------------------------------------------

def _create_objective(store, source, item, owner_id):
    """改进项 → 下周期目标（草稿）；必须走发布流程才会进入执行。"""
    cycle = next_cycle(store, source.get("cycle_id"))
    objective_id = next_id("OBJ", store.get("objectives", []), "objective_id")
    snapshot_krs = (source.get("published_snapshot") or {}).get("key_results") or []
    key_results = []
    for index, kr in enumerate(snapshot_krs, start=1):
        key_results.append({
            "kr_id": "KR-%s%d" % (objective_id.split("-")[-1], index),
            "title": kr.get("title"),
            "metric_type": kr.get("metric_type") or c_metrics.METRIC_FORWARD,
            "weight": kr.get("weight") or 1,
            "target_value": kr.get("target_value"),
            "unit": kr.get("unit") or "",
            "current_value": None,
            "reviewed_value": None,
            "qualitative_level": "",
            "reviewed_level": "",
            "milestones": kr.get("milestones") or [],
        })
    store.setdefault("objectives", []).append({
        "objective_id": objective_id,
        "series_id": source.get("series_id") or objective_id,
        "parent_id": source.get("parent_id") or "",
        "level": source.get("level") or 3,
        "level_label": source.get("level_label") or "个人级",
        "title": item.get("text"),
        "desc": "由复盘改进项转化：%s" % source.get("objective_id"),
        "user_id": owner_id,
        "cycle_id": (cycle or {}).get("cycle_id") or source.get("cycle_id"),
        "weight": source.get("weight"),
        "status": c_metrics.OBJ_DRAFT,
        "key_results": key_results,
        "progress_updated": False,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "published_snapshot": {"published_at": "", "title": item.get("text"),
                               "weight": source.get("weight"), "key_results": []},
    })
    return objective_id


def _create_task(store, source, item, owner_id):
    """改进项 → 自建任务，挂在一份「复盘改进」计划下。"""
    plan = next((p for p in store.get("plans", []) if p.get("goal") == "复盘改进专项"), None)
    if plan is None:
        plan = {
            "plan_id": "P-RETRO",
            "user_id": owner_id,
            "raw_text": "由结构化复盘沉淀的改进项集合。",
            "goal": "复盘改进专项",
            "created_at": now_iso(),
        }
        store.setdefault("plans", []).append(plan)

    task_id = next_id("T", store.get("tasks", []), "task_id")
    due = (datetime.now() + timedelta(days=14)).replace(microsecond=0, hour=18, minute=0)
    store.setdefault("tasks", []).append({
        "task_id": task_id,
        "plan_id": plan["plan_id"],
        "todo_id": "",
        "user_id": owner_id,
        "parent_task_id": "",
        "title": item.get("text"),
        "detail": "由 %s 的复盘改进项转化。" % source.get("objective_id"),
        "priority": "中",
        "estimate_minutes": 60,
        "due_at": due.isoformat(),
        "scheduled_start_at": "",
        "planned_finish_at": "",
        "status": "待安排",
        "progress": 0,
        "depends_on": [],
        "delay_count": 0,
        "delay_reason": "",
        "completed_at": "",
        "is_demo": False,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    })
    return task_id
