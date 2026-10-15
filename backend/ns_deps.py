"""B4 关键路径与阻塞（F-216~219）—— /ns/critical-path 页面 + 依赖 / 关键路径 / 影响传导接口。

算法要点（4.4.5）：
- 拓扑排序做环检测：成环 → 1005 + 环上 task_id 有序列表，保存被阻断；
- ES/LS 动态规划（按拓扑序，防死循环）：total_float = 0 串成关键路径；
- 唯一热点阻塞：关键路径上唯一且工期占比最高的节点；工期并列 → 如实提示无单点；
- 影响传导：受影响任务重算 EF/ES 差分，输出「延期 N 天 → 项目整体延期 M 天」；
- 预警联动（F-218）：关键路径节点为黄/红时整体健康度降一级。
"""

import math
from collections import deque

from flask import request

from backend.core import (
    DATA_LOCK,
    TASK_STATUS,
    fail,
    f1,
    iso,
    next_id,
    now,
    parse_dt,
    read_store,
    write_store,
)
from backend.ns_common import (
    api_perm,
    assignee_ids,
    audit,
    health_label,
    in_visible,
    is_manager,
    ns_api,
    ns_render,
    ns_user,
    payload,
    require_ns_menu,
    scope_objectives_check,
    task_by_id,
    user_name,
    visible_user_ids,
)

DEP_TYPES = {
    "FS": "完成-开始（前置完成后才能开始）",
    "SS": "开始-开始",
    "FF": "完成-完成",
}
HOURS_PER_DAY = 8.0


def _editable(store, user, task):
    if not task:
        return False
    if is_manager(store, user):
        return any(in_visible(store, user, uid) for uid in assignee_ids(task))
    return user["user_id"] in assignee_ids(task)


def _duration(task):
    """工期（天）：优先按计划工时折算（8h/天），无工时时按计划日期跨度。"""
    hours = float(task.get("plan_hours") or 0)
    if hours > 0:
        return max(1, int(math.ceil(hours / HOURS_PER_DAY)))
    start, end = parse_dt(task.get("plan_start")), parse_dt(task.get("plan_end"))
    if start and end and end >= start:
        return max(1, (end - start).days)
    return 1


def _nodes(store, objective_id=""):
    tasks = [
        t for t in store["work_task"]
        if t.get("status") != "cancelled"
        and (not objective_id or t.get("objective_id") == objective_id)
    ]
    return {t["task_id"]: t for t in tasks}


def _edges(store, node_ids):
    return [
        dict(d) for d in store["dependency"]
        if d.get("from_task_id") in node_ids and d.get("to_task_id") in node_ids
    ]


def _topo(node_ids, edges):
    indeg = {nid: 0 for nid in node_ids}
    outgoing = {nid: [] for nid in node_ids}
    for edge in edges:
        outgoing[edge["from_task_id"]].append(edge)
        indeg[edge["to_task_id"]] += 1
    queue = deque(sorted(nid for nid in node_ids if indeg[nid] == 0))
    order = []
    while queue:
        nid = queue.popleft()
        order.append(nid)
        for edge in sorted(outgoing[nid], key=lambda e: e["to_task_id"]):
            nxt = edge["to_task_id"]
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if len(order) == len(node_ids):
        return order, []
    return order, _find_cycle(node_ids, edges, set(order))


def _find_cycle(node_ids, edges, settled):
    outgoing = {}
    for edge in edges:
        outgoing.setdefault(edge["from_task_id"], []).append(edge["to_task_id"])
    state = {}
    stack = []

    def dfs(node):
        state[node] = 1
        stack.append(node)
        for nxt in sorted(outgoing.get(node, [])):
            if state.get(nxt, 0) == 0:
                found = dfs(nxt)
                if found:
                    return found
            elif state.get(nxt) == 1:
                return stack[stack.index(nxt):]
        stack.pop()
        state[node] = 2
        return None

    for nid in sorted(node_ids):
        if nid in settled or state.get(nid, 0) != 0:
            continue
        found = dfs(nid)
        if found:
            return found
    return []


def _cpm(node_ids, durations, edges):
    """ES/LS 计算：支持 FS / SS / FF 与可负 lag；返回关键路径与各节点总时差。"""
    order, cycle = _topo(node_ids, edges)
    if cycle:
        return {"cycle_path": cycle}
    incoming = {nid: [] for nid in node_ids}
    outgoing = {nid: [] for nid in node_ids}
    for edge in edges:
        incoming[edge["to_task_id"]].append(edge)
        outgoing[edge["from_task_id"]].append(edge)
    es, ef = {}, {}
    for nid in order:
        latest = 0.0
        for edge in incoming[nid]:
            pred = edge["from_task_id"]
            lag = int(edge.get("lag_days") or 0)
            kind = edge.get("dep_type") or "FS"
            if kind == "SS":
                latest = max(latest, es[pred] + lag)
            elif kind == "FF":
                latest = max(latest, ef[pred] + lag - durations[nid])
            else:
                latest = max(latest, ef[pred] + lag)
        es[nid] = latest
        ef[nid] = latest + durations[nid]
    project = max(ef.values()) if ef else 0.0
    ls, lf = {}, {}
    for nid in reversed(order):
        if not outgoing[nid]:
            lf[nid] = project
        else:
            bound = None
            for edge in outgoing[nid]:
                succ = edge["to_task_id"]
                lag = int(edge.get("lag_days") or 0)
                kind = edge.get("dep_type") or "FS"
                if kind == "SS":
                    cand = ls[succ] - lag + durations[nid]
                elif kind == "FF":
                    cand = lf[succ] - lag
                else:
                    cand = ls[succ] - lag
                bound = cand if bound is None else min(bound, cand)
            lf[nid] = bound
        ls[nid] = lf[nid] - durations[nid]
    total_float = {nid: ls[nid] - es[nid] for nid in node_ids}
    critical = [nid for nid in node_ids if total_float[nid] <= 1e-9]
    critical.sort(key=lambda nid: (es[nid], -durations[nid], nid))
    return {
        "cycle_path": [], "order": order, "es": es, "ef": ef, "ls": ls, "lf": lf,
        "total_float": total_float, "critical_ids": critical,
        "project_duration_days": int(math.ceil(project)),
    }


def _hotspot(node_ids, tasks, durations, cpm_result):
    critical = cpm_result.get("critical_ids") or []
    if not critical:
        return None, "关键路径为空：依赖数据不足，结论仅供参考"
    ranked = sorted(critical, key=lambda nid: (-durations[nid], nid))
    top = ranked[0]
    if len(ranked) > 1 and durations[ranked[1]] == durations[top]:
        return None, "存在多条关键路径（工期并列），无唯一单点瓶颈"
    total = float(sum(durations[nid] for nid in critical)) or 1.0
    share = round(durations[top] / total * 100)
    hotspot = {
        "task_id": top,
        "title": (tasks.get(top) or {}).get("title", top),
        "plan_hours": (tasks.get(top) or {}).get("plan_hours", 0),
        "duration_days": durations[top],
        "share": share,
    }
    return hotspot, "唯一单点瓶颈：%s（占关键路径工期 %d%%）" % (hotspot["title"], share)


def _cpm_with_delay(node_ids, durations, edges, delay_task_id, delay_days):
    if not delay_task_id or delay_days <= 0:
        return None
    bumped = dict(durations)
    bumped[delay_task_id] = durations[delay_task_id] + int(delay_days)
    return _cpm(node_ids, bumped, edges)


def critical_snapshot(store, objective_id=""):
    """一次算齐：关键路径 / 热点 / 每任务时差与传导信息（页面、预警、变更共用）。"""
    tasks = _nodes(store, objective_id)
    node_ids = set(tasks)
    edges = _edges(store, node_ids)
    durations = {nid: _duration(task) for nid, task in tasks.items()}
    result = _cpm(node_ids, durations, edges)
    if result.get("cycle_path"):
        return {"cycle_path": result["cycle_path"], "critical_ids": set(), "critical_path": [],
                "float_map": {}, "durations": durations, "order": [], "hotspot": None,
                "hint": "依赖图存在环路，无法计算关键路径，请先修复依赖。",
                "project_duration_days": 0, "edges": edges, "tasks": tasks}
    hotspot, hint = _hotspot(node_ids, tasks, durations, result)
    if hotspot:
        delayed = _cpm_with_delay(node_ids, durations, edges, hotspot["task_id"], 1)
        delay = int(delayed["project_duration_days"] - result["project_duration_days"]) if delayed else 0
        hotspot["impact"] = {"delay_days": 1, "project_delay_days": delay}
        hotspot["impact_text"] = "该环节延期 1 天 → 项目整体延期 %d 天" % delay
    return {
        "cycle_path": [],
        "critical_ids": set(result["critical_ids"]),
        "critical_path": list(result["critical_ids"]),
        "float_map": result["total_float"],
        "es_map": result["es"],
        "durations": durations,
        "order": result["order"],
        "hotspot": hotspot,
        "hint": hint,
        "project_duration_days": result["project_duration_days"],
        "edges": edges,
        "tasks": tasks,
    }


def impact_for(store, task_id, delay_days=0, objective_id=""):
    """impact_days(task)：重算项目工期差分 + 受影响任务（传递闭包，按拓扑序）。"""
    snapshot = critical_snapshot(store, objective_id)
    if snapshot["cycle_path"]:
        fail(1005, "依赖图存在环路，无法计算影响传导。", {"cycle_path": snapshot["cycle_path"]})
    task = snapshot["tasks"].get(task_id)
    if not task:
        fail(1003, "任务不存在或不在当前图范围内。")
    durations, edges = snapshot["durations"], snapshot["edges"]
    node_ids = set(durations)
    assume = int(delay_days or 0)
    base_lag = 0
    end = parse_dt(task.get("plan_end"))
    if assume <= 0 and end and end < now() and float(task.get("progress") or 0) < 100:
        base_lag = max(1, (now() - end).days)
    delay = assume if assume > 0 else base_lag
    base_cpm = _cpm(node_ids, durations, edges)
    is_critical = task_id in snapshot["critical_ids"]
    total_float = snapshot["float_map"].get(task_id, 0)
    if delay <= 0:
        return {
            "task_id": task_id, "title": task["title"], "is_critical": is_critical,
            "delay_days": 0, "impact_days": 0, "project_delay_days": 0,
            "total_float_days": int(total_float), "affected_tasks": [],
            "conclusion": "任务当前未超期，且未给定假设延期天数，暂无传导影响。",
            "suggestions": ["如需评估风险，可在假设延期天数中填写 1~N 天后重算。"],
        }
    delayed = _cpm_with_delay(node_ids, durations, edges, task_id, delay)
    project_delay = int(delayed["project_duration_days"] - base_cpm["project_duration_days"])
    affected = []
    for nid in delayed["order"]:
        if nid == task_id:
            continue
        shift = int(round(delayed["es"][nid] - base_cpm["es"][nid]))
        if shift > 0:
            item = snapshot["tasks"][nid]
            affected.append({
                "task_id": nid, "title": item["title"], "delay_days": shift,
                "status": item.get("status"), "status_label": TASK_STATUS.get(item.get("status"), ""),
                "assignee_names": [user_name(store, uid) for uid in assignee_ids(item)],
            })
    if is_critical or project_delay > 0:
        conclusion = "该环节延期 %d 天 → 项目整体延期 %d 天。" % (delay, project_delay)
    else:
        conclusion = "该环节延期 %d 天可由总时差（%d 天）吸收，项目整体不延期。" % (
            delay, int(total_float))
    suggestions = []
    if project_delay > 0:
        suggestions.append("优先压缩「%s」后的关键路径任务工期，或调整资源投入。" % task["title"])
    if affected:
        names = "、".join(item["title"] for item in affected[:3])
        suggestions.append("受影响后续任务：%s%s。" % (names, " 等" if len(affected) > 3 else ""))
    suggestions.append("可通过「变更管理」调整计划，或对责任人发起「AI 过程指导」。")
    return {
        "task_id": task_id, "title": task["title"], "is_critical": is_critical,
        "delay_days": delay, "impact_days": project_delay, "project_delay_days": project_delay,
        "total_float_days": int(total_float), "affected_tasks": affected,
        "conclusion": conclusion, "suggestions": suggestions,
    }


def _scoped_nodes(store, user, objective_id=""):
    tasks = _nodes(store, objective_id)
    scope_ids = visible_user_ids(store, user)
    if scope_ids is None:
        return tasks
    return {
        nid: task for nid, task in tasks.items()
        if any(uid in scope_ids for uid in assignee_ids(task))
    }


def _project_health(store, snapshot, tasks):
    """F-218：关键路径节点黄/红 → 整体健康度降一级。"""
    from backend import ns_common

    worst = "green"
    for nid in snapshot["critical_ids"]:
        task = tasks.get(nid)
        if not task or task.get("status") in ("done", "cancelled"):
            continue
        tag = ns_common.task_health(store, task)["health"]
        if tag == "lagging":
            worst = "red"
            break
        if tag == "normal":
            worst = "yellow" if worst == "green" else worst
    return worst


def graph_payload(store, user, objective_id="", with_done=True):
    tasks = _scoped_nodes(store, user, objective_id)
    node_ids = set(tasks)
    edges = _edges(store, node_ids)
    durations = {nid: _duration(task) for nid, task in tasks.items()}
    snapshot = critical_snapshot(store, objective_id)
    if snapshot["tasks"] and objective_id:
        allowed = set(tasks)
        snapshot["critical_ids"] = snapshot["critical_ids"] & allowed
    nodes = []
    for nid, task in tasks.items():
        if not with_done and task.get("status") in ("done", "cancelled"):
            continue
        item = ns_common_task_card(store, task)
        item.update({
            "is_critical": nid in snapshot["critical_ids"],
            "total_float": int(round(snapshot["float_map"].get(nid, 0))),
            "es": int(round(snapshot["es_map"].get(nid, 0))),
            "duration_days": durations[nid],
            "is_hotspot": bool(snapshot["hotspot"] and snapshot["hotspot"].get("task_id") == nid),
        })
        nodes.append(item)
    visible_ids = {item["task_id"] for item in nodes}
    edges = [e for e in edges if e["from_task_id"] in visible_ids and e["to_task_id"] in visible_ids]
    with_edges = {e["from_task_id"] for e in edges} | {e["to_task_id"] for e in edges}
    coverage = round(len(with_edges) / len(visible_ids) * 100) if visible_ids else 0
    critical_path = [nid for nid in snapshot["critical_path"] if nid in visible_ids]
    hotspot = snapshot["hotspot"]
    if hotspot and hotspot.get("task_id") not in visible_ids:
        hotspot = None
    warnings = []
    if snapshot["cycle_path"]:
        warnings.append({"level": "red", "text": "依赖图存在环路，请先修复：" + " → ".join(snapshot["cycle_path"])})
    if coverage < 50:
        warnings.append({"level": "yellow",
                         "text": "依赖覆盖率 %d%%，关键路径结论仅供参考，建议先批量推断依赖。" % coverage})
    return {
        "objective_id": objective_id,
        "nodes": sorted(nodes, key=lambda item: item["task_id"]),
        "edges": edges,
        "critical_path": critical_path,
        "critical_path_detail": [
            {
                "task_id": nid,
                "title": tasks[nid]["title"],
                "plan_hours": tasks[nid].get("plan_hours", 0),
                "total_float": int(round(snapshot["float_map"].get(nid, 0))),
                "assignee_names": [user_name(store, uid) for uid in assignee_ids(tasks[nid])],
            }
            for nid in critical_path
        ],
        "hotspot": hotspot,
        "hotspot_task_id": hotspot["task_id"] if hotspot else None,
        "hotspot_reason": snapshot["hint"],
        "project_duration_days": snapshot["project_duration_days"],
        "dep_coverage": coverage,
        "project_health": _project_health(store, snapshot, snapshot["tasks"]),
        "warnings": warnings,
    }


def ns_common_task_card(store, task):
    from backend.ns_common import task_health

    metrics = task_health(store, task)
    return {
        "task_id": task["task_id"],
        "title": task["title"],
        "objective_id": task.get("objective_id", ""),
        "plan_hours": task.get("plan_hours", 0),
        "progress": f1(task.get("progress")),
        "exec_rate": f1(task.get("exec_rate")),
        "status": task.get("status"),
        "status_label": TASK_STATUS.get(task.get("status"), task.get("status")),
        "plan_start": task.get("plan_start", ""),
        "plan_end": task.get("plan_end", ""),
        "blocked_reason": task.get("blocked_reason", ""),
        "assignee_names": [user_name(store, uid) for uid in assignee_ids(task)],
        "assignee_ids": assignee_ids(task),
        "health": metrics["health"],
        "health_label": health_label(metrics["health"]),
        "time_progress": metrics["time_progress"],
    }


def _validate_edge(store, edge):
    kind = (edge.get("dep_type") or "FS").upper()
    if kind not in DEP_TYPES:
        fail(1001, "dep_type 仅支持 FS / SS / FF。")
    try:
        lag = int(edge.get("lag_days") or 0)
    except (TypeError, ValueError):
        fail(1001, "lag_days 需为整数（可为负）。")
    from_id, to_id = edge.get("from_task_id"), edge.get("to_task_id")
    if not from_id or not to_id:
        fail(1001, "依赖边缺少 from_task_id / to_task_id。")
    if from_id == to_id:
        fail(1001, "不允许任务依赖自身。")
    if not task_by_id(store, from_id):
        fail(1003, "前置任务 %s 不存在。" % from_id)
    if not task_by_id(store, to_id):
        fail(1003, "后置任务 %s 不存在。" % to_id)
    return {"dep_id": edge.get("dep_id") or "", "from_task_id": from_id,
            "to_task_id": to_id, "dep_type": kind, "lag_days": lag}


def _clean_dangling(store):
    valid = {t["task_id"] for t in store["work_task"]}
    before = len(store["dependency"])
    store["dependency"] = [
        d for d in store["dependency"]
        if d.get("from_task_id") in valid and d.get("to_task_id") in valid
    ]
    return before - len(store["dependency"])


def register(app, services):
    @app.get("/ns/critical-path")
    @require_ns_menu("critical_path")
    def ns_critical_path():
        store = read_store()
        user = ns_user(store)
        objective_id = (request.args.get("objective_id") or "").strip()
        if objective_id:
            scope_objectives_check(store, user, objective_id)
        graph = graph_payload(store, user, objective_id)
        objectives = [
            {"objective_id": o["objective_id"], "title": o["title"]}
            for o in store["objective"]
            if not o.get("is_archived")
            and (visible_user_ids(store, user) is None
                 or o.get("owner_id") in visible_user_ids(store, user)
                 or any(t.get("objective_id") == o["objective_id"]
                        and any(uid in visible_user_ids(store, user) for uid in assignee_ids(t))
                        for t in store["work_task"]))
        ]
        return ns_render(
            "ns/critical_path.html", "critical_path", "关键路径与阻塞",
            "依赖图 → 环检测 → ES/LS 关键路径 → 唯一热点阻塞 → 影响传导",
            store=store, user=user,
            objective_id=objective_id,
            objectives=objectives,
            graph=graph,
            can_edit=is_manager(store, user),
            dep_types=DEP_TYPES,
        )

    @app.get("/api/ns/dep/graph")
    @ns_api
    def ns_api_dep_graph():
        store = read_store()
        user = ns_user(store)
        objective_id = (request.args.get("objective_id") or "").strip()
        if objective_id:
            scope_objectives_check(store, user, objective_id)
        with_done = request.args.get("with_done", "true") != "false"
        return graph_payload(store, user, objective_id, with_done)

    @app.post("/api/ns/dep/save")
    @ns_api
    def ns_api_dep_save():
        body = payload()
        mode = (body.get("mode") or "save").strip()
        if mode not in ("save", "infer", "delete"):
            fail(1001, "mode 仅支持 save / infer / delete。")
        with DATA_LOCK:
            store = read_store()
            user = ns_user(store)
            api_perm(store, user, "action:dep.save")
            removed = _clean_dangling(store)
            before = critical_snapshot(store, (body.get("objective_id") or "").strip())
            before_path = list(before["critical_path"])

            if mode == "delete":
                keys = set()
                for edge in body.get("edges") or []:
                    if edge.get("dep_id"):
                        keys.add(("id", edge["dep_id"]))
                    elif edge.get("from_task_id") and edge.get("to_task_id"):
                        keys.add(("pair", edge["from_task_id"], edge["to_task_id"]))
                kept = []
                for dep in store["dependency"]:
                    if ("id", dep["dep_id"]) in keys:
                        continue
                    if ("pair", dep.get("from_task_id"), dep.get("to_task_id")) in keys:
                        continue
                    kept.append(dep)
                removed += len(store["dependency"]) - len(kept)
                store["dependency"] = kept
                audit(store, user, "删除依赖", "dependency", str(len(keys)), None, {"removed": removed})
            else:
                if mode == "save":
                    edges = body.get("edges") or []
                    if not edges:
                        fail(1001, "请提供 edges 边列表。")
                    for raw in edges:
                        edge = _validate_edge(store, raw)
                        if not _editable(store, user, task_by_id(store, edge["from_task_id"])) \
                                and not _editable(store, user, task_by_id(store, edge["to_task_id"])):
                            fail(1008, "仅可维护数据范围内任务的依赖关系。")
                else:
                    objective_id = (body.get("objective_id") or "").strip()
                    if not objective_id:
                        fail(1001, "mode=infer 需要提供 objective_id。")
                    scope_objectives_check(store, user, objective_id)
                    siblings = {}
                    for task in store["work_task"]:
                        if task.get("objective_id") != objective_id or task.get("status") == "cancelled":
                            continue
                        siblings.setdefault(task.get("parent_task_id") or "__root__", []).append(task)
                    def _chain(rows):
                        ordered = sorted(rows, key=lambda t: (t.get("plan_start") or "", t["task_id"]))
                        return [
                            {"from_task_id": prev["task_id"], "to_task_id": nxt["task_id"],
                             "dep_type": "FS", "lag_days": 0}
                            for prev, nxt in zip(ordered, ordered[1:])
                        ]
                    edges = _chain(siblings.get("__root__", []))
                    for parent_id, children in siblings.items():
                        if parent_id == "__root__":
                            continue
                        edges.extend(
                            {"from_task_id": parent_id, "to_task_id": child["task_id"],
                             "dep_type": "FS", "lag_days": 0}
                            for child in sorted(children, key=lambda t: (t.get("plan_start") or "", t["task_id"]))
                        )

                existing_pairs = {(d["from_task_id"], d["to_task_id"]): d for d in store["dependency"]}
                saved = 0
                for raw in edges:
                    edge = _validate_edge(store, raw)
                    pair = (edge["from_task_id"], edge["to_task_id"])
                    if pair in existing_pairs:
                        target = existing_pairs[pair]
                        target["dep_type"] = edge["dep_type"]
                        target["lag_days"] = edge["lag_days"]
                    else:
                        if edge["dep_id"]:
                            target = next((d for d in store["dependency"] if d["dep_id"] == edge["dep_id"]), None)
                        else:
                            target = None
                        if target:
                            target["from_task_id"] = edge["from_task_id"]
                            target["to_task_id"] = edge["to_task_id"]
                            target["dep_type"] = edge["dep_type"]
                            target["lag_days"] = edge["lag_days"]
                        else:
                            store["dependency"].append({
                                "dep_id": next_id(store, "dependency", "D", 3),
                                "from_task_id": edge["from_task_id"],
                                "to_task_id": edge["to_task_id"],
                                "dep_type": edge["dep_type"],
                                "lag_days": edge["lag_days"],
                            })
                            existing_pairs[pair] = store["dependency"][-1]
                    saved += 1

                after_ids = {t["task_id"] for t in store["work_task"] if t.get("status") != "cancelled"}
                _, cycle = _topo(after_ids, _edges(store, after_ids))
                if cycle:
                    fail(1005, "依赖成环，保存被拒绝：%s。" % " → ".join(cycle + [cycle[0]]),
                         {"cycle_path": cycle})
                audit(store, user, "维护依赖", "dependency", str(saved), None,
                      {"mode": mode, "saved": saved})

            write_store(store)
            after = critical_snapshot(store, (body.get("objective_id") or "").strip())
            changed = after["critical_path"] != before_path
            return {
                "saved_count": saved if mode != "delete" else 0,
                "removed_count": removed,
                "cycle_path": [],
                "critical_path_changed": changed,
                "new_critical_path": after["critical_path"] if changed else before_path,
            }

    @app.get("/api/ns/dep/critical-path")
    @ns_api
    def ns_api_dep_critical_path():
        store = read_store()
        user = ns_user(store)
        objective_id = (request.args.get("objective_id") or "").strip()
        if objective_id:
            scope_objectives_check(store, user, objective_id)
        snapshot = critical_snapshot(store, objective_id)
        if snapshot["cycle_path"]:
            fail(1005, "依赖图存在环路，无法计算关键路径。", {"cycle_path": snapshot["cycle_path"]})
        tasks = snapshot["tasks"]
        return {
            "critical_path": snapshot["critical_path"],
            "critical_path_detail": [
                {
                    "task_id": nid, "title": tasks[nid]["title"],
                    "plan_hours": tasks[nid].get("plan_hours", 0),
                    "total_float": int(round(snapshot["float_map"].get(nid, 0))),
                    "assignee_names": [user_name(store, uid) for uid in assignee_ids(tasks[nid])],
                }
                for nid in snapshot["critical_path"]
            ],
            "hotspot": snapshot["hotspot"],
            "hotspot_task_id": snapshot["hotspot"]["task_id"] if snapshot["hotspot"] else None,
            "hotspot_reason": snapshot["hint"],
            "total_duration_days": snapshot["project_duration_days"],
        }

    @app.get("/api/ns/dep/impact")
    @ns_api
    def ns_api_dep_impact():
        store = read_store()
        user = ns_user(store)
        task_id = (request.args.get("task_id") or "").strip()
        if not task_id:
            fail(1001, "缺少 task_id。")
        task = task_by_id(store, task_id)
        if not task:
            fail(1003, "任务不存在。")
        if not _editable(store, user, task) and not is_manager(store, user):
            fail(1008, "无权查看该任务的影响传导。")
        try:
            delay = int(request.args.get("delay_days", 0) or 0)
        except (TypeError, ValueError):
            fail(1001, "delay_days 需为整数。")
        result = impact_for(store, task_id, delay, task.get("objective_id") or "")
        result["updated_at"] = iso()
        return result
