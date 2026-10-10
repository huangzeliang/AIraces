# 用户管理演示

一个基于 Flask 的本地用户管理页面，支持搜索、状态筛选，以及用户的新增、查看、编辑和删除。数据保存在 `data/users.json`，首次打开即可看到四条示例记录。

后端路由与依赖清单集中在 `backend/`；根目录的 `app.py` 是应用启动入口，本地 JSON 数据保留在 `data/` 中，页面模板和静态资源集中在 `frontend/templates/` 与 `frontend/static/`。

前端按页面拆分：`pages/` 中每个页面拥有同名的模板、样式和脚本；`layout.html`、`components/`、`css/tokens.css`、`css/layout.css` 与 `js/theme.js` 是公共文件，修改前需先协调。

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

这适合本地演示或学习。JSON 文件让初期配置更简单，但不适合多人同时写入，也不方便复杂查询。正式业务系统通常会改用数据库（小规模可先用 SQLite），并加入登录、权限控制等功能。Flask 自带服务器只用于本地开发。
