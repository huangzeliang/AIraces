"""工作线 A · 目标与派工线（1/2）：部门目标 / SMART 校验 / 拆解树与发布。

对应软需：A1 F-101~107 · A2 F-108~112 · A3 F-113~115（验收锚点 AC-01~AC-06）
归属文件：backend/goal.py · pages/goal_dept.* / goal_smart.* / goal_tree.*

数据契约（backend/core.py 管理，data/system.json 23 张表）：
- objective 状态机：draft → pending_confirm → confirmed → published → executing → pending_review
  → reviewed → archived；非法流转由服务端拦截（1004）；
- 发布瞬间写 published_snapshot（成功标准 + KR 基线 + 责任人 + 时限），验收对照快照而非当前值；
- AI 只出草案：ai-decompose 结果不落库，人工采纳（走 /api/objective/save）后才进目标树；
- 所有写接口支持 Idempotency-Key（10 分钟内重复提交直接返回上次结果）。

接口契约：
- GET  /goal/dept · /goal/smart · /goal/tree                      页面
- POST /api/objective/save            新建 / 编辑草稿（含环检测、权重、必填）
- GET  /api/objective/tree            当前数据范围内的目标树（JSON）
- POST /api/objective/ai-decompose    AI 拆解建议（草案不落库）
- POST /api/objective/smart-check     五维质检（LLM + 规则兜底）
- POST /api/objective/smart-adopt     采纳改写建议（写 progress_log 留痕 + 自动重跑）
- POST /api/objective/countersign     承接会签（普通用户确认 / 驳回）
- POST /api/objective/publish         action=submit 提交确认 / action=publish 发布
- GET  /api/objective/detail          目标档案卡（五组只读聚合）
"""

import os
import re
from importlib import import_module

from flask import flash, jsonify, redirect, render_template, request, url_for

from backend import core

llm = import_module("backend.llm")

SMART_DIMS = ("S", "M", "A", "R", "T")
SMART_DIM_LABELS = {
    "S": "具体 Specific", "M": "可衡量 Measurable", "A": "可达成 Achievable",
    "R": "相关 Relevant", "T": "时限 Time-bound",
}
DIM_TARGET_FIELD = {"S": "title", "M": "success_criteria", "A": "success_criteria",
                    "R": "source", "T": "plan_end"}
LEVEL_LABELS = {"dep": "部门", "team": "团队", "personal": "个人"}
LEVEL_NEXT = {"dep": "team", "team": "personal", "personal": "personal"}
ROOT_LEVEL = "dep"

# 目标写法模板（F-102）：录入时参考 / 套用，套用后仍可继续编辑
TEMPLATES = (
    {
        "template_id": "TPL-01", "label": "交付上线类",
        "title": "完成「XX 系统 / 模块」上线并稳定运行",
        "success_criteria": ["XX 上线并通过验收评审", "上线后 P1 缺陷 ≤ 0", "核心运行指标达到约定阈值"],
    },
    {
        "template_id": "TPL-02", "label": "量化提升类",
        "title": "「XX 指标」由 A 提升至 B",
        "success_criteria": ["XX 指标达到 B（基线 A）", "连续 2 个月保持达标", "达成路径沉淀为可复用方案"],
    },
    {
        "template_id": "TPL-03", "label": "规范沉淀类",
        "title": "建立「XX 规范 / 机制」并落地执行",
        "success_criteria": ["规范文档发布并完成评审", "试点单位按规范执行", "季度抽查符合率 ≥ 90%"],
    },
)

WRITE_STATUSES = ("draft",)                       # 仅草稿可直接编辑
SMART_STATUSES = ("draft", "pending_confirm")     # SMART 质检 / 采纳允许的状态
MAX_CRITERIA = 8
MAX_WEIGHT_GAP = 0.01
ACTION_LOG_NAME = "progress_log"


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def _payload():
    return request.get_json(silent=True) or {}


def _require_str(payload, key, max_len, label):
    value = str(payload.get(key) or "").strip()
    if not value:
        core.fail(1001, "%s不能为空。" % label)
    if len(value) > max_len:
        core.fail(1002, "%s不能超过 %d 个字符。" % (label, max_len))
    return value


def _find(store, objective_id):
    return next((obj for obj in store["objective"] if obj["objective_id"] == objective_id), None)


def _find_user(store, user_id):
    return next((user for user in store["user"] if user["user_id"] == user_id), None)


def _user_name(store, user_id):
    user = _find_user(store, user_id)
    return user.get("display_name") if user else user_id


def _org_name(store, org_unit_id):
    org = core.org_of(store, org_unit_id)
    return org.get("org_name") if org else org_unit_id


def _period(store, period_id):
    return next((item for item in store["period"] if item["period_id"] == period_id), None)


def _now_period(store):
    return next((item for item in store["period"] if item.get("is_current")), None)


def _role_code(store, user):
    role = core.role_of_user(store, user["user_id"])
    return (role or {}).get("role_code") or ""


def _is_manager(store, user):
    # 统一账号体系下「管理员」= admin；兼容旧种子角色码 manager / super_admin
    return _role_code(store, user) in ("admin", "manager", "super_admin")


def _is_staff(store, user):
    return _role_code(store, user) == "staff"


def _status_label(status):
    return core.OBJECTIVE_STATUS.get(status, status)


# ---------------------------------------------------------------------------
# 数据范围
# ---------------------------------------------------------------------------

def _obj_visible(store, user, obj):
    org_ids = core.user_org_ids(store, user)
    if org_ids is None:
        return True
    if _is_staff(store, user):
        return obj.get("owner_id") == user["user_id"]
    return obj.get("org_unit_id") in org_ids


def _scope_objectives(store, user, include_archived=False):
    rows = [obj for obj in store["objective"] if include_archived or not obj.get("is_archived")]
    return [obj for obj in rows if _obj_visible(store, user, obj)]


def _visible_with_ancestors(store, user):
    """普通用户视角：自己的节点 + 完整父链（“为什么做”的上下文）。"""
    base = _scope_objectives(store, user)
    if not _is_staff(store, user):
        return base
    ids = {obj["objective_id"] for obj in base}
    for obj in list(base):
        cursor = obj
        while cursor.get("parent_id"):
            parent = _find(store, cursor["parent_id"])
            if parent is None or parent["objective_id"] in ids:
                break
            ids.add(parent["objective_id"])
            cursor = parent
    return [obj for obj in store["objective"] if obj["objective_id"] in ids and not obj.get("is_archived")]


def _objective_or_fail(store, user, objective_id):
    """目标读取 + 数据范围（越权一律按 1003，不泄露存在性）。"""
    obj = _find(store, objective_id)
    if obj is None:
        core.fail(1003, "目标不存在或已归档。")
    if not _obj_visible(store, user, obj):
        core.fail(1003, "目标不存在或不在你的数据范围内。")
    return obj


def _require_manager_writable(store, user, obj=None):
    if not _is_manager(store, user):
        core.fail(1008, "当前角色没有目标管理操作权限。")
    if obj is not None and not _obj_visible(store, user, obj):
        core.fail(1003, "目标不存在或不在你的数据范围内。")


# ---------------------------------------------------------------------------
# 结构与校验（AS-01：必填拦截 / 权重合计 / 环检测）
# ---------------------------------------------------------------------------

def _children_of(store, objective_id):
    return [obj for obj in store["objective"]
            if obj.get("parent_id") == objective_id and not obj.get("is_archived")]


def _weight_sum(children):
    return round(sum(float(obj.get("weight") or 0) for obj in children), 1)


def _descendant_ids(store, objective_id):
    result, queue = set(), [objective_id]
    while queue:
        current = queue.pop()
        for child in _children_of(store, current):
            if child["objective_id"] not in result:
                result.add(child["objective_id"])
                queue.append(child["objective_id"])
    return result


def _detect_cycle(store, objective_id, parent_id):
    """选自身或后代作上级即形成环（AC-01）。"""
    seen, cursor = set(), parent_id
    while cursor:
        if cursor == objective_id or cursor in seen:
            return True
        seen.add(cursor)
        parent = _find(store, cursor)
        cursor = parent.get("parent_id") if parent else ""
    return False


def _title_duplicated(store, parent_id, title, exclude_id=""):
    return any(
        obj for obj in store["objective"]
        if obj.get("parent_id") == parent_id and obj.get("title") == title
        and obj["objective_id"] != exclude_id and not obj.get("is_archived")
    )


def _collect_criteria(payload):
    raw = payload.get("success_criteria")
    if isinstance(raw, str):
        items = re.split(r"[\n；;]+", raw)
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    criteria = [str(item).strip() for item in items if str(item).strip()]
    if not criteria:
        core.fail(1001, "成功标准不能为空，至少 1 条。")
    if len(criteria) > MAX_CRITERIA:
        core.fail(1002, "成功标准最多 %d 条。" % MAX_CRITERIA)
    for item in criteria:
        if len(item) > 500:
            core.fail(1002, "单条成功标准不能超过 500 个字符。")
    return criteria


def _collect_date(payload, key, label):
    raw = str(payload.get(key) or "").strip()
    if not raw:
        core.fail(1001, "%s不能为空。" % label)
    moment = core.parse_dt(raw)
    if moment is None:
        core.fail(1002, "%s格式应为 YYYY-MM-DD。" % label)
    return moment.date()


def _collect_weight(payload):
    raw = payload.get("weight", 0)
    try:
        weight = round(float(raw or 0), 1)
    except (TypeError, ValueError):
        core.fail(1001, "权重必须是 0~100 的数字。")
    if not 0 <= weight <= 100:
        core.fail(1001, "权重必须在 0~100 之间。")
    return weight


def _validate_scope_fields(store, user, org_unit_id, owner_id, exclude_id=""):
    org_ids = core.user_org_ids(store, user)
    if org_ids is not None and org_unit_id not in org_ids:
        core.fail(1003, "组织单元不存在或超出你的数据范围。")
    owner = _find_user(store, owner_id)
    if owner is None or owner.get("status") != "启用":
        core.fail(1005, "责任人必须是本部门在册且启用的成员。")
    if org_ids is not None:
        owner_org = owner.get("org_unit_id")
        if owner_org not in org_ids:
            core.fail(1005, "责任人不在你的数据范围内（只能选择本部门及下级在册成员）。")
    return owner


# ---------------------------------------------------------------------------
# SMART 五维质检（F-108）：LLM 结构化输出 + 规则引擎兜底
# ---------------------------------------------------------------------------

def _rule_smart(store, obj):
    """确定性评分兜底：按字段完整度给基础分，依据 / 建议均可核对。"""
    title = obj.get("title") or ""
    criteria = obj.get("success_criteria") or []
    joined = title + " " + " ".join(criteria)
    start = core.parse_dt(obj.get("plan_start"))
    end = core.parse_dt(obj.get("plan_end"))
    window = (end - start).days if start and end else None

    reasons, score = [], 8
    if len(title) >= 10:
        score += 4
        reasons.append("标题含明确对象与范围（%d 字）" % len(title))
    if re.search(r"(完成|实现|上线|发布|建立|搭建|优化|提升|交付|沉淀|治理|落地)", title):
        score += 4
        reasons.append("使用可执行动词表述")
    if any(len(item) >= 10 for item in criteria) or obj.get("parent_id"):
        score += 4
        reasons.append("成功标准 / 上级承接提供了上下文")
    s_score = min(20, score)
    s_comment = "；".join(reasons) if reasons else "标题过短或缺少动作与对象，指向不够具体"

    number_count = len(re.findall(r"\d", joined))
    metric_word = bool(re.search(r"(≥|≤|达到|以内|不超过|降至|提升至|完成率|覆盖率|缺陷|时长|响应|投诉|曝光|比例)", joined))
    m_score = min(20, 6 + min(8, number_count * 2) + (6 if metric_word else 0))
    m_comment = "命中量化数字 %d 处%s" % (
        number_count, "，并含指标口径词" if metric_word else "；建议补充比例 / 数量等量化口径")
    if m_score >= 12:
        m_comment += "，可衡量性达标"

    if window is None:
        a_score = 10
        a_comment = "缺少明确起止时间，难以评估可达成性"
    else:
        a_score = 17
        a_comment = "计划窗口 %d 天" % window
        if window < 3:
            a_score = 9
            a_comment += "，窗口偏紧，注意拆期"
        elif window > 120:
            a_score = 13
            a_comment += "，周期偏长，建议拆分里程碑"
        else:
            a_comment += "，与目标体量匹配"
        if len(criteria) >= 6 and window < 14:
            a_score = min(a_score, 11)
            a_comment += "；成功标准 %d 条而窗口较短" % len(criteria)

    if obj.get("parent_id"):
        parent = _find(store, obj["parent_id"])
        r_score = 18 if obj.get("source") else 16
        r_comment = "承接上级目标「%s」%s" % (
            (parent or {}).get("title", "")[:20], "，来源清晰" if obj.get("source") else "")
    else:
        r_score = 15 if obj.get("level") == ROOT_LEVEL else 9
        r_comment = "部门级根目标，来源为部门重点工作" if obj.get("level") == ROOT_LEVEL \
            else "缺少上级承接，存在孤儿目标风险"

    t_score = 18 + (2 if (window or 0) >= 7 else 0) if end else 6
    t_comment = "明确截止 %s" % (obj.get("plan_end") or "") if end else "缺少明确截止日期"

    dims = {
        "S": {"score": s_score, "comment": s_comment, "suggestion": ""},
        "M": {"score": m_score, "comment": m_comment, "suggestion": ""},
        "A": {"score": min(20, a_score), "comment": a_comment, "suggestion": ""},
        "R": {"score": min(20, r_score), "comment": r_comment, "suggestion": ""},
        "T": {"score": min(20, t_score), "comment": t_comment, "suggestion": ""},
    }
    min_score = int(core.setting_value(store, "smart_min_score", 12) or 12)
    if dims["S"]["score"] < min_score:
        dims["S"]["suggestion"] = "把标题改写为「动词 + 对象 + 量化结果」，例如：%s" % _suggest_title(obj)
    if dims["M"]["score"] < min_score:
        dims["M"]["suggestion"] = "为成功标准补充可量化口径，例如：完成率 ≥ 95%、缺陷数 ≤ 3 件、平均响应 ≤ 2 天"
    if dims["A"]["score"] < min_score:
        dims["A"]["suggestion"] = "把目标拆分为 2~3 个阶段里程碑，并为每个阶段设定检查点与产出物"
    if dims["R"]["score"] < min_score:
        dims["R"]["suggestion"] = "补充目标来源说明，例如：承接「上级目标」或部门季度重点工作"
    if dims["T"]["score"] < min_score:
        period = _period(store, obj.get("period_id")) or {}
        suggested = period.get("end_date") or _today_plus(30)
        dims["T"]["suggestion"] = "明确截止日期：建议不晚于 %s（以当前周期结束为准）" % suggested
    return dims


def _today_plus(days):
    from datetime import timedelta
    return (core.now() + timedelta(days=days)).date().isoformat()


def _suggest_title(obj):
    title = (obj.get("title") or "").strip()[:80]
    if not re.search(r"(完成|实现|上线|发布|建立|搭建|优化|提升|交付|沉淀|治理|落地)", title):
        return "完成「%s」并按成功标准通过验收" % title
    if not re.search(r"\d", title):
        return "%s（明确量化验收标准）" % title
    return "%s，验证达标" % title


def _smart_total(smart):
    if not smart:
        return 0
    return sum(int((smart.get(dim) or {}).get("score") or 0) for dim in SMART_DIMS)


def _smart_low_dims(smart, min_score):
    if not smart:
        return list(SMART_DIMS)
    return [dim for dim in SMART_DIMS if int((smart.get(dim) or {}).get("score") or 0) < min_score]


def _submit_block_reason(store, obj):
    """提交确认前置校验（AC-04）：owner 已指定 + SMART 总分 ≥ 60 且无一维 < 12。"""
    if not obj.get("owner_id"):
        return "尚未指定唯一责任人（owner_id）"
    smart = obj.get("smart")
    if not smart:
        return "尚未完成 SMART 五维质检"
    min_score = int(core.setting_value(store, "smart_min_score", 12) or 12)
    low = _smart_low_dims(smart, min_score)
    if low:
        detail = "、".join("%s %d 分" % (SMART_DIM_LABELS[dim][:2], int((smart.get(dim) or {}).get("score") or 0)) for dim in low)
        return "SMART 维度未达标（低于 %d 分）：%s" % (min_score, detail)
    total = _smart_total(smart)
    if total < 60:
        return "SMART 总分 %d 低于 60 分" % total
    return None


def _run_smart_check(store, obj):
    """LLM 优先、规则兜底；返回 (五维结果, 引擎标注, 是否降级)。"""
    engine, degraded = "规则引擎（本地兜底）", False
    result = None
    if llm.llm_configured():
        parent = _find(store, obj.get("parent_id")) if obj.get("parent_id") else None
        result = llm.llm_smart_check(obj, period=_period(store, obj.get("period_id")),
                                     parent_title=(parent or {}).get("title", ""))
        if result:
            engine = "大模型（%s）" % os.environ.get("LLM_MODEL", "llm")
        else:
            degraded = True
    if not result:
        result = _rule_smart(store, obj)
    return result, engine, degraded


def _smart_check_and_store(objective_id):
    """质检并持久化（加锁重读，避免与并发写互相覆盖）。"""
    with core.DATA_LOCK:
        store = core.read_store()
        obj = _find(store, objective_id)
        if obj is None:
            core.fail(1003, "目标不存在或已归档。")
        result, engine, degraded = _run_smart_check(store, obj)
        obj["smart"] = result
        obj["smart_meta"] = {"checked_at": core.iso(), "engine": engine, "degraded": degraded}
        obj["updated_at"] = core.iso()
        core.write_store(store)
        return obj


def _apply_suggestion(store, obj, dim):
    """按 target_field 把建议写回目标文本（AC-02 之外的 F-109 路径）。"""
    smart = obj.get("smart") or {}
    text = ((smart.get(dim) or {}).get("suggestion") or "").strip()
    if not text:
        core.fail(1005, "该维度暂无可采纳的改写建议。")
    field = DIM_TARGET_FIELD.get(dim, "success_criteria")
    if field == "title":
        new_title = text[:100]
        if _title_duplicated(store, obj.get("parent_id") or "", new_title, exclude_id=obj["objective_id"]):
            core.fail(1005, "改写后的标题与同父目标重复，未采纳。")
        obj["title"] = new_title
    elif field == "source":
        obj["source"] = text[:200]
    elif field == "plan_end":
        match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if not match:
            core.fail(1005, "时限建议缺少可识别日期，未采纳。")
        obj["plan_end"] = match.group(0)
    else:
        criteria = list(obj.get("success_criteria") or [])
        if text not in criteria:
            criteria.append(text[:500])
        if len(criteria) > MAX_CRITERIA:
            core.fail(1005, "成功标准最多 %d 条，无法继续追加。" % MAX_CRITERIA)
        obj["success_criteria"] = criteria
    return field


def _log(store, target_type, target_id, note, operator_id, progress=None, source="manual"):
    store[ACTION_LOG_NAME].insert(0, {
        "log_id": core.next_id(store, ACTION_LOG_NAME, "LOG", 4),
        "target_type": target_type, "target_id": target_id,
        "progress": progress, "note": note, "operator_id": operator_id,
        "logged_at": core.iso(), "source": source,
    })


# ---------------------------------------------------------------------------
# 树 / 档案卡 / 对齐度 视图装配
# ---------------------------------------------------------------------------

def _node_flags(store, user, obj):
    status = obj.get("status")
    manager = _is_manager(store, user)
    staff = _is_staff(store, user)
    in_scope = _obj_visible(store, user, obj)
    flags = {
        "can_smart": manager and in_scope and status in SMART_STATUSES,
        "can_edit": manager and in_scope and status in WRITE_STATUSES,
        "can_submit": manager and in_scope and status == "draft",
        "can_publish": manager and in_scope and status == "confirmed",
        "can_confirm": staff and status == "pending_confirm" and obj.get("owner_id") == user["user_id"],
        "can_change": in_scope and status in ("published", "executing", "pending_review", "reviewed"),
        "is_context": staff and obj.get("owner_id") != user["user_id"],
    }
    if flags["can_submit"]:
        flags["submit_block"] = _submit_block_reason(store, obj)
    return flags


def _node_view(store, user, obj, depth=0):
    smart = obj.get("smart") or {}
    min_score = int(core.setting_value(store, "smart_min_score", 12) or 12)
    node = {
        "objective_id": obj["objective_id"],
        "title": obj.get("title") or "",
        "level": obj.get("level") or ROOT_LEVEL,
        "level_label": LEVEL_LABELS.get(obj.get("level"), obj.get("level") or ""),
        "status": obj.get("status") or "draft",
        "status_label": _status_label(obj.get("status")),
        "owner_id": obj.get("owner_id") or "",
        "owner_name": _user_name(store, obj.get("owner_id")),
        "org_name": _org_name(store, obj.get("org_unit_id")),
        "weight": float(obj.get("weight") or 0),
        "progress": float(obj.get("progress") or 0),
        "plan_start": obj.get("plan_start") or "",
        "plan_end": obj.get("plan_end") or "",
        "criteria": obj.get("success_criteria") or [],
        "smart_total": _smart_total(smart),
        "low_dims": _smart_low_dims(smart, min_score) if smart else [],
        "countersign": obj.get("countersign") or {},
        "published": bool(obj.get("published_snapshot")),
        "depth": depth,
        "flags": _node_flags(store, user, obj),
        "children": [],
    }
    children = _children_of(store, obj["objective_id"])
    node["children"] = [_node_view(store, user, child, depth + 1) for child in children]
    return node


def _build_tree(store, user, period_id="", org_unit_id=""):
    rows = _visible_with_ancestors(store, user)
    if period_id:
        period_ids = {period_id}
    else:
        current = _now_period(store)
        period_ids = {current["period_id"]} if current else None
    if period_ids is not None:
        rows = [obj for obj in rows if obj.get("period_id") in period_ids]
    if org_unit_id:
        org_ids = set(core.org_subtree_ids(store, org_unit_id))
        rows = [obj for obj in rows if obj.get("org_unit_id") in org_ids]
    ids = {obj["objective_id"] for obj in rows}
    children_map = {}
    for obj in rows:
        children_map.setdefault(obj.get("parent_id") or "", []).append(obj)
    for group in children_map.values():
        group.sort(key=lambda item: (-float(item.get("weight") or 0), item["objective_id"]))

    def build(obj, depth):
        node = _node_view(store, user, obj, depth)
        node["children"] = [build(child, depth + 1) for child in children_map.get(obj["objective_id"], [])]
        return node

    roots = [obj for obj in children_map.get("", [])]
    roots += [obj for obj in rows if obj.get("parent_id") and obj["parent_id"] not in ids]
    roots.sort(key=lambda item: (-float(item.get("weight") or 0), item["objective_id"]))
    return [build(obj, 0) for obj in roots]


def _alignment(store, user):
    """对齐度总评（F-111）：孤儿目标 / 空承接 / 权重异常。"""
    rows = _scope_objectives(store, user)
    issues = []
    groups = {}
    for obj in rows:
        groups.setdefault(obj.get("parent_id") or "", []).append(obj)
    for parent_id, group in groups.items():
        if not parent_id or len(group) < 2:
            continue
        total = _weight_sum(group)
        if abs(total - 100) > MAX_WEIGHT_GAP:
            parent = _find(store, parent_id)
            issues.append({
                "type": "权重异常",
                "objective_id": parent_id,
                "title": (parent or {}).get("title") or parent_id,
                "detail": "同父兄弟权重合计 %.1f%%（应 = 100%%）" % total,
            })
    for obj in rows:
        if obj.get("level") != ROOT_LEVEL and not obj.get("parent_id"):
            issues.append({
                "type": "孤儿目标", "objective_id": obj["objective_id"],
                "title": obj.get("title") or "", "detail": "%s级目标未挂接上级" % LEVEL_LABELS.get(obj.get("level"), ""),
            })
        if obj.get("status") in ("published", "executing"):
            for child in _children_of(store, obj["objective_id"]):
                if child.get("status") in ("draft", "pending_confirm"):
                    issues.append({
                        "type": "空承接", "objective_id": child["objective_id"],
                        "title": child.get("title") or "",
                        "detail": "上级「%s」已发布，该节点尚未承接" % (obj.get("title") or "")[:18],
                    })
    score = max(0, 100 - 12 * sum(1 for item in issues if item["type"] == "孤儿目标")
                - 8 * sum(1 for item in issues if item["type"] == "空承接")
                - 5 * sum(1 for item in issues if item["type"] == "权重异常"))
    return {"score": score, "issues": issues[:12]}


def _detail_payload(store, obj):
    objective_id = obj["objective_id"]
    krs = [kr for kr in store["key_result"] if kr.get("objective_id") == objective_id]
    logs = sorted(
        (log for log in store["progress_log"] if log.get("target_id") == objective_id and log.get("target_type") == "objective"),
        key=lambda item: item.get("logged_at") or "", reverse=True)[:20]
    changes = sorted(
        (change for change in store["change_request"] if change.get("objective_id") == objective_id),
        key=lambda item: item.get("applied_at") or "", reverse=True)
    assessments = [item for item in store["assessment"] if item.get("objective_id") == objective_id]
    return {
        "objective": {
            "objective_id": objective_id, "title": obj.get("title") or "",
            "source": obj.get("source") or "", "status": obj.get("status"),
            "status_label": _status_label(obj.get("status")),
            "owner_id": obj.get("owner_id") or "", "owner_name": _user_name(store, obj.get("owner_id")),
            "level_label": LEVEL_LABELS.get(obj.get("level"), ""),
            "org_name": _org_name(store, obj.get("org_unit_id")),
            "weight": float(obj.get("weight") or 0),
            "plan_start": obj.get("plan_start") or "", "plan_end": obj.get("plan_end") or "",
            "progress": float(obj.get("progress") or 0),
            "criteria": obj.get("success_criteria") or [],
            "smart_total": _smart_total(obj.get("smart")),
            "countersign": obj.get("countersign") or {},
            "snapshot": obj.get("published_snapshot"),
            "locked": obj.get("status") not in WRITE_STATUSES,
            "updated_at": obj.get("updated_at") or "",
        },
        "krs": krs,
        "logs": [{
            "note": log.get("note") or "", "progress": log.get("progress"),
            "operator_name": _user_name(store, log.get("operator_id")),
            "logged_at": (log.get("logged_at") or "").replace("T", " ")[:16],
            "source": log.get("source") or "manual",
        } for log in logs],
        "changes": [{
            "change_id": change.get("change_id"), "change_type": change.get("change_type"),
            "reason_code": change.get("reason_code"), "status": change.get("status"),
            "status_label": core.CHANGE_STATUS.get(change.get("status"), change.get("status")),
            "applied_at": (change.get("applied_at") or "").replace("T", " ")[:16],
        } for change in changes],
        "assessments": [{
            "assess_id": item.get("assess_id"), "self_score": item.get("self_score"),
            "final_score": item.get("final_score"), "grade": item.get("grade") or "",
            "conclusion": item.get("conclusion") or "",
            "status_label": core.ASSESS_STATUS.get(item.get("status"), item.get("status")),
        } for item in assessments],
    }


# ---------------------------------------------------------------------------
# 页面选项装配
# ---------------------------------------------------------------------------

def _owner_options(store, user):
    scope_ids = core.scope_user_ids(store, user)
    rows = []
    for item in store["user"]:
        if item.get("status") != "启用":
            continue
        if scope_ids is not None and item["user_id"] not in scope_ids:
            continue
        rows.append({
            "user_id": item["user_id"], "name": item.get("display_name") or item["user_id"],
            "org_name": _org_name(store, item.get("org_unit_id")),
            "position": core.dict_name(store, "position", item.get("position") or ""),
        })
    rows.sort(key=lambda item: (item["org_name"], item["name"]))
    return rows


def _org_options(store, user):
    org_ids = core.user_org_ids(store, user)
    rows = [org for org in store["org_unit"] if org.get("status") == "启用"]
    if org_ids is not None:
        rows = [org for org in rows if org["org_unit_id"] in org_ids]
    rows.sort(key=lambda item: item.get("path") or "")
    return [{"org_unit_id": org["org_unit_id"], "org_name": org["org_name"],
             "org_type": org.get("org_type")} for org in rows]


def _parent_options(store, user, exclude_id=""):
    exclude = set()
    if exclude_id:
        exclude = {exclude_id} | _descendant_ids(store, exclude_id)
    rows = _scope_objectives(store, user)
    rows = [obj for obj in rows if obj["objective_id"] not in exclude]
    by_parent = {}
    for obj in rows:
        by_parent.setdefault(obj.get("parent_id") or "", []).append(obj)
    ordered = []

    def walk(parent_id, depth):
        group = sorted(by_parent.get(parent_id, []), key=lambda item: (-float(item.get("weight") or 0), item["objective_id"]))
        for obj in group:
            ordered.append({"objective_id": obj["objective_id"], "title": obj.get("title") or "",
                            "level_label": LEVEL_LABELS.get(obj.get("level"), ""), "depth": depth})
            walk(obj["objective_id"], depth + 1)

    walk("", 0)
    return ordered


def _weight_ctx(store, user):
    """同父兄弟权重合计的实时数据（A1 底部合计条 + 发布阻断提示共用）。"""
    groups = {}
    for obj in _scope_objectives(store, user):
        parent_id = obj.get("parent_id") or ""
        groups.setdefault(parent_id, []).append({
            "objective_id": obj["objective_id"], "title": obj.get("title") or "",
            "weight": float(obj.get("weight") or 0), "status": obj.get("status"),
        })
    return {parent_id: {"sum": _weight_sum(rows), "children": rows}
            for parent_id, rows in groups.items()}


def _children_ctx(store, user):
    """完整度检查（F-105）所需：父目标 -> 现有子目标（含成功标准），仅前端本地比对。"""
    result = {}
    for obj in _scope_objectives(store, user):
        parent_id = obj.get("parent_id") or ""
        result.setdefault(parent_id, []).append({
            "objective_id": obj["objective_id"], "title": obj.get("title") or "",
            "criteria": obj.get("success_criteria") or [], "status": obj.get("status"),
        })
    return result


def _page_common(store, user, active_page, page_name):
    return {
        "active_page": active_page,
        "page_name": page_name,
        "is_manager": _is_manager(store, user),
        "is_staff": _is_staff(store, user),
        "current_period": _now_period(store),
    }


# ---------------------------------------------------------------------------
# 路由注册
# ---------------------------------------------------------------------------

def register(app, services):

    # ------------------------------------------------------------- 页面 A1
    @app.get("/goal/dept")
    @core.require_menu("goal_dept")
    def goal_dept():
        store = core.read_store()
        user = core.current_user(store)
        objective_id = request.args.get("objective_id", "").strip()
        objective = None
        if objective_id:
            objective = _find(store, objective_id)
            if objective is None or not _obj_visible(store, user, objective):
                flash("目标不存在或不在你的数据范围内。", "error")
                return redirect(url_for("goal_tree"))
        context = _page_common(store, user, "goal_dept", "部门目标")
        context.update({
            "objective": objective,
            "objective_json": objective,
            "default_org_id": objective.get("org_unit_id") if objective else user.get("org_unit_id"),
            "parent_options": _parent_options(store, user, exclude_id=objective_id),
            "org_options": _org_options(store, user),
            "owner_options": _owner_options(store, user),
            "period_options": [item for item in store["period"] if not item.get("is_frozen")],
            "templates": TEMPLATES,
            "weight_ctx": _weight_ctx(store, user),
            "children_ctx": _children_ctx(store, user),
            "max_criteria": MAX_CRITERIA,
            "can_write": _is_manager(store, user),
            "recent_drafts": [obj for obj in _scope_objectives(store, user)
                              if obj.get("status") == "draft"][:8],
        })
        return render_template("pages/goal_dept.html", **context)

    # ------------------------------------------------------------- 页面 A2
    @app.get("/goal/smart")
    @core.require_menu("goal_smart")
    def goal_smart():
        store = core.read_store()
        user = core.current_user(store)
        objective_id = request.args.get("objective_id", "").strip()
        objective = _find(store, objective_id) if objective_id else None
        if objective is None or not _obj_visible(store, user, objective):
            flash("目标不存在或不在你的数据范围内，已返回拆解树。", "error")
            return redirect(url_for("goal_tree"))
        # 进入页面即自动质检：无结果，或种子里的占位评分（无 smart_meta）尚未经引擎复核时
        if objective.get("status") in SMART_STATUSES and \
                (not objective.get("smart") or not objective.get("smart_meta")):
            objective = _smart_check_and_store(objective["objective_id"])
            store = core.read_store()
        min_score = int(core.setting_value(store, "smart_min_score", 12) or 12)
        smart = objective.get("smart") or {}
        dims = [{
            "key": dim, "label": SMART_DIM_LABELS[dim],
            "score": int((smart.get(dim) or {}).get("score") or 0),
            "comment": (smart.get(dim) or {}).get("comment") or "",
            "suggestion": (smart.get(dim) or {}).get("suggestion") or "",
            "target_field": DIM_TARGET_FIELD.get(dim, ""),
            "low": smart and int((smart.get(dim) or {}).get("score") or 0) < min_score,
        } for dim in SMART_DIMS]
        context = _page_common(store, user, "goal_smart", "SMART 校验")
        ancestors = []
        cursor = objective
        while cursor.get("parent_id"):
            parent_obj = _find(store, cursor["parent_id"])
            if parent_obj is None:
                break
            ancestors.insert(0, {
                "objective_id": parent_obj["objective_id"],
                "title": parent_obj.get("title") or "",
                "level_label": LEVEL_LABELS.get(parent_obj.get("level"), ""),
                "status_label": _status_label(parent_obj.get("status")),
                "visible": _obj_visible(store, user, parent_obj),
            })
            cursor = parent_obj
        children = [{
            "objective_id": child["objective_id"],
            "title": child.get("title") or "",
            "status": child.get("status"),
            "status_label": _status_label(child.get("status")),
            "owner_name": _user_name(store, child.get("owner_id")),
            "weight": float(child.get("weight") or 0),
            "smart_total": _smart_total(child.get("smart")),
            "visible": _obj_visible(store, user, child),
        } for child in _children_of(store, objective["objective_id"])]
        context.update({
            "objective": objective,
            "dims": dims,
            "smart_total": _smart_total(smart),
            "smart_meta": objective.get("smart_meta") or {},
            "min_score": min_score,
            "has_smart": bool(smart),
            "block_reason": _submit_block_reason(store, objective),
            "can_apply": _is_manager(store, user) and objective.get("status") in SMART_STATUSES,
            "countersign": objective.get("countersign") or {},
            "alignment": _alignment(store, user),
            "period": _period(store, objective.get("period_id")),
            "owner_name": _user_name(store, objective.get("owner_id")),
            "parent": _find(store, objective.get("parent_id")) if objective.get("parent_id") else None,
            "ancestors": ancestors,
            "children": children,
        })
        return render_template("pages/goal_smart.html", **context)

    # ------------------------------------------------------------- 页面 A3
    @app.get("/goal/tree")
    @core.require_menu("goal_tree")
    def goal_tree():
        store = core.read_store()
        user = core.current_user(store)
        period_id = request.args.get("period", "").strip()
        org_unit_id = request.args.get("org_unit_id", "").strip()
        if org_unit_id:
            allowed = core.user_org_ids(store, user)
            if allowed is not None and org_unit_id not in allowed:
                org_unit_id = ""
        nodes = _build_tree(store, user, period_id=period_id, org_unit_id=org_unit_id)
        flat = _flatten(nodes)
        context = _page_common(store, user, "goal_tree", "拆解树与发布")
        context.update({
            "nodes": nodes,
            "focus_id": request.args.get("focus_id", "").strip(),
            "filter_period": period_id,
            "filter_org": org_unit_id,
            "period_options": sorted(store["period"], key=lambda item: item.get("start_date") or ""),
            "org_options": _org_options(store, user),
            "stats": {
                "total": len(flat),
                "draft": sum(1 for node in flat if node["status"] == "draft"),
                "pending": sum(1 for node in flat if node["status"] == "pending_confirm"),
                "published": sum(1 for node in flat if node["status"] in ("published", "executing")),
            },
        })
        return render_template("pages/goal_tree.html", **context)

    # ------------------------------------------------------- API：保存草稿
    @app.post("/api/objective/save")
    def api_objective_save():
        payload = _payload()
        cached = core.idempotency_lookup(core.client_key())
        if cached is not None:
            return jsonify(cached), 200
        store = core.read_store()
        user = core.api_require(store, "action:goal.create")
        _require_manager_writable(store, user)

        objective_id = str(payload.get("objective_id") or "").strip()
        title = _require_str(payload, "title", 100, "目标标题")
        source = _require_str(payload, "source", 200, "目标来源")
        owner_id = _require_str(payload, "owner_id", 40, "唯一责任人")
        period_id = _require_str(payload, "period_id", 40, "周期")
        criteria = _collect_criteria(payload)
        plan_start = _collect_date(payload, "plan_start", "计划开始日期")
        plan_end = _collect_date(payload, "plan_end", "计划结束日期")
        if plan_end < plan_start:
            core.fail(1005, "计划结束日期不能早于开始日期。")
        weight = _collect_weight(payload)
        parent_id = str(payload.get("parent_id") or "").strip()
        org_unit_id = str(payload.get("org_unit_id") or "").strip() or user.get("org_unit_id") or ""

        period = _period(store, period_id)
        if period is None:
            core.fail(1003, "周期不存在。")
        if period.get("is_frozen"):
            core.fail(1005, "周期「%s」已冻结，不能新建或修改目标。" % period.get("period_name"))
        period_start = core.parse_dt(period.get("start_date")).date()
        period_end = core.parse_dt(period.get("end_date")).date()
        if plan_start < period_start or plan_end > period_end:
            core.fail(1005, "计划起止须落在周期 %s ~ %s 内。" % (period.get("start_date"), period.get("end_date")))

        parent = None
        if parent_id:
            parent = _find(store, parent_id)
            if parent is None or not _obj_visible(store, user, parent):
                core.fail(1003, "上级目标不存在或超出数据范围。")
            if parent.get("is_archived"):
                core.fail(1003, "上级目标已归档，请重新选择。")

        editing = None
        if objective_id:
            editing = _objective_or_fail(store, user, objective_id)
            if editing.get("status") not in WRITE_STATUSES:
                core.fail(1004, "目标当前状态为「%s」，已发布或进入后续流程，请走变更申请。" % _status_label(editing.get("status")),
                          {"change_apply_url": "/changes?objective_id=%s" % objective_id})
            if _detect_cycle(store, objective_id, parent_id):
                core.fail(1005, "上级目标选择会形成环（不能选择自身或其后代节点）。", {"field": "parent_id"})
        _validate_scope_fields(store, user, org_unit_id, owner_id)
        if _title_duplicated(store, parent_id, title, exclude_id=objective_id):
            core.fail(1005, "同一父目标下已存在同名目标，请调整标题。")

        with core.DATA_LOCK:
            locked = core.read_store()
            if editing is not None:
                target = _find(locked, objective_id)
                if target is None:
                    core.fail(1003, "目标不存在或已归档。")
            else:
                target = {
                    "objective_id": core.next_id(locked, "objective", "O", 3),
                    "level": ROOT_LEVEL, "progress": 0.0, "attainment": None,
                    "status": "draft", "is_archived": False, "published_snapshot": None,
                    "smart": None, "countersign": {},
                    "created_by": user["user_id"], "created_at": core.iso(),
                }
                locked["objective"].append(target)
            target.update({
                "title": title, "source": source, "parent_id": parent_id,
                "level": LEVEL_NEXT.get(parent.get("level"), ROOT_LEVEL) if parent else ROOT_LEVEL,
                "org_unit_id": org_unit_id, "owner_id": owner_id, "period_id": period_id,
                "weight": weight, "plan_start": plan_start.isoformat(), "plan_end": plan_end.isoformat(),
                "success_criteria": criteria, "updated_at": core.iso(),
            })
            if editing is not None:
                _log(locked, "objective", objective_id, "编辑目标草稿", user["user_id"], target.get("progress"))
            core.write_store(locked)
            result = core.ok({
                "objective_id": target["objective_id"],
                "status": target["status"],
                "status_label": _status_label(target["status"]),
                "parent_id": parent_id,
                "weight_sum": _weight_sum(_children_of(locked, parent_id)),
            }, "草稿已保存")
        core.idempotency_save(core.client_key(), result)
        return jsonify(result), 200

    # ------------------------------------------------------- API：目标树
    @app.get("/api/objective/tree")
    def api_objective_tree():
        store = core.read_store()
        user = core.current_user(store)
        if user is None:
            core.fail(1008, "登录已失效，请重新登录。")
        period_id = request.args.get("period", "").strip()
        org_unit_id = request.args.get("org_unit_id", "").strip()
        nodes = _build_tree(store, user, period_id=period_id, org_unit_id=org_unit_id)
        return jsonify(core.ok({"nodes": nodes, "weights": _weight_ctx(store, user)}))

    # ------------------------------------------------- API：AI 拆解建议
    @app.post("/api/objective/ai-decompose")
    def api_objective_ai_decompose():
        payload = _payload()
        cached = core.idempotency_lookup(core.client_key())
        if cached is not None:
            return jsonify(cached), 200
        store = core.read_store()
        user = core.api_require(store, "action:goal.ai_draft")
        objective_id = str(payload.get("objective_id") or "").strip()
        if not objective_id:
            core.fail(1001, "请先保存目标草稿，再生成拆解建议。")
        obj = _objective_or_fail(store, user, objective_id)
        _require_manager_writable(store, user, obj)
        if obj.get("status") not in WRITE_STATUSES:
            core.fail(1004, "目标当前状态为「%s」，不可生成拆解草案。" % _status_label(obj.get("status")))

        siblings = [child.get("title") or "" for child in _children_of(store, objective_id)]
        engine, degraded = "规则引擎（本地兜底）", False
        result = None
        if llm.llm_configured():
            result = llm.llm_goal_decompose(obj, sibling_titles=siblings)
            if result:
                engine = "大模型（%s）" % os.environ.get("LLM_MODEL", "llm")
            else:
                degraded = True
        if not result:
            result = {"children": _rule_decompose(obj, siblings)}
        response = core.ok({
            "children": result["children"],
            "engine": engine,
            "degraded": degraded,
            "notice": "草案只在右侧展示，不写入目标树；勾选采纳后才落为子目标草稿。",
        })
        core.idempotency_save(core.client_key(), response)
        return jsonify(response)

    def _rule_decompose(obj, sibling_titles):
        criteria = obj.get("success_criteria") or []
        children = []
        for index, criterion in enumerate(criteria[:4], start=1):
            title = str(criterion).strip()[:100]
            children.append({
                "title": title,
                "success_criteria": [str(criterion).strip()[:200]],
                "rationale": "对应上级成功标准第 %d 条：%s" % (index, str(criterion)[:40]),
            })
        if not children:
            children.append({
                "title": ("推进「%s」的关键动作" % (obj.get("title") or ""))[:100],
                "success_criteria": ["完成关键动作并留存可复核记录"],
                "rationale": "上级目标缺少可拆解的成功标准，生成兜底草案。",
            })
        for child in children:
            base = child["title"]
            suffix = 2
            while child["title"] in sibling_titles:
                child["title"] = ("%s（%d）" % (base[:96], suffix))[:100]
                suffix += 1
        return children

    # ------------------------------------------------- API：SMART 质检
    @app.post("/api/objective/smart-check")
    def api_objective_smart_check():
        payload = _payload()
        cached = core.idempotency_lookup(core.client_key())
        if cached is not None:
            return jsonify(cached), 200
        store = core.read_store()
        user = core.api_require(store, "action:goal.smart_check")
        objective_id = str(payload.get("objective_id") or "").strip()
        if not objective_id:
            core.fail(1001, "缺少目标参数 objective_id。")
        obj = _objective_or_fail(store, user, objective_id)
        _require_manager_writable(store, user, obj)
        if obj.get("status") not in SMART_STATUSES:
            core.fail(1004, "目标已被发布或进入后续流程（当前「%s」），页面转为只读。" % _status_label(obj.get("status")),
                      {"status": obj.get("status")})
        obj = _smart_check_and_store(objective_id)
        min_score = int(core.setting_value(store, "smart_min_score", 12) or 12)
        result = core.ok({
            "objective_id": objective_id,
            "smart": obj.get("smart"),
            "smart_total": _smart_total(obj.get("smart")),
            "low_dims": _smart_low_dims(obj.get("smart"), min_score),
            "smart_meta": obj.get("smart_meta"),
            "block_reason": _submit_block_reason(store, obj),
        }, "质检完成")
        core.idempotency_save(core.client_key(), result)
        return jsonify(result)

    # --------------------------------------------- API：采纳 SMART 建议
    @app.post("/api/objective/smart-adopt")
    def api_objective_smart_adopt():
        payload = _payload()
        cached = core.idempotency_lookup(core.client_key())
        if cached is not None:
            return jsonify(cached), 200
        store = core.read_store()
        user = core.api_require(store, "action:goal.smart_apply")
        objective_id = str(payload.get("objective_id") or "").strip()
        raw_dims = payload.get("dims")
        if not objective_id:
            core.fail(1001, "缺少目标参数 objective_id。")
        if not isinstance(raw_dims, list) or not raw_dims:
            core.fail(1001, "请先勾选要采纳的建议。")
        dims = [str(dim) for dim in raw_dims if str(dim) in SMART_DIMS]
        if not dims:
            core.fail(1001, "勾选的建议维度无效。")

        obj = _objective_or_fail(store, user, objective_id)
        _require_manager_writable(store, user, obj)
        if obj.get("status") not in SMART_STATUSES:
            core.fail(1004, "目标已被发布或进入后续流程（当前「%s」），页面转为只读。" % _status_label(obj.get("status")))
        if not obj.get("smart"):
            core.fail(1005, "请先完成 SMART 五维质检，再采纳改写建议。")

        before = {"total": _smart_total(obj.get("smart")),
                  "dims": {dim: int((obj.get("smart", {}).get(dim) or {}).get("score") or 0) for dim in SMART_DIMS}}
        applied = []
        with core.DATA_LOCK:
            locked = core.read_store()
            target = _find(locked, objective_id)
            if target is None:
                core.fail(1003, "目标不存在或已归档。")
            for dim in dims:
                field = _apply_suggestion(locked, target, dim)
                applied.append({"dim": dim, "field": field})
                _log(locked, "objective", objective_id, "采纳 SMART 建议（%s 维度 → %s）" % (dim, field),
                     user["user_id"], target.get("progress"))
            result, engine, degraded = _run_smart_check(locked, target)
            target["smart"] = result
            target["smart_meta"] = {"checked_at": core.iso(), "engine": engine, "degraded": degraded}
            target["updated_at"] = core.iso()
            core.write_store(locked)

        fresh = core.read_store()
        target = _find(fresh, objective_id)
        min_score = int(core.setting_value(fresh, "smart_min_score", 12) or 12)
        after = {"total": _smart_total(target.get("smart")),
                 "dims": {dim: int((target.get("smart", {}).get(dim) or {}).get("score") or 0) for dim in SMART_DIMS}}
        response = core.ok({
            "objective_id": objective_id,
            "applied": applied,
            "before": before,
            "after": after,
            "smart": target.get("smart"),
            "smart_meta": target.get("smart_meta"),
            "low_dims": _smart_low_dims(target.get("smart"), min_score),
            "block_reason": _submit_block_reason(fresh, target),
        }, "已采纳建议并重新质检")
        core.idempotency_save(core.client_key(), response)
        return jsonify(response)

    # ------------------------------------------------- API：承接会签
    @app.post("/api/objective/countersign")
    def api_objective_countersign():
        payload = _payload()
        cached = core.idempotency_lookup(core.client_key())
        if cached is not None:
            return jsonify(cached), 200
        store = core.read_store()
        user = core.current_user(store)
        if user is None:
            core.fail(1008, "登录已失效，请重新登录。")
        objective_id = str(payload.get("objective_id") or "").strip()
        action = str(payload.get("action") or "").strip()
        note = str(payload.get("note") or "").strip()[:200]
        if not objective_id:
            core.fail(1001, "缺少目标参数 objective_id。")
        obj = _objective_or_fail(store, user, objective_id)

        if action == "initiate":
            # 管理员发起承接会签（A2 按钮）＝ 提交确认（draft → pending_confirm）
            core.api_require(store, "action:goal.submit_confirm")
            _require_manager_writable(store, user, obj)
            return _submit_confirm(store, user, obj)

        if action in ("confirm", "reject"):
            if obj.get("owner_id") != user["user_id"]:
                core.fail(1008, "只有目标责任人本人可以确认或驳回承接。")
            if obj.get("status") != "pending_confirm":
                core.fail(1004, "目标当前状态为「%s」，无法执行承接操作。" % _status_label(obj.get("status")))
            with core.DATA_LOCK:
                locked = core.read_store()
                target = _find(locked, objective_id)
                if target is None:
                    core.fail(1003, "目标不存在或已归档。")
                if action == "confirm":
                    target["status"] = "confirmed"
                    target["countersign"] = {"status": "confirmed", "by": user["user_id"],
                                             "at": core.iso(), "note": note}
                    message = "已确认承接，等待管理员发布。"
                else:
                    target["status"] = "draft"
                    target["countersign"] = {"status": "rejected", "by": user["user_id"],
                                             "at": core.iso(), "note": note}
                    _log(locked, "objective", objective_id,
                         "承接驳回：%s" % (note or "未填写原因"), user["user_id"], target.get("progress"))
                    message = "已驳回承接，目标退回草稿由管理员调整。"
                target["updated_at"] = core.iso()
                core.write_store(locked)
                result = core.ok({"objective_id": objective_id, "status": target["status"],
                                  "status_label": _status_label(target["status"]),
                                  "countersign": target["countersign"]}, message)
            core.idempotency_save(core.client_key(), result)
            return jsonify(result)
        core.fail(1001, "action 仅支持 initiate / confirm / reject。")

    def _submit_confirm(store, user, obj):
        if obj.get("status") != "draft":
            core.fail(1004, "目标当前状态为「%s」，无法提交确认。" % _status_label(obj.get("status")))
        reason = _submit_block_reason(store, obj)
        if reason:
            core.fail(1005, "不满足提交条件：%s。" % reason, {"reason": reason})
        with core.DATA_LOCK:
            locked = core.read_store()
            target = _find(locked, obj["objective_id"])
            if target is None:
                core.fail(1003, "目标不存在或已归档。")
            target["status"] = "pending_confirm"
            target["countersign"] = {"status": "pending", "initiated_by": user["user_id"], "at": core.iso()}
            target["updated_at"] = core.iso()
            core.write_store(locked)
            result = core.ok({
                "objective_id": target["objective_id"], "status": target["status"],
                "status_label": _status_label(target["status"]),
                "owner_name": _user_name(locked, target.get("owner_id")),
            }, "已提交确认，等待责任人「%s」承接会签" % _user_name(locked, target.get("owner_id")))
        core.idempotency_save(core.client_key(), result)
        return jsonify(result)

    # ------------------------------------------------- API：提交确认 / 发布
    @app.post("/api/objective/publish")
    def api_objective_publish():
        payload = _payload()
        cached = core.idempotency_lookup(core.client_key())
        if cached is not None:
            return jsonify(cached), 200
        store = core.read_store()
        action = str(payload.get("action") or "").strip()
        objective_id = str(payload.get("objective_id") or "").strip()
        if not objective_id:
            core.fail(1001, "缺少目标参数 objective_id。")

        if action == "submit":
            user = core.api_require(store, "action:goal.submit_confirm")
            obj = _objective_or_fail(store, user, objective_id)
            _require_manager_writable(store, user, obj)
            return _submit_confirm(store, user, obj)

        if action != "publish":
            core.fail(1001, "action 仅支持 submit / publish。")
        user = core.api_require(store, "action:goal.publish")
        obj = _objective_or_fail(store, user, objective_id)
        _require_manager_writable(store, user, obj)
        if obj.get("status") != "confirmed":
            core.fail(1004, "目标当前状态为「%s」，仅「已承接」状态可发布。" % _status_label(obj.get("status")))
        scope_mode = str(payload.get("publish_scope") or "node").strip()
        if scope_mode not in ("node", "subtree"):
            core.fail(1001, "发布形态仅支持 node（本节点）/ subtree（含全部子树）。")
        publish_note = str(payload.get("publish_note") or "").strip()
        if len(publish_note) > 200:
            core.fail(1002, "发布备注不能超过 200 个字符。")

        with core.DATA_LOCK:
            locked = core.read_store()
            target = _find(locked, objective_id)
            if target is None:
                core.fail(1003, "目标不存在或已归档。")
            if target.get("status") != "confirmed":
                core.fail(1004, "该目标刚被他人操作（当前「%s」），请刷新后重试。" % _status_label(target.get("status")))

            targets = [target]
            if scope_mode == "subtree":
                for descendant_id in _descendant_ids(locked, objective_id):
                    descendant = _find(locked, descendant_id)
                    if descendant is not None:
                        targets.append(descendant)

            blocking = []
            for node in targets:
                for child in _children_of(locked, node["objective_id"]):
                    if child.get("status") in ("draft", "pending_confirm"):
                        blocking.append({
                            "objective_id": child["objective_id"], "title": child.get("title") or "",
                            "status": child.get("status"), "status_label": _status_label(child.get("status")),
                        })
            if blocking:
                core.fail(1005, "存在 %d 个子目标未完成承接，无法发布。" % len(blocking), {"blocking": blocking})

            weight_issues = []
            for node in targets:
                children = _children_of(locked, node["objective_id"])
                if len(children) >= 2:
                    total = _weight_sum(children)
                    if abs(total - 100) > MAX_WEIGHT_GAP:
                        weight_issues.append({
                            "objective_id": node["objective_id"], "title": node.get("title") or "",
                            "sum": total,
                        })
            if weight_issues:
                first = weight_issues[0]
                core.fail(1005, "「%s」的子目标权重合计 %.1f%%（同父兄弟合计须为 100%%），暂不能发布。"
                          % (first["title"][:20], first["sum"]), {"weight_issues": weight_issues})

            published = []
            now_iso = core.iso()
            for node in targets:
                if node.get("status") != "confirmed":
                    continue
                krs = [{
                    "kr_id": kr["kr_id"], "kr_name": kr.get("kr_name"),
                    "baseline_value": kr.get("baseline_value"), "target_value": kr.get("target_value"),
                } for kr in locked["key_result"] if kr.get("objective_id") == node["objective_id"]]
                node["status"] = "published"
                node["published_snapshot"] = {
                    "published_at": now_iso,
                    "criteria": list(node.get("success_criteria") or []),
                    "krs": krs,
                    "owner_id": node.get("owner_id"),
                    "plan_end": node.get("plan_end"),
                    "weight": node.get("weight"),
                    "published_by": user["user_id"],
                }
                node["updated_at"] = now_iso
                published.append({"objective_id": node["objective_id"], "title": node.get("title") or ""})
                if node["objective_id"] == objective_id and publish_note:
                    _log(locked, "objective", objective_id, "发布：%s" % publish_note, user["user_id"], node.get("progress"))
            if not published:
                core.fail(1004, "目标状态已变化，没有可发布的节点。")
            core.write_store(locked)
            result = core.ok({
                "published": published,
                "publish_scope": scope_mode,
                "snapshot_objective": objective_id,
            }, "已发布 %d 个目标节点，快照已留存" % len(published))
        core.idempotency_save(core.client_key(), result)
        return jsonify(result)

    # ------------------------------------------------- API：目标档案卡
    @app.get("/api/objective/detail")
    def api_objective_detail():
        store = core.read_store()
        user = core.current_user(store)
        if user is None:
            core.fail(1008, "登录已失效，请重新登录。")
        objective_id = request.args.get("objective_id", "").strip()
        if not objective_id:
            core.fail(1001, "缺少目标参数 objective_id。")
        obj = _objective_or_fail(store, user, objective_id)
        return jsonify(core.ok(_detail_payload(store, obj)))


def _flatten(nodes):
    rows = []
    for node in nodes:
        rows.append(node)
        rows.extend(_flatten(node["children"]))
    return rows
