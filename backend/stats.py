"""C 线 · 驾驶舱（C1）：KPI 聚合、图表数据、北极星拆解与报表导出。

对应需求：REQ-011 / 012 / 013 / 028（F-601~604 · F-701/702 · F-1102）
归属文件：backend/stats.py · templates/pages/dashboard.html · css|js/pages/dashboard.*

指标口径（Q-10 / Q-11 未定，先按下列示例值实现，页面同步标注）：
- 统计周期 period 以任务的「截止时间 due_at」落入区间为准，切换周期即重算全部 KPI 与图表；
- 完成率 completion_rate = 周期内已完成 / 周期内总数（四舍五入为百分数整数）；
- 延期数 delay_count = 周期内 delay_count > 0 的任务数；
- 今日到期 due_today 为全局口径，不随周期变化。
"""

from datetime import datetime, timedelta

from flask import Response, render_template, request

PERIODS = ("今日", "本周", "本月")
# 导出文件名用 ASCII 键，避免 HTTP 头中的中文在下载时乱码
PERIOD_KEYS = {"今日": "today", "本周": "week", "本月": "month"}
STATUS_ORDER = ("待安排", "待执行", "进行中", "已完成", "已延期", "已取消")
PRIORITY_ORDER = ("高", "中", "低")
WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _parse(stamp):
    """ISO8601 字符串转 datetime；解析失败返回 None，保证聚合过程不抛异常。"""
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


def _period_range(period, now):
    """按周期返回左闭右开区间 [start, end)。"""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "本周":
        start = today - timedelta(days=now.weekday())
        return start, start + timedelta(days=7)
    if period == "本月":
        start = today.replace(day=1)
        return start, (start + timedelta(days=32)).replace(day=1)
    return today, today + timedelta(days=1)


def _within(moment, start, end):
    return moment is not None and start <= moment < end


def _same_day(moment, day):
    return moment is not None and moment.date() == day


def _build_trend(tasks, start, end):
    """周期内逐日统计「新增 / 完成」，供原生 SVG 折线图使用。"""
    series, cursor = [], start
    while cursor < end:
        created = sum(1 for t in tasks if _same_day(_parse(t.get("created_at")), cursor.date()))
        completed = sum(
            1 for t in tasks
            if t.get("status") == "已完成" and _same_day(_parse(t.get("completed_at")), cursor.date())
        )
        series.append(
            {
                "date": cursor.date().isoformat(),
                "label": "%d/%d" % (cursor.month, cursor.day),
                "created": created,
                "completed": completed,
            }
        )
        cursor += timedelta(days=1)
    return series


def _build_dist(tasks, field, order):
    """按枚举维度统计数量，保持枚举顺序稳定。"""
    counts = {key: 0 for key in order}
    for task in tasks:
        value = task.get(field)
        if value in counts:
            counts[value] += 1
    return [{"dimension": key, "count": counts[key]} for key in order]


def _build_rank(tasks, field, reverse=True, size=5):
    """排行榜：最耗时 / 最常延期。"""
    ranked = sorted(tasks, key=lambda t: int(t.get(field) or 0), reverse=reverse)
    ranked = [t for t in ranked if int(t.get(field) or 0) > 0][:size]
    return [
        {
            "task_id": t.get("task_id", ""),
            "title": t.get("title", ""),
            "value": int(t.get(field) or 0),
            "status": t.get("status", ""),
        }
        for t in ranked
    ]


def _build_north_star(tasks, kpi):
    """北极星指标与一层子指标拆解（F-701 / F-702）。"""
    total = len(tasks)
    done = [t for t in tasks if t.get("status") == "已完成"]
    on_time = [t for t in done if _parse(t.get("completed_at")) and _parse(t.get("due_at"))
               and _parse(t.get("completed_at")) <= _parse(t.get("due_at"))]
    delayed = [t for t in tasks if int(t.get("delay_count") or 0) > 0]
    active_plans = len({t.get("plan_id") for t in tasks if t.get("plan_id")})

    rate = round(len(on_time) / len(done) * 100) if done else 0
    return {
        "north_star_metric": "计划按时完成率",
        "value": rate,
        "unit": "%",
        "detail": "已完成任务中，实际完成时间不晚于截止时间的占比",
        "sub_metrics": [
            {
                "key": "completion_rate",
                "name": "完成率",
                "value": kpi["completion_rate"],
                "unit": "%",
                "hint": "周期内已完成 / 周期内总数",
                "children": [
                    {"name": "已完成任务", "value": len(done), "unit": "项"},
                    {"name": "周期内任务", "value": kpi["task_total"], "unit": "项"},
                ],
            },
            {
                "key": "delay_rate",
                "name": "延期率",
                "value": round(len(delayed) / total * 100) if total else 0,
                "unit": "%",
                "hint": "发生过延期的任务占全部任务的比例",
                "children": [
                    {"name": "延期任务", "value": len(delayed), "unit": "项"},
                    {"name": "延期总次数", "value": sum(int(t.get("delay_count") or 0) for t in tasks), "unit": "次"},
                ],
            },
            {
                "key": "plan_usage",
                "name": "计划使用频次",
                "value": active_plans,
                "unit": "个",
                "hint": "当前纳入管理的计划数量",
                "children": [
                    {"name": "任务总量", "value": total, "unit": "项"},
                    {"name": "平均每计划任务", "value": round(total / active_plans, 1) if active_plans else 0, "unit": "项"},
                ],
            },
        ],
    }


def _build_report(store, period, kpi, priority_dist, status_dist):
    """AI 周报（D4）：预生成样例，不接实时 LLM。"""
    tasks = store.get("tasks", [])
    top_priority = max(priority_dist, key=lambda item: item["count"])
    delayed_titles = [t.get("title", "") for t in tasks if int(t.get("delay_count") or 0) > 0][:2]
    lines = [
        "%s执行情况：当前纳入统计的任务 %d 项，其中已完成 %d 项，完成率 %d%%。"
        % (period, kpi["task_total"], round(kpi["task_total"] * kpi["completion_rate"] / 100), kpi["completion_rate"]),
        "任务集中在「%s」优先级，共 %d 项，建议保持当前的投入节奏。" % (top_priority["dimension"], top_priority["count"]),
        "今日有 %d 项任务到期，请优先确认交付状态。" % kpi["due_today"],
    ]
    if delayed_titles:
        lines.append("延期风险集中在：%s。建议复盘其时间估算口径。" % "、".join("「%s」" % t for t in delayed_titles))
    else:
        lines.append("当前没有延期任务，排期执行健康。")
    return {
        "period": period,
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "lines": lines,
        "metrics": [
            {"name": "任务总数", "value": kpi["task_total"], "unit": "项"},
            {"name": "完成率", "value": kpi["completion_rate"], "unit": "%"},
            {"name": "今日到期", "value": kpi["due_today"], "unit": "项"},
            {"name": "延期数", "value": kpi["delay_count"], "unit": "项"},
        ],
    }


def _build_recommend(kpi, delayed_rank, time_rank, status_dist):
    """智能推荐（D5）：固定 3 条可解释建议。"""
    recs = []
    if kpi["due_today"]:
        recs.append({
            "title": "先清今日到期",
            "reason": "今日有 %d 项任务到期，属于当天不可回退的时间点。" % kpi["due_today"],
        })
    if delayed_rank:
        top = delayed_rank[0]
        recs.append({
            "title": "复盘「%s」" % top["title"],
            "reason": "该任务已延期 %d 次，在同类任务中最高。" % top["value"],
        })
    if time_rank:
        top = time_rank[0]
        recs.append({
            "title": "拆分「%s」" % top["title"],
            "reason": "预计耗时 %d 分钟，是当前最重的单点任务，建议拆成子任务。" % top["value"],
        })
    doing = next((item["count"] for item in status_dist if item["dimension"] == "进行中"), 0)
    if doing:
        recs.append({
            "title": "收敛进行中任务",
            "reason": "当前有 %d 项处于进行中，建议控制在 3 项以内以减少切换成本。" % doing,
        })
    return recs[:3]


def build_dashboard(store, period, now=None):
    """一次性聚合驾驶舱所需的全部数据。"""
    now = now or datetime.now().replace(microsecond=0)
    tasks = store.get("tasks", [])
    start, end = _period_range(period, now)

    scoped = [t for t in tasks if _within(_parse(t.get("due_at")), start, end)]
    done = [t for t in scoped if t.get("status") == "已完成"]
    delayed = [t for t in scoped if int(t.get("delay_count") or 0) > 0]

    kpi = {
        "task_total": len(scoped),
        "due_today": sum(1 for t in tasks if _same_day(_parse(t.get("due_at")), now.date())),
        "completion_rate": round(len(done) / len(scoped) * 100) if scoped else 0,
        "delay_count": len(delayed),
    }

    priority_dist = _build_dist(scoped, "priority", PRIORITY_ORDER)
    status_dist = _build_dist(scoped, "status", STATUS_ORDER)
    delayed_rank = _build_rank(tasks, "delay_count")
    time_rank = _build_rank(tasks, "estimate_minutes")

    return {
        "period": period,
        "range": {"start": start.date().isoformat(), "end": (end - timedelta(days=1)).date().isoformat()},
        "kpi": kpi,
        "trend": _build_trend(tasks, start, end),
        "priority_dist": priority_dist,
        "status_dist": status_dist,
        "delayed_rank": delayed_rank,
        "time_rank": time_rank,
        "north_star": _build_north_star(tasks, kpi),
        "report": _build_report(store, period, kpi, priority_dist, status_dist),
        "recommend": _build_recommend(kpi, delayed_rank, time_rank, status_dist),
        "bot_dialog": [
            {"role": "user", "text": "今天我最该做什么？"},
            {"role": "bot", "text": "优先处理今日到期的 %d 项任务，再跟进延期次数最高的那一项。" % kpi["due_today"]},
            {"role": "user", "text": "我的计划健康吗？"},
            {"role": "bot", "text": "当前完成率 %d%%，延期 %d 项。整体可控，建议把预计耗时最长的任务拆细。" % (kpi["completion_rate"], kpi["delay_count"])},
        ],
        "has_data": bool(tasks),
    }


def _rows_for_export(data):
    rows = [["指标", "数值", "单位"]]
    for name, value, unit in (
        ("任务总数", data["kpi"]["task_total"], "项"),
        ("今日到期", data["kpi"]["due_today"], "项"),
        ("完成率", data["kpi"]["completion_rate"], "%"),
        ("延期数", data["kpi"]["delay_count"], "项"),
        ("北极星·%s" % data["north_star"]["north_star_metric"], data["north_star"]["value"], data["north_star"]["unit"]),
    ):
        rows.append([name, str(value), unit])
    rows.append(["", "", ""])
    rows.append(["趋势·日期", "新增", "完成"])
    for point in data["trend"]:
        rows.append([point["date"], str(point["created"]), str(point["completed"])])
    return rows


def register(app, services):
    read_store = services.read_store
    read_settings = services.read_settings

    def _render(period):
        data = build_dashboard(read_store(), period)
        return render_template(
            "pages/dashboard.html",
            active_page="dashboard",
            page_name="驾驶舱",
            periods=PERIODS,
            settings=read_settings(),
            **data
        )

    @app.get("/dashboard")
    def dashboard():
        period = request.args.get("period", "本周")
        if period not in PERIODS:
            period = "本周"
        return _render(period)

    @app.get("/dashboard/export")
    def export_dashboard():
        period = request.args.get("period", "本周")
        if period not in PERIODS:
            period = "本周"
        data = build_dashboard(read_store(), period)
        key = PERIOD_KEYS.get(period, "week")

        if request.args.get("format", "csv") == "json":
            import json

            payload = json.dumps(data, ensure_ascii=False, indent=2)
            return Response(
                payload,
                mimetype="application/json",
                headers={"Content-Disposition": "attachment; filename=dashboard_%s.json" % key},
            )

        lines = [",".join('"%s"' % cell.replace('"', '""') for cell in row) for row in _rows_for_export(data)]
        return Response(
            "\n".join(lines),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=dashboard_%s.csv" % key},
        )
