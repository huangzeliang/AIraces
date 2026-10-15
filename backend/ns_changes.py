"""B3 变更管理（F-211~215）—— /ns/changes 页面 + 申请 / 审批 / 留痕 / 分析接口。

规则要点：
- 原因分类（reason_code）与影响说明必填，原因说明 ≤300 字符；
- 变更类型一次一语义：content / due_date / owner / weight（兼容种子数据的中文类型）；
- 通过后写新版本（objective.version 自增，原基线快照留档不覆盖）并通知申请人；
- 员工不可自审（申请人与审批人相同 → 1008）；冻结窗口（period.is_frozen + 提前 N 天）内 1005；
- 变更对象在关键路径上时，审批横幅带出「整体工期预计延期 N 天」（联动 B4）。
"""

from flask import request

from backend.core import (
    CHANGE_STATUS,
    DATA_LOCK,
    OBJECTIVE_STATUS,
    fail,
    f1,
    iso,
    next_id,
    now,
    parse_dt,
    paginate,
    page_args,
    read_store,
    setting_value,
    write_store,
)
from backend.ns_common import (
    api_perm,
    assignee_ids,
    audit,
    create_reminder,
    direct_manager,
    in_visible,
    is_manager,
    ns_api,
    ns_render,
    ns_user,
    obj_by_id,
    kr_by_id,
    payload,
    require_ns_menu,
    role_scope,
    scope_objectives_check,
    user_name,
    visible_user_ids,
)

CHANGE_TYPES = {
    "content": "内容调整",
    "due_date": "延期",
    "owner": "责任人调整",
    "weight": "权重调整",
}
TYPE_ALIASES = {
    "内容调整": "content", "范围调整": "content", "指标调整": "content", "内容": "content",
    "延期": "due_date", "时限调整": "due_date", "时间": "due_date",
    "责任人调整": "owner", "责任人": "owner",
    "权重调整": "weight", "权重": "weight",
}
STATUS_LABEL = dict(CHANGE_STATUS, cancelled="已作废")
TYPE_FIELDS = {
    "content": ("title", "success_criteria", "krs"),
    "due_date": ("plan_start", "plan_end"),
    "owner": ("owner_id",),
    "weight": ("weight",),
}
FIELD_LABELS = {
    "title": "目标内容", "success_criteria": "成功标准", "plan_start": "开始日期",
    "plan_end": "结束日期", "owner_id": "责任人", "weight": "目标权重",
}
CHANGEABLE_STATUS = ("published", "executing", "pending_review")


def normalize_type(raw):
    value = (raw or "").strip()
    if value in CHANGE_TYPES:
        return value
    if value in TYPE_ALIASES:
        return TYPE_ALIASES[value]
    fail(1001, "change_type 仅支持 content / due_date / owner / weight（一次只允许一个）。")


def type_label(code):
    return CHANGE_TYPES.get(code, code)


def freeze_state(store, obj, moment=None):
    moment = moment or now()
    freeze_days = int(setting_value(store, "change_freeze_days", 3))
    period = next((p for p in store["period"] if p.get("period_id") == obj.get("period_id")), None)
    base = {"frozen": False, "reason": "", "days_left": None, "freeze_days": freeze_days,
            "period_name": period["period_name"] if period else ""}
    if not period:
        return base
    if period.get("is_frozen"):
        base.update({"frozen": True, "reason": "周期「%s」已冻结，不接受变更申请。" % period["period_name"]})
        return base
    end = parse_dt(period.get("end_date"))
    if end:
        days_left = (end.date() - moment.date()).days
        base["days_left"] = days_left
        if days_left <= freeze_days:
            base.update({
                "frozen": True,
                "reason": "周期结束前 %d 天进入冻结窗口（剩余 %d 天），除特批外不接受变更申请。" % (
                    freeze_days, max(0, days_left)),
            })
    return base


def _snapshot_value(store, obj, key):
    if key == "krs":
        return [
            {"kr_id": kr["kr_id"], "target_value": kr.get("target_value")}
            for kr in store["key_result"] if kr.get("objective_id") == obj["objective_id"]
        ]
    if key == "success_criteria":
        return list(obj.get("success_criteria") or [])
    return obj.get(key)


def build_before(store, obj, change_type):
    return {key: _snapshot_value(store, obj, key) for key in TYPE_FIELDS[change_type]}


def _validate_after(store, obj, change_type, after):
    allowed = TYPE_FIELDS[change_type]
    if not isinstance(after, dict) or not after:
        fail(1001, "after_snapshot 不能为空。")
    extra = [key for key in after if key not in allowed]
    if extra:
        fail(1001, "变更类型「%s」不支持的字段：%s。" % (type_label(change_type), "、".join(extra)))
    normalized = {}
    if "title" in after:
        title = str(after["title"]).strip()
        if not title or len(title) > 60:
            fail(1001, "目标内容需为 1~60 字。")
        normalized["title"] = title
    if "success_criteria" in after:
        rows = [str(row).strip() for row in (after["success_criteria"] or []) if str(row).strip()]
        if not rows:
            fail(1001, "成功标准至少保留 1 条。")
        normalized["success_criteria"] = rows
    if "plan_start" in after or "plan_end" in after:
        start = after.get("plan_start", obj.get("plan_start"))
        end = after.get("plan_end", obj.get("plan_end"))
        start_dt, end_dt = parse_dt(start), parse_dt(end)
        if not start_dt or not end_dt:
            fail(1001, "计划日期格式不正确。")
        if end_dt < start_dt:
            fail(1001, "计划结束日期不能早于开始日期。")
        if "plan_start" in after:
            normalized["plan_start"] = start
        if "plan_end" in after:
            normalized["plan_end"] = end
    if "owner_id" in after:
        owner = next((u for u in store["user"] if u["user_id"] == after["owner_id"]), None)
        if not owner:
            fail(1003, "责任人不存在。")
        normalized["owner_id"] = owner["user_id"]
    if "weight" in after:
        try:
            weight = float(after["weight"])
        except (TypeError, ValueError):
            fail(1001, "权重需为数字。")
        if weight <= 0 or weight > 100:
            fail(1001, "权重需在 0~100 之间。")
        normalized["weight"] = weight
    if "krs" in after:
        rows = []
        for item in after["krs"] or []:
            kr = kr_by_id(store, (item or {}).get("kr_id"))
            if not kr or kr.get("objective_id") != obj["objective_id"]:
                fail(1003, "指标 %s 不属于该目标。" % (item or {}).get("kr_id"))
            rows.append({"kr_id": kr["kr_id"], "target_value": item.get("target_value")})
        if not rows:
            fail(1001, "KR 目标值不能为空。")
        normalized["krs"] = rows
    if not normalized:
        fail(1001, "after_snapshot 未提供有效变更内容。")
    return normalized


def apply_after(store, obj, after):
    if "title" in after:
        obj["title"] = after["title"]
    if "success_criteria" in after:
        obj["success_criteria"] = after["success_criteria"]
    if "plan_start" in after:
        obj["plan_start"] = after["plan_start"]
    if "plan_end" in after:
        obj["plan_end"] = after["plan_end"]
    if "owner_id" in after:
        obj["owner_id"] = after["owner_id"]
    if "weight" in after:
        obj["weight"] = after["weight"]
    if "krs" in after:
        for item in after["krs"]:
            kr = kr_by_id(store, item["kr_id"])
            if kr:
                kr["target_value"] = item["target_value"]
                kr["updated_at"] = iso()
    obj["version"] = int(obj.get("version") or 1) + 1
    obj["updated_at"] = iso()


def _display(store, key, value, kr_id=""):
    if key == "owner_id":
        return user_name(store, value) if value else "—"
    if key == "success_criteria":
        return "；".join(value or []) or "—"
    if key == "weight":
        return "%s%%" % f1(value)
    if key == "krs":
        return "目标值 %s" % value
    return value if value not in (None, "") else "—"


def diff_rows(store, change):
    before, after = change.get("before_snapshot") or {}, change.get("after_snapshot") or {}
    rows = []
    for key, new_value in after.items():
        if key == "krs":
            old_map = {item["kr_id"]: item.get("target_value") for item in before.get("krs") or []}
            for item in new_value:
                kr = kr_by_id(store, item["kr_id"])
                rows.append({
                    "field": "krs.%s" % item["kr_id"],
                    "label": "%s %s" % (item["kr_id"], (kr or {}).get("kr_name", "")),
                    "before": _display(store, "krs", old_map.get(item["kr_id"])),
                    "after": _display(store, "krs", item.get("target_value")),
                })
            continue
        rows.append({
            "field": key,
            "label": FIELD_LABELS.get(key, key),
            "before": _display(store, key, before.get(key)),
            "after": _display(store, key, new_value),
        })
    return rows


def change_row(store, change, with_diff=False):
    obj = obj_by_id(store, change.get("objective_id"))
    try:
        type_text = type_label(normalize_type(change.get("change_type") or "content"))
    except Exception:  # noqa: BLE001 — 历史数据兜底
        type_text = change.get("change_type") or "内容调整"
    row = {
        "change_id": change["change_id"],
        "objective_id": change.get("objective_id", ""),
        "objective_title": obj["title"] if obj else change.get("objective_id", ""),
        "change_type": change.get("change_type", ""),
        "change_type_label": type_text,
        "reason_code": change.get("reason_code", ""),
        "reason_text": change.get("reason_text", ""),
        "impact_note": change.get("impact_note", ""),
        "status": change.get("status", ""),
        "status_label": STATUS_LABEL.get(change.get("status"), change.get("status", "")),
        "applicant_id": change.get("applicant_id", ""),
        "applicant_name": user_name(store, change.get("applicant_id")),
        "approver_id": change.get("approver_id", ""),
        "approver_name": user_name(store, change.get("approver_id")),
        "approve_note": change.get("approve_note", ""),
        "applied_at": change.get("applied_at", ""),
        "approved_at": change.get("approved_at", ""),
        "version_after": change.get("version_after"),
        "before_snapshot": change.get("before_snapshot") or {},
        "after_snapshot": change.get("after_snapshot") or {},
        "on_critical_path": bool(change.get("on_critical_path")),
        "predicted_delay_days": int(change.get("predicted_delay_days") or 0),
    }
    if with_diff:
        row["diff"] = diff_rows(store, change)
    return row


def _visible_changes(store, user, objective_id="", status="", mine=False, applicant_id="", approver_id=""):
    ids = visible_user_ids(store, user)
    scope_objs = {
        o["objective_id"] for o in store["objective"]
        if ids is None or o.get("owner_id") in ids
        or any(t.get("objective_id") == o["objective_id"] and any(uid in ids for uid in assignee_ids(t))
               for t in store["work_task"])
    }
    rows = []
    for change in store["change_request"]:
        if objective_id and change.get("objective_id") != objective_id:
            continue
        if status and change.get("status") != status:
            continue
        if mine and change.get("applicant_id") != user["user_id"]:
            continue
        if applicant_id and change.get("applicant_id") != applicant_id:
            continue
        if approver_id and change.get("approver_id") != approver_id:
            continue
        visible = (
            change.get("objective_id") in scope_objs
            or change.get("applicant_id") == user["user_id"]
            or change.get("approver_id") == user["user_id"]
            or (is_manager(store, user) and in_visible(store, user, change.get("applicant_id")))
        )
        if visible:
            rows.append(change)
    return sorted(rows, key=lambda c: (c.get("applied_at") or "", c["change_id"]), reverse=True)


def _critical_context(store, obj, delay_days=0):
    from backend import ns_deps

    snapshot = ns_deps.critical_snapshot(store)
    task_ids = [
        t["task_id"] for t in store["work_task"]
        if t.get("objective_id") == obj["objective_id"] and t.get("status") != "cancelled"
    ]
    on_critical = [tid for tid in task_ids if tid in snapshot["critical_ids"]]
    if not on_critical or delay_days <= 0:
        return {"on_critical_path": bool(on_critical), "predicted_delay_days": 0}
    delays = []
    for tid in on_critical:
        result = ns_deps.impact_for(store, tid, delay_days)
        delays.append(int(result.get("project_delay_days") or 0))
    return {"on_critical_path": True, "predicted_delay_days": max(delays) if delays else 0}


def stats_payload(store, user, objective_id=""):
    threshold = int(setting_value(store, "change_frequent_threshold", 3))
    rows = _visible_changes(store, user, objective_id)
    by_status = {"pending": 0, "approved": 0, "rejected": 0, "cancelled": 0}
    by_type, by_reason, by_month = {}, {}, {}
    for change in rows:
        by_status[change.get("status")] = by_status.get(change.get("status"), 0) + 1
        code = change.get("change_type") or "content"
        try:
            code = normalize_type(code)
        except Exception:  # noqa: BLE001 — 历史数据兜底
            code = "content"
        by_type.setdefault(code, 0)
        by_type[code] += 1
        reason = change.get("reason_code") or "未分类"
        by_reason.setdefault(reason, 0)
        by_reason[reason] += 1
        month = (change.get("applied_at") or "")[:7]
        if month:
            by_month.setdefault(month, 0)
            by_month[month] += 1
    total = len(rows)
    frequent_map = {}
    for change in rows:
        oid = change.get("objective_id") or ""
        if not oid:
            continue
        entry = frequent_map.setdefault(oid, {"objective_id": oid, "count": 0, "last_at": ""})
        entry["count"] += 1
        entry["last_at"] = max(entry["last_at"], change.get("applied_at") or "")
    frequent = []
    for entry in frequent_map.values():
        if entry["count"] < threshold:
            continue
        obj = obj_by_id(store, entry["objective_id"])
        frequent.append({
            **entry,
            "objective_title": obj["title"] if obj else entry["objective_id"],
            "advice": "该目标变更 %d 次（阈值 %d），建议复盘计划合理性与需求稳定性。" % (entry["count"], threshold),
        })
    return {
        "total": total,
        "threshold": threshold,
        "by_status": [{"code": code, "label": STATUS_LABEL.get(code, code), "count": count}
                      for code, count in by_status.items() if count],
        "by_type": [{"code": code, "label": type_label(code), "count": count}
                    for code, count in sorted(by_type.items(), key=lambda kv: -kv[1])],
        "by_reason": [
            {"code": code, "name": code, "count": count, "share": round(count / total * 100) if total else 0}
            for code, count in sorted(by_reason.items(), key=lambda kv: -kv[1])
        ],
        "by_month": [{"month": month, "count": count} for month, count in sorted(by_month.items())],
        "frequent": sorted(frequent, key=lambda row: -row["count"]),
    }


def _versions(store, obj):
    published = obj.get("published_snapshot") or {}
    rows = [{
        "version": 1,
        "label": "发布基线",
        "at": published.get("published_at", ""),
        "summary": "成功标准与指标基线快照",
        "snapshot": published,
    }]
    approved = [
        c for c in store["change_request"]
        if c.get("objective_id") == obj["objective_id"] and c.get("status") == "approved"
    ]
    for change in sorted(approved, key=lambda c: c.get("approved_at") or ""):
        rows.append({
            "version": change.get("version_after") or len(rows) + 1,
            "label": "变更：%s" % type_label(normalize_type(change.get("change_type") or "content")),
            "at": change.get("approved_at", ""),
            "summary": change.get("reason_text", ""),
            "snapshot": change.get("after_snapshot") or {},
            "change_id": change["change_id"],
        })
    current = int(obj.get("version") or 1)
    if current != rows[-1]["version"]:
        rows.append({
            "version": current, "label": "当前版本", "at": obj.get("updated_at", ""),
            "summary": "当前生效基线", "snapshot": build_before(store, obj, "content"),
        })
    return rows


def register(app, services):
    @app.get("/ns/changes")
    @require_ns_menu("changes")
    def ns_changes():
        store = read_store()
        user = ns_user(store)
        objective_id = (request.args.get("objective_id") or "").strip()
        tab = (request.args.get("tab") or "").strip()
        pending = _visible_changes(store, user, objective_id, status="pending")
        mine = _visible_changes(store, user, mine=True)
        all_rows = _visible_changes(store, user, objective_id)[:50]
        objectives = [
            {"objective_id": o["objective_id"], "title": o["title"], "status": o["status"],
             "status_label": OBJECTIVE_STATUS.get(o.get("status"), o.get("status")),
             "version": int(o.get("version") or 1),
             "plan_start": o.get("plan_start", ""), "plan_end": o.get("plan_end", ""),
             "weight": o.get("weight"),
             "criteria": list(o.get("success_criteria") or []),
             "krs": [
                 {"kr_id": kr["kr_id"], "kr_name": kr.get("kr_name", ""),
                  "target_value": kr.get("target_value"), "unit": kr.get("unit", "")}
                 for kr in store["key_result"] if kr.get("objective_id") == o["objective_id"]
             ],
             "freeze": freeze_state(store, o),
             "changeable": o.get("status") in CHANGEABLE_STATUS,
             "owner_name": user_name(store, o.get("owner_id"))}
            for o in store["objective"]
            if not o.get("is_archived")
            and (visible_user_ids(store, user) is None
                 or o.get("owner_id") in visible_user_ids(store, user)
                 or any(t.get("objective_id") == o["objective_id"]
                        and any(uid in visible_user_ids(store, user) for uid in assignee_ids(t))
                        for t in store["work_task"]))
        ]
        return ns_render(
            "ns/changes.html", "changes", "变更管理",
            "原因结构化 → 审批 → 新版本留档 → 冻结窗口与频繁变更预警",
            store=store, user=user,
            tab=tab,
            objective_id=objective_id,
            objectives=objectives,
            pending_rows=[change_row(store, c, with_diff=True) for c in pending],
            mine_rows=[change_row(store, c) for c in mine],
            all_rows=[change_row(store, c) for c in all_rows],
            stats=stats_payload(store, user, objective_id),
            change_types=[{"code": code, "label": label} for code, label in CHANGE_TYPES.items()],
            reason_items=[d for d in store["dict_item"]
                          if d.get("dict_type") == "reason" and d.get("status") != "停用"],
            can_approve=is_manager(store, user),
            is_admin=role_scope(store, user) == "ALL",
            owner_candidates=[
                {"user_id": u["user_id"], "name": u["display_name"]}
                for u in store["user"]
                if u.get("status") == "启用"
                and (visible_user_ids(store, user) is None or u["user_id"] in visible_user_ids(store, user))
            ],
        )

    @app.post("/api/ns/change/apply")
    @ns_api
    def ns_api_change_apply():
        body = payload()
        with DATA_LOCK:
            store = read_store()
            user = ns_user(store)
            api_perm(store, user, "action:change.apply")
            objective_id = (body.get("objective_id") or "").strip()
            if not objective_id:
                fail(1001, "缺少 objective_id。")
            obj = scope_objectives_check(store, user, objective_id)
            if obj.get("status") not in CHANGEABLE_STATUS:
                fail(1004, "目标未发布或已进入终态（%s），当前不可发起变更。" % OBJECTIVE_STATUS.get(
                    obj.get("status"), obj.get("status")))
            change_type = normalize_type(body.get("change_type"))
            reason_code = (body.get("reason_code") or "").strip()
            if not reason_code:
                fail(1001, "原因分类必填。")
            reason_text = (body.get("reason_text") or "").strip()
            if not reason_text:
                fail(1001, "原因说明必填。")
            if len(reason_text) > 300:
                fail(1002, "原因说明不能超过 300 字符。")
            impact_note = (body.get("impact_note") or "").strip()
            if not impact_note:
                fail(1001, "影响说明必填（是否影响关键路径 / 上下游）。")
            freeze = freeze_state(store, obj)
            if freeze["frozen"] and role_scope(store, user) != "ALL":
                fail(1005, freeze["reason"])
            if bool(body.get("force")) and role_scope(store, user) != "ALL":
                fail(1008, "特批变更仅限超管操作。")
            version = body.get("version")
            if version is not None and int(version) != int(obj.get("version") or 1):
                fail(1004, "基线已更新，请刷新后重提。")
            duplicated = next((c for c in store["change_request"]
                               if c.get("objective_id") == objective_id
                               and c.get("status") == "pending"
                               and normalize_type(c.get("change_type") or "content") == change_type), None)
            if duplicated:
                fail(1004, "该目标已有同类型待审批申请（%s），请先处理。" % duplicated["change_id"])

            after = _validate_after(store, obj, change_type, body.get("after_snapshot") or {})
            before = build_before(store, obj, change_type)
            if before == after:
                fail(1001, "变更后内容与原值一致，无需发起变更。")
            delay_days = 0
            if change_type == "due_date":
                old_end, new_end = parse_dt(before.get("plan_end")), parse_dt(after.get("plan_end"))
                if old_end and new_end:
                    delay_days = max(0, (new_end.date() - old_end.date()).days)
            critical = _critical_context(store, obj, delay_days)
            applicant = user
            approver = direct_manager(store, applicant)
            if not approver:
                approver = next((u for u in store["user"]
                                 if u.get("status") == "启用" and role_scope(store, u) == "ALL" and u["user_id"] != applicant["user_id"]),
                                None)
            change = {
                "change_id": next_id(store, "change_request", "CR", 3),
                "objective_id": objective_id,
                "change_type": change_type,
                "reason_code": reason_code,
                "reason_text": reason_text,
                "before_snapshot": before,
                "after_snapshot": after,
                "impact_note": impact_note,
                "applicant_id": applicant["user_id"],
                "approver_id": approver["user_id"] if approver else "",
                "status": "pending",
                "applied_at": iso(),
                "approved_at": "",
                "on_critical_path": critical["on_critical_path"],
                "predicted_delay_days": critical["predicted_delay_days"],
                "version_before": int(obj.get("version") or 1),
            }
            store["change_request"].insert(0, change)
            if approver and approver["user_id"] != applicant["user_id"]:
                create_reminder(
                    store, approver["user_id"], "objective", objective_id, "deviation", "yellow",
                    "变更待审批：%s" % obj["title"],
                    "%s 发起「%s」变更（原因：%s），请审批。" % (
                        applicant["display_name"], type_label(change_type), reason_code),
                    dedupe_key="change:%s:apply" % change["change_id"],
                )
            audit(store, user, "发起变更", "change_request", change["change_id"],
                  before, {"after": after, "reason": reason_text})
            write_store(store)
            return {
                "change_id": change["change_id"],
                "status": change["status"],
                "status_label": STATUS_LABEL[change["status"]],
                "before_snapshot": before,
                "after_snapshot": after,
                "diff": diff_rows(store, change),
                "on_critical_path": change["on_critical_path"],
                "predicted_delay_days": change["predicted_delay_days"],
                "freeze": freeze,
                "approver_name": user_name(store, change["approver_id"]),
            }

    @app.post("/api/ns/change/approve")
    @ns_api
    def ns_api_change_approve():
        body = payload()
        action = (body.get("action") or "approve").strip()
        if action not in ("approve", "reject"):
            fail(1001, "action 仅支持 approve / reject。")
        with DATA_LOCK:
            store = read_store()
            user = ns_user(store)
            api_perm(store, user, "action:change.approve")
            change = next((c for c in store["change_request"] if c["change_id"] == body.get("change_id")), None)
            if not change:
                fail(1003, "变更申请不存在。")
            if change.get("status") != "pending":
                fail(1004, "该申请已处理（%s）。" % STATUS_LABEL.get(change.get("status"), change.get("status")))
            if change.get("applicant_id") == user["user_id"]:
                fail(1008, "不能审批自己发起的变更。")
            obj = scope_objectives_check(store, user, change.get("objective_id") or "")
            if obj.get("is_archived") or obj.get("status") == "archived":
                change["status"] = "cancelled"
                change["approve_note"] = "目标已归档，申请自动作废。"
                change["approved_at"] = iso()
                if change.get("applicant_id"):
                    create_reminder(
                        store, change["applicant_id"], "objective", obj["objective_id"], "deviation", "yellow",
                        "变更申请已作废：%s" % obj["title"],
                        "%s 在审批前已被归档，申请 %s 自动作废。" % (obj["title"], change["change_id"]),
                        dedupe_key="change:%s:invalid" % change["change_id"],
                    )
                audit(store, user, "变更作废（目标已归档）", "change_request", change["change_id"],
                      {"status": "pending"}, {"status": "cancelled"})
                write_store(store)
                fail(1004, "目标已归档，申请自动作废并已通知双方。")
            note = (body.get("note") or "").strip()
            if action == "reject":
                if not note:
                    fail(1001, "驳回须填写审批意见。")
                if len(note) > 300:
                    fail(1002, "审批意见不能超过 300 字符。")
                change["status"] = "rejected"
                change["approve_note"] = note
                change["approved_at"] = iso()
                change["approver_id"] = user["user_id"]
                create_reminder(
                    store, change["applicant_id"], "objective", obj["objective_id"], "deviation", "yellow",
                    "变更被驳回：%s" % obj["title"],
                    "你发起的变更 %s 被驳回：%s。可修改后重新提交。" % (change["change_id"], note),
                    dedupe_key="change:%s:rejected" % change["change_id"],
                )
                audit(store, user, "驳回变更", "change_request", change["change_id"],
                      {"status": "pending"}, {"status": "rejected", "note": note})
            else:
                if not note:
                    note = "同意变更，按新版本执行。"
                apply_after(store, obj, change.get("after_snapshot") or {})
                change["status"] = "approved"
                change["approve_note"] = note
                change["approved_at"] = iso()
                change["approver_id"] = user["user_id"]
                change["version_after"] = int(obj.get("version") or 1)
                create_reminder(
                    store, change["applicant_id"], "objective", obj["objective_id"], "deviation", "green",
                    "变更已通过：%s" % obj["title"],
                    "你发起的「%s」变更已通过（原因：%s），目标已写入新版本 v%d。" % (
                        type_label(change.get("change_type") or "content"),
                        change.get("reason_code", ""), change["version_after"]),
                    dedupe_key="change:%s:approved" % change["change_id"],
                )
                new_owner = change.get("after_snapshot", {}).get("owner_id")
                if new_owner and new_owner != change.get("applicant_id"):
                    create_reminder(
                        store, new_owner, "objective", obj["objective_id"], "deviation", "green",
                        "目标责任人变更：%s" % obj["title"],
                        "经变更审批（%s），该目标责任人变更为你。" % change["change_id"],
                        dedupe_key="change:%s:new-owner" % change["change_id"],
                    )
                audit(store, user, "通过变更（写新版本）", "change_request", change["change_id"],
                      change.get("before_snapshot"), {"after": change.get("after_snapshot"),
                                                      "version": change["version_after"]})
            write_store(store)
            return {
                "change_id": change["change_id"],
                "status": change["status"],
                "status_label": STATUS_LABEL.get(change["status"], change["status"]),
                "version_after": change.get("version_after"),
                "notified_applicant": bool(change.get("applicant_id")),
                "objective_version": int(obj.get("version") or 1),
            }

    @app.get("/api/ns/change/timeline")
    @ns_api
    def ns_api_change_timeline():
        store = read_store()
        user = ns_user(store)
        objective_id = (request.args.get("objective_id") or "").strip()
        if objective_id:
            scope_objectives_check(store, user, objective_id)
        rows = _visible_changes(
            store, user, objective_id,
            status=(request.args.get("status") or "").strip(),
            mine=request.args.get("mine") == "1",
            applicant_id=(request.args.get("applicant_id") or "").strip(),
            approver_id=(request.args.get("approver_id") or "").strip(),
        )
        page, size = page_args()
        rows_page, page, total_pages = paginate(rows, page, size)
        data = {
            "items": [change_row(store, c, with_diff=True) for c in rows_page],
            "page": page, "size": size, "total": len(rows), "total_pages": total_pages,
            "stats": stats_payload(store, user, objective_id),
        }
        if objective_id:
            obj = obj_by_id(store, objective_id)
            data["versions"] = _versions(store, obj)
            data["objective"] = {
                "objective_id": obj["objective_id"], "title": obj["title"],
                "version": int(obj.get("version") or 1),
                "freeze": freeze_state(store, obj),
            }
        return data

    @app.get("/api/ns/change/stats")
    @ns_api
    def ns_api_change_stats():
        store = read_store()
        user = ns_user(store)
        objective_id = (request.args.get("objective_id") or "").strip()
        if objective_id:
            scope_objectives_check(store, user, objective_id)
        return stats_payload(store, user, objective_id)
