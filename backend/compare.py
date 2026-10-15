"""C 线 · 对比分析（F-307 纵向对比 / F-308 横向对比）。

对应需求：AC-24 三率分开 · 不可比项必须提示 · 缺失周期显示「无数据」而非 0
归属文件：backend/compare.py · templates/pages/compare.html · css|js/pages/compare.*

硬约束：
- 三率（目标达成率 / 任务完成率 / 目标更新覆盖率）口径不同，必须分开展示；
- 禁止用任务完成率冒充目标达成率；
- 缺失周期不补 0，按「无数据」呈现，并明确标注不可比；
- 汇总一律调用 backend.c_metrics 的加权口径，不做简单平均。
"""

from importlib import import_module

from flask import render_template, request

c_metrics = import_module("backend.c_metrics")


def series_index(store):
    """按目标系（series_id）归集跨周期目标，供纵向对比。"""
    index = {}
    for objective in store.get("objectives", []):
        if objective.get("status") == c_metrics.OBJ_DRAFT:
            continue
        key = objective.get("series_id") or objective.get("objective_id")
        index.setdefault(key, []).append(objective)
    return index


def merged_score(objective):
    return c_metrics.objective_achievement(objective, "final")


def vertical_rows(store, series_objects, series_id):
    """同一目标系按周期排开；缺失周期标记为无数据，不补 0。"""
    cycles = {item["cycle_id"]: item for item in store.get("cycles", [])}
    by_cycle = {item.get("cycle_id"): item for item in series_objects}
    ordered = sorted(store.get("cycles", []), key=lambda item: item.get("start_at") or "")
    rows = []
    for cycle in ordered:
        objective = by_cycle.get(cycle.get("cycle_id"))
        if objective:
            score = merged_score(objective)
            rows.append({
                "cycle_id": cycle.get("cycle_id"),
                "cycle_name": cycle.get("name"),
                "has_data": True,
                "score": score,
                "status": objective.get("status"),
                "verified": objective.get("status") in c_metrics.VERIFIED_STATUSES,
                "weight": objective.get("weight"),
                "kr_total": len(objective.get("key_results") or []),
                "objective_id": objective.get("objective_id"),
                "grade": c_metrics.grade_of(score),
            })
        else:
            rows.append({
                "cycle_id": cycle.get("cycle_id"),
                "cycle_name": cycle.get("name"),
                "has_data": False,
                "score": None,
                "status": "无数据",
                "verified": False,
                "weight": None,
                "kr_total": 0,
                "objective_id": "",
                "grade": "",
            })

    warnings = []
    known = [row for row in rows if row["has_data"]]
    kr_counts = {row["kr_total"] for row in known}
    if len(kr_counts) > 1:
        warnings.append("该目标系各周期的关键结果数量不同（%s），趋势对比仅供参考。"
                        % "、".join(str(count) for count in sorted(kr_counts)))
    weights = {row["weight"] for row in known}
    if len(weights) > 1:
        warnings.append("各周期目标权重不同（%s），跨周期加权不可直接比较。"
                        % "、".join(str(weight) for weight in sorted(weights)))
    missing = [row["cycle_name"] for row in rows if not row["has_data"]]
    if missing:
        warnings.append("以下周期没有该目标的数据：%s，图中按「无数据」呈现，不做趋势推断。"
                        % "、".join(missing))

    delta = None
    dated = [row for row in known if row["score"] is not None]
    if len(dated) >= 2:
        delta = round(dated[-1]["score"] - dated[0]["score"], 1)
    title = series_objects[0].get("title") if series_objects else "—"
    return {"series_id": series_id, "title": title, "rows": rows, "warnings": warnings, "delta": delta}


def horizontal_users(store, users):
    """横向 · 个人维度：只统计已定案目标，按目标权重加权。"""
    name_map = {item["id"]: item for item in users}
    buckets = {}
    for objective in store.get("objectives", []):
        if objective.get("status") not in c_metrics.VERIFIED_STATUSES:
            continue
        owner = objective.get("user_id") or ""
        buckets.setdefault(owner, []).append(objective)
    rows = []
    for owner, objectives in buckets.items():
        score = c_metrics.weighted_objective_average(objectives)
        finals = [item for item in objectives if item.get("objective_id")]
        rows.append({
            "key": owner,
            "name": (name_map.get(owner) or {}).get("name") or "未指派",
            "role": (name_map.get(owner) or {}).get("role") or "",
            "objective_count": len(objectives),
            "achievement": score,
            "grade": c_metrics.grade_of(score),
        })
    rows.sort(key=lambda item: -(item.get("achievement") or 0))
    if rows:
        top = rows[0].get("achievement") or 0
        for row in rows:
            row["bar"] = round((row.get("achievement") or 0) / top * 100, 1) if top else 0
    return rows


def horizontal_levels(store):
    """横向 · 层级维度：公司级 / 部门级 / 个人级。"""
    buckets = {}
    for objective in store.get("objectives", []):
        if objective.get("status") not in c_metrics.VERIFIED_STATUSES:
            continue
        label = objective.get("level_label") or "个人级"
        buckets.setdefault(label, []).append(objective)
    order = {"公司级": 0, "部门级": 1, "个人级": 2}
    rows = []
    for label, objectives in buckets.items():
        score = c_metrics.weighted_objective_average(objectives)
        rows.append({
            "label": label,
            "objective_count": len(objectives),
            "achievement": score,
            "grade": c_metrics.grade_of(score),
        })
    rows.sort(key=lambda item: order.get(item["label"], 9))
    return rows


def compare_payload(store, users, series_id, dim):
    index = series_index(store)
    series_list = []
    for key, objectives in index.items():
        scored = [item for item in objectives if merged_score(item) is not None]
        latest = max(objectives, key=lambda item: (item.get("cycle_id") or ""))
        series_list.append({
            "series_id": key,
            "title": objectives[0].get("title"),
            "cycles": len(objectives),
            "owner": (next((u["name"] for u in users if u["id"] == latest.get("user_id")), "未指派")),
            "latest_score": merged_score(latest),
        })
    series_list.sort(key=lambda item: (-item["cycles"], item["title"] or ""))
    if not series_list:
        series_id = ""
    elif not series_id or series_id not in index:
        series_id = series_list[0]["series_id"]

    vertical = vertical_rows(store, index.get(series_id, []), series_id) if series_id else \
        {"series_id": "", "title": "—", "rows": [], "warnings": [], "delta": None}

    rates = c_metrics.three_rates(store.get("objectives", []), store.get("tasks", []))
    return {
        "dim": dim,
        "series_list": series_list,
        "current_series": series_id,
        "vertical": vertical,
        "horizontal_users": horizontal_users(store, users),
        "horizontal_levels": horizontal_levels(store),
        "rates": rates,
        "rates_note": c_metrics.rates_note(),
        "grade_bands": c_metrics.grade_bands_text(),
    }


def register(app, services):
    read_store = services.read_store
    read_users = services.read_users

    @app.get("/compare")
    def compare():
        series_id = request.args.get("series", "")
        dim = request.args.get("dim", "user")
        if dim not in ("user", "level"):
            dim = "user"
        return render_template(
            "pages/compare.html",
            active_page="compare",
            page_name="对比分析",
            **compare_payload(read_store(), read_users(), series_id, dim)
        )
