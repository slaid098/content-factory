#!/usr/bin/env bash
# Ночной бэкап единственной копии конфига и полного списка запросов.
# content/ и db/ намеренно не архивируются.
set -euo pipefail

umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname -- "$SCRIPT_DIR")"
APP_DATA_DIR="${APP_DATA_DIR:-${REPO_DIR}/app_data}"
BACKUP_DIR="${BACKUP_DIR:-${REPO_DIR}/backups}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
ARCHIVE="${BACKUP_DIR}/content-factory-config-queries-${STAMP}.tar.gz"

warn() {
    printf 'warning: %s\n' "$*" >&2
}

mkdir -p -- "$BACKUP_DIR"

sources=()
for name in config queries; do
    if [[ -d "${APP_DATA_DIR}/${name}" ]]; then
        sources+=("${name}")
    else
        warn "${APP_DATA_DIR}/${name} отсутствует, пропущено"
    fi
done

if ((${#sources[@]} == 0)); then
    warn "нечего архивировать в ${APP_DATA_DIR}"
    exit 0
fi

tar -C "$APP_DATA_DIR" -czf "$ARCHIVE" "${sources[@]}"
printf '%s\n' "$ARCHIVE"
