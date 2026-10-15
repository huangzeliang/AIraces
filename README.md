# 智能计划管理 Agent

一个基于 Flask 的本地计划管理应用：自然语言录入计划 → AI 提炼确认 → 智能排期 → 任务执行与提醒，数据保存在 `data/` 目录的 JSON 文件中。系统内置账号体系与两种角色（管理员 / 普通用户），登录后按角色进入不同的工作界面。

后端路由与依赖清单集中在 `backend/`；根目录的 `app.py` 是应用启动入口，本地 JSON 数据保留在 `data/` 中，页面模板和静态资源集中在 `frontend/templates/` 与 `frontend/static/`。

前端按页面拆分：`pages/` 中每个页面拥有同名的模板、样式和脚本；`layout.html`、`components/`、`css/tokens.css`、`css/layout.css` 与 `js/theme.js` 是公共文件，修改前需先协调。

页面开发前必须阅读 [UI 主题与多端适配规范](docs/UI_THEME_SPEC.md)。所有新增或修改页面默认同时适配手机、平板和 PC，按规范第 8–10 节实现布局、交互并完成验收；AI 开发入口要求见 [AGENTS.md](AGENTS.md)。涉及公共适配能力的修改按规范执行并回归受影响页面。

## 安装

需要 Python 3.10 或更新版本。在项目目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

## 启动

应用通过 `APP_ENV` 区分运行环境。启动后浏览器打开 <http://127.0.0.1:5000>。

### 本地开发（自动重载）

修改 Python 后端文件后 Flask 会自动重启；修改模板或静态文件后刷新浏览器即可看到效果。

```bash
APP_ENV=development python app.py
```

### 生产环境（不自动重载）

```bash
APP_ENV=production python app.py
```

Flask 自带服务器仅适用于本地开发；正式部署应使用生产级 WSGI 服务器。

## 登录与角色

未登录访问任何页面都会跳转登录页（`/login`），登录成功按角色进入对应界面。

| 角色 | 演示账号 | 初始密码 | 可见页面 | 数据范围 |
| --- | --- | --- | --- | --- |
| 管理员 | `ruoxi.lin@example.com` | `123456` | 全部：驾驶舱、任务列表、提醒中心、计划录入、提炼确认、智能排期、数据管理、用户管理、系统设置 | 全量数据 |
| 普通用户 | `yuhang.chen@example.com` | `123456` | 任务列表、提醒中心、计划录入、提炼确认、智能排期 | 仅自己创建的计划 / 任务 / 提醒 |

- 登录页提供演示账号「填入」按钮；密码以 pbkdf2 哈希存储，管理员可在「用户管理」中创建账号、分配角色、重置密码与启停用。
- 普通用户访问管理页会被重定向回自己的任务列表并给出提示；接口层同样校验归属，越权操作返回「不存在」。
- 顶栏右侧可退出登录；系统设置页不再包含「视图角色」模拟，角色由登录账号决定。
- 重置演示数据（含演示账号配套的任务归属）：`python backend/seed_demo.py`。

## 五人分工

`app.py` 负责启动、JSON 读写和表单校验，通常不需要改动。根据运行场景使用上方对应的启动命令；后端接口和前端页面按以下范围拆分：

| 成员 | 后端职责 | 前端页面职责 |
| --- | --- | --- |
| 黄泽良 | `backend/1.py`：用户列表与搜索 | 工作概览：`pages/dashboard.html`、`css/pages/dashboard.css`、`js/pages/dashboard.js` |
| 晨屹 | `backend/2.py`：新增用户 | 用户管理：`pages/users.html`、`css/pages/users.css`、`js/pages/users.js` |
| 华建 | `backend/3.py`：编辑用户 | 数据报表：`pages/analytics.html`、`css/pages/analytics.css`、`js/pages/analytics.js` |
| 英儒 | `backend/4.py`：删除用户 | 不负责前端开发 |
| 靖楠 | `test/`：测试与验收 | 测试四个页面及公共昼夜主题切换 |

系统设置页面的代码已放在 `frontend/templates/pages/settings.html`、`frontend/static/css/pages/settings.css` 和 `frontend/static/js/pages/settings.js`，作为公共示例页面，当前未分配给个人维护。

各自修改对应文件即可；如果需要改动共同的数据格式或校验规则，再协商修改 `app.py`。

这适合本地演示或学习。JSON 文件让初期配置更简单，但不适合多人同时写入，也不方便复杂查询。正式业务系统通常会改用数据库（小规模可先用 SQLite），并在现有账号体系的基础上扩展更细的权限控制。Flask 自带服务器只用于本地开发。
