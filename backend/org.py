"""C 线 · 组织与角色（C5）。

对应需求：组织归属是部门口径的前提 · 角色决定可见范围
归属文件：backend/org.py · templates/pages/org.html · css/pages/org.css

设计取向（MVP）：
- 组织数据独立成域（departments / org_members），不改写 users.json，
  避免与已有的用户管理页产生字段冲突；
- 部门树只做「一个上级」的简单层级，够支撑公司级 / 部门级 / 个人级的归属；
- 部门达成度调用 backend.c_metrics 的加权口径，且只统计已定案目标，
  与验收、对比分析、目标全景完全同一出口；
- 角色沿用账号体系的两个角色（管理员 / 普通用户），本页只展示与归属，
  不复制一套权限模型。
"""

from datetime import datetime
from importlib import import_module

from flask import flash, redirect, render_template, request, url_for

c_metrics = import_module("backend.c_metrics")

POST_OPTIONS = ("研发", "产品", "运营", "职能")
ROLE_HINT = {
    "管理员": "可见全部页面，含目标全景与周期字典",
    "普通用户": "仅见任务、提醒、计划流程与验收评估",
}


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def next_dept_id(store):
    existing = {item.get("dept_id") for item in store.get("departments", [])}
    index = 1
    while "DP-%03d" % index in existing:
        index += 1
    return "DP-%03d" % index


def member_index(store):
    return {item.get("user_id"): item for item in store.get("org_members", [])}


def dept_tree(store):
    """把扁平部门列表整理成带层级的展示行（父在前、子缩进）。"""
    departments = store.get("departments", [])
    children = {}
    for item in departments:
        children.setdefault(item.get("parent_id") or "", []).append(item)

    ordered = []

    def walk(parent_id, depth):
        # 同级按编号排序：与创建顺序一致，避免中文名称排序把新部门顶到最前
        for item in sorted(children.get(parent_id, []), key=lambda x: x.get("dept_id") or ""):
            ordered.append((item, depth))
            walk(item.get("dept_id"), depth + 1)

    walk("", 0)
    # 兜底：父节点缺失导致的孤儿部门也要显示，避免数据被静默隐藏
    seen = {item.get("dept_id") for item, _depth in ordered}
    for item in sorted(departments, key=lambda x: x.get("dept_id") or ""):
        if item.get("dept_id") not in seen:
            ordered.append((item, 0))
    return ordered


def descendant_ids(store, dept_id):
    """含自身的所有下级部门 id，用于部门口径汇总。"""
    children = {}
    for item in store.get("departments", []):
        children.setdefault(item.get("parent_id") or "", []).append(item.get("dept_id"))
    collected = []
    stack = [dept_id]
    while stack:
        current = stack.pop()
        collected.append(current)
        stack.extend(children.get(current, []))
    return collected


def build(store, users):
    members = member_index(store)
    name_of = {item["id"]: item for item in users}
    objectives = [item for item in store.get("objectives", [])
                  if item.get("status") != c_metrics.OBJ_DRAFT]

    by_dept = {}
    unassigned = []
    for user in users:
        member = members.get(user["id"]) or {}
        dept_id = member.get("dept_id") or ""
        if dept_id:
            by_dept.setdefault(dept_id, []).append(user)
        else:
            unassigned.append(user)

    rows = []
    for dept, depth in dept_tree(store):
        dept_id = dept.get("dept_id")
        scope = set(descendant_ids(store, dept_id))
        direct = by_dept.get(dept_id, [])
        scoped_users = [user for user in users
                        if (members.get(user["id"]) or {}).get("dept_id") in scope]
        scoped_ids = {user["id"] for user in scoped_users}
        scoped_objectives = [item for item in objectives if item.get("user_id") in scoped_ids]
        verified = [item for item in scoped_objectives
                    if item.get("status") in c_metrics.VERIFIED_STATUSES]
        rows.append({
            "dept_id": dept_id,
            "name": dept.get("name"),
            "depth": depth,
            "indent": depth * 22,
            "parent_id": dept.get("parent_id") or "",
            "parent_name": next((x.get("name") for x in store.get("departments", [])
                                 if x.get("dept_id") == dept.get("parent_id")), ""),
            "leader": (name_of.get(dept.get("leader_id")) or {}).get("name") or "未指定",
            "leader_id": dept.get("leader_id") or "",
            "note": dept.get("note") or "",
            "direct_members": len(direct),
            "members": len(scoped_users),
            "objective_total": len(scoped_objectives),
            "verified_total": len(verified),
            "achievement": c_metrics.weighted_objective_average(verified),
            "grade": c_metrics.grade_of(c_metrics.weighted_objective_average(verified)),
            "has_child": any(x.get("parent_id") == dept_id for x in store.get("departments", [])),
        })

    member_rows = []
    for user in users:
        member = members.get(user["id"]) or {}
        manager = name_of.get(member.get("manager_id")) or {}
        member_rows.append({
            "user_id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user.get("role"),
            "status": user.get("status"),
            "dept_id": member.get("dept_id") or "",
            "post": member.get("post") or "",
            "manager_id": member.get("manager_id") or "",
            "manager_name": manager.get("name") or "—",
        })

    # 部门口径：只统计有成员且已定案目标的部门
    scored = [row for row in rows if row["achievement"] is not None]
    summary = {
        "dept_total": len(rows),
        "member_total": len(users),
        "assigned_total": len(users) - len(unassigned),
        "unassigned_total": len(unassigned),
        "max_depth": max([row["depth"] for row in rows], default=0) + 1,
        "achievement": c_metrics.weighted_objective_average(
            [item for item in objectives if item.get("status") in c_metrics.VERIFIED_STATUSES]),
        "covered_dept": len(scored),
        "leader_missing": sum(1 for row in rows if not row["leader_id"]),
    }

    return {
        "rows": rows,
        "member_rows": member_rows,
        "unassigned": [{"user_id": u["id"], "name": u["name"]} for u in unassigned],
        "summary": summary,
        "post_options": POST_OPTIONS,
        "role_hints": ROLE_HINT,
        "role_counts": [
            {"role": role, "count": sum(1 for user in users if user.get("role") == role)}
            for role in ("管理员", "普通用户")
        ],
        "grade_bands": c_metrics.grade_bands_text(),
        "options": [
            {"dept_id": row["dept_id"], "name": ("　" * row["depth"]) + (row["name"] or "")}
            for row in rows
        ],
        "user_options": [{"user_id": user["id"], "name": user["name"]} for user in users],
    }


def register(app, services):
    read_store = services.read_store
    write_store = services.write_store
    read_users = services.read_users
    lock = services.store_lock

    @app.get("/settings/org")
    def org():
        return render_template(
            "pages/org.html",
            active_page="org",
            page_name="组织与角色",
            **build(read_store(), read_users())
        )

    @app.post("/settings/org/dept/save")
    def org_dept_save():
        dept_id = (request.form.get("dept_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        parent_id = (request.form.get("parent_id") or "").strip()
        leader_id = (request.form.get("leader_id") or "").strip()
        note = (request.form.get("note") or "").strip()

        if not name:
            flash("请填写部门名称。", "error")
            return redirect(url_for("org"))

        with lock:
            store = read_store()
            departments = store.setdefault("departments", [])
            record = next((item for item in departments if item.get("dept_id") == dept_id), None)
            if record is None:
                record = {"dept_id": next_dept_id(store), "created_at": now_iso()}
                departments.append(record)
                dept_id = record["dept_id"]

            if parent_id == dept_id:
                flash("上级部门不能是自己。", "error")
                return redirect(url_for("org"))
            # 防止把部门挂到自己的下级，形成环：拟定的上级不能是本部门的后代
            if parent_id and parent_id in descendant_ids(store, dept_id):
                flash("不能把部门挂到自己的下级部门下，会形成循环。", "error")
                return redirect(url_for("org"))
            if parent_id and not any(item.get("dept_id") == parent_id for item in departments):
                flash("上级部门不存在。", "error")
                return redirect(url_for("org"))

            record.update({"name": name, "parent_id": parent_id,
                           "leader_id": leader_id, "note": note})
            write_store(store)

        flash("部门已保存：%s" % name, "ok")
        return redirect(url_for("org"))

    @app.post("/settings/org/dept/delete")
    def org_dept_delete():
        dept_id = (request.form.get("dept_id") or "").strip()
        with lock:
            store = read_store()
            departments = store.get("departments", [])
            record = next((item for item in departments if item.get("dept_id") == dept_id), None)
            if record is None:
                flash("部门不存在。", "error")
                return redirect(url_for("org"))
            children = [item for item in departments if item.get("parent_id") == dept_id]
            if children:
                flash("该部门下还有 %d 个下级部门，请先处理下级。" % len(children), "error")
                return redirect(url_for("org"))
            members = [item for item in store.get("org_members", [])
                       if item.get("dept_id") == dept_id]
            if members:
                flash("该部门还有 %d 名成员，请先把成员调整到其他部门。" % len(members), "error")
                return redirect(url_for("org"))
            store["departments"] = [item for item in departments if item.get("dept_id") != dept_id]
            write_store(store)

        flash("部门已删除。", "ok")
        return redirect(url_for("org"))

    @app.post("/settings/org/member/save")
    def org_member_save():
        user_id = (request.form.get("user_id") or "").strip()
        dept_id = (request.form.get("dept_id") or "").strip()
        post = (request.form.get("post") or "").strip()
        manager_id = (request.form.get("manager_id") or "").strip()

        users = {item["id"]: item for item in read_users()}
        if user_id not in users:
            flash("成员不存在。", "error")
            return redirect(url_for("org"))
        if post and post not in POST_OPTIONS:
            flash("岗位取值不在字典内。", "error")
            return redirect(url_for("org"))
        if manager_id == user_id:
            flash("直属上级不能是自己。", "error")
            return redirect(url_for("org"))

        with lock:
            store = read_store()
            if dept_id and not any(item.get("dept_id") == dept_id
                                   for item in store.get("departments", [])):
                flash("所选部门不存在。", "error")
                return redirect(url_for("org"))
            if manager_id and manager_id not in users:
                flash("所选直属上级不存在。", "error")
                return redirect(url_for("org"))

            members = store.setdefault("org_members", [])
            record = next((item for item in members if item.get("user_id") == user_id), None)
            if record is None:
                record = {"user_id": user_id, "created_at": now_iso()}
                members.append(record)
            record.update({"dept_id": dept_id, "post": post, "manager_id": manager_id,
                           "updated_at": now_iso()})
            write_store(store)

        flash("已更新 %s 的组织归属。" % users[user_id]["name"], "ok")
        return redirect(url_for("org"))
