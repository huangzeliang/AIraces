"""公共 · 数据范围：按登录用户过滤计划 / 任务 / 提醒（管理员看全量，普通用户只看自己）。

归属规则：
- 计划以 plan.user_id 为准；任务以 task.user_id 为准（旧数据缺失时回退所属计划 owner）；
- 提醒 / 反馈通过 task_id 归属到任务；找不到任务的孤儿记录对普通用户不可见。

读取路径用 scope_store 生成只读视图；写入路径必须操作完整 store 并先用 owns_* 校验归属。
"""

from importlib import import_module

auth = import_module("backend.auth")


def is_admin(user):
    return auth.is_admin(user)


def owns_plan(plan, user):
    if auth.is_admin(user):
        return True
    return bool(plan) and plan.get("user_id") == user["id"]


def owns_task(task, user):
    if auth.is_admin(user):
        return True
    return bool(task) and task.get("user_id") == user["id"]


def find_task(store, task_id):
    return next((task for task in store.get("tasks", []) if task.get("task_id") == task_id), None)


def owns_task_id(store, task_id, user):
    return owns_task(find_task(store, task_id), user)


def owns_reminder(reminder, store, user):
    if auth.is_admin(user):
        return True
    if not reminder:
        return False
    return owns_task(find_task(store, reminder.get("task_id")), user)


def scope_store(store, user):
    """当前用户可见的数据视图；管理员原样返回，避免无谓拷贝。"""
    if auth.is_admin(user):
        return store
    uid = user["id"]
    plans = [plan for plan in store.get("plans", []) if plan.get("user_id") == uid]
    plan_ids = {plan.get("plan_id") for plan in plans}
    tasks = [
        task for task in store.get("tasks", [])
        if task.get("user_id") == uid
        or (not task.get("user_id") and task.get("plan_id") in plan_ids)
    ]
    task_ids = {task.get("task_id") for task in tasks}
    scoped = dict(store)
    scoped["plans"] = plans
    scoped["todos"] = [todo for todo in store.get("todos", []) if todo.get("plan_id") in plan_ids]
    scoped["tasks"] = tasks
    scoped["reminders"] = [
        reminder for reminder in store.get("reminders", []) if reminder.get("task_id") in task_ids
    ]
    scoped["feedbacks"] = [
        feedback for feedback in store.get("feedbacks", []) if feedback.get("task_id") in task_ids
    ]
    return scoped


def unread_reminder_count(store, user):
    if user is None:
        return 0
    scoped = scope_store(store, user)
    return sum(1 for item in scoped.get("reminders", []) if item.get("status") == "未读")
