"""Developer 3: edit user route."""

from importlib import import_module

from flask import abort, flash, g, redirect, request, url_for

auth = import_module("backend.auth")


def register(app, services):
    @app.post("/users/<user_id>/edit")
    def edit_user(user_id):
        fields, error = services.form_data()
        if error:
            flash(error, "error")
            return redirect(url_for("index"))

        # 自我保护：不能停用自己或改掉自己的角色，避免把自己锁在门外
        if user_id == g.current_user["id"]:
            if fields["status"] != "启用":
                flash("不能停用当前登录的账号。", "error")
                return redirect(url_for("index"))
            if fields["role"] != g.current_user["role"]:
                flash("不能修改当前登录账号的角色。", "error")
                return redirect(url_for("index"))

        with services.data_lock:
            users = services.read_users()
            user = next((item for item in users if item["id"] == user_id), None)
            if user is None:
                abort(404)
            if any(item["id"] != user_id and item["email"] == fields["email"] for item in users):
                flash("这个邮箱已被使用。", "error")
                return redirect(url_for("index"))
            user.update(fields)
            services.write_users(users)
        flash("用户信息已更新。", "success")
        return redirect(url_for("index"))

    @app.post("/users/<user_id>/reset-password")
    def reset_password(user_id):
        password = request.form.get("password", "")
        if not auth.PASSWORD_MIN_LENGTH <= len(password) <= auth.PASSWORD_MAX_LENGTH:
            flash(
                "密码长度需为 %d-%d 个字符。" % (auth.PASSWORD_MIN_LENGTH, auth.PASSWORD_MAX_LENGTH),
                "error",
            )
            return redirect(url_for("index"))

        with services.data_lock:
            users = services.read_users()
            user = next((item for item in users if item["id"] == user_id), None)
            if user is None:
                abort(404)
            user["password_hash"] = auth.hash_password(password)
            services.write_users(users)
        flash("已重置「%s」的登录密码。" % user["name"], "success")
        return redirect(url_for("index"))
