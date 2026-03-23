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
