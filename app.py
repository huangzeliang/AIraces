"""智能计划管理 Agent —— Flask MPA 唯一启动入口。

公共骨架约定（见 docs/智能计划管理Agent_软需说明书 1.5）：
- 初始化、路由注册、通用校验、线程锁、数据读写集中在本文件；
- 各业务线只在 backend/ 下新增自己的路由模块，由本文件统一 import；
- 数据读写沿用「读取 → 校验 → 加锁写 → 刷新」闭环，不引入数据库。

数据载体（统一账号体系）：
- data/users.json   账号权威（姓名 / 角色 管理员|普通用户 / 状态 / 口令），登录与角色守卫的数据源
- data/system.json  目标逐级拆解与验证闭环（org/role/objective/work_task 等 23 张表，backend/core.py 管理）
- data/plans.json   Plan / Todo / Task / Reminder / Feedback / Exception（按 user_id 归属到账号）
- data/settings.json 系统设置
登录后由 sync_account 把 users.json 身份同步进 system.json 用户表，两线共用同一 session["user_id"]。
"""

import json
import os
import re
import tempfile
import threading
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, g, jsonify, render_template, request


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR
DATA_DIR = PROJECT_DIR / "data"
USERS_FILE = DATA_DIR / "users.json"
STORE_FILE = DATA_DIR / "plans.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

# 请求线程与后台巡检线程共用同一把锁，保证并发写入不互相覆盖
DATA_LOCK = threading.Lock()

auth = import_module("backend.auth")
scope = import_module("backend.scope")
core = import_module("backend.core")

ALLOWED_ROLES = set(auth.ALLOWED_ROLES)
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
# 账号（登录与角色体系的数据源）
# --------------------------------------------------------------------------

def read_users():
    return read_json(USERS_FILE, [])


def write_users(users):
    write_json(USERS_FILE, users)


def form_data(require_password=False):
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
    fields = {"name": name, "email": email, "phone": phone, "role": role, "status": status}
    if require_password:
        password = request.form.get("password", "")
        if not auth.PASSWORD_MIN_LENGTH <= len(password) <= auth.PASSWORD_MAX_LENGTH:
            return None, "密码长度需为 %d-%d 个字符。" % (auth.PASSWORD_MIN_LENGTH, auth.PASSWORD_MAX_LENGTH)
        fields["password_hash"] = auth.hash_password(password)
    return fields, None


# --------------------------------------------------------------------------
# 计划数据与设置（C 线及后续业务线共用）
# --------------------------------------------------------------------------

settings_io = import_module("backend.settings_io")


def sync_account(account):
    """统一账号：把 users.json 的账号同步进 system.json 用户表（目标闭环侧）。

    账号池 users.json 是身份权威（姓名 / 角色 管理员|普通用户 / 状态）；
    org_unit_id、岗位、能力等目标闭环扩展字段保留在 system.json，互不覆盖。
    """
    if not account or not account.get("id"):
        return None
    role_code = "admin" if account.get("role") == auth.ROLE_ADMIN else "staff"
    with core.DATA_LOCK:
        store = core.read_store()
        row = next((item for item in store["user"] if item["user_id"] == account["id"]), None)
        changed = False
        login_name = account.get("login_name") or (account.get("email") or "").split("@")[0]
        if row is None:
            row = {
                "user_id": account["id"],
                "display_name": account.get("name") or account["id"],
                "login_name": login_name,
                "org_unit_id": "ORG-001",
                "position": "",
                "status": account.get("status") or "启用",
                "last_login_at": "",
            }
            store["user"].append(row)
            changed = True
        else:
            if row.get("display_name") != (account.get("name") or row.get("display_name")):
                row["display_name"] = account.get("name") or row.get("display_name")
                changed = True
            if row.get("status") != (account.get("status") or row.get("status")):
                row["status"] = account.get("status") or row.get("status")
                changed = True
            if login_name and row.get("login_name") != login_name:
                row["login_name"] = login_name
                changed = True
        role = next((item for item in store["role"] if item.get("role_code") == role_code), None)
        if role is not None:
            grants = [item for item in store["user_role"] if item["user_id"] == account["id"]]
            if len(grants) != 1 or grants[0].get("role_id") != role["role_id"]:
                store["user_role"] = [item for item in store["user_role"] if item["user_id"] != account["id"]]
                store["user_role"].append({
                    "user_id": account["id"], "role_id": role["role_id"],
                    "org_unit_id": row.get("org_unit_id") or "",
                    "granted_by": "system", "granted_at": core.iso(),
                })
                changed = True
        if changed:
            core.write_store(store)
        return row


def sync_all_accounts():
    for account in read_users():
        sync_account(account)


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
    sync_account=sync_account,
    # 计划数据与设置
    read_store=read_store,
    write_store=write_store,
    read_settings=read_settings,
    write_settings=write_settings,
    store_lock=DATA_LOCK,
    data_lock=DATA_LOCK,
)

# 每条业务线各占一个路由模块；在此统一注册，启动始终只有一条命令。
# backend.auth 必须最先注册：它的 before_request 负责登录与角色守卫。
for module_name in (
    "backend.auth",                                       # 公共：登录 / 退出 / 角色守卫
    "backend.1", "backend.2", "backend.3", "backend.4",   # 用户管理（管理员专属）
    "backend.plan",                                        # A1/A2 计划录入与提炼确认
    "backend.schedule",                                    # A3 智能排期
    "backend.task_ops",                                    # B1/B2 任务列表与提醒中心
    "backend.scheduler",                                   # B3 后台定时巡检
    "backend.stats",                                       # C1 驾驶舱
    "backend.data_io",                                     # C2 数据管理
    "backend.settings_io",                                 # C3 系统设置
    "backend.hub",                                         # D 线 AI 任务中枢（跨线聚合 + 卡点研判）
    "backend.goal",                                        # 工作线A：部门目标 / SMART 校验 / 拆解树与发布
    "backend.assign",                                      # 工作线A：智能派工与饱和度校验
):
    import_module(module_name).register(app, services)

# ---------------------------------------------------------------------------
# NS 线（目标拆解与验证闭环 · 工作线 B）：账号池与页面壳对齐统一账号体系，
# 数据源 data/system.json（backend/core），路由统一走 /ns 与 /api/ns 前缀。
# 单系统合并：根路径 / 由 ns_root 接管；/ns/login 与 /login 两个入口共用同一会话；
# ns_* 端点对旧登录守卫整体放行，由 NS 守卫自查登录。
# ---------------------------------------------------------------------------
for module_name in (
    "backend.ns_auth",        # NS 登录 / 退出 / 守卫 / 错误包 / 非 B 线菜单占位页
    "backend.ns_workbench",   # B5 个人工作台
    "backend.ns_progress",    # B1 进度更新
    "backend.ns_warning",     # B2 预警与指导
    "backend.ns_changes",     # B3 变更管理
    "backend.ns_deps",        # B4 关键路径与阻塞
    "backend.ns_today",       # B6 日计划与任务
    "backend.ns_scheduler",   # NS 后台巡检（提醒 / 连续异常补偿）
):
    import_module(module_name).register(app, services)

for _endpoint in list(app.view_functions):
    if _endpoint.startswith("ns_"):
        auth.PUBLIC_ENDPOINTS.add(_endpoint)


def migrate_legacy_data():
    """启动时补齐旧数据：角色归一为两种、密码哈希、任务归属 user_id。

    保证用旧的 users.json / plans.json 直接启动也能登录，且数据归属正确。
    """
    users = read_users()
    changed = False
    for user in users:
        if user.get("role") not in ALLOWED_ROLES:
            user["role"] = auth.ROLE_USER
            changed = True
        if not user.get("password_hash"):
            user["password_hash"] = auth.hash_password(auth.DEFAULT_DEMO_PASSWORD)
            changed = True
    if changed:
        write_users(users)

    store = read_store()
    plan_owners = {plan.get("plan_id"): plan.get("user_id") for plan in store.get("plans", [])}
    migrated = False
    for task in store.get("tasks", []):
        if not task.get("user_id"):
            task["user_id"] = plan_owners.get(task.get("plan_id")) or ""
            migrated = True
    if migrated:
        write_store(store)


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
def inject_shell_context():
    user = g.get("current_user")
    store = read_store()
    # 目标闭环侧上下文：统一账号已同步，同一身份可直达 A 线页面
    ns_user = None
    ns_role_label = ""
    ns_perms = ()
    ns_unread = 0
    try:
        ns_store = core.read_store()
        ns_user = core.current_user(ns_store)
        if ns_user is not None:
            ns_perms = tuple(sorted(core.user_perms(ns_store, ns_user["user_id"])))
            ns_role_label = auth.ROLE_ADMIN if auth.is_admin(user) else auth.ROLE_USER
            ns_unread = sum(
                1 for item in ns_store.get("reminder", [])
                if item.get("user_id") == ns_user["user_id"] and item.get("status") == "未读"
            )
    except Exception:
        ns_user = None
    return {
        "current_user": user,
        "is_admin": auth.is_admin(user),
        "unread_reminders": scope.unread_reminder_count(store, user),
        "ns_user": ns_user,
        "ns_role_label": ns_role_label,
        "ns_perms": ns_perms,
        "ns_unread": ns_unread,
    }


@app.errorhandler(core.BizError)
def handle_biz_error(error):
    """业务异常统一转 {code, message, data} 响应包；HTTP 状态按错误码表映射。"""
    status = core.ERROR_HTTP.get(error.code, 400)
    return jsonify({"code": error.code, "message": error.message, "data": error.data}), status


@app.get("/forbidden")
def forbidden():
    """无权限页（新版菜单守卫 core.require_menu 的重定向落点）。"""
    return render_template("pages/forbidden.html", page_name="无权限"), 403


@app.get("/analytics")
def analytics():
    return render_template("pages/analytics.html", **page_context("analytics", "数据报表"))


migrate_legacy_data()
sync_all_accounts()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=app.config["IS_DEVELOPMENT"])
