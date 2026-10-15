"""演示种子数据：一次性生成 data/system.json 全量 23 张表。

设计原则：
- 所有日期相对「今天」生成，任何时间打开演示都有"进行中 / 临期 / 超期"的真实分布；
- 覆盖全部状态机、四类指标口径（forward / reverse / milestone / qualitative）、
  三色饱和度档位（绿 / 黄 / 红）、关键路径与唯一热点、连续三日异常等演示场景；
- 重复执行会重置为一份全新的相对数据（python -m backend.seed）。
"""

from datetime import datetime, timedelta, time as dtime

from backend.core import STORE_FILE, write_json

PASS = "123456"  # 演示统一口令


def build_seed(now=None):
    now = (now or datetime.now()).replace(microsecond=0)
    today = now.date()
    base = today - timedelta(days=9)  # 本季度工作起点（相对今天）

    def d(offset):
        return (base + timedelta(days=offset)).isoformat()

    def dtime_iso(offset, hour=18):
        return datetime.combine(base + timedelta(days=offset), dtime(hour, 0)).isoformat()

    def rel(days):
        return (today + timedelta(days=days)).isoformat()

    store = {key: [] for key in (
        "org_unit", "user", "role", "permission", "role_permission", "user_role",
        "period", "objective", "key_result",
        "work_task", "task_assignee", "dependency", "progress_log", "evidence",
        "daily_plan", "daily_plan_item",
        "change_request", "assessment", "retrospective", "reminder", "staff_profile",
        "dict_item", "setting", "audit_log",
    )}

    # ------------------------------------------------------------------ 组织
    store["org_unit"] = [
        {"org_unit_id": "ORG-000", "org_name": "星北科技", "parent_id": "", "org_type": "company",
         "manager_user_id": "U001", "path": "/ORG-000/", "status": "启用"},
        {"org_unit_id": "ORG-001", "org_name": "研发部", "parent_id": "ORG-000", "org_type": "dept",
         "manager_user_id": "U002", "path": "/ORG-000/ORG-001/", "status": "启用"},
        {"org_unit_id": "ORG-002", "org_name": "市场部", "parent_id": "ORG-000", "org_type": "dept",
         "manager_user_id": "U003", "path": "/ORG-000/ORG-002/", "status": "启用"},
        {"org_unit_id": "ORG-101", "org_name": "平台组", "parent_id": "ORG-001", "org_type": "team",
         "manager_user_id": "U004", "path": "/ORG-000/ORG-001/ORG-101/", "status": "启用"},
        {"org_unit_id": "ORG-102", "org_name": "数据组", "parent_id": "ORG-001", "org_type": "team",
         "manager_user_id": "U005", "path": "/ORG-000/ORG-001/ORG-102/", "status": "启用"},
    ]

    store["user"] = [
        {"user_id": "U001", "display_name": "陈北峰", "login_name": "chenbf", "org_unit_id": "ORG-000",
         "position": "super_admin", "status": "启用", "last_login_at": dtime_iso(-1, 9)},
        {"user_id": "U002", "display_name": "李敏", "login_name": "limin", "org_unit_id": "ORG-001",
         "position": "manager", "status": "启用", "last_login_at": dtime_iso(0, 8)},
        {"user_id": "U003", "display_name": "周彦", "login_name": "zhouyan", "org_unit_id": "ORG-002",
         "position": "manager", "status": "启用", "last_login_at": dtime_iso(0, 9)},
        {"user_id": "U004", "display_name": "张伟", "login_name": "zhangwei", "org_unit_id": "ORG-101",
         "position": "backend", "status": "启用", "last_login_at": dtime_iso(0, 8)},
        {"user_id": "U005", "display_name": "王芳", "login_name": "wangfang", "org_unit_id": "ORG-102",
         "position": "data", "status": "启用", "last_login_at": dtime_iso(-1, 10)},
        {"user_id": "U006", "display_name": "赵磊", "login_name": "zhaolei", "org_unit_id": "ORG-101",
         "position": "backend", "status": "启用", "last_login_at": dtime_iso(-2, 15)},
        {"user_id": "U007", "display_name": "孙悦", "login_name": "sunyue", "org_unit_id": "ORG-101",
         "position": "frontend", "status": "启用", "last_login_at": dtime_iso(0, 9)},
        {"user_id": "U008", "display_name": "吴迪", "login_name": "wudi", "org_unit_id": "ORG-101",
         "position": "product", "status": "启用", "last_login_at": dtime_iso(-1, 11)},
        {"user_id": "U009", "display_name": "陈晨", "login_name": "chenchen", "org_unit_id": "ORG-002",
         "position": "market", "status": "启用", "last_login_at": dtime_iso(-1, 14)},
        {"user_id": "U010", "display_name": "何佳", "login_name": "hejia", "org_unit_id": "ORG-002",
         "position": "market", "status": "启用", "last_login_at": dtime_iso(-3, 16)},
    ]

    # ------------------------------------------------------------------ 权限
    menu_defs = [
        ("workbench", "个人工作台"), ("today", "日计划与任务"), ("dashboard", "部门驾驶舱"),
        ("goal_dept", "部门目标"), ("goal_smart", "SMART 校验"), ("goal_tree", "拆解树与发布"),
        ("assign", "智能派工"), ("progress", "进度更新"), ("warning", "预警与指导"),
        ("changes", "变更管理"), ("critical_path", "关键路径与阻塞"), ("review", "完成与验收"),
        ("retro", "复盘"), ("compare", "对比分析"), ("settings_org", "组织与角色"),
        ("settings_dict", "周期与字典"), ("data", "数据管理"), ("settings", "系统设置"),
    ]
    action_defs = [
        ("goal.create", "创建目标"), ("goal.submit_confirm", "提交确认"), ("goal.confirm_accept", "承接确认"),
        ("goal.publish", "发布目标"), ("goal.smart_apply", "采纳 SMART 建议"), ("goal.ai_draft", "AI 拆解建议"),
        ("assign.dispatch", "下达派工"), ("assign.override", "超载强制下发"),
        ("task.create", "创建任务"), ("task.update", "更新任务"), ("task.mount_goal", "挂目标申请"),
        ("daily.submit", "提交日计划"), ("daily.review", "审核日计划"), ("daily.slider", "进度拉条"),
        ("progress.manual", "手动更新进度"),
        ("guidance.generate", "AI 过程指导"), ("dep.save", "维护依赖关系"),
        ("change.apply", "发起变更"), ("change.approve", "审批变更"),
        ("review.submit", "完成提报"), ("review.accept", "对照验收"), ("review.appeal", "验收申诉"),
        ("retro.create", "编写复盘"), ("retro.convert", "复盘转改进"),
        ("org.manage", "组织维护"), ("role.manage", "角色授权"), ("user.manage", "用户管理"),
        ("period.manage", "周期管理"), ("dict.manage", "字典管理"), ("settings.manage", "系统参数"),
        ("data.import", "数据导入"), ("data.export", "数据导出"), ("data.resolve", "异常处置"),
        ("data.backup", "备份恢复"),
    ]
    field_defs = [
        ("field:objective.owner_id", "目标责任人"), ("field:objective.content", "目标内容"),
        ("field:objective.status_confirm", "承接确认状态"), ("field:work_task.assignees", "任务执行人"),
        ("field:staff_profile.capacity", "人员产能"), ("field:assessment.self_score", "自评分"),
        ("field:assessment.final_score", "终评分"), ("field:setting.value", "系统参数值"),
        ("field:role.grant", "角色授权"),
    ]
    perms = []
    index = 1
    for code, name in menu_defs:
        perms.append({"perm_id": "P%03d" % index, "perm_code": "menu:" + code, "perm_type": "menu",
                      "target_key": code, "group_name": "菜单"})
        index += 1
    for code, name in action_defs:
        perms.append({"perm_id": "P%03d" % index, "perm_code": "action:" + code, "perm_type": "action",
                      "target_key": code, "group_name": "操作"})
        index += 1
    for code, name in field_defs:
        perms.append({"perm_id": "P%03d" % index, "perm_code": code, "perm_type": "field",
                      "target_key": code.split(":", 1)[1], "group_name": "字段"})
        index += 1
    store["permission"] = perms

    managers_menus = ["workbench", "today", "dashboard", "goal_dept", "goal_smart", "goal_tree", "assign",
                      "progress", "warning", "changes", "critical_path", "review", "retro", "compare"]
    managers_actions = ["goal.create", "goal.submit_confirm", "goal.publish", "goal.smart_apply", "goal.ai_draft",
                        "assign.dispatch", "assign.override", "task.create", "daily.review", "progress.manual",
                        "change.approve", "review.accept", "retro.create", "retro.convert", "data.export",
                        "guidance.generate", "dep.save"]
    managers_fields = ["field:objective.content", "field:objective.owner_id", "field:work_task.assignees",
                       "field:staff_profile.capacity", "field:assessment.final_score"]
    staff_menus = ["workbench", "today", "progress", "warning", "changes", "critical_path", "review", "retro", "compare"]
    staff_actions = ["goal.confirm_accept", "task.create", "task.update", "task.mount_goal", "daily.submit",
                     "daily.slider", "change.apply", "review.submit", "review.appeal", "retro.create",
                     "guidance.generate"]
    staff_fields = ["field:objective.status_confirm", "field:assessment.self_score"]

    def perms_for(menu_keys, action_keys, field_keys):
        codes = ["menu:" + m for m in menu_keys] + ["action:" + a for a in action_keys] + field_keys
        return [p["perm_id"] for p in perms if p["perm_code"] in codes]

    store["role"] = [
        {"role_id": "R001", "role_code": "super_admin", "role_name": "系统管理员", "scope_type": "ALL",
         "is_builtin": True, "status": "启用", "remark": "全局配置与审计，不参与业务操作"},
        {"role_id": "R002", "role_code": "manager", "role_name": "部门负责人", "scope_type": "DEPT",
         "is_builtin": True, "status": "启用", "remark": "本部门及下级组织的目标、派工与验收"},
        {"role_id": "R003", "role_code": "staff", "role_name": "员工", "scope_type": "SELF",
         "is_builtin": True, "status": "启用", "remark": "承接目标、日计划、拉条与自评"},
    ]
    super_all = [p["perm_id"] for p in perms if p["perm_code"].startswith("menu:")] + \
        [p["perm_id"] for p in perms if p["perm_code"] in (
            "action:org.manage", "action:role.manage", "action:user.manage", "action:period.manage",
            "action:dict.manage", "action:settings.manage", "action:data.import", "action:data.export",
            "action:data.resolve", "action:data.backup", "field:setting.value", "field:role.grant")]
    store["role_permission"] = (
        [{"role_id": "R001", "perm_id": pid} for pid in super_all] +
        [{"role_id": "R002", "perm_id": pid} for pid in perms_for(managers_menus, managers_actions, managers_fields)] +
        [{"role_id": "R003", "perm_id": pid} for pid in perms_for(staff_menus, staff_actions, staff_fields)]
    )
    store["user_role"] = [
        {"user_id": "U001", "role_id": "R001", "org_unit_id": "ORG-000", "granted_by": "system", "granted_at": dtime_iso(-30)},
        {"user_id": "U002", "role_id": "R002", "org_unit_id": "ORG-001", "granted_by": "U001", "granted_at": dtime_iso(-28)},
        {"user_id": "U003", "role_id": "R002", "org_unit_id": "ORG-002", "granted_by": "U001", "granted_at": dtime_iso(-28)},
    ] + [
        {"user_id": uid, "role_id": "R003", "org_unit_id": org, "granted_by": "U001", "granted_at": dtime_iso(-27)}
        for uid, org in (("U004", "ORG-101"), ("U005", "ORG-102"), ("U006", "ORG-101"), ("U007", "ORG-101"),
                         ("U008", "ORG-101"), ("U009", "ORG-002"), ("U010", "ORG-002"))
    ]

    # ------------------------------------------------------------------ 周期
    store["period"] = [
        {"period_id": "P-2026Q3", "period_name": "2026 年 Q3", "period_type": "quarter",
         "start_date": rel(-105), "end_date": rel(-15), "is_current": False, "is_frozen": True},
        {"period_id": "P-2026Q4", "period_name": "2026 年 Q4", "period_type": "quarter",
         "start_date": rel(-14), "end_date": rel(77), "is_current": True, "is_frozen": False},
        {"period_id": "P-2026-10", "period_name": "2026 年 10 月", "period_type": "month",
         "start_date": rel(-14), "end_date": rel(16), "is_current": False, "is_frozen": False},
    ]

    # ------------------------------------------------------------------ 目标
    def smart(scores):
        dims = ("S", "M", "A", "R", "T")
        return {dim: {"score": scores[i], "comment": "", "suggestion": ""} for i, dim in enumerate(dims)}

    store["objective"] = [
        {"objective_id": "O-001", "title": "研发部 Q4 交付 AIraces 目标闭环平台并达成稳定性 SLA",
         "source": "部门重点工作", "parent_id": "", "level": "dept", "org_unit_id": "ORG-001",
         "owner_id": "U002", "period_id": "P-2026Q4", "weight": 40,
         "plan_start": d(0), "plan_end": d(80),
         "success_criteria": ["11 月底完成拆解 / 派工 / 验收 3 个核心模块上线", "线上 P1 缺陷保持 0",
                              "平台演示通过正式验收评审"],
         "smart": smart([18, 17, 16, 18, 16]), "progress": 58.0, "attainment": None,
         "status": "executing", "is_archived": False,
         "published_snapshot": {"published_at": dtime_iso(-12, 10),
                                "criteria": ["11 月底完成拆解 / 派工 / 验收 3 个核心模块上线", "线上 P1 缺陷保持 0",
                                             "平台演示通过正式验收评审"],
                                "krs": [{"kr_id": "KR-001", "target_value": 300},
                                        {"kr_id": "KR-002", "target_value": 4},
                                        {"kr_id": "KR-003", "target_value": "A"}]}},
        {"objective_id": "O-002", "title": "搭建目标拆解与 SMART 校验模块", "source": "上级拆解",
         "parent_id": "O-001", "level": "team", "org_unit_id": "ORG-101", "owner_id": "U004",
         "period_id": "P-2026Q4", "weight": 30, "plan_start": d(0), "plan_end": d(14),
         "success_criteria": ["拆解树支持 3 层结构与上下追溯", "SMART 五维质检拦截率 100%", "AI 草案采纳流程可演示"],
         "smart": smart([17, 16, 17, 18, 15]), "progress": 55.0, "attainment": None,
         "status": "executing", "is_archived": False,
         "published_snapshot": {"published_at": dtime_iso(-12, 10),
                                "criteria": ["拆解树支持 3 层结构与上下追溯", "SMART 五维质检拦截率 100%",
                                             "AI 草案采纳流程可演示"],
                                "krs": [{"kr_id": "KR-004", "target_value": 100}]}},
        {"objective_id": "O-003", "title": "完成部门驾驶舱与数据看板", "source": "上级拆解",
         "parent_id": "O-001", "level": "team", "org_unit_id": "ORG-102", "owner_id": "U005",
         "period_id": "P-2026Q4", "weight": 25, "plan_start": d(7), "plan_end": d(20),
         "success_criteria": ["驾驶舱 8 张 KPI 卡与三视图全部可用", "统计口径与任务 / 目标分开展示"],
         "smart": smart([17, 16, 17, 17, 15]), "progress": 41.0, "attainment": None,
         "status": "executing", "is_archived": False,
         "published_snapshot": {"published_at": dtime_iso(-12, 10),
                                "criteria": ["驾驶舱 8 张 KPI 卡与三视图全部可用", "统计口径与任务 / 目标分开展示"],
                                "krs": [{"kr_id": "KR-006", "target_value": 8}]}},
        {"objective_id": "O-004", "title": "客户满意度提升专项", "source": "上级拆解",
         "parent_id": "O-001", "level": "team", "org_unit_id": "ORG-101", "owner_id": "U008",
         "period_id": "P-2026Q4", "weight": 20, "plan_start": rel(1), "plan_end": d(70),
         "success_criteria": ["客户投诉数降至每月 3 件以内", "重点客户完成 2 轮回访"],
         "smart": smart([16, 17, 15, 16, 14]), "progress": 0.0, "attainment": None,
         "status": "published", "is_archived": False,
         "published_snapshot": {"published_at": dtime_iso(-1, 11),
                                "criteria": ["客户投诉数降至每月 3 件以内", "重点客户完成 2 轮回访"],
                                "krs": [{"kr_id": "KR-007", "target_value": 3}]}},
        {"objective_id": "O-005", "title": "目标拆解模块的移动端适配", "source": "上级拆解",
         "parent_id": "O-002", "level": "team", "org_unit_id": "ORG-101", "owner_id": "U007",
         "period_id": "P-2026Q4", "weight": 15, "plan_start": d(2), "plan_end": rel(-2),
         "success_criteria": ["三个断点无横向溢出", "手机端可完成拆解树浏览与承接确认", "真机验证记录留存"],
         "smart": smart([16, 15, 16, 17, 14]), "progress": 88.0, "attainment": None,
         "status": "pending_review", "is_archived": False,
         "published_snapshot": {"published_at": dtime_iso(-10, 10),
                                "criteria": ["三个断点无横向溢出", "手机端可完成拆解树浏览与承接确认",
                                             "真机验证记录留存"],
                                "krs": []}},
        {"objective_id": "O-006", "title": "沉淀数据治理规范 v1", "source": "员工自建",
         "parent_id": "O-001", "level": "team", "org_unit_id": "ORG-102", "owner_id": "U005",
         "period_id": "P-2026Q4", "weight": 10, "plan_start": rel(3), "plan_end": d(75),
         "success_criteria": ["输出规范文档并完成评审"],
         "smart": {"S": {"score": 15, "comment": "", "suggestion": ""}, "M": {"score": 9, "comment": "", "suggestion": ""},
                   "A": {"score": 16, "comment": "", "suggestion": ""}, "R": {"score": 10, "comment": "", "suggestion": ""},
                   "T": {"score": 9, "comment": "", "suggestion": ""}},
         "progress": 0.0, "attainment": None, "status": "draft", "is_archived": False, "published_snapshot": None},
        {"objective_id": "O-007", "title": "研发效能与质量基建（季度工作包）", "source": "部门重点工作",
         "parent_id": "", "level": "dept", "org_unit_id": "ORG-001", "owner_id": "U002",
         "period_id": "P-2026Q4", "weight": 20, "plan_start": d(0), "plan_end": d(80),
         "success_criteria": ["性能 P95 优化到 300ms 内", "技术债存量下降 50%"],
         "smart": smart([17, 16, 15, 17, 15]), "progress": 33.0, "attainment": None,
         "status": "executing", "is_archived": False,
         "published_snapshot": {"published_at": dtime_iso(-12, 10),
                                "criteria": ["性能 P95 优化到 300ms 内", "技术债存量下降 50%"],
                                "krs": [{"kr_id": "KR-008", "target_value": 300}]}},
        {"objective_id": "O-008", "title": "个人工作台与提醒中心上线", "source": "上级拆解",
         "parent_id": "O-001", "level": "team", "org_unit_id": "ORG-101", "owner_id": "U004",
         "period_id": "P-2026Q4", "weight": 5, "plan_start": d(1), "plan_end": d(20),
         "success_criteria": ["工作台五组待办聚合可用", "提醒中心支持已知悉抑制"],
         "smart": smart([17, 16, 17, 17, 15]), "progress": 0.0, "attainment": None,
         "status": "pending_confirm", "is_archived": False, "published_snapshot": None},
        {"objective_id": "O-101", "title": "市场部 Q4 品牌曝光增长 50%", "source": "部门重点工作", "parent_id": "", "level": "dept", "org_unit_id": "ORG-002", "owner_id": "U003",
         "period_id": "P-2026Q4", "weight": 60, "plan_start": d(0), "plan_end": d(80),
         "success_criteria": ["品牌曝光量达到 180 万", "完成 3 场行业活动"],
         "smart": smart([17, 17, 16, 17, 16]), "progress": 45.0, "attainment": None,
         "status": "executing", "is_archived": False,
         "published_snapshot": {"published_at": dtime_iso(-12, 10),
                                "criteria": ["品牌曝光量达到 180 万", "完成 3 场行业活动"],
                                "krs": [{"kr_id": "KR-101", "target_value": 180}]}},
        {"objective_id": "O-102", "title": "完成 3 场行业活动落地", "source": "上级拆解",
         "parent_id": "O-101", "level": "team", "org_unit_id": "ORG-002", "owner_id": "U009",
         "period_id": "P-2026Q4", "weight": 40, "plan_start": d(0), "plan_end": d(30),
         "success_criteria": ["3 场活动全部落地且报名达标", "活动复盘模板沉淀"],
         "smart": smart([16, 16, 17, 16, 15]), "progress": 100.0, "attainment": None,
         "status": "reviewed", "is_archived": False,
         "published_snapshot": {"published_at": dtime_iso(-12, 10),
                                "criteria": ["3 场活动全部落地且报名达标", "活动复盘模板沉淀"],
                                "krs": [{"kr_id": "KR-102", "target_value": 3}]}},
        {"objective_id": "O-103", "title": "Q3 平台 1.0 版本上线", "source": "部门重点工作",
         "parent_id": "", "level": "dept", "org_unit_id": "ORG-001", "owner_id": "U002",
         "period_id": "P-2026Q3", "weight": 50, "plan_start": rel(-105), "plan_end": rel(-20),
         "success_criteria": ["1.0 版本按期上线并通过验收"],
         "smart": smart([16, 16, 16, 16, 16]), "progress": 100.0, "attainment": 96.0,
         "status": "archived", "is_archived": True, "published_snapshot": None},
    ]

    store["key_result"] = [
        {"kr_id": "KR-001", "objective_id": "O-001", "kr_name": "核心接口 P95 响应时间", "metric_direction": "down",
         "baseline_value": 800, "target_value": 300, "current_value": 420, "unit": "ms", "weight": 40,
         "calc_strategy": "reverse"},
        {"kr_id": "KR-002", "objective_id": "O-001", "kr_name": "核心模块上线数", "metric_direction": "up",
         "baseline_value": 0, "target_value": 4, "current_value": 2, "unit": "个", "weight": 35,
         "calc_strategy": "milestone"},
        {"kr_id": "KR-003", "objective_id": "O-001", "kr_name": "验收评级", "metric_direction": "up",
         "baseline_value": "C", "target_value": "A", "current_value": "B", "unit": "", "weight": 25,
         "calc_strategy": "qualitative"},
        {"kr_id": "KR-004", "objective_id": "O-002", "kr_name": "SMART 校验点覆盖率", "metric_direction": "up",
         "baseline_value": 0, "target_value": 100, "current_value": 70, "unit": "%", "weight": 60,
         "calc_strategy": "forward"},
        {"kr_id": "KR-005", "objective_id": "O-002", "kr_name": "拆解树节点通过率", "metric_direction": "up",
         "baseline_value": 40, "target_value": 95, "current_value": 62, "unit": "%", "weight": 40,
         "calc_strategy": "forward"},
        {"kr_id": "KR-006", "objective_id": "O-003", "kr_name": "驾驶舱 KPI 卡片数", "metric_direction": "up",
         "baseline_value": 3, "target_value": 8, "current_value": 5, "unit": "个", "weight": 100,
         "calc_strategy": "milestone"},
        {"kr_id": "KR-007", "objective_id": "O-004", "kr_name": "月度客户投诉数", "metric_direction": "down",
         "baseline_value": 12, "target_value": 3, "current_value": 6, "unit": "件", "weight": 100,
         "calc_strategy": "reverse"},
        {"kr_id": "KR-008", "objective_id": "O-007", "kr_name": "接口 P95 响应时间", "metric_direction": "down",
         "baseline_value": 900, "target_value": 300, "current_value": 760, "unit": "ms", "weight": 100,
         "calc_strategy": "reverse"},
        {"kr_id": "KR-101", "objective_id": "O-101", "kr_name": "品牌曝光量", "metric_direction": "up",
         "baseline_value": 120, "target_value": 180, "current_value": 152, "unit": "万", "weight": 100,
         "calc_strategy": "forward"},
        {"kr_id": "KR-102", "objective_id": "O-102", "kr_name": "行业活动场次", "metric_direction": "up",
         "baseline_value": 0, "target_value": 3, "current_value": 3, "unit": "场", "weight": 100,
         "calc_strategy": "milestone"},
    ]

    # ------------------------------------------------------------------ 任务
    def task(tid, title, oid, kid, owner, hours, status, progress, start, end, source="assigned",
             assignees=None, parent="", detail="", blocked_reason="", manual=False):
        return {
            "task_id": tid, "title": title, "detail": detail, "parent_task_id": parent,
            "objective_id": oid, "kr_id": kid, "priority": "高" if status in ("blocked",) else "中",
            "plan_hours": hours, "plan_start": d(start), "plan_end": d(end),
            "progress": progress, "exec_rate": float(progress), "status": status, "source": source,
            "is_demo": False, "manual_override": manual, "blocked_reason": blocked_reason,
            "assignees": assignees or [{"user_id": owner, "role_in_task": "R", "weight": 100, "is_owner": True}],
            "created_at": dtime_iso(start - 1, 9), "updated_at": dtime_iso(-1, 17),
        }

    store["work_task"] = [
        task("T-001", "目标树数据模型与接口设计", "O-002", "KR-004", "U004", 16, "done", 100, 0, 1,
             detail="四层模型（目标-指标-任务-证据）落库与接口契约冻结。"),
        task("T-002", "拆解树前端页面（PC + 移动端）", "O-002", "KR-005", "U007", 24, "doing", 60, 2, 5,
             assignees=[{"user_id": "U007", "role_in_task": "R", "weight": 70, "is_owner": True},
                        {"user_id": "U004", "role_in_task": "C", "weight": 30, "is_owner": False}]),
        task("T-003", "核心校验引擎（SMART + 完整度 + 对齐度）", "O-002", "KR-004", "U004", 16, "doing", 70, 2, 6,
             detail="五维分开打分与 <12 分拦截；发布前完整度与对齐度检查。"),
        task("T-004", "AI 拆解建议接入（LLM + mock 降级）", "O-002", "KR-004", "U004", 12, "doing", 45, 7, 9),
        task("T-005", "驾驶舱 KPI 聚合接口", "O-003", "KR-006", "U005", 20, "doing", 50, 7, 9),
        task("T-006", "三视图 SVG 组件（环形 / 热图 / 日历）", "O-003", "KR-006", "U007", 20, "blocked", 0, 10, 14,
             blocked_reason="依赖驾驶舱 KPI 聚合接口（T-005）字段冻结"),
        task("T-007", "移动端适配与三断点验证", "O-005", "", "U007", 16, "doing", 80, 5, 8),
        task("T-008", "验收用例与证据整理", "O-005", "", "U004", 8, "todo", 0, 9, 11),
        task("T-009", "客户回访脚本与满意度问卷", "O-004", "KR-007", "U008", 12, "todo", 0, 11, 16),
        task("T-010", "P95 性能压测与优化", "O-007", "KR-008", "U006", 24, "doing", 35, 0, 5),
        task("T-011", "缺陷清理专项（P2 存量）", "O-007", "KR-008", "U006", 16, "todo", 0, 6, 7),
        task("T-012", "数据治理规范草案", "O-006", "", "U005", 8, "doing", 40, 3, 6, source="self"),
        task("T-013", "行业活动策划案与执行", "O-102", "KR-102", "U009", 16, "done", 100, 0, 2),
        task("T-014", "品牌曝光渠道投放排期", "O-101", "KR-101", "U010", 12, "doing", 60, 3, 6),
        task("T-016", "季度技术债与稳定性治理（工作包）", "O-007", "KR-008", "U006", 400, "doing", 22, 0, 80,
             detail="季度持续工作包：技术债清理、监控告警、稳定性演练。"),
        task("T-017", "设计系统与组件库重建（季度工作包）", "O-007", "", "U007", 300, "doing", 18, 0, 60,
             detail="季度持续工作包：设计令牌、SVG 组件、适配基线。"),
        task("T-018", "管理端页面全面适配改造", "O-007", "", "U007", 200, "todo", 0, 10, 55),
    ]
    store["task_assignee"] = [
        {"task_id": t["task_id"], "user_id": a["user_id"], "role_in_task": a["role_in_task"],
         "weight": a["weight"], "is_owner": a["is_owner"]}
        for t in store["work_task"] for a in t["assignees"]
    ]

    store["dependency"] = [
        {"dep_id": "D-001", "from_task_id": "T-001", "to_task_id": "T-002", "dep_type": "FS", "lag_days": 0},
        {"dep_id": "D-002", "from_task_id": "T-001", "to_task_id": "T-003", "dep_type": "FS", "lag_days": 0},
        {"dep_id": "D-003", "from_task_id": "T-003", "to_task_id": "T-004", "dep_type": "FS", "lag_days": 0},
        {"dep_id": "D-004", "from_task_id": "T-003", "to_task_id": "T-005", "dep_type": "FS", "lag_days": 0},
        {"dep_id": "D-005", "from_task_id": "T-005", "to_task_id": "T-006", "dep_type": "FS", "lag_days": 0},
        {"dep_id": "D-006", "from_task_id": "T-002", "to_task_id": "T-007", "dep_type": "FS", "lag_days": 0},
        {"dep_id": "D-007", "from_task_id": "T-007", "to_task_id": "T-008", "dep_type": "FS", "lag_days": 0},
        {"dep_id": "D-008", "from_task_id": "T-010", "to_task_id": "T-011", "dep_type": "FS", "lag_days": 0},
        {"dep_id": "D-009", "from_task_id": "T-013", "to_task_id": "T-014", "dep_type": "FS", "lag_days": 2},
    ]

    store["progress_log"] = [
        {"log_id": "LOG-0001", "target_type": "task", "target_id": "T-003", "progress": 70,
         "note": "评分规则与拦截逻辑完成，等待联调", "operator_id": "U004", "logged_at": dtime_iso(-1, 18), "source": "daily"},
        {"log_id": "LOG-0002", "target_type": "task", "target_id": "T-002", "progress": 60,
         "note": "拆解树主视图完成，移动端抽屉待联调", "operator_id": "U007", "logged_at": dtime_iso(-1, 18), "source": "daily"},
        {"log_id": "LOG-0003", "target_type": "task", "target_id": "T-005", "progress": 50,
         "note": "KPI 聚合完成一半，热图数据源待接", "operator_id": "U005", "logged_at": dtime_iso(-2, 18), "source": "daily"},
        {"log_id": "LOG-0004", "target_type": "task", "target_id": "T-010", "progress": 35,
         "note": "压测脚本完成，定位到 2 个慢查询", "operator_id": "U006", "logged_at": dtime_iso(-2, 18), "source": "manual"},
        {"log_id": "LOG-0005", "target_type": "objective", "target_id": "O-001", "progress": 58,
         "note": "月度复盘：整体按计划推进，性能专项滞后", "operator_id": "U002", "logged_at": dtime_iso(-2, 20), "source": "manual"},
        {"log_id": "LOG-0006", "target_type": "kr", "target_id": "KR-001", "progress": 60,
         "note": "P95 降至 420ms", "operator_id": "U006", "logged_at": dtime_iso(-2, 20), "source": "manual"},
    ]

    store["evidence"] = [
        {"evidence_id": "EV-001", "target_type": "objective", "target_id": "O-005", "file_name": "三断点自查截图.zip",
         "file_path": "data/uploads/breakpoints.zip", "note": "375 / 768 / 1440 三档自查截图",
         "uploaded_by": "U007", "uploaded_at": dtime_iso(-2, 16),
         "ai_review_result": {"result": "基本齐备", "missing": ["真机验证记录"], "mismatch": [], "pending": ["验收用例执行记录"]}},
        {"evidence_id": "EV-002", "target_type": "objective", "target_id": "O-005", "file_name": "移动端适配说明.md",
         "file_path": "data/uploads/mobile_note.md", "note": "适配方案与已知问题",
         "uploaded_by": "U007", "uploaded_at": dtime_iso(-2, 16), "ai_review_result": None},
        {"evidence_id": "EV-003", "target_type": "task", "target_id": "T-013", "file_name": "活动复盘报告.pdf",
         "file_path": "data/uploads/event_review.pdf", "note": "3 场活动数据与复盘",
         "uploaded_by": "U009", "uploaded_at": dtime_iso(-3, 15),
         "ai_review_result": {"result": "齐备", "missing": [], "mismatch": [], "pending": []}},
        {"evidence_id": "EV-004", "target_type": "kr", "target_id": "KR-001", "file_name": "压测报告 v2.pdf",
         "file_path": "data/uploads/perf_v2.pdf", "note": "P95 = 420ms 的证据",
         "uploaded_by": "U006", "uploaded_at": dtime_iso(-2, 17), "ai_review_result": None},
    ]

    # ------------------------------------------------------------------ 日计划
    store["daily_plan"] = [
        {"plan_id": "DP-001", "user_id": "U004", "plan_date": rel(-2), "day_status": "approved",
         "day_progress": 65.0, "streak_abnormal": 0, "reviewer_id": "U002", "reviewed_at": dtime_iso(-3, 20),
         "reject_reason": "", "submitted_at": dtime_iso(-3, 9), "ack_at": dtime_iso(-3, 21)},
        {"plan_id": "DP-002", "user_id": "U004", "plan_date": rel(-1), "day_status": "approved",
         "day_progress": 50.0, "streak_abnormal": 0, "reviewer_id": "U002", "reviewed_at": dtime_iso(-1, 21),
         "reject_reason": "", "submitted_at": dtime_iso(-2, 9), "ack_at": dtime_iso(-1, 22)},
        {"plan_id": "DP-003", "user_id": "U004", "plan_date": rel(0), "day_status": "submitted",
         "day_progress": 0.0, "streak_abnormal": 0, "reviewer_id": "", "reviewed_at": "",
         "reject_reason": "", "submitted_at": dtime_iso(0, 9), "ack_at": ""},
        {"plan_id": "DP-004", "user_id": "U007", "plan_date": rel(0), "day_status": "draft",
         "day_progress": 0.0, "streak_abnormal": 0, "reviewer_id": "", "reviewed_at": "",
         "reject_reason": "", "submitted_at": "", "ack_at": ""},
        {"plan_id": "DP-005", "user_id": "U005", "plan_date": rel(-1), "day_status": "rejected",
         "day_progress": 0.0, "streak_abnormal": 0, "reviewer_id": "U002", "reviewed_at": dtime_iso(-1, 19),
         "reject_reason": "工时与目标口径不一致，请拆分到具体任务后重新提交",
         "submitted_at": dtime_iso(-2, 10), "ack_at": ""},
        # U010（何佳）连续三日完成率未达 100%：演示 F-413 双角色提醒 / 升级判定
        {"plan_id": "DP-006", "user_id": "U010", "plan_date": rel(-3), "day_status": "approved",
         "day_progress": 62.0, "streak_abnormal": 0, "reviewer_id": "U003", "reviewed_at": dtime_iso(-3, 21),
         "reject_reason": "", "submitted_at": dtime_iso(-4, 9), "ack_at": dtime_iso(-3, 22)},
        {"plan_id": "DP-007", "user_id": "U010", "plan_date": rel(-2), "day_status": "approved",
         "day_progress": 70.0, "streak_abnormal": 0, "reviewer_id": "U003", "reviewed_at": dtime_iso(-2, 21),
         "reject_reason": "", "submitted_at": dtime_iso(-3, 9), "ack_at": ""},
        {"plan_id": "DP-008", "user_id": "U010", "plan_date": rel(-1), "day_status": "approved",
         "day_progress": 55.0, "streak_abnormal": 0, "reviewer_id": "U003", "reviewed_at": dtime_iso(-1, 21),
         "reject_reason": "", "submitted_at": dtime_iso(-2, 9), "ack_at": ""},
    ]
    store["daily_plan_item"] = [
        {"item_id": "DIT-001", "plan_id": "DP-001", "task_id": "T-002", "plan_hours": 4,
         "plan_desc": "拆解树主视图联调", "done": True, "progress": 60, "sort_no": 1, "abnormal_flag": ""},
        {"item_id": "DIT-002", "plan_id": "DP-001", "task_id": "T-003", "plan_hours": 4,
         "plan_desc": "评分规则编码与自测", "done": True, "progress": 70, "sort_no": 2, "abnormal_flag": ""},
        {"item_id": "DIT-003", "plan_id": "DP-002", "task_id": "T-002", "plan_hours": 4,
         "plan_desc": "树节点交互与抽屉", "done": True, "progress": 60, "sort_no": 1, "abnormal_flag": ""},
        {"item_id": "DIT-004", "plan_id": "DP-002", "task_id": "T-004", "plan_hours": 4,
         "plan_desc": "LLM 提示词与 mock 兜底", "done": True, "progress": 45, "sort_no": 2, "abnormal_flag": ""},
        {"item_id": "DIT-005", "plan_id": "DP-002", "task_id": "T-012", "plan_hours": 2,
         "plan_desc": "规范目录结构梳理", "done": True, "progress": 40, "sort_no": 3, "abnormal_flag": ""},
        {"item_id": "DIT-006", "plan_id": "DP-003", "task_id": "T-002", "plan_hours": 3,
         "plan_desc": "拆解树移动端交互联调", "done": False, "progress": 0, "sort_no": 1, "abnormal_flag": ""},
        {"item_id": "DIT-007", "plan_id": "DP-003", "task_id": "T-004", "plan_hours": 3,
         "plan_desc": "LLM 真实接口联调与降级验证", "done": False, "progress": 0, "sort_no": 2, "abnormal_flag": ""},
        {"item_id": "DIT-008", "plan_id": "DP-003", "task_id": "T-003", "plan_hours": 2,
         "plan_desc": "完善五维评分细节规则", "done": False, "progress": 0, "sort_no": 3, "abnormal_flag": ""},
        {"item_id": "DIT-009", "plan_id": "DP-004", "task_id": "T-002", "plan_hours": 2,
         "plan_desc": "拆解树字号与点击区适配", "done": False, "progress": 0, "sort_no": 1, "abnormal_flag": ""},
        {"item_id": "DIT-010", "plan_id": "DP-004", "task_id": "T-017", "plan_hours": 6,
         "plan_desc": "设计令牌与组件盘点", "done": False, "progress": 0, "sort_no": 2, "abnormal_flag": ""},
        {"item_id": "DIT-011", "plan_id": "DP-005", "task_id": "T-005", "plan_hours": 6,
         "plan_desc": "KPI 聚合接口", "done": False, "progress": 0, "sort_no": 1, "abnormal_flag": ""},
        {"item_id": "DIT-012", "plan_id": "DP-006", "task_id": "T-014", "plan_hours": 5,
         "plan_desc": "渠道排期表初稿", "done": False, "progress": 62, "sort_no": 1, "abnormal_flag": ""},
        {"item_id": "DIT-013", "plan_id": "DP-007", "task_id": "T-014", "plan_hours": 5,
         "plan_desc": "投放素材对接与排期确认", "done": False, "progress": 70, "sort_no": 1, "abnormal_flag": ""},
        {"item_id": "DIT-014", "plan_id": "DP-008", "task_id": "T-014", "plan_hours": 5,
         "plan_desc": "渠道数据回收模板", "done": False, "progress": 55, "sort_no": 1, "abnormal_flag": ""},
    ]

    # ------------------------------------------------------------------ 变更 / 验收 / 复盘
    store["change_request"] = [
        {"change_id": "CR-001", "objective_id": "O-005", "change_type": "延期",
         "reason_code": "需求变更", "reason_text": "移动端适配新增真机验证要求，需要额外 4 个工作日。",
         "before_snapshot": {"plan_end": rel(-2)}, "after_snapshot": {"plan_end": rel(2)},
         "impact_note": "影响验收时间 4 天，不影响上级目标关键路径。", "applicant_id": "U007",
         "approver_id": "U002", "status": "pending", "applied_at": dtime_iso(-1, 10), "approved_at": ""},
        {"change_id": "CR-002", "objective_id": "O-001", "change_type": "指标调整",
         "reason_code": "优先级调整", "reason_text": "P95 目标由 300ms 放宽至 350ms，与基础设施升级计划对齐。",
         "before_snapshot": {"krs": [{"kr_id": "KR-001", "target_value": 300}]},
         "after_snapshot": {"krs": [{"kr_id": "KR-001", "target_value": 350}]},
         "impact_note": "达成度口径同步调整，基线快照留档。", "applicant_id": "U002",
         "approver_id": "U001", "status": "approved", "applied_at": dtime_iso(-6, 9), "approved_at": dtime_iso(-5, 11)},
        {"change_id": "CR-003", "objective_id": "O-003", "change_type": "范围调整",
         "reason_code": "资源冲突", "reason_text": "申请去掉热图视图，集中资源保障 KPI 卡片。",
         "before_snapshot": {"criteria": ["驾驶舱 8 张 KPI 卡与三视图全部可用"]},
         "after_snapshot": {"criteria": ["驾驶舱 8 张 KPI 卡与环形、日历视图可用"]},
         "impact_note": "减少 1 个视图开发量。", "applicant_id": "U005", "approver_id": "U002",
         "status": "rejected", "applied_at": dtime_iso(-4, 15), "approved_at": dtime_iso(-3, 10)},
    ]

    store["assessment"] = [
        {"assess_id": "AS-001", "objective_id": "O-005", "period_id": "P-2026Q4",
         "self_score": 90, "self_note": "三个断点自查通过，真机验证记录待补。",
         "evidence_ids": ["EV-001", "EV-002"],
         "ai_precheck": {"result": "基本齐备", "missing": ["真机验证记录"], "mismatch": [],
                         "pending": ["验收用例执行记录"], "suggestion": "建议补充一台 iOS 与一台 Android 真机记录后再提交验收。"},
         "final_score": None, "grade": "", "conclusion": "", "assessor_id": "U002",
         "status": "pending_manager", "appealed": False},
        {"assess_id": "AS-002", "objective_id": "O-102", "period_id": "P-2026Q4",
         "self_score": 88, "self_note": "3 场活动均落地，复盘模板已沉淀。", "evidence_ids": ["EV-003"],
         "ai_precheck": {"result": "齐备", "missing": [], "mismatch": [], "pending": [], "suggestion": "证据链完整。"},
         "final_score": 85, "grade": "B", "conclusion": "达成", "assessor_id": "U003",
         "status": "done", "appealed": False},
        {"assess_id": "AS-003", "objective_id": "O-103", "period_id": "P-2026Q3",
         "self_score": 96, "self_note": "按期上线。", "evidence_ids": [],
         "ai_precheck": None, "final_score": 96, "grade": "A", "conclusion": "达成",
         "assessor_id": "U001", "status": "done", "appealed": False},
    ]

    store["retrospective"] = [
        {"retro_id": "RT-001", "objective_id": "O-102", "period_id": "P-2026Q4",
         "review_note": "3 场活动平均报名率 118%，复盘机制跑通。",
         "result_compare": "目标 3 场，实际 3 场；曝光贡献 62 万，超出预期 12%。",
         "diff_reason": "渠道排期与活动主题匹配度高。",
         "improvements": [{"text": "建立活动效果复盘模板并沉淀到字典", "converted": False},
                          {"text": "下一周期提前锁定 2 个标杆客户联合活动", "converted": False}],
         "author_id": "U009", "created_at": dtime_iso(-3, 16), "converted_task_ids": []},
        {"retro_id": "RT-002", "objective_id": "O-103", "period_id": "P-2026Q3",
         "review_note": "1.0 上线按期交付，变更流程运转良好。",
         "result_compare": "达成度 96%，超基线。",
         "diff_reason": "需求冻结机制前置是关键。",
         "improvements": [{"text": "将需求冻结窗口写入流程规范", "converted": False}],
         "author_id": "U002", "created_at": dtime_iso(-18, 15), "converted_task_ids": []},
    ]

    # ------------------------------------------------------------------ 提醒 / 档案
    store["reminder"] = [
        {"reminder_id": "RM-001", "user_id": "U006", "target_type": "task", "target_id": "T-011",
         "alert_type": "overdue", "level": "red", "title": "任务已超期：缺陷清理专项",
         "body": "T-011 的计划结束已达 2 天，当前进度 0%，请更新或发起变更。",
         "status": "未读", "created_at": dtime_iso(-1, 6), "read_at": "", "ack_at": "",
         "trigger_id": "", "role": "", "dedupe_key": "alert:AL-overdue-T-011:U006"},
        {"reminder_id": "RM-002", "user_id": "U002", "target_type": "task", "target_id": "T-006",
         "alert_type": "deviation", "level": "red", "title": "进度异常：三视图 SVG 组件",
         "body": "T-006 阻塞 3 天，位于关键路径（T-001 → T-003 → T-005 → T-006）。",
         "status": "未读", "created_at": dtime_iso(-1, 6), "read_at": "", "ack_at": "",
         "trigger_id": "", "role": "", "dedupe_key": "alert:AL-deviation-T-006:U002"},
        {"reminder_id": "RM-003", "user_id": "U010", "target_type": "user", "target_id": "U010",
         "alert_type": "daily_streak", "level": "yellow", "title": "连续 3 天未完成当日计划",
         "body": "你已连续 3 天（%s ~ %s）当日完成率未达 100%%，最低 55%%。今天已无待办项，保持节奏即可。建议先处理关键路径上的任务；连续 ≥5 天将升级展示。" % (rel(-3), rel(-1)),
         "status": "未读", "created_at": dtime_iso(-1, 6), "read_at": "", "ack_at": "",
         "trigger_id": "streak:U010:%s" % rel(-3), "role": "staff",
         "dedupe_key": "streak:self:U010:%s:w" % rel(-3),
         "meta": {"days": 3, "window_start": rel(-3), "window_end": rel(-1), "min_progress": 55.0,
                  "subject_name": "何佳"}},
        {"reminder_id": "RM-004", "user_id": "U003", "target_type": "user", "target_id": "U010",
         "alert_type": "daily_streak", "level": "yellow", "title": "何佳 连续 3 天进度异常",
         "body": "何佳（市场专员）连续 3 天（%s ~ %s）当日完成率未达 100%%，最低 55%%。请关注并跟进，可发起 AI 过程指导或调整派工。" % (rel(-3), rel(-1)),
         "status": "未读", "created_at": dtime_iso(-1, 6), "read_at": "", "ack_at": "",
         "trigger_id": "streak:U010:%s" % rel(-3), "role": "manager",
         "dedupe_key": "streak:mgr:U003:U010:%s:w" % rel(-3),
         "meta": {"days": 3, "window_start": rel(-3), "window_end": rel(-1), "min_progress": 55.0,
                  "subject_name": "何佳", "critical_count": 0, "hotspot_title": "", "impact_days": 0}},
        {"reminder_id": "RM-005", "user_id": "U007", "target_type": "daily_plan", "target_id": "DP-004",
         "alert_type": "checkpoint", "level": "yellow", "title": "今日日计划尚未提交",
         "body": "距离今日结束不足 6 小时，请完成日计划提交。",
         "status": "已读", "created_at": dtime_iso(0, 12), "read_at": dtime_iso(0, 13), "ack_at": ""},
    ]

    store["staff_profile"] = [
        {"profile_id": "SP-001", "user_id": uid, "position_code": pos,
         "ability_tags": tags, "base_capacity_hours": 480, "current_load_hours": 0,
         "saturation": 0.0, "updated_at": dtime_iso(-1, 6)}
        for uid, pos, tags in (
            ("U004", "backend", ["Python", "Flask", "AI应用"]),
            ("U005", "data", ["SQL", "数据可视化", "Python"]),
            ("U006", "backend", ["Python", "性能优化", "监控"]),
            ("U007", "frontend", ["前端工程", "数据可视化", "SVG"]),
            ("U008", "product", ["项目管理", "客户沟通"]),
            ("U009", "market", ["活动策划", "渠道投放"]),
            ("U010", "market", ["渠道投放", "内容运营"]),
        )
    ]

    # ------------------------------------------------------------------ 字典 / 参数
    store["dict_item"] = []
    dict_defs = [
        ("position", [("backend", "后端工程师"), ("frontend", "前端工程师"), ("data", "数据工程师"),
                      ("product", "产品经理"), ("market", "市场专员"), ("manager", "部门负责人"),
                      ("super_admin", "系统管理员")]),
        ("ability", [("Python", "Python"), ("Flask", "Flask"), ("SQL", "SQL"), ("数据可视化", "数据可视化"),
                     ("AI应用", "AI 应用"), ("客户沟通", "客户沟通"), ("项目管理", "项目管理"),
                     ("性能优化", "性能优化"), ("监控", "监控"), ("前端工程", "前端工程"), ("SVG", "SVG"),
                     ("活动策划", "活动策划"), ("渠道投放", "渠道投放"), ("内容运营", "内容运营")]),
        ("reason", [("需求变更", "需求变更"), ("排期偏差", "排期偏差"), ("依赖阻塞", "依赖阻塞"),
                    ("资源冲突", "资源冲突"), ("优先级调整", "优先级调整")]),
        ("channel", [("站内", "站内"), ("企微", "企业微信"), ("钉钉", "钉钉"), ("邮件", "邮件")]),
        ("grade", [("A", "A（100 分档）"), ("B", "B（85 分档）"), ("C", "C（70 分档）"), ("D", "D（50 分档）")]),
    ]
    index = 1
    for dict_type, items in dict_defs:
        for sort_no, (code, name) in enumerate(items, start=1):
            store["dict_item"].append({
                "dict_id": "DICT-%03d" % index, "dict_type": dict_type, "dict_code": code,
                "dict_name": name, "sort_no": sort_no, "status": "启用",
            })
            index += 1

    store["setting"] = [
        {"setting_key": "smart_weights", "setting_value": {"S": 20, "M": 20, "A": 20, "R": 20, "T": 20},
         "value_type": "json", "group_name": "smart", "updated_by": "U001", "updated_at": dtime_iso(-29)},
        {"setting_key": "smart_min_score", "setting_value": 12, "value_type": "number",
         "group_name": "smart", "updated_by": "U001", "updated_at": dtime_iso(-29)},
        {"setting_key": "warn_thresholds", "setting_value": {"green": 90, "yellow": 110},
         "value_type": "json", "group_name": "assign", "updated_by": "U001", "updated_at": dtime_iso(-29)},
        {"setting_key": "saturation_limit", "setting_value": 90, "value_type": "number",
         "group_name": "assign", "updated_by": "U001", "updated_at": dtime_iso(-29)},
        {"setting_key": "streak_days", "setting_value": 3, "value_type": "number",
         "group_name": "warning", "updated_by": "U001", "updated_at": dtime_iso(-29)},
        {"setting_key": "checkpoint_days", "setting_value": [7, 3, 1], "value_type": "json",
         "group_name": "warning", "updated_by": "U001", "updated_at": dtime_iso(-29)},
        {"setting_key": "due_soon_days", "setting_value": 2, "value_type": "number",
         "group_name": "warning", "updated_by": "U001", "updated_at": dtime_iso(-29)},
        {"setting_key": "grade_map", "setting_value": {"A": 100, "B": 85, "C": 70, "D": 50},
         "value_type": "json", "group_name": "review", "updated_by": "U001", "updated_at": dtime_iso(-29)},
        {"setting_key": "llm_timeout", "setting_value": 8, "value_type": "number",
         "group_name": "ai", "updated_by": "U001", "updated_at": dtime_iso(-29)},
    ]

    store["audit_log"] = [
        {"log_id": "LOG-1001", "operator_id": "U002", "operator_name": "李敏", "action": "发布目标",
         "target_type": "objective", "target_id": "O-001",
         "before": {"status": "confirmed"}, "after": {"status": "published"}, "ip": "127.0.0.1",
         "created_at": dtime_iso(-12, 10)},
        {"log_id": "LOG-1002", "operator_id": "U002", "operator_name": "李敏", "action": "强制下发（超载豁免）",
         "target_type": "work_task", "target_id": "T-018",
         "before": {"assignee": ""}, "after": {"assignee": "U007", "reason": "季度工作包整体规划，饱和度已备案"},
         "ip": "127.0.0.1", "created_at": dtime_iso(-8, 11)},
        {"log_id": "LOG-1003", "operator_id": "U001", "operator_name": "陈北峰", "action": "角色授权",
         "target_type": "role", "target_id": "R002",
         "before": {"perm_count": 40}, "after": {"perm_count": 44}, "ip": "127.0.0.1",
         "created_at": dtime_iso(-28, 9)},
        {"log_id": "LOG-1004", "operator_id": "U001", "operator_name": "陈北峰", "action": "配置变更",
         "target_type": "setting", "target_id": "saturation_limit",
         "before": {"value": 100}, "after": {"value": 90}, "ip": "127.0.0.1",
         "created_at": dtime_iso(-29, 10)},
        {"log_id": "LOG-1005", "operator_id": "U002", "operator_name": "李敏", "action": "结果归档",
         "target_type": "objective", "target_id": "O-103",
         "before": {"status": "reviewed"}, "after": {"status": "archived"}, "ip": "127.0.0.1",
         "created_at": dtime_iso(-18, 16)},
    ]

    return store


if __name__ == "__main__":
    write_json(STORE_FILE, build_seed())
    print("演示数据已重建：%s" % STORE_FILE)
