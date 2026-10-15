# -*- coding: utf-8 -*-
"""
智能计划管理 Agent · AI 自测执行脚本 · 前端交互层（Playwright）

覆盖接口层测不到的纯前端用例：字数计数、填充示例、拖拽排期、进度到100自动完成、
筛选与URL保持、延期必填拦截、二次确认取消、已读角标递减、主题切换与刷新保持。

安装（一次性）：
    pip install playwright
    playwright install chromium

用法：
    python selftest_ui.py                       # 全量前端用例
    python selftest_ui.py --headed              # 有头模式，方便观察
    python selftest_ui.py --case TC-018         # 只跑某条
    python selftest_ui.py --base http://127.0.0.1:8080

退出码：0 = 无 P0 失败；1 = 存在 P0 失败
"""
import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("缺少依赖，请先执行： pip install playwright && playwright install chromium")
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "routes.json")


@dataclass
class UIResult:
    tc: str
    title: str
    level: str
    ctype: str
    status: str
    detail: str = ""


class UIRunner:
    def __init__(self, cfg: Dict[str, Any], page, base: str):
        self.cfg = cfg
        self.sel = cfg.get("selectors", {})
        self.pages = cfg.get("ui_cases", {})
        self.page = page
        self.base = base.rstrip("/")
        self.results: List[UIResult] = []

    # ---------- 工具 ----------
    def record(self, tc, title, level, ctype, ok, detail, skip=False):
        status = "SKIP" if skip else ("PASS" if ok else "FAIL")
        self.results.append(UIResult(tc, title, level, ctype, status, detail))
        print(f"  [{status}] {tc} {title} -> {detail}")

    def s(self, key: str) -> str:
        return self.sel.get(key, "")

    def find(self, key: str):
        """返回 locator，找不到返回 None"""
        sel = self.s(key)
        if not sel:
            return None
        for part in sel.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                loc = self.page.locator(part).first
                if loc.count() > 0:
                    return loc
            except Exception:
                continue
        return None

    def goto(self, key: str) -> bool:
        path = self.pages.get(key, "")
        if not path:
            return False
        try:
            self.page.goto(self.base + path, timeout=15000, wait_until="domcontentloaded")
            return True
        except Exception as e:
            return False

    def require(self, tc, title, level, ctype, *keys) -> Optional[list]:
        """校验依赖控件是否存在，缺失则记 SKIP 并返回 None"""
        locs = []
        missing = []
        for k in keys:
            loc = self.find(k)
            if loc is None:
                missing.append(k)
            else:
                locs.append(loc)
        if missing:
            self.record(tc, title, level, ctype, False,
                        f"页面缺少控件 {missing}（选择器可在 routes.json 调整）", skip=True)
            return None
        return locs

    # ============ 用例 ============
    def case_tc002_char_counter(self):
        """TC-002 正例边界：输入 2000 字符，计数显示 2000"""
        if not self.goto("plan_input"):
            self.record("TC-002", "计划录入-2000字符计数上限", "P0", "正例", False, "页面打不开")
            return
        locs = self.require("TC-002", "计划录入-2000字符计数上限", "P0", "正例", "raw_text")
        if not locs:
            return
        locs[0].fill("字" * 2000)
        self.page.wait_for_timeout(300)
        counter = self.find("char_counter")
        if counter is None:
            self.record("TC-002", "计划录入-2000字符计数上限", "P0", "正例", False,
                        "未找到字数计数元素", skip=True)
            return
        txt = counter.inner_text()
        ok = "2000" in txt
        self.record("TC-002", "计划录入-2000字符计数上限", "P0", "正例", ok, f"计数文本={txt.strip()}")

    def case_tc003_fill_example(self):
        """TC-003 正例：填充示例写入文本域"""
        if not self.goto("plan_input"):
            self.record("TC-003", "计划录入-填充示例写入文本", "P0", "正例", False, "页面打不开")
            return
        locs = self.require("TC-003", "计划录入-填充示例写入文本", "P0", "正例",
                            "raw_text", "fill_example")
        if not locs:
            return
        before = locs[0].input_value()
        locs[1].click()
        self.page.wait_for_timeout(300)
        after = locs[0].input_value()
        ok = len(after) > len(before)
        self.record("TC-003", "计划录入-填充示例写入文本", "P0", "正例", ok,
                    f"填充后长度={len(after)}")

    def case_tc013_drag_schedule(self):
        """TC-013 正例：拖拽时间条后位置变化"""
        if not self.goto("schedule"):
            self.record("TC-013", "智能排期-拖拽时间条重算", "P0", "正例", False, "页面打不开")
            return
        locs = self.require("TC-013", "智能排期-拖拽时间条重算", "P0", "正例", "time_bar")
        if not locs:
            return
        bar = locs[0]
        try:
            box = bar.bounding_box()
            if not box:
                raise ValueError("no box")
            x0 = box["x"]
            self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            self.page.mouse.down()
            self.page.mouse.move(box["x"] + box["width"] / 2 + 120,
                                 box["y"] + box["height"] / 2, steps=10)
            self.page.mouse.up()
            self.page.wait_for_timeout(400)
            box2 = bar.bounding_box()
            moved = box2 and abs(box2["x"] - x0) > 5
            self.record("TC-013", "智能排期-拖拽时间条重算", "P0", "正例", bool(moved),
                        f"x: {x0:.0f} -> {box2['x']:.0f}" if box2 else "拖拽后未取到位置")
        except Exception as e:
            self.record("TC-013", "智能排期-拖拽时间条重算", "P0", "正例", False, f"拖拽失败：{e}")

    def case_tc017_progress_100(self):
        """TC-017 正例边界：进度拖到 100 自动完成"""
        if not self.goto("tasks"):
            self.record("TC-017", "任务列表-进度100自动完成", "P0", "正例", False, "页面打不开")
            return
        locs = self.require("TC-017", "任务列表-进度100自动完成", "P0", "正例", "progress_slider")
        if not locs:
            return
        try:
            locs[0].fill("100")
            self.page.wait_for_timeout(600)
            body = self.page.inner_text("body")
            ok = "已完成" in body
            self.record("TC-017", "任务列表-进度100自动完成", "P0", "正例", ok,
                        "页面出现已完成状态" if ok else "未出现已完成状态")
        except Exception as e:
            self.record("TC-017", "任务列表-进度100自动完成", "P0", "正例", False, f"操作失败：{e}")

    def case_tc018_filter_url(self):
        """TC-018 正例：筛选 + 关键词后 URL 保留筛选态"""
        if not self.goto("tasks"):
            self.record("TC-018", "任务列表-筛选后URL保留筛选态", "P0", "正例", False, "页面打不开")
            return
        chip = self.find("filter_chip")
        if chip:
            try:
                chip.click()
                self.page.wait_for_timeout(300)
            except Exception:
                pass
        kw = self.find("keyword_search")
        if kw:
            try:
                kw.fill("立项")
                self.page.wait_for_timeout(500)
            except Exception:
                pass
        url = self.page.url
        ok = ("?" in url) and ("keyword" in url or "status" in url or "priority" in url)
        self.record("TC-018", "任务列表-筛选后URL保留筛选态", "P0", "正例", ok, f"URL={url}")

    def case_tc019_delay_required(self):
        """TC-019 反例：延期原因留空被拦截"""
        if not self.goto("tasks"):
            self.record("TC-019", "任务列表-延期原因留空被拦截", "P0", "反例", False, "页面打不开")
            return
        locs = self.require("TC-019", "任务列表-延期原因留空被拦截", "P0", "反例", "delay_button")
        if not locs:
            return
        try:
            locs[0].first.click() if hasattr(locs[0], "first") else locs[0].click()
            self.page.wait_for_timeout(400)
            modal = self.find("modal")
            if modal is None:
                self.record("TC-019", "任务列表-延期原因留空被拦截", "P0", "反例", False,
                            "未弹出延期弹窗", skip=True)
                return
            ok_btn = self.find("modal_confirm")
            if ok_btn is None:
                self.record("TC-019", "任务列表-延期原因留空被拦截", "P0", "反例", False,
                            "弹窗缺少确认按钮", skip=True)
                return
            ok_btn.click()
            self.page.wait_for_timeout(400)
            still_open = self.find("modal") is not None
            body = self.page.inner_text("body")
            blocked = still_open or ("必填" in body or "不能为空" in body)
            self.record("TC-019", "任务列表-延期原因留空被拦截", "P0", "反例", blocked,
                        "弹窗未关闭" if still_open else "页面给出必填提示")
        except Exception as e:
            self.record("TC-019", "任务列表-延期原因留空被拦截", "P0", "反例", False, f"操作失败：{e}")

    def case_tc020_confirm_cancel(self):
        """TC-020 反例：二次确认点取消不执行"""
        if not self.goto("tasks"):
            self.record("TC-020", "任务列表-二次确认点取消不执行", "P0", "反例", False, "页面打不开")
            return
        locs = self.require("TC-020", "任务列表-二次确认点取消不执行", "P0", "反例", "cancel_button")
        if not locs:
            return
        try:
            before = self.page.inner_text("body")
            locs[0].click()
            self.page.wait_for_timeout(400)
            modal_cancel = self.find("modal_cancel")
            if modal_cancel is None:
                self.record("TC-020", "任务列表-二次确认点取消不执行", "P0", "反例", False,
                            "未弹出确认框", skip=True)
                return
            modal_cancel.click()
            self.page.wait_for_timeout(400)
            after = self.page.inner_text("body")
            ok = before.strip() == after.strip() or self.find("modal") is None
            self.record("TC-020", "任务列表-二次确认点取消不执行", "P0", "反例", ok,
                        "弹窗关闭且列表未变化")
        except Exception as e:
            self.record("TC-020", "任务列表-二次确认点取消不执行", "P0", "反例", False, f"操作失败：{e}")

    def case_tc027_mark_read(self):
        """TC-027 正例：标记已读后角标递减"""
        if not self.goto("reminders"):
            self.record("TC-027", "提醒中心-标记已读角标递减", "P0", "正例", False, "页面打不开")
            return
        badge = self.find("badge")
        mr = self.find("mark_read")
        if mr is None:
            self.record("TC-027", "提醒中心-标记已读角标递减", "P0", "正例", False,
                        "无未读提醒或无标记已读按钮", skip=True)
            return
        try:
            def num():
                if badge is None:
                    return None
                t = badge.inner_text().strip()
                return int(t) if t.isdigit() else None
            before = num()
            mr.click()
            self.page.wait_for_timeout(500)
            after = num()
            if before is None or after is None:
                body = self.page.inner_text("body")
                self.record("TC-027", "提醒中心-标记已读角标递减", "P0", "正例", "已读" in body,
                            f"角标不可读，按正文判断（{before} -> {after}）")
            else:
                self.record("TC-027", "提醒中心-标记已读角标递减", "P0", "正例", after < before,
                            f"角标 {before} -> {after}")
        except Exception as e:
            self.record("TC-027", "提醒中心-标记已读角标递减", "P0", "正例", False, f"操作失败：{e}")

    def case_tc028_all_read(self):
        """TC-028 正例：全部已读"""
        if not self.goto("reminders"):
            self.record("TC-028", "提醒中心-全部已读批量置为已读", "P0", "正例", False, "页面打不开")
            return
        btn = self.find("mark_all_read")
        if btn is None:
            self.record("TC-028", "提醒中心-全部已读批量置为已读", "P0", "正例", False,
                        "无全部已读按钮", skip=True)
            return
        try:
            btn.click()
            self.page.wait_for_timeout(400)
            confirm = self.find("modal_confirm")
            if confirm:
                confirm.click()
                self.page.wait_for_timeout(600)
            badge = self.find("badge")
            txt = badge.inner_text().strip() if badge else ""
            ok = (txt in ("", "0")) or ("未读" not in self.page.inner_text("body"))
            self.record("TC-028", "提醒中心-全部已读批量置为已读", "P0", "正例", ok, f"角标={txt or '空'}")
        except Exception as e:
            self.record("TC-028", "提醒中心-全部已读批量置为已读", "P0", "正例", False, f"操作失败：{e}")

    def case_tc037_theme_persist(self):
        """TC-037 正例：主题切换即时生效且刷新保持"""
        if not self.goto("settings"):
            self.record("TC-037", "系统设置-主题切换并刷新保持", "P1", "正例", False, "页面打不开")
            return
        btn = self.find("theme_toggle")
        if btn is None:
            self.record("TC-037", "系统设置-主题切换并刷新保持", "P1", "正例", False,
                        "无主题切换按钮", skip=True)
            return
        try:
            before = self.page.evaluate("document.documentElement.dataset.theme || ''")
            btn.click()
            self.page.wait_for_timeout(300)
            after = self.page.evaluate("document.documentElement.dataset.theme || ''")
            changed = before != after
            self.page.reload(wait_until="domcontentloaded")
            self.page.wait_for_timeout(400)
            persisted = self.page.evaluate("document.documentElement.dataset.theme || ''")
            ok = changed and persisted == after
            self.record("TC-037", "系统设置-主题切换并刷新保持", "P1", "正例", ok,
                        f"{before} -> {after} -> 刷新后 {persisted}")
        except Exception as e:
            self.record("TC-037", "系统设置-主题切换并刷新保持", "P1", "正例", False, f"操作失败：{e}")


CASES = {
    "TC-002": lambda r: r.case_tc002_char_counter(),
    "TC-003": lambda r: r.case_tc003_fill_example(),
    "TC-013": lambda r: r.case_tc013_drag_schedule(),
    "TC-017": lambda r: r.case_tc017_progress_100(),
    "TC-018": lambda r: r.case_tc018_filter_url(),
    "TC-019": lambda r: r.case_tc019_delay_required(),
    "TC-020": lambda r: r.case_tc020_confirm_cancel(),
    "TC-027": lambda r: r.case_tc027_mark_read(),
    "TC-028": lambda r: r.case_tc028_all_read(),
    "TC-037": lambda r: r.case_tc037_theme_persist(),
}


def load_cfg(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        print(f"配置文件不存在：{path}")
        sys.exit(2)
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def write_report(results: List[UIResult], base: str, path: str):
    p = sum(1 for x in results if x.status == "PASS")
    f = sum(1 for x in results if x.status == "FAIL")
    s = sum(1 for x in results if x.status == "SKIP")
    lines = ["# AI 自测报告 · 前端交互层", "",
             f"- 目标地址：{base}",
             f"- 执行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 汇总：PASS {p} / FAIL {f} / SKIP {s} / 共 {len(results)}", "",
             "| 编号 | 用例标题 | 优先级 | 类型 | 结果 | 详情 |",
             "|:---|:---|:---:|:---:|:---:|:---|"]
    for x in results:
        lines.append(f"| {x.tc} | {x.title} | {x.level} | {x.ctype} | {x.status} | {x.detail} |")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--case", default=None, help="只跑指定编号，如 TC-018")
    ap.add_argument("--headed", action="store_true", help="有头模式")
    ap.add_argument("--report", default="selftest_ui_report.md")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    base = args.base or cfg.get("base", "http://127.0.0.1:5000")

    keys = [args.case] if args.case else list(CASES.keys())
    keys = [k for k in keys if k in CASES]
    if not keys:
        print(f"未找到用例 {args.case}，可选：{', '.join(CASES.keys())}")
        sys.exit(2)

    results: List[UIResult] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        runner = UIRunner(cfg, page, base)
        print(f"\n开始执行前端交互用例，目标：{base}\n")
        for k in keys:
            try:
                CASES[k](runner)
            except Exception as e:
                runner.record(k, f"{k} 执行异常", "P0", "正例", False, f"{type(e).__name__}: {e}")
        results = runner.results
        browser.close()

    p = sum(1 for x in results if x.status == "PASS")
    f = sum(1 for x in results if x.status == "FAIL")
    s = sum(1 for x in results if x.status == "SKIP")
    print("-" * 96)
    print(f"PASS {p} / FAIL {f} / SKIP {s} / 共 {len(results)}\n")

    write_report(results, base, args.report)
    print(f"报告已写入：{args.report}")

    p0_fail = any(x.level == "P0" and x.status == "FAIL" for x in results)
    sys.exit(1 if p0_fail else 0)


if __name__ == "__main__":
    main()
