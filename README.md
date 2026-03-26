# Codex-keygen

Codex-keygen 是一个 OpenAI 账号管理 Web UI 工具，支持临时邮箱池规则、并发批量注册、代理管理和账号管理。

# 本次更改依据工程修改而来，地址：https://github.com/cnlimiter/codex-manager  

> ⚠️ **免责声明**：本工具仅供学习和研究使用，使用本工具产生的一切后果由使用者自行承担。请遵守相关服务的使用条款，不要用于任何违法或不当用途。 如有侵权，请及时联系，会及时删除。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

## 功能特性

- **临时邮箱池支持（多供应商）**
  - 全局临时邮箱固定项（不可编辑/删除，仅可启用与否）
  - 预置免费供应商：Tempmail.lol / Mail.tm / Mail.gw / 1secmail / GuerrillaMail
  - 临时邮箱规则管理（供应商调用方式、前缀规则、优先域名、独立 API、超时、重试）
  - 邮箱选择模式：`single`（固定单服务）或 `multi`（启用服务轮询）

- **注册模式**
  - 单次注册
  - 批量注册（可配置数量和间隔时间）
  - 循环注册（按时间窗口持续执行）

- **并发控制**
  - 流水线模式（Pipeline）：每隔 interval 秒启动新任务，限制最大并发数
  - 并行模式（Parallel）：所有任务同时提交，Semaphore 控制最大并发
  - 并发数可在 UI 自定义（1-50）
  - 日志混合显示，带 `[任务N]` 前缀区分

- **实时监控**
  - WebSocket 实时日志推送
  - 跨页面导航后自动重连
  - 降级轮询备用方案

- **代理管理**
  - 动态代理（通过 API 每次获取新 IP）
  - 代理列表（随机选取，支持设置默认代理，记录使用时间）

- **账号管理**
  - 查看、删除、批量操作
  - Token 刷新与验证
  - 订阅状态管理（手动标记 / 自动检测 plus/team/free）
  - 导出格式：JSON / CSV / CPA 格式 / Sub2API 格式
    - 单个账号导出为独立 `.json` 文件
    - 多个 CPA 账号打包为 `.zip`，每个账号一个独立文件
    - Sub2API 格式所有账号合并为单个 JSON
  - 上传目标（直连不走代理）：
    - **CPA**：支持多服务配置，上传时选择目标服务，可按服务开关将账号实际代理写入 auth file 的 `proxy_url`
    - **Sub2API**：支持多服务配置，标准 sub2api-data 格式
    - **Team Manager**：支持多服务配置

- **支付升级**
  - 为账号生成 ChatGPT Plus 或 Team 订阅支付链接
  - 后端命令行以无痕模式自动打开 Chrome/Edge
  - Team 套餐支持自定义工作区名称、座位数、计费周期

- **系统设置**
  - 代理配置（动态代理 + 代理列表，支持设默认）
  - CPA 服务列表管理（多服务，连接测试）
  - Sub2API 服务列表管理（多服务，连接测试）
  - Team Manager 服务列表管理（多服务，连接测试）
  - 临时邮箱池管理入口
  - 注册参数（超时、重试、密码长度等）
  - 验证码等待配置
  - 数据库管理（备份、清理）
  - 支持远程 PostgreSQL

## 快速开始

### 环境要求

- Python 3.10+
- [uv](https://github.com/astral-sh/uv)（推荐）或 pip

### 安装依赖

```bash
# 使用 uv（推荐）
uv sync

# 或使用 pip
pip install -r requirements.txt
```

### 一步到位命令（拉取 + 进入目录 + 安装）

> 如果你是首次部署，直接执行下面一条命令即可完成「拉取仓库 + 进入目录 + 启动安装流程」。

> 仓库地址：`https://github.com/1402771410/Codex-keygen.git`


#### Windows（PowerShell / CMD）

```powershell
if (Test-Path .\Codex-keygen\.git) { Set-Location .\Codex-keygen; git pull --ff-only } else { git clone https://github.com/1402771410/Codex-keygen.git Codex-keygen; Set-Location .\Codex-keygen }; .\keygen.bat install
```

#### Linux（Bash）

```bash
if [ -d /root/Codex-keygen/.git ]; then cd /root/Codex-keygen && git pull --ff-only; else cd /root && git clone https://github.com/1402771410/Codex-keygen.git Codex-keygen && cd /root/Codex-keygen; fi && chmod +x keygen && ./keygen install
```

> Linux 服务器默认使用 `/root/Codex-keygen` 作为部署目录。

> 如果 `git clone` 提示 `Username for 'https://github.com':`，说明当前走的是 HTTPS 鉴权：
> - Username 输入你的 GitHub 用户名（如 `1402771410`）
> - Password 位置输入 GitHub PAT（不是网页登录密码）

### 一步到位升级命令（拉取更新 + 升级）

#### Windows（PowerShell / CMD）

```powershell
cd Codex-keygen && git pull --ff-only && .\keygen.bat upgrade
```

#### Linux（Bash）

```bash
cd /root/Codex-keygen && git pull --ff-only && ./keygen upgrade
```

### 统一命令入口：`keygen`

> 目标：安装、升级、打包、配置都走同一命令族（`keygen`）。

#### 各终端命令入口

| 平台 | 命令入口 | 说明 |
|------|----------|------|
| Windows (CMD/PowerShell) | `.\keygen.bat` | 推荐直接在项目根目录执行 |
| macOS/Linux | `./keygen` | 首次执行前：`chmod +x keygen` |
| 已安装 Python 包 | `keygen` | 通过 `pyproject` 的脚本入口调用 |

### 一键安装（同一条命令）

> 部署参数统一维护在根目录 `runtime-config.json`（端口、登录账号、登录密码等实时可改）。

```bash
keygen install
```

按不同系统实际执行示例：

```bash
# Windows
.\keygen.bat install

# macOS/Linux
./keygen install
```

部署脚本能力：
- 自动识别系统并智能推荐部署模式（Docker / 本地）。
- Linux 支持手动选择 `Docker` 或 `本地`。
- Linux 选择 Docker 且当前无 Docker 环境时，会询问是否自动安装：
  - 同意：自动安装 Docker + Compose 并继续部署
  - 不同意：自动回退到本地部署
- 本地安装会自动检查依赖环境：
  - 若检测到项目 `.venv`，安装、预检、后续 keygen 命令会优先复用该解释器
  - `uv` 可用时优先 `uv sync`
  - 若 `uv` 不可用或失败，自动回退 `pip` 安装
  - 若缺失 `pip`，自动尝试 `ensurepip` 安装后继续依赖安装
- 安装前会检查代码是否落后远程分支：
  - 交互模式会提示是否先执行 `git pull --ff-only`
  - 非交互模式会给出明确告警，避免旧代码构建失败
- 部署流程中会交互采集：监听地址、端口、登录账号、登录密码、debug、日志级别。
- 登录密码与登录账号一致，使用明文可见输入。
- 安装完成后自动执行健康检查：
  - Docker 模式：检查 `http://127.0.0.1:<port>/login`
  - Docker 健康检查会显示加载进度（等待秒数与轮询状态）
  - 若检测到应用可能仍监听默认 1455，会额外探测 `1455` 作为诊断依据（不作为通过条件）
  - 本地模式：检查 `webui` 与 FastAPI 应用可导入
- 安装失败时自动回滚 `.env` / `.env.docker`；Docker 失败会打印 `ps -a`、容器状态/端口、最近日志，本地失败会输出错误信息。

### 一键升级（同一条命令）

```bash
keygen upgrade
```

按不同系统实际执行示例：

```bash
# Windows
.\keygen.bat upgrade

# macOS/Linux
./keygen upgrade
```

升级流程：
- 自动尝试 `git pull --ff-only`
- Docker 模式执行镜像更新并重建容器
- 本地模式执行依赖更新（`uv sync` 或 `pip install -r requirements.txt`）

### 配置面板（同一条命令）

```bash
keygen config
```

按不同系统实际执行示例：

```bash
# Windows
.\keygen.bat config

# macOS/Linux
./keygen config
```

可在菜单中修改：端口、登录账号、登录密码、debug、日志级别，并可一键保存/安装。

> 直接输入 `keygen`（不带子命令）默认打开配置面板。

### 一键打包（同一条命令）

```bash
keygen package
```

按不同系统实际执行示例：

```bash
# Windows
.\keygen.bat package

# macOS/Linux
./keygen package
```

打包行为：
- 自动识别当前系统并打包对应产物（Windows -> win 包；macOS -> mac 包）
- 如需强制目标可使用：`keygen package --target windows|macos`
- 打包前会自动检查并补齐环境依赖：
  - 核心依赖探测失败时自动安装（`requirements.txt` / `-e .`）
  - `PyInstaller` 缺失时自动安装
  - `pip` 缺失时自动尝试 `ensurepip`

输出到 `dist/releases/<target>/`：
- 可执行文件（可直接运行）
- `runtime-config.json`（统一实时修改端口/账号/密码）
- 启动脚本（`start.bat` 或 `start.command`）

> 说明：Windows 包需在 Windows 打包，macOS 包需在 macOS 打包（命令会自动校验）。

### 关于“实时生效”

- `runtime-config.json` 中的 **登录账号/密码** 在进程运行期间修改后会自动同步，后续登录校验可即时生效。
- `host` / `port` 修改会被立即写入配置，但属于监听层参数，**需要重启进程后端口绑定才会真正切换**。
- `debug` / `log_level` 会同步写入配置，建议重启进程后按新配置完全生效。

### 环境变量配置（可选）

复制 `.env.example` 为 `.env`，按需填写：

```bash
cp .env.example .env
```

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `APP_HOST` | 监听主机 | `0.0.0.0` |
| `APP_PORT` | 监听端口 | `1455` |
| `APP_ACCESS_USERNAME` | Web UI 访问账号 | `admin` |
| `APP_ACCESS_PASSWORD` | Web UI 访问密钥 | `admin123` |
| `APP_DATABASE_URL` | 数据库连接字符串 | `data/database.db` |

> 启动阶段优先级：已存在环境变量（如 Docker 注入） > `runtime-config.json` > `.env` > 数据库设置 > 默认值。
> 命令行参数会先写入数据库；若同名环境变量已存在，启动时仍以环境变量为准。

### 启动 Web UI

```bash
# 默认启动（若存在 runtime-config.json，默认访问 127.0.0.1:1455）
python webui.py

# 指定地址和端口
python webui.py --host 0.0.0.0 --port 8080

# 调试模式（热重载）
python webui.py --debug

# 设置 Web UI 访问密钥
python webui.py --access-password mypassword

# 组合参数
python webui.py --host 0.0.0.0 --port 8080 --access-password mypassword
```

> `--access-password` 优先级高于数据库中保存的密钥设置，每次启动时生效。

### Docker 部署

项目支持通过 Docker 进行容器化部署。Docker 镜像已托管至 GitHub Container Registry (GHCR)。

#### 使用 docker-compose (推荐)

在项目根目录下启动（推荐使用 `keygen install` 生成的 `.env.docker`）：

```bash
docker compose --env-file .env.docker up -d --build
```

如需自定义端口或访问账号/密码，可在启动前通过环境变量覆盖默认值（例如 `HOST_PORT`、`WEBUI_PORT`、`WEBUI_ACCESS_USERNAME`、`WEBUI_ACCESS_PASSWORD`）。

关键变量：

- `HOST_PORT`：宿主机端口
- `WEBUI_PORT`：容器内监听端口
- `WEBUI_ACCESS_USERNAME`：Web UI 访问账号
- `WEBUI_ACCESS_PASSWORD`：Web UI 访问密码

例如 `HOST_PORT=18080`、`WEBUI_PORT=1455` 时，访问地址为 `http://127.0.0.1:18080`。

#### 直接使用 docker run

如果你不想使用 docker-compose，也可以直接拉取并运行镜像：

```bash
docker run -d \
  -p 18080:1455 \
  -e WEBUI_HOST=0.0.0.0 \
  -e WEBUI_PORT=1455 \
  -e WEBUI_ACCESS_PASSWORD=your_secure_password \
  -v $(pwd)/data:/app/data \
  --name codex-keygen \
  ghcr.io/yunxilyf/codex-keygen:latest
```

环境变量说明：
- `WEBUI_HOST`: 监听的主机地址 (默认 `0.0.0.0`)
- `WEBUI_PORT`: 监听的端口 (默认 `1455`)
- `WEBUI_ACCESS_USERNAME`: 设置 Web UI 的访问账号
- `WEBUI_ACCESS_PASSWORD`: 设置 Web UI 的访问密码
- `DEBUG`: 设为 `1` 或 `true` 开启调试模式
- `LOG_LEVEL`: 日志级别，如 `info`, `debug`

> `-p 18080:1455` 中前者是宿主机端口，后者是容器端口（应与 `WEBUI_PORT` 一致）。

> **注意**：`-v $(pwd)/data:/app/data` 挂载参数非常重要，它确保了你的数据库文件和账户信息在容器重启或更新后不会丢失。

### 使用远程 PostgreSQL

通过环境变量指定数据库连接字符串：

```bash
export APP_DATABASE_URL="postgresql://user:password@host:5432/dbname"
python webui.py
```

也支持 `DATABASE_URL`，优先级低于 `APP_DATABASE_URL`。

启动后访问 http://127.0.0.1:1455

## 项目结构

```
Codex-keygen/
├── webui.py            # Web UI 入口
├── keygen / keygen.bat # 统一命令入口
├── runtime-config.json # 统一运行参数（端口/账号/密码）
├── scripts/            # 部署/打包管理脚本
│   ├── keygen.py
│   ├── deploy_manager.py
│   └── package_manager.py
├── src/
│   ├── config/         # 配置管理（Pydantic Settings）
│   ├── core/
│   │   ├── openai/     # OAuth、Token 刷新、支付核心
│   │   └── upload/     # CPA / Sub2API / Team Manager 上传模块
│   ├── database/       # 数据库（SQLAlchemy + SQLite/PostgreSQL）
│   ├── services/       # 邮箱服务实现
│   └── web/
│       ├── app.py      # 应用入口、路由挂载
│       ├── task_manager.py  # 任务/日志/WebSocket 管理
│       └── routes/     # API 路由
│           └── upload/ # CPA / Sub2API / TM 服务管理路由
├── templates/          # Jinja2 HTML 模板
├── static/             # 静态资源（CSS / JS）
└── data/               # 运行时数据目录（数据库、日志）
```

## 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| 数据库 | SQLAlchemy + SQLite / PostgreSQL |
| 模板引擎 | Jinja2 |
| HTTP 客户端 | curl_cffi（浏览器指纹模拟） |
| 实时通信 | WebSocket |
| 并发 | asyncio Semaphore + ThreadPoolExecutor |
| 前端 | 原生 JavaScript（无框架） |

## API 端点

### 注册任务

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/registration/start` | 启动注册任务 |
| GET | `/api/registration/tasks` | 任务列表 |
| GET | `/api/registration/tasks/{uuid}/logs` | 任务日志 |
| POST | `/api/registration/tasks/{uuid}/cancel` | 取消任务 |
| GET | `/api/registration/available-services` | 可用邮箱服务 |

### 账号管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/accounts` | 账号列表（支持分页、筛选、搜索） |
| GET | `/api/accounts/{id}` | 账号详情 |
| PATCH | `/api/accounts/{id}` | 更新账号（状态/cookies） |
| DELETE | `/api/accounts/{id}` | 删除账号 |
| POST | `/api/accounts/batch-delete` | 批量删除 |
| POST | `/api/accounts/export/json` | 导出 JSON |
| POST | `/api/accounts/export/csv` | 导出 CSV |
| POST | `/api/accounts/export/cpa` | 导出 CPA 格式（单文件或 ZIP） |
| POST | `/api/accounts/export/sub2api` | 导出 Sub2API 格式 |
| POST | `/api/accounts/{id}/refresh` | 刷新 Token |
| POST | `/api/accounts/batch-refresh` | 批量刷新 Token |
| POST | `/api/accounts/{id}/validate` | 验证 Token |
| POST | `/api/accounts/batch-validate` | 批量验证 Token |
| POST | `/api/accounts/{id}/upload-cpa` | 上传单账号到 CPA |
| POST | `/api/accounts/batch-upload-cpa` | 批量上传到 CPA |
| POST | `/api/accounts/{id}/upload-sub2api` | 上传单账号到 Sub2API |
| POST | `/api/accounts/batch-upload-sub2api` | 批量上传到 Sub2API |

### 支付升级

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/payment/generate` | 生成 Plus/Team 支付链接 |
| POST | `/api/payment/open` | 后端无痕模式打开浏览器 |
| POST | `/api/payment/accounts/{id}/mark-subscription` | 手动标记订阅类型 |
| POST | `/api/payment/accounts/batch-check-subscription` | 批量检测订阅状态 |
| POST | `/api/payment/accounts/{id}/upload-tm` | 上传单账号到 Team Manager |
| POST | `/api/payment/accounts/batch-upload-tm` | 批量上传到 Team Manager |

### 邮箱服务

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/email-services` | 服务列表 |
| POST | `/api/email-services` | 添加服务 |
| PATCH | `/api/email-services/{id}` | 更新服务 |
| DELETE | `/api/email-services/{id}` | 删除服务 |
| POST | `/api/email-services/{id}/test` | 测试服务 |
| POST | `/api/email-services/test-tempmail` | 测试全局临时邮箱配置 |

### 上传服务管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/cpa-services` | CPA 服务列表/创建 |
| PUT/DELETE | `/api/cpa-services/{id}` | 更新/删除 CPA 服务 |
| POST | `/api/cpa-services/{id}/test` | 测试 CPA 连接 |
| GET/POST | `/api/sub2api-services` | Sub2API 服务列表/创建 |
| PUT/DELETE | `/api/sub2api-services/{id}` | 更新/删除 Sub2API 服务 |
| POST | `/api/sub2api-services/{id}/test` | 测试 Sub2API 连接 |
| GET/POST | `/api/tm-services` | Team Manager 服务列表/创建 |
| PUT/DELETE | `/api/tm-services/{id}` | 更新/删除 TM 服务 |
| POST | `/api/tm-services/{id}/test` | 测试 TM 连接 |

### 设置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/settings` | 获取所有设置 |
| POST | `/api/settings/proxy/dynamic` | 更新动态代理设置 |
| GET/POST/DELETE | `/api/settings/proxies` | 代理列表管理 |
| POST | `/api/settings/proxies/{id}/set-default` | 设为默认代理 |
| GET | `/api/settings/database` | 数据库信息 |

### WebSocket

| 路径 | 说明 |
|------|------|
| `ws://host/api/ws/logs/{uuid}` | 实时日志流 |

## Docker 常用命令（补充）

> 推荐优先使用 `keygen install`。以下命令用于手动排障或手动运维。

```bash
# 按 keygen install 生成的 .env.docker 启动
docker compose --env-file .env.docker up -d --build

# 查看日志
docker compose logs -f

# 停止服务
docker compose down

# 重新构建
docker compose build --no-cache
```

## 注意事项

- 首次运行会自动创建 `data/` 目录和 SQLite 数据库
- 所有账号和设置数据存储在 `data/database.db`
- 日志文件写入 `logs/` 目录
- 代理优先级：动态代理 > 代理列表（随机/默认） > 直连
- CPA / Sub2API / Team Manager 上传始终直连，不走代理；其中 CPA 可选把账号记录的代理写入 auth file 的 `proxy_url`
- 注册时自动随机生成用户名和生日（年龄范围 18-45 岁）
- 支付链接生成使用账号 access_token 鉴权，走全局代理配置
- 无痕浏览器优先使用 playwright（注入 cookie 直达支付页）；未安装时降级为系统 Chrome/Edge 无痕模式
- 安装完整支付功能：`pip install ".[payment]" && playwright install chromium`（可选）
- 订阅状态自动检测调用 `chatgpt.com/backend-api/me`，走全局代理
- 批量注册并发数上限为 50，线程池大小已相应调整

## License

[MIT](LICENSE)
