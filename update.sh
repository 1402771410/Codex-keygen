#!/usr/bin/env bash

set -euo pipefail

# 一键更新脚本：拉取最新代码并重建 Docker 服务

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

REMOTE_NAME="${REMOTE_NAME:-origin}"
BRANCH_NAME="${BRANCH_NAME:-main}"
ENV_FILE="${ENV_FILE:-.env.docker}"
SERVICE_NAME="${SERVICE_NAME:-webui}"
TARGET_REPO_URL="${REPO_URL:-https://github.com/1402771410/Codex-Manager-zh.git}"
AUTO_STASH="${AUTO_STASH:-1}"
STASH_CREATED=0
STASH_LABEL=""
ENV_BACKUP_FILE=""

cleanup_on_exit() {
    if [[ -n "$ENV_BACKUP_FILE" && -f "$ENV_BACKUP_FILE" ]]; then
        cp "$ENV_BACKUP_FILE" "$ENV_FILE" 2>/dev/null || true
        rm -f "$ENV_BACKUP_FILE" 2>/dev/null || true
        ENV_BACKUP_FILE=""
    fi
}

trap cleanup_on_exit EXIT

trim_value() {
    local value="$1"
    value="${value#${value%%[![:space:]]*}}"
    value="${value%${value##*[![:space:]]}}"
    printf '%s' "$value"
}

read_env_value() {
    local key="$1"
    local default_value="$2"
    local line=""
    local value=""

    if [[ -f "$ENV_FILE" ]]; then
        line="$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -n1 || true)"
        if [[ -n "$line" ]]; then
            value="${line#*=}"
            value="$(trim_value "$value")"
            value="${value%\"}"
            value="${value#\"}"
            value="${value%\'}"
            value="${value#\'}"
        fi
    fi

    if [[ -z "$value" ]]; then
        value="$default_value"
    fi

    printf '%s' "$value"
}

upsert_env_value() {
    local key="$1"
    local new_value="$2"
    local tmp_file

    tmp_file="$(mktemp)"

    if grep -qE "^[[:space:]]*${key}=" "$ENV_FILE"; then
        sed -E "s|^[[:space:]]*${key}=.*$|${key}=${new_value}|" "$ENV_FILE" > "$tmp_file"
        mv "$tmp_file" "$ENV_FILE"
    else
        cat "$ENV_FILE" > "$tmp_file"
        printf '\n%s=%s\n' "$key" "$new_value" >> "$tmp_file"
        mv "$tmp_file" "$ENV_FILE"
    fi
}

is_valid_port() {
    local port="$1"
    [[ "$port" =~ ^[0-9]+$ ]] || return 1
    ((port >= 1 && port <= 65535))
}

detect_primary_ip() {
    local ip=""
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    if [[ -z "$ip" ]]; then
        ip="127.0.0.1"
    fi
    printf '%s' "$ip"
}

is_port_in_use() {
    local port="$1"

    if command -v ss >/dev/null 2>&1; then
        ss -ltnH 2>/dev/null | grep -qE "[.:]${port}[[:space:]]" && return 0
        return 1
    fi

    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && return 0
        return 1
    fi

    if command -v netstat >/dev/null 2>&1; then
        netstat -ltn 2>/dev/null | grep -qE "[.:]${port}[[:space:]]" && return 0
        return 1
    fi

    return 1
}

log() {
    printf '[update] %s\n' "$*"
}

fail() {
    printf '[update] ERROR: %s\n' "$*" >&2
    exit 1
}

if ! command -v git >/dev/null 2>&1; then
    fail "未检测到 git，请先安装 git。"
fi

if ! command -v docker >/dev/null 2>&1; then
    fail "未检测到 docker，请先安装 Docker。"
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
else
    fail "未检测到 docker compose（docker compose 或 docker-compose）。"
fi

if [[ ! -d .git ]]; then
    fail "当前目录不是 Git 仓库：$ROOT_DIR"
fi

if git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    current_remote_url="$(git remote get-url "$REMOTE_NAME")"
    if [[ "$current_remote_url" != "$TARGET_REPO_URL" ]]; then
        log "更新远程仓库地址：$REMOTE_NAME -> $TARGET_REPO_URL"
        git remote set-url "$REMOTE_NAME" "$TARGET_REPO_URL"
    fi
else
    log "远程仓库不存在，正在创建：$REMOTE_NAME -> $TARGET_REPO_URL"
    git remote add "$REMOTE_NAME" "$TARGET_REPO_URL"
fi

if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f .env.docker.example ]]; then
        cp .env.docker.example "$ENV_FILE"
        fail "未找到 $ENV_FILE，已从模板生成。请先修改配置后重新执行。"
    fi
    fail "未找到 $ENV_FILE，且不存在 .env.docker.example 模板。"
fi

current_host_port="$(read_env_value "HOST_PORT" "1455")"
current_webui_port="$(read_env_value "WEBUI_PORT" "1455")"
current_access_password="$(read_env_value "WEBUI_ACCESS_PASSWORD" "admin123")"
legacy_access_password="$(read_env_value "APP_ACCESS_PASSWORD" "")"

if [[ "$current_access_password" == "admin123" && -n "$legacy_access_password" ]]; then
    current_access_password="$legacy_access_password"
fi

log "当前配置端口：HOST_PORT=$current_host_port（容器 WEBUI_PORT=$current_webui_port）"
if [[ -t 0 ]]; then
    if is_port_in_use "$current_host_port"; then
        log "警告：当前主机端口 $current_host_port 已被占用，如保持不变可能启动失败。"
    fi

    while true; do
        read -r -p "[update] 输入新的主机端口（1-65535，直接回车保持 $current_host_port）：" input_host_port
        if [[ -z "$input_host_port" ]]; then
            log "保持端口不变：HOST_PORT=$current_host_port"
            break
        fi

        if ! is_valid_port "$input_host_port"; then
            log "端口无效：$input_host_port（需在 1-65535），请重新输入。"
            continue
        fi

        if is_port_in_use "$input_host_port"; then
            log "端口 $input_host_port 已被占用，请重新输入其他端口。"
            continue
        fi

        upsert_env_value "HOST_PORT" "$input_host_port"
        current_host_port="$input_host_port"
        log "已更新主机端口：HOST_PORT=$current_host_port"
        break
    done
else
    log "检测到非交互模式，保持端口不变：HOST_PORT=$current_host_port"
fi

ENV_BACKUP_FILE="$(mktemp)"
cp "$ENV_FILE" "$ENV_BACKUP_FILE"

log "获取远程最新代码..."
git fetch "$REMOTE_NAME" "$BRANCH_NAME"

if [[ -n "$(git status --porcelain)" ]]; then
    if [[ "$AUTO_STASH" == "1" ]]; then
        STASH_LABEL="update-auto-stash-$(date +%Y%m%d-%H%M%S)"
        log "检测到本地改动，自动暂存：$STASH_LABEL"
        git stash push -u -m "$STASH_LABEL" >/dev/null
        STASH_CREATED=1
    else
        fail "检测到本地改动，请先提交/暂存后再更新，或设置 AUTO_STASH=1。"
    fi
fi

current_branch="$(git branch --show-current || true)"
if [[ "$current_branch" != "$BRANCH_NAME" ]]; then
    log "切换分支到 $BRANCH_NAME"
    if git show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
        git checkout "$BRANCH_NAME"
    else
        git checkout -b "$BRANCH_NAME" "$REMOTE_NAME/$BRANCH_NAME"
    fi
fi

log "拉取并对齐分支：$REMOTE_NAME/$BRANCH_NAME"
git pull --rebase "$REMOTE_NAME" "$BRANCH_NAME"

if [[ -f "$ENV_BACKUP_FILE" ]]; then
    cp "$ENV_BACKUP_FILE" "$ENV_FILE"
    rm -f "$ENV_BACKUP_FILE"
    ENV_BACKUP_FILE=""
fi

if [[ "$STASH_CREATED" -eq 1 ]]; then
    log "本地改动已暂存，可用以下命令查看/恢复："
    log "  git stash list"
    log "  git stash show -p stash@{0}"
    log "  git stash pop"
fi

log "重建并后台启动容器"
"${COMPOSE_CMD[@]}" --env-file "$ENV_FILE" up -d --build

log "当前容器状态"
"${COMPOSE_CMD[@]}" ps

log "最近日志（服务：$SERVICE_NAME）"
"${COMPOSE_CMD[@]}" logs --tail=120 "$SERVICE_NAME" || true

log "更新完成。"
panel_ip="$(detect_primary_ip)"
log "面板地址："
log "  http://127.0.0.1:${current_host_port}"
if [[ "$panel_ip" != "127.0.0.1" ]]; then
    log "  http://${panel_ip}:${current_host_port}"
else
    log "  http://<你的服务器IP>:${current_host_port}"
fi
log "登录密码：${current_access_password}"
