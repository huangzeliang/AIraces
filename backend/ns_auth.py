"""NS 线 · 登录 / 退出 / 守卫 / 错误包 / 非 B 线菜单占位页。

- 账号体系：data/system.json 的 user 表（U001~U010），演示统一口令 123456；
- 会话：与统一账号共用 backend.core.SESSION_KEY，任一登录入口登录后两线页面互通；
- 旧系统 before_request 守卫通过 app.py 把 ns_* 端点注册进 PUBLIC_ENDPOINTS 整体放行，
  本模块的 before_request 只拦截 /ns 与 /api/ns 前缀，自行判登录。
"""

from flask import g, jsonify, redirect, render_template, request, url_for

from backend.core import BizError, DATA_LOCK, ERROR_HTTP, fail, iso, read_store, user_perms, write_store
from backend.ns_common import (
    NS_DEMO_PASSWORD,
    landing_url,
    ns_api,
    ns_current_user,
    ns_login_session,
    ns_logout_session,
    ns_render,
    ns_user,
    payload,
    require_ns_menu,
)

NS_PUBLIC_PATHS = ("/ns/login", "/api/ns/auth/login")


def register(app, services):
    @app.before_request
    def ns_guard():
        path = request.path
        if not (path == "/ns" or path.startswith("/ns/") or path.startswith("/api/ns/")):
            return None
        if path in NS_PUBLIC_PATHS:
            return None
        store = read_store()
        user = ns_current_user(store)
        if user is None:
            if path.startswith("/api/ns/"):
                return jsonify({"code": 1008, "message": "登录已失效，请重新登录。", "data": None}), 401
            return redirect(url_for("ns_login", next=request.full_path.rstrip("?")))
        g.ns_user = user
        g.ns_store = store
        return None

    @app.errorhandler(BizError)
    def ns_biz_error(err):
        payload = {"code": err.code, "message": err.message, "data": err.data, "ts": iso()}
        return jsonify(payload), ERROR_HTTP.get(err.code, 500)

    # ------------------------------------------------------------------ 登录
    @app.route("/ns/login", methods=("GET", "POST"))
    def ns_login():
        store = read_store()
        signed_in = ns_current_user(store)
        if signed_in is not None and request.method == "GET":
            return redirect(landing_url(store, signed_in))

        error = ""
        login_name = request.form.get("login_name", "").strip() if request.method == "POST" else ""
        if request.method == "POST":
            password = request.form.get("password", "")
            candidate = next(
                (u for u in store["user"] if u.get("login_name") == login_name or u.get("user_id") == login_name),
                None,
            )
            if candidate is None or password != NS_DEMO_PASSWORD:
                error = "账号或口令不正确，请重试（演示口令 123456）。"
            elif candidate.get("status") != "启用":
                error = "该账号已停用，请联系系统管理员。"
            else:
                ns_login_session(candidate["user_id"])
                with DATA_LOCK:
                    fresh = read_store()
                    fresh_user = next((u for u in fresh["user"] if u["user_id"] == candidate["user_id"]), None)
                    if fresh_user:
                        fresh_user["last_login_at"] = iso()
                    write_store(fresh)
                target = request.args.get("next") or request.form.get("next") or ""
                if not target.startswith("/ns") or target.startswith("//"):
                    target = landing_url(store, candidate)
                return redirect(target)

        accounts = [
            {
                "user_id": u["user_id"], "login_name": u["login_name"], "display_name": u["display_name"],
                "position": u.get("position", ""), "org_name": next(
                    (o["org_name"] for o in store["org_unit"] if o["org_unit_id"] == u.get("org_unit_id")), ""),
            }
            for u in store["user"]
        ]
        return render_template(
            "ns/login.html",
            page_title="登录",
            error=error,
            login_name=login_name,
            demo_password=NS_DEMO_PASSWORD,
            accounts=accounts,
        )

    @app.post("/ns/logout")
    def ns_logout():
        ns_logout_session()
        return redirect(url_for("ns_login"))

    @app.get("/ns")
    @app.get("/ns/")
    def ns_index():
        store = read_store()
        user = ns_current_user(store)
        if user is None:
            return redirect(url_for("ns_login"))
        return redirect(landing_url(store, user))

    # 合并后系统唯一入口：已登录进各自工作台，未登录进 NS 登录页
    @app.get("/")
    def ns_root():
        store = read_store()
        user = ns_current_user(store)
        if user is None:
            return redirect(url_for("ns_login"))
        return redirect(landing_url(store, user))

    # ------------------------------------------------------------------ 非 B 线菜单占位页
    # 已实现的工作线页面：占位入口 302 直达真实页面（权限点与应用页守卫一致，不放宽）
    wired_pages = {
        "dashboard": "dashboard",     # C1 驾驶舱（管理员专属）
        "goal_dept": "goal_dept",     # A1 部门目标
        "goal_smart": "goal_smart",   # A2 SMART 校验
        "goal_tree": "goal_tree",     # A3 拆解树与发布
        "assign": "assign",           # A4 智能派工
        "data": "data_manage",        # C2 数据管理（管理员专属）
        "settings": "settings",       # C3 系统设置（管理员专属）
    }
    placeholder_pages = (
        ("ns_dashboard", "/ns/dashboard", "dashboard", "部门驾驶舱", "工作线 C · 看板与数据"),
        ("ns_goal_dept", "/ns/goal/dept", "goal_dept", "部门目标", "工作线 A · 目标制定"),
        ("ns_goal_smart", "/ns/goal/smart", "goal_smart", "SMART 校验", "工作线 A · 目标制定"),
        ("ns_goal_tree", "/ns/goal/tree", "goal_tree", "拆解树与发布", "工作线 A · 目标拆解"),
        ("ns_assign", "/ns/assign", "assign", "智能派工", "工作线 A · 派工"),
        ("ns_review", "/ns/review", "review", "完成与验收", "工作线 C · 验收评估"),
        ("ns_retro", "/ns/retro", "retro", "复盘", "工作线 C · 复盘"),
        ("ns_compare", "/ns/compare", "compare", "对比分析", "工作线 C · 对比"),
        ("ns_settings_org", "/ns/settings/org", "settings_org", "组织与角色", "工作线 C · 数据与设置"),
        ("ns_settings_dict", "/ns/settings/dict", "settings_dict", "周期与字典", "工作线 C · 数据与设置"),
        ("ns_data", "/ns/data", "data", "数据管理", "工作线 C · 数据与设置"),
        ("ns_settings", "/ns/settings", "settings", "系统设置", "工作线 C · 数据与设置"),
    )

    def make_placeholder(endpoint, menu_key, title, owner_line):
        target_endpoint = wired_pages.get(menu_key)

        @require_ns_menu(menu_key)
        def view():
            if target_endpoint:
                return redirect(url_for(target_endpoint))
            return ns_render(
                "ns/coming_soon.html", menu_key, title, "该菜单属于「%s」，尚未接入本演示入口。" % owner_line,
                owner_line=owner_line,
            )
        view.__name__ = endpoint
        return view

    for endpoint, path, menu_key, title, owner_line in placeholder_pages:
        view = make_placeholder(endpoint, menu_key, title, owner_line)
        app.add_url_rule(path, endpoint=endpoint, view_func=view)

    # ------------------------------------------------------------------ API：登录 / 退出 / 当前用户
    @app.post("/api/ns/auth/login")
    @ns_api
    def ns_api_auth_login():
        body = payload()
        store = read_store()
        login_name = (body.get("login_name") or "").strip()
        password = body.get("password") or ""
        candidate = next(
            (u for u in store["user"] if u.get("login_name") == login_name or u.get("user_id") == login_name),
            None,
        )
        if candidate is None or password != NS_DEMO_PASSWORD:
            fail(1005, "账号或口令不正确。")
        if candidate.get("status") != "启用":
            fail(1004, "该账号已停用。")
        ns_login_session(candidate["user_id"])
        return {"user_id": candidate["user_id"], "display_name": candidate["display_name"]}

    @app.post("/api/ns/auth/logout")
    @ns_api
    def ns_api_auth_logout():
        ns_logout_session()
        return {"ok": True}

    @app.get("/api/ns/auth/me")
    @ns_api
    def ns_api_auth_me():
        store = read_store()
        user = ns_user(store)
        perms = sorted(user_perms(store, user["user_id"]))
        return {
            "user_id": user["user_id"],
            "display_name": user["display_name"],
            "org_unit_id": user.get("org_unit_id", ""),
            "perms": perms,
        }
