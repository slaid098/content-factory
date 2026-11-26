#!/usr/bin/env bash
# Снимок текстовых данных content-factory в приватный git-репозиторий.
# Аудио, шрифты и видео намеренно не копируются: это не промты.
set -euo pipefail

umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname -- "$SCRIPT_DIR")"
APP_DATA_DIR="${APP_DATA_DIR:-${REPO_DIR}/app_data}"
BACKUP_DIR="${BACKUP_DIR:-/root/dockers/content-factory-prompts}"
BACKUP_REMOTE="${BACKUP_REMOTE:-ssh://git@git.slaid098.dev:2222/slaid098/content-factory-prompts.git}"
BACKUP_SSH_KEY="${BACKUP_SSH_KEY:-/root/.ssh/content-factory-prompts}"
LOCK_FILE="${LOCK_FILE:-/tmp/content-factory-prompts.lock}"
BRANCH="main"

export GIT_SSH_COMMAND="ssh -i ${BACKUP_SSH_KEY} -p 2222 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"

log() {
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

# Текстовые файлы каталога; бинарники отсекаются списком расширений.
copy_text_files() {
    local source="$1" target="$2"
    rm -rf -- "$target"
    mkdir -p -- "$target"
    cd -- "$source"
    find . -type f \
        \( -name '*.txt' -o -name '*.yaml' -o -name '*.yml' -o -name '*.json' \
        -o -name '*.jsonl' -o -name '*.md' -o -name '*.py' -o -name '*.sh' \
        -o -name '*.sha256' \) \
        -exec cp -p --parents -t "$target" {} +
}

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "предыдущий снимок ещё выполняется, пропуск"
    exit 0
fi

if [[ ! -d "${APP_DATA_DIR}/profiles" ]]; then
    log "нет каталога ${APP_DATA_DIR}/profiles"
    exit 1
fi

mkdir -p -- "$BACKUP_DIR"

if [[ ! -d "${BACKUP_DIR}/.git" ]]; then
    git -C "$BACKUP_DIR" init -q -b "$BRANCH"
fi

if git -C "$BACKUP_DIR" remote get-url origin >/dev/null 2>&1; then
    git -C "$BACKUP_DIR" remote set-url origin "$BACKUP_REMOTE"
else
    git -C "$BACKUP_DIR" remote add origin "$BACKUP_REMOTE"
fi

cd -- "$APP_DATA_DIR"
copy_text_files profiles "${BACKUP_DIR}/profiles"
cd -- "$APP_DATA_DIR"

mkdir -p -- "${BACKUP_DIR}/config"
if [[ -f "${APP_DATA_DIR}/config/config.yaml" ]]; then
    cp -p -- "${APP_DATA_DIR}/config/config.yaml" "${BACKUP_DIR}/config/config.yaml"
else
    rm -f -- "${BACKUP_DIR}/config/config.yaml"
fi

if [[ -d "${APP_DATA_DIR}/queries" ]]; then
    rm -rf -- "${BACKUP_DIR}/queries"
    mkdir -p -- "${BACKUP_DIR}/queries"
    cd -- "${APP_DATA_DIR}/queries"
    find . -type f \
        ! -name '*.db' ! -name '*.sqlite' ! -name '*.sqlite3' \
        -exec cp -p --parents -t "${BACKUP_DIR}/queries" {} +
    cd -- "$APP_DATA_DIR"
else
    rm -rf -- "${BACKUP_DIR}/queries"
fi

git -C "$BACKUP_DIR" add -A

if git -C "$BACKUP_DIR" diff --cached --quiet; then
    log "изменений нет"
    exit 0
fi

git -C "$BACKUP_DIR" \
    -c user.name="content-factory backup" \
    -c user.email="backup@slaid098.dev" \
    commit -q -m "snapshot: $(date -u +%Y-%m-%dT%H:%MZ)"

git -C "$BACKUP_DIR" push -q origin "$BRANCH"
log "снимок отправлен"
