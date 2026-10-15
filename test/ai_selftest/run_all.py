# -*- coding: utf-8 -*-
"""
一键执行：接口层 + 前端交互层，合并输出一份报告。

用法：
    python run_all.py
    python run_all.py --no-ui                # 跳过 Playwright 前端层
    python run_all.py --phase smoke          # 只跑端到端冒烟
    python run_all.py --base http://127.0.0.1:8080
    python run_all.py --report 最终报告.md

退出码：0 = 无 P0 失败；1 = 存在 P0 失败
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(cmd, label):
    print(f"\n{'=' * 96}\n{label}\n{'=' * 96}")
    p = subprocess.run([PY] + cmd, cwd=HERE)
    return p.returncode


def load_cfg(path):
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def merge_reports(files, out, base):
    rows = []
    for f in files:
        fp = os.path.join(HERE, f)
        if not os.path.exists(fp):
            continue
        with open(fp, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("|") and "---" not in line and "编号" not in line:
                    rows.append(line)
        try:
            os.remove(fp)
        except OSError:
            pass

    p = sum(1 for r in rows if "| PASS |" in r)
    f = sum(1 for r in rows if "| FAIL |" in r)
    s = sum(1 for r in rows if "| SKIP |" in r)

    lines = ["# AI 自测报告（合并）", "",
             f"- 目标地址：{base}",
             f"- 执行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 汇总：PASS {p} / FAIL {f} / SKIP {s} / 共 {len(rows)}", "",
             "| 编号 | 用例标题 | 优先级 | 类型 | 结果 | 详情 |",
             "|:---|:---|:---:|:---:|:---:|:---|"]
    lines += rows
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return p, f, s


def has_playwright() -> bool:
    return importlib.util.find_spec("playwright") is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None)
    ap.add_argument("--config", default=os.path.join(HERE, "routes.json"))
    ap.add_argument("--phase", default="all", choices=["all", "p0", "p1", "smoke", "pages"])
    ap.add_argument("--no-ui", action="store_true", help="跳过前端交互层")
    ap.add_argument("--report", default="report.md")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    base = args.base or cfg.get("base", "http://127.0.0.1:5000")
    base_arg = ["--base", base] if args.base else []

    tmp_files = []

    # 1. 接口层
    api_report = "_tmp_api.md"
    code_api = run(["selftest.py", "--config", args.config, "--phase", args.phase,
                    "--report", api_report] + base_arg, "接口层 + 页面层")
    tmp_files.append(api_report)

    # 2. 前端层
    code_ui = 0
    if not args.no_ui and args.phase in ("all", "p0"):
        if has_playwright():
            ui_report = "_tmp_ui.md"
            code_ui = run(["selftest_ui.py", "--config", args.config,
                           "--report", ui_report] + base_arg, "前端交互层（Playwright）")
            tmp_files.append(ui_report)
        else:
            print("\n[提示] 未安装 playwright，跳过前端交互层。"
                  "如需启用： pip install playwright && playwright install chromium")

    # 3. 合并
    out = os.path.join(HERE, args.report)
    p, f, s = merge_reports(tmp_files, out, base)

    print(f"\n{'=' * 96}")
    print(f"合并报告：PASS {p} / FAIL {f} / SKIP {s}")
    print(f"报告文件：{out}")
    print("=" * 96)

    sys.exit(1 if (code_api == 1 or code_ui == 1) else 0)


if __name__ == "__main__":
    main()
