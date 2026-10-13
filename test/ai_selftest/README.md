# AI 自测执行脚本 · 使用说明（比赛版）

## 0. 文件清单

| 文件 | 作用 |
|:---|:---|
| `routes.json` | **唯一需要改的文件**：应用地址、接口路径、页面路径、前端选择器 |
| `selftest.py` | 接口层 + 页面层：页面可达性、端到端主链路、服务端校验 |
| `selftest_ui.py` | 前端交互层（Playwright）：拖拽、滑块、筛选、弹窗、角标、主题 |
| `requirements.txt` | 依赖 |
| `run_all.py` | 一键跑两层并合并报告 |

## 1. 安装

```bash
pip install -r requirements.txt          # 必装（requests）
pip install playwright                   # 选装（前端交互层需要）
playwright install chromium
```

## 2. 适配你们的实际代码（重要）

打开 `routes.json`，把接口路径和选择器改成你们真实实现的，例如：

```json
"api": {
  "parse":         { "method": "POST", "path": "/plan/parse" },
  "task_complete": { "method": "POST", "path": "/tasks/{task_id}/complete" }
}
```

前端元素的 class/id 不同时改 `selectors`，例如：

```json
"task_row": ".task-row, tbody tr",
"modal_confirm": ".modal button:has-text('确认')"
```

> 选择器支持逗号分隔多候选，脚本会自动挑第一个命中的。

## 3. 一键执行

```bash
python run_all.py                        # 跑全部，生成 report.md
python run_all.py --base http://127.0.0.1:8080
python run_all.py --no-ui                # 只跑接口层（没装 Playwright 时用）
python run_all.py --phase smoke          # 只跑端到端冒烟 TC-041
```

单独跑：

```bash
python selftest.py --phase smoke         # 接口层冒烟
python selftest_ui.py --case TC-018      # 只跑某条前端用例
python selftest_ui.py --headed           # 有头模式，肉眼看效果
```

## 4. 结果解读

| 结果 | 含义 | 处理 |
|:---|:---|:---|
| PASS | 通过 | 无需处理 |
| FAIL | 功能已实现但结果不对 | **必须修**（P0 失败退出码为 1） |
| SKIP | 路由未实现 404 / 页面缺控件 | 说明还没开发到，不是 bug |

> 脚本刻意把 404 与"选择器没命中"判为 SKIP，避免开发过程中被误报淹没。

## 5. 覆盖的用例

### 接口层 `selftest.py`

| 检查 | 对应用例 |
|:---|:---|
| 页面可达 + 关键元素 | TC-001 / TC-011 / TC-016 / TC-026 / TC-033 / TC-035 / TC-037 |
| 端到端主链路 | TC-041（解析→确认→排期→完成→反馈→下游可访问） |
| 服务端校验 | TC-004 / TC-005 / TC-010 / TC-032 |

### 前端层 `selftest_ui.py`

| 检查 | 对应用例 |
|:---|:---|
| 2000 字计数 | TC-002 |
| 填充示例写入 | TC-003 |
| 拖拽时间条重算 | TC-013 |
| 进度 100 自动完成 | TC-017 |
| 筛选后 URL 保留 | TC-018 |
| 延期原因必填拦截 | TC-019 |
| 二次确认点取消 | TC-020 |
| 标记已读角标递减 | TC-027 |
| 全部已读 | TC-028 |
| 主题切换刷新保持 | TC-037 |

## 6. 让 AI 自动修

把 `report.md` 贴给 AI：

```
以下是自测报告，请只修 FAIL 项，不要重构代码：
1. 按编号逐条定位问题文件与原因。
2. 只做最小改动让用例通过。
3. 改完重跑 python run_all.py，直到 P0 全部 PASS。
4. 输出改动清单：文件路径 | 改动点 | 影响用例。
```

## 7. 比赛节奏建议

| 时间点 | 动作 |
|:---|:---|
| 开发中 | 每完成一个功能点，跑 `python run_all.py --phase smoke` 快速确认没跑偏 |
| 剩 40 分钟 | 跑一次 `python run_all.py`，看 SKIP 项决定哪些功能来不及做、降级为静态演示 |
| 剩 15 分钟 | 只跑 `python run_all.py --phase smoke`，只要 TC-041 过就稳 |
