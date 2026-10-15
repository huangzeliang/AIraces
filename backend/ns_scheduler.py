"""NS 巡检调度（F-206 / F-207 / F-220 / F-413）：

- 连续三日异常（F-413）：按 2.7.4 双角色文案生成提醒，员工 + 直接上级各一条，
  以同一 trigger_id 关联；
- 抑制规则：同窗口去重（第 4 天起每 2 天汇总）、已知悉 3 天抑制、阻塞日豁免；
- 任务三色预警与检查点（F-206 / F-220）落地为提醒，按 dedupe_key 永久去重；
- scan_once() 全量巡检；alert_chain() 单人 / 全体补偿计算（页面加载与演示触发）；
- register() 守护线程：首跑 10s，之后每 600s（10 分钟，与需求 2.7.3 一致）。
"""

import os
import threading
import time
import traceback
from datetime import datetime, timedelta

from backend.core import DATA_LOCK, fail, f1, iso, now, read_store, write_store
from backend.ns_common import (
    assignee_ids,
    compute_task_alerts,
    create_reminder,
    direct_manager,
    in_visible,
    is_manager,
    plan_items,
    role_scope,
    streak_info,
    task_by_id,
    user_by_id,
)

SCAN_INTERVAL = 600
FIRST_DELAY = 10
ACK_SUPPRESS_DAYS = 3

_scan_lock = threading.Lock()
_started = False


# ---------------------------------------------------------------------------
# 连续异常判定与文案（2.7.4）
# ---------------------------------------------------------------------------

def _bucket(days):
    """同窗口去重桶：<4 天只推 1 次；第 4 天起每 2 天汇总一条。"""
    if days < 4:
        return "w"
    return "b%d" % (days // 2)


def _acked_recently(store, recipient_id, trigger_id, moment=None):
    """收件人 3 天内点过「已知悉」→ 抑制该触发的新提醒。"""
    moment = moment or now()
    for item in store["reminder"]:
        if item.get("user_id") != recipient_id or item.get("trigger_id") != trigger_id:
            continue
        acked = item.get("ack_at")
        if not acked:
            continue
        try:
            when = datetime.fromisoformat(acked)
        except (TypeError, ValueError):
            continue
        if moment - when <= timedelta(days=ACK_SUPPRESS_DAYS):
            return True
    return False


def _today_remaining(store, user_id):
    """今日计划的剩余项与建议优先项（保持与 B6 日计划同一份数据）。"""
    day = now().date().isoformat()
    plan = next(
        (p for p in store["daily_plan"] if p.get("user_id") == user_id and p.get("plan_date") == day),
        None,
    )
    if not plan:
        return 0, ""
    items = [item for item in plan_items(store, plan["plan_id"]) if not item.get("done")]
    suggestion = ""
    best_end = None
    for item in items:
        task = task_by_id(store, item.get("task_id"))
        if not task:
            continue
        end = task.get("plan_end") or "9999"
        if best_end is None or end < best_end:
            best_end = end
            suggestion = task["title"]
    return len(items), suggestion


def _critical_facts(store, user_id):
    """管理者文案用：关键路径任务数 + 热点延期传导天数。"""
    from backend import ns_deps

    snapshot = ns_deps.critical_snapshot(store)
    critical_ids = snapshot.get("critical_ids") or set()
    my_open = [
        t for t in store["work_task"]
        if t.get("status") not in ("done", "cancelled") and user_id in assignee_ids(t)
    ]
    on_critical = [t for t in my_open if t["task_id"] in critical_ids]
    hotspot = snapshot.get("hotspot")
    impact_days = 0
    hotspot_title = ""
    if hotspot and hotspot.get("task_id") in {t["task_id"] for t in my_open}:
        hotspot_title = hotspot.get("title") or ""
        impact_days = int((hotspot.get("impact") or {}).get("project_delay_days") or 0)
    return {"critical_count": len(on_critical), "hint": snapshot.get("hint", ""),
            "hotspot_title": hotspot_title, "impact_days": impact_days}


def streak_rows(store, base_date=None, user_id=""):
    """连续异常行（员工版 + 管理者版），未落库，供补偿计算与巡检共用。"""
    targets = [user_id] if user_id else [u["user_id"] for u in store["user"] if u.get("status") == "启用"]
    rows = []
    for uid in targets:
        person = user_by_id(store, uid)
        if not person or person.get("status") != "启用":
            continue
        info = streak_info(store, uid, base_date)
        if not info["triggered"]:
            continue
        trigger = "streak:%s:%s" % (uid, info["window_start"])
        bucket = _bucket(info["days"])
        level = "red" if info["days"] >= 5 else "yellow"
        remaining, suggestion = _today_remaining(store, uid)
        facts = _critical_facts(store, uid)
        common = {
            "trigger_id": trigger,
            "subject_id": uid,
            "subject_name": person["display_name"],
            "days": info["days"],
            "window_start": info["window_start"],
            "window_end": info["window_end"],
            "min_progress": info["min_progress"],
            "level": level,
            "threshold": info["threshold"],
            "bucket": bucket,
        }

        staff_body = "你已连续 %d 天有当日任务未完成（%s ~ %s，最低完成率 %s%%）。" % (
            info["days"], info["window_start"], info["window_end"], f1(info["min_progress"]))
        if remaining:
            staff_body += "今天还剩 %d 项" % remaining
            if suggestion:
                staff_body += "，建议先处理「%s」" % suggestion
            staff_body += "。"
        else:
            staff_body += "今天已无待办项，保持节奏即可。"
        rows.append(dict(common, role="staff", recipient_id=uid,
                         title="连续 %d 天未完成当日计划" % info["days"],
                         body=staff_body + "建议先处理关键路径上的任务；连续 ≥5 天将升级展示。",
                         meta={"remaining_today": remaining, "suggest_task": suggestion}))

        manager = direct_manager(store, person)
        if manager:
            manager_body = "%s（%s）连续 %d 天（%s ~ %s）当日完成率未达 100%%，最低 %s%%。" % (
                person["display_name"], person.get("position", ""), info["days"],
                info["window_start"], info["window_end"], f1(info["min_progress"]))
            if facts["critical_count"]:
                manager_body += "其手上有 %d 项任务处于「关键路径」上" % facts["critical_count"]
                if facts["hotspot_title"]:
                    manager_body += "，其中「%s」延期将导致整体延期 %d 天" % (
                        facts["hotspot_title"], facts["impact_days"])
                manager_body += "。"
            manager_body += "请关注并跟进，可发起 AI 过程指导或调整派工。"
            rows.append(dict(common, role="manager", recipient_id=manager["user_id"],
                             title="%s 连续 %d 天进度异常" % (person["display_name"], info["days"]),
                             body=manager_body,
                             meta={"critical_count": facts["critical_count"],
                                   "hotspot_title": facts["hotspot_title"],
                                   "impact_days": facts["impact_days"]}))
    return rows


def _deliver_streak_rows(store, rows):
    created = []
    for row in rows:
        if _acked_recently(store, row["recipient_id"], row["trigger_id"]):
            continue
        if row["role"] == "staff":
            dedupe = "streak:self:%s:%s:%s" % (row["subject_id"], row["window_start"], row["bucket"])
        else:
            dedupe = "streak:mgr:%s:%s:%s:%s" % (
                row["recipient_id"], row["subject_id"], row["window_start"], row["bucket"])
        entry = create_reminder(
            store, row["recipient_id"], "user", row["subject_id"], "daily_streak", row["level"],
            row["title"], row["body"], trigger_id=row["trigger_id"], role=row["role"],
            dedupe_key=dedupe,
        )
        if entry:
            entry["meta"] = row["meta"]
            entry["meta"]["days"] = row["days"]
            entry["meta"]["window_start"] = row["window_start"]
            entry["meta"]["window_end"] = row["window_end"]
            entry["meta"]["min_progress"] = row["min_progress"]
            entry["meta"]["subject_name"] = row["subject_name"]
            created.append({"reminder_id": entry["reminder_id"], "user_id": entry["user_id"],
                            "role": row["role"], "title": row["title"], "level": row["level"]})
    return created


# ---------------------------------------------------------------------------
# 任务三色预警 / 检查点 → 提醒
# ---------------------------------------------------------------------------

_ALERT_TITLE = {
    "overdue": "任务已超期：%s",
    "due_soon": "任务临近截止：%s",
    "checkpoint": "检查点提醒：%s",
    "deviation": "进度异常：%s",
}


def _deliver_task_alerts(store):
    """责任人收预警提醒；红级同时通知其直接上级。dedupe_key 保证同一预警不重复入库。"""
    all_user = next(
        (u for u in store["user"] if u.get("status") == "启用" and role_scope(store, u) == "ALL"),
        None,
    )
    created = []
    for alert in compute_task_alerts(store, all_user) if all_user else []:
        task = task_by_id(store, alert["target_id"])
        if not task:
            continue
        title = _ALERT_TITLE.get(alert["alert_type"], "任务预警：%s") % task["title"]
        recipients = set(assignee_ids(task))
        if alert["level"] == "red":
            for uid in list(recipients):
                person = user_by_id(store, uid)
                manager = direct_manager(store, person) if person else None
                if manager:
                    recipients.add(manager["user_id"])
        for uid in recipients:
            entry = create_reminder(
                store, uid, "task", task["task_id"], alert["alert_type"], alert["level"],
                title, alert["body"], dedupe_key="alert:%s:%s" % (alert["alert_id"], uid),
            )
            if entry:
                created.append({"reminder_id": entry["reminder_id"], "user_id": uid,
                                "title": title, "level": alert["level"]})
    return created


# ---------------------------------------------------------------------------
# 巡检入口
# ---------------------------------------------------------------------------

def scan_once():
    """全量巡检：连续异常双角色提醒 + 任务预警入库。不阻塞调用方（可由线程或同步调用）。"""
    if not _scan_lock.acquire(blocking=False):
        return {"skipped": True, "reason": "上一次巡检尚未结束"}
    try:
        with DATA_LOCK:
            store = read_store()
            rows = streak_rows(store)
            created = _deliver_streak_rows(store, rows)
            created += _deliver_task_alerts(store)
            if created:
                write_store(store)
        return {"skipped": False, "streak_rows": len(rows), "created": created,
                "created_count": len(created), "scanned_at": iso()}
    finally:
        _scan_lock.release()


def alert_chain(user_id="", as_of_date="", dry_run=False):
    """补偿计算（F-413 演示入口）：单人 / 全体连续异常判定，可 dry_run 只看不写。"""
    base = None
    if as_of_date:
        try:
            base = datetime.strptime(as_of_date, "%Y-%m-%d").date()
        except ValueError:
            fail(1001, "as_of_date 需为 YYYY-MM-DD 格式。")
    with DATA_LOCK:
        store = read_store()
        if user_id and not user_by_id(store, user_id):
            fail(1003, "用户不存在。")
        rows = streak_rows(store, base, user_id)
        created = []
        if not dry_run:
            created = _deliver_streak_rows(store, rows)
            if created:
                write_store(store)
    return {
        "as_of_date": (base or now().date()).isoformat(),
        "dry_run": bool(dry_run),
        "rows": [
            {key: row[key] for key in
             ("role", "recipient_id", "subject_id", "subject_name", "days", "window_start",
              "window_end", "min_progress", "level", "trigger_id", "title", "body")}
            for row in rows
        ],
        "created": created,
        "created_count": len(created),
    }


def streak_overview(store, user):
    """预警页顶部：范围内连续异常概览（员工看自己，管理者看成员）。"""
    rows = []
    if is_manager(store, user):
        targets = [u for u in store["user"] if u.get("status") == "启用" and in_visible(store, user, u["user_id"])]
    else:
        targets = [u for u in store["user"] if u["user_id"] == user["user_id"]]
    for person in targets:
        info = streak_info(store, person["user_id"])
        if info["days"] <= 0:
            continue
        rows.append({
            "user_id": person["user_id"], "user_name": person["display_name"],
            "is_self": person["user_id"] == user["user_id"], **info,
        })
    return sorted(rows, key=lambda row: row["days"], reverse=True)


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

def _loop():
    time.sleep(FIRST_DELAY)
    while True:
        try:
            scan_once()
        except Exception:  # noqa: BLE001 — 巡检失败不影响服务
            traceback.print_exc()
        time.sleep(SCAN_INTERVAL)


def register(app, services):
    global _started
    if _started or os.environ.get("NS_SCHEDULER") == "0":
        return
    _started = True
    threading.Thread(target=_loop, name="ns-scheduler", daemon=True).start()
