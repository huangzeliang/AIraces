"""D 线 · AI 任务中枢：把跨线信号收敛成一个入口，回答三个问题。

    「现在卡在哪一环」→ 环节定位（待排期 / 待启动 / 执行中 / 已延期 / 已收尾）
    「卡的到底是什么」→ 卡点清单（每条都有可核对的证据行）
    「该先动谁」      → 行动清单（按解冻收益排序）

数据来源不新增：只用 A 线计划/排期、B 线任务/提醒/巡检、C 线数据异常，
全部从 data/plans.json 聚合，不引入数据库、不写回任何文件。

归属文件：backend/hub.py · templates/pages/hub.html · css|js/pages/hub.*

接口契约：
- GET  /hub              中枢页面
- GET  /hub/analyze      研判结果 JSON（页面局部刷新 / 自测脚本）
- POST /hub/deep         深度研判（配置了大模型则调用，否则原样回退规则结论）

判定原则：全部规则可解释、可复算，给出结论的同时必须给出依据，
不做无法追溯的「黑箱打分」。
"""

from datetime import datetime, timedelta
from urllib.parse import urlencode

from flask import jsonify, render_template, request, url_for

# 兜底路径：让研判引擎在没有请求上下文时（自测脚本 / 后台巡检）也能算出可点击的链接
FALLBACK_PATHS = {"tasks": "/tasks", "schedule": "/schedule", "data_manage": "/data", "hub": "/hub"}


def _link(endpoint, **values):
    try:
        return url_for(endpoint, **values)
    except Exception:
        base = FALLBACK_PATHS.get(endpoint, "/")
        clean = {k: v for k, v in values.items() if v}
        return base + ("?" + urlencode(clean) if clean else "")

# 未完成 = 仍在流水线上的任务
OPEN_STATUSES = ("待安排", "待执行", "进行中", "已延期")
# 已关闭 = 不再产生卡点
CLOSED_STATUSES = ("已完成", "已取消")

# 环节定义：按任务状态把流水线切成五段，顺序即流转顺序
STAGE_DEFS = (
    ("待排期", ("待安排",), "已登记但未确定截止时间的任务"),
    ("待启动", ("待执行",), "已排定时间但尚未开工"),
    ("执行中", ("进行中",), "正在推进的任务"),
    ("已延期", ("已延期",), "已越过截止时间或主动延期，重点盯防"),
    ("已收尾", ("已完成",), "已完成归档"),
)

LEVEL_ORDER = {"critical": 0, "warning": 1, "info": 2}
LEVEL_LABEL = {"critical": "🔴 高风险", "warning": "🟡 需关注", "info": "🟢 可延后"}

# 阈值集中在这里，便于按团队节奏调整
OVERDUE_CRITICAL_DAYS = 3      # 逾期超过 3 天升级为高风险
STALL_DAYS = 3                 # 进行中超过 3 天没有更新视为停摆
LAG_WARN = 35                  # 实际进度落后时间进度 35 个点触发预警
LAG_CRITICAL = 65
# 临期红线：越接近截止时间，允许的进度越低 —— (剩余小时上限, 进度下限, 等级)
RISK_BANDS = ((6, 85, "critical"), (24, 70, "warning"), (48, 60, "warning"))
ORPHAN_DAYS = 3                # 悬空未排期超过 3 天提醒
DELAY_WARN = 2                 # 延期次数预警
DELAY_CRITICAL = 4
KEYSTONE_IMPACT = 2            # 下游依赖达到 2 项，自身即可视为关键前置

HEALTH_BANDS = ((85, "稳健", "good"), (70, "关注", "watch"), (50, "警戒", "alert"), (0, "危急", "critical"))


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def _now():
    return datetime.now()


def _parse(value):
    """宽松解析时间字段；无法解析时返回 None，绝不抛错打断聚合。"""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("/", "-"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _stamp(moment):
    return moment.strftime("%m-%d %H:%M")


def _human_gap(moment, now):
    """把时间差写成「还剩 3 天」/「已逾期 4 天」这类人话。"""
    if moment is None:
        return "未设置截止时间"
    delta = moment - now
    overdue = delta.total_seconds() < 0
    seconds = abs(delta.total_seconds())
    if seconds < 3600:
        amount, unit = max(1, int(seconds // 60)), "分钟"
    elif seconds < 86400:
        amount, unit = int(seconds // 3600), "小时"
    else:
        amount, unit = int(seconds // 86400), "天"
    return "已逾期 %d %s" % (amount, unit) if overdue else "还剩 %d %s" % (amount, unit)


def _downstream(tasks):
    """前置任务 id → 直接依赖它的任务 id 列表。"""
    dependents = {}
    for task in tasks:
        for dep in task.get("depends_on") or []:
            dependents.setdefault(dep, []).append(task.get("task_id"))
    return dependents


def _impact(task_id, dependents, seen=None):
    """一个任务被卡住后，会连带卡住多少个下游任务（传递闭包，去重计数）。"""
    seen = seen if seen is not None else set()
    total = 0
    for child in dependents.get(task_id, []):
        if child in seen:
            continue
        seen.add(child)
        total += 1 + _impact(child, dependents, seen)
    return total


def _cycle_ids(tasks):
    """找出处在依赖环上的任务 id。"""
    graph = {t.get("task_id"): [d for d in (t.get("depends_on") or [])] for t in tasks}
    state, cycle = {}, set()

    def walk(node, stack):
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt not in graph:
                continue
            if state.get(nxt) == 1:
                cycle.update(stack[stack.index(nxt):])
            elif nxt not in state:
                walk(nxt, stack)
        stack.pop()
        state[node] = 2

    for node in graph:
        if node not in state:
            walk(node, [])
    return cycle


def _conflict_clusters(open_tasks):
    """把互相重叠的排期时段合并成「冲突组」，一组只报一次，避免同一件事反复刷屏。

    做法：按开始时间排序后扫描并求并集，凡是时间轴上有交叠的任务归入同一组。
    """
    rows = []
    for task in open_tasks:
        start = _parse(task.get("scheduled_start_at"))
        finish = _parse(task.get("planned_finish_at"))
        if start and finish and finish > start:
            rows.append((task, start, finish))
    rows.sort(key=lambda row: row[1])

    clusters, current, current_end = [], [], None
    for task, start, finish in rows:
        if current and start < current_end:
            current.append(task)
            current_end = max(current_end, finish)
        else:
            if len(current) > 1:
                clusters.append(current)
            current, current_end = [task], finish
    if len(current) > 1:
        clusters.append(current)
    return clusters


def _chains(tasks, limit=4):
    """最长阻塞链：从卡点源出发，沿着依赖关系一路向下。"""
    by_id = {t.get("task_id"): t for t in tasks}
    dependents = _downstream(tasks)
    open_ids = {t.get("task_id") for t in tasks if t.get("status") in OPEN_STATUSES}

    def longest(node, seen):
        best = [node]
        for child in dependents.get(node, []):
            if child in seen or child not in open_ids:
                continue
            path = longest(child, seen | {child})
            if len(path) + 1 > len(best):
                best = [node] + path
        return best

    found = []
    for task in tasks:
        tid = task.get("task_id")
        if tid not in open_ids or not dependents.get(tid):
            continue
        path = longest(tid, {tid})
        if len(path) >= 2:
            found.append(path)

    found.sort(key=len, reverse=True)
    chains = []
    for path in found:
        if any(set(path) <= set(existing) for existing in chains):
            continue
        chains.append(path)
        if len(chains) >= limit:
            break

    def brief(tid):
        task = by_id.get(tid) or {}
        return {"task_id": tid, "title": task.get("title") or "", "status": task.get("status") or ""}

    return [[brief(tid) for tid in path] for path in chains]


# --------------------------------------------------------------------------
# 卡点识别
# --------------------------------------------------------------------------

def collect_blockers(store, now):
    """逐条扫描，返回 (卡点列表, 数据异常列表)。一个任务只保留最严重的一条卡点。"""
    tasks = store.get("tasks") or []
    plans = {p.get("plan_id"): (p.get("goal") or "") for p in (store.get("plans") or [])}
    dependents = _downstream(tasks)
    cycle_ids = _cycle_ids(tasks)
    status_of = {t.get("task_id"): t.get("status") for t in tasks}
    title_of = {t.get("task_id"): (t.get("title") or "") for t in tasks}
    open_tasks = [t for t in tasks if t.get("status") in OPEN_STATUSES]
    # 冲突组：{成员 id: (代表任务 id, 组内全部任务)}，一组只在代表任务上报一次
    conflicts = {}
    for cluster in _conflict_clusters(open_tasks):
        leader = cluster[0].get("task_id")
        for member in cluster:
            conflicts[member.get("task_id")] = (leader, cluster)

    blockers = []

    for task in open_tasks:
        tid = task.get("task_id")
        due = _parse(task.get("due_at"))
        start = _parse(task.get("scheduled_start_at")) or _parse(task.get("created_at"))
        updated = _parse(task.get("updated_at")) or _parse(task.get("created_at"))
        created = _parse(task.get("created_at"))
        progress = int(task.get("progress") or 0)
        delay_count = int(task.get("delay_count") or 0)
        impact = _impact(tid, dependents)

        base = {
            "task_id": tid,
            "title": task.get("title") or "",
            "plan_id": task.get("plan_id"),
            "plan_title": plans.get(task.get("plan_id"), ""),
            "status": task.get("status"),
            "progress": progress,
            "link": _link("tasks", focus=tid),
        }

        candidates = []

        # 1 · 关键前置：自己没完成、下游还有任务在等 —— 这是「卡点源」，动它收益最大
        if impact >= 1 and task.get("status") in ("进行中", "待执行", "已延期"):
            at_risk = bool(due and due < now + timedelta(hours=48)) or progress < 50
            if impact >= KEYSTONE_IMPACT or at_risk:
                candidates.append({
                    "code": "B-KEYSTONE",
                    "level": "critical" if impact >= KEYSTONE_IMPACT else "warning",
                    "kind": "关键前置",
                    "reason": "下游有 %d 项任务在等它，它不动下游全部停摆" % impact,
                    "evidence": ["下游依赖：" + "、".join(dependents.get(tid, [])[:4]),
                                 "自身状态：%s · 进度 %d%%" % (task.get("status"), progress),
                                 "截止时间：%s（%s）" % (_stamp(due), _human_gap(due, now))],
                    "suggestion": "这是当前杠杆最大的一步：先把它推到完成，%d 项下游任务立刻解冻。" % impact,
                })

        # 2 · 依赖锁死：前置没完成，本任务根本开不了工 —— 最典型的「卡点」
        pending = [d for d in (task.get("depends_on") or []) if status_of.get(d) not in CLOSED_STATUSES]
        if pending:
            detail = "、".join("%s《%s》· %s" % (d, (title_of.get(d) or "")[:14], status_of.get(d) or "未知") for d in pending)
            candidates.append({
                "code": "B-BLOCKED", "level": "critical", "kind": "依赖锁死",
                "reason": "前置任务尚未完成，本任务无法开工",
                "evidence": ["等待前置：" + detail,
                             "截止时间：" + (_stamp(due) if due else "未设置"),
                             "当前状态：%s · 进度 %d%%" % (task.get("status"), progress)],
                "suggestion": "先推动前置任务 %s 收口；若前置短期无法完成，考虑拆分本任务先做不受影响的部分。" % (pending[0]),
            })

        # 3 · 逾期未完成
        if due and due < now:
            overdue_days = (now - due).total_seconds() / 86400
            candidates.append({
                "code": "B-OVERDUE",
                "level": "critical" if overdue_days >= OVERDUE_CRITICAL_DAYS else "warning",
                "kind": "逾期未完成",
                "reason": "已越过截止时间 %s 仍未完成" % _human_gap(due, now).replace("已逾期 ", ""),
                "evidence": ["截止时间：%s（%s）" % (_stamp(due), _human_gap(due, now)),
                             "当前状态：%s · 进度 %d%%" % (task.get("status"), progress),
                             "累计延期：%d 次" % delay_count],
                "suggestion": "确认是时间估算偏差还是被外部阻塞；必要时调用延期接口填写原因，避免静默超期。",
            })

        # 4 · 进度滞后：实际进度明显落后于时间进度
        if task.get("status") == "进行中" and due and start and due > start:
            elapsed = (now - start).total_seconds() / max(1.0, (due - start).total_seconds())
            expected = max(0, min(100, round(elapsed * 100)))
            lag = expected - progress
            if lag >= LAG_WARN:
                candidates.append({
                    "code": "B-LAG",
                    "level": "critical" if lag >= LAG_CRITICAL else "warning",
                    "kind": "进度滞后",
                    "reason": "时间已走过 %d%%，实际进度只有 %d%%，落后 %d 个点" % (expected, progress, lag),
                    "evidence": ["时间进度：%d%%（%s 起算）" % (expected, _stamp(start)),
                                 "实际进度：%d%%" % progress,
                                 "按当前节奏，%s" % _human_gap(due, now)],
                    "suggestion": "盯一下卡在哪一步：是人力被抽走，还是在等外部输入；必要时降级范围先交付可用部分。",
                })

        # 5 · 进度停摆：进行中但长时间没有任何更新
        if task.get("status") == "进行中" and updated and (now - updated) >= timedelta(days=STALL_DAYS):
            candidates.append({
                "code": "B-STALL", "level": "warning", "kind": "进度停摆",
                "reason": "已连续 %d 天没有更新记录，可能已经停摆" % ((now - updated).days),
                "evidence": ["最后更新：%s" % _stamp(updated),
                             "当前进度：%d%%" % progress,
                             "当前状态：%s" % task.get("status")],
                "suggestion": "向执行人确认是否已经搁置；确实停摆的建议直接改期或拆分，不要挂在「进行中」里虚占产能。",
            })

        # 6 · 反复延期
        if delay_count >= DELAY_WARN:
            last_reason = (task.get("delay_reason") or "").strip()
            candidates.append({
                "code": "B-DELAY",
                "level": "critical" if delay_count >= DELAY_CRITICAL else "warning",
                "kind": "反复延期",
                "reason": "已累计延期 %d 次，说明排期与实际产能不匹配" % delay_count,
                "evidence": ["累计延期：%d 次" % delay_count,
                             "最近原因：" + (last_reason[:40] if last_reason else "未记录"),
                             "截止时间：%s（%s）" % (_stamp(due), _human_gap(due, now))],
                "suggestion": "延期次数多说明任务粒度或估算有问题，建议拆小重排，而不是继续顺延。",
            })

        # 7 · 临期高风险：离截止越近，允许的进度越低
        if due and now <= due < now + timedelta(hours=RISK_BANDS[-1][0]) and progress < 100:
            hours_left = (due - now).total_seconds() / 3600
            for window, floor, level in RISK_BANDS:
                if hours_left <= window and progress < floor:
                    candidates.append({
                        "code": "B-RISK", "level": level, "kind": "临期高风险",
                        "reason": "%s到期，进度仅 %d%%，低于该时间点应有的 %d%%" % (_human_gap(due, now), progress, floor),
                        "evidence": ["截止时间：%s（%s）" % (_stamp(due), _human_gap(due, now)),
                                     "当前进度：%d%%（该时间点红线 %d%%）" % (progress, floor)],
                        "suggestion": "今天必须给出结论：是收口交付，还是提前走延期流程并同步下游。",
                    })
                    break

        # 8 · 悬空未排期
        if (task.get("status") == "待安排" and not due and not task.get("scheduled_start_at")
                and created and (now - created) >= timedelta(days=ORPHAN_DAYS)):
            candidates.append({
                "code": "B-ORPHAN", "level": "info", "kind": "悬空未排期",
                "reason": "登记 %d 天仍未确定截止时间，等于没有进入执行" % (now - created).days,
                "evidence": ["登记时间：%s" % _stamp(created),
                             "截止时间：未设置",
                             "执行窗口：未排期"],
                "suggestion": "到「智能排期」里给它落一个时间窗；确定做不了的直接取消，别留在列表里。",
            })

        # 9 · 排期冲突：一组只报一次，挂在时段最早的那条任务上
        cluster = conflicts.get(tid)
        if cluster and cluster[0] == tid:
            members = cluster[1]
            candidates.append({
                "code": "B-CONFLICT", "level": "warning", "kind": "排期冲突",
                "reason": "%d 项任务的排期时段互相重叠，同一时间被安排了两件以上的事" % len(members),
                "evidence": ["重叠任务：" + "、".join("%s《%s》" % (m.get("task_id"), (m.get("title") or "")[:12]) for m in members[:4]),
                             "时段范围：%s ~ %s" % (
                                 _stamp(min(_parse(m.get("scheduled_start_at")) for m in members)),
                                 _stamp(max(_parse(m.get("planned_finish_at")) for m in members)))],
                "suggestion": "到「智能排期」重排时间窗；冲突不解决，最终都会变成延期。",
                "impact": len(members) - 1,
                "impact_text": "同窗口 %d 项互相挤占" % len(members),
            })

        # 10 · 依赖成环
        if tid in cycle_ids:
            candidates.append({
                "code": "B-CYCLE", "level": "critical", "kind": "依赖成环",
                "reason": "依赖关系形成环路，这些任务永远等不到开工条件",
                "evidence": ["环上任务：" + "、".join(sorted(cycle_ids)),
                             "当前状态：%s" % task.get("status")],
                "suggestion": "必须手工打断环：挑其中一环去掉依赖，或把两者合并成一个任务。",
            })

        if not candidates:
            continue

        best = sorted(candidates, key=lambda item: (LEVEL_ORDER[item["level"]], item["code"]))[0]
        best.update(base)
        best["level_label"] = LEVEL_LABEL[best["level"]]
        best["tags"] = [item["kind"] for item in candidates]
        best.setdefault("impact", impact)
        best.setdefault("impact_text", ("牵连 %d 项下游任务" % best["impact"]) if best["impact"] else "无下游依赖")
        blockers.append(best)

    blockers.sort(key=lambda item: (LEVEL_ORDER[item["level"]], -item["impact"], item["task_id"]))

    data_issues = []
    for row in store.get("exceptions") or []:
        if row.get("status") != "待处置":
            continue
        data_issues.append({
            "exception_id": row.get("exception_id"),
            "reason": row.get("reason") or "",
            "source": row.get("source") or "",
            "title": (row.get("raw") or {}).get("title") or "",
            "link": _link("data_manage"),
        })
    return blockers, data_issues


# --------------------------------------------------------------------------
# 研判汇总
# --------------------------------------------------------------------------

def _health(blockers, open_total):
    """健康分：加权卡点 ÷ (未完成任务数 × 3)，即「人人都是高风险」时才归零。"""
    weights = {"critical": 3.0, "warning": 1.0, "info": 0.3}
    weighted = sum(weights.get(item["level"], 0) for item in blockers)
    ceiling = max(1.0, open_total * 3.0)
    value = int(round(max(0.0, min(1.0, 1 - weighted / ceiling)) * 100))
    label, key = next((name, code) for floor, name, code in HEALTH_BANDS if value >= floor)
    return {
        "value": value,
        "label": label,
        "key": key,
        "weighted": round(weighted, 1),
        "ceiling": round(ceiling, 1),
        "formula": "100 × (1 − 加权卡点 %.1f ÷ (未完成 %d × 3))" % (weighted, open_total),
    }


def _stages(tasks, blockers):
    """按流水线环节统计在途与受阻情况，用来定位「卡在哪一环」。"""
    hard = {item["task_id"]: item for item in blockers if item["level"] != "info"}
    rows = []
    for name, statuses, hint in STAGE_DEFS:
        members = [t for t in tasks if t.get("status") in statuses]
        blocked = [t for t in members if t.get("task_id") in hard]
        critical = [t for t in blocked if hard[t.get("task_id")]["level"] == "critical"]
        ratio = round(len(blocked) / len(members) * 100) if members else 0
        if name == "已收尾":
            state = "ok"
        elif critical:
            state = "danger"
        elif blocked:
            state = "warn"
        else:
            state = "ok"
        rows.append({
            "name": name, "hint": hint,
            "count": len(members), "blocked": len(blocked), "critical": len(critical),
            "ratio": ratio, "state": state,
        })
    return rows


def _bottleneck(stages, blockers):
    """主卡点 = 受阻最严重（先看高风险数、再看受阻率）的环节。"""
    scored = [row for row in stages if row["name"] != "已收尾"]
    if not scored:
        return None
    worst = sorted(scored, key=lambda row: (row["critical"], row["blocked"], row["ratio"]), reverse=True)[0]
    if worst["blocked"] == 0:
        headline = "当前没有识别到硬卡点，流水线整体通畅"
        reason = "各环节均无逾期或依赖锁死，健康分处于「稳健」区间。"
        hint = "保持当前节奏，重点转向质量与容量评估。"
        link, top = _link("tasks"), ""
    else:
        top = next((item for item in blockers if item["task_id"]), None)
        headline = "「%s」环节是当前卡点：%d 项在途中有 %d 项受阻" % (worst["name"], worst["count"], worst["blocked"])
        reason = "其中 %d 项为高风险；首要卡点是 %s《%s》——%s。" % (
            worst["critical"], top["task_id"], top["title"][:16], top["reason"])
        hint = ("优先处理它，可一次性解冻 %d 项下游任务。" % top["impact"]) if top["impact"] else top["suggestion"]
        link = top["link"]
    return {
        "stage": worst["name"], "headline": headline, "reason": reason, "hint": hint,
        "link": link, "top_task_id": top["task_id"] if top else "", "top_title": top["title"] if top else "",
        "critical": worst["critical"], "blocked": worst["blocked"], "count": worst["count"],
    }


def _actions(blockers, data_issues, stages, chains):
    """行动清单：按「一次动手能解冻多少」排序，输出可点击的下一步。"""
    items = []
    for item in blockers:
        if item["level"] == "critical" and item["impact"] >= 1:
            items.append({
                "level": "critical",
                "title": "解冻 %s《%s》" % (item["task_id"], item["title"][:18]),
                "why": "%s；处理它可释放 %d 项下游任务" % (item["kind"], item["impact"]),
                "link": item["link"],
            })
        if len(items) >= 3:
            break

    conflict_ids = {item["task_id"] for item in blockers if item["code"] == "B-CONFLICT"}
    if conflict_ids:
        items.append({
            "level": "warning",
            "title": "重排 %d 组冲突时段" % len(conflict_ids),
            "why": "同一时间窗被安排了两件以上任务，不处理会直接变成下一次延期",
            "link": _link("schedule"),
        })

    orphan = [item for item in blockers if item["code"] == "B-ORPHAN"]
    if orphan:
        items.append({
            "level": "info",
            "title": "为 %d 项悬空任务补齐时间" % len(orphan),
            "why": "这些任务登记后一直没有截止时间，等于没进入执行",
            "link": _link("schedule"),
        })

    if data_issues:
        items.append({
            "level": "info",
            "title": "处置 %d 条异常数据" % len(data_issues),
            "why": "导入时被校验拦截的记录，积压会让统计口径失真",
            "link": _link("data_manage"),
        })

    if chains and len(chains[0]) >= 3:
        head = chains[0]
        items.append({
            "level": "warning",
            "title": "关注最长阻塞链（%d 级）" % len(head),
            "why": " → ".join(node["title"][:12] for node in head),
            "link": _link("tasks", focus=head[0]["task_id"]),
        })
    return items


def analyze(store, now=None):
    """中枢研判主入口：一次算完页面和接口需要的全部内容。"""
    now = now or _now()
    tasks = store.get("tasks") or []
    open_tasks = [t for t in tasks if t.get("status") in OPEN_STATUSES]
    blockers, data_issues = collect_blockers(store, now)
    stages = _stages(tasks, blockers)
    chains = _chains(tasks)
    health = _health(blockers, len(open_tasks))
    bottleneck = _bottleneck(stages, blockers)

    counts = {"critical": 0, "warning": 0, "info": 0}
    for item in blockers:
        counts[item["level"]] += 1

    scheduled = sum(1 for t in open_tasks if t.get("scheduled_start_at"))
    done = sum(1 for t in tasks if t.get("status") == "已完成")

    if not tasks:
        judgement = "还没有任务数据，先在「数据管理」导入一份任务清单，中枢才能开始研判。"
    elif not blockers and not data_issues:
        judgement = "共 %d 项未完成任务，未识别到卡点，流水线整体通畅。" % len(open_tasks)
    else:
        judgement = "共 %d 项未完成任务，识别出 %d 个卡点（高风险 %d / 需关注 %d / 可延后 %d），主要堵在「%s」。%s" % (
            len(open_tasks), len(blockers), counts["critical"], counts["warning"], counts["info"],
            bottleneck["stage"] if bottleneck else "—",
            bottleneck["hint"] if bottleneck else "")

    return {
        "has_data": bool(tasks),
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "judgement": judgement,
        "health": health,
        "bottleneck": bottleneck,
        "kpi": {
            "open_total": len(open_tasks),
            "blocker_total": len(blockers),
            "critical": counts["critical"],
            "warning": counts["warning"],
            "info": counts["info"],
            "chain_len": max((len(chain) for chain in chains), default=0),
            "schedule_rate": round(scheduled / len(open_tasks) * 100) if open_tasks else 0,
            "completion_rate": round(done / len(tasks) * 100) if tasks else 0,
            "data_issues": len(data_issues),
        },
        "stages": stages,
        "blockers": blockers,
        "chains": chains,
        "actions": _actions(blockers, data_issues, stages, chains),
        "data_issues": data_issues,
    }


# --------------------------------------------------------------------------
# 可选：接入大模型做深度研判（未配置时原样回退，页面不报错）
# --------------------------------------------------------------------------

def deep_narrative(payload):
    """把规则结论交给大模型复述成人话；任何失败都回退到规则结论。"""
    fallback = {"source": "rule", "lines": [payload["judgement"]], "advice": None}
    try:
        from importlib import import_module

        llm = import_module("backend.llm")
        if not llm.llm_configured():
            return fallback
        brief = {
            "summary": payload["judgement"],
            "health": payload["health"],
            "bottleneck": payload["bottleneck"],
            "blockers": [
                {"level": item["level"], "kind": item["kind"], "title": item["title"],
                 "reason": item["reason"], "impact": item["impact"]}
                for item in payload["blockers"][:8]
            ],
        }
        result = llm._llm_chat_json(
            "你是项目进度分析师。只依据给定的研判数据说话，不得编造任务或数字。"
            "输出 JSON：{\"lines\": [2-3 条中文结论]，\"advice\": \"一句最优先动作\"}。",
            "研判数据：\n" + __import__("json").dumps(brief, ensure_ascii=False),
            timeout=20,
        )
        if not isinstance(result, dict) or not result.get("lines"):
            return fallback
        return {"source": "llm",
                "lines": [str(line) for line in result["lines"]][:3],
                "advice": result.get("advice")}
    except Exception:
        return fallback


# --------------------------------------------------------------------------
# 路由
# --------------------------------------------------------------------------

def register(app, services):
    read_store = services.read_store
    read_settings = services.read_settings

    def _payload():
        return analyze(read_store())

    @app.get("/hub")
    def hub():
        return render_template(
            "pages/hub.html",
            active_page="hub",
            page_name="AI 任务中枢",
            settings=read_settings(),
            **_payload()
        )

    @app.get("/hub/analyze")
    def hub_analyze():
        return jsonify(_payload())

    @app.post("/hub/deep")
    def hub_deep():
        payload = _payload()
        return jsonify(deep_narrative(payload))
