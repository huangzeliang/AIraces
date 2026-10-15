"""生成演示数据：data/plans.json 与 data/settings.json。

C 线（看板 / 数据 / 体验）自用。重复执行会重置为一份"时间相对当下"的样例数据，
保证驾驶舱在任何时候打开都有内容可看。

数据规模刻意做到「像真实在用」：9 个并行计划、79 个任务，时间跨度覆盖过去 50 天
到未来 30 天，状态 / 优先级枚举全部出现，并带有延期、依赖、提醒与异常数据。

用法：
    python backend/seed_demo.py
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

NOW = datetime.now().replace(microsecond=0)
random.seed(20261013)

# 状态 / 优先级枚举，严格取自软需说明书 1.3 枚举字典
STATUS_TODO = "待安排"
STATUS_READY = "待执行"
STATUS_DOING = "进行中"
STATUS_DONE = "已完成"
STATUS_DELAYED = "已延期"
STATUS_CANCELED = "已取消"

PRIORITY_HIGH = "高"
PRIORITY_MID = "中"
PRIORITY_LOW = "低"


def iso(moment):
    return moment.replace(microsecond=0).isoformat()


def day(offset=0, hour=18, minute=0):
    """相对今天的偏移天数 + 当天钟点，例如 day(-3, 10) = 三天前 10:00。"""
    moment = (NOW + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return moment


# ---------------------------------------------------------------------------
# 计划（plan）：用户用自然语言写下的一段目标
# ---------------------------------------------------------------------------
PLANS = [
    {
        "plan_id": "P20260921001",
        "user_id": "U001",
        "raw_text": "三季度生产运行复盘：把 7 到 9 月的生产事件整理清楚，归类原因、统计影响时长，"
                    "产出整改清单并落实跟进机制，月底开会同步。",
        "goal": "完成三季度生产运行复盘，输出整改清单并建立跟进机制",
        "created_at": iso(day(-52, 9, 12)),
    },
    {
        "plan_id": "P20260924002",
        "user_id": "U001",
        "raw_text": "核心系统 V3.2 投产准备：把变更清单、实施方案、回退预案都定稿，"
                    "组织投产评审，完成演练和业务验证，投产日安排值守。",
        "goal": "完成核心系统 V3.2 投产前的全部准备与验证",
        "created_at": iso(day(-30, 10, 5)),
    },
    {
        "plan_id": "P20260928003",
        "user_id": "U004",
        "raw_text": "团队知识库建设：先把开发环境搭建手册和常见报错处理整理出来，"
                    "再沉淀常见问题库，同步安排一次新人培训。",
        "goal": "搭建团队知识库并完成首轮新人培训",
        "created_at": iso(day(-28, 15, 40)),
    },
    {
        "plan_id": "P20260930004",
        "user_id": "U001",
        "raw_text": "监管报送数据核对：按最新报送口径逐项比对，完成数据校验与修正，"
                    "报送前做双人交叉复核后提交归档。",
        "goal": "按期完成监管报送数据核对与报送",
        "created_at": iso(day(-14, 9, 30)),
    },
    {
        "plan_id": "P20261008005",
        "user_id": "U004",
        "raw_text": "数据中台需求对接：收集各业务部门的取数需求，与中台团队对齐接口字段和频率，"
                    "输出一份需求说明书。",
        "goal": "完成数据中台下游取数需求的梳理与接口对齐",
        "created_at": iso(day(-11, 14, 20)),
    },
    {
        "plan_id": "P20261009006",
        "user_id": "U004",
        "raw_text": "准备一次技术分享，主题是 AI 在内网开发中的使用，先定大纲、收集行内案例，"
                    "再准备演示环境，最后试讲定稿。",
        "goal": "完成「AI 在内网的使用」技术分享从选题到定稿的全流程",
        "created_at": iso(day(-9, 11, 15)),
    },
    {
        "plan_id": "P20261010007",
        "user_id": "U001",
        "raw_text": "处理本周的线上问题：定位根因、完成修复和回归，走变更流程上线并观察一段时间后总结。",
        "goal": "完成本周线上问题的排查、修复与上线观察",
        "created_at": iso(day(-8, 16, 50)),
    },
    {
        "plan_id": "P20261012008",
        "user_id": "U002",
        "raw_text": "本月系统巡检和容量评估：执行一次完整巡检，汇总容量使用数据，输出评估报告并上报。",
        "goal": "完成本月系统巡检与容量评估报告",
        "created_at": iso(day(-5, 9, 5)),
    },
    {
        "plan_id": "P20261013009",
        "user_id": "U001",
        "raw_text": "团队周会与计划管理：每周把各条线的计划收上来，排出优先级，汇总上周完成情况后输出周报。",
        "goal": "形成每周固定的计划编排与周报输出节奏",
        "created_at": iso(day(-3, 8, 40)),
    },
]

# ---------------------------------------------------------------------------
# 待办（todo）：计划解析后的中间产物，尚未拆解为可执行任务
# 格式：(所属计划ID, 待办内容, 关键时间偏移天数)
# ---------------------------------------------------------------------------
TODO_SEEDS = [
    ("P20260921001", "汇总 7-9 月生产事件明细与原因分布", -40),
    ("P20260921001", "输出整改清单并落实跟进机制", -24),
    ("P20260921001", "组织季度复盘会并同步结论", -21),
    ("P20260924002", "完成投产方案与回退预案并定稿", -16),
    ("P20260924002", "组织投产前评审并闭环意见", -7),
    ("P20260924002", "完成测试环境准备与投产演练", -5),
    ("P20260924002", "完成投产业务验证与值守安排", 8),
    ("P20260928003", "编写开发环境搭建手册", -21),
    ("P20260928003", "沉淀常见问题库", 6),
    ("P20260928003", "组织新人培训并回收效果", 13),
    ("P20260930004", "核对监管报送口径", -8),
    ("P20260930004", "完成报送数据校验与修正", 0),
    ("P20260930004", "报送前双人复核并提交归档", 5),
    ("P20261008005", "梳理各部门取数需求", -8),
    ("P20261008005", "与中台团队对齐接口方案", 4),
    ("P20261008005", "输出需求说明书并评审", 10),
    ("P20261009006", "拟定分享大纲并收集案例", 0),
    ("P20261009006", "准备演示环境与示例", 9),
    ("P20261009006", "试讲并定稿分享材料", 16),
    ("P20261010007", "定位线上问题根因", -1),
    ("P20261010007", "完成修复与回归测试", 1),
    ("P20261010007", "走变更流程上线并观察", 6),
    ("P20261012008", "执行本月系统巡检并记录问题", 6),
    ("P20261012008", "完成容量评估并上报", 8),
    ("P20261013009", "编排本周任务优先级", 0),
    ("P20261013009", "汇总并输出团队周报", 1),
    ("P20260921001", "归档历史变更并清理遗留问题", -48),
]

# ---------------------------------------------------------------------------
# 任务（task）：待办拆解后的可执行单元
# 格式：(标题, 待办序号, 优先级, 预计耗时分钟, 截止偏移天数, 截止钟点, 状态, 进度, 延期次数)
# ---------------------------------------------------------------------------
TASK_BLUEPRINT = [
    # ── D1 汇总生产事件（P002 复盘）───────────────────────────────
    ("导出 7-9 月生产事件明细", 0, PRIORITY_MID, 60, -45, 17, STATUS_DONE, 100, 0),
    ("归类事件原因分布", 0, PRIORITY_MID, 90, -43, 17, STATUS_DONE, 100, 0),
    ("统计事件影响时长与等级", 0, PRIORITY_MID, 60, -42, 17, STATUS_DONE, 100, 0),
    # ── D2 整改清单 ───────────────────────────────────────────────
    ("归集重复发生的问题", 1, PRIORITY_HIGH, 120, -33, 17, STATUS_DONE, 100, 2),
    ("制定整改措施与责任人", 1, PRIORITY_HIGH, 150, -31, 17, STATUS_DONE, 100, 0),
    ("输出整改跟踪表", 1, PRIORITY_MID, 90, -29, 17, STATUS_DONE, 100, 0),
    # ── D3 复盘会 ────────────────────────────────────────────────
    ("准备复盘会议材料", 2, PRIORITY_MID, 120, -26, 17, STATUS_DONE, 100, 0),
    ("组织季度复盘会", 2, PRIORITY_HIGH, 90, -24, 15, STATUS_DONE, 100, 0),
    ("落实整改跟进机制", 2, PRIORITY_MID, 60, -20, 17, STATUS_DELAYED, 50, 3),
    # ── D4 投产方案（P001 投产）───────────────────────────────────
    ("梳理本次投产变更清单", 3, PRIORITY_MID, 90, -25, 17, STATUS_DONE, 100, 0),
    ("编写投产实施方案", 3, PRIORITY_HIGH, 180, -22, 17, STATUS_DONE, 100, 0),
    ("编写回退预案", 3, PRIORITY_HIGH, 120, -20, 17, STATUS_DONE, 100, 1),
    ("评审并定稿投产方案", 3, PRIORITY_HIGH, 90, -18, 16, STATUS_DONE, 100, 0),
    # ── D5 投产评审 ───────────────────────────────────────────────
    ("准备投产评审材料", 4, PRIORITY_MID, 120, -10, 17, STATUS_DONE, 100, 0),
    ("组织投产评审会", 4, PRIORITY_HIGH, 60, -9, 15, STATUS_DONE, 100, 0),
    ("跟踪评审意见整改", 4, PRIORITY_MID, 90, -2, 17, STATUS_DELAYED, 80, 1),
    # ── D6 环境与演练 ─────────────────────────────────────────────
    ("申请测试环境与数据库资源", 5, PRIORITY_MID, 45, -23, 17, STATUS_DONE, 100, 0),
    ("准备投产演练数据", 5, PRIORITY_MID, 150, -8, 17, STATUS_DONE, 100, 1),
    ("执行投产演练", 5, PRIORITY_HIGH, 480, -6, 18, STATUS_DONE, 100, 0),
    # ── D7 业务验证 ───────────────────────────────────────────────
    ("编写投产验证用例", 6, PRIORITY_HIGH, 180, 1, 17, STATUS_DOING, 40, 0),
    ("协调业务方参与验证", 6, PRIORITY_MID, 60, 3, 17, STATUS_READY, 0, 0),
    ("安排投产当日值守排班", 6, PRIORITY_MID, 45, 8, 17, STATUS_READY, 0, 0),
    ("投产结果确认与通报", 6, PRIORITY_HIGH, 60, 10, 17, STATUS_TODO, 0, 0),
    # ── D8 环境搭建手册（P005 知识库）─────────────────────────────
    ("整理开发环境安装步骤", 7, PRIORITY_LOW, 120, -28, 17, STATUS_DONE, 100, 0),
    ("补充常见报错处理", 7, PRIORITY_LOW, 90, -23, 17, STATUS_DONE, 100, 0),
    ("手册评审并发布到知识库", 7, PRIORITY_LOW, 60, -1, 16, STATUS_DONE, 100, 0),
    # ── D9 常见问题库 ─────────────────────────────────────────────
    ("收集高频问题清单", 8, PRIORITY_MID, 90, -6, 17, STATUS_DONE, 100, 0),
    ("编写问题解答条目", 8, PRIORITY_MID, 180, 4, 17, STATUS_DOING, 20, 0),
    ("分类整理并入库", 8, PRIORITY_LOW, 120, 9, 17, STATUS_TODO, 0, 0),
    # ── D10 新人培训 ──────────────────────────────────────────────
    ("制定新人培训计划", 9, PRIORITY_MID, 90, 0, 11, STATUS_DONE, 100, 0),
    ("准备培训课件", 9, PRIORITY_MID, 240, 6, 17, STATUS_READY, 0, 0),
    ("开展新人培训", 9, PRIORITY_MID, 180, 13, 14, STATUS_TODO, 0, 0),
    ("回收培训效果并改进", 9, PRIORITY_LOW, 60, 16, 17, STATUS_TODO, 0, 0),
    # ── D11 报送口径（P003 监管报送）──────────────────────────────
    ("收集最新报送口径说明", 10, PRIORITY_HIGH, 90, -11, 17, STATUS_DONE, 100, 0),
    ("比对口径变更点", 10, PRIORITY_HIGH, 120, -9, 17, STATUS_DONE, 100, 0),
    ("编写口径差异说明", 10, PRIORITY_MID, 90, -7, 17, STATUS_DONE, 100, 0),
    # ── D12 数据校验与修正 ────────────────────────────────────────
    ("抽取报送基础数据", 11, PRIORITY_HIGH, 120, -6, 17, STATUS_DONE, 100, 0),
    ("执行数据完整性校验", 11, PRIORITY_HIGH, 180, -4, 17, STATUS_DONE, 100, 1),
    ("修正校验发现的异常数据", 11, PRIORITY_HIGH, 150, 0, 17, STATUS_DOING, 60, 1),
    ("复核修正结果", 11, PRIORITY_HIGH, 90, 0, 20, STATUS_READY, 0, 0),
    # ── D13 双人复核与报送 ────────────────────────────────────────
    ("生成报送底稿", 12, PRIORITY_HIGH, 120, 2, 17, STATUS_READY, 0, 0),
    ("双人交叉复核", 12, PRIORITY_HIGH, 90, 4, 17, STATUS_TODO, 0, 0),
    ("提交报送并归档", 12, PRIORITY_HIGH, 60, 6, 17, STATUS_TODO, 0, 0),
    # ── D14 取数需求（P004 中台对接）──────────────────────────────
    ("收集各部门取数需求", 13, PRIORITY_MID, 120, -10, 17, STATUS_DONE, 100, 0),
    ("归纳共性取数场景", 13, PRIORITY_MID, 90, -8, 17, STATUS_DONE, 100, 0),
    # ── D15 接口对齐 ──────────────────────────────────────────────
    ("与中台团队第一次对齐", 14, PRIORITY_MID, 90, -3, 15, STATUS_DONE, 100, 0),
    ("确认接口字段与调用频率", 14, PRIORITY_MID, 120, 2, 17, STATUS_DOING, 30, 0),
    ("输出接口对接方案", 14, PRIORITY_MID, 150, 5, 17, STATUS_READY, 0, 0),
    # ── D16 需求说明书 ────────────────────────────────────────────
    ("撰写需求说明书初稿", 15, PRIORITY_MID, 300, 8, 17, STATUS_TODO, 0, 0),
    ("需求评审并定稿", 15, PRIORITY_MID, 120, 12, 17, STATUS_TODO, 0, 0),
    # ── D17 分享大纲（P006 技术分享）──────────────────────────────
    ("拟定分享主题与大纲", 16, PRIORITY_LOW, 60, -1, 11, STATUS_DONE, 100, 0),
    ("收集行内 AI 应用案例", 16, PRIORITY_LOW, 120, 1, 17, STATUS_READY, 0, 0),
    # ── D18 演示环境 ──────────────────────────────────────────────
    ("准备代码演示示例", 17, PRIORITY_LOW, 150, 7, 17, STATUS_TODO, 0, 0),
    ("录制操作演示视频", 17, PRIORITY_LOW, 120, 11, 17, STATUS_TODO, 0, 0),
    # ── D19 试讲定稿 ──────────────────────────────────────────────
    ("内部试讲", 18, PRIORITY_LOW, 60, 15, 16, STATUS_TODO, 0, 0),
    ("分享材料定稿", 18, PRIORITY_LOW, 60, 18, 17, STATUS_TODO, 0, 0),
    # ── D20 定位根因（P007 线上问题）─────────────────────────────
    ("复现线上问题", 19, PRIORITY_HIGH, 120, -3, 17, STATUS_DONE, 100, 0),
    ("分析日志与调用链路", 19, PRIORITY_HIGH, 210, -2, 17, STATUS_DONE, 100, 0),
    ("定位问题根因", 19, PRIORITY_HIGH, 120, -1, 17, STATUS_DONE, 100, 0),
    # ── D21 修复与回归 ────────────────────────────────────────────
    ("完成代码修复", 20, PRIORITY_HIGH, 150, 0, 15, STATUS_DONE, 100, 0),
    ("回归测试", 20, PRIORITY_HIGH, 120, 1, 17, STATUS_DOING, 40, 0),
    ("代码评审", 20, PRIORITY_MID, 60, 2, 17, STATUS_READY, 0, 0),
    # ── D22 变更上线 ──────────────────────────────────────────────
    ("提交变更申请单", 21, PRIORITY_MID, 45, 3, 17, STATUS_TODO, 0, 0),
    ("执行变更上线", 21, PRIORITY_HIGH, 90, 5, 22, STATUS_TODO, 0, 0),
    ("上线后观察与总结", 21, PRIORITY_MID, 60, 8, 17, STATUS_TODO, 0, 0),
    # ── D23 本月巡检（P008 巡检）─────────────────────────────────
    ("执行月度系统巡检", 22, PRIORITY_MID, 120, 0, 12, STATUS_DONE, 100, 0),
    ("输出巡检记录与问题清单", 22, PRIORITY_MID, 60, 0, 21, STATUS_DOING, 50, 0),
    ("跟进巡检发现问题", 22, PRIORITY_MID, 60, 7, 17, STATUS_TODO, 0, 0),
    # ── D24 容量评估 ──────────────────────────────────────────────
    ("汇总容量使用数据", 23, PRIORITY_MID, 120, -13, 17, STATUS_DONE, 100, 2),
    ("编写容量评估报告", 23, PRIORITY_MID, 180, 5, 17, STATUS_READY, 0, 0),
    ("评估结果上报", 23, PRIORITY_LOW, 60, 9, 17, STATUS_TODO, 0, 0),
    # ── D25 周计划编排（P009 周会）───────────────────────────────
    ("收集本周各线计划", 24, PRIORITY_LOW, 45, 0, 10, STATUS_DONE, 100, 0),
    ("编排本周任务优先级", 24, PRIORITY_LOW, 60, 0, 19, STATUS_READY, 0, 0),
    # ── D26 周报输出 ──────────────────────────────────────────────
    ("汇总上周完成情况", 25, PRIORITY_LOW, 60, -7, 17, STATUS_DONE, 100, 0),
    ("输出团队周报", 25, PRIORITY_LOW, 45, 1, 10, STATUS_READY, 0, 0),
    # ── D27 历史遗留（更早，收尾与取消）───────────────────────────
    ("编写旧系统迁移说明", 26, PRIORITY_MID, 180, -48, 17, STATUS_DONE, 100, 0),
    ("归档历史变更记录", 26, PRIORITY_LOW, 60, -47, 17, STATUS_DONE, 100, 0),
    ("补做上季度口径对齐", 26, PRIORITY_LOW, 60, -9, 17, STATUS_CANCELED, 0, 0),
    ("整理过渡期遗留问题", 26, PRIORITY_LOW, 90, -5, 17, STATUS_CANCELED, 0, 0),
]

# 依赖关系：仅保留几条有意义的（做依赖图演示用，1 起始的序号）
DEPENDENCIES = {
    6: [5],     # 输出整改跟踪表 ← 制定整改措施与责任人
    38: [37],   # 执行数据完整性校验 ← 抽取报送基础数据
    40: [39],   # 复核修正结果 ← 修正校验发现的异常数据
    41: [39],   # 生成报送底稿 ← 修正校验发现的异常数据
    61: [60],   # 回归测试 ← 完成代码修复
    62: [60],   # 代码评审 ← 完成代码修复
    65: [64],   # 上线后观察与总结 ← 执行变更上线
}

# 延期原因（配对给 delay_count > 0 的任务）
DELAY_REASONS = {
    4: "上游数据口径未对齐，等待确认后重新统计",
    9: "责任人出差，跟进机制推迟到本周落实",
    12: "回退方案评审时补充了两项边界场景，改动量增加",
    16: "评审意见涉及跨系统接口，需要中台团队配合确认",
    18: "演练环境资源被占用，顺延到次日上午执行",
    38: "数据源新增了两张表，校验规则需要同步调整",
    39: "校验发现的异常集中在历史数据，修正耗时超出预期",
    69: "容量数据来自多个监控平台，口径统一花了额外时间",
}

# ---------------------------------------------------------------------------
# 提醒（reminder）
# 格式：(任务序号, 触发偏移天数, 触发钟点, 渠道, 状态, 内容)
# ---------------------------------------------------------------------------
REMINDER_SEEDS = [
    (38, 0, 9, "页面弹窗", "未读", "任务「修正校验发现的异常数据」将于今日 17:00 到期，当前进度 60%，请确认能否按时完成"),
    (39, 0, 10, "页面弹窗", "未读", "任务「复核修正结果」将于今日 20:00 到期，尚未开始"),
    (20, 0, 8, "页面弹窗", "未读", "任务「编写投产验证用例」已进入进行中，距截止还有 1 天"),
    (16, -1, 9, "桌面通知", "已读", "任务「跟踪评审意见整改」已延期 1 天，建议今日优先处理"),
    (61, 0, 11, "页面弹窗", "未读", "任务「回归测试」计划明日开始，请预留时间"),
    (67, 0, 14, "桌面通知", "未读", "任务「输出巡检记录与问题清单」将于今晚 21:00 到期"),
    (9, -2, 10, "企业微信", "已读", "任务「落实整改跟进机制」已延期 3 次，建议重新评估排期"),
    (41, 0, 15, "页面弹窗", "未读", "任务「生成报送底稿」将在 2 天后开始，属于监管报送关键路径"),
    (73, 0, 16, "页面弹窗", "未读", "任务「编排本周任务优先级」将于今晚 19:00 到期"),
    (28, -1, 17, "钉钉", "已忽略", "任务「编写问题解答条目」为知识库建设重点，建议安排整块时间"),
    (31, -3, 9, "桌面通知", "已读", "任务「准备培训课件」将在 6 天后开始"),
    (38, -1, 18, "页面弹窗", "已读", "任务「执行数据完整性校验」已逾期完成，请补充延期说明"),
    (47, 0, 13, "页面弹窗", "未读", "任务「确认接口字段与调用频率」当前进度 30%，距截止还有 2 天"),
    (49, -2, 11, "页面弹窗", "未读", "任务「撰写需求说明书初稿」预计耗时 300 分钟，建议拆分为子任务"),
    (21, 0, 12, "桌面通知", "未读", "任务「协调业务方参与验证」计划后天开始"),
    (70, 0, 17, "企业微信", "未读", "任务「编写容量评估报告」将在 5 天后开始"),
    (38, 0, 12, "桌面通知", "已读", "重复提醒：任务「修正校验发现的异常数据」仍未完成，已延期 1 次"),
    (64, -1, 16, "页面弹窗", "未读", "任务「执行变更上线」为高优先级，请提前确认变更窗口"),
    (22, -4, 15, "页面弹窗", "已读", "任务「安排投产当日值守排班」将在 8 天后开始"),
    (52, -1, 10, "桌面通知", "已忽略", "任务「收集行内 AI 应用案例」明日到期，尚未开始"),
]

# ---------------------------------------------------------------------------
# 反馈（feedback）：对任务执行结果或排期的记录
# 格式：(任务序号, 类型, 备注)
# ---------------------------------------------------------------------------
FEEDBACK_SEEDS = [
    (3, "完成", ""),
    (2, "完成", "原因分布按五大类收敛，重复问题占比最高"),
    (6, "完成", "整改跟踪表已同步到团队共享目录"),
    (5, "完成", "整改项逐条指定了责任人和完成时限"),
    (4, "延期", "上游数据口径未对齐，等待确认后重新统计"),
    (7, "完成", ""),
    (8, "完成", "复盘会产出 12 条整改项，已逐条指定责任人"),
    (9, "延期", "责任人出差，跟进机制推迟到本周落实"),
    (10, "完成", ""),
    (12, "延期", "回退方案评审时补充了两项边界场景，改动量增加"),
    (13, "完成", "投产方案已归档到变更管理平台"),
    (14, "完成", ""),
    (16, "延期", "评审意见涉及跨系统接口，需要中台团队配合确认"),
    (17, "完成", ""),
    (18, "延期", "演练环境资源被占用，顺延到次日上午执行"),
    (24, "完成", ""),
    (26, "完成", "手册第一版已发布，新人反馈步骤清晰"),
    (27, "完成", "高频问题按模块分类，共 43 条"),
    (34, "完成", ""),
    (35, "完成", "口径变更点写成了对照表，便于后续复用"),
    (36, "完成", ""),
    (38, "延期", "数据源新增了两张表，校验规则需要同步调整"),
    (44, "完成", "各业务部门共反馈 26 条取数需求"),
    (57, "完成", "问题在测试环境稳定复现，已录屏留档"),
    (69, "延期", "容量数据来自多个监控平台，口径统一花了额外时间"),
    (75, "完成", "上周完成情况已汇总，周报模板固定下来"),
]


def _jitter(moment, days=0, hours=0):
    return moment + timedelta(days=days, hours=hours)


def build_store():
    todos, tasks = [], []
    plan_owners = {plan["plan_id"]: plan["user_id"] for plan in PLANS}

    for index, (plan_id, content, offset) in enumerate(TODO_SEEDS, start=1):
        todos.append(
            {
                "todo_id": "D%03d" % index,
                "plan_id": plan_id,
                "content": content,
                "key_time": iso(day(offset, 18)),
            }
        )

    for index, seed in enumerate(TASK_BLUEPRINT, start=1):
        title, todo_index, priority, estimate, offset, hour, status, progress, delay = seed
        todo = todos[todo_index]
        task_id = "T%03d" % index
        due_at = day(offset, hour)

        if status == STATUS_TODO:
            # 尚未排期：没有计划开始与计划完成时间点
            scheduled_start = planned_finish = None
        else:
            scheduled_start = due_at - timedelta(minutes=estimate * 2)
            planned_finish = scheduled_start + timedelta(minutes=estimate)

        # 实际完成时间：按时完成略早于截止，延期完成晚于截止
        if status == STATUS_DONE:
            if delay:
                completed_at = due_at + timedelta(hours=6 * delay)
            else:
                completed_at = due_at - timedelta(hours=random.randint(1, 8))
        else:
            completed_at = None

        # 创建时间：截止前 1-6 天；若落在未来则回退到最近 12 天内
        created_at = due_at - timedelta(days=random.randint(1, 6), hours=random.randint(0, 20))
        if created_at > NOW:
            created_at = NOW - timedelta(days=random.randint(0, 12), hours=random.randint(1, 20))
        if completed_at and created_at > completed_at:
            created_at = completed_at - timedelta(days=random.randint(1, 4))

        tasks.append(
            {
                "task_id": task_id,
                "plan_id": todo["plan_id"],
                "todo_id": todo["todo_id"],
                "user_id": plan_owners.get(todo["plan_id"], "U001"),
                "parent_task_id": "",
                "title": title,
                "detail": "",
                "priority": priority,
                "estimate_minutes": estimate,
                "due_at": iso(due_at),
                "scheduled_start_at": iso(scheduled_start) if scheduled_start else "",
                "planned_finish_at": iso(planned_finish) if planned_finish else "",
                "status": status,
                "progress": progress,
                "depends_on": ["T%03d" % ref for ref in DEPENDENCIES.get(index, [])],
                "delay_count": delay,
                "delay_reason": DELAY_REASONS.get(index, ""),
                "completed_at": iso(completed_at) if completed_at else "",
                "is_demo": False,
                "created_at": iso(created_at),
                "updated_at": iso(_jitter(NOW, hours=-random.randint(1, 30))),
            }
        )

    reminders = [
        {
            "reminder_id": "R%03d" % index,
            "task_id": "T%03d" % task_index,
            "trigger_at": iso(day(offset, hour, random.randint(0, 59))),
            "channel": channel,
            "status": status,
            "content": content,
        }
        for index, (task_index, offset, hour, channel, status, content) in enumerate(REMINDER_SEEDS, start=1)
    ]

    feedbacks = [
        {
            "feedback_id": "F%03d" % index,
            "task_id": "T%03d" % task_index,
            "feedback_type": kind,
            "comment": comment,
            "created_at": iso(_jitter(NOW, days=-random.randint(1, 20), hours=-random.randint(1, 9))),
        }
        for index, (task_index, kind, comment) in enumerate(FEEDBACK_SEEDS, start=1)
    ]

    # 异常数据区：历次导入时未能通过校验的原始行（F-901 / F-902 / AC-18）
    exceptions = [
        {
            "exception_id": "E001",
            "source": "tasks_import_20261006.csv",
            "raw": {"title": "整理季度复盘材料", "priority": "紧急", "estimate_minutes": "90", "due_at": "2026-10-20 18:00", "detail": ""},
            "reason": "优先级取值非法：紧急（允许值：高 / 中 / 低）",
            "status": "待处置",
            "created_at": iso(day(-7, 9, 32)),
        },
        {
            "exception_id": "E002",
            "source": "tasks_import_20261006.csv",
            "raw": {"title": "同步客户回访结果", "priority": "中", "estimate_minutes": "60", "due_at": "", "detail": ""},
            "reason": "缺少或无法解析：截止时间（due_at）",
            "status": "待处置",
            "created_at": iso(day(-7, 9, 32)),
        },
        {
            "exception_id": "E003",
            "source": "tasks_import_20261006.csv",
            "raw": {"title": "上线前回归测试", "priority": "高", "estimate_minutes": "0", "due_at": "2026-10-18 18:00", "detail": ""},
            "reason": "预计耗时必须大于 0，实际为 0",
            "status": "待处置",
            "created_at": iso(day(-7, 9, 32)),
        },
        {
            "exception_id": "E004",
            "source": "tasks_import_20261009.csv",
            "raw": {"title": "梳理监管报送最新口径并同步相关业务部门后形成书面说明材料", "priority": "高", "estimate_minutes": "120", "due_at": "2026-10-16 18:00", "detail": ""},
            "reason": "任务标题超过 80 字符",
            "status": "待处置",
            "created_at": iso(day(-4, 14, 8)),
        },
        {
            "exception_id": "E005",
            "source": "tasks_import_20261009.csv",
            "raw": {"title": "核对报送数据差异", "priority": "高", "estimate_minutes": "九十", "due_at": "2026-10-15 18:00", "detail": ""},
            "reason": "预计耗时必须是整数，实际为「九十」",
            "status": "待处置",
            "created_at": iso(day(-4, 14, 8)),
        },
        {
            "exception_id": "E006",
            "source": "tasks_import_20261009.csv",
            "raw": {"title": "组织数据中台需求评审", "priority": "中", "estimate_minutes": "90", "due_at": "2026/13/45", "detail": ""},
            "reason": "缺少或无法解析：截止时间（due_at）",
            "status": "待处置",
            "created_at": iso(day(-4, 14, 8)),
        },
        {
            "exception_id": "E007",
            "source": "tasks_import_20261010.csv",
            "raw": {"title": "", "priority": "中", "estimate_minutes": "60", "due_at": "2026-10-17 18:00", "detail": "标题列为空"},
            "reason": "缺少必填字段：任务标题（title）",
            "status": "待处置",
            "created_at": iso(day(-3, 10, 45)),
        },
        {
            "exception_id": "E008",
            "source": "tasks_import_20261010.csv",
            "raw": {"title": "准备容量评估汇报材料", "priority": "最高", "estimate_minutes": "150", "due_at": "2026-10-22 18:00", "detail": ""},
            "reason": "优先级取值非法：最高（允许值：高 / 中 / 低）",
            "status": "已忽略",
            "created_at": iso(day(-3, 10, 45)),
        },
        {
            "exception_id": "E009",
            "source": "tasks_import_20261011.csv",
            "raw": {"title": "整理巡检问题清单", "priority": "中", "estimate_minutes": "", "due_at": "2026-10-19 18:00", "detail": ""},
            "reason": "缺少必填字段：预计耗时（estimate_minutes）",
            "status": "待处置",
            "created_at": iso(day(-2, 16, 20)),
        },
        {
            "exception_id": "E010",
            "source": "tasks_import_20261011.csv",
            "raw": {"title": "确认投产窗口并通知相关方", "priority": "高", "estimate_minutes": "45", "due_at": "2026-10-24 18:00", "detail": "已按高优先级重新录入"},
            "reason": "优先级取值非法：P0（允许值：高 / 中 / 低）",
            "status": "已处置",
            "created_at": iso(day(-2, 16, 20)),
        },
    ]

    return {
        "plans": PLANS,
        "todos": todos,
        "tasks": tasks,
        "reminders": reminders,
        "feedbacks": feedbacks,
        "exceptions": exceptions,
    }


def build_settings():
    return {
        "theme": "day",
        "reminder_interval_minutes": 120,
        "dnd_start": "12:00",
        "dnd_end": "14:00",
        "channels": [
            {"channel_key": "page", "channel_name": "页面弹窗", "enabled": True, "webhook_url": ""},
            {"channel_key": "desktop", "channel_name": "桌面通知", "enabled": True, "webhook_url": ""},
            {"channel_key": "wecom", "channel_name": "企业微信", "enabled": False,
             "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=PLACEHOLDER"},
            {"channel_key": "dingtalk", "channel_name": "钉钉", "enabled": False, "webhook_url": ""},
        ],
    }


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    store = build_store()
    (DATA_DIR / "plans.json").write_text(
        json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (DATA_DIR / "settings.json").write_text(
        json.dumps(build_settings(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    tasks = store["tasks"]
    by_status = {}
    by_priority = {}
    for task in tasks:
        by_status[task["status"]] = by_status.get(task["status"], 0) + 1
        by_priority[task["priority"]] = by_priority.get(task["priority"], 0) + 1

    print("已生成 data/plans.json：")
    print("  计划 %d · 待办 %d · 任务 %d · 提醒 %d · 反馈 %d · 异常 %d"
          % (len(store["plans"]), len(store["todos"]), len(tasks),
             len(store["reminders"]), len(store["feedbacks"]), len(store["exceptions"])))
    print("  状态分布：", "、".join("%s %d" % (k, v) for k, v in sorted(by_status.items(), key=lambda x: -x[1])))
    print("  优先级分布：", "、".join("%s %d" % (k, v) for k, v in sorted(by_priority.items(), key=lambda x: -x[1])))
    print("已生成 data/settings.json")


if __name__ == "__main__":
    main()
