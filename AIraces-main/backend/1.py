"""Developer 1: list and search user routes."""

from flask import render_template, request


def register(app, services):
    @app.get("/users")
    def index():
        users = services.read_users()
        keyword = request.args.get("q", "").strip().lower()
        status_filter = request.args.get("status", "全部")
        if status_filter not in services.allowed_statuses | {"全部"}:
            status_filter = "全部"

        visible_users = [
            user for user in users
            if (not keyword or keyword in (user["name"] + " " + user["email"] + " " + user["phone"]).lower())
            and (status_filter == "全部" or user["status"] == status_filter)
        ]
        visible_users.sort(key=lambda user: user["created_at"], reverse=True)
        return render_template(
            "pages/users.html",
            users=visible_users,
            total=len(users),
            active=sum(user["status"] == "启用" for user in users),
            admins=sum(user["role"] == "管理员" for user in users),
            keyword=request.args.get("q", "").strip(),
            status_filter=status_filter,
            active_page="users",
            page_name="用户管理",
        )
