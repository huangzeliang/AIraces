"""Developer 3: edit user route."""

from flask import abort, flash, redirect, url_for


def register(app, services):
    @app.post("/users/<user_id>/edit")
    def edit_user(user_id):
        fields, error = services.form_data()
        if error:
            flash(error, "error")
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
