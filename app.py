"""A small, local user management demo backed by a JSON file."""

import json
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import Flask, abort, flash, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data" / "users.json"
DATA_LOCK = threading.Lock()
ALLOWED_ROLES = {"管理员", "运营", "编辑", "普通用户"}
ALLOWED_STATUSES = {"启用", "停用"}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "local-demo-only-change-me")


def read_users():
    if not DATA_FILE.exists():
        return []
    with DATA_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_users(users):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(
        prefix=".users-", suffix=".json", dir=DATA_FILE.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(users, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, DATA_FILE)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


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


@app.get("/")
def index():
    users = read_users()
    keyword = request.args.get("q", "").strip().lower()
    status_filter = request.args.get("status", "全部")
    if status_filter not in ALLOWED_STATUSES | {"全部"}:
        status_filter = "全部"

    visible_users = [
        user for user in users
        if (not keyword or keyword in (user["name"] + " " + user["email"] + " " + user["phone"]).lower())
        and (status_filter == "全部" or user["status"] == status_filter)
    ]
    visible_users.sort(key=lambda user: user["created_at"], reverse=True)
    return render_template(
        "index.html",
        users=visible_users,
        total=len(users),
        active=sum(user["status"] == "启用" for user in users),
        admins=sum(user["role"] == "管理员" for user in users),
        keyword=request.args.get("q", "").strip(),
        status_filter=status_filter,
    )


@app.post("/users")
def create_user():
    fields, error = form_data()
    if error:
        flash(error, "error")
        return redirect(url_for("index"))

    with DATA_LOCK:
        users = read_users()
        if any(user["email"] == fields["email"] for user in users):
            flash("这个邮箱已被使用。", "error")
            return redirect(url_for("index"))
        users.append({"id": uuid4().hex, **fields, "created_at": datetime.now().strftime("%Y-%m-%d")})
        write_users(users)
    flash("用户已创建。", "success")
    return redirect(url_for("index"))


@app.post("/users/<user_id>/edit")
def edit_user(user_id):
    fields, error = form_data()
    if error:
        flash(error, "error")
        return redirect(url_for("index"))

    with DATA_LOCK:
        users = read_users()
        user = next((item for item in users if item["id"] == user_id), None)
        if user is None:
            abort(404)
        if any(item["id"] != user_id and item["email"] == fields["email"] for item in users):
            flash("这个邮箱已被使用。", "error")
            return redirect(url_for("index"))
        user.update(fields)
        write_users(users)
    flash("用户信息已更新。", "success")
    return redirect(url_for("index"))


@app.post("/users/<user_id>/delete")
def delete_user(user_id):
    with DATA_LOCK:
        users = read_users()
        remaining = [user for user in users if user["id"] != user_id]
        if len(remaining) == len(users):
            abort(404)
        write_users(remaining)
    flash("用户已删除。", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
