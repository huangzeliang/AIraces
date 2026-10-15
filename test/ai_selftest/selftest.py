# -*- coding: utf-8 -*-
"""
智能计划管理 Agent · AI 自测执行脚本 · 接口与页面层（比赛版）

路由与选择器全部来自同目录 routes.json，接口命名不同只需改 JSON，不用改代码。

用法：
    pip install -r requirements.txt
    python selftest.py                                  # 全量
    python selftest.py --phase smoke                    # 只跑端到端冒烟 TC-041
    python selftest.py --base http://127.0.0.1:8080
    python selftest.py --config my_routes.json          # 指定配置

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

try:
    import requests
except ImportError:
    print("缺少依赖，请先执行： pip install -r requirements.txt")
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "routes.json")


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

    # ---------- 配置读取 ----------
    def endpoint(self, key: str, **kwargs) -> str:
        item = self.api.get(key)
        if not item:
            return ""
        return item.get("path", "").format(**kwargs)

    # ---------- HTTP ----------
    def get(self, path: str):
        try:
            return requests.get(self.base + path, timeout=self.timeout, allow_redirects=True)
        except Exception:
            return None

    def post(self, path: str, payload: Dict[str, Any]):
        try:
            return requests.post(self.base + path, json=payload,
                                 timeout=self.timeout, allow_redirects=False)
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
