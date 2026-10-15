"""公共 · 登录、退出与角色守卫（管理员 / 普通用户）。

- 会话：Flask session 保存 user_id，每次请求由 before_request 还原登录用户；
- 角色：管理员可见全部页面；普通用户仅见核心工作流页面（任务 / 提醒 / 计划流程）；
- 守卫：未登录跳转登录页（JSON 请求返回 401）；普通用户访问管理页时重定向回本角色首页。
"""

from flask import flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

ROLE_ADMIN = "管理员"
ROLE_USER = "普通用户"
ALLOWED_ROLES = (ROLE_ADMIN, ROLE_USER)

DEFAULT_DEMO_PASSWORD = "123456"
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 64

# 仅管理员可访问的路径前缀；"/" 是驾驶舱别名，单列在 is_admin_path 中
ADMIN_PATH_PREFIXES = ("/dashboard", "/data", "/settings", "/users", "/analytics")

PUBLIC_ENDPOINTS = {"static", "login"}


def hash_password(password):
    # 固定用 pbkdf2：兼容未编译 scrypt 的 Python 环境（如系统自带 3.9）
    return generate_password_hash(password, method="pbkdf2:sha256")


def verify_password(password_hash, password):
    if not password_hash or not password:
        return False
    try:
        return check_password_hash(password_hash, password)
    except ValueError:
        return False


def is_admin(user):
    return bool(user) and user.get("role") == ROLE_ADMIN


def is_admin_path(path):
    if path == "/":
        return True
    return any(path == prefix or path.startswith(prefix + "/") for prefix in ADMIN_PATH_PREFIXES)


def landing_path(user):
    """登录后的默认落地页：管理员回驾驶舱，普通用户回任务列表。"""
    return url_for("dashboard") if is_admin(user) else url_for("tasks")


def _safe_next(raw, user):
    """只接受站内相对路径，且普通用户不能借 next 直达管理页。"""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return None
    if is_admin_path(raw) and not is_admin(user):
        return None
    return raw


def _wants_json():
    return request.is_json or request.accept_mimetypes.best == "application/json"


def find_user(users, user_id):
    return next((user for user in users if user.get("id") == user_id), None)


def _demo_accounts(users):
    """登录页演示提示：每类角色取第一个启用账号。"""
    accounts = []
    for role in ALLOWED_ROLES:
        user = next(
            (item for item in users if item.get("role") == role and item.get("status") == "启用"),
            None,
        )
        if user:
            accounts.append({"name": user["name"], "email": user["email"], "role": role})
    return accounts


def register(app, services):
    read_users = services.read_users

    @app.before_request
    def require_login():
        if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
            return None

        users = read_users()
        user = find_user(users, session.get("user_id"))
        if user is None or user.get("status") != "启用":
            session.pop("user_id", None)
            if _wants_json() and request.method != "GET":
                return jsonify({"ok": False, "message": "登录已过期，请重新登录。"}), 401
            target = request.full_path.rstrip("?")
            return redirect(url_for("login", next=target))

        g.current_user = user
        if not is_admin(user) and is_admin_path(request.path):
            if _wants_json() and request.method != "GET":
                return jsonify({"ok": False, "message": "该操作仅管理员可用。"}), 403
            flash("该页面仅管理员可以访问，已返回你的工作台。", "error")
            return redirect(landing_path(user))
        return None

    @app.route("/login", methods=("GET", "POST"))
    def login():
        users = read_users()
        signed_in = find_user(users, session.get("user_id"))
        if signed_in is not None and signed_in.get("status") == "启用" and request.method == "GET":
            return redirect(landing_path(signed_in))

        email = ""
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            candidate = next((item for item in users if item.get("email") == email), None)
            if candidate is None or not verify_password(candidate.get("password_hash"), password):
                flash("邮箱或密码不正确，请重试。", "error")
            elif candidate.get("status") != "启用":
                flash("该账号已停用，请联系管理员。", "error")
            else:
                session.clear()
                session["user_id"] = candidate["id"]
                target = _safe_next(request.args.get("next") or request.form.get("next"), candidate)
                flash("欢迎回来，%s。" % candidate["name"], "success")
                return redirect(target or landing_path(candidate))

        return render_template(
            "pages/login.html",
            page_name="登录",
            email=email,
            next_target=request.args.get("next", ""),
            demo_password=DEFAULT_DEMO_PASSWORD,
            demo_accounts=_demo_accounts(users),
        )

    @app.post("/logout")
    def logout():
        session.clear()
        flash("已安全退出登录。", "success")
        return redirect(url_for("login"))
