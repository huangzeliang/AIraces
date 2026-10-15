"""C 线 · 目标达成度口径引擎（验收评估命题的公共底座）。

对应需求：F-302 达成度计算 · AC-21 四类算法 · AC-30 加权汇总
归属文件：backend/c_metrics.py（被 review / retro / compare 共用）

设计约束（本模块是全站「达成度」的唯一计算出口）：
- 验收（review）、对比分析（compare）、部门驾驶舱都必须调用本模块，禁止各页面自行算一遍；
- 所有分数一律 0-100 分制，保留一位小数；
- 四类 KR 算法固定，改动必须同时更新 METRIC_FORMULA 说明，保证页面上「口径可自证」。

四类指标算法：
    forward      正向指标：实际 ÷ 目标 × 100，封顶 100
    reverse      逆向指标：(2 − 实际 ÷ 目标) × 100，封顶 100、下限 0
    milestone    里程碑：已达成里程碑数 ÷ 里程碑总数 × 100
    qualitative  定性评价：按档位映射达成度
"""

import json

# ---------------------------------------------------------------------------
# 指标类型与说明
# ---------------------------------------------------------------------------

METRIC_FORWARD = "forward"
METRIC_REVERSE = "reverse"
METRIC_MILESTONE = "milestone"
METRIC_QUALITATIVE = "qualitative"

METRIC_ORDER = (METRIC_FORWARD, METRIC_REVERSE, METRIC_MILESTONE, METRIC_QUALITATIVE)

METRIC_LABELS = {
    METRIC_FORWARD: "正向指标",
    METRIC_REVERSE: "逆向指标",
    METRIC_MILESTONE: "里程碑",
    METRIC_QUALITATIVE: "定性评价",
}

METRIC_FORMULA = {
    METRIC_FORWARD: "实际 ÷ 目标 × 100，封顶 100",
    METRIC_REVERSE: "(2 − 实际 ÷ 目标) × 100，封顶 100、下限 0",
    METRIC_MILESTONE: "已达成里程碑 ÷ 里程碑总数 × 100",
    METRIC_QUALITATIVE: "按定性档位映射达成度",
}

# 定性档位 → 达成度系数（C6 字典可覆盖，这里给默认档位）
QUALITATIVE_SCALE = {
    "卓越": 100.0,
    "优秀": 100.0,
    "出色": 95.0,
    "良好": 85.0,
    "达标": 70.0,
    "基本达标": 60.0,
    "待改进": 35.0,
    "未达标": 20.0,
}
QUALITATIVE_LEVELS = ["卓越", "优秀", "出色", "良好", "达标", "基本达标", "待改进", "未达标"]

# 评分档位（分数下限从高到低），C6「评分档位」字典可覆盖
DEFAULT_GRADE_BANDS = (
    ("卓越", 90.0),
    ("达标", 75.0),
    ("待改进", 60.0),
    ("未达标", 0.0),
)

# 目标状态
OBJ_DRAFT = "草稿"
OBJ_RUNNING = "执行中"
OBJ_PENDING = "待验收"
OBJ_VERIFIED = "已验收"
OBJ_RETROED = "已复盘"

OBJECTIVE_STATUSES = (OBJ_DRAFT, OBJ_RUNNING, OBJ_PENDING, OBJ_VERIFIED, OBJ_RETROED)
# 视为「已定案」的状态：达成度按验收录入值（final）计算
VERIFIED_STATUSES = (OBJ_VERIFIED, OBJ_RETROED)

# 自评与终评差异红线：超过则强制填写差异说明
SELF_FINAL_DIFF_LIMIT = 20.0


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def num(value, default=0.0):
    """安全转数值；空串 / None / 非数值一律回落默认值。"""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def copy_json(payload):
    """深拷贝，避免调用方修改模块常量或原始数据。"""
    return json.loads(json.dumps(payload, ensure_ascii=False))


def fmt_value(value, unit=""):
    """把数值与单位拼成展示串：85 → 85%；3.5 → 3.5 万；空值 → —。"""
    if value is None or value == "":
        return "—"
    number = num(value, None)
    if number is None:
        return "%s%s" % (value, unit)
    if abs(number - round(number)) < 1e-9:
        text = str(int(round(number)))
    else:
        text = ("%.1f" % number).rstrip("0").rstrip(".")
    return "%s%s" % (text, unit)


# ---------------------------------------------------------------------------
# 单条 KR 达成度
# ---------------------------------------------------------------------------

def kr_achievement(kr, phase="final"):
    """计算单条 KR 的达成度（0-100）。

    phase="draft"：执行期的预估口径，取 current_value（最新执行值）；
    phase="final"：验收定案口径，优先取 reviewed_value（验收录入值），
                   缺省回落到 current_value —— 保证未录入时页面仍可读。
    """
    metric = (kr.get("metric_type") or METRIC_FORWARD).strip()

    if metric == METRIC_MILESTONE:
        milestones = kr.get("milestones") or []
        if not milestones:
            return 0.0
        done = sum(1 for item in milestones if item.get("done"))
        return round(clamp(done / len(milestones) * 100), 1)

    if metric == METRIC_QUALITATIVE:
        level = ""
        if phase == "final":
            level = kr.get("reviewed_level") or ""
        level = level or kr.get("qualitative_level") or ""
        return round(QUALITATIVE_SCALE.get(level, 0.0), 1)

    target = num(kr.get("target_value"))
    actual = kr.get("reviewed_value") if phase == "final" else None
    if actual is None or actual == "":
        actual = kr.get("current_value")
    actual = num(actual)
    if target <= 0:
        return 0.0
    ratio = actual / target
    if metric == METRIC_REVERSE:
        return round(clamp((2 - ratio) * 100), 1)
    return round(clamp(ratio * 100), 1)


def kr_breakdown(objective, phase="final"):
    """逐条 KR 的达成明细，供验收页 / 对比页 / 驾驶舱展示。"""
    rows = []
    for kr in objective.get("key_results") or []:
        metric = kr.get("metric_type") or METRIC_FORWARD
        rows.append({
            "kr_id": kr.get("kr_id") or "",
            "title": kr.get("title") or "",
            "metric_type": metric,
            "metric_label": METRIC_LABELS.get(metric, "正向指标"),
            "formula": METRIC_FORMULA.get(metric, ""),
            "target_value": kr.get("target_value"),
            "target_text": kr_target_text(kr, metric),
            "unit": kr.get("unit") or "",
            "weight": num(kr.get("weight"), 1.0) or 1.0,
            "current_value": kr.get("current_value"),
            "current_text": kr_actual_text(kr, phase, metric),
            "achievement": kr_achievement(kr, phase),
        })
    return rows


def kr_target_text(kr, metric):
    if metric == METRIC_MILESTONE:
        total = len(kr.get("milestones") or [])
        return "%d 个里程碑" % total
    if metric == METRIC_QUALITATIVE:
        return "定性档位"
    return fmt_value(kr.get("target_value"), kr.get("unit") or "")


def kr_actual_text(kr, phase, metric):
    if metric == METRIC_MILESTONE:
        milestones = kr.get("milestones") or []
        done = sum(1 for item in milestones if item.get("done"))
        return "%d / %d" % (done, len(milestones))
    if metric == METRIC_QUALITATIVE:
        level = (kr.get("reviewed_level") if phase == "final" else None) or kr.get("qualitative_level") or ""
        return level or "未评"
    value = (kr.get("reviewed_value") if phase == "final" else None)
    if value is None or value == "":
        value = kr.get("current_value")
    return fmt_value(value, kr.get("unit") or "")


# ---------------------------------------------------------------------------
# 目标达成度与汇总
# ---------------------------------------------------------------------------

def objective_achievement(objective, phase="final"):
    """目标达成度 = 各 KR 按 weight 加权平均；无 KR 时返回 None（而非 0）。"""
    krs = objective.get("key_results") or []
    if not krs:
        return None
    total = 0.0
    weight_sum = 0.0
    for kr in krs:
        weight = num(kr.get("weight"), 1.0) or 1.0
        total += kr_achievement(kr, phase) * weight
        weight_sum += weight
    if weight_sum <= 0:
        return None
    return round(total / weight_sum, 1)


def weighted_objective_average(objectives):
    """按 objective.weight 加权汇总（AC-30：禁止简单平均）；无有效项返回 None。"""
    total = 0.0
    weight_sum = 0.0
    for objective in objectives:
        score = objective_achievement(objective, "final")
        if score is None:
            continue
        weight = num(objective.get("weight"), 1.0) or 1.0
        total += score * weight
        weight_sum += weight
    if weight_sum <= 0:
        return None
    return round(total / weight_sum, 1)


def grade_of(score, bands=None):
    """把分数落到评分档位；score 为 None 时返回「未评级」。"""
    if score is None:
        return "未评级"
    for name, floor in (bands or DEFAULT_GRADE_BANDS):
        if score >= floor:
            return name
    return "未达标"


def grade_bands_text(bands=None):
    rows = []
    ordered = list(bands or DEFAULT_GRADE_BANDS)
    ordered.sort(key=lambda item: item[1], reverse=True)
    for index, (name, floor) in enumerate(ordered):
        ceiling = ordered[index - 1][1] - 0.1 if index > 0 else 100
        if index == 0:
            rows.append("%s：≥ %.0f" % (name, floor))
        else:
            rows.append("%s：%.0f ~ %.0f" % (name, floor, ceiling))
    return rows


def self_final_diff(self_score, final_score):
    """自评与终评的差值（绝对值）；任一缺失返回 None。"""
    if self_score is None or final_score is None:
        return None
    return round(abs(num(self_score) - num(final_score)), 1)


def needs_diff_note(self_score, final_score):
    """自评与终评差 > 20 分时必须补差异说明（AC-26）。"""
    diff = self_final_diff(self_score, final_score)
    return diff is not None and diff > SELF_FINAL_DIFF_LIMIT


# ---------------------------------------------------------------------------
# 三率分离（C3 对比分析 / C4 部门驾驶舱共用）
# ---------------------------------------------------------------------------

def _task_done(task):
    return task.get("status") == "已完成"


def three_rates(objectives, tasks):
    """三率分离口径（AC-24 / AC-30）。

    三者定义完全不同，必须分开展示，禁止拿任务完成率冒充目标达成率：
      · 目标达成率：已验收目标的加权达成度，未验收不给分（不填 0，记 None）
      · 任务完成率：任务口径，已完成 ÷ 总数
      · 目标更新覆盖率：执行期内有进展更新的目标 ÷ 在册目标
    """
    registered = [o for o in objectives if o.get("status") != OBJ_DRAFT]
    verified = [o for o in registered if o.get("status") in VERIFIED_STATUSES]
    updated = [o for o in registered if o.get("progress_updated")]

    done = sum(1 for task in tasks if _task_done(task))
    return {
        "objective_registered": len(registered),
        "objective_verified": len(verified),
        "objective_achievement": weighted_objective_average(verified),
        "task_total": len(tasks),
        "task_done": done,
        "task_rate": round(done / len(tasks) * 100, 1) if tasks else None,
        "coverage_total": len(registered),
        "coverage_updated": len(updated),
        "coverage_rate": round(len(updated) / len(registered) * 100, 1) if registered else None,
    }


def rates_note():
    """三率口径说明，供页面脚注直接引用，避免各处口径漂移。"""
    return (
        "目标达成率只统计已验收目标，按权重加权；任务完成率是执行口径；"
        "两者不可互相替代，缺失周期一律显示「无数据」而不是 0。"
    )
