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
readonly WORKER_STARTUP_SECONDS=60
readonly WORKER_STABILITY_SECONDS=15
readonly WORKER_INTERVAL=2
readonly WORKER_LOG_TAIL=50
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

# Предыдущий тег нужен для отката, если новый образ не пройдёт проверки.
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

# Состояние контейнера в формате "status restart_count exit_code".
inspect_worker() {
    local container="$1"
    docker inspect \
        --format '{{.State.Status}} {{.State.RestartCount}} {{.State.ExitCode}}' \
        "$container" 2>/dev/null
}

# Диагноз при провале воркера: состояние без секретов и ограниченный хвост логов.
worker_diagnostics() {
    local container="${1:-}"
    local state status="unknown" restarts="unknown" exit_code="unknown"

    if [[ -z "$container" ]]; then
        log "worker: контейнер не найден (docker compose ps -q worker пуст)"
    elif state="$(inspect_worker "$container")"; then
        read -r status restarts exit_code <<<"$state"
        log "worker: status=${status} restart_count=${restarts} exit_code=${exit_code}"
    else
        log "worker: не удалось прочитать состояние контейнера ${container}"
    fi

    if ! docker compose logs --no-color --tail "$WORKER_LOG_TAIL" worker >&2; then
        warn "не удалось прочитать логи worker"
    fi
}

# Успех: контейнер дошёл до running за окно запуска и остаётся running
# с неизменным RestartCount в течение окна стабильности.
workercheck() {
    local attempt container="" state
    local status="" restarts="" exit_code="" baseline_restarts=""
    local startup_attempts=$((WORKER_STARTUP_SECONDS / WORKER_INTERVAL + 1))
    local stability_attempts=$(((WORKER_STABILITY_SECONDS + WORKER_INTERVAL - 1) / WORKER_INTERVAL))

    for ((attempt = 1; attempt <= startup_attempts; attempt++)); do
        if ! container="$(docker compose ps -q worker 2>/dev/null)"; then
            log "worker: docker compose ps завершился ошибкой (попытка ${attempt}/${startup_attempts})"
        elif [[ -z "$container" ]]; then
            log "worker: контейнер не найден (попытка ${attempt}/${startup_attempts})"
        elif state="$(inspect_worker "$container")"; then
            read -r status restarts exit_code <<<"$state"
            if [[ "$status" == "running" ]]; then
                log "worker: running, restart_count=${restarts} (попытка ${attempt})"
                baseline_restarts="$restarts"
                break
            fi
            log "worker: status=${status} restart_count=${restarts} exit_code=${exit_code} (попытка ${attempt}/${startup_attempts})"
        else
            log "worker: docker inspect завершился ошибкой (попытка ${attempt}/${startup_attempts})"
        fi

        if ((attempt < startup_attempts)); then
            sleep "$WORKER_INTERVAL"
        fi
    done

    if [[ -z "$baseline_restarts" ]]; then
        log "worker: не достиг running за ${WORKER_STARTUP_SECONDS} с"
        worker_diagnostics "$container"
        return 1
    fi

    for ((attempt = 1; attempt <= stability_attempts; attempt++)); do
        sleep "$WORKER_INTERVAL"
        if ! state="$(inspect_worker "$container")"; then
            log "worker: контейнер пропал в окне стабильности"
            worker_diagnostics "$container"
            return 1
        fi
        read -r status restarts exit_code <<<"$state"
        if [[ "$status" != "running" || "$restarts" != "$baseline_restarts" ]]; then
            log "worker: status=${status} restart_count=${restarts} exit_code=${exit_code}, ожидалось running с restart_count=${baseline_restarts}"
            worker_diagnostics "$container"
            return 1
        fi
    done

    log "worker стабилен: running, restart_count=${baseline_restarts} не менялся ${WORKER_STABILITY_SECONDS} с"
    return 0
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

if ! workercheck; then
    if rollback; then
        fail "проверка worker не прошла, выполнен откат на ${previous_tag}"
    fi
    fail "проверка worker не прошла, откат невозможен"
fi

printf '%s\n' "$tag" >"$STATE_FILE"

# Регистрация deployment не должна отменять уже выполненный up -d: при
# недоступном Prefect контейнеры остаются на новом образе.
log "регистрация deployment в Prefect"
if ! docker compose run --rm --no-deps worker prefect deploy --all; then
    warn "prefect deploy --all завершился ошибкой; контейнеры уже обновлены"
fi

log "деплой $sha завершён (${IMAGE}:${tag})"
