"""Developer 2: create user route."""

from datetime import datetime
from uuid import uuid4

from flask import flash, redirect, url_for


def register(app, services):
    @app.post("/users")
    def create_user():
        fields, error = services.form_data(require_password=True)
        if error:
            flash(error, "error")
            return redirect(url_for("index"))

        with services.data_lock:
            users = services.read_users()
            if any(user["email"] == fields["email"] for user in users):
                flash("这个邮箱已被使用。", "error")
                return redirect(url_for("index"))
            users.append({"id": uuid4().hex, **fields, "created_at": datetime.now().strftime("%Y-%m-%d")})
            services.write_users(users)
        flash("账号已创建，可使用邮箱和设置的密码登录。", "success")
        return redirect(url_for("index"))
