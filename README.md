# 用户管理演示

一个基于 Flask 的本地用户管理页面，支持搜索、状态筛选，以及用户的新增、查看、编辑和删除。数据保存在 `data/users.json`，首次打开即可看到四条示例记录。

后端路由与依赖清单集中在 `backend/`；根目录的 `app.py` 是应用启动入口，本地 JSON 数据保留在 `data/` 中，`templates/` 和 `static/` 则作为页面模板和静态资源。

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

`app.py` 负责启动、JSON 读写和表单校验，通常不需要改动。根据运行场景使用上方对应的启动命令；接口按文件分开：

| 成员 | 文件 |
| --- | --- |
| 黄泽良 | `backend/1.py` |
| 晨屹 | `backend/2.py` |
| 华建 | `backend/3.py` |
| 英儒 | `backend/4.py` |
| 靖楠 | `test/` |

各自修改对应文件即可；如果需要改动共同的数据格式或校验规则，再协商修改 `app.py`。

这适合本地演示或学习。JSON 文件让初期配置更简单，但不适合多人同时写入，也不方便复杂查询。正式业务系统通常会改用数据库（小规模可先用 SQLite），并加入登录、权限控制等功能。Flask 自带服务器只用于本地开发。
