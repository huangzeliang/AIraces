# 用户管理演示

一个基于 Flask 的本地用户管理页面，支持搜索、状态筛选，以及用户的新增、查看、编辑和删除。数据保存在 `data/users.json`，首次打开即可看到四条示例记录。

## 启动

需要 Python 3.10 或更新版本。在项目目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

浏览器打开 <http://127.0.0.1:5000>。

## 四人分工

所有人使用同一个启动命令 `python app.py`。`app.py` 负责启动、JSON 读写和表单校验，通常不需要改动。接口按文件分开：

| 文件 | 负责的接口 |
| --- | --- |
| `1.py` | 用户列表与搜索：`GET /` |
| `2.py` | 新增用户：`POST /users` |
| `3.py` | 编辑用户：`POST /users/<user_id>/edit` |
| `4.py` | 删除用户：`POST /users/<user_id>/delete` |

各自修改对应文件即可；如果需要改动共同的数据格式或校验规则，再协商修改 `app.py`。

这适合本地演示或学习。JSON 文件让初期配置更简单，但不适合多人同时写入，也不方便复杂查询。正式业务系统通常会改用数据库（小规模可先用 SQLite），并加入登录、权限控制等功能。Flask 自带服务器只用于本地开发。
