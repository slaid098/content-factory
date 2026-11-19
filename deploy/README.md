# Деплой content-factory

Прод-контур — Linux 1: Docker Compose поднимает API и Prefect-воркер, том
`app_data` живёт на хосте, партнёр забирает готовые файлы по SFTP, конфиг и
список запросов уходят в ночной бэкап.

## 1. Требования

- Docker Engine 24+ и Docker Compose 2.24+ (`env_file.required`,
  `host-gateway`).
- Prefect 3 API, доступный воркеру (свой сервер или Prefect Cloud).
- BitBrowser и MCP-сервер, доступные из контейнера.
- OpenSSH 9.0+ на хосте: нужны `sftp-server -P`/`-p` и запрос `copy-data`,
  который обязательно попадает в запрет. Проверка: `ssh -V` и
  `sftp-server -Q requests` (на Debian/Ubuntu бинарник лежит в
  `/usr/lib/openssh/sftp-server`).

Репозиторий и `app_data` удобно держать в `/opt/content-factory`; дальше
используется этот путь.

`host.docker.internal` указывает на шлюз Docker. Сервисы на хосте должны
слушать этот интерфейс (например, `prefect server start --host 0.0.0.0`), а не
только `127.0.0.1`; доступ снаружи закройте firewall. Если Prefect или
BitBrowser уже живут в контейнере той же сети, используйте его имя вместо
`host.docker.internal`.

## 2. Переменные окружения

Секретов в репозитории нет. Compose читает `.env` из корня проекта: значения
подставляются в `compose.yaml`, а сервис `worker` получает файл целиком через
`env_file`. Поэтому тонкие настройки `SUPPLIER_*` можно менять в `.env`, не
редактируя compose.

| Переменная | Обязательно | По умолчанию | Назначение |
|---|---|---|---|
| `PREFECT_API_URL` | да | `http://host.docker.internal:4200/api` | API Prefect 3 |
| `PREFECT_API_KEY` | для Prefect Cloud | — | ключ API; для локального сервера не нужен |
| `TELEGRAM_BOT_TOKEN` | нет | — | токен бота для алертов; без него алерты выключены |
| `TELEGRAM_CHAT_ID` | нет | — | чат алертов |
| `SUPPLIER_MCP_URL` | да | `http://host.docker.internal:8765` | MCP-сервер BitBrowser |
| `SUPPLIER_BITBROWSER_URL` | да | `http://host.docker.internal:54529` | API BitBrowser |
| `SUPPLIER_PROFILE_ID` | да | — | профиль BitBrowser для TikTok |
| `SUPPLIER_PROXY` | нет | — | прокси движка: `http://`, `https://`, `socks5://`, `socks5h://` |
| `CONTENT_FACTORY_LOG_LEVEL` | нет | `INFO` | уровень loguru в API и воркере |

`CONTENT_FACTORY_DATA_DIR=/app/app_data` жёстко задан в `compose.yaml`: это
путь внутри контейнера, а на хосте тому соответствует `./app_data`.

Пример `.env`:

```bash
cat > /opt/content-factory/.env <<'EOF'
PREFECT_API_URL=http://host.docker.internal:4200/api
PREFECT_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
SUPPLIER_MCP_URL=http://host.docker.internal:8765
SUPPLIER_BITBROWSER_URL=http://host.docker.internal:54529
# id профиля BitBrowser обязателен
SUPPLIER_PROFILE_ID=
SUPPLIER_PROXY=
EOF
chmod 600 /opt/content-factory/.env
```

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

После изменения `.env` пересоздайте воркер:

```bash
docker compose up -d worker
```

## 3. Запуск

```bash
cd /opt/content-factory
docker compose config -q
docker compose up -d --build
docker compose ps
curl -f http://127.0.0.1:8000/api/v1/stock
```

- API слушает `0.0.0.0:8000` внутри контейнера, наружу порт опубликован
  только как `127.0.0.1:8000:8000`.
- Одноразовый сервис `init` создаёт `app_data/{config,db,queries}`, выставляет
  владельца `10001` и не трогает существующий `content/`.
- Том `./app_data` переживает `docker compose down` и пересоздание
  контейнеров.
- Healthcheck API: `curl -f http://127.0.0.1:8000/api/v1/stock`.

Снаружи API доступен только через SSH-туннель:

```bash
ssh -L 8000:127.0.0.1:8000 user@linux-1
curl -f http://127.0.0.1:8000/api/v1/stock
```

## 4. Prefect

Один раз создайте work pool и зарегистрируйте deployment:

```bash
cd /opt/content-factory
docker compose exec worker prefect work-pool create content-factory --type process
docker compose exec worker prefect deploy --all
```

Если pool уже существует, первая команда завершится ошибкой — это нормально,
переходите ко второй.

Проверка:

```bash
docker compose exec worker prefect deployment ls
docker compose logs -f worker
```

`prefect.yaml` задаёт cron `*/15 * * * *`, конкурентность 1 и pool
`content-factory`. Шаг `set_working_directory` указывает воркеру на `/app`, где
в образе лежат `src/` и `prefect.yaml`; поэтому deployment не зависит от
текущего каталога процесса. Cron — это тик планировщика; решение о
производстве принимает сам flow по `config.run.interval`, поэтому холостые тики
выходят сразу.

`prefect.yaml` смонтирован в воркер как `/app/prefect.yaml`, поэтому после
его изменения достаточно выполнить
`docker compose exec worker prefect deploy --all` — пересборка не нужна.
После обновления кода выполните `docker compose up -d --build`: entrypoint уже
зарегистрирован, а новый код лежит в образе воркера.

## 5. SFTP для партнёра

Партнёр видит только `app_data/content` и может читать файлы и удалять их.
Запись в каталог нужна для удаления, поэтому `put` запрещается на уровне
SFTP-запросов, а не POSIX-прав.

### 5.1 Пользователь

```bash
sudo groupadd --system content-partner
sudo useradd --system --gid content-partner \
  --home-dir /var/lib/content-partner --shell /usr/sbin/nologin content-partner
sudo install -d -m 700 -o content-partner -g content-partner /var/lib/content-partner
sudo install -d -m 700 -o content-partner -g content-partner /var/lib/content-partner/.ssh
sudo install -m 600 -o content-partner -g content-partner \
  partner-key.pub /var/lib/content-partner/.ssh/authorized_keys
```

### 5.2 Права на каталоги

`ChrootDirectory` должен принадлежать root и не быть доступен на запись
группе и остальным. Каталоги языков остаются во владении `app` (uid 10001),
чтобы поставщик мог писать файлы; группе партнёра даётся чтение и удаление.

```bash
sudo chown root:root /opt/content-factory/app_data/content
sudo chmod 0755 /opt/content-factory/app_data/content

sudo install -d -o 10001 -g content-partner -m 2770 \
  /opt/content-factory/app_data/content/ru
```

Если включаете ещё язык, создайте для него такой же каталог. Выключенные
языки каталогов не получают и партнёру не видны.

Файлы поставщик создаёт с umask `022`, то есть `0644`: владелец `app` пишет,
группа `content-partner` читает. Удаление разрешено правом записи на каталог
`ru`, а не на файл. Не делайте файлы group-writable: иначе `put` сможет
открыть существующий файл с `TRUNC` и обнулить его, даже несмотря на запрет
запроса `write`.

### 5.3 sshd

Создайте `/etc/ssh/sshd_config.d/60-content-partner.conf`:

```sshconfig
Match User content-partner
    ChrootDirectory /opt/content-factory/app_data/content
    ForceCommand internal-sftp -d / -P write,setstat,fsetstat,mkdir,rmdir,rename,posix-rename,symlink,hardlink,lsetstat,fsync,copy-data
    AllowTcpForwarding no
    AllowAgentForwarding no
    PermitTunnel no
    X11Forwarding no
    PasswordAuthentication no
```

`-d /` задаёт стартовый каталог внутри chroot: домашний каталог пользователя
`/var/lib/content-partner` находится снаружи. `-P` запрещает изменяющие
запросы, включая `write` и `copy-data`; `open` остаётся разрешён, иначе не
работает чтение.

`Match` должен идти после общих настроек. Отдельный файл в
`sshd_config.d/` подключается директивой `Include` вне блоков `Match`, так
что это требование выполняется. Проверьте и перезагрузите:

```bash
sudo sshd -t
sudo systemctl reload ssh    # на Debian/Ubuntu; на других системах — sshd
```

### 5.4 Проверка

```bash
sftp content-partner@<host>
sftp> ls                 # виден ru/
sftp> get ru/<sha256>.mp4
sftp> rm ru/<sha256>.mp4  # удаление проходит
sftp> put /etc/hostname ru/test.mp4  # должно завершиться ошибкой
```

OpenSSH не различает «создать» и «удалить» на уровне POSIX: и то и другое
требует write на каталог. Поэтому запись запрещена списком `-P`: сервер
отклоняет `write`, `setstat`, `rename`, `mkdir` и остальные изменяющие
запросы. Если после неудачного `put` клиент оставил пустой файл, удалите его
через `rm`. Существующие `*.mp4` при этом не обнуляются: они принадлежат
`app` и не доступны партнёру на запись.

## 6. Бэкап

`scripts/backup_app_data.sh` архивирует только `app_data/config` и
`app_data/queries`; `content/` и `db/` не трогаются. Если `queries/` ещё нет,
скрипт печатает предупреждение и завершается успешно.

```bash
sudo install -d -m 700 -o root -g root /var/backups/content-factory
sudo crontab -e
```

```cron
15 3 * * * BACKUP_DIR=/var/backups/content-factory /opt/content-factory/scripts/backup_app_data.sh >> /var/log/content-factory-backup.log 2>&1
```

Скрипт должен быть исполняемым; в git уже стоит нужный бит. Проверка:

```bash
sudo -u root env BACKUP_DIR=/var/backups/content-factory \
  /opt/content-factory/scripts/backup_app_data.sh
tar -tzf /var/backups/content-factory/content-factory-config-queries-*.tar.gz
```

Восстановление: остановите `worker`, распакуйте архив в `app_data/`, затем
запустите `docker compose up -d worker` — `init` выставит владельца файлов
`10001`.

## 7. Обновление

```bash
cd /opt/content-factory
git pull
docker compose up -d --build
docker compose exec worker prefect deploy --all   # только если менялся prefect.yaml
```

`app_data` и состояние цикла в `app_data/db/db.sqlite3` при этом сохраняются.
