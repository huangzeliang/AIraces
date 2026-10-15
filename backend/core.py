"""目标逐级拆解与验证闭环系统 · 公共骨架（软需概设一体化 4.5）。

承载：本地 JSON 持久化（原子写 + 全局锁）、统一错误包与错误码、幂等防重、
审计日志、会话与权限、数据范围、分页与通用格式化。

约定：
- 所有写操作遵循「读取 → 校验 → 加锁写入 → 刷新展示」，请求线程与后台线程共用一把全局锁；
- 业务异常一律抛 BizError(code, message, data)，由 app.py 统一转换为响应包；
- 响应包统一 {code, message, data}，code=0 成功；
- 错误码：1001 参数非法 / 1002 字段格式 / 1003 不存在或超范围 / 1004 状态冲突 /
  1005 业务校验失败 / 1006 服务端异常 / 1007 LLM 超时或输出不合规 / 1008 权限不足 /
  1009 上传不合法 / 1010 重复提交 / 1011 日计划未通过审核禁止拉条。
"""

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import redirect, request, session, url_for

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STORE_FILE = DATA_DIR / "system.json"
UPLOAD_DIR = DATA_DIR / "uploads"
BACKUP_DIR = DATA_DIR / "backups"

DATA_LOCK = threading.RLock()

# 23 张表在 JSON 中一一对应为顶层数组（对象名 ↔ 文件内数组 ↔ 目标库表名）
STORE_KEYS = (
    "org_unit", "user", "role", "permission", "role_permission", "user_role",
    "period", "objective", "key_result",
    "work_task", "task_assignee", "dependency", "progress_log", "evidence",
    "daily_plan", "daily_plan_item",
    "change_request", "assessment", "retrospective", "reminder", "staff_profile",
    "dict_item", "setting", "audit_log",
)
EMPTY_STORE = {key: [] for key in STORE_KEYS}

ERROR_HTTP = {
    1001: 400, 1002: 400, 1003: 404, 1004: 409, 1005: 422,
    1006: 500, 1007: 504, 1008: 403, 1009: 413, 1010: 429, 1011: 409,
}


class BizError(Exception):
    """业务异常：由 app.py 统一转成 {code, message, data} 响应包。"""

    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def fail(code, message, data=None):
    raise BizError(code, message, data)


def ok(data=None, message="ok"):
    return {"code": 0, "message": message, "data": data}


# ---------------------------------------------------------------------------
# 通用读写（原子写：临时文件 + fsync + os.replace）
# ---------------------------------------------------------------------------

def read_json(path, default):
    if not path.exists():
        return json.loads(json.dumps(default))
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(default))


def write_json(path, payload):
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


def read_store():
    """读取全量数据；文件缺失时自动生成演示种子，保证首次启动即可用。"""
    if not STORE_FILE.exists():
        with DATA_LOCK:
            if not STORE_FILE.exists():
                from backend import seed
                write_json(STORE_FILE, seed.build_seed())
    store = read_json(STORE_FILE, None)
    if not isinstance(store, dict):
        store = json.loads(json.dumps(EMPTY_STORE))
    for key in STORE_KEYS:
        store.setdefault(key, [])
    return store


def write_store(store):
    write_json(STORE_FILE, store)


# ---------------------------------------------------------------------------
# 时间与格式化（统一取服务端 now，百分比保留 1 位小数）
# ---------------------------------------------------------------------------

def now():
    return datetime.now().replace(microsecond=0)


def iso(moment=None):
    return (moment or now()).replace(microsecond=0).isoformat()


def parse_dt(raw):
    if not raw:
        return None
    cleaned = str(raw).strip().replace("/", "-")
    try:
        return datetime.fromisoformat(cleaned).replace(microsecond=0)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def f1(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# 幂等防重：同一 Idempotency-Key 10 分钟内直接返回上次结果（1010 静默处理）
# ---------------------------------------------------------------------------

_IDEMP_LOCK = threading.Lock()
_IDEMP = {}
IDEMP_TTL = 600


def idempotency_lookup(key):
    if not key:
        return None
    with _IDEMP_LOCK:
        item = _IDEMP.get(key)
        if item and time.time() - item[0] < IDEMP_TTL:
            return item[1]
        if item:
            _IDEMP.pop(key, None)
    return None


def idempotency_save(key, payload):
    if not key:
        return
    with _IDEMP_LOCK:
        _IDEMP[key] = (time.time(), payload)
        if len(_IDEMP) > 500:
            cutoff = time.time() - IDEMP_TTL
            for stale in [k for k, v in _IDEMP.items() if v[0] < cutoff]:
                _IDEMP.pop(stale, None)


def client_key():
    return (request.headers.get("Idempotency-Key") or "").strip()


# ---------------------------------------------------------------------------
# 会话、权限与数据范围
# ---------------------------------------------------------------------------

def login_session(user_id):
    session["user_id"] = user_id


def logout_session():
    session.pop("user_id", None)


def current_user(store=None):
    user_id = session.get("user_id")
    if not user_id:
        return None
    store = store or read_store()
    return next((u for u in store["user"] if u["user_id"] == user_id and u.get("status") == "启用"), None)


def user_of(store, user_id):
    return next((u for u in store["user"] if u["user_id"] == user_id), None)


def org_of(store, org_unit_id):
    return next((o for o in store["org_unit"] if o["org_unit_id"] == org_unit_id), None)


def dict_name(store, dict_type, dict_code):
    item = next((d for d in store["dict_item"] if d["dict_type"] == dict_type and d["dict_code"] == dict_code), None)
    return item["dict_name"] if item else dict_code


def setting_value(store, key, default=None):
    item = next((s for s in store["setting"] if s["setting_key"] == key), None)
    return item["setting_value"] if item else default


def org_subtree_ids(store, org_unit_id):
    """按物化路径展开子树（含自身）。"""
    root = org_of(store, org_unit_id)
    if not root:
        return []
    prefix = root.get("path") or "/%s/" % org_unit_id
    return [o["org_unit_id"] for o in store["org_unit"]
            if (o.get("path") or "").startswith(prefix) or o["org_unit_id"] == org_unit_id]


def user_org_ids(store, user):
    """用户可见的组织范围：超管 = 全部；部门/团队负责人 = 所在单元子树。"""
    role = role_of_user(store, user["user_id"])
    if role and role.get("scope_type") == "ALL":
        return None
    return org_subtree_ids(store, user.get("org_unit_id"))


def scope_user_ids(store, user):
    org_ids = user_org_ids(store, user)
    if org_ids is None:
        return None
    return [u["user_id"] for u in store["user"] if u.get("org_unit_id") in org_ids]


def in_scope(store, user, target_user_id):
    scoped = scope_user_ids(store, user)
    return scoped is None or target_user_id in scoped


def roles_of_user(store, user_id):
    role_ids = [ur["role_id"] for ur in store["user_role"] if ur["user_id"] == user_id]
    return [r for r in store["role"] if r["role_id"] in role_ids and r.get("status") == "启用"]


def role_of_user(store, user_id):
    """取用户主角色（scope 最大者优先），演示态一人一主角色。"""
    rank = {"ALL": 3, "DEPT": 2, "SELF": 1}
    roles = roles_of_user(store, user_id)
    if not roles:
        return None
    return sorted(roles, key=lambda r: rank.get(r.get("scope_type"), 0), reverse=True)[0]


def user_perms(store, user_id):
    role_ids = {r["role_id"] for r in roles_of_user(store, user_id)}
    perm_ids = {rp["perm_id"] for rp in store["role_permission"] if rp["role_id"] in role_ids}
    return {p["perm_code"] for p in store["permission"] if p["perm_id"] in perm_ids}


def has_perm(store, user, code):
    if not user:
        return False
    return code in user_perms(store, user["user_id"])


def require_login(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def require_menu(menu_key):
    """页面级权限：未登录 → 登录页；无权限 → 403 越权提示（不泄露资源存在性）。"""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            store = read_store()
            user = current_user(store)
            if user is None:
                return redirect(url_for("login", next=request.path))
            if not has_perm(store, user, "menu:" + menu_key):
                return redirect(url_for("forbidden"))
            return view(*args, **kwargs)
        return wrapper
    return decorator


def api_require(store, code):
    """接口级操作权限；不足时抛 1008。"""
    user = current_user(store)
    if user is None:
        fail(1008, "请先登录，再进行操作。")
    if not has_perm(store, user, code):
        fail(1008, "当前角色缺少权限点 %s，操作被拒绝。" % code)
    return user


def api_login_required():
    user = current_user()
    if user is None:
        fail(1008, "登录已失效，请重新登录。")
    return user


# ---------------------------------------------------------------------------
# 审计 / 主键 / 分页
# ---------------------------------------------------------------------------

def audit(store, action, target_type="", target_id="", before=None, after=None, operator=None):
    """必须留痕的场景：角色授权、配置变更、归档、强制派工豁免。"""
    user = operator or current_user(store)
    entry = {
        "log_id": next_id(store, "audit_log", "LOG", 4),
        "operator_id": user["user_id"] if user else "system",
        "operator_name": user["display_name"] if user else "系统",
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "before": before or {},
        "after": after or {},
        "ip": request.remote_addr if request else "",
        "created_at": iso(),
    }
    store["audit_log"].insert(0, entry)
    return entry


def next_id(store, key, prefix, width=3):
    pattern = "%s-" if prefix else "%s"
    used = set()
    for row in store.get(key, []):
        for field in ("objective_id", "kr_id", "task_id", "dep_id", "log_id", "evidence_id",
                      "plan_id", "item_id", "change_id", "assess_id", "retro_id", "reminder_id",
                      "profile_id", "dict_id", "org_unit_id", "user_id", "role_id", "perm_id",
                      "period_id", "log_id"):
            value = row.get(field)
            if isinstance(value, str) and value.startswith(pattern % prefix):
                used.add(value)
    index = 1
    while ("%s%0*d" % (pattern % prefix, width, index)) in used:
        index += 1
    return "%s%0*d" % (pattern % prefix, width, index)


def page_args(default_size=20):
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(request.args.get("size", default_size))
    except (TypeError, ValueError):
        size = default_size
    return max(1, page), min(max(size, 5), 100)


def paginate(rows, page, size):
    total_pages = max(1, (len(rows) + size - 1) // size)
    page = min(page, total_pages)
    start = (page - 1) * size
    return rows[start:start + size], page, total_pages


# ---------------------------------------------------------------------------
# 业务枚举与展示辅助
# ---------------------------------------------------------------------------

OBJECTIVE_STATUS = {
    "draft": "草稿", "pending_confirm": "待确认", "confirmed": "已承接", "published": "已发布",
    "executing": "执行中", "pending_review": "待验收", "reviewed": "已验收", "archived": "已归档",
}
OBJECTIVE_NEXT = {
    "draft": "pending_confirm", "pending_confirm": "confirmed", "confirmed": "published",
    "published": "executing", "executing": "pending_review", "pending_review": "reviewed",
    "reviewed": "archived",
}
TASK_STATUS = {"todo": "待开始", "doing": "进行中", "blocked": "阻塞", "done": "已完成", "cancelled": "已取消"}
DAILY_STATUS = {"draft": "编制中", "submitted": "待审核", "approved": "已通过", "rejected": "已驳回"}
CHANGE_STATUS = {"pending": "待审批", "approved": "已通过", "rejected": "已驳回"}
ASSESS_STATUS = {"pending_self": "待自评", "pending_manager": "待终评", "done": "已完成", "appealed": "已申诉"}
ALERT_TYPES = {
    "overdue": "超期", "due_soon": "临近截止", "deviation": "进度偏差",
    "checkpoint": "检查点", "daily_streak": "连续异常",
}


def health_level(progress, plan_end, warn_days=3):
    """三色健康度：超期红 / 临期黄 / 正常绿。进度口径由调用方传入（0~100）。"""
    end = parse_dt(plan_end)
    if end and end < now():
        return "red"
    if end and end - now() <= timedelta(days=warn_days) and (progress or 0) < 90:
        return "yellow"
    return "green"


def level_label(level):
    return {"green": "正常", "yellow": "预警", "red": "严重"}.get(level, "正常")
