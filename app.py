"""A small, local user management demo backed by a JSON file."""

import json
import os
import re
import tempfile
import threading
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, request


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR
DATA_FILE = PROJECT_DIR / "data" / "users.json"
DATA_LOCK = threading.Lock()
ALLOWED_ROLES = {"管理员", "运营", "编辑", "普通用户"}
ALLOWED_STATUSES = {"启用", "停用"}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

environment = os.environ.get("APP_ENV", "production").strip().lower()
is_development = environment == "development"

app = Flask(
    __name__,
    template_folder=str(PROJECT_DIR / "templates"),
    static_folder=str(PROJECT_DIR / "static"),
)
app.secret_key = os.environ.get("SECRET_KEY", "local-demo-only-change-me")
app.config["APP_ENV"] = environment
app.config["IS_DEVELOPMENT"] = is_development


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


services = SimpleNamespace(
    read_users=read_users,
    write_users=write_users,
    form_data=form_data,
    data_lock=DATA_LOCK,
    allowed_statuses=ALLOWED_STATUSES,
)

# Each developer owns one route file. Keep shared setup here, so starting the
# application and registering all routes always takes one command.
for module_name in ("backend.1", "backend.2", "backend.3", "backend.4"):
    import_module(module_name).register(app, services)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=app.config["IS_DEVELOPMENT"])
