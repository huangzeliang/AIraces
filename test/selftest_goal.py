"""工作线A（目标与派工线）自测脚本 · 目标闭环 · 智能计划管理统一系统。

运行方式（项目根目录）：
    .venv/bin/python test/selftest_goal.py

覆盖范围（对应软需 AC-01~AC-09 与测试用例 TC-G1~G4 的 P0 断言）：
- A1 部门目标：必填 / 超长 / 重名 / 环检测 / 日期越周期 / 幂等 / 权限 1008 / AI 草案不落库 / 非草稿编辑 1004
- A2 SMART 校验：五维与总分 / 低维建议 / 采纳改写与留痕 / 提交门禁 1005 / 会签确认 / 非法状态 1004
- A3 拆解树与发布：员工视角范围 / 子目标未承接 1005 blocking / 权重合计 1005 weight_issues / 发布快照 / 档案卡五组
- A4 智能派工：任务创建校验 / 三色饱和度 / 权重与超载拦截 / 强制下发（理由 + 审计）/ 计划项窗口与工时 /
  AI 分析结构 / 普通用户越权 1008 / 主责单选
- E2E-01：录入 → 质检 → 采纳 → 提交确认 → 承接会签 → 发布 → 新建派工任务 → 试算 → 下发 全链路

隔离策略：脚本把 core.STORE_FILE 指向临时目录后启动 Flask 测试客户端，
目标闭环数据（system.json）在临时副本中反复读写；账号池 users.json 只读不写。
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backend.core as core  # noqa: E402

TEMP_DIR = Path(tempfile.mkdtemp(prefix="airaces-selftest-"))
core.STORE_FILE = TEMP_DIR / "system.json"

import app as appmod  # noqa: E402  在替换 STORE_FILE 之后导入，测试数据全部落在临时副本

ADMIN = "U002"        # 管理员（同账号池：limin）
STAFF = "U004"        # 普通用户（同账号池：zhangwei）
OTHER_STAFF = "U005"  # 普通用户（wangfang）
client = appmod.app.test_client()

CASES = []
CTX = {}


def case(cid, name):
    def decorator(fn):
        CASES.append((cid, name, fn))
        return fn
    return decorator


def expect(cond, message):
    if not cond:
        raise AssertionError(message)


def expect_code(body, code, label=""):
    expect(body.get("code") == code,
           "%s期望 code=%s，实际 code=%s（message=%s）" % (
               label + "：" if label else "", code, body.get("code"), body.get("message")))


def call(method, path, payload=None, idem=None, as_user=None):
    if as_user is not None:
        with client.session_transaction() as sess:
            sess.clear()
            sess["user_id"] = as_user
    headers = {}
    if idem:
        headers["Idempotency-Key"] = idem
    if method == "GET":
        resp = client.get(path, headers=headers)
    else:
        resp = client.post(path, json=payload or {}, headers=headers)
    try:
        body = resp.get_json()
    except Exception:
        body = None
    return resp.status_code, body or {}


def store():
    return core.read_store()


def find_obj(objective_id):
    return next((row for row in store()["objective"] if row["objective_id"] == objective_id), None)


def find_task(task_id):
    return next((row for row in store()["work_task"] if row["task_id"] == task_id), None)


def period_window():
    period = next(row for row in store()["period"] if row["period_id"] == "P-2026Q4")
    return period["start_date"], period["end_date"]


def date_plus(iso_date, days):
    from datetime import datetime, timedelta
    return (datetime.strptime(iso_date, "%Y-%m-%d") + timedelta(days=days)).date().isoformat()


def save_payload(**overrides):
    start, _ = period_window()
    payload = {
        "title": "完成自动化验收演练并输出报告",
        "source": "部门季度重点工作",
        "parent_id": "",
        "org_unit_id": "ORG-101",
        "owner_id": STAFF,
        "period_id": "P-2026Q4",
        "plan_start": date_plus(start, 10),
        "plan_end": date_plus(start, 40),
        "weight": 0,
        "success_criteria": ["2026-11-20 前完成 3 轮演练并输出验收报告"],
    }
    payload.update(overrides)
    return payload


def qualify_and_confirm(objective_id, owner_id=STAFF):
    """SMART 达标（循环采纳建议）→ 提交确认 → 责任人承接。"""
    for _ in range(4):
        _, check = call("POST", "/api/objective/smart-check",
                        {"objective_id": objective_id}, as_user=ADMIN)
        expect_code(check, 0, "质检")
        low = check["data"]["low_dims"]
        if not low:
            break
        _, adopt = call("POST", "/api/objective/smart-adopt",
                        {"objective_id": objective_id, "dims": low}, as_user=ADMIN)
        expect_code(adopt, 0, "采纳建议")
    _, submit = call("POST", "/api/objective/publish",
                     {"objective_id": objective_id, "action": "submit"}, as_user=ADMIN)
    expect_code(submit, 0, "提交确认")
    _, confirm = call("POST", "/api/objective/countersign",
                      {"objective_id": objective_id, "action": "confirm", "note": "承接确认（自测）"},
                      as_user=owner_id)
    expect_code(confirm, 0, "承接会签")


# ---------------------------------------------------------------------------
# A1 · 部门目标（F-101~107）
# ---------------------------------------------------------------------------

@case("TC-G1-01", "必填拦截：空标题保存被拒（1001）")
def tc_g1_01():
    _, body = call("POST", "/api/objective/save", save_payload(title=""), as_user=ADMIN)
    expect_code(body, 1001)
    return body["message"]


@case("TC-G1-02", "字段格式：标题超过 100 字被拒（1002）")
def tc_g1_02():
    _, body = call("POST", "/api/objective/save", save_payload(title="长" * 101), as_user=ADMIN)
    expect_code(body, 1002)
    return body["message"]


@case("TC-G1-03", "保存强目标草稿成功（draft + 自动编号）")
def tc_g1_03():
    _, body = call("POST", "/api/objective/save", save_payload(), idem="g1-strong", as_user=ADMIN)
    expect_code(body, 0)
    CTX["obj_ok"] = body["data"]["objective_id"]
    expect(find_obj(CTX["obj_ok"])["status"] == "draft", "新建目标应为草稿")
    return "objective_id=%s" % CTX["obj_ok"]


@case("TC-G1-04", "保存弱目标草稿（供门禁 / 采纳断言）")
def tc_g1_04():
    start, _ = period_window()
    payload = save_payload(
        title="数据对账", source="部门季度重点工作",
        plan_start=date_plus(start, 12), plan_end=date_plus(start, 45),
        success_criteria=["落实"],
    )
    _, body = call("POST", "/api/objective/save", payload, idem="g1-weak", as_user=ADMIN)
    expect_code(body, 0)
    CTX["obj_weak"] = body["data"]["objective_id"]
    return "objective_id=%s" % CTX["obj_weak"]


@case("TC-G1-05", "周期校验：计划日期越出周期被拒（1005）")
def tc_g1_05():
    start, _ = period_window()
    payload = save_payload(plan_start=date_plus(start, -3), plan_end=date_plus(start, 10))
    _, body = call("POST", "/api/objective/save", payload, as_user=ADMIN)
    expect_code(body, 1005)
    return body["message"][:40]


@case("TC-G1-06", "创建父子目标 + 环检测：父选自身后代被拒（1005 field=parent_id）")
def tc_g1_06():
    parent_payload = save_payload(title="季度回归演练总体目标")
    _, body = call("POST", "/api/objective/save", parent_payload, idem="g1-parent", as_user=ADMIN)
    expect_code(body, 0)
    CTX["obj_parent"] = body["data"]["objective_id"]
    child_payload = save_payload(
        title="回归演练第一子目标", parent_id=CTX["obj_parent"], weight=40.0)
    _, body = call("POST", "/api/objective/save", child_payload, idem="g1-c1", as_user=ADMIN)
    expect_code(body, 0)
    CTX["child1"] = body["data"]["objective_id"]
    child2_payload = save_payload(
        title="回归演练第二子目标", parent_id=CTX["obj_parent"], weight=40.0)
    _, body = call("POST", "/api/objective/save", child2_payload, idem="g1-c2", as_user=ADMIN)
    expect_code(body, 0)
    CTX["child2"] = body["data"]["objective_id"]
    # 把父节点挂到自己的子节点下 → 形成环
    cycle_payload = save_payload(
        title="季度回归演练总体目标", objective_id=CTX["obj_parent"], parent_id=CTX["child1"])
    _, body = call("POST", "/api/objective/save", cycle_payload, as_user=ADMIN)
    expect_code(body, 1005)
    expect(body.get("data", {}).get("field") == "parent_id", "环检测应返回 field=parent_id")
    return "环检测 data.field=%s" % body["data"]["field"]


@case("TC-G1-07", "同父重名拦截（1005）")
def tc_g1_07():
    dup = save_payload(title="回归演练第一子目标", parent_id=CTX["obj_parent"], weight=20.0)
    _, body = call("POST", "/api/objective/save", dup, as_user=ADMIN)
    expect_code(body, 1005)
    return body["message"][:36]


@case("TC-G1-08", "幂等：相同 Idempotency-Key 重放返回同一目标且不重复落库")
def tc_g1_08():
    before = len(store()["objective"])
    _, body = call("POST", "/api/objective/save", save_payload(), idem="g1-strong", as_user=ADMIN)
    expect_code(body, 0)
    expect(body["data"]["objective_id"] == CTX["obj_ok"], "重放应返回首次结果")
    expect(len(store()["objective"]) == before, "重放不应新增目标")
    return "objective_id=%s 未新增" % body["data"]["objective_id"]


@case("TC-G1-09", "权限：普通用户调用目标保存被拒（1008）")
def tc_g1_09():
    _, body = call("POST", "/api/objective/save", save_payload(title="越权目标"), as_user=STAFF)
    expect_code(body, 1008)
    return body["message"][:36]


@case("TC-G1-10", "AI 拆解草案不落库（objective 数量不变）")
def tc_g1_10():
    before = len(store()["objective"])
    _, body = call("POST", "/api/objective/ai-decompose",
                   {"objective_id": CTX["obj_ok"]}, as_user=ADMIN)
    expect_code(body, 0)
    children = body["data"].get("children") or []
    expect(len(children) >= 2, "草案应包含 2~4 个子目标，实际 %d" % len(children))
    expect(len(store()["objective"]) == before, "草案不得写入目标树")
    return "草案 %d 条，未落库" % len(children)


@case("TC-G1-11", "状态机：对已执行目标编辑被拒（1004 + 变更入口）")
def tc_g1_11():
    payload = save_payload(title="研发部 Q4 交付 AIraces 目标闭环平台并达成稳定性 SLA")
    payload["objective_id"] = "O-001"  # 种子中为 executing
    _, body = call("POST", "/api/objective/save", payload, as_user=ADMIN)
    expect_code(body, 1004)
    expect("/changes" in (body.get("data", {}).get("change_apply_url") or ""),
           "应回传变更申请入口")
    return body["message"][:44]


# ---------------------------------------------------------------------------
# A2 · SMART 校验（F-108~112）
# ---------------------------------------------------------------------------

@case("TC-G2-01", "五维质检：S/M/A/R/T 齐全且总分 = 五维之和")
def tc_g2_01():
    _, body = call("POST", "/api/objective/smart-check",
                   {"objective_id": CTX["obj_weak"]}, as_user=ADMIN)
    expect_code(body, 0)
    data = body["data"]
    expect(set(data["smart"].keys()) == {"S", "M", "A", "R", "T"}, "五维键应齐全")
    total = sum(int(data["smart"][dim]["score"]) for dim in ("S", "M", "A", "R", "T"))
    expect(data["smart_total"] == total, "总分应为五维之和")
    expect(data["smart_meta"].get("engine") in ("llm", "rule"), "应记录质检引擎")
    return "总分=%d 引擎=%s" % (data["smart_total"], data["smart_meta"]["engine"])


@case("TC-G2-02", "低分维度：弱目标存在 <12 分维度且附带改写建议")
def tc_g2_02():
    _, body = call("POST", "/api/objective/smart-check",
                   {"objective_id": CTX["obj_weak"]}, as_user=ADMIN)
    data = body["data"]
    low = data["low_dims"]
    expect(len(low) >= 1, "弱目标应有低分维度")
    expect(any(data["smart"][dim]["suggestion"] for dim in low),
           "低分维度应附带改写建议")
    CTX["low_dims"] = low
    return "低分维度=%s" % "、".join(low)


@case("TC-G2-03", "提交门禁：未达标时提交确认被拒（1005 + 原因）")
def tc_g2_03():
    _, body = call("POST", "/api/objective/publish",
                   {"objective_id": CTX["obj_weak"], "action": "submit"}, as_user=ADMIN)
    expect_code(body, 1005)
    expect("SMART" in body["message"], "阻断原因应指向 SMART 未达标")
    return body["message"][:44]


@case("TC-G2-04", "一键采纳：改写后分数提升并留痕 progress_log")
def tc_g2_04():
    dims = CTX.get("low_dims") or ["S"]
    _, before = call("POST", "/api/objective/smart-check",
                     {"objective_id": CTX["obj_weak"]}, as_user=ADMIN)
    _, body = call("POST", "/api/objective/smart-adopt",
                   {"objective_id": CTX["obj_weak"], "dims": dims}, as_user=ADMIN)
    expect_code(body, 0)
    data = body["data"]
    improved = all(data["after"]["dims"][dim] >= data["before"]["dims"][dim] for dim in dims)
    expect(improved, "采纳后各维度分数不应下降")
    expect(data["after"]["total"] >= data["before"]["total"], "总分不应下降")
    logs = [row for row in store()["progress_log"]
            if row.get("target_id") == CTX["obj_weak"] and "采纳 SMART 建议" in (row.get("note") or "")]
    expect(logs, "应写入采纳建议的进度日志")
    return "总分 %d → %d，日志 %d 条" % (data["before"]["total"], data["after"]["total"], len(logs))


@case("TC-G2-05", "提交确认 → 非责任人承接被拒（1008）→ 责任人确认（confirmed）")
def tc_g2_05():
    for _ in range(4):
        _, check = call("POST", "/api/objective/smart-check",
                        {"objective_id": CTX["obj_weak"]}, as_user=ADMIN)
        low = check["data"]["low_dims"]
        if not low:
            break
        call("POST", "/api/objective/smart-adopt",
             {"objective_id": CTX["obj_weak"], "dims": low}, as_user=ADMIN)
    _, submit = call("POST", "/api/objective/publish",
                     {"objective_id": CTX["obj_weak"], "action": "submit"}, as_user=ADMIN)
    expect_code(submit, 0, "提交确认")
    expect(find_obj(CTX["obj_weak"])["status"] == "pending_confirm", "应为待确认")
    _, wrong = call("POST", "/api/objective/countersign",
                    {"objective_id": CTX["obj_weak"], "action": "confirm"}, as_user=OTHER_STAFF)
    expect_code(wrong, 1008, "非责任人确认")
    _, ok = call("POST", "/api/objective/countersign",
                 {"objective_id": CTX["obj_weak"], "action": "confirm", "note": "确认承接"},
                 as_user=STAFF)
    expect_code(ok, 0, "责任人确认")
    expect(find_obj(CTX["obj_weak"])["status"] == "confirmed", "应为已承接")
    return "pending_confirm → confirmed"


@case("TC-G2-06", "非法状态：对已执行目标补做质检被拒（1004）")
def tc_g2_06():
    _, body = call("POST", "/api/objective/smart-check",
                   {"objective_id": "O-001"}, as_user=ADMIN)
    expect_code(body, 1004)
    return body["message"][:44]


# ---------------------------------------------------------------------------
# A3 · 拆解树与发布（F-113~115）
# ---------------------------------------------------------------------------

@case("TC-G3-01", "数据范围：普通用户目标树 = 自己节点 + 上级上下文")
def tc_g3_01():
    _, body = call("GET", "/api/objective/tree", as_user=STAFF)
    expect_code(body, 0)
    nodes = body["data"]["nodes"]

    def walk(rows, bucket):
        for row in rows:
            bucket.append(row)
            walk(row.get("children") or [], bucket)

    flat = []
    walk(nodes, flat)
    expect(any(row["objective_id"] == CTX["obj_ok"] for row in flat), "应包含本人负责的目标")
    for row in flat:
        expect(row["owner_id"] == STAFF or row["flags"]["is_context"],
               "节点 %s 既非本人目标也非上级上下文" % row["objective_id"])
    return "节点 %d 个（仅本人 + 上下文）" % len(flat)


@case("TC-G3-02", "发布阻断：子目标未承接时发布被拒（1005 blocking）")
def tc_g3_02():
    qualify_and_confirm(CTX["obj_parent"])
    _, body = call("POST", "/api/objective/publish",
                   {"objective_id": CTX["obj_parent"], "action": "publish",
                    "publish_scope": "node"}, as_user=ADMIN)
    expect_code(body, 1005)
    blocking = body.get("data", {}).get("blocking") or []
    expect(len(blocking) == 2, "应有两名未承接子目标，实际 %d" % len(blocking))
    return "阻断子目标 %d 个" % len(blocking)


@case("TC-G3-03", "权重门：子目标权重合计 ≠100 发布被拒（1005 weight_issues）")
def tc_g3_03():
    qualify_and_confirm(CTX["child1"])
    qualify_and_confirm(CTX["child2"])
    _, body = call("POST", "/api/objective/publish",
                   {"objective_id": CTX["obj_parent"], "action": "publish",
                    "publish_scope": "node"}, as_user=ADMIN)
    expect_code(body, 1005)
    issues = body.get("data", {}).get("weight_issues") or []
    expect(issues and abs(issues[0]["sum"] - 80.0) < 0.1,
           "权重合计应为 80%%，实际 %s" % (issues[0]["sum"] if issues else "无"))
    return "权重合计 %.1f%%（≠100）" % issues[0]["sum"]


@case("TC-G3-04", "发布快照：发布成功且快照写入（成功标准 / 责任人 / 时限）")
def tc_g3_04():
    qualify_and_confirm(CTX["obj_ok"])
    _, body = call("POST", "/api/objective/publish",
                   {"objective_id": CTX["obj_ok"], "action": "publish",
                    "publish_scope": "node", "publish_note": "自测发布"}, as_user=ADMIN)
    expect_code(body, 0)
    target = find_obj(CTX["obj_ok"])
    expect(target["status"] == "published", "状态应为已发布")
    snapshot = target.get("published_snapshot") or {}
    expect(snapshot.get("owner_id") == STAFF, "快照应记录责任人")
    expect(snapshot.get("criteria"), "快照应记录成功标准")
    expect(snapshot.get("plan_end"), "快照应记录时限")
    expect(snapshot.get("published_by") == ADMIN, "快照应记录发布人")
    return "快照 criteria=%d 条，published_by=%s" % (len(snapshot["criteria"]), snapshot["published_by"])


@case("TC-G3-05", "发布后锁定：再次编辑已发布目标被拒（1004）")
def tc_g3_05():
    payload = save_payload(objective_id=CTX["obj_ok"])
    _, body = call("POST", "/api/objective/save", payload, as_user=ADMIN)
    expect_code(body, 1004)
    return body["message"][:40]


@case("TC-G3-06", "目标档案卡：五组聚合齐全（档案 / KR / 进度日志 / 变更 / 考评）")
def tc_g3_06():
    _, body = call("GET", "/api/objective/detail?objective_id=" + CTX["obj_ok"], as_user=ADMIN)
    expect_code(body, 0)
    data = body["data"]
    for key in ("objective", "krs", "logs", "changes", "assessments"):
        expect(key in data, "档案卡缺少分组 %s" % key)
    expect(data["objective"]["snapshot"], "发布后档案卡应带快照")
    return "五组齐全，日志 %d 条" % len(data["logs"])


# ---------------------------------------------------------------------------
# A4 · 智能派工（F-116~124）
# ---------------------------------------------------------------------------

@case("TC-G4-01", "任务创建：挂草稿目标被拒（1005）；挂已发布目标成功")
def tc_g4_01():
    payload = {
        "title": "自测派工任务", "detail": "覆盖饱和度试算与计划项",
        "objective_id": CTX["obj_weak"], "priority": "高",
        "plan_hours": 24, "plan_start": date_plus(period_window()[0], 12),
        "plan_end": date_plus(period_window()[0], 30),
    }
    _, body = call("POST", "/api/task/create", payload, as_user=ADMIN)
    expect_code(body, 1005, "挂未发布目标")
    payload["objective_id"] = CTX["obj_ok"]
    _, body = call("POST", "/api/task/create", payload, idem="g4-task", as_user=ADMIN)
    expect_code(body, 0)
    CTX["task"] = body["data"]["task_id"]
    return "task_id=%s" % CTX["task"]


@case("TC-G4-02", "任务创建：工时越界被拒（1002）")
def tc_g4_02():
    payload = {
        "title": "越界工时任务", "objective_id": CTX["obj_ok"],
        "plan_hours": 200, "plan_start": date_plus(period_window()[0], 12),
        "plan_end": date_plus(period_window()[0], 30),
    }
    _, body = call("POST", "/api/task/create", payload, as_user=ADMIN)
    expect_code(body, 1002)
    return body["message"][:36]


@case("TC-G4-03", "饱和度试算：三色阈值生效且推荐不超过 2 人")
def tc_g4_03():
    _, body = call("POST", "/api/assign/check",
                   {"task_id": CTX["task"], "assignees": [{"user_id": STAFF, "weight": 100}]},
                   as_user=ADMIN)
    expect_code(body, 0)
    data = body["data"]
    expect(data["thresholds"]["green"] == 90 and data["thresholds"]["red"] == 110, "阈值应为 90/110")
    levels = {row["user_id"]: row["level"] for row in data["members"]}
    expect("red" in levels.values(), "种子中 U007 应为红区（>110%%）")
    expect(levels.get(STAFF) == "green", "张伟加入后应处于绿区")
    expect(len(data["recommendations"]) <= 2, "推荐人选最多 2 人")
    return "红区成员 %d 人，推荐 %d 人" % (
        sum(1 for value in levels.values() if value == "red"), len(data["recommendations"]))


@case("TC-G4-04", "权重校验：合计 ≠100 提交被拒（1005 weight_sum）")
def tc_g4_04():
    payload = {"task_id": CTX["task"], "assignees": [
        {"user_id": STAFF, "weight": 60}, {"user_id": OTHER_STAFF, "weight": 30}]}
    _, body = call("POST", "/api/assign/commit", payload, as_user=ADMIN)
    expect_code(body, 1005)
    expect(abs(body["data"]["weight_sum"] - 90) < 0.1, "应回传合计 90%%")
    return "合计 %.1f%%" % body["data"]["weight_sum"]


@case("TC-G4-05", "超载拦截：红区成员提交被拒（1005 overload）")
def tc_g4_05():
    payload = {"task_id": CTX["task"], "assignees": [
        {"user_id": "U007", "weight": 100}]}  # 孙悦种子负荷 >110%
    _, body = call("POST", "/api/assign/commit", payload, as_user=ADMIN)
    expect_code(body, 1005)
    overload = body.get("data", {}).get("overload") or []
    expect(overload and overload[0]["saturation_after"] > 110,
           "应回传超载明细：%s" % overload)
    return "孙悦 %.1f%%" % overload[0]["saturation_after"]


@case("TC-G4-06", "强制下发：未填理由被拒（1001）")
def tc_g4_06():
    payload = {"task_id": CTX["task"], "assignees": [{"user_id": "U007", "weight": 100}],
               "override": True, "override_reason": ""}
    _, body = call("POST", "/api/assign/commit", payload, as_user=ADMIN)
    expect_code(body, 1001)
    return body["message"][:36]


@case("TC-G4-07", "强制下发：理由齐备 → 落库 + 超载标记 + 审计日志")
def tc_g4_07():
    payload = {"task_id": CTX["task"], "assignees": [{"user_id": "U007", "weight": 100}],
               "override": True, "override_reason": "演示强制下发：窗口期仅有孙悦熟悉该链路"}
    _, body = call("POST", "/api/assign/commit", payload, as_user=ADMIN)
    expect_code(body, 0)
    task = find_task(CTX["task"])
    expect(task["manual_override"] is True, "任务应标记 manual_override")
    expect(task.get("overload_marks"), "任务应带超载标记")
    audits = [row for row in store()["audit_log"]
              if row.get("action") == "assign.force_dispatch" and row.get("target_id") == CTX["task"]]
    expect(audits, "应写入强制下发审计日志")
    expect(audits[0]["operator_id"] == ADMIN, "审计应为管理员操作")
    return "manual_override=True 审计 %d 条" % len(audits)


@case("TC-G4-08", "权限：普通用户下达派工被拒（1008）")
def tc_g4_08():
    payload = {"task_id": CTX["task"], "assignees": [{"user_id": STAFF, "weight": 100}]}
    _, body = call("POST", "/api/assign/commit", payload, as_user=STAFF)
    expect_code(body, 1008)
    return body["message"][:44]


@case("TC-G4-09", "计划项：时间窗落在任务窗口内且工时合计 = 任务工时")
def tc_g4_09():
    _, body = call("POST", "/api/assign/plan", {
        "task_id": CTX["task"], "assignees": [
            {"user_id": STAFF, "weight": 60}, {"user_id": OTHER_STAFF, "weight": 40}]}, as_user=ADMIN)
    expect_code(body, 0)
    items = body["data"]["items"]
    task = find_task(CTX["task"])
    expect(len(items) == 2, "应生成 2 条计划项")
    for item in items:
        expect(task["plan_start"] <= item["start"] <= item["end"] <= task["plan_end"],
               "计划项窗口 %s~%s 越出任务窗口 %s~%s" % (
                   item["start"], item["end"], task["plan_start"], task["plan_end"]))
    total = round(sum(item["plan_hours"] for item in items), 1)
    expect(abs(total - task["plan_hours"]) < 0.1, "工时合计应等于任务工时")
    return "计划项 %d 条，合计 %sh" % (len(items), total)


@case("TC-G4-10", "AI 派工分析：关联度 / 风险结构完整（规则兜底）")
def tc_g4_10():
    _, body = call("POST", "/api/assign/analyze", {
        "task_id": CTX["task"], "assignees": [
            {"user_id": STAFF, "weight": 60}, {"user_id": OTHER_STAFF, "weight": 40}]}, as_user=ADMIN)
    expect_code(body, 0)
    data = body["data"]
    expect(data["engine"] in ("llm", "rule"), "引擎应为 llm 或 rule")
    expect(len(data["people"]) == 2, "应返回两名候选人的分析")
    for person in data["people"]:
        expect(0 <= person["relation_score"] <= 100, "关联度应在 0~100")
        expect(person["note"], "应给出分析理由")
    return "引擎=%s 关联度=%s" % (data["engine"], [p["relation_score"] for p in data["people"]])


@case("TC-G4-11", "越权与错误：不存在任务派工被拒（1003）；员工查他人档案被拒（1008）")
def tc_g4_11():
    payload = {"task_id": "T-XXX", "assignees": [{"user_id": STAFF, "weight": 100}]}
    _, body = call("POST", "/api/assign/commit", payload, as_user=ADMIN)
    expect_code(body, 1003)
    _, body = call("GET", "/api/staff/profile?user_id=" + OTHER_STAFF, as_user=STAFF)
    expect_code(body, 1008)
    return "不存在任务→1003，他人档案→1008"


@case("TC-G4-12", "多人并行派工：权重 / 主责单选 / 计划项落库一致")
def tc_g4_12():
    payload = {
        "task_id": CTX["task"],
        "assignees": [
            {"user_id": STAFF, "weight": 60, "is_owner": False},
            {"user_id": OTHER_STAFF, "weight": 40, "is_owner": True},
        ],
        "plan_items": [
            {"user_id": STAFF, "plan_hours": 14.4,
             "start": date_plus(period_window()[0], 12), "end": date_plus(period_window()[0], 16),
             "acceptance": "完成三轮回归并提交缺陷清单", "edited": True},
            {"user_id": OTHER_STAFF, "plan_hours": 9.6,
             "start": date_plus(period_window()[0], 17), "end": date_plus(period_window()[0], 20),
             "acceptance": "输出验收报告并通过评审", "edited": False},
        ],
        "override": False,
    }
    _, body = call("POST", "/api/assign/commit", payload, as_user=ADMIN)
    expect_code(body, 0)
    task = find_task(CTX["task"])
    owner = next(row for row in task["assignees"] if row["is_owner"])
    expect(owner["user_id"] == OTHER_STAFF, "主责应为显式指定的王芳（单选优先于权重）")
    rows = [row for row in store()["task_assignee"] if row["task_id"] == CTX["task"]]
    expect(sorted(row["user_id"] for row in rows) == sorted([STAFF, OTHER_STAFF]),
           "task_assignee 应双写两名执行人")
    edited = [item for item in task["plan_items"] if item["edited"]]
    expect(len(edited) == 1, "计划项应保留 edited 标记")
    expect(abs(sum(item["plan_hours"] for item in task["plan_items"]) - 24.0) < 0.1,
           "计划项工时合计应为任务工时")
    return "主责=%s，执行人 %d 名，计划项 %d 条" % (
        owner["user_id"], len(rows), len(task["plan_items"]))


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------

def main():
    print("工作线A 自测 · 临时数据副本：%s" % TEMP_DIR)
    print("-" * 78)
    passed, failed = 0, []
    for cid, name, fn in CASES:
        try:
            detail = fn() or ""
            print("PASS  %-10s %s  |  %s" % (cid, name, detail))
            passed += 1
        except Exception as exc:  # noqa: BLE001  自测脚本需要汇总全部失败
            print("FAIL  %-10s %s  |  %s" % (cid, name, exc))
            failed.append(cid)
    print("-" * 78)
    print("E2E-01 链路：录入 → SMART 质检 → 采纳 → 提交确认 → 承接会签 → 发布快照 → "
          "新建派工任务 → 试算 → 下发（含强制下发与审计）")
    print("总计：%d/%d 通过%s" % (passed, len(CASES), "" if not failed else "，失败：" + "、".join(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
    sys.exit(code)
