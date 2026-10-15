"""生成 C 线演示数据：周期 / 目标 / 验收 / 复盘 / 组织（写入 data/plans.json 的对应域）。

用途：验收评估命题需要「已发布目标 + 历史验收结果」才有东西可看。
本脚本只覆盖 C 线六个域（cycles / objectives / reviews / retros / departments / org_members），
不改动 A/B 线的 plans / tasks / reminders，可重复执行（幂等）。

数据设计：
- 两个周期：2026 Q3（已结束）与 2026 Q4（进行中），形成纵向对比基准；
- 10 个目标，分 3 级（公司 / 部门 / 个人），4 类 KR 指标全部出现；
- 状态覆盖「执行中 / 待验收 / 已验收 / 已复盘」，验收记录对照发布快照；
- 目标系 series_id 串起跨周期同目标，供 C3 纵向对比使用；
- 组织：技术部下挂两个研发组，另设一个本期未挂目标的产品运营部，
  让「需关注人员」「风险目标」「长期未更新」「部门无已定案」四类判定都能真实触发。

用法：
    python backend/seed_c_line.py
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

import c_metrics  # noqa: E402  同目录模块，口径引擎

DATA_DIR = BASE_DIR / "data"
STORE_FILE = DATA_DIR / "plans.json"
NOW = datetime.now().replace(microsecond=0)


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


CYCLES = [
    {
        "cycle_id": "CY-2026Q3",
        "name": "2026 Q3",
        "type": "季度",
        "start_at": "2026-07-01T00:00:00",
        "end_at": "2026-09-30T23:59:59",
        "status": "已结束",
        "note": "上一周期，作为纵向对比基准",
    },
    {
        "cycle_id": "CY-2026Q4",
        "name": "2026 Q4",
        "type": "季度",
        "start_at": "2026-10-01T00:00:00",
        "end_at": "2026-12-31T23:59:59",
        "status": "进行中",
        "note": "当前周期",
    },
]

# 每个目标：(id, series, parent, level, title, user, cycle, weight, status)
OBJECTIVE_SEEDS = [
    ("OBJ-100", "SR-100", "", 1, "把季度目标达成率提升到 85%", "U001", "CY-2026Q4", 30, c_metrics.OBJ_RUNNING),
    ("OBJ-101", "SR-101", "", 1, "沉淀一套可复用的目标闭环机制", "U001", "CY-2026Q4", 20, c_metrics.OBJ_PENDING),
    ("OBJ-200", "SR-200", "OBJ-100", 2, "提升客户响应速度", "U002", "CY-2026Q4", 25, c_metrics.OBJ_VERIFIED),
    ("OBJ-201", "SR-200", "OBJ-100", 2, "提升客户响应速度", "U002", "CY-2026Q3", 25, c_metrics.OBJ_RETROED),
    ("OBJ-202", "SR-202", "OBJ-100", 2, "压降线上故障率", "U004", "CY-2026Q4", 25, c_metrics.OBJ_VERIFIED),
    ("OBJ-203", "SR-203", "OBJ-100", 2, "推进团队能力认证", "U004", "CY-2026Q4", 20, c_metrics.OBJ_PENDING),
    ("OBJ-300", "SR-300", "OBJ-200", 3, "完成数据质量专项治理", "U002", "CY-2026Q4", 15, c_metrics.OBJ_RUNNING),
    ("OBJ-301", "SR-301", "OBJ-201", 3, "上线真实进展看板", "U004", "CY-2026Q3", 15, c_metrics.OBJ_RETROED),
    ("OBJ-302", "SR-302", "OBJ-200", 3, "完成 3 项流程自动化", "U002", "CY-2026Q4", 15, c_metrics.OBJ_RUNNING),
    ("OBJ-303", "SR-303", "OBJ-201", 3, "客户满意度提升", "U004", "CY-2026Q3", 15, c_metrics.OBJ_RETROED),
]


def kr(kr_id, title, metric, weight=1.0, target=None, unit="", current=None,
       reviewed=None, milestones=None, level="", reviewed_level=""):
    payload = {
        "kr_id": kr_id,
        "title": title,
        "metric_type": metric,
        "weight": weight,
        "target_value": target,
        "unit": unit,
        "current_value": current,
        "reviewed_value": reviewed,
        "qualitative_level": level,
        "reviewed_level": reviewed_level,
        "milestones": milestones or [],
    }
    return payload


def snapshot_from(objective):
    """发布快照：冻结目标定义与 KR 目标值，验收时只对照它，不看当前值。"""
    return {
        "published_at": objective["published_snapshot"]["published_at"],
        "title": objective["title"],
        "weight": objective["weight"],
        "key_results": [
            {k: v for k, v in item.items()
             if k not in ("current_value", "reviewed_value", "reviewed_level")}
            for item in objective["key_results"]
        ],
    }


def build_objectives():
    published_q4 = "2026-10-01T09:00:00"
    published_q3 = "2026-07-01T09:00:00"
    # 长期未更新场景：执行中目标超过 STALE_DAYS 天没有进展更新，
    # 用于触发 C4 目标全景的「需关注人员」第二条判定规则（与分数无关）。
    stale_offsets = {"OBJ-300": 9}
    krs = {
        "OBJ-100": [
            kr("KR-1001", "季度目标达成率", c_metrics.METRIC_FORWARD, 2, 85, "%", current=79),
            kr("KR-1002", "目标按期发布率", c_metrics.METRIC_FORWARD, 1, 95, "%", current=92),
            kr("KR-1003", "闭环机制评审通过", c_metrics.METRIC_MILESTONE, 1,
               milestones=[{"name": "方案定稿", "done": True}, {"name": "试点验证", "done": True},
                           {"name": "全量推广", "done": False}]),
        ],
        "OBJ-101": [
            kr("KR-1011", "机制文档沉淀", c_metrics.METRIC_MILESTONE, 2,
               milestones=[{"name": "框架成文", "done": True}, {"name": "评审定稿", "done": True},
                           {"name": "宣贯落地", "done": True}]),
            kr("KR-1012", "部门覆盖率", c_metrics.METRIC_FORWARD, 1, 100, "%", current=78),
            kr("KR-1013", "机制有效性评价", c_metrics.METRIC_QUALITATIVE, 1, level="良好"),
        ],
        "OBJ-200": [
            kr("KR-2001", "平均首次响应时长", c_metrics.METRIC_REVERSE, 2, 4, " 小时", current=4.4),
            kr("KR-2002", "客户问题一次解决率", c_metrics.METRIC_FORWARD, 1, 80, "%", current=76),
            kr("KR-2003", "响应规范落地", c_metrics.METRIC_MILESTONE, 1,
               milestones=[{"name": "规范发布", "done": True}, {"name": "全组培训", "done": True},
                           {"name": "抽检达标", "done": True}]),
            kr("KR-2004", "客户主观评价", c_metrics.METRIC_QUALITATIVE, 1, level="良好"),
        ],
        "OBJ-201": [
            kr("KR-2011", "平均首次响应时长", c_metrics.METRIC_REVERSE, 2, 6, " 小时", current=7),
            kr("KR-2012", "客户问题一次解决率", c_metrics.METRIC_FORWARD, 1, 75, "%", current=71),
            kr("KR-2013", "响应规范落地", c_metrics.METRIC_MILESTONE, 1,
               milestones=[{"name": "规范发布", "done": True}, {"name": "全组培训", "done": False},
                           {"name": "抽检达标", "done": False}]),
        ],
        "OBJ-202": [
            kr("KR-2021", "线上故障次数", c_metrics.METRIC_REVERSE, 2, 2, " 次", current=3.4),
            kr("KR-2022", "故障平均恢复时长", c_metrics.METRIC_REVERSE, 1, 30, " 分钟", current=42),
            kr("KR-2023", "监控覆盖关键链路", c_metrics.METRIC_FORWARD, 1, 100, "%", current=92),
            kr("KR-2024", "应急演练完成", c_metrics.METRIC_MILESTONE, 1,
               milestones=[{"name": "演练方案", "done": True}, {"name": "首次演练", "done": True},
                           {"name": "复盘改进", "done": False}]),
        ],
        "OBJ-203": [
            kr("KR-2031", "认证通过人数", c_metrics.METRIC_FORWARD, 2, 8, " 人", current=6),
            kr("KR-2032", "学习完成率", c_metrics.METRIC_FORWARD, 1, 90, "%", current=85),
            kr("KR-2033", "能力评级", c_metrics.METRIC_QUALITATIVE, 1, level="达标"),
        ],
        "OBJ-300": [
            kr("KR-3001", "异常数据清零率", c_metrics.METRIC_FORWARD, 2, 95, "%", current=88),
            kr("KR-3002", "校验规则补充", c_metrics.METRIC_MILESTONE, 1,
               milestones=[{"name": "规则梳理", "done": True}, {"name": "规则上线", "done": False},
                           {"name": "效果验证", "done": False}]),
        ],
        "OBJ-301": [
            kr("KR-3011", "看板覆盖计划数", c_metrics.METRIC_FORWARD, 2, 10, " 个", current=9),
            kr("KR-3012", "数据刷新及时率", c_metrics.METRIC_FORWARD, 1, 100, "%", current=100),
            kr("KR-3013", "使用满意度", c_metrics.METRIC_QUALITATIVE, 1, level="达标"),
        ],
        "OBJ-302": [
            kr("KR-3021", "自动化流程数", c_metrics.METRIC_FORWARD, 2, 3, " 项", current=2),
            kr("KR-3022", "人工工时压降", c_metrics.METRIC_FORWARD, 1, 20, "%", current=14),
        ],
        "OBJ-303": [
            kr("KR-3031", "客户满意度得分", c_metrics.METRIC_FORWARD, 2, 90, " 分", current=86),
            kr("KR-3032", "投诉件数", c_metrics.METRIC_REVERSE, 1, 2, " 件", current=3),
            kr("KR-3033", "客户回访覆盖", c_metrics.METRIC_MILESTONE, 1,
               milestones=[{"name": "回访名单", "done": True}, {"name": "首轮回访", "done": True},
                           {"name": "问题闭环", "done": True}]),
        ],
    }

    # 验收录入值：已验收 / 已复盘的目标逐条给 reviewed_value
    reviewed_values = {
        "OBJ-200": {"KR-2001": 4.4, "KR-2002": 76, "KR-2003": None, "KR-2004": None},
        "OBJ-201": {"KR-2011": 7, "KR-2012": 71, "KR-2013": None},
        "OBJ-202": {"KR-2021": 3.4, "KR-2022": 42, "KR-2023": 92, "KR-2024": None},
        "OBJ-301": {"KR-3011": 9, "KR-3012": 100, "KR-3013": None},
        "OBJ-303": {"KR-3031": 86, "KR-3032": 3, "KR-3033": None},
    }
    reviewed_levels = {
        "OBJ-200": {"KR-2004": "达标"},
        "OBJ-301": {"KR-3013": "达标"},
    }
    milestone_done = {
        "OBJ-200": {"KR-2003": 3},
        "OBJ-201": {"KR-2013": 1},
        "OBJ-202": {"KR-2024": 2},
        "OBJ-303": {"KR-3033": 3},
    }

    objectives = []
    for oid, series, parent, level, title, user, cycle, weight, status in OBJECTIVE_SEEDS:
        items = json.loads(json.dumps(krs[oid], ensure_ascii=False))
        if oid in reviewed_values:
            for item in items:
                if item["kr_id"] in reviewed_values[oid]:
                    item["reviewed_value"] = reviewed_values[oid][item["kr_id"]]
        if oid in reviewed_levels:
            for item in items:
                if item["kr_id"] in reviewed_levels[oid]:
                    item["reviewed_level"] = reviewed_levels[oid][item["kr_id"]]
        if oid in milestone_done:
            for item in items:
                if item["kr_id"] in milestone_done[oid]:
                    count = milestone_done[oid][item["kr_id"]]
                    for index, stone in enumerate(item["milestones"]):
                        stone["done"] = index < count

        published = published_q3 if cycle == "CY-2026Q3" else published_q4
        objective = {
            "objective_id": oid,
            "series_id": series,
            "parent_id": parent,
            "level": level,
            "level_label": {1: "公司级", 2: "部门级", 3: "个人级"}[level],
            "title": title,
            "desc": "",
            "user_id": user,
            "cycle_id": cycle,
            "weight": weight,
            "status": status,
            "key_results": items,
            "progress_updated": status != c_metrics.OBJ_DRAFT,
            "created_at": published,
            "updated_at": iso(NOW - timedelta(days=stale_offsets.get(oid, 0))),
            "published_snapshot": {
                "published_at": published,
                "title": title,
                "weight": weight,
                "key_results": [],
            },
        }
        objective["published_snapshot"] = snapshot_from(objective)
        objectives.append(objective)
    return objectives


def build_reviews(objectives):
    by_id = {item["objective_id"]: item for item in objectives}
    # 已验收 / 已复盘的目标各有验收记录；待验收的已提报未定级
    plan = [
        ("RV-200", "OBJ-200", "U002", 90, 88, ""),
        ("RV-201", "OBJ-201", "U002", 80, 72, ""),
        ("RV-202", "OBJ-202", "U004", 86, 58, "自评 86 与终评 58 相差 28 分，超过 20 分阈值，按规则强制说明：故障次数与恢复时长双双超出目标，验收按实际数据下修，故终评显著低于自评。"),
        ("RV-301", "OBJ-301", "U004", 92, 85, "看板覆盖计划数差 1 个，验收时按实际完成计，与自评存在差异。"),
        ("RV-303", "OBJ-303", "U004", 86, 85, ""),
        ("RV-101", "OBJ-101", "U001", 88, None, ""),
        ("RV-203", "OBJ-203", "U004", 95, None, ""),
    ]
    reviews = []
    for rid, oid, submitter, self_score, final_score, diff_note in plan:
        objective = by_id[oid]
        items = []
        for kr_item in objective["key_results"]:
            snapshot_kr = next(
                (s for s in objective["published_snapshot"]["key_results"] if s["kr_id"] == kr_item["kr_id"]),
                {},
            )
            merged = dict(snapshot_kr)
            merged.update({
                "reviewed_value": kr_item.get("reviewed_value"),
                "reviewed_level": kr_item.get("reviewed_level"),
                "milestones": kr_item.get("milestones"),
            })
            items.append({
                "kr_id": kr_item["kr_id"],
                "title": kr_item["title"],
                "metric_type": kr_item["metric_type"],
                "metric_label": c_metrics.METRIC_LABELS.get(kr_item["metric_type"], ""),
                "target_value": kr_item.get("target_value"),
                "unit": kr_item.get("unit") or "",
                "weight": kr_item.get("weight") or 1,
                "target_text": c_metrics.kr_target_text(merged, kr_item["metric_type"]),
                "actual_text": c_metrics.kr_actual_text(merged, "final", kr_item["metric_type"]),
                "statement": "按发布快照口径完成，证据见附件。" if final_score is not None else "本期按计划推进。",
                "evidence": [
                    {"name": "执行记录截图", "url": "#", "note": "验收现场留档"},
                    {"name": "数据核对表", "url": "#", "note": "由数据管理导出"},
                ] if final_score is not None else [],
                "achievement": c_metrics.kr_achievement(merged, "final"),
            })
        achievement = c_metrics.objective_achievement(objective, "final")
        status = "待验收" if final_score is None else "已验收"
        created = "2026-10-08T16:20:00" if oid.startswith("OBJ-1") or oid in ("OBJ-300", "OBJ-302") else "2026-10-06T15:10:00"
        reviews.append({
            "review_id": rid,
            "objective_id": oid,
            "cycle_id": objective["cycle_id"],
            "submitter_id": submitter,
            "snapshot_at": objective["published_snapshot"]["published_at"],
            "items": items,
            "achievement": achievement,
            "self_score": self_score,
            "final_score": final_score,
            "grade": c_metrics.grade_of(final_score) if final_score is not None else "未评级",
            "diff_note": diff_note,
            "ai_precheck": {"source": "", "lines": [], "generated_at": ""},
            "status": status,
            "reviewed_by": "U001" if final_score is not None else "",
            "created_at": created,
            "reviewed_at": "2026-10-09T10:30:00" if final_score is not None else "",
        })
    return reviews


# 组织与角色（C5）：单上级的部门树 + 成员归属。
# 技术部为顶层，下挂研发一组 / 研发二组；产品运营部本期未挂目标，
# 用来验证「部门无已定案目标时不参与达成度口径」的留空表现。
DEPARTMENTS = [
    {"dept_id": "DP-001", "name": "技术部", "parent_id": "", "leader_id": "U001",
     "note": "研发与交付主体", "created_at": "2026-07-01T09:00:00"},
    {"dept_id": "DP-002", "name": "研发一组", "parent_id": "DP-001", "leader_id": "U002",
     "note": "客户响应与数据质量", "created_at": "2026-07-01T09:05:00"},
    {"dept_id": "DP-003", "name": "研发二组", "parent_id": "DP-001", "leader_id": "U004",
     "note": "稳定性与进展看板", "created_at": "2026-07-01T09:06:00"},
    {"dept_id": "DP-004", "name": "产品运营部", "parent_id": "", "leader_id": "U001",
     "note": "本期未挂目标，口径留空", "created_at": "2026-07-01T09:10:00"},
]

ORG_MEMBERS = [
    {"user_id": "U001", "dept_id": "DP-001", "post": "职能", "manager_id": "",
     "created_at": "2026-07-01T09:20:00", "updated_at": "2026-07-01T09:20:00"},
    {"user_id": "U002", "dept_id": "DP-002", "post": "研发", "manager_id": "U001",
     "created_at": "2026-07-01T09:21:00", "updated_at": "2026-07-01T09:21:00"},
    {"user_id": "U003", "dept_id": "DP-004", "post": "产品", "manager_id": "U001",
     "created_at": "2026-07-01T09:22:00", "updated_at": "2026-07-01T09:22:00"},
    {"user_id": "U004", "dept_id": "DP-003", "post": "研发", "manager_id": "U001",
     "created_at": "2026-07-01T09:23:00", "updated_at": "2026-07-01T09:23:00"},
]


def build_retros(objectives, reviews):
    review_by_obj = {item["objective_id"]: item for item in reviews}
    plan = [
        ("RT-201", "OBJ-201", "U002"),
        ("RT-301", "OBJ-301", "U004"),
        ("RT-303", "OBJ-303", "U004"),
    ]
    sections_text = {
        "RT-201": (
            "本周期目标是提升客户响应速度，把平均首次响应时长压到 6 小时以内，一次解决率提升到 75%。",
            "实际首次响应 7 小时、一次解决率 71%，未达目标；里程碑只完成 1/3，培训环节滞后。",
            "主要原因是全组培训排在月末，与季度结算冲突，两次顺延；培训没做完，抽检自然不达标。",
            "下周期把培训前置到周期首两周；建立培训完成度周跟踪，避免再被结算挤占。",
        ),
        "RT-301": (
            "本周期目标是把真实进展看板上线，覆盖 10 个计划并保持数据实时刷新。",
            "看板按期上线，覆盖 9 个计划，刷新及时率 100%，满意度达标；差 1 个计划未纳入。",
            "未纳入的那 1 个计划属跨部门协作，数据源未打通，等对方接口开放后即可补齐。",
            "下周期补齐最后一个计划；把数据源对接列为前置项，避免临期才发现依赖。",
        ),
        "RT-303": (
            "本周期目标是客户满意度达到 90 分，投诉不超过 2 件，并完成一轮回访闭环。",
            "满意度 86 分、投诉 3 件，回访闭环全部完成；满意度差 4 分、投诉超 1 件，均未达标。",
            "回访发现高频问题是响应时效，本周期末才启动专项优化，见效时间不足。",
            "下周期把响应时效优化并入「提升客户响应速度」目标系，与响应类目标合并推进。",
        ),
    }
    retros = []
    for rid, oid, owner in plan:
        review = review_by_obj.get(oid) or {}
        review_text, compare_text, reason_text, improve_text = sections_text[rid]
        retros.append({
            "retro_id": rid,
            "objective_id": oid,
            "review_id": review.get("review_id", ""),
            "owner_id": owner,
            "sections": {
                "回顾": review_text,
                "对照": compare_text,
                "原因": reason_text,
                "改进": improve_text,
            },
            "improvements": [
                {"text": "把培训与数据源对接前置到周期首两周", "owner_id": owner,
                 "kind": "任务", "converted_to": ""},
                {"text": "下周期沿用同一目标系继续跟踪", "owner_id": owner,
                 "kind": "目标", "converted_to": ""},
            ],
            "ai_draft": {"source": "", "generated_at": ""},
            "created_at": "2026-10-10T14:00:00",
            "updated_at": "2026-10-10T15:20:00",
        })
    return retros


def main():
    if not STORE_FILE.exists():
        print("未找到 data/plans.json，请先运行 python backend/seed_demo.py")
        return
    store = json.loads(STORE_FILE.read_text(encoding="utf-8"))

    objectives = build_objectives()
    reviews = build_reviews(objectives)
    retros = build_retros(objectives, reviews)

    store["cycles"] = CYCLES
    store["objectives"] = objectives
    store["reviews"] = reviews
    store["retros"] = retros
    store["departments"] = DEPARTMENTS
    store["org_members"] = ORG_MEMBERS
    STORE_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    verified = [o for o in objectives if o["status"] in c_metrics.VERIFIED_STATUSES]
    avg = c_metrics.weighted_objective_average(verified)
    print("已写入 C 线数据（data/plans.json）：")
    print("  周期 %d · 目标 %d · 验收 %d · 复盘 %d · 部门 %d · 成员归属 %d"
          % (len(CYCLES), len(objectives), len(reviews), len(retros),
             len(DEPARTMENTS), len(ORG_MEMBERS)))
    print("  状态分布：",
          "、".join("%s %d" % (s, sum(1 for o in objectives if o["status"] == s))
                    for s in c_metrics.OBJECTIVE_STATUSES))
    print("  已定案目标加权达成度：%s" % ("%.1f" % avg if avg is not None else "—"))
    for objective in objectives:
        score = c_metrics.objective_achievement(objective, "final")
        print("    %s %-22s %-6s 达成度 %s"
              % (objective["objective_id"], objective["title"][:22], objective["status"],
                 "%.1f" % score if score is not None else "—"))

    members = {item["user_id"]: item for item in ORG_MEMBERS}
    print("  部门口径（含下级部门）：")
    for dept in DEPARTMENTS:
        scope = [dept["dept_id"]]
        stack = [dept["dept_id"]]
        while stack:
            current = stack.pop()
            for item in DEPARTMENTS:
                if item["parent_id"] == current:
                    scope.append(item["dept_id"])
                    stack.append(item["dept_id"])
        owners = {uid for uid, item in members.items() if item["dept_id"] in scope}
        scoped = [o for o in verified if o["user_id"] in owners]
        score = c_metrics.weighted_objective_average(scoped)
        print("    %s %-8s %d 个已定案 · 达成度 %s"
              % (dept["dept_id"], dept["name"], len(scoped),
                 "%.1f" % score if score is not None else "—"))


if __name__ == "__main__":
    main()
