# Деплой content-factory

Прод-контур — Linux 1 (`193.3.168.35`): Docker Compose поднимает API и
Prefect-воркер, том `app_data` живёт на хосте, партнёр забирает готовые файлы
по SFTP или через веб-страницу `content.slaid098.dev`, конфиг и список
запросов уходят в ночной бэкап. Prefect 3 работает на Linux 2
(`https://prefect.slaid098.dev/api`).

Проект лежит в `/root/dockers/content-factory`, как остальные compose-проекты
Linux 1. Корень принадлежит root, поэтому деплой-скрипт запускается через
`sudo`, а пользователь `deploy` не входит в группу `docker`.

## 1. Требования

- Docker Engine 24+ и Docker Compose 2.24+ (`env_file.required`,
  `host-gateway`). На Linux 1 Docker поставлен из snap: compose не задаёт
  `security_opt` с запретом повышения привилегий, иначе AppArmor snapd
  запрещает контейнерам `exec` (snapd LP#1908448). `cap_drop: ALL` и
  loopback-порт остаются.
- Prefect 3.6.x на Linux 2, доступный воркеру. Клиент в образе закреплён
  `prefect>=3.6,<3.7`, чтобы совпадать с сервером 3.6.22.
- BitBrowser и MCP-сервер, доступные из контейнера.
- OpenSSH 9.0+ на хосте: нужны `sftp-server -P`/`-p` и запрос `copy-data`.
  Проверка: `ssh -V` и `sftp-server -Q requests` (на Debian/Ubuntu бинарник
  лежит в `/usr/lib/openssh/sftp-server`).
- `sudo`, `git`, `curl`; на Linux 1 — пользователь `deploy` с ключом и
  sudoers-правилом на деплой-скрипт.
- Право `packages: write` у workflow: образ публикуется в GHCR.

Сеть Compose зафиксирована в `compose.yaml`: `172.28.0.0/24`, шлюз
`172.28.0.1`. Сервисы на хосте должны слушать этот адрес, а не `0.0.0.0`:
из контейнера `host.docker.internal` указывает именно на шлюз. Если подсеть
занята, поменяйте её в `compose.yaml` и адреса в этом runbook.

## 2. Контур CI/CD

`.github/workflows/ci.yml` состоит из трёх job'ов:

| Job | Когда | Что делает |
|---|---|---|
| `ci` | push и PR в `main` | `ruff check`, `ruff format --check`, `mypy`, `pytest` |
| `build-push` | после `ci`, только push в `main` или `workflow_dispatch` из `main` | собирает образ и пушит `latest` и `sha-<7>` |
| `deploy` | после `build-push` | заходит по SSH на Linux 1 и запускает `/usr/local/sbin/content-factory-deploy <full-sha>` |

Красный `ci` останавливает цепочку: `build-push` объявлен с `needs: ci`, а
`deploy` — с `needs: build-push`. PR и push в другие ветки не собирают и не
деплоят образ.

Образ: `ghcr.io/slaid098/content-factory`. Теги:

- `latest` — образ из последнего зелёного `main`;
- `sha-<7>` — первые 7 символов `github.sha`, иммутабельный тег для отката.

`deploy` использует `concurrency: group: deploy-production` с
`cancel-in-progress: false`: одновременные пуши в `main` выполняются по
очереди, текущий деплой не обрывается.

Секретов в репозитории нет. Workflow получает их из GitHub Secrets:

| Секрет | Значение |
|---|---|
| `DEPLOY_HOST` | `193.3.168.35` |
| `DEPLOY_PORT` | `22` |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | приватный ключ `deploy` целиком, без пароля |
| `DEPLOY_KNOWN_HOSTS` | вывод `ssh-keyscan` для Linux 1 |

Если секреты не заданы, `deploy` падает на шаге `Check deploy secrets` с
перечислением отсутствующих имён. `build-push` от этого не страдает: он
выполняется раньше и не использует `DEPLOY_*`.

В GitHub → Settings → Actions → General → Workflow permissions должно быть
разрешено `Read and write permissions` (или явный доступ к пакетам). При
read-only настройке `GITHUB_TOKEN` не сможет запушить образ в GHCR, даже
если в workflow указано `packages: write`.

Ветку `main` держите под branch protection: деплой-скрипт выполняет от root
код из этого коммита, поэтому в неё не должно попадать ничего в обход
зелёного `ci`.

## 3. Пользователи, ключи и sudoers

### 3.1 Пользователи и ключи

На Linux 1:

```bash
sudo useradd --system --user-group --create-home --shell /bin/bash \
  --home-dir /home/deploy deploy
sudo install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
```

На рабочей машине создайте отдельную пару для GitHub Actions:

```bash
ssh-keygen -t ed25519 -C "github-actions content-factory" \
  -N "" -f content-factory-deploy
ssh-keyscan -p 22 193.3.168.35 > content-factory-known-hosts
```

Публичный ключ установите на Linux 1 с ограничениями `restrict` (запрещает
PTY и пересылки, но оставляет выполнение команды):

```bash
{ printf 'restrict '; cat content-factory-deploy.pub; } | \
  sudo install -m 600 -o deploy -g deploy /dev/stdin \
  /home/deploy/.ssh/authorized_keys
```

Приватный ключ и `known_hosts` попадут только в GitHub Secrets (см. §3.3).
Не коммитьте их и не копируйте в `/root/dockers/content-factory`.

Для SFTP нужен отдельный системный пользователь `content-partner`, которому
принадлежит `app_data/content/ru`. Создайте его и положите публичный ключ
партнёра:

```bash
sudo groupadd --system content-partner
sudo useradd --system --gid content-partner \
  --home-dir /var/lib/content-partner --shell /usr/sbin/nologin content-partner
sudo install -d -m 700 -o content-partner -g content-partner /var/lib/content-partner
sudo install -d -m 700 -o content-partner -g content-partner /var/lib/content-partner/.ssh
sudo install -m 600 -o content-partner -g content-partner \
  partner-key.pub /var/lib/content-partner/.ssh/authorized_keys
```

Ключ можно дополнительно ограничить префиксом `restrict` в `authorized_keys`:
он запрещает PTY и пересылки на уровне ключа. Не путайте `content-partner` с
пользователем `deploy`: первый читает готовые файлы по SFTP, второй только
запускает деплой через sudo.

### 3.2 sudoers

Пользователь `deploy` не входит в группу `docker` и не имеет доступа к
каталогу `/root`. Единственное повышение прав — один скрипт:

```bash
sudo tee /etc/sudoers.d/content-factory-deploy >/dev/null <<'EOF'
deploy ALL=(root) NOPASSWD: /usr/local/sbin/content-factory-deploy
EOF
sudo chmod 0440 /etc/sudoers.d/content-factory-deploy
sudo visudo -c
```

Правило без аргументов разрешает любые аргументы команды, поэтому скрипт сам
проверяет, что аргумент — 40-символьный SHA в нижнем регистре, и до этой
проверки ничего не меняет. Скрипт также требует `EUID=0`, так что прямой
запуск от `deploy` ничего не делает.

Проверка правила:

```bash
sudo -u deploy sudo -n -l /usr/local/sbin/content-factory-deploy
```

### 3.3 Секреты GitHub

С рабочей машины, где авторизован `gh` с правами администратора репозитория:

```bash
gh secret set DEPLOY_HOST --body "193.3.168.35"
gh secret set DEPLOY_PORT --body "22"
gh secret set DEPLOY_USER --body "deploy"
gh secret set DEPLOY_SSH_KEY < content-factory-deploy
gh secret set DEPLOY_KNOWN_HOSTS < content-factory-known-hosts
gh secret list
```

`DEPLOY_SSH_KEY` — приватный ключ, `DEPLOY_KNOWN_HOSTS` — файл из
`ssh-keyscan`. Оба значения передаются только через Secrets и не печатаются
в логах workflow.

## 4. Клон и каталоги

Клонируйте репозиторий от root: деплой-скрипт делает `git fetch` и
`git reset --hard`, поэтому рабочая копия должна быть root-овой.

```bash
sudo install -d -m 0755 /root/dockers
sudo git clone https://github.com/slaid098/content-factory.git \
  /root/dockers/content-factory
sudo chown -R root:root /root/dockers/content-factory
```

Если репозиторий приватный, настройте для root read-доступ (deploy key или
credential helper) до первого `git fetch`. Ключ не храните в репозитории.

### 4.1 Каталоги chroot

sshd требует, чтобы **каждый** компонент пути `ChrootDirectory` принадлежал
root и не был доступен на запись группе и остальным. `/root` имеет режим
`0700` — для chroot это допустимо, sshd заходит в путь от root. Корень
репозитория, `app_data` и `app_data/content` — `root:root 0755`:

```bash
sudo chown root:root /root/dockers/content-factory
sudo chmod 0755 /root/dockers/content-factory
sudo install -d -o root -g root -m 0755 \
  /root/dockers/content-factory/app_data \
  /root/dockers/content-factory/app_data/content
namei -l /root/dockers/content-factory/app_data/content
```

Последняя команда должна показать `root root` на всех компонентах и
отсутствие права записи для group/other. `/root` при этом остаётся
`drwx------`.

`app_data/config`, `app_data/db`, `app_data/queries`, `app_data/archive` и
`app_data/generated` принадлежат `10001` (пользователь `app` в контейнере) и закрыты от остальных
(`0750`); это делает одноразовый сервис `init` при каждом `docker compose up`.
`archive` лежит вне SFTP-chroot `content/` и партнёру не виден.

### 4.2 Каталоги языков

Приложение не может писать в root-овый `content`, поэтому каталоги активных
языков создаются заранее:

```bash
sudo install -d -o 10001 -g content-partner -m 2770 \
  /root/dockers/content-factory/app_data/content/ru
```

`setgid` (`2770`) заставляет новые файлы наследовать группу
`content-partner`, поэтому партнёр читает их. Файлы поставщик создаёт с umask
`022`, то есть `0644`: группа получает только чтение. Не делайте файлы
group-writable: `-P write` запрещает SFTP-запрос `write`, но `open` с
`O_TRUNC` остаётся доступен, и такой файл можно обнулить. Проверка:

```bash
sudo find /root/dockers/content-factory/app_data/content -perm /022 -print
```

Вывод должен быть пустым.

Если включаете ещё язык, создайте для него такой же каталог и добавьте его в
`config.yaml`. Выключенные языки каталогов не получают и партнёру не видны.

## 5. Переменные окружения

Compose читает `.env` из корня проекта: значения подставляются в
`compose.yaml`, а сервис `worker` получает файл целиком через `env_file`.
Поэтому тонкие настройки `SUPPLIER_*` можно менять в `.env`, не редактируя
compose. Каталог принадлежит root, поэтому `.env` создаём от root с правами
`0600`:

```bash
# install выполняйте только при первом создании: существующий .env не
# перезаписывайте, иначе потеряете ключи и настройки.
sudo install -m 600 -o root -g root /dev/null \
  /root/dockers/content-factory/.env
sudo tee /root/dockers/content-factory/.env >/dev/null <<'EOF'
PREFECT_API_URL=https://prefect.slaid098.dev/api
PREFECT_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
SUPPLIER_MCP_URL=http://host.docker.internal:8765
SUPPLIER_BITBROWSER_URL=http://host.docker.internal:54529
# id профиля BitBrowser обязателен
SUPPLIER_PROFILE_ID=
SUPPLIER_PROXY=
# Bearer-токен выдачи контента; пустое значение выключает endpoint (503).
CONTENT_FACTORY_DELIVERY_TOKEN=
CONTENT_FACTORY_DELIVERY_RETENTION_HOURS=24
# Генератор дейтинг-роликов (flow generate_batch). OPENAI_API_KEY и
# OPENAI_BASE_URL — общие ключ и адрес LLM для названий и генератора;
# VAST_API_TOKEN — ключ Vast.ai с правом аренды.
OPENAI_API_KEY=
OPENAI_BASE_URL=https://ollama.com/v1
TITLES_MODEL=deepseek-v4.1-flash
VAST_API_TOKEN=
GENERATOR_MODEL=deepseek-v4.1-flash
GENERATOR_MAX_COST_PER_HOUR=
# Сколько серверов арендовать параллельно: первый ответивший на /system_stats
# остаётся, остальные уничтожаются. 1 — по одному, как раньше.
GENERATOR_RENT_POOL_SIZE=3
# Как часто (сек) перепроверять рынок и добирать офферы в свободные слоты.
GENERATOR_OFFER_REFRESH_SECONDS=60
# Проверка кадров Clef до плашки: сначала один запрос на синтетическом кадре
# до аренды, затем 12 кадров каждого исходника. Выключение возвращает прежнее
# поведение без проверки.
GENERATOR_SAFETY_ENABLED=true
GENERATOR_SAFETY_FRAMES=12
GENERATOR_SAFETY_THRESHOLD=0.5
GENERATOR_SAFETY_NUDE_THRESHOLD=0.3
GENERATOR_SAFETY_FRAME_ATTEMPTS=2
GENERATOR_CLEF_MODEL=clef
EOF
```

| Переменная | Обязательно | По умолчанию | Назначение |
|---|---|---|---|
| `PREFECT_API_URL` | да | `http://host.docker.internal:4200/api` | API Prefect 3 на Linux 2 |
| `PREFECT_API_KEY` | для защищённого API | — | ключ API Prefect |
| `TELEGRAM_BOT_TOKEN` | нет | — | токен бота для алертов; без него алерты выключены |
| `TELEGRAM_CHAT_ID` | нет | — | чат алертов |
| `SUPPLIER_MCP_URL` | да | `http://host.docker.internal:8765` | MCP-сервер BitBrowser |
| `SUPPLIER_BITBROWSER_URL` | да | `http://host.docker.internal:54529` | API BitBrowser |
| `SUPPLIER_PROFILE_ID` | да | — | профиль BitBrowser для TikTok |
| `SUPPLIER_PROXY` | нет | — | прокси движка: `http://`, `https://`, `socks5://`, `socks5h://` |
| `CONTENT_FACTORY_DELIVERY_TOKEN` | нет | — | Bearer-токен `POST /api/v1/content/deliveries`; пусто — выдача выключена |
| `CONTENT_FACTORY_DELIVERY_RETENTION_HOURS` | нет | `24` | TTL zip-архивов выдачи, часов (≥ 1) |
| `OPENAI_API_KEY` | для названий и генерации | — | общий ключ OpenAI-совместимого API; пусто — flow `titles` выключен |
| `OPENAI_BASE_URL` | нет | `https://ollama.com/v1` | общий адрес API без завершающего `/` |
| `TITLES_MODEL` | нет | `deepseek-v4.1-flash` | модель названий |
| `VAST_API_TOKEN` | для генерации | — | ключ Vast.ai с правом аренды серверов |
| `GENERATOR_MODEL` | нет | `deepseek-v4.1-flash` | имя модели для текстов роликов |
| `GENERATOR_MAX_COST_PER_HOUR` | нет | — | глобальный потолок цены сервера, $/час; пусто — только `profile.max_cost` |
| `GENERATOR_RENT_POOL_SIZE` | нет | `3` | сколько серверов арендовать параллельно (1–8); первый готовый остаётся, остальные уничтожаются |
| `GENERATOR_OFFER_REFRESH_SECONDS` | нет | `60` | как часто пул перепроверяет рынок и добирает новые офферы в свободные слоты, секунд (10–600) |
| `GENERATOR_SAFETY_ENABLED` | нет | `true` | проверять исходники Clef до плашки и не арендовать сервер, если preflight не прошёл |
| `GENERATOR_SAFETY_FRAMES` | нет | `12` | сколько кадров каждого исходника проверять, включая последний (3–24) |
| `GENERATOR_SAFETY_THRESHOLD` | нет | `0.5` | порог вопросов `back`, `short_skirt`, `cleavage`, `all_skin` |
| `GENERATOR_SAFETY_NUDE_THRESHOLD` | нет | `0.3` | порог вопроса `nude` |
| `GENERATOR_SAFETY_FRAME_ATTEMPTS` | нет | `2` | сколько раз генерировать ролик при браке кадров (1–3); затем слот `failed` |
| `GENERATOR_CLEF_MODEL` | нет | `clef` | имя модели Clef в `/systemone` |
| `CONTENT_FACTORY_LOG_LEVEL` | нет | `INFO` | уровень loguru в API и воркере |

Пул аренды держит до `GENERATOR_RENT_POOL_SIZE` оплачиваемых инстансов, пока
ни один не ответит `/system_stats`; затем все проигравшие уничтожаются с
проверкой по списку аккаунта. Пока пул ждёт, он раз в
`GENERATOR_OFFER_REFRESH_SECONDS` повторяет поиск и занимает освободившиеся
слоты новыми офферами, не арендуя сверх размера пула.
`GENERATOR_RENT_POOL_SIZE=1` возвращает прежний последовательный перебор.
С пулом выставьте на сервере
`GENERATOR_SERVER_READY_TIMEOUT=900`: общий дедлайн гонки в 15 минут дешевле
часа ожидания одного медленного хоста.

При `GENERATOR_SAFETY_ENABLED=true` партия сначала делает один запрос Clef на
синтетическом кадре: если модель или ключ недоступны, сервер не арендуется.
Каждый скачанный исходник проверяется до плашки по
`GENERATOR_SAFETY_FRAMES` кадрам, последний — за 0,05 с до конца ролика. Брак
кадров перегенерируется на том же сервере с новым seed; после
`GENERATOR_SAFETY_FRAME_ATTEMPTS` попыток слот получает `failed` с причиной в
`run.json`. Сетевые ошибки и 5xx Clef повторяются один раз и затем тоже
закрывают ролик как `failed`.

`IMAGE_TAG` в `.env` не задаётся: его экспортирует деплой-скрипт. Значение по
умолчанию — `latest` (`compose.yaml`). `CONTENT_FACTORY_DATA_DIR=/app/app_data`
жёстко задан в `compose.yaml`: это путь внутри контейнера, а на хосте ему
соответствует `./app_data`.

У `SUPPLIER_*` с таймингами и лимитами есть значения по умолчанию в коде.
Полный список:

<details>
<summary>Необязательные настройки поставщика</summary>

| Переменная | По умолчанию |
|---|---|
| `SUPPLIER_MAX_VIEWS` | `1000` |
| `SUPPLIER_MAX_FOLLOWERS` | `500` |
| `SUPPLIER_MIN_DURATION` | `5` |
| `SUPPLIER_MAX_VIDEO_DURATION` | `60` |
| `SUPPLIER_MIN_AGE_DAYS` | `5` |
| `SUPPLIER_MAX_VIDEOS_PER_AUTHOR` | `1` (допустимо только `1`) |
| `SUPPLIER_SCROLLS_MIN` / `SUPPLIER_SCROLLS_MAX` | `5` / `8` |
| `SUPPLIER_QUERIES_PER_SESSION_MIN` / `SUPPLIER_QUERIES_PER_SESSION_MAX` | `12` / `18` |
| `SUPPLIER_PAUSE_SCROLL_MIN` / `SUPPLIER_PAUSE_SCROLL_MAX` | `1.5` / `4.0` |
| `SUPPLIER_PAGE_SETTLE_MIN` / `SUPPLIER_PAGE_SETTLE_MAX` | `3.0` / `6.0` |
| `SUPPLIER_SESSION_BREAK_MIN` / `SUPPLIER_SESSION_BREAK_MAX` | `180` / `300` |
| `SUPPLIER_CAPTCHA_COOLDOWN_MIN` / `SUPPLIER_CAPTCHA_COOLDOWN_MAX` | `900` / `1800` |
| `SUPPLIER_PROBE_WORKERS` | `8` |
| `SUPPLIER_HEADLESS` | `false` |
| `SUPPLIER_MCP_TIMEOUT` | `90` |
| `SUPPLIER_CLOSE_PROFILE_AFTER_CRAWL` | `true` |

</details>

После первого деплоя пересоздайте воркер на том же образе, что API. Тег
берётся из `.deploy-image-tag`, который пишет деплой-скрипт:

```bash
cd /root/dockers/content-factory
sudo env IMAGE_TAG="$(sudo cat .deploy-image-tag)" docker compose pull worker
sudo env IMAGE_TAG="$(sudo cat .deploy-image-tag)" docker compose up -d worker
```

Без `IMAGE_TAG` compose подставит `latest` и может поднять не тот образ,
который задеплоен (а при отсутствии `latest` — собрать локальный из `build: .`).

## 6. sshd

Создайте `/etc/ssh/sshd_config.d/60-content-partner.conf`:

```sshconfig
Match User content-partner
    ChrootDirectory /root/dockers/content-factory/app_data/content
    ForceCommand internal-sftp -d / -P write,setstat,fsetstat,mkdir,rmdir,rename,posix-rename,symlink,hardlink,lsetstat,fsync,copy-data,users-groups-by-id,expand-path,home-directory
    DisableForwarding yes
    AuthenticationMethods publickey
    KbdInteractiveAuthentication no
    PasswordAuthentication no
    PermitTTY no
    PermitTunnel no
    X11Forwarding no

Match all
```

`-d /` задаёт стартовый каталог внутри chroot: домашний каталог пользователя
`/var/lib/content-partner` находится снаружи. `-P` запрещает изменяющие
запросы, включая `write` и `copy-data`; `open` и `remove` остаются
разрешёнными — иначе не работают чтение и удаление.

`Match all` обязателен: файл подключается через `Include` в начале
`sshd_config`, и без него все последующие глобальные директивы попали бы в
блок партнёра. `DisableForwarding` закрывает TCP- и Unix-socket forwarding;
`AuthenticationMethods publickey` не даёт включиться keyboard-interactive.

Drop-in опирается на глобальную строку `Subsystem sftp ...` в основном
`sshd_config` (в Debian/Ubuntu она есть). В минимальном образе её нужно
добавить.

Проверьте оба прохода и перезагрузите:

```bash
sudo sshd -t
sudo sshd -T -C user=content-partner,host=localhost,addr=127.0.0.1 \
  | grep -E 'chrootdirectory|forcecommand|disableforwarding|permittty'
sudo systemctl reload ssh    # на Debian/Ubuntu; на других системах — sshd
```

### 6.1 Проверка

```bash
sftp content-partner@<host>
sftp> ls                 # виден ru/
sftp> get ru/<sha256>.mp4
sftp> rm ru/<sha256>.mp4  # удаление проходит
sftp> put /etc/hostname ru/test.mp4  # ошибка: write запрещён
```

`put` может создать пустой файл (`open` с `O_CREAT` разрешён), но данные не
запишутся; удалите его через `rm`. После проверки убедитесь, что в `ru/` не
осталось файлов нулевого размера.

OpenSSH не различает «создать» и «удалить» на уровне POSIX: и то и другое
требует write на каталог. Поэтому запись запрещена списком `-P`: сервер
отклоняет `write`, `setstat`, `rename`, `mkdir` и остальные изменяющие
запросы. Существующие `*.mp4` при этом не обнуляются: они принадлежат `app`
и не доступны партнёру на запись.

## 7. GHCR

Workflow пушит образ в `ghcr.io/slaid098/content-factory` от
`GITHUB_TOKEN` с правом `packages: write`; отдельный PAT для CI не нужен.

Пакет GHCR по умолчанию приватный. Тогда `docker compose pull` на Linux 1
падает с `unauthorized`. Два варианта:

1. **Публичный пакет.** GitHub → Packages → `content-factory` → Package
   settings → Change visibility → Public. Образ содержит код и зависимости,
   но не `.env` и не `app_data`; перед сменой видимости убедитесь, что в
   слоях нет секретов.
2. **Приватный пакет и read-токен.** Создайте PAT с единственным правом
   `read:packages` и залогиньтесь от root на Linux 1:

   ```bash
   sudo docker login ghcr.io -u slaid098
   # токен вводится в приглашении, в git и .env он не попадает
   ```

   Токен сохранится в `/root/.docker/config.json`. Тот же логин нужен после
   ротации токена.

Проверка:

```bash
sudo docker pull ghcr.io/slaid098/content-factory:latest
```

Деплой-скрипт логин не выполняет: он рассчитывает, что публичный пакет
доступен без авторизации или credential уже лежит у root.

## 8. Prefect и первый деплой

### 8.1 Пул до первого деплоя

Пул `content-factory` создаётся на сервере Prefect до первого запуска
deploy-скрипта. Если пула нет, `prefect deploy --all` только предупреждает, а
контейнеры уже обновлены; flow при этом не запустится.

С машины с настроенным Prefect CLI:

```bash
PREFECT_API_URL=https://prefect.slaid098.dev/api \
  prefect work-pool create content-factory --type process
```

Если пул уже существует, команда завершится ошибкой — это нормально.
Проверка:

```bash
PREFECT_API_URL=https://prefect.slaid098.dev/api prefect work-pool ls
```

### 8.2 Установка деплой-скрипта

`scripts/deploy.sh` — исходник root-овой копии. Установите её после клона и
переустанавливайте вручную при каждом изменении скрипта: автоматический
деплой не должен обновлять код, который выполняется от root.

```bash
sudo install -o root -g root -m 0755 \
  /root/dockers/content-factory/scripts/deploy.sh \
  /usr/local/sbin/content-factory-deploy
```

Проверка контракта без изменений:

```bash
sudo /usr/local/sbin/content-factory-deploy not-a-sha; echo "exit=$?"  # 2
```

### 8.3 Первый запуск

Запустите workflow вручную из `main` (или сделайте push в `main`):

```bash
gh workflow run ci.yml --ref main
gh run watch
```

Цепочка: `ci` → `build-push` (образ `latest` и `sha-<7>`) → `deploy`
(`sudo /usr/local/sbin/content-factory-deploy <full-sha>`). Деплой-скрипт
проверит SHA, обновит git-чекаут, выполнит `docker compose pull`, поднимет
сервисы, дождётся здорового API и стабильного `worker`.

Проверка на Linux 1:

```bash
cd /root/dockers/content-factory
sudo docker compose ps
curl -f http://127.0.0.1:8000/api/v1/stock
sudo cat .deploy-image-tag
sudo docker image ls ghcr.io/slaid098/content-factory
```

- API слушает `0.0.0.0:8000` внутри контейнера, наружу порт опубликован
  только как `127.0.0.1:8000:8000`; новых публичных портов нет.
- Одноразовый сервис `init` создаёт `app_data/{config,db,queries,archive}`,
  выставляет им владельца `10001` и фиксирует `root:root 0755` на `app_data`
  и `content`. Каталог `ru` он не трогает.
- `./app_data` — bind mount с хоста, в образ не попадает. `git reset --hard`
  его не удаляет: каталог в `.gitignore`.
- Healthcheck API: `curl -f http://127.0.0.1:8000/api/v1/stock`.

Снаружи API доступен только через SSH-туннель:

```bash
ssh -L 8000:127.0.0.1:8000 user@linux-1
curl -f http://127.0.0.1:8000/api/v1/stock
```

После первого деплоя проверьте deployment:

```bash
cd /root/dockers/content-factory
sudo docker compose run --rm --no-deps worker prefect deployment ls
sudo docker compose logs -f worker
```

`prefect.yaml` задаёт два deployment: `stock_topup` с cron `*/15 * * * *` и
`titles` с cron `*/10 * * * *`. Оба используют pool `content-factory`, шаг
`set_working_directory` на `/app`, где в образе лежат `src/` и `prefect.yaml`;
deployment не зависит от текущего каталога процесса. Cron `stock_topup` — это
тик планировщика; решение о производстве принимает сам flow по
`config.run.interval`, поэтому холостые тики выходят сразу.

Одновременность ограничена на двух уровнях:

- `concurrency_limit: 1` у каждого deployment в `prefect.yaml` — серверный
  лимит. Это timed lease: пока run жив, Prefect его продлевает, а
  `collision_strategy` по умолчанию (`ENQUEUE`) ставит новые тики в очередь.
  Если воркер убит (например, деплоем), lease убитого run'а истекает и сервер
  отдаёт слот следующему run'у, хотя state зомби остаётся `RUNNING`.
- `--limit 2` в команде сервиса `worker` (`compose.yaml`) — жёсткий кап
  воркера, не зависящий от lease. Два слота нужны, чтобы долгий
  `stock_topup` не блокировал тики `titles`: каждый deployment всё равно
  выполняет не больше одного run, но два разных deployment могут идти
  одновременно. Если Prefect всё же выдаст лишний run, воркер напишет
  `Flow run limit reached` и не запустит его, пока не освободится слот.

Деплой может перезапустить `worker` и прервать активные run — это допустимо:
после рестарта запускается не больше одного run на deployment. Второй
`worker` в пуле per-worker лимит не покрывает и один run на весь пул не
гарантирует — это вне текущего контура.

## 9. Автоматический деплой и откат

`deploy` job выполняет на Linux 1 ровно одну команду от root:

```bash
sudo -n /usr/local/sbin/content-factory-deploy <full-sha>
```

`scripts/deploy.sh`:

1. принимает только `^[0-9a-f]{40}$`, иначе `exit 2` до любых изменений;
2. проверяет, что запущен от root, и что есть `docker`, `git`, `curl`;
3. читает предыдущий тег из `.deploy-image-tag` (по умолчанию `latest`);
4. делает `git fetch` (без интерактивного запроса пароля) и
   `git reset --hard <sha>`;
5. экспортирует `IMAGE_TAG=sha-<7>`, выполняет `docker compose pull` и
   `docker compose up -d --remove-orphans`;
6. до 30 раз с интервалом 2 с проверяет
   `http://127.0.0.1:8000/api/v1/stock`;
7. проверяет `worker`: находит контейнер через `docker compose ps -q worker`
   (пустой вывод — провал), до 60 с ждёт `State.Status == running`, затем не
   меньше 15 с (опрос раз в 2 с) требует, чтобы статус оставался `running`, а
   `RestartCount` не рос; при провале печатает `State.Status`,
   `RestartCount`, `State.ExitCode` и ограниченный
   `docker compose logs --tail 50 worker`;
8. при провале healthcheck API или проверки `worker` возвращает предыдущий
   `IMAGE_TAG` и поднимает прежний образ; если предыдущего образа нет
   локально или его тег совпадает с новым, откат невозможен — скрипт
   завершается с ненулевым кодом и не пишет state;
9. только после обеих проверок записывает новый тег в `.deploy-image-tag`;
10. best-effort запускает `docker compose run --rm --no-deps worker prefect
    deploy --all`: ошибка Prefect логируется, но не отменяет уже выполненный
    `up -d`.

Сервис `init` — one-shot: он завершается с кодом 0, а скрипт отдельно его не
проверяет. `api` и `worker` объявлены с
`depends_on: condition: service_completed_successfully`, поэтому к моменту
проверок `init` уже отработал.

Скрипт не печатает `.env`, не включает `set -x` и не логирует секреты.
Полный SHA остаётся в логе деплоя; тег `sha-<7>` может совпасть у двух
коммитов, для репозитория это допустимо.

Ручной откат на предыдущий коммит:

```bash
cd /root/dockers/content-factory
sudo git log --oneline -5
sudo /usr/local/sbin/content-factory-deploy <full-sha-предыдущего-коммита>
```

Скрипт сам скачает тег `sha-<7>` старого коммита, поднимет контейнеры и
обновит `.deploy-image-tag`. Если нужного образа нет ни локально, ни в
GHCR, сначала опубликуйте его или соберите вручную. Откат не трогает
`app_data`.

Изменения в `scripts/deploy.sh` вступают в силу только после ручной
переустановки из §8.2. Изменения в `scripts/backup_app_data.sh` — после
переустановки из §10.

## 10. Бэкап

`scripts/backup_app_data.sh` архивирует только `app_data/config` и
`app_data/queries`; `content/` и `db/` не трогаются. Если каталогов нет,
скрипт печатает предупреждение и завершается успешно.

Cron работает от root, поэтому установите root-овую копию скрипта: изменение
файла в git-чекауте не должно давать выполнение от root.

```bash
sudo install -d -m 700 -o root -g root /var/backups/content-factory
sudo install -o root -g root -m 0755 \
  /root/dockers/content-factory/scripts/backup_app_data.sh \
  /usr/local/sbin/content-factory-backup
sudo crontab -e
```

```cron
15 3 * * * APP_DATA_DIR=/root/dockers/content-factory/app_data BACKUP_DIR=/var/backups/content-factory /usr/local/sbin/content-factory-backup >> /var/log/content-factory-backup.log 2>&1
```

`APP_DATA_DIR` обязателен: установленная копия не может вычислить корень
репозитория по своему пути.

Проверка:

```bash
sudo env APP_DATA_DIR=/root/dockers/content-factory/app_data \
  BACKUP_DIR=/var/backups/content-factory \
  /usr/local/sbin/content-factory-backup
sudo tar -tzf /var/backups/content-factory/content-factory-config-queries-*.tar.gz
```

Восстановление: остановите `worker`, распакуйте архив в `app_data/`, затем
поднимите воркер на задеплоенном образе (тег лежит в `.deploy-image-tag`):

```bash
cd /root/dockers/content-factory
sudo env IMAGE_TAG="$(sudo cat .deploy-image-tag)" docker compose pull worker
sudo env IMAGE_TAG="$(sudo cat .deploy-image-tag)" docker compose up -d worker
```

`init` выставит владельца файлов `10001`.

## 11. Обновление и обслуживание

Обычное обновление — push в `main`. CI собирает образ, деплой обновляет
Linux 1 и регистрирует deployment в Prefect.

Ручной прогон того же контура:

```bash
gh workflow run ci.yml --ref main
```

Локальная проверка compose на Linux 1:

```bash
cd /root/dockers/content-factory
sudo docker compose config -q
sudo docker compose ps
```

Если менялся `prefect.yaml`, деплой-скрипт сам выполнит `prefect deploy
--all`. Если менялся `scripts/deploy.sh` или `scripts/backup_app_data.sh`,
переустановите соответствующую root-овую копию вручную. Если менялся
`compose.yaml`, новый образ всё равно приходит из GHCR; локальная сборка из
`docker/prod/Dockerfile` (контекст — корень репозитория) остаётся только для
разработки (`docker compose up -d --build`).

## 12. Веб-выдача для партнёра

Партнёру не нужен SFTP, чтобы забрать готовые видео: API отдаёт страницу
`GET /` и JSON выдачи, а NPM на `92.119.114.77` терминирует TLS для
`content.slaid098.dev`, пускает только IP партнёра и подставляет токен
выдачи. Страница и её скрипты токена не содержат.

Страница состоит из вкладок «Выдача», «Запросы» и «Настройки». «Выдача»
запускает асинхронную сборку `POST /api/v1/content/deliveries/link` и
опрашивает её статус; готовый архив открывается внешней ссылкой Litterbox или
локальным `/api/v1/content/archives/...`. «Запросы» читают и дополняют файл
запросов, «Настройки» показывают diff и применяют runtime-конфиг с бэкапом.

Схема запроса:

```
браузер партнёра
  → NPM 92.119.114.77:443 (TLS, Access List)
  → 172.20.0.1:8001 в сети digital_factory_app_network
  → SSH-туннель linux2 → 127.0.0.1:8000 API на linux1
```

`172.20.0.1` — шлюз сети `digital_factory_app_network`, в которой живёт
контейнер NPM. Из контейнера адрес хоста linux2 доступен как шлюз этой сети,
а не как `127.0.0.1`. Адрес API для NPM — `http://172.20.0.1:8001`; туннель
слушает и `172.20.0.1:8001` (для NPM), и `127.0.0.1:8001` (для проверки с
хоста).

### 12.1 SSH-туннель linux2 → linux1

На linux2 создайте служебного пользователя и ключ:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin tunnel
sudo install -d -m 700 -o tunnel -g tunnel /home/tunnel/.ssh
sudo -u tunnel ssh-keygen -t ed25519 -N "" -f /home/tunnel/.ssh/id_ed25519
sudo -u tunnel sh -c 'ssh-keyscan -H 193.3.168.35 >> ~/.ssh/known_hosts'
```

Публичный ключ добавьте на linux1 пользователю, от которого разрешён вход
(в примере ниже — `deploy`), с ограничением только на один forward:

```
restrict,port-forwarding,permitopen="127.0.0.1:8000" ssh-ed25519 AAAA... tunnel@linux2
```

`restrict` запрещает PTY, агент и остальные пересылки, а `port-forwarding`
возвращает только TCP-forward; `permitopen` оставляет единственный адрес
`127.0.0.1:8000`. Пользователь туннеля не получает shell.

Юнит `/etc/systemd/system/content-factory-tunnel.service`:

```ini
[Unit]
Description=SSH tunnel to content-factory API on linux1
Wants=network-online.target
After=network-online.target docker.service

[Service]
User=tunnel
Group=tunnel
ExecStart=/usr/bin/ssh -N \
  -o BatchMode=yes \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L 127.0.0.1:8001:127.0.0.1:8000 \
  -L 172.20.0.1:8001:127.0.0.1:8000 \
  deploy@193.3.168.35
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`ExitOnForwardFailure` не даёт юниту «зависнуть» без туннеля: если порт
занят или адрес `172.20.0.1` ещё не появился, ssh завершается, и systemd
перезапускает его. На linux2 Docker поставлен из snap, поэтому вместо
`docker.service` может понадобиться `After=snap.docker.dockerd.service`;
проверьте `systemctl list-units --type=service | grep -i docker`. Если сеть
`digital_factory_app_network` поднимается не сразу, `RestartSec=5` закрывает
разрыв.

Установка и проверка:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now content-factory-tunnel.service
systemctl status content-factory-tunnel.service
curl -fsS http://127.0.0.1:8001/api/v1/stock
```

Адрес шлюза сети проверяется на linux2:

```bash
docker network inspect digital_factory_app_network \
  -f '{{ (index .IPAM.Config 0).Gateway }}'
```

Он должен совпадать с адресом в юните и в NPM. Если сеть называется иначе,
подставьте её имя и её шлюз.

### 12.2 NPM: proxy host, TLS и Access List

В NPM → Access Lists создайте список «Партнёр» с IP партнёра (один адрес,
например `203.0.113.10`). В NPM → Hosts → Proxy Hosts → Add:

| Поле | Значение |
|---|---|
| Domain Names | `content.slaid098.dev` |
| Scheme | `http` |
| Forward Hostname / IP | `172.20.0.1` |
| Forward Port | `8001` |
| Block Common Exploits | включено |
| Websockets Support | выключено |
| Access List | «Партнёр» |

DNS-запись `content.slaid098.dev → 92.119.114.77` уже существует; отдельный
A-рекорд не создаётся.

На вкладке SSL запросите сертификат Let's Encrypt, включите `Force SSL`,
`HTTP/2 Support` и при желании `HSTS Enabled`. Без `Force SSL` партнёр
сможет открыть страницу по HTTP в обход HSTS. Если выпуск сертификата
упирается в Access List, временно отключите список на время выпуска и
включите обратно после сохранения сертификата.

Токен подставляется только для `/api/v1/content/`: NPM не должен отправлять
его на страницу и в публичный сток. Откройте у proxy host вкладку Custom
Locations, добавьте location:

| Поле | Значение |
|---|---|
| Location | `/api/v1/content/` |
| Scheme | `http` |
| Forward Hostname / IP | `172.20.0.1` |
| Forward Port | `8001` |

В поле Advanced configuration этой custom location вставьте:

```nginx
proxy_set_header Authorization "Bearer <токен>";
proxy_read_timeout 300s;
proxy_send_timeout 300s;
```

`<токен>` — значение `CONTENT_FACTORY_DELIVERY_TOKEN` из `.env` на linux1.
В репозиторий и в страницу оно не попадает. `proxy_read_timeout 300s`
нужен, потому что сборка десяти видео занимает минуты; таймаут меньше
пяти минут оборвёт POST, пока API ещё пишет архив.

`POST /api/v1/content/deliveries/link` отвечает `202` сразу, но за ним идут
опрос статуса раз в 3 секунды и скачивание локального архива, поэтому оба
таймаута остаются 300 с. Внешний архив Litterbox принимает до 1 ГБ. Если
архив больше лимита или заливка не удалась, zip остаётся на сервере, job
получает `ready_local`, и страница даёт ссылку на
`/api/v1/content/archives/<language>/<request_id>.zip`. Ссылка Litterbox
публичная и обходит Access List: это осознанно, видео уже публичны, а ссылка
живёт 24 часа. Access List и токен применяются к странице, API и локальным
архивам.

NPM сам добавляет к custom location стандартные `Host`, `X-Real-IP`,
`X-Forwarded-For` и `X-Forwarded-Proto`. Не добавляйте `Authorization` в
общий Advanced configuration proxy host: тогда заголовок уйдёт и на `/`, и в
`/api/v1/stock`, хотя им он не нужен.

Проверьте конфиг и примените изменения:

```bash
sudo docker exec nginx-proxy-manager nginx -t
```

Контейнер NPM может называться иначе; имя смотрите в `docker ps`. NPM
применяет конфиг сам после сохранения, `nginx -t` — только проверка.

### 12.3 Проверка доступа

С разрешённого IP:

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' https://content.slaid098.dev/   # 200
curl -fsS https://content.slaid098.dev/api/v1/stock | head -c 300
curl -fsS "https://content.slaid098.dev/api/v1/content/topics?language=ru"

request_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
curl -fsS -X POST https://content.slaid098.dev/api/v1/content/deliveries \
  -H "Content-Type: application/json" \
  -d "{\"language\":\"ru\",\"count\":1,\"request_id\":\"$request_id\"}" \
  -o delivery.zip
unzip -l delivery.zip
curl -fsS -o delivery-again.zip \
  "https://content.slaid098.dev/api/v1/content/archives/ru/$request_id.zip"
```

Повторное скачивание не пересобирает архив и не тратит остаток. Синхронная
выдача выше остаётся для воркеров YouTube Kit; ручной сценарий использует
асинхронную ссылку:

```bash
async_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
curl -fsS -X POST https://content.slaid098.dev/api/v1/content/deliveries/link \
  -H "Content-Type: application/json" \
  -d "{\"language\":\"ru\",\"count\":10,\"request_id\":\"$async_id\"}"
curl -fsS \
  "https://content.slaid098.dev/api/v1/content/deliveries/ru/$async_id"
```

В браузере с разрешённого IP страница показывает остаток по языкам и темы
только для чтения. На вкладке «Выдача» выберите язык и количество, нажмите
«Собрать архив», дождитесь статуса `ready` или `ready_local` и скачайте архив
по ссылке; повторное скачивание доступно в «Готовых архивах» и не пересобирает
zip. Вкладка «Запросы» ищет по списку, показывает остаток и `in_file` и
добавляет новый запрос. Вкладка «Настройки» сначала показывает diff по
«Проверить», затем применяет его по «Применить» с бэкапом
`config.yaml.bak-<YYYYmmdd-HHMMSS>`.

С чужого IP (например, с мобильного интернета) NPM возвращает `403`:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://content.slaid098.dev/   # 403
```

Локальная проверка без NPM: `http://127.0.0.1:8000/` открывает страницу, но
запросы к `/api/v1/content/*` без токена получают `401`, и страница пишет
«Нет доступа к API». Обхода авторизации нет.

### 12.4 Ротация токена

1. Замените `CONTENT_FACTORY_DELIVERY_TOKEN` в `.env` на linux1.
2. Пересоздайте API, чтобы контейнер прочитал новое значение:
   `sudo env IMAGE_TAG="$(sudo cat .deploy-image-tag)" docker compose up -d api`.
3. Обновите `proxy_set_header Authorization` в custom location NPM и
   сохраните proxy host.
4. Повторите проверки из §12.3; старый токен после шага 2 даёт `401`.

Токен хранится в конфиге NPM на linux2 и в `.env` на linux1. Доступ к
админке NPM и к `/root/dockers/content-factory/.env` должен быть только у
эксплуатации; в браузер, логи API и git токен не попадает.
