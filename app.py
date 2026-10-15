"""智能计划管理 Agent —— Flask MPA 唯一启动入口。

公共骨架约定（见 docs/智能计划管理Agent_软需说明书 1.5）：
- 初始化、路由注册、通用校验、线程锁、数据读写集中在本文件；
- 各业务线只在 backend/ 下新增自己的路由模块，由本文件统一 import；
- 数据读写沿用「读取 → 校验 → 加锁写 → 刷新」闭环，不引入数据库。

数据载体：
- data/users.json   用户（旧演示数据，保留不动）
- data/plans.json   Plan / Todo / Task / Reminder / Feedback / Exception
- data/settings.json 系统设置
"""

import json
import os
import re
import tempfile
import threading
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, render_template, request


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR
DATA_DIR = PROJECT_DIR / "data"
USERS_FILE = DATA_DIR / "users.json"
STORE_FILE = DATA_DIR / "plans.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

# 请求线程与后台巡检线程共用同一把锁，保证并发写入不互相覆盖
DATA_LOCK = threading.Lock()

ALLOWED_ROLES = {"管理员", "运营", "编辑", "普通用户"}
ALLOWED_STATUSES = {"启用", "停用"}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

EMPTY_STORE = {
    "plans": [],
    "todos": [],
    "tasks": [],
    "reminders": [],
    "feedbacks": [],
    "exceptions": [],
    "logs": [],
    "scan_state": {},
}

environment = os.environ.get("APP_ENV", "production").strip().lower()
is_development = environment == "development"

app = Flask(
    __name__,
    template_folder=str(PROJECT_DIR / "frontend" / "templates"),
    static_folder=str(PROJECT_DIR / "frontend" / "static"),
)
app.secret_key = os.environ.get("SECRET_KEY", "local-demo-only-change-me")
app.config["APP_ENV"] = environment
app.config["IS_DEVELOPMENT"] = is_development


# --------------------------------------------------------------------------
# 通用 JSON 读写（原子写入，供各业务线复用）
# --------------------------------------------------------------------------

def read_json(path, default):
    """读取 JSON；文件缺失或损坏时回退到默认值，避免聚合逻辑被打断。"""
    if not path.exists():
        return json.loads(json.dumps(default))
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(default))


def write_json(path, payload):
    """原子写入：先写临时文件并 fsync，再 os.replace 覆盖，避免写一半损坏数据。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(prefix=".%s-" % path.stem, suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


# --------------------------------------------------------------------------
# 用户（旧演示数据，保留原有能力）
# --------------------------------------------------------------------------

def read_users():
    return read_json(USERS_FILE, [])


def write_users(users):
    write_json(USERS_FILE, users)


def form_data():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    role = request.form.get("role", "").strip()
    status = request.form.get("status", "").strip()

    if not name or len(name) > 40:
        return None, "姓名不能为空，且不能超过 40 个字符。"
    if len(email) > 120 or not EMAIL_PATTERN.fullmatch(email):
        return None, "请输入有效的邮箱地址。"
    if len(phone) > 30:
        return None, "电话不能超过 30 个字符。"
    if role not in ALLOWED_ROLES or status not in ALLOWED_STATUSES:
        return None, "角色或状态无效。"
    return {"name": name, "email": email, "phone": phone, "role": role, "status": status}, None


# --------------------------------------------------------------------------
# 计划数据与设置（C 线及后续业务线共用）
# --------------------------------------------------------------------------

settings_io = import_module("backend.settings_io")


def read_store():
    """读取计划数据；缺失字段自动补齐，保证下游聚合不出现 KeyError。"""
    store = read_json(STORE_FILE, EMPTY_STORE)
    for key, value in EMPTY_STORE.items():
        store.setdefault(key, json.loads(json.dumps(value)))
    return store


def write_store(store):
    write_json(STORE_FILE, store)


def read_settings():
    settings = read_json(SETTINGS_FILE, settings_io.default_settings())
    defaults = settings_io.default_settings()
    for key, value in defaults.items():
        settings.setdefault(key, value)
    # 渠道数量与定义不一致时回退默认，避免页面按索引取值时越界
    if len(settings.get("channels") or []) != len(settings_io.CHANNEL_DEFS):
        settings["channels"] = defaults["channels"]
    return settings


def write_settings(settings):
    write_json(SETTINGS_FILE, settings)


services = SimpleNamespace(
    # 用户
    read_users=read_users,
    write_users=write_users,
    form_data=form_data,
    allowed_statuses=ALLOWED_STATUSES,
    # 计划数据与设置
    read_store=read_store,
    write_store=write_store,
    read_settings=read_settings,
    write_settings=write_settings,
    store_lock=DATA_LOCK,
    data_lock=DATA_LOCK,
)

# 每条业务线各占一个路由模块；在此统一注册，启动始终只有一条命令。
for module_name in (
    "backend.1", "backend.2", "backend.3", "backend.4",   # 旧演示：用户管理
    "backend.plan",                                        # A1/A2 计划录入与提炼确认
    "backend.schedule",                                    # A3 智能排期
    "backend.task_ops",                                    # B1/B2 任务列表与提醒中心
    "backend.scheduler",                                   # B3 后台定时巡检
    "backend.stats",                                       # C1 驾驶舱
    "backend.data_io",                                     # C2 数据管理
    "backend.settings_io",                                 # C3 系统设置
    "backend.hub",                                         # D 线 AI 任务中枢（跨线聚合 + 卡点研判）
):
    import_module(module_name).register(app, services)


def page_context(active_page, page_name):
    users = read_users()
    return {
        "active_page": active_page,
        "page_name": page_name,
        "total": len(users),
        "active": sum(user["status"] == "启用" for user in users),
        "admins": sum(user["role"] == "管理员" for user in users),
    }


@app.context_processor
def inject_reminder_badge():
    store = read_store()
    unread = sum(1 for item in store.get("reminders", []) if item.get("status") == "未读")
    return {"unread_reminders": unread}


@app.get("/analytics")
def analytics():
    return render_template("pages/analytics.html", **page_context("analytics", "数据报表"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=app.config["IS_DEVELOPMENT"])
