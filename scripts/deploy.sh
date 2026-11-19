#!/usr/bin/env bash
# Автоматический деплой content-factory из GHCR на Linux 1.
# Исходник для root-овой копии /usr/local/sbin/content-factory-deploy
# (root:root 0755). Запускается пользователем deploy только через sudo.
set -euo pipefail

# Без TTY git не должен зависать на запросе пароля к приватному remote.
export GIT_TERMINAL_PROMPT=0

readonly WORKDIR="/root/dockers/content-factory"
readonly IMAGE="ghcr.io/slaid098/content-factory"
readonly STATE_FILE="${WORKDIR}/.deploy-image-tag"
readonly HEALTHCHECK_URL="http://127.0.0.1:8000/api/v1/stock"
readonly HEALTHCHECK_ATTEMPTS=30
readonly HEALTHCHECK_INTERVAL=2
readonly SHA_PATTERN='^[0-9a-f]{40}$'

log() {
    printf '[content-factory-deploy] %s\n' "$*" >&2
}

warn() {
    printf '[content-factory-deploy] warning: %s\n' "$*" >&2
}

fail() {
    log "error: $*"
    exit 1
}

usage() {
    printf 'usage: %s <40-char-lowercase-hex-sha>\n' "${0##*/}" >&2
}

# Аргумент проверяем до любых изменений: невалидный SHA — exit 2.
if (($# != 1)); then
    usage
    exit 2
fi

sha="$1"
if [[ ! "$sha" =~ $SHA_PATTERN ]]; then
    printf '[content-factory-deploy] error: ожидается 40-символьный SHA в нижнем регистре, получено: %q\n' \
        "$sha" >&2
    exit 2
fi

tag="sha-${sha:0:7}"

if ((EUID != 0)); then
    fail "скрипт запускается только от root через sudo"
fi

if [[ ! -d "$WORKDIR" ]]; then
    fail "нет рабочей директории $WORKDIR"
fi

for binary in docker git curl; do
    command -v "$binary" >/dev/null 2>&1 || fail "не найдена команда $binary"
done

cd "$WORKDIR"

# Предыдущий тег нужен для отката, если новый образ не пройдёт healthcheck.
previous_tag="latest"
if [[ -s "$STATE_FILE" ]]; then
    state_tag="$(head -n 1 -- "$STATE_FILE")"
    if [[ "$state_tag" =~ ^[A-Za-z0-9._-]+$ ]]; then
        previous_tag="$state_tag"
    else
        warn "файл $STATE_FILE повреждён, откат будет на latest"
    fi
fi

rollback() {
    if [[ "$previous_tag" == "$tag" ]]; then
        warn "предыдущий тег совпадает с новым, откатывать некуда"
        return 1
    fi
    if ! docker image inspect "${IMAGE}:${previous_tag}" >/dev/null 2>&1; then
        warn "образ ${IMAGE}:${previous_tag} отсутствует локально, откат невозможен"
        return 1
    fi
    log "откат на ${IMAGE}:${previous_tag}"
    export IMAGE_TAG="$previous_tag"
    docker compose up -d --remove-orphans
}

healthcheck() {
    local attempt
    for ((attempt = 1; attempt <= HEALTHCHECK_ATTEMPTS; attempt++)); do
        if curl -fsS --max-time 2 --output /dev/null "$HEALTHCHECK_URL"; then
            log "healthcheck прошёл с попытки $attempt"
            return 0
        fi
        if ((attempt < HEALTHCHECK_ATTEMPTS)); then
            sleep "$HEALTHCHECK_INTERVAL"
        fi
    done
    return 1
}

log "деплой $sha (${IMAGE}:${tag})"

log "обновление кода до $sha"
git fetch --prune --quiet origin
if ! git cat-file -e "${sha}^{commit}" 2>/dev/null; then
    fail "коммит $sha не найден после git fetch origin"
fi
git reset --hard --quiet "$sha"

log "загрузка образа ${IMAGE}:${tag}"
export IMAGE_TAG="$tag"
docker compose pull

log "перезапуск сервисов"
if ! docker compose up -d --remove-orphans; then
    if rollback; then
        fail "docker compose up завершился ошибкой, выполнен откат на ${previous_tag}"
    fi
    fail "docker compose up завершился ошибкой, откат невозможен"
fi

if ! healthcheck; then
    if rollback; then
        fail "healthcheck не прошёл, выполнен откат на ${previous_tag}"
    fi
    fail "healthcheck не прошёл, откат невозможен"
fi

printf '%s\n' "$tag" >"$STATE_FILE"

# Регистрация deployment не должна отменять уже выполненный up -d: при
# недоступном Prefect контейнеры остаются на новом образе.
log "регистрация deployment в Prefect"
if ! docker compose run --rm --no-deps worker prefect deploy --all; then
    warn "prefect deploy --all завершился ошибкой; контейнеры уже обновлены"
fi

log "деплой $sha завершён (${IMAGE}:${tag})"
