"""批量演示数据：在基础种子之上，为 U001~U010 每位用户扩写足量数据。

基础场景（三色预警样例、U010 连续异常、关键路径与唯一热点、变更审批样例）来自
backend.seed；本脚本按用户扩写目标 / 指标 / 任务 / 依赖 / 进度日志 / 日计划 /
提醒 / 变更 / 证据 / 验收 / 复盘，规模足够答辩演示翻页、筛选与多角色对比。

- 所有日期相对「今天」生成，任何时间打开都有进行中 / 临期 / 超期分布；
- 保持引用完整：任务挂目标、日计划项挂本人任务、依赖无环、证据/验收挂目标；
- 重复执行会整体重建（基础种子 + 批量扩写），结果为确定性数据。

用法：
    python -m backend.seed_bulk
"""

import random
import shutil
from datetime import date, datetime, time as dtime, timedelta

from backend import seed as base_seed
from backend.core import BACKUP_DIR, STORE_FILE, write_json

RNG = random.Random(20261015)


def make_clock(now):
    today = now.date()

    def d(offset):
        return (today + timedelta(days=offset)).isoformat()

    def ts(offset, hour=18, minute=0):
        return datetime.combine(today + timedelta(days=offset), dtime(hour, minute)).isoformat()

    return d, ts


def smart_scores():
    dims = ("S", "M", "A", "R", "T")
    return {dim: {"score": RNG.randint(14, 19), "comment": "", "suggestion": ""} for dim in dims}


# ---------------------------------------------------------------------------
# 每位用户的主题池：目标标题 → 任务步骤沿用统一动作词，允许重复
# ---------------------------------------------------------------------------

USER_THEMES = {
    "U001": ["集团交付质量与合规专项", "核心系统稳定性提升计划"],
    "U002": ["研发部工程质量提升专项", "数据平台能力建设专项"],
    "U003": ["市场部增长专项", "品牌资产数字化运营"],
    "U004": ["订单服务高可用改造", "接口性能优化专项", "研发效能流水线升级", "线上故障复盘与整改"],
    "U005": ["数据仓库分层重构", "指标口径统一治理", "实时数仓链路建设", "数据质量监控体系"],
    "U006": ["监控告警体系升级", "数据库慢查询治理", "容量水位与弹性评估", "混沌工程演练专项"],
    "U007": ["组件库 2.0 建设", "前端性能与适配专项", "可视化大屏体验优化", "多端一致性治理"],
    "U008": ["客户成功流程优化", "需求管理流程升级", "客户回访机制建设", "产品知识库运营"],
    "U009": ["季度渠道投放优化", "内容运营矩阵搭建", "获客成本下降专项", "活动转化链路复盘"],
    "U010": ["私域用户运营", "活动获客转化提升", "社群活跃度运营", "投放素材迭代优化"],
}

STEPS = ["需求梳理与方案设计", "核心功能开发", "联调与自测", "灰度发布与验证", "监控与回归", "文档与验收材料"]

STEP_HOURS = [12, 24, 16, 8, 12, 6]


def objective_blueprint(owner, index):
    """返回该用户第 index（0 起）个目标的形态。"""
    shape = index % 4
    return {
        0: {"status": "executing", "level": "team", "kind": "active",
            "progress": RNG.randint(38, 66), "span": (-18, 22)},
        1: {"status": "executing", "level": "team", "kind": "active",
            "progress": RNG.randint(25, 55), "span": (-12, 26)},
        2: {"status": "executing", "level": "team", "kind": "risky",
            "progress": RNG.randint(20, 42), "span": (-24, -3)},
        3: {"status": RNG.choice(["pending_review", "reviewed"]), "level": "team", "kind": "closing",
            "progress": RNG.choice([88.0, 92.0, 96.0]), "span": (-34, -2)},
    }[shape]


def build_bulk(store, now=None):
    now = (now or datetime.now()).replace(microsecond=0)
    d, ts = make_clock(now)
    users = {u["user_id"]: u for u in store["user"]}
    orgs = {o["org_unit_id"]: o for o in store["org_unit"]}

    def manager_of(user):
        org = orgs.get(user["org_unit_id"])
        while org:
            manager = org.get("manager_user_id")
            if manager and manager != user["user_id"]:
                return manager
            org = orgs.get(org.get("parent_id"))
        return "U001"

    managers = {uid: manager_of(user) for uid, user in users.items()}

    obj_seq = [200]
    kr_seq = [200]
    task_seq = [200]
    dep_seq = [200]
    log_seq = [2000]
    plan_seq = [200]
    item_seq = [2000]
    change_seq = [200]
    ev_seq = [200]
    as_seq = [200]
    rt_seq = [200]
    rm_seq = [200]

    def nid(seq, prefix, width=3):
        seq[0] += 1
        return "%s-%0*d" % (prefix, width, seq[0])

    created_tasks = {}
    objective_ids = {}

    # ---------------- 目标 / 指标 / 任务 / 依赖 ----------------
    dept_roots = {
        "U002": {"parent": "O-001", "period": "P-2026Q4", "level": "dept"},
        "U003": {"parent": "O-101", "period": "P-2026Q4", "level": "dept"},
        "U001": {"parent": "", "period": "P-2026Q4", "level": "dept"},
    }

    for uid in ("U001", "U002", "U003", "U004", "U005", "U006", "U007", "U008", "U009", "U010"):
        user = users[uid]
        root_meta = dept_roots.get(uid, {})
        theme_list = USER_THEMES[uid]
        my_objectives = []
        for index, theme in enumerate(theme_list):
            shape = objective_blueprint(uid, index)
            start_off, end_off = shape["span"]
            period = "P-2026Q3" if shape["kind"] == "closing" and index == 3 and RNG.random() < 0.3 else "P-2026Q4"
            if period == "P-2026Q3":
                start_off, end_off = -80, -30
            oid = nid(obj_seq, "O")
            is_closing = shape["kind"] == "closing"
            title = theme
            if is_closing:
                title = "%s（收尾验收）" % theme
            if uid in dept_roots:
                parent_id = root_meta["parent"]
            else:
                parent_id = (objective_ids.get(managers[uid]) or [""])[0]
            criteria = ["阶段交付物通过评审", "关键指标达成目标值", "过程数据留痕可追溯"]
            store["objective"].append({
                "objective_id": oid, "title": title, "source": "上级拆解" if uid not in dept_roots else "部门重点工作",
                "parent_id": parent_id, "level": root_meta.get("level", "team"),
                "org_unit_id": user["org_unit_id"], "owner_id": uid, "period_id": period,
                "weight": RNG.choice([5, 10, 15, 20, 25, 30]),
                "plan_start": d(start_off), "plan_end": d(end_off),
                "success_criteria": criteria,
                "smart": smart_scores(), "progress": shape["progress"],
                "attainment": RNG.choice([88.0, 92.0, 96.0]) if is_closing and shape["status"] == "reviewed" else None,
                "status": shape["status"] if not (period == "P-2026Q3") else "archived",
                "is_archived": period == "P-2026Q3",
                "published_snapshot": {
                    "published_at": ts(start_off - 1, 10), "criteria": criteria,
                    "krs": [{"kr_id": "KR-PLACEHOLDER", "target_value": 100}],
                },
            })
            my_objectives.append((oid, shape, start_off, end_off))

            for kr_index in range(2):
                store["key_result"].append({
                    "kr_id": nid(kr_seq, "KR"), "objective_id": oid,
                    "kr_name": RNG.choice(["关键里程碑完成率", "核心指标达成率", "缺陷收敛数", "处理时长 P95"]),
                    "metric_direction": RNG.choice(["up", "down"]),
                    "baseline_value": RNG.choice([0, 40, 60, 100]),
                    "target_value": RNG.choice([80, 95, 100, 300]),
                    "current_value": RNG.choice([30, 55, 70, 88]),
                    "unit": RNG.choice(["%", "个", "ms"]), "weight": 50,
                    "calc_strategy": RNG.choice(["forward", "reverse", "milestone", "qualitative"]),
                })

            # 任务：按目标形态分配状态
            if shape["kind"] == "active":
                statuses = ["done", "done", "doing", "doing", "todo", "todo"]
            elif shape["kind"] == "risky":
                statuses = ["done", "done", "doing", "blocked", "doing", "todo"]
            else:
                statuses = ["done", "done", "done", "done", "doing", "done"]
            task_rows = []
            span = end_off - start_off
            for step_index, step in enumerate(STEPS):
                status = statuses[step_index]
                tstart = start_off + int(span * step_index / 6)
                tend = tstart + RNG.randint(3, 7)
                if shape["kind"] == "risky" and step_index in (4,):
                    tend = min(tend, -1)  # 制造超期
                if shape["kind"] == "risky" and step_index == 3:
                    tend = RNG.choice([0, 1, 2])  # 制造临期
                if step_index == 3 and status in ("doing", "todo"):
                    status = "doing" if RNG.random() < 0.6 else "todo"
                progress = {"done": 100.0, "doing": float(RNG.randint(15, 85)),
                            "blocked": float(RNG.randint(0, 40)), "todo": 0.0}[status]
                hours = STEP_HOURS[step_index] + RNG.choice([0, 4, 8])
                tid = nid(task_seq, "T")
                blocked_reason = ""
                if status == "blocked":
                    blocked_reason = RNG.choice(["等待上游接口字段冻结", "测试环境资源被占用，排队中",
                                                 "需要跨部门确认口径后继续"])
                store["work_task"].append({
                    "task_id": tid, "title": "%s·%s" % (theme, step), "detail": "",
                    "parent_task_id": "", "objective_id": oid, "kr_id": "",
                    "priority": RNG.choice(["高", "中", "中", "低"]),
                    "plan_hours": hours, "plan_start": d(tstart), "plan_end": d(tend),
                    "progress": progress, "exec_rate": progress, "status": status,
                    "source": "self" if RNG.random() < 0.2 else "assigned",
                    "is_demo": False, "manual_override": False, "blocked_reason": blocked_reason,
                    "version": 1,
                    "assignees": [{"user_id": uid, "role_in_task": "R", "weight": 100, "is_owner": True}],
                    "created_at": ts(tstart - 1, 9), "updated_at": ts(-1, 17),
                })
                store["task_assignee"].append({"task_id": tid, "user_id": uid, "role_in_task": "R",
                                               "weight": 100, "is_owner": True})
                task_rows.append((tid, status, progress, tstart, tend))
                created_tasks.setdefault(uid, []).append((tid, status, progress, tstart, tend, oid))

                if status in ("done", "doing", "blocked"):
                    store["progress_log"].append({
                        "log_id": nid(log_seq, "LOG", 4), "target_type": "task", "target_id": tid,
                        "progress": progress,
                        "note": "%s：%s" % (users[uid]["display_name"], RNG.choice(
                            ["完成主体开发，进入联调", "按评审意见完成整改", "完成自测并补充用例",
                             "联调中发现边界问题，已定位", "阶段性成果已上传"])) if status != "blocked" else "阻塞：%s" % blocked_reason,
                        "operator_id": uid, "logged_at": ts(min(tend, 0), RNG.choice([17, 18, 20])),
                        "source": RNG.choice(["daily", "manual"]),
                    })

            # 依赖链：同一目标内 FS 串联，保证无环
            prev = None
            for tid, status, progress, tstart, tend in task_rows:
                if prev:
                    store["dependency"].append({
                        "dep_id": nid(dep_seq, "D"), "from_task_id": prev, "to_task_id": tid,
                        "dep_type": "FS", "lag_days": 0,
                    })
                prev = tid

            store["progress_log"].append({
                "log_id": nid(log_seq, "LOG", 4), "target_type": "objective", "target_id": oid,
                "progress": shape["progress"], "note": "阶段复盘：整体按计划推进，关注关键路径任务",
                "operator_id": uid, "logged_at": ts(-2, 20), "source": "manual",
            })
            objective_ids[uid] = objective_ids.get(uid, []) + [oid]

    # ---------------- 日计划 / 计划项 ----------------
    existing_plan_keys = {(p["user_id"], p["plan_date"]) for p in store["daily_plan"]}
    today_states = {"U001": "draft", "U002": "approved", "U003": "approved", "U004": "submitted",
                    "U005": "submitted", "U006": "draft", "U007": "draft", "U008": "submitted",
                    "U009": "approved", "U010": "submitted"}

    for uid, tasks in created_tasks.items():
        user = users[uid]
        reviewer = managers.get(uid, "U001")
        active = [t for t in tasks if t[1] not in ("done", "cancelled")]
        any_tasks = tasks[:]
        for offset in range(-12, 1):
            day_str = d(offset)
            if (uid, day_str) in existing_plan_keys:
                continue
            if uid == "U010" and -3 <= offset <= -1:
                continue  # 保持 U010 连续三日异常演示口径
            pool = active if active else any_tasks
            picks = RNG.sample(pool, min(len(pool), RNG.choice([2, 3, 3, 4])))
            if offset == 0:
                state = today_states.get(uid, "draft")
            elif offset <= -8 and RNG.random() < 0.25:
                state = "rejected"
            else:
                state = "approved"
            pid = nid(plan_seq, "DP")
            items = []
            for sort_no, (tid, tstatus, tprogress, tstart, tend, _oid) in enumerate(picks, start=1):
                if state == "approved" and offset < 0:
                    done = True
                    progress = 100.0
                elif offset == 0 and state == "approved":
                    done = RNG.random() < 0.4
                    progress = 60.0 if done else float(RNG.randint(0, 50))
                else:
                    done = False
                    progress = 0.0
                items.append({"item_id": nid(item_seq, "DIT", 4), "plan_id": pid, "task_id": tid,
                              "plan_hours": float(RNG.choice([2, 3, 4, 4, 5, 6])),
                              "plan_desc": RNG.choice(["推进核心功能开发", "联调与问题修复", "整理阶段产出",
                                                       "补充测试用例", "对齐上游依赖"]),
                              "done": done, "progress": progress, "sort_no": sort_no,
                              "abnormal_flag": "阻塞" if tstatus == "blocked" and offset == 0 else ""})
            if state == "approved":
                if offset < 0:
                    day_progress = 100.0 if RNG.random() < 0.82 else float(RNG.choice([65.0, 80.0]))
                else:
                    day_progress = float(sum(i["progress"] for i in items) / max(1, len(items)))
            else:
                day_progress = 0.0
            if -2 <= offset <= -1 or (uid == "U010" and offset <= -4):
                day_progress = 100.0 if state == "approved" else day_progress  # 避免额外连续异常触发
            reviewer = managers.get(uid, "U001") if uid not in ("U001", "U002", "U003") else ""
            store["daily_plan"].append({
                "plan_id": pid, "user_id": uid, "plan_date": day_str, "day_status": state,
                "day_progress": day_progress, "streak_abnormal": 0,
                "reviewer_id": reviewer if state in ("approved", "rejected") else "",
                "reviewed_at": ts(offset, 21) if state in ("approved", "rejected") else "",
                "reject_reason": "当日计划与目标口径不一致，请拆分到具体任务后重新提交" if state == "rejected" else "",
                "submitted_at": ts(offset, 9) if state in ("approved", "rejected", "submitted") else "",
                "ack_at": ts(offset, 22) if state == "approved" else "",
            })
            store["daily_plan_item"].extend(items)

    # ---------------- 提醒 ----------------
    def add_reminder(uid, target_type, target_id, alert_type, level, title, body, status="未读", role=""):
        rid = nid(rm_seq, "RM")
        store["reminder"].append({
            "reminder_id": rid, "user_id": uid, "target_type": target_type, "target_id": target_id,
            "alert_type": alert_type, "level": level, "title": title, "body": body,
            "status": status, "created_at": ts(-RNG.randint(0, 3), RNG.choice([6, 9, 12])),
            "read_at": ts(-1, 13) if status != "未读" else "", "ack_at": "",
            "trigger_id": "", "role": role, "dedupe_key": "bulk:%s:%s:%s" % (alert_type, target_id or uid, uid),
        })
        return rid

    for uid, tasks in created_tasks.items():
        overdue = [t for t in tasks if t[1] != "done" and t[4] < 0]
        due_soon = [t for t in tasks if t[1] != "done" and 0 <= t[4] <= 2]
        blocked = [t for t in tasks if t[1] == "blocked"]
        for tid, _s, _p, _ts_, _te, _oid in overdue[:2]:
            add_reminder(uid, "task", tid, "overdue", "red",
                         "任务已超期", "任务 %s 已超过计划结束时间，请更新进度或发起变更。" % tid)
        for tid, _s, _p, _ts_, _te, _oid in due_soon[:2]:
            add_reminder(uid, "task", tid, "due_soon", "yellow",
                         "任务临近截止", "任务 %s 将在 2 天内到期，请确认节奏。" % tid)
        for tid, _s, _p, _ts_, _te, _oid in blocked[:1]:
            add_reminder(uid, "task", tid, "deviation", "red",
                         "任务阻塞待跟进", "任务 %s 处于阻塞状态，请协调资源或上报。" % tid, status="已读")
        mgr = managers.get(uid)
        if mgr and uid in ("U006", "U009"):
            add_reminder(mgr, "user", uid, "deviation", "yellow",
                         "%s 存在超期任务" % users[uid]["display_name"],
                         "该成员存在超期未完成任务，请关注并跟进。", role="manager")

    # ---------------- 变更 / 证据 / 验收 / 复盘 ----------------
    dept_changes = [
        ("U004", "U002", "延期", "排期偏差", "联调依赖上游接口延期，申请目标整体顺延 5 天。"),
        ("U007", "U002", "范围调整", "资源冲突", "多端一致性治理与性能专项重叠，申请缩减一个验收场景。"),
        ("U009", "U003", "指标调整", "优先级调整", "获客成本目标在渠道价格波动下过紧，申请上调 8%。"),
        ("U010", "U003", "延期", "依赖阻塞", "素材审核链路阻塞，申请活动上线顺延 3 天。"),
    ]
    for applicant, approver, ctype, reason, text in dept_changes:
        target_obj = objective_ids.get(applicant, [""])[0] or "O-001"
        obj = next((o for o in store["objective"] if o["objective_id"] == target_obj), None)
        before_end = obj["plan_end"] if obj else d(10)
        after_end = (datetime.fromisoformat(before_end) + timedelta(days=5)).date().isoformat() if obj else d(15)
        store["change_request"].append({
            "change_id": nid(change_seq, "CR"), "objective_id": target_obj, "change_type": ctype,
            "reason_code": reason, "reason_text": text,
            "before_snapshot": {"plan_end": before_end, "criteria": obj.get("success_criteria") if obj else []},
            "after_snapshot": {"plan_end": after_end, "criteria": obj.get("success_criteria") if obj else []},
            "impact_note": "影响验收时间与达成度口径，已同步干系人。",
            "applicant_id": applicant, "approver_id": approver, "status": "pending",
            "applied_at": ts(-RNG.randint(1, 3), 10), "approved_at": "",
        })
        store["change_request"].append({
            "change_id": nid(change_seq, "CR"), "objective_id": target_obj,
            "change_type": RNG.choice(["指标调整", "范围调整"]),
            "reason_code": RNG.choice(["需求变更", "优先级调整"]), "reason_text": "历史变更留痕：口径与范围调整已闭环。",
            "before_snapshot": {"criteria": ["原口径"]}, "after_snapshot": {"criteria": ["调整后口径"]},
            "impact_note": "已完成影响评估。", "applicant_id": applicant, "approver_id": approver,
            "status": RNG.choice(["approved", "rejected"]),
            "applied_at": ts(-RNG.randint(8, 16), 14), "approved_at": ts(-RNG.randint(4, 7), 11),
        })

    for uid, oid_list in objective_ids.items():
        for oid in oid_list:
            obj = next(o for o in store["objective"] if o["objective_id"] == oid)
            if obj["status"] in ("pending_review", "reviewed"):
                ev_id = nid(ev_seq, "EV")
                store["evidence"].append({
                    "evidence_id": ev_id, "target_type": "objective", "target_id": oid,
                    "file_name": "阶段验收材料-%s.zip" % oid, "file_path": "data/uploads/%s.zip" % oid,
                    "note": "含自查报告、指标截图与用例执行记录", "uploaded_by": uid,
                    "uploaded_at": ts(-RNG.randint(1, 4), 16),
                    "ai_review_result": RNG.choice([
                        {"result": "基本齐备", "missing": ["真机/生产环境验证记录"], "mismatch": [],
                         "pending": ["验收用例执行记录"]},
                        {"result": "齐备", "missing": [], "mismatch": [], "pending": []},
                        None,
                    ]),
                })
                assess_id = nid(as_seq, "AS")
                precheck = {"result": "基本齐备", "missing": ["验收用例执行记录"], "mismatch": [],
                            "pending": [], "suggestion": "建议补齐执行记录后提交终评。"}
                if obj["status"] == "pending_review":
                    store["assessment"].append({
                        "assess_id": assess_id, "objective_id": oid, "period_id": obj["period_id"],
                        "self_score": RNG.choice([85, 88, 90]), "self_note": "阶段交付完成，申请验收。",
                        "evidence_ids": [ev_id], "ai_precheck": precheck, "final_score": None, "grade": "",
                        "conclusion": "", "assessor_id": managers.get(uid, "U001"),
                        "status": "pending_manager", "appealed": False,
                    })
                else:
                    score = RNG.choice([85, 90, 95])
                    store["assessment"].append({
                        "assess_id": assess_id, "objective_id": oid, "period_id": obj["period_id"],
                        "self_score": min(100, score + 2), "self_note": "按期交付并完成验收。",
                        "evidence_ids": [ev_id], "ai_precheck": precheck, "final_score": float(score),
                        "grade": "A" if score >= 90 else "B", "conclusion": "达成",
                        "assessor_id": managers.get(uid, "U001"), "status": "done", "appealed": False,
                    })
            if obj["status"] == "archived" or (obj["status"] == "reviewed"):
                store["retrospective"].append({
                    "retro_id": nid(rt_seq, "RT"), "objective_id": oid, "period_id": obj["period_id"],
                    "review_note": "复盘：关键路径任务提前锁定资源，整体节奏可控。",
                    "result_compare": "目标值与实际值偏差在 10% 以内。",
                    "diff_reason": "依赖前置与评审前置是达成的关键。",
                    "improvements": [{"text": "把关键路径资源锁定写入排期规范", "converted": False},
                                     {"text": "验收材料模板化，减少返工", "converted": False}],
                    "author_id": uid, "created_at": ts(-RNG.randint(3, 10), 15), "converted_task_ids": [],
                })

    # ---------------- 人员负载 ----------------
    for profile in store["staff_profile"]:
        active_hours = sum(t["plan_hours"] for t in store["work_task"]
                           if profile["user_id"] in [a["user_id"] for a in t["assignees"]]
                           and t.get("status") not in ("done", "cancelled"))
        profile["current_load_hours"] = round(active_hours, 1)
        profile["saturation"] = round(min(150.0, active_hours / max(1, profile["base_capacity_hours"]) * 100), 1)
        profile["updated_at"] = ts(-1, 6)

    return store


def main():
    now = datetime.now().replace(microsecond=0)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if STORE_FILE.exists():
        backup = BACKUP_DIR / ("system-%s.json" % now.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(STORE_FILE, backup)
        print("已备份原数据：%s" % backup)
    RNG.seed(20261015)
    store = build_bulk(base_seed.build_seed(now), now)
    write_json(STORE_FILE, store)
    counts = "、".join("%s %d" % (key, len(store[key])) for key in (
        "objective", "key_result", "work_task", "dependency", "progress_log", "daily_plan",
        "daily_plan_item", "change_request", "evidence", "assessment", "retrospective", "reminder"))
    print("批量演示数据已生成：%s" % STORE_FILE)
    print("  " + counts)
    per_user = {}
    for task in store["work_task"]:
        for assignee in task["assignees"]:
            per_user[assignee["user_id"]] = per_user.get(assignee["user_id"], 0) + 1
    print("  各用户任务数：" + "、".join("%s %d" % (uid, per_user.get(uid, 0)) for uid in sorted(per_user)))


if __name__ == "__main__":
    main()
