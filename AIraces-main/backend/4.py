"""Developer 4: delete user route."""

from flask import abort, flash, redirect, url_for


def register(app, services):
    @app.post("/users/<user_id>/delete")
    def delete_user(user_id):
        with services.data_lock:
            users = services.read_users()
            remaining = [user for user in users if user["id"] != user_id]
            if len(remaining) == len(users):
                abort(404)
            services.write_users(remaining)
        flash("用户已删除。", "success")
        return redirect(url_for("index"))
