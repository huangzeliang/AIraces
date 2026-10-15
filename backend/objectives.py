"""C 线 · 目标全景驾驶舱（C4）。

对应需求：AC-30 三率分离 + 加权汇总 + 需关注人员 · 员工不渲染本页
归属文件：backend/objectives.py · templates/pages/objectives.html · css|js/pages/objectives.*

要点：
- 目标全景按层级铺开，红黄绿灯直接指向需要干预的目标；
- 达成度按目标权重加权（调用 backend.c_metrics），不做简单平均；
- 「需关注人员」判定：已定案加权达成度低于阈值，或长期未更新目标；
- 页面仅管理员可见（在 backend.auth 的 ADMIN_PATH_PREFIXES 中）。
"""

from datetime import datetime
from importlib import import_module

from flask import render_template

c_metrics = import_module("backend.c_metrics")

STALE_DAYS = 5          # 超过该天数没有进展更新视为长期未更新
WATCH_SCORE = 78.0      # 已定案加权达成度低于该值进入需关注
RISK_SCORE = 60.0       # 单目标达成度低于该值标红

LEVEL_ORDER = ((1, "公司级"), (2, "部门级"), (3, "个人级"))


def parse_dt(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def days_since(value):
    moment = parse_dt(value)
    if not moment:
        return None
    return (datetime.now() - moment).days


def traffic_light(score):
    if score is None:
        return "none"
    if score >= 90:
        return "green"
    if score >= 75:
        return "amber"
    return "red"


def build(store, users):
    name_map = {item["id"]: item for item in users}
    cycles = {item["cycle_id"]: item for item in store.get("cycles", [])}
    reviews = {item.get("objective_id"): item for item in store.get("reviews", [])}
    retros = {item.get("objective_id"): item for item in store.get("retros", [])}

    objectives = [item for item in store.get("objectives", [])
                  if item.get("status") != c_metrics.OBJ_DRAFT]
    by_id = {item.get("objective_id"): item for item in objectives}

    nodes = []
    for objective in objectives:
        review = reviews.get(objective.get("objective_id"))
        score = review.get("achievement") if review else c_metrics.objective_achievement(objective, "final")
        verified = objective.get("status") in c_metrics.VERIFIED_STATUSES
        stale = days_since(objective.get("updated_at"))
        parent = by_id.get(objective.get("parent_id"))
        nodes.append({
            "objective_id": objective.get("objective_id"),
            "title": objective.get("title"),
            "level": objective.get("level") or 3,
            "level_label": objective.get("level_label") or "个人级",
            "owner": (name_map.get(objective.get("user_id")) or {}).get("name") or "未指派",
            "owner_id": objective.get("user_id"),
            "cycle": (cycles.get(objective.get("cycle_id")) or {}).get("name") or "—",
            "weight": objective.get("weight"),
            "status": objective.get("status"),
            "score": score,
            "light": "none" if not verified else traffic_light(score),
            "verified": verified,
            "has_retro": objective.get("objective_id") in retros,
            "updated_at": objective.get("updated_at"),
            "stale_days": stale,
            "stale": stale is not None and stale > STALE_DAYS and not verified,
            "parent_title": (parent or {}).get("title") or "",
            "kr_total": len(objective.get("key_results") or []),
            "risk": verified and score is not None and score < RISK_SCORE,
        })

    # 层级分组（树形铺开）
    groups = []
    for level, label in LEVEL_ORDER:
        rows = [node for node in nodes if node["level"] == level]
        rows.sort(key=lambda item: (item["parent_title"] or "", -(item["score"] or 0)))
        groups.append({
            "level": level,
            "label": label,
            "count": len(rows),
            "score": c_metrics.weighted_objective_average(
                [by_id[node["objective_id"]] for node in rows
                 if node["verified"] and node["objective_id"] in by_id]),
            "rows": rows,
        })

    # 需关注人员
    owners = {}
    for node in nodes:
        key = node["owner_id"] or ""
        entry = owners.setdefault(key, {"name": node["owner"], "total": 0, "verified": 0,
                                        "scores": [], "stale": 0, "max_stale": 0})
        entry["total"] += 1
        if node["verified"] and node["score"] is not None:
            entry["verified"] += 1
            entry["scores"].append((node["score"], node["weight"] or 1))
        if node["stale"]:
            entry["stale"] += 1
            entry["max_stale"] = max(entry["max_stale"], node["stale_days"] or 0)

    watch = []
    for key, entry in owners.items():
        weight_sum = sum(weight for _score, weight in entry["scores"])
        score = round(sum(s * w for s, w in entry["scores"]) / weight_sum, 1) if weight_sum else None
        reasons = []
        if score is not None and score < WATCH_SCORE:
            reasons.append("已定案加权达成度 %.1f，低于关注线 %.0f" % (score, WATCH_SCORE))
        if entry["stale"]:
            reasons.append("%d 项目标超过 %d 天未更新（最久 %d 天）"
                           % (entry["stale"], STALE_DAYS, entry["max_stale"]))
        if reasons:
            watch.append({
                "owner_id": key,
                "name": entry["name"],
                "total": entry["total"],
                "verified": entry["verified"],
                "score": score,
                "grade": c_metrics.grade_of(score),
                "reasons": reasons,
            })
    watch.sort(key=lambda item: (item["score"] if item["score"] is not None else 999))

    verified_nodes = [node for node in nodes if node["verified"]]
    summary = {
        "objective_total": len(nodes),
        "verified_total": len(verified_nodes),
        "achievement": c_metrics.weighted_objective_average(
            [by_id[node["objective_id"]] for node in verified_nodes]),
        "risk_total": sum(1 for node in nodes if node["risk"]),
        "stale_total": sum(1 for node in nodes if node["stale"]),
        "watch_total": len(watch),
        "retro_pending": sum(1 for node in verified_nodes if not node["has_retro"]),
    }

    rates = c_metrics.three_rates(store.get("objectives", []), store.get("tasks", []))
    cycle_trend = []
    ordered_cycles = sorted(store.get("cycles", []), key=lambda item: item.get("start_at") or "")
    for cycle in ordered_cycles:
        scoped = [item for item in objectives
                  if item.get("cycle_id") == cycle.get("cycle_id")
                  and item.get("status") in c_metrics.VERIFIED_STATUSES]
        score = c_metrics.weighted_objective_average(scoped) if scoped else None
        cycle_trend.append({
            "cycle_name": cycle.get("name"),
            "score": score,
            "count": len(scoped),
            "has_data": score is not None,
        })

    return {
        "groups": groups,
        "summary": summary,
        "watch": watch,
        "rates": rates,
        "rates_note": c_metrics.rates_note(),
        "cycle_trend": cycle_trend,
        "stale_days": STALE_DAYS,
        "watch_score": WATCH_SCORE,
        "grade_bands": c_metrics.grade_bands_text(),
    }


def register(app, services):
    read_store = services.read_store
    read_users = services.read_users

    @app.get("/objectives")
    def objectives():
        store = read_store()
        return render_template(
            "pages/objectives.html",
            active_page="objectives",
            page_name="目标全景",
            **build(store, read_users())
        )
