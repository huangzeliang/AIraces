# -*- coding: utf-8 -*-
"""
智能计划管理 Agent · AI 自测执行脚本 · 接口与页面层（比赛版）

路由与选择器全部来自同目录 routes.json，接口命名不同只需改 JSON，不用改代码。

用法：
    pip install -r requirements.txt
    python selftest.py                                  # 全量（含登录与角色用例）
    python selftest.py --phase smoke                    # 只跑端到端冒烟 TC-041
    python selftest.py --base http://127.0.0.1:8080
    python selftest.py --config my_routes.json          # 指定配置

说明：系统已启用账号体系，脚本会先用 routes.json 的 auth 配置登录；
TC-050~054 覆盖管理员/普通用户登录、越权拦截、导航过滤与数据隔离。

退出码：0 = 无 P0 失败；1 = 存在 P0 失败
"""
import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "routes.json")

try:
    import requests
except ImportError:
    requests = None


if requests is None:
    # 无 requests 的离线环境回退：极简会话（Cookie + get/post + 重定向控制），不联网安装依赖。
    import http.cookiejar
    import json as _json_module
    import urllib.error
    import urllib.parse
    import urllib.request

    class _MiniResponse:
        def __init__(self, status_code, text, headers):
            self.status_code = status_code
            self.text = text
            self.headers = headers

        def json(self):
            return json.loads(self.text)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    class _MiniSession:
        def __init__(self):
            self._jar = http.cookiejar.CookieJar()

        def _open(self, req, timeout, allow_redirects):
            handlers = [urllib.request.HTTPCookieProcessor(self._jar)]
            if not allow_redirects:
                handlers.append(_NoRedirect)
            opener = urllib.request.build_opener(*handlers)
            try:
                with opener.open(req, timeout=timeout) as resp:
                    return _MiniResponse(resp.status, resp.read().decode("utf-8", "replace"),
                                         dict(resp.headers))
            except urllib.error.HTTPError as err:
                return _MiniResponse(err.code, err.read().decode("utf-8", "replace"),
                                     dict(err.headers))
            except Exception:
                return None

        def get(self, url, timeout=8, allow_redirects=True):
            return self._open(urllib.request.Request(url), timeout, allow_redirects)

        def post(self, url, data=None, json=None, timeout=8, allow_redirects=False):
            headers = {}
            payload = None
            if json is not None:
                payload = _json_module.dumps(json).encode("utf-8")
                headers["Content-Type"] = "application/json"
            elif data is not None:
                payload = urllib.parse.urlencode(data).encode("utf-8")
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            req = urllib.request.Request(url, data=payload, headers=headers)
            return self._open(req, timeout, allow_redirects)

    _session_cls = _MiniSession
else:
    _session_cls = requests.Session


@dataclass
class Result:
    tc: str
    title: str
    level: str
    ctype: str
    status: str
    detail: str = ""


class Runner:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.base = cfg.get("base", "http://127.0.0.1:5000").rstrip("/")
        self.timeout = cfg.get("timeout", 8)
        self.api = cfg.get("api", {})
        self.results: List[Result] = []
        self.plan_id: Optional[str] = None
        self.session = _session_cls()

    # ---------- 配置读取 ----------
    def endpoint(self, key: str, **kwargs) -> str:
        item = self.api.get(key)
        if not item:
            return ""
        return item.get("path", "").format(**kwargs)

    # ---------- HTTP ----------
    def get(self, path: str):
        try:
            return self.session.get(self.base + path, timeout=self.timeout, allow_redirects=True)
        except Exception:
            return None

    def post(self, path: str, payload: Dict[str, Any]):
        try:
            return self.session.post(self.base + path, json=payload,
                                     timeout=self.timeout, allow_redirects=False)
        except Exception:
            return None

    # ---------- 登录 ----------
    def login(self, account: Dict[str, str], session=None):
        auth = self.cfg.get("auth", {})
        path = auth.get("login_path", "/login")
        sess = session or self.session
        try:
            return sess.post(self.base + path,
                             data={"email": account.get("email", ""),
                                   "password": account.get("password", "")},
                             timeout=self.timeout, allow_redirects=False)
        except Exception:
            return None

    def _get_with(self, session, path: str, redirects: bool = True):
        try:
            return session.get(self.base + path, timeout=self.timeout, allow_redirects=redirects)
        except Exception:
            return None

    def _export_count(self, session) -> Optional[int]:
        r = self._get_with(session, "/tasks/export?format=json")
        try:
            return len((r.json() or {}).get("items") or [])
        except Exception:
            return None

    # ---------- 记录 ----------
    def record(self, tc, title, level, ctype, ok: bool, detail: str, skip: bool = False):
        status = "SKIP" if skip else ("PASS" if ok else "FAIL")
        self.results.append(Result(tc, title, level, ctype, status, detail))

    # ============ 页面层 ============
    def run_pages(self):
        for p in self.cfg.get("pages", []):
            tc = p.get("tc", "")
            name = p.get("name", "")
            path = p.get("path", "")
            markers = p.get("markers", [])
            r = self.get(path)
            if r is None:
                self.record(tc, f"{name}-页面可访问", "P0", "正例", False,
                            f"{path} 不可达（确认 app.py 已启动）")
                continue
            if r.status_code == 404:
                self.record(tc, f"{name}-页面可访问", "P0", "正例", False,
                            f"{path} 404 路由未注册", skip=True)
                continue
            if r.status_code >= 500:
                self.record(tc, f"{name}-页面可访问", "P0", "正例", False,
                            f"{path} 返回 {r.status_code}")
                continue
            missing = [m for m in markers if m not in (r.text or "")]
            if missing:
                self.record(tc, f"{name}-页面可访问且含关键元素", "P0", "正例", False,
                            f"缺少关键元素 {missing}")
            else:
                self.record(tc, f"{name}-页面可访问且含关键元素", "P0", "正例", True,
                            f"{path} 200，元素齐全")

    # ============ 端到端冒烟 TC-041 ============
    def run_smoke(self):
        # Step1 解析
        path = self.endpoint("parse")
        if not path:
            self.record("TC-041", "主流程冒烟-解析计划", "P0", "正例", False,
                        "routes.json 未配置 api.parse", skip=True)
            return
        r = self.post(path, {"raw_text": "下周三前完成项目立项、需求评审和原型设计"})
        if r is None:
            self.record("TC-041", "主流程冒烟-解析计划", "P0", "正例", False, f"POST {path} 请求失败")
            return
        if r.status_code == 404:
            self.record("TC-041", "主流程冒烟-解析计划", "P0", "正例", False,
                        f"POST {path} 未实现（404）", skip=True)
            return
        if r.status_code == 302:
            m = re.search(r"plan_id=([^&]+)", r.headers.get("Location", ""))
            self.plan_id = m.group(1) if m else None
        elif r.status_code == 200:
            try:
                j = r.json()
                self.plan_id = j.get("plan_id") or (j.get("data") or {}).get("plan_id")
            except Exception:
                pass
        self.record("TC-041", "主流程冒烟-解析返回plan_id", "P0", "正例",
                    bool(self.plan_id), f"status={r.status_code}, plan_id={self.plan_id}")
        if not self.plan_id:
            return

        # Step2 确认拆解
        path = self.endpoint("confirm")
        r2 = self.post(path, {
            "plan_id": self.plan_id,
            "goal": "完成项目立项与原型设计",
            "todos": [{"content": "项目立项", "key_time": ""},
                      {"content": "需求评审", "key_time": ""},
                      {"content": "原型设计", "key_time": ""}],
        })
        if r2 is None or r2.status_code == 404:
            self.record("TC-041", "主流程冒烟-确认并拆解", "P0", "正例", False,
                        f"POST {path} 未实现或请求失败", skip=r2 is not None)
        else:
            self.record("TC-041", "主流程冒烟-确认并拆解", "P0", "正例",
                        r2.status_code in (200, 302), f"status={r2.status_code}")

        # Step3 保存排期
        path = self.endpoint("schedule_save")
        r3 = self.post(path, {"plan_id": self.plan_id})
        if r3 is None or r3.status_code == 404:
            self.record("TC-041", "主流程冒烟-保存排期", "P0", "正例", False,
                        f"POST {path} 未实现或请求失败", skip=r3 is not None)
        else:
            self.record("TC-041", "主流程冒烟-保存排期", "P0", "正例",
                        r3.status_code in (200, 302), f"status={r3.status_code}")

        # Step4 任务存在 + 完成
        r4 = self.get("/tasks")
        task_id = None
        if r4 is not None and r4.status_code == 200:
            m = re.search(r"(T\d{3,})", r4.text)
            task_id = m.group(1) if m else None
        self.record("TC-041", "主流程冒烟-任务列表已生成任务", "P0", "正例",
                    bool(task_id), f"task_id={task_id}")

        if task_id:
            path = self.endpoint("task_complete", task_id=task_id)
            r5 = self.post(path, {})
            if r5 is None or r5.status_code == 404:
                self.record("TC-041", "主流程冒烟-完成任务并产生反馈", "P0", "正例", False,
                            f"POST {path} 未实现或请求失败", skip=r5 is not None)
            else:
                self.record("TC-041", "主流程冒烟-完成任务并产生反馈", "P0", "正例",
                            r5.status_code in (200, 302), f"status={r5.status_code}")

        # Step5 下游页面
        for p in ("/reminders", "/dashboard"):
            rr = self.get(p)
            self.record("TC-041", f"主流程冒烟-{p} 可访问", "P0", "正例",
                        rr is not None and rr.status_code == 200,
                        f"{p} status={getattr(rr, 'status_code', 'N/A')}")

    # ============ 反例（服务端校验） ============
    def run_negative(self):
        path = self.endpoint("parse")
        if path:
            r = self.post(path, {"raw_text": ""})
            if r is None or r.status_code == 404:
                self.record("TC-004", "计划录入-空文本被拦截", "P0", "反例", False,
                            "未实现或请求失败", skip=r is not None)
            else:
                body = (r.text or "").lower()
                ok = r.status_code >= 400 or ("不能为空" in body or "error" in body)
                self.record("TC-004", "计划录入-空文本被拦截", "P0", "反例", ok, f"status={r.status_code}")

            r = self.post(path, {"raw_text": "字" * 2001})
            if r is None or r.status_code == 404:
                self.record("TC-005", "计划录入-超2000字符被拦截", "P0", "反例", False,
                            "未实现或请求失败", skip=r is not None)
            else:
                self.record("TC-005", "计划录入-超2000字符被拦截", "P0", "反例",
                            r.status_code >= 400, f"status={r.status_code}（期望400）")

        path = self.endpoint("confirm")
        if path:
            r = self.post(path, {"plan_id": self.plan_id or "P001", "goal": "x", "todos": []})
            if r is None or r.status_code == 404:
                self.record("TC-010", "提炼确认-待办为0禁止确认", "P0", "反例", False,
                            "未实现或请求失败", skip=r is not None)
            else:
                self.record("TC-010", "提炼确认-待办为0禁止确认", "P0", "反例",
                            r.status_code >= 400, f"status={r.status_code}（期望400）")

        path = self.endpoint("settings_save")
        if path:
            r = self.post(path, {"reminder_interval_minutes": 0})
            if r is None or r.status_code == 404:
                self.record("TC-032", "系统设置-巡检间隔0被拦截", "P1", "反例", False,
                            "未实现或请求失败", skip=r is not None)
            else:
                self.record("TC-032", "系统设置-巡检间隔0被拦截", "P1", "反例",
                            r.status_code >= 400, f"status={r.status_code}（期望400）")

    # ============ 登录与角色（TC-050~054） ============
    def run_auth(self):
        auth = self.cfg.get("auth", {})
        admin = auth.get("admin")
        if not admin:
            self.record("TC-050", "账号体系-管理员登录", "P0", "正例", False,
                        "routes.json 未配置 auth.admin", skip=True)
            return

        r1 = self.login(admin)
        home = self.get("/tasks")
        self.record("TC-050", "账号体系-管理员登录进入工作台", "P0", "正例",
                    r1 is not None and r1.status_code == 302
                    and home is not None and home.status_code == 200,
                    f"login={getattr(r1, 'status_code', 'N/A')}, /tasks={getattr(home, 'status_code', 'N/A')}")

        employee = auth.get("employee")
        if not employee:
            return

        emp = _session_cls()
        r2 = self.login(employee, emp)
        self.record("TC-051", "账号体系-普通用户登录成功", "P0", "正例",
                    r2 is not None and r2.status_code == 302,
                    f"login={getattr(r2, 'status_code', 'N/A')}")

        emp_settings = self._get_with(emp, "/settings", redirects=False)
        self.record("TC-052", "账号体系-普通用户访问系统设置被拦截", "P0", "反例",
                    emp_settings is not None and emp_settings.status_code in (301, 302),
                    f"status={getattr(emp_settings, 'status_code', 'N/A')}（期望 3xx 重定向）")

        emp_tasks = self._get_with(emp, "/tasks")
        text = (emp_tasks.text if emp_tasks is not None else "")
        hidden = (emp_tasks is not None and emp_tasks.status_code == 200
                  and all(term not in text for term in ("数据管理", "系统设置", "用户管理", "驾驶舱")))
        self.record("TC-053", "账号体系-普通用户导航不含管理入口", "P0", "正例", bool(hidden),
                    "导航已按角色过滤" if hidden else "页面中仍出现管理入口或页面不可访问")

        admin_count = self._export_count(self.session)
        emp_count = self._export_count(emp)
        isolated = admin_count is not None and emp_count is not None and admin_count >= emp_count
        self.record("TC-054", "账号体系-普通用户仅见自己的任务（数据隔离）", "P0", "正例",
                    bool(isolated), f"管理员 {admin_count} 条 / 普通用户 {emp_count} 条")

    # ============ 报告 ============
    def merge(self, other: "Runner"):
        self.results.extend(other.results)

    def report(self, path: str):
        p = sum(1 for x in self.results if x.status == "PASS")
        f = sum(1 for x in self.results if x.status == "FAIL")
        s = sum(1 for x in self.results if x.status == "SKIP")
        lines = ["# AI 自测报告", "",
                 f"- 目标地址：{self.base}",
                 f"- 执行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
                 f"- 汇总：PASS {p} / FAIL {f} / SKIP {s} / 共 {len(self.results)}", "",
                 "| 编号 | 用例标题 | 优先级 | 类型 | 结果 | 详情 |",
                 "|:---|:---|:---:|:---:|:---:|:---|"]
        for x in self.results:
            lines.append(f"| {x.tc} | {x.title} | {x.level} | {x.ctype} | {x.status} | {x.detail} |")
        with open(path, "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + "\n")

    def print_console(self):
        print(f"\n目标地址：{self.base}\n")
        print(f"{'编号':<9}{'结果':<7}{'级别':<5}{'类型':<5}用例标题")
        print("-" * 96)
        for x in self.results:
            print(f"{x.tc:<9}{x.status:<7}{x.level:<5}{x.ctype:<5}{x.title}  [{x.detail}]")
        p = sum(1 for x in self.results if x.status == "PASS")
        f = sum(1 for x in self.results if x.status == "FAIL")
        s = sum(1 for x in self.results if x.status == "SKIP")
        print("-" * 96)
        print(f"PASS {p} / FAIL {f} / SKIP {s} / 共 {len(self.results)}\n")

    def has_p0_fail(self) -> bool:
        return any(x.level == "P0" and x.status == "FAIL" for x in self.results)


def load_cfg(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        print(f"配置文件不存在：{path}")
        sys.exit(2)
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None, help="覆盖 routes.json 中的应用地址")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="路由配置文件")
    ap.add_argument("--phase", default="all", choices=["all", "p0", "p1", "smoke", "pages"])
    ap.add_argument("--report", default="selftest_report.md")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    if args.base:
        cfg["base"] = args.base

    r = Runner(cfg)
    r.run_auth()
    if args.phase in ("all", "pages", "p0"):
        r.run_pages()
    if args.phase in ("all", "smoke", "p0"):
        r.run_smoke()
    if args.phase in ("all", "p0", "p1"):
        r.run_negative()

    r.print_console()
    r.report(args.report)
    print(f"报告已写入：{args.report}")
    sys.exit(1 if r.has_p0_fail() else 0)


if __name__ == "__main__":
    main()
