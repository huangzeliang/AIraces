"""A 线 · 解析引擎（F-102 结构化提炼 / F-201/202 拆解与属性建议 / F-801 一句话办事 / F-805 依据说明）。

两种引擎，同一套输出契约：
- 规则引擎（默认，本地兜底）：按句读拆分 + 中文时间表达识别 + 动词估算，离线可用、结果稳定；
- 大模型（可选）：配置环境变量或项目根目录 .env（LLM_BASE_URL / LLM_API_KEY / LLM_MODEL，OpenAI 兼容接口）
  后自动优先调用，返回结果必须通过 JSON 结构校验；模型漏提的时间由规则引擎补全；
  任何失败都不落半成品，自动兜底为规则引擎并在依据说明中标注。

输出契约（parse_plan）：
    {"goal": str, "todos": [{"content": str, "key_time": ISO8601 或 ""}],
     "basis": [{"item": str, "detail": str}], "engine": str}
"""

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# 可选配置：项目根目录 .env（KEY=VALUE，仅读取、不覆盖已有环境变量）
# 用于免命令行注入大模型配置，例如 DeepSeek：
#   LLM_BASE_URL=https://api.deepseek.com
#   LLM_API_KEY=sk-xxxx
#   LLM_MODEL=deepseek-flash
# ---------------------------------------------------------------------------


def _load_env_file():
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_env_file()

# ---------------------------------------------------------------------------
# 通用时间工具
# ---------------------------------------------------------------------------

_WEEKDAY_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_CONNECTIVE_PREFIX = re.compile(r"^\s*(然后|接着|随后|其次|另外|同时|最后|以及|并且|再|先|并|我想|我要)[，,、]?\s*")
_TRAILING_PUNCT = re.compile(r"[\s。；;，,、！!？?：:]+$")


def parse_dt(raw):
    """解析 ISO8601 与常见格式，失败返回 None。"""
    if not raw:
        return None
    cleaned = str(raw).strip().replace("/", "-")
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1]
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


def iso(moment):
    return moment.replace(microsecond=0).isoformat()


def _at(moment, hour=18):
    return moment.replace(hour=hour, minute=0, second=0, microsecond=0)


def _last_day_of_month(moment):
    return (moment.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)


# ---------------------------------------------------------------------------
# 中文关键时间识别（F-102 关键时间）
# 逐条正则收集命中，取位置最靠前（同位置时更具体）的一条
# ---------------------------------------------------------------------------

def _detect_time_candidates(text, now):
    candidates = []

    def push(pattern, resolver, priority=5):
        for match in re.finditer(pattern, text):
            moment, label = resolver(match, now)
            if moment:
                candidates.append((match.start(), priority, moment, label, match.span()))

    def year_of(month, day, now):
        moment = _at(now.replace(month=month, day=1)).replace(day=day)
        if moment < now:
            moment = moment.replace(year=moment.year + 1)
        return moment

    push(r"(\d{4})-(\d{1,2})-(\d{1,2})",
         lambda m, n: (_at(n.replace(year=int(m.group(1)), month=int(m.group(2)), day=int(m.group(3)))), m.group(0)), 1)
    push(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]?",
         lambda m, n: (_at(n.replace(year=int(m.group(1)), month=int(m.group(2)), day=int(m.group(3)))), m.group(0)), 1)
    push(r"(\d{1,2})月(\d{1,2})[日号]",
         lambda m, n: (year_of(int(m.group(1)), int(m.group(2)), n), m.group(0)), 2)
    push(r"今天|今日", lambda m, n: (_at(n), m.group(0)), 1)
    push(r"明天|明日", lambda m, n: (_at(n + timedelta(days=1)), m.group(0)), 1)
    push(r"后天", lambda m, n: (_at(n + timedelta(days=2)), m.group(0)), 1)
    push(r"下周([一二三四五六日天])",
         lambda m, n: (_at(_week_start(n) + timedelta(days=7 + _WEEKDAY_CN[m.group(1)])), m.group(0)), 1)
    push(r"下周(?=前|内|之前)",
         lambda m, n: (_at(_week_start(n) + timedelta(days=13)), m.group(0)), 2)
    push(r"下周",
         lambda m, n: (_at(_week_start(n) + timedelta(days=13)), "下周（按下周日内计）"), 4)
    push(r"(?:本周|这周|周|星期)([一二三四五六日天])", _resolve_this_weekday, 3)
    push(r"月底|月末",
         lambda m, n: (_at(_last_day_of_month(n)), m.group(0)), 2)
    push(r"月初",
         lambda m, n: (_at(_month_first(n)), m.group(0)), 2)
    push(r"本周末|周末",
         lambda m, n: (_resolve_this_weekday_text("周日", n, 3)), 3)
    push(r"(\d{1,2})[日号](?![0-9])", _resolve_day_of_month, 4)
    push(r"([0-9]+|[一二两三四五六七八九十])天(?:之?内|以内|后|之后)?",
         _resolve_days_later, 6)
    push(r"([0-9]+|[一二两三四五六七八九十])周(?:之?内|以内|后|之后)",
         _resolve_weeks_later, 6)
    return candidates


def _week_start(moment):
    return (moment - timedelta(days=moment.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def _month_first(moment):
    first = moment.replace(day=1)
    if first < moment.replace(hour=0, minute=0, second=0, microsecond=0):
        first = (first + timedelta(days=32)).replace(day=1)
    return first


def _resolve_this_weekday(match, now):
    weekday = _WEEKDAY_CN[match.group(1)]
    moment = _at(_week_start(now) + timedelta(days=weekday))
    if moment < now:
        moment = moment + timedelta(days=7)
    return moment, match.group(0)


def _resolve_this_weekday_text(text, now, _priority):
    moment = _at(_week_start(now) + timedelta(days=6))
    if moment < now:
        moment = moment + timedelta(days=7)
    return moment, text


def _resolve_day_of_month(match, now):
    day = int(match.group(1))
    if not 1 <= day <= 31:
        return None, ""
    try:
        moment = _at(now.replace(day=day))
    except ValueError:
        return None, ""
    if moment < now:
        next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
        try:
            moment = _at(next_month.replace(day=day))
        except ValueError:
            return None, ""
    return moment, match.group(0)


def _resolve_days_later(match, now):
    token = match.group(1)
    count = int(token) if token.isdigit() else _CN_NUM.get(token, 0)
    if not count:
        return None, ""
    return _at(now + timedelta(days=count)), match.group(0)


def _resolve_weeks_later(match, now):
    token = match.group(1)
    count = int(token) if token.isdigit() else _CN_NUM.get(token, 0)
    if not count:
        return None, ""
    return _at(now + timedelta(days=count * 7)), match.group(0)


def detect_key_time(text, now=None):
    """识别关键时间，返回 (datetime, 命中词, 命中区间)；未命中返回 (None, "", None)。"""
    now = now or datetime.now().replace(microsecond=0)
    candidates = _detect_time_candidates(text, now)
    if not candidates:
        return None, "", None
    candidates.sort(key=lambda item: (item[0], item[1]))
    _, _, moment, label, span = candidates[0]
    return moment, label, span


_HOUR_WORDS = (("凌晨", 5), ("早上", 8), ("上午", 11), ("中午", 12),
               ("下午", 15), ("傍晚", 18), ("晚上", 20))


def _apply_hour_word(text, span, moment):
    """若时间短语后紧跟「上午 / 下午 / 晚上」等词，修正小时并扩大命中区间。"""
    if not span or moment is None:
        return moment, span
    tail = text[span[1]:span[1] + 2]
    for word, hour in _HOUR_WORDS:
        if tail.startswith(word):
            return moment.replace(hour=hour), (span[0], span[1] + len(word))
    return moment, span


def _strip_time_phrase(text, span):
    """把命中的时间短语（及其后的 前 / 内 / 之前 等词）从待办内容中剥离。"""
    if not span:
        return text
    head, tail = text[:span[0]], text[span[1]:]
    tail = re.sub(r"^(之前|以前|之内|以内|内|前)(?=[\u4e00-\u9fa5A-Za-z0-9])", "", tail)
    return (head + tail).strip()


def _clean_content(text):
    content = text.strip()
    while True:
        cleaned = _CONNECTIVE_PREFIX.sub("", content, count=1)
        if cleaned == content:
            break
        content = cleaned
    content = _TRAILING_PUNCT.sub("", content).strip()
    return content


# ---------------------------------------------------------------------------
# 规则引擎：目标 / 待办拆分
# ---------------------------------------------------------------------------

_ENUM_SPLIT = re.compile(r"[，,]")
_DESCRIPTIVE_START = re.compile(r"^(主题|话题|内容|议题|方向|背景|目标)(是|为|：|:)")


def _split_todos(text, goal=""):
    parts = []
    for sentence in re.split(r"[。！？!?；;\n]+", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > 16:
            parts.extend(piece.strip() for piece in _ENUM_SPLIT.split(sentence))
        else:
            parts.append(sentence)

    refined = []
    for part in parts:
        if "、" in part and len(part) > 14:
            refined.extend(piece.strip() for piece in part.split("、"))
        else:
            refined.append(part)

    todos = []
    for part in refined:
        part = _clean_content(part)
        if not part or part == goal.strip() or _DESCRIPTIVE_START.match(part):
            continue
        # 「A和B」短并列再拆一层
        if len(part) <= 24 and re.search(r"[\u4e00-\u9fa5]{2,}和[\u4e00-\u9fa5]{2,}", part):
            pieces = [p.strip() for p in re.split(r"和", part) if p.strip()]
            if len(pieces) >= 2 and all(len(p) >= 3 for p in pieces):
                refined.extend(pieces)
                continue
        if len(part) >= 3:
            todos.append(part)

    if not todos:
        todos = [_clean_content(text)[:80] or text[:80]]

    unique = []
    for todo in todos:
        if todo not in unique:
            unique.append(todo)
    return unique[:8]


def _rule_parse(raw_text, now):
    text = raw_text.strip()
    colon_parts = re.split(r"[：:]", text, maxsplit=1)
    if len(colon_parts) == 2 and 2 <= len(colon_parts[0].strip()):
        goal = colon_parts[0].strip()
        body = colon_parts[1].strip()
        goal_source = "冒号前的总述短语"
    else:
        goal = re.split(r"[。！？!?\n]", text, maxsplit=1)[0].strip()
        body = text
        goal_source = "首句"
    goal = goal[:200]

    todos = []
    time_notes = []
    for content in _split_todos(body, goal):
        moment, label, span = detect_key_time(content, now)
        moment, span = _apply_hour_word(content, span, moment)
        cleaned = _strip_time_phrase(content, span) if span else content
        cleaned = cleaned or content
        todos.append({"content": cleaned[:120], "key_time": iso(moment) if moment else ""})
        if moment:
            time_notes.append("「%s」← 识别「%s」→ %s" % (cleaned[:14], label, moment.strftime("%m-%d %H:%M")))

    # 全局兜底：正文没有任何时间时，回看整段原文（如「月底前完成…：」式的总述时间）
    if not time_notes:
        global_moment, global_label, _ = detect_key_time(text, now)
        if global_moment:
            for todo in todos:
                todo["key_time"] = iso(global_moment)
            time_notes.append("全篇时间「%s」→ %s，作为全部待办的默认截止" % (
                global_label, global_moment.strftime("%m-%d %H:%M")))

    basis = [
        {"item": "目标", "detail": "取自%s，共 %d 字" % (goal_source, len(goal))},
        {"item": "待办", "detail": "按句读拆分 %d 条：%s" % (len(todos), "、".join(t["content"][:12] for t in todos))},
    ]
    if time_notes:
        basis.append({"item": "关键时间", "detail": "；".join(time_notes)})
    else:
        basis.append({"item": "关键时间", "detail": "未识别到明确时间表达，确认后将使用计划默认截止时间（7 天后的 18:00）。"})
    return {"goal": goal, "todos": todos, "basis": basis}


# ---------------------------------------------------------------------------
# 大模型解析（可选）：OpenAI 兼容接口，返回结果必须通过结构校验
# ---------------------------------------------------------------------------

_WEEKDAY_FULL = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _llm_system_prompt(now):
    return (
        "你是智能计划管理助手。当前时间：%s（%s）。\n"
        "把用户的一段自然语言计划解析为严格 JSON，不要输出任何解释，格式：\n"
        '{"goal": "总目标", "todos": [{"content": "待办内容", "key_time": "ISO8601 或空字符串"}]}\n'
        "要求：\n"
        "1. goal：若原文含「：」，取冒号前的总述短语；否则概括为一句话；不超过 200 字。\n"
        "2. todos：拆分为 1-8 条可执行待办，动词开头，每条不超过 120 字。\n"
        "3. key_time：把「下周X / 月底 / 明天下午 / X月X日 / N天内」等时间表达换算为 ISO8601"
        "（如 2026-10-21T18:00:00，默认 18:00；按当前时间推算）；仅出现在总述中的时间，"
        "可作为全部待办的统一截止时间重复填入；确实没有任何时间信息才用空字符串。\n"
        "只输出 JSON。"
    ) % (now.strftime("%Y-%m-%d %H:%M"), _WEEKDAY_FULL[now.weekday()])


def llm_configured():
    return bool(os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY"))


def _llm_chat_json(system_prompt, user_prompt, timeout=20):
    """调用大模型并解析 JSON 返回；任何异常或非法 JSON 都返回 None，由调用方兜底。"""
    base_url = (os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
    api_key = os.environ.get("LLM_API_KEY") or ""
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    if not base_url or not api_key:
        return None

    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    request = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, IndexError, ValueError, OSError):
        return None

    content = re.sub(r"^```(?:json)?|```$", "", (content or "").strip(), flags=re.M).strip()
    try:
        return json.loads(content)
    except ValueError:
        return None


def _llm_parse(raw_text, now=None):
    """调用大模型解析；任何异常或结构不合法都返回 None，由调用方兜底。"""
    now = now or datetime.now().replace(microsecond=0)
    return _validate_llm_payload(_llm_chat_json(_llm_system_prompt(now), raw_text))


def _validate_llm_payload(parsed):
    """校验模型返回值；通过后返回与规则引擎一致的中间结构，否则 None。"""
    if not isinstance(parsed, dict):
        return None
    goal = parsed.get("goal")
    if not isinstance(goal, str) or not goal.strip() or len(goal.strip()) > 200:
        return None
    raw_todos = parsed.get("todos")
    if not isinstance(raw_todos, list) or not raw_todos:
        return None

    todos = []
    for item in raw_todos[:12]:
        if not isinstance(item, dict):
            return None
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            return None
        key_time = item.get("key_time") or ""
        moment = parse_dt(key_time) if key_time else None
        todos.append({"content": content.strip()[:120], "key_time": iso(moment) if moment else ""})
    if not todos:
        return None
    return {"goal": goal.strip(), "todos": todos}


# ---------------------------------------------------------------------------
# 对外：解析计划
# ---------------------------------------------------------------------------

def _fill_missing_times(todos, raw_text, now):
    """模型漏提时间时的规则兜底：先按条识别，再回看全篇；返回依据说明条目。"""
    notes = []
    for todo in todos:
        if todo["key_time"]:
            moment = parse_dt(todo["key_time"])
            if moment:
                notes.append("「%s」← 模型返回时间 %s" % (todo["content"][:14], moment.strftime("%m-%d %H:%M")))

    missing = [t for t in todos if not t["key_time"]]
    if not missing:
        return notes

    global_moment, global_label, _ = detect_key_time(raw_text, now)
    global_applied = []
    for todo in missing:
        moment, label, span = detect_key_time(todo["content"], now)
        moment, span = _apply_hour_word(todo["content"], span, moment)
        if moment:
            todo["key_time"] = iso(moment)
            notes.append("「%s」← 规则引擎补全「%s」→ %s" % (
                todo["content"][:14], label, moment.strftime("%m-%d %H:%M")))
        elif global_moment:
            todo["key_time"] = iso(global_moment)
            global_applied.append(todo["content"][:14])
    if global_applied:
        notes.append("全篇时间「%s」→ %s（作为 %d 条待办的统一截止：%s）" % (
            global_label, global_moment.strftime("%m-%d %H:%M"), len(global_applied), "、".join(global_applied)))
    return notes


def parse_plan(raw_text, now=None):
    now = now or datetime.now().replace(microsecond=0)

    llm_result = _llm_parse(raw_text, now) if llm_configured() else None
    if llm_result:
        todos = llm_result["todos"]
        notes = _fill_missing_times(todos, raw_text, now)
        basis = [
            {"item": "解析引擎", "detail": "大模型（%s），返回结果已通过 JSON 结构校验。" % os.environ.get("LLM_MODEL", "gpt-4o-mini")},
            {"item": "目标", "detail": "由模型从原文提炼，共 %d 字" % len(llm_result["goal"])},
            {"item": "待办", "detail": "由模型拆分 %d 条：%s" % (len(todos), "、".join(t["content"][:12] for t in todos))},
        ]
        basis.append({"item": "关键时间", "detail": "；".join(notes) if notes else
                      "原文未包含可识别的时间表达；确认后将使用计划默认截止时间（7 天后的 18:00）。"})
        return {"goal": llm_result["goal"], "todos": todos, "basis": basis, "engine": "大模型"}

    result = _rule_parse(raw_text, now)
    if llm_configured() and llm_result is None:
        engine = "规则引擎（大模型调用失败或返回非结构化，已自动兜底）"
    else:
        engine = "规则引擎（本地兜底，未配置大模型）"
    result["basis"].insert(0, {"item": "解析引擎", "detail": engine})
    result["engine"] = engine
    return result


# ---------------------------------------------------------------------------
# 对外：任务拆解与属性建议（F-201 / F-202）
# ---------------------------------------------------------------------------

_ESTIMATE_RULES = (
    (("会议", "评审", "培训", "演练", "汇报", "宣讲", "分享", "对齐会"), 90),
    (("整理", "汇总", "统计", "分析", "编写", "撰写", "输出", "方案", "文档", "清单",
      "材料", "报告", "设计", "拆解", "复盘", "梳理", "搭建"), 120),
    (("检查", "核对", "校验", "复核", "验收", "确认", "测试", "验证", "定位", "排查"), 60),
    (("提交", "发送", "同步", "通知", "更新", "归档", "上传", "记录", "通报"), 30),
)
_STEP_SPLIT = re.compile(r"[，,]|然后|接着|最后|(?<![合并兼])并(?=[\u4e00-\u9fa5]{2})|再(?=[\u4e00-\u9fa5]{2})")


def _suggest_priority(text):
    if any(word in text for word in ("紧急", "尽快", "优先", "重要", "务必", "马上")):
        return "高"
    if any(word in text for word in ("可选", "参考", "有空", "顺带", "抽空")):
        return "低"
    return "中"


def _suggest_estimate(text):
    for keywords, minutes in _ESTIMATE_RULES:
        if any(word in text for word in keywords):
            return minutes
    return 60


def _split_steps(content):
    parts = []
    for piece in _STEP_SPLIT.split(content):
        piece = _clean_content(piece or "")
        if len(piece) >= 3:
            parts.append(piece)
    if not parts:
        return [content.strip()]
    return parts[:3]


def decompose_todo(content, key_time=None, default_due=None, now=None):
    """把一条待办拆解为可执行步骤，并给出属性建议（标题/优先级/耗时/截止）。"""
    now = now or datetime.now().replace(microsecond=0)
    due = parse_dt(key_time) or parse_dt(default_due) or _at(now + timedelta(days=7))
    tasks = []
    for step in _split_steps(content):
        tasks.append({
            "title": step[:80],
            "detail": ("由待办拆解：%s" % content[:200]) if step != content else "",
            "priority": _suggest_priority(content + " " + step),
            "estimate_minutes": _suggest_estimate(step),
            "due_at": iso(due),
        })
    return tasks


def plan_default_due(todos, now=None):
    """计划默认截止 = 最早的关键时间；没有则 7 天后 18:00。"""
    now = now or datetime.now().replace(microsecond=0)
    moments = [parse_dt(t.get("key_time")) for t in todos]
    moments = [m for m in moments if m]
    if moments:
        return iso(min(moments))
    return iso(_at(now + timedelta(days=7)))


# ---------------------------------------------------------------------------
# 对外：一句话办事（F-801 / REQ-014）
# ---------------------------------------------------------------------------

_PRIORITY_RANK = {"高": 0, "中": 1, "低": 2}


def _fmt_task(task):
    due = parse_dt(task.get("due_at"))
    due_text = due.strftime("%m-%d %H:%M") if due else "未设置截止"
    return "「%s」%s优先级 · 截止 %s · %s" % (
        task.get("title", "未命名"), task.get("priority", "中"), due_text, task.get("status", ""))


def _rule_command(query, store, now=None):
    """规则引擎：命中关键词返回结果或跳转目标，不命中给出可选项（不静默失败）。"""
    text = (query or "").strip()
    now = now or datetime.now().replace(microsecond=0)

    open_tasks = [t for t in store.get("tasks", []) if t.get("status") not in ("已完成", "已取消")]

    def answer(title, lines, link=None, note="结果来自本地任务数据"):
        return {"ok": True, "matched": True, "kind": "answer", "title": title,
                "lines": lines, "note": note, "link": link}

    def redirect(title, lines, path, label):
        return {"ok": True, "matched": True, "kind": "redirect", "title": title,
                "lines": lines, "link": {"path": path, "label": label}}

    if re.search(r"今天|今日", text):
        today = now.date()
        due_list = []
        for task in open_tasks:
            due = parse_dt(task.get("due_at"))
            if due and due.date() <= today:
                due_list.append(task)
        due_list.sort(key=lambda t: t.get("due_at") or "")
        lines = [_fmt_task(t) for t in due_list[:5]]
        if not due_list:
            lines = ["今天没有到期或已逾期的未完成任务，节奏不错。"]
        elif len(due_list) > 5:
            lines.append("……共 %d 项，展开见智能排期。" % len(due_list))
        return answer("今天该做什么（到期 / 逾期优先）", lines, {"path": "/schedule", "label": "打开智能排期"})

    if re.search(r"最紧急|最急|优先做|先做哪|该做|待办", text):
        ranked = sorted(open_tasks, key=lambda t: (
            _PRIORITY_RANK.get(t.get("priority"), 1), t.get("due_at") or "9999"))[:3]
        if ranked:
            lines = [_fmt_task(t) for t in ranked]
        else:
            lines = ["当前没有未完成的任务，可以去录入一条新计划。"]
        return answer("最紧急的未完成任务", lines, {"path": "/schedule", "label": "打开智能排期"})

    if re.search(r"延期|风险|超期|逾期", text):
        risky = [t for t in open_tasks if (t.get("delay_count") or 0) > 0
                 or (parse_dt(t.get("due_at")) and parse_dt(t.get("due_at")) < now)]
        risky.sort(key=lambda t: -(t.get("delay_count") or 0))
        lines = [_fmt_task(t) + "（已延期 %d 次）" % (t.get("delay_count") or 0) for t in risky[:4]]
        if not lines:
            lines = ["未发现延期或超期风险的任务。"]
        return answer("延期与风险任务", lines, {"path": "/schedule", "label": "打开智能排期"})

    if re.search(r"提醒|通知", text):
        unread = [r for r in store.get("reminders", []) if r.get("status") == "未读"]
        lines = ["当前有 %d 条未读提醒。" % len(unread)]
        for item in unread[:3]:
            lines.append("· %s" % (item.get("content") or "")[:50])
        return answer("提醒概览", lines, None, "结果来自提醒中心数据")

    navigation = (
        (r"新建|录入|写.{0,2}计划|新的计划", "/plan/input", "计划录入", "去录入一条新计划"),
        (r"排期|安排|时间轴", "/schedule", "智能排期", "查看任务排期"),
        (r"驾驶舱|看板|概览|统计|报表|指标", "/dashboard", "驾驶舱", "查看核心指标"),
        (r"数据|导入|导出|脱敏", "/data", "数据管理", "进入数据管理"),
        (r"设置|主题|免打扰|渠道", "/settings", "系统设置", "进入系统设置"),
    )
    for pattern, path, name, title in navigation:
        if re.search(pattern, text):
            return redirect("前往「%s」" % name, [title], path, "打开「%s」" % name)

    return {
        "ok": True, "matched": False, "kind": "options",
        "title": "暂不支持这条指令",
        "lines": ["已支持的自然语言指令示例：", "· 显示本周最紧急的任务", "· 今天该做什么",
                  "· 哪些任务有延期风险", "· 提醒概览", "· 新建计划 / 打开智能排期"],
        "options": ["显示最紧急的任务", "今天该做什么", "哪些任务有延期风险", "新建计划"],
    }


# ---------------------------------------------------------------------------
# 一句话办事 · 大模型路径（可选）：意图解析 + 基于本地真实数据作答
# ---------------------------------------------------------------------------

_COMMAND_LINK_PATHS = ("/plan/input", "/plan/confirm", "/schedule", "/dashboard", "/data", "/settings")
_COMMAND_PATH_RE = re.compile(r"^/[A-Za-z0-9/?=&_.\-]*$")


def _command_snapshot(store, now):
    """把任务 / 提醒 / 计划压缩成紧凑快照，供模型引用（含数量与具体条目，均为真实数据）。"""
    tasks = store.get("tasks", [])
    open_tasks = [t for t in tasks if t.get("status") not in ("已完成", "已取消")]
    open_tasks.sort(key=lambda t: (_PRIORITY_RANK.get(t.get("priority"), 1), t.get("due_at") or "9999"))

    lines = ["任务总览：未完成 %d 个，已完成 %d 个，已取消 %d 个。" % (
        len(open_tasks),
        len([t for t in tasks if t.get("status") == "已完成"]),
        len([t for t in tasks if t.get("status") == "已取消"]),
    )]
    lines.append("未完成任务（按优先级与截止时间排序，最多展示 40 个）：")
    for task in open_tasks[:40]:
        due = parse_dt(task.get("due_at"))
        due_text = due.strftime("%m-%d %H:%M") if due else "未设置"
        delay = " · 已延期%d次" % task["delay_count"] if (task.get("delay_count") or 0) > 0 else ""
        lines.append("- 「%s」%s优先级 · 截止 %s · %s%s" % (
            task.get("title", "未命名"), task.get("priority", "中"), due_text, task.get("status", ""), delay))
    if not open_tasks:
        lines.append("- （无）")

    reminders = store.get("reminders", [])
    unread = [r for r in reminders if r.get("status") == "未读"]
    lines.append("")
    lines.append("未读提醒：%d 条（共 %d 条）。" % (len(unread), len(reminders)))
    for item in unread[:8]:
        lines.append("- %s" % (item.get("content") or "")[:60])

    plans = store.get("plans", [])
    lines.append("")
    lines.append("计划列表（最近 %d 个）：" % min(len(plans), 15))
    for plan in plans[-15:]:
        lines.append("- %s | %s | %s" % (
            plan.get("plan_id"), (plan.get("goal") or "（未命名计划）")[:40], plan.get("status") or "待确认"))
    if not plans:
        lines.append("- （无）")
    return "\n".join(lines)


def _llm_command_system(now):
    return (
        "你是「智能计划管理」应用的一句话办事助手。根据【数据快照】解析用户意图并作答。\n"
        "当前时间：%s（%s）。\n"
        "只输出严格 JSON，格式：\n"
        '{"kind": "answer | redirect | options", "title": "简短标题", "lines": ["结果条目"],'
        ' "link": {"path": "页面路径", "label": "按钮文字"} 或 null, "options": ["可选指令"]}\n'
        "要求：\n"
        "1. 只能引用【数据快照】中的真实数据，禁止编造任务、数量或时间；涉及任务时写明任务名与截止时间；\n"
        "2. kind=answer 用于数据类问题（今天该做什么、最紧急的任务、延期风险、提醒、计划概览、统计等）："
        "title 不超过 20 字，lines 1-6 条、每条不超过 60 字；\n"
        "3. kind=redirect 用于用户想打开某个页面：path 只能是 /plan/input、/plan/confirm、/schedule、"
        "/dashboard、/data、/settings 之一（可带 ?plan_id= 查询参数）；\n"
        "4. kind=options 用于无法满足的请求：说明能力边界，并给出 2-4 条 options 指令示例；\n"
        "5. options 是用户可点击继续执行的指令（不超过 4 条，每条不超过 20 字），不需要时省略该字段；\n"
        "6. 全部使用中文，不要 Markdown，不要输出 JSON 之外的任何内容。"
    ) % (now.strftime("%Y-%m-%d %H:%M"), _WEEKDAY_FULL[now.weekday()])


def _validate_command_payload(parsed):
    """严格校验模型返回的指令结果；任何不合规都返回 None。"""
    if not isinstance(parsed, dict):
        return None
    kind = parsed.get("kind")
    if kind not in ("answer", "redirect", "options"):
        return None
    title = parsed.get("title")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 40:
        return None
    raw_lines = parsed.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        return None
    lines = []
    for line in raw_lines[:6]:
        if not isinstance(line, str) or not line.strip():
            return None
        lines.append(line.strip()[:120])

    link = None
    raw_link = parsed.get("link")
    if raw_link is not None:
        if not isinstance(raw_link, dict):
            return None
        path = raw_link.get("path")
        label = raw_link.get("label")
        if not isinstance(path, str) or not isinstance(label, str) or not label.strip():
            return None
        path = path.strip()
        if not any(path == item or path.startswith(item + "?") for item in _COMMAND_LINK_PATHS):
            return None
        if not _COMMAND_PATH_RE.match(path):
            return None
        link = {"path": path, "label": label.strip()[:20]}
    if kind == "redirect" and not link:
        return None

    options = []
    raw_options = parsed.get("options") or []
    if not isinstance(raw_options, list):
        return None
    for item in raw_options[:4]:
        if not isinstance(item, str) or not item.strip():
            return None
        options.append(item.strip()[:30])
    return {"kind": kind, "title": title.strip()[:40], "lines": lines, "link": link, "options": options}


def _llm_command(query, store, now):
    """大模型解析指令意图；返回与规则引擎同构的结果，任何失败返回 None 由调用方兜底。"""
    parsed = _llm_chat_json(
        _llm_command_system(now),
        "【数据快照】\n%s\n\n用户指令：%s" % (_command_snapshot(store, now), query),
    )
    checked = _validate_command_payload(parsed)
    if not checked:
        return None
    result = {
        "ok": True,
        "matched": checked["kind"] != "options",
        "kind": checked["kind"],
        "title": checked["title"],
        "lines": checked["lines"],
        "note": "由大模型（%s）解析意图，结果基于本地真实数据。" % os.environ.get("LLM_MODEL", "gpt-4o-mini"),
    }
    if checked["link"]:
        result["link"] = checked["link"]
    if checked["options"]:
        result["options"] = checked["options"]
    return result


def handle_command(query, store, now=None):
    """一句话办事入口：配置了大模型则优先由其解析意图，失败自动兜底规则引擎。"""
    now = now or datetime.now().replace(microsecond=0)
    text = (query or "").strip()
    if not text:
        return {"ok": False, "message": "请输入要执行的指令。"}

    llm_result = _llm_command(text, store, now) if llm_configured() else None
    if llm_result:
        return llm_result

    result = _rule_command(text, store, now)
    if llm_configured():
        suffix = "（大模型调用失败或返回非结构化，已由规则引擎兜底）"
        result["note"] = (result.get("note") + suffix) if result.get("note") else suffix
    return result
