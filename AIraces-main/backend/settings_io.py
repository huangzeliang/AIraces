"""C 线 · 系统设置（C3）：主题、视图角色、巡检间隔、免打扰与通知渠道配置。

对应需求：REQ-025 / 026 / 027 / 029 / 030（F-1004 / 1005 / 1101 / 1103 / 1104）
归属文件：backend/settings_io.py · templates/pages/settings.html · css|js/pages/settings.*

配置落库沿用全局约定：读取 → 校验 → 加锁写 → 刷新展示。
通知渠道为「配置占位」：保存开关与 Webhook，不做真实投递（D9）。
"""

import json
from datetime import datetime
from urllib.parse import urlparse

from flask import Response, flash, jsonify, redirect, render_template, request, url_for

ROLE_OPTIONS = ("普通用户", "管理员")
THEME_OPTIONS = ("day", "night")
CHANNEL_DEFS = (
    ("page", "页面弹窗", "系统内弹出提示条，默认开启"),
    ("desktop", "桌面通知", "需要浏览器授权；被拒时自动降级为页面弹窗"),
    ("wecom", "企业微信", "配置 Webhook 占位，演示环境不真实投递"),
    ("dingtalk", "钉钉", "配置 Webhook 占位，演示环境不真实投递"),
)

DEFAULT_SETTINGS = {
    "display_name": "魏先生",
    "view_role": "普通用户",
    "theme": "day",
    "reminder_interval_minutes": 240,
    "dnd_start": "12:00",
    "dnd_end": "14:00",
    "channels": [
        {"channel_key": key, "channel_name": name, "enabled": key == "page", "webhook_url": ""}
        for key, name, _ in CHANNEL_DEFS
    ],
}

SHORTCUTS = (
    ("T", "切换白天 / 夜晚主题"),
    ("G 然后 D", "前往驾驶舱"),
    ("G 然后 M", "前往数据管理"),
    ("G 然后 S", "前往系统设置"),
    ("Esc", "关闭当前弹窗"),
)


def default_settings():
    """深拷贝一份默认配置，避免调用方污染模块常量。"""
    return json.loads(json.dumps(DEFAULT_SETTINGS, ensure_ascii=False))


def _valid_time(value):
    if not value:
        return True
    try:
        datetime.strptime(value, "%H:%M")
        return True
    except ValueError:
        return False


def _collect(form):
    """把表单收集为配置对象，同时返回错误列表。"""
    errors = []
    display_name = (form.get("display_name") or "").strip()
    if not display_name:
        errors.append("显示名不能为空。")
    elif len(display_name) > 30:
        errors.append("显示名不能超过 30 个字符。")

    view_role = form.get("view_role") or "普通用户"
    if view_role not in ROLE_OPTIONS:
        errors.append("视图角色取值无效。")

    interval_raw = str(form.get("reminder_interval_minutes") or "").strip()
    interval = None
    if not interval_raw.isdigit() or int(interval_raw) <= 0:
        errors.append("巡检间隔必须是大于 0 的整数（分钟）。")
    else:
        interval = int(interval_raw)

    dnd_start = (form.get("dnd_start") or "").strip()
    dnd_end = (form.get("dnd_end") or "").strip()
    if not _valid_time(dnd_start) or not _valid_time(dnd_end):
        errors.append("免打扰时间格式应为 HH:mm。")
    elif dnd_start and dnd_end and dnd_start > dnd_end:
        errors.append("免打扰开始时间不能晚于结束时间。")

    channels = []
    for key, name, _ in CHANNEL_DEFS:
        webhook = (form.get("webhook_%s" % key) or "").strip()
        if webhook:
            parsed = urlparse(webhook)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                errors.append("「%s」的 Webhook 地址格式无效。" % name)
        channels.append({
            "channel_key": key,
            "channel_name": name,
            "enabled": form.get("channel_%s" % key) == "on",
            "webhook_url": webhook,
        })

    theme = form.get("theme") or "day"
    if theme not in THEME_OPTIONS:
        theme = "day"

    payload = {
        "display_name": display_name,
        "view_role": view_role,
        "theme": theme,
        "reminder_interval_minutes": interval if interval else DEFAULT_SETTINGS["reminder_interval_minutes"],
        "dnd_start": dnd_start,
        "dnd_end": dnd_end,
        "channels": channels,
    }
    return payload, errors


def register(app, services):
    read_settings = services.read_settings
    write_settings = services.write_settings
    store_lock = services.store_lock

    def _render():
        settings = read_settings()
        return render_template(
            "pages/settings.html",
            active_page="settings",
            page_name="系统设置",
            settings=settings,
            channel_defs=CHANNEL_DEFS,
            role_options=ROLE_OPTIONS,
            shortcuts=SHORTCUTS,
            defaults=DEFAULT_SETTINGS,
        )

    @app.get("/settings")
    def settings():
        return _render()

    @app.post("/settings/save")
    def save_settings():
        json_payload = request.get_json(silent=True)
        payload, errors = _collect(json_payload if json_payload is not None else request.form)
        if errors:
            if json_payload is not None:
                return jsonify({"ok": False, "message": "；".join(errors), "errors": errors}), 400
            for message in errors:
                flash(message, "error")
            return _render()

        with store_lock:
            write_settings(payload)
        if json_payload is not None:
            return jsonify({"ok": True, "message": "设置已保存。"})
        flash("设置已保存。", "success")
        return redirect(url_for("settings"))

    @app.post("/settings/reset")
    def reset_settings():
        with store_lock:
            write_settings(default_settings())
        flash("已恢复默认设置。", "success")
        return redirect(url_for("settings"))

    @app.get("/settings/export")
    def export_settings():
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        payload = {"exported_at": datetime.now().replace(microsecond=0).isoformat(), "settings": read_settings()}
        return Response(
            json.dumps(payload, ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=settings_%s.json" % stamp},
        )
