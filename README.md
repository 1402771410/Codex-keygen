# Codex-keygen

Codex-keygen 是一个基于 FastAPI + Web UI 的 OpenAI 账号注册/管理工具，聚焦以下能力：

- 临时邮箱池（多供应商）
- 并发注册任务
- 账号管理与批量操作
- 日志中心（运行日志 + 操作日志）
- 统一部署入口（`keygen` / `keygen.bat`）

> ⚠️ 免责声明：本项目仅用于学习与研究。请遵守相关平台服务条款与法律法规，使用后果自行承担。

---

## 功能总览

### 1) 临时邮箱池（Tempmail）

- 当前内置供应商：Tempmail.lol
- 支持规则化配置：base_url、前缀、优先域名、超时、重试、供应商参数
- 支持 `single`（单服务）与 `multi`（启用服务轮询）
- 内置固定规则不可编辑/删除，只可启用或禁用
- 测试采用真实 OTP 探测流程
- 仅“测试成功且确认收到 OTP”的规则被判定为**可用**并允许启用

### 2) 注册任务

- 单次注册
- 批量注册
- 循环注册
- 支持并发与间隔控制
- WebSocket 实时日志推送

### 3) 账号管理

- 账号列表与详情查看
- 批量刷新 Token / 批量验证 Token
- 批量上传（CPA / Sub2API / Team Manager）
- 批量导出（JSON / CSV / CPA / Sub2API）
- 列表筛选支持：
  - 状态
  - 邮箱服务
  - 关键词
  - 起止时间
  - 邮箱列表（多邮箱）

### 4) 日志中心

- 页面入口：`/logs`
- 运行日志：读取应用日志文件，支持级别/关键词过滤
- 操作日志：聚合注册任务关键事件与临时邮箱测试事件

### 5) 系统设置

- 代理（动态代理 + 代理列表）
- CPA/Sub2API/Team Manager 服务管理
- 注册参数、验证码参数、数据库管理
- SQLite / PostgreSQL

---

## 环境要求

- Python 3.10+
- 推荐使用 `uv`（也可使用 pip）

---

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/1402771410/Codex-keygen.git
cd Codex-keygen
```

### 2. 安装依赖

```bash
# 推荐
uv sync

# 需要运行测试时
uv sync --extra dev

# 需要支付自动化（Playwright）时
uv sync --extra payment

# 或（pip）
pip install -r requirements.txt

# 如需运行测试
pip install pytest httpx
```

### 3. 启动 Web UI

```bash
python webui.py
```

默认访问：`http://127.0.0.1:1455`

---

## 统一命令入口（keygen）

项目当前维护的命令族：

- `install`
- `upgrade`
- `package`
- `config`
- `recommend`
- `menu`

> 直接执行 `keygen`（不带子命令）会默认打开 `config` 配置面板。

### 平台执行方式

| 平台 | 命令入口 |
|---|---|
| Windows (PowerShell/CMD) | `.\keygen.bat <subcommand>` |
| Linux / macOS | `./keygen <subcommand>` |

> Linux/macOS 首次执行前请确保可执行权限：`chmod +x keygen`

### 常用命令

```powershell
# Windows
.\keygen.bat install
.\keygen.bat upgrade
.\keygen.bat config
.\keygen.bat package
```

```bash
# Linux/macOS
./keygen install
./keygen upgrade
./keygen config
./keygen package
```

---

## webui.py 启动参数

```bash
python webui.py --help
```

支持参数：

- `--host`
- `--port`
- `--debug`
- `--reload`
- `--log-level`
- `--access-username`
- `--access-password`

示例：

```bash
python webui.py --host 0.0.0.0 --port 1455 --access-username admin --access-password admin123
```

---

## 环境变量（可选）

可通过 `.env` 或运行环境注入：

| 变量 | 说明 |
|---|---|
| `APP_HOST` / `WEBUI_HOST` | 监听地址 |
| `APP_PORT` / `WEBUI_PORT` | 监听端口 |
| `APP_ACCESS_USERNAME` / `WEBUI_ACCESS_USERNAME` | 登录账号 |
| `APP_ACCESS_PASSWORD` / `WEBUI_ACCESS_PASSWORD` | 登录密码 |
| `APP_DATABASE_URL` | 数据库连接串 |
| `DEBUG` | 调试开关 |
| `LOG_LEVEL` | 日志级别 |

数据库使用 PostgreSQL 示例：

```bash
export APP_DATABASE_URL="postgresql://user:password@host:5432/dbname"
python webui.py
```

---

## Docker 部署

### docker compose（推荐）

```bash
docker compose --env-file .env.docker up -d --build
```

### docker run（示例）

```bash
docker run -d \
  -p 18080:1455 \
  -e WEBUI_HOST=0.0.0.0 \
  -e WEBUI_PORT=1455 \
  -e WEBUI_ACCESS_USERNAME=admin \
  -e WEBUI_ACCESS_PASSWORD=your_password \
  -v $(pwd)/data:/app/data \
  --name codex-keygen \
  ghcr.io/yunxilyf/codex-keygen:latest
```

---

## 开发与验证

```bash
# 语法检查
python -m compileall src tests scripts webui.py

# 测试
python -m pytest -q
```

Windows 也可用 `py -3 -m ...` 形式执行上述命令。

---

## 页面入口

- `/`：注册控制台
- `/accounts`：账号管理
- `/email-services`：临时邮箱池
- `/logs`：日志中心
- `/settings`：系统设置
- `/payment`：支付升级

---

## 项目结构

```text
Codex-keygen/
├── webui.py
├── keygen
├── keygen.bat
├── scripts/
│   ├── keygen.py
│   ├── deploy_manager.py
│   └── package_manager.py
├── src/
│   ├── config/
│   ├── core/
│   ├── database/
│   ├── services/
│   └── web/
│       ├── app.py
│       ├── task_manager.py
│       └── routes/
├── templates/
├── static/
└── data/
```

---

## License

[MIT](LICENSE)
