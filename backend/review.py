"""C 线 · 完成与验收（F-301 完成提报 / F-302 达成度计算 / F-303 对照验收）。

对应需求：AC-20 对照发布快照 · AC-21 四类算法 · AC-22 AI 预审仅建议 · AC-26 差异说明
归属文件：backend/review.py · templates/pages/review.html|review_detail.html · css|js/pages/review.*

硬约束（来自软需，实现时不得绕过）：
- 验收永远对照 published_snapshot，不看当前值：目标值取发布快照，验收录入值取当期实测；
- AI 预审结论只作证据完整性与风险的提示，不参与 final_score 计算；
- 自评与终评差 > 20 分时强制填写差异说明，否则拒绝落库（AC-26）；
- 达成度一律调用 backend.c_metrics 计算，本模块不自行实现算法。
"""

import json
from datetime import datetime
from importlib import import_module

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for

c_metrics = import_module("backend.c_metrics")


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def next_id(prefix, records, key):
    number = len(records) + 1
    existing = {str(item.get(key)) for item in records}
    while "%s-%03d" % (prefix, number) in existing:
        number += 1
    return "%s-%03d" % (prefix, number)


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


def me():
    return g.get("current_user")


def is_admin(user=None):
    user = user if user is not None else me()
    return bool(user) and user.get("role") == "管理员"


def can_view(user, objective):
    if is_admin(user):
        return True
    return bool(user) and objective.get("user_id") == user.get("id")


def parse_evidence(raw):
    """把「名称 | 链接 | 备注」逐行文本解析成证据列表。"""
    rows = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [piece.strip() for piece in line.split("|")]
        rows.append({
            "name": parts[0] if parts else line,
            "url": parts[1] if len(parts) > 1 else "",
            "note": parts[2] if len(parts) > 2 else "",
        })
    return rows


def merge_with_snapshot(objective):
    """把发布快照的目标值与执行期的实际值合并，供口径引擎计算。

    目标值一律取快照（AC-20）；实际值取执行期字段。这样即使目标发布后被改动，
    验收仍按发布时的口径判定。
    """
    snapshot = {item.get("kr_id"): item for item in
                (objective.get("published_snapshot") or {}).get("key_results", [])}
    merged_krs = []
    for live in objective.get("key_results") or []:
        base = dict(snapshot.get(live.get("kr_id"), {}))
        base.setdefault("kr_id", live.get("kr_id"))
        for field in ("current_value", "reviewed_value", "reviewed_level",
                      "milestones", "qualitative_level"):
            base[field] = live.get(field)
        merged_krs.append(base)
    merged = dict(objective)
    merged["key_results"] = merged_krs
    return merged


def build_rows(objective, review=None):
    """对照发布快照逐条构造 KR 行：目标值 / 当前值 / 验收录入 / 达成度。"""
    snapshot = {item.get("kr_id"): item for item in
                (objective.get("published_snapshot") or {}).get("key_results", [])}
    live = {item.get("kr_id"): item for item in objective.get("key_results") or []}
    recorded = {}
    if review:
        recorded = {item.get("kr_id"): item for item in review.get("items") or []}

    rows = []
    for kr_id, base in snapshot.items():
        actual = live.get(kr_id, {})
        merged = dict(base)
        for field in ("current_value", "reviewed_value", "reviewed_level",
                      "milestones", "qualitative_level"):
            merged[field] = actual.get(field)
        metric = base.get("metric_type") or c_metrics.METRIC_FORWARD
        record = recorded.get(kr_id) or {}
        rows.append({
            "kr_id": kr_id,
            "title": base.get("title") or "",
            "metric_type": metric,
            "metric_label": c_metrics.METRIC_LABELS.get(metric, ""),
            "formula": c_metrics.METRIC_FORMULA.get(metric, ""),
            "weight": c_metrics.num(base.get("weight"), 1.0) or 1.0,
            "target_text": c_metrics.kr_target_text(base, metric),
            "current_text": c_metrics.kr_actual_text(merged, "draft", metric),
            "reviewed_text": record.get("actual_text") or "",
            "draft_achievement": c_metrics.kr_achievement(merged, "draft"),
            "final_achievement": c_metrics.kr_achievement(merged, "final"),
            "reviewed_value": merged.get("reviewed_value"),
            "reviewed_level": merged.get("reviewed_level") or "",
            "qualitative_level": merged.get("qualitative_level") or "",
            "milestones": [
                {"name": stone.get("name"), "done": bool(stone.get("done"))}
                for stone in (merged.get("milestones") or [])
            ],
            "statement": record.get("statement", ""),
            "evidence": record.get("evidence", []),
            "evidence_text": "\n".join(
                " | ".join(part for part in [item.get("name") or "", item.get("url") or "",
                                             item.get("note") or ""] if part)
                for item in (record.get("evidence") or [])
            ),
            # 已评审定级后锁定；仅提报未定级时仍可修改后重新提报
            "locked": bool(review and review.get("final_score") is not None),
        })
    return rows


def milestone_texts(kr):
    return [stone.get("name") for stone in (kr.get("milestones") or [])]


def rule_precheck(objective, rows):
    """规则兜底预审：只检查证据完备性与数值异常，不做主观判断。"""
    lines = []
    risks = []
    for row in rows:
        if not (row.get("statement") or "").strip():
            risks.append("%s《%s》缺少完成说明" % (row["kr_id"], row["title"][:18]))
        if not row.get("evidence"):
            risks.append("%s《%s》未附证据" % (row["kr_id"], row["title"][:18]))
        if row.get("final_achievement") is not None and row["final_achievement"] < 60:
            risks.append("%s《%s》达成度仅 %.1f，建议补充未达成原因"
                         % (row["kr_id"], row["title"][:18], row["final_achievement"]))
    achievement = c_metrics.objective_achievement(merge_with_snapshot(objective), "final")
    if achievement is not None:
        lines.append("系统按发布快照口径算出目标达成度 %.1f（%s）。"
                     % (achievement, c_metrics.grade_of(achievement)))
    lines.append("共 %d 条关键结果，其中 %d 条已附证据。"
                 % (len(rows), sum(1 for row in rows if row.get("evidence"))))
    advice = "先补齐缺失说明与证据，再提交评审。" if risks else "证据链完整，可提交评审。"
    return {"source": "rule", "lines": lines, "risks": risks, "advice": advice,
            "generated_at": now_iso()}


def ai_precheck(objective, rows):
    """AI 证据预审：结论仅供参考，不参与 final_score（AC-22）。"""
    fallback = rule_precheck(objective, rows)
    try:
        llm = import_module("backend.llm")
        if not llm.llm_configured():
            return fallback
        brief = {
            "目标": objective.get("title"),
            "关键结果": [
                {
                    "编号": row["kr_id"],
                    "内容": row["title"],
                    "口径": row["metric_label"],
                    "目标": row["target_text"],
                    "实际": row["current_text"],
                    "系统达成度": row["final_achievement"],
                    "完成说明": (row.get("statement") or "")[:120],
                    "证据条数": len(row.get("evidence") or []),
                }
                for row in rows
            ],
            "规则预审风险": fallback["risks"],
        }
        result = llm._llm_chat_json(
            "你是验收评审助理。只依据给定的关键结果、说明与证据条数评论，不得编造材料。"
            "输出 JSON：{\"lines\": [2 条中文结论], \"risks\": [最多 3 条证据或数据风险], "
            "\"advice\": \"一句给评审人的提示\"}。",
            "验收材料：\n" + json.dumps(brief, ensure_ascii=False),
            timeout=20,
        )
        if not isinstance(result, dict) or not result.get("lines"):
            return fallback
        return {
            "source": "llm",
            "lines": [str(item) for item in result["lines"]][:3],
            "risks": [str(item) for item in (result.get("risks") or [])][:4],
            "advice": result.get("advice") or fallback["advice"],
            "generated_at": now_iso(),
        }
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# 页面数据
# ---------------------------------------------------------------------------

def list_payload(store, users, user, tab="pending"):
    users = {item["id"]: item for item in users}
    cycles = {item["cycle_id"]: item for item in store.get("cycles", [])}
    reviews = {item.get("objective_id"): item for item in store.get("reviews", [])}
    retros = {item.get("objective_id"): item for item in store.get("retros", [])}
    merged_all = []

    groups = {"pending": [], "verified": [], "running": []}
    for objective in store.get("objectives", []):
        if not can_view(user, objective):
            continue
        status = objective.get("status")
        if status == c_metrics.OBJ_DRAFT:
            continue
        merged = merge_with_snapshot(objective)
        merged_all.append(merged)
        review = reviews.get(objective.get("objective_id"))
        score = review.get("achievement") if review else c_metrics.objective_achievement(merged, "final")
        row = {
            "objective_id": objective.get("objective_id"),
            "title": objective.get("title"),
            "level_label": objective.get("level_label") or "",
            "owner": (users.get(objective.get("user_id")) or {}).get("name") or "未指派",
            "owner_id": objective.get("user_id"),
            "cycle": (cycles.get(objective.get("cycle_id")) or {}).get("name") or "—",
            "cycle_id": objective.get("cycle_id"),
            "weight": objective.get("weight"),
            "status": status,
            "score": score,
            "grade": c_metrics.grade_of(score),
            "has_review": bool(review),
            "self_score": (review or {}).get("self_score"),
            "final_score": (review or {}).get("final_score"),
            "has_retro": objective.get("objective_id") in retros,
            "kr_total": len(objective.get("key_results") or []),
        }
        if status == c_metrics.OBJ_PENDING:
            groups["pending"].append(row)
        elif status in c_metrics.VERIFIED_STATUSES:
            groups["verified"].append(row)
        else:
            groups["running"].append(row)

    for key in groups:
        groups[key].sort(key=lambda item: -(item.get("score") or 0))

    verified_objs = [item for item in merged_all if item.get("status") in c_metrics.VERIFIED_STATUSES]
    summary = {
        "pending_count": len(groups["pending"]),
        "running_count": len(groups["running"]),
        "verified_count": len(groups["verified"]),
        "avg_final": c_metrics.weighted_objective_average(verified_objs),
        "verified_objs": len(verified_objs),
        "need_retro": sum(1 for item in groups["verified"] if not item.get("has_retro")),
    }
    return {
        "tab": tab if tab in groups else "pending",
        "groups": groups,
        "summary": summary,
        "diff_limit": c_metrics.SELF_FINAL_DIFF_LIMIT,
        "cycle_list": sorted(store.get("cycles", []),
                             key=lambda item: item.get("start_at") or "", reverse=True),
        "status_order": c_metrics.OBJECTIVE_STATUSES,
    }


def detail_payload(store, users, objective, review=None):
    users = {item["id"]: item for item in users}
    cycles = {item["cycle_id"]: item for item in store.get("cycles", [])}
    retros = {item.get("objective_id"): item for item in store.get("retros", [])}
    rows = build_rows(objective, review)
    merged = merge_with_snapshot(objective)
    preview = c_metrics.objective_achievement(merged, "final")
    achievement = review.get("achievement") if review else preview

    parent = find_objective(store, objective.get("parent_id")) if objective.get("parent_id") else None
    diff = c_metrics.self_final_diff((review or {}).get("self_score"), (review or {}).get("final_score"))
    return {
        "objective": objective,
        "snapshot": objective.get("published_snapshot") or {},
        "rows": rows,
        "review": review,
        "retro": retros.get(objective.get("objective_id")),
        "achievement": achievement,
        "preview": preview,
        "grade": c_metrics.grade_of(achievement),
        "self_final_diff": diff,
        "needs_diff": c_metrics.needs_diff_note((review or {}).get("self_score"),
                                                (review or {}).get("final_score")),
        "diff_limit": c_metrics.SELF_FINAL_DIFF_LIMIT,
        "owner": (users.get(objective.get("user_id")) or {}),
        "parent": parent,
        "cycle": cycles.get(objective.get("cycle_id")) or {},
        "users": users,
        "metric_formulas": c_metrics.METRIC_FORMULA,
        "qualitative_levels": c_metrics.QUALITATIVE_LEVELS,
        "grade_bands": c_metrics.grade_bands_text(),
        "can_submit": (is_admin() or objective.get("user_id") == (me() or {}).get("id"))
                      and objective.get("status") in (c_metrics.OBJ_RUNNING, c_metrics.OBJ_PENDING)
                      and not (review and review.get("final_score") is not None),
        "can_score": bool(review) and review.get("final_score") is None and is_admin(),
        "status_order": c_metrics.OBJECTIVE_STATUSES,
    }


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

def register(app, services):
    read_store = services.read_store
    write_store = services.write_store
    read_users = services.read_users
    lock = services.store_lock

    @app.get("/review")
    def review_list():
        tab = request.args.get("tab", "pending")
        return render_template(
            "pages/review.html",
            active_page="review",
            page_name="完成与验收",
            **list_payload(read_store(), read_users(), me(), tab)
        )

    @app.get("/review/<objective_id>")
    def review_detail(objective_id):
        store = read_store()
        objective = find_objective(store, objective_id)
        if not objective:
            abort(404)
        if not can_view(me(), objective):
            abort(403)
        review = find_review(store, objective_id)
        return render_template(
            "pages/review_detail.html",
            active_page="review",
            page_name="验收详情",
            **detail_payload(store, read_users(), objective, review)
        )

    @app.post("/review/<objective_id>/submit")
    def review_submit(objective_id):
        with lock:
            store = read_store()
            objective = find_objective(store, objective_id)
            if not objective:
                abort(404)
            if not (can_view(me(), objective) or is_admin()):
                abort(403)
            review = find_review(store, objective_id)
            if review and review.get("final_score") is not None:
                flash("该目标已完成验收定级，不能重复提报。", "error")
                return redirect(url_for("review_detail", objective_id=objective_id))

            items = []
            for row in build_rows(objective, None):
                kr_id = row["kr_id"]
                reviewed_raw = (request.form.get("reviewed_%s" % kr_id) or "").strip()
                level = (request.form.get("level_%s" % kr_id) or "").strip()
                live_kr = _live_kr(objective, kr_id)
                merged = dict(_snapshot_kr(objective, kr_id))
                merged.update({
                    "current_value": (live_kr or {}).get("current_value"),
                    "reviewed_value": c_metrics.num(reviewed_raw, None) if reviewed_raw else None,
                    "reviewed_level": level,
                    "milestones": (live_kr or {}).get("milestones"),
                    "qualitative_level": (live_kr or {}).get("qualitative_level"),
                })
                if row["metric_type"] == c_metrics.METRIC_QUALITATIVE:
                    merged["reviewed_level"] = level or merged.get("qualitative_level") or ""
                items.append({
                    "kr_id": kr_id,
                    "title": row["title"],
                    "metric_type": row["metric_type"],
                    "metric_label": row["metric_label"],
                    "target_value": merged.get("target_value"),
                    "unit": merged.get("unit") or "",
                    "weight": row["weight"],
                    "target_text": row["target_text"],
                    "reviewed_value": merged.get("reviewed_value"),
                    "reviewed_level": merged.get("reviewed_level") or "",
                    "actual_text": c_metrics.kr_actual_text(merged, "final", row["metric_type"]),
                    "statement": (request.form.get("statement_%s" % kr_id) or "").strip(),
                    "evidence": parse_evidence(request.form.get("evidence_%s" % kr_id)),
                    "achievement": c_metrics.kr_achievement(merged, "final"),
                })

            # 验收录入值写回目标，供口径引擎与其它页面统一取数
            for kr in objective.get("key_results") or []:
                record = next((item for item in items if item["kr_id"] == kr.get("kr_id")), None)
                if not record:
                    continue
                if record["metric_type"] == c_metrics.METRIC_QUALITATIVE:
                    kr["reviewed_level"] = record["reviewed_level"]
                else:
                    kr["reviewed_value"] = record["reviewed_value"]

            achievement = c_metrics.objective_achievement(merge_with_snapshot(objective), "final")
            self_score = c_metrics.num(request.form.get("self_score"), None)
            if self_score is None:
                flash("请填写自评分。", "error")
                return redirect(url_for("review_detail", objective_id=objective_id))

            if review is None:
                review = {
                    "review_id": next_id("RV", store.get("reviews", []), "review_id"),
                    "objective_id": objective_id,
                    "cycle_id": objective.get("cycle_id"),
                    "submitter_id": (me() or {}).get("id"),
                    "snapshot_at": (objective.get("published_snapshot") or {}).get("published_at"),
                    "ai_precheck": {"source": "", "lines": [], "generated_at": ""},
                    "created_at": now_iso(),
                    "reviewed_by": "",
                    "reviewed_at": "",
                    "final_score": None,
                    "diff_note": "",
                }
                store.setdefault("reviews", []).append(review)
            review.update({
                "items": items,
                "achievement": achievement,
                "self_score": self_score,
                "grade": c_metrics.grade_of(achievement),
                "status": "待验收",
            })
            objective["status"] = c_metrics.OBJ_PENDING
            objective["updated_at"] = now_iso()
            write_store(store)

        flash("已提交验收提报，等待评审定级。", "ok")
        return redirect(url_for("review_detail", objective_id=objective_id))

    @app.post("/review/<objective_id>/score")
    def review_score(objective_id):
        if not is_admin():
            abort(403)
        with lock:
            store = read_store()
            objective = find_objective(store, objective_id)
            if not objective:
                abort(404)
            review = find_review(store, objective_id)
            if not review:
                flash("请先由负责人提交提报，再评审定级。", "error")
                return redirect(url_for("review_detail", objective_id=objective_id))

            final_score = c_metrics.num(request.form.get("final_score"), None)
            if final_score is None:
                flash("请填写终评分数。", "error")
                return redirect(url_for("review_detail", objective_id=objective_id))
            diff_note = (request.form.get("diff_note") or "").strip()
            if c_metrics.needs_diff_note(review.get("self_score"), final_score) and not diff_note:
                flash("自评与终评相差超过 %.0f 分，必须填写差异说明。"
                      % c_metrics.SELF_FINAL_DIFF_LIMIT, "error")
                return redirect(url_for("review_detail", objective_id=objective_id))

            review.update({
                "final_score": final_score,
                "grade": c_metrics.grade_of(final_score),
                "diff_note": diff_note,
                "status": "已验收",
                "reviewed_by": (me() or {}).get("id"),
                "reviewed_at": now_iso(),
            })
            objective["status"] = c_metrics.OBJ_VERIFIED
            objective["updated_at"] = now_iso()
            write_store(store)

        flash("验收定级完成，可进入复盘。", "ok")
        return redirect(url_for("review_detail", objective_id=objective_id))

    @app.post("/review/<objective_id>/precheck")
    def review_precheck(objective_id):
        store = read_store()
        objective = find_objective(store, objective_id)
        if not objective:
            abort(404)
        if not can_view(me(), objective):
            abort(403)
        rows = build_rows(objective, find_review(store, objective_id))
        return jsonify(ai_precheck(objective, rows))


def _snapshot_kr(objective, kr_id):
    for item in (objective.get("published_snapshot") or {}).get("key_results", []):
        if item.get("kr_id") == kr_id:
            return item
    return {}


def _live_kr(objective, kr_id):
    for item in objective.get("key_results") or []:
        if item.get("kr_id") == kr_id:
            return item
    return {}
