# Codex-keygen

Codex-keygen 是一个基于 FastAPI + Web UI 的 OpenAI 账号注册/管理工具，聚焦以下能力：

- 临时邮箱池（多供应商）
- 临时邮箱规则化管理
- 并发注册任务
- 账号管理与批量操作
- 日志中心（运行日志 + 操作日志）
- 统一部署入口（`keygen` / `keygen.bat`）

> ⚠️ 免责声明：本项目仅用于学习与研究。请遵守相关平台服务条款与法律法规，使用后果自行承担。

---

## 功能总览

### 1) 临时邮箱池（Tempmail）

- 当前可用供应商：Tempmail.lol
- 支持规则化配置：base_url、前缀、优先域名、超时、重试、供应商参数
- 支持 `single`（单服务）与 `multi`（启用服务轮询）
- 内置固定规则不可编辑/删除，只可启用或禁用
- 测试采用真实 OTP 探测流程
- 仅“测试成功且确认收到 OTP”的规则被判定为**可用**并允许启用
- 全局临时邮箱创建限流：5 分钟内最多 25 个，超限会自动冷却等待

### 2) 临时邮箱规则

- 当前支持在“邮箱服务”页面选择并配置：`Tempmail.lol`
- 注册页仅允许使用临时邮箱规则；POP 注册方式已下线
- 支持规则化配置：前缀、优先域名、超时、重试与供应商参数

### 3) 注册任务

- 单次注册
- 批量注册
- 循环注册
- 支持并发与间隔控制
- WebSocket 实时日志推送

### 4) 账号管理

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

### 5) 日志中心

- 页面入口：`/logs`
- 运行日志：读取应用日志文件，支持级别/关键词过滤
- 操作日志：聚合注册任务关键事件与临时邮箱测试事件

### 6) 系统设置

- 代理（动态代理 + 代理列表）
- CPA/Sub2API/Team Manager 服务管理
- 注册参数、验证码参数、数据库管理
- SQLite / PostgreSQL

---

## 环境要求

- Python 3.10+
- Git（推荐，用于升级时自动拉取）

---

## 快速开始

### 1. 安装/升级

Linux / macOS / Docker 终端：

```bash
curl -L https://raw.githubusercontent.com/1402771410/Codex-keygen/main/scripts/install_auto.sh | bash
```

Windows CMD / PowerShell：

```powershell
chcp 65001>nul && powershell -NoProfile -ExecutionPolicy Bypass -Command "$tmp=Join-Path $env:TEMP 'install_auto.ps1'; curl.exe -L 'https://raw.githubusercontent.com/1402771410/Codex-keygen/main/scripts/install_auto.ps1' -o $tmp; powershell -NoProfile -ExecutionPolicy Bypass -File $tmp"
```

说明：

- 首次执行：完成安装与部署。
- 再次执行同一条命令：自动完成系统升级。
- 安装流程会提示填写监听地址、端口、登录用户名、登录密码（Win/Linux/macOS/Docker 均一致）。
- 若缺少依赖（如 git/python/pip/docker），会先征求同意后自动安装。
- Linux 环境下会给出编号选项让你选择：`1) 本地部署` / `2) Docker 部署`，脚本不会替你自动决定。
- 安装完成后可直接输入 `keygen` 打开管理面板（无需进入项目目录）。
- Linux/macOS 安装后会优先尝试创建 `/usr/local/bin/keygen` 全局命令，减少 PATH 未生效导致的 `command not found`。

### 2. 卸载

Linux / macOS / Docker：

```bash
curl -L https://raw.githubusercontent.com/1402771410/Codex-keygen/main/scripts/install_auto.sh | bash -s -- uninstall
```

Windows CMD / PowerShell：

```powershell
chcp 65001>nul && powershell -NoProfile -ExecutionPolicy Bypass -Command "$tmp=Join-Path $env:TEMP 'install_auto.ps1'; curl.exe -L 'https://raw.githubusercontent.com/1402771410/Codex-keygen/main/scripts/install_auto.ps1' -o $tmp; powershell -NoProfile -ExecutionPolicy Bypass -File $tmp -Action uninstall"
```

深度清理（删除 `.venv` / `.env` / `.env.docker`；Docker 模式清理卷）：

```bash
curl -L https://raw.githubusercontent.com/1402771410/Codex-keygen/main/scripts/install_auto.sh | bash -s -- uninstall --purge
```

```powershell
chcp 65001>nul && powershell -NoProfile -ExecutionPolicy Bypass -Command "$tmp=Join-Path $env:TEMP 'install_auto.ps1'; curl.exe -L 'https://raw.githubusercontent.com/1402771410/Codex-keygen/main/scripts/install_auto.ps1' -o $tmp; powershell -NoProfile -ExecutionPolicy Bypass -File $tmp -Action uninstall -Purge"
```

### 3. 本地模式

下载项目到本地-解压-双击webui.py文件或在对应文件夹执行下面命令（需要安装好py环境）
```bash
python webui.py
```


> 若安装时选择 Docker 模式，服务会由 Docker 启动，无需再手动执行 `python webui.py`。`

---

## 统一命令入口（keygen）

项目当前维护的命令族：

- `install`（推荐：安装与升级共用）
- `upgrade`（兼容别名，效果等价于 `install`）
- `uninstall`
- `start`
- `stop`
- `restart`
- `status`
- `autostart-on`
- `autostart-off`
- `info`
- `package`
- `config`
- `recommend`
- `menu`

### 统一命令

> 直接执行 `keygen`（不带子命令）默认打开管理面板。

### 一键打包（Windows CMD / macOS 终端）

在项目目录执行：

```bash
keygen package --target interactive
```

Windows CMD 可直接使用打包脚本：

```bat
package.bat
```

打包时会交互选择平台（`windows` / `macos`）。

指定发布目录（会自动创建 `windows` 或 `macos` 子目录）：

```bat
package.bat D:\build-output
```

或：

```bat
keygen.bat package --target interactive --output-dir "D:\build-output"
```

> 注意：Windows 包必须在 Windows 构建，macOS 包必须在 macOS 构建。

### 管理面板能力

- 安装/更新/卸载
- 查看配置
- 修改监听地址、端口、用户名、密码
- 启动/停止/重启/查看服务状态
- 设置开机自启/关闭开机自启
- 文件目录信息展示

> 在 Linux 上，安装/更新时会先让你选择本地或 Docker；升级仍复用同一条安装命令。

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
├── package.bat
├── scripts/
│   ├── install_auto.sh
│   ├── install_auto.ps1
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
