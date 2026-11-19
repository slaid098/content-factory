# Деплой content-factory

Прод-контур — Linux 1: Docker Compose поднимает API и Prefect-воркер, том
`app_data` живёт на хосте, партнёр забирает готовые файлы по SFTP, конфиг и
список запросов уходят в ночной бэкап.

## 1. Требования

- Docker Engine 24+ и Docker Compose 2.24+ (`env_file.required`,
  `host-gateway`).
- Prefect 3 API, доступный воркеру (свой сервер или Prefect Cloud).
- BitBrowser и MCP-сервер, доступные из контейнера.
- OpenSSH 9.0+ на хосте: нужны `sftp-server -P`/`-p` и запрос `copy-data`.
  Проверка: `ssh -V` и `sftp-server -Q requests` (на Debian/Ubuntu бинарник
  лежит в `/usr/lib/openssh/sftp-server`).
- `sudo`, `git`, `curl`.

Репозиторий и `app_data` держим в `/opt/content-factory`; дальше используется
этот путь.

Сеть Compose зафиксирована в `compose.yaml`: `172.28.0.0/24`, шлюз
`172.28.0.1`. Сервисы на хосте должны слушать этот адрес, а не `0.0.0.0`:
из контейнера `host.docker.internal` указывает именно на шлюз. Если подсеть
занята, поменяйте её в `compose.yaml` и адреса в этом runbook.

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
путь внутри контейнера, а на хосте ему соответствует `./app_data`.

Каталог репозитория принадлежит root (требование sshd, см. §3), поэтому
`.env` создаём так:

```bash
test -f /opt/content-factory/.env || \
  sudo install -m 600 -o "$(id -u)" -g "$(id -g)" /dev/null /opt/content-factory/.env
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
```

Файл уже принадлежит деплой-пользователю, поэтому `cat >` работает без
`sudo`. У `SUPPLIER_*` с таймингами и лимитами есть значения по умолчанию в
коде. Полный список:

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

## 3. Пользователь и каталоги

### 3.1 Пользователь партнёра

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
он запрещает PTY и пересылки на уровне ключа.

### 3.2 Путь chroot

sshd требует, чтобы **каждый** компонент пути `ChrootDirectory` принадлежал
root и не был доступен на запись группе и остальным. Поэтому корень
репозитория, `app_data` и `app_data/content` — `root:root 0755`:

```bash
sudo chown root:root /opt/content-factory
sudo chmod 0755 /opt/content-factory
sudo install -d -o root -g root -m 0755 \
  /opt/content-factory/app_data \
  /opt/content-factory/app_data/content
namei -l /opt/content-factory/app_data/content
```

Последняя команда должна показать `root root` и `drwxr-xr-x` на всех
компонентах.

`app_data/config`, `app_data/db` и `app_data/queries` принадлежат `10001`
(пользователь `app` в контейнере) и закрыты от остальных (`0750`); это делает
одноразовый сервис `init` при каждом `docker compose up`.

### 3.3 Каталоги языков

Приложение не может писать в root-овый `content`, поэтому каталоги активных
языков создаются заранее:

```bash
sudo install -d -o 10001 -g content-partner -m 2770 \
  /opt/content-factory/app_data/content/ru
```

`setgid` (`2770`) заставляет новые файлы наследовать группу
`content-partner`, поэтому партнёр читает их. Файлы поставщик создаёт с umask
`022`, то есть `0644`: группа получает только чтение. Не делайте файлы
group-writable: `-P write` запрещает SFTP-запрос `write`, но `open` с
`O_TRUNC` остаётся доступен, и такой файл можно обнулить. Проверка:

```bash
sudo find /opt/content-factory/app_data/content -perm /022 -print
```

Вывод должен быть пустым.

Если включаете ещё язык, создайте для него такой же каталог и добавьте его в
`config.yaml`. Выключенные языки каталогов не получают и партнёру не видны.

## 4. sshd

Создайте `/etc/ssh/sshd_config.d/60-content-partner.conf`:

```sshconfig
Match User content-partner
    ChrootDirectory /opt/content-factory/app_data/content
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

### 4.1 Проверка

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

## 5. Запуск

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
  им владельца `10001` и фиксирует `root:root 0755` на `app_data` и
  `content`. Каталог `ru` он не трогает.
- Том `./app_data` переживает `docker compose down` и пересоздание
  контейнеров.
- Healthcheck API: `curl -f http://127.0.0.1:8000/api/v1/stock`.

Снаружи API доступен только через SSH-туннель:

```bash
ssh -L 8000:127.0.0.1:8000 user@linux-1
curl -f http://127.0.0.1:8000/api/v1/stock
```

## 6. Prefect

Prefect на хосте должен слушать шлюз сети Compose (`172.28.0.1`), а не
`0.0.0.0`:

```bash
prefect server start --host 172.28.0.1
```

BitBrowser и MCP-сервер тоже привяжите к `172.28.0.1`. Если приложение не
умеет выбирать адрес, закройте порты firewall'ом и разрешите только подсеть
`172.28.0.0/24`:

```bash
sudo ufw allow OpenSSH
sudo ufw allow from 172.28.0.0/24 to any port 4200 proto tcp
sudo ufw allow from 172.28.0.0/24 to any port 54529 proto tcp
sudo ufw allow from 172.28.0.0/24 to any port 8765 proto tcp
sudo ufw default deny incoming
```

`PREFECT_API_URL` по умолчанию `http://host.docker.internal:4200/api`; из
контейнера это имя резолвится в шлюз `172.28.0.1`.

Создайте pool и зарегистрируйте deployment. `docker compose run` не зависит
от перезапусков воркера:

```bash
cd /opt/content-factory
docker compose run --rm --no-deps worker \
  prefect work-pool create content-factory --type process
docker compose run --rm --no-deps worker prefect deploy --all
docker compose up -d worker
```

Если pool уже существует, первая команда завершится ошибкой — это нормально,
переходите ко второй.

Проверка:

```bash
docker compose run --rm --no-deps worker prefect deployment ls
docker compose logs -f worker
```

`prefect.yaml` задаёт cron `*/15 * * * *`, конкурентность 1 и pool
`content-factory`. Шаг `set_working_directory` указывает воркеру на `/app`,
где в образе лежат `src/` и `prefect.yaml`; поэтому deployment не зависит от
текущего каталога процесса. Cron — это тик планировщика; решение о
производстве принимает сам flow по `config.run.interval`, поэтому холостые тики
выходят сразу.

`prefect.yaml` вшит в образ. После его изменения пересоберите воркер:

```bash
docker compose up -d --build worker
docker compose run --rm --no-deps worker prefect deploy --all
```

## 7. Бэкап

`scripts/backup_app_data.sh` архивирует только `app_data/config` и
`app_data/queries`; `content/` и `db/` не трогаются. Если каталогов нет,
скрипт печатает предупреждение и завершается успешно.

Cron работает от root, поэтому установите root-овую копию скрипта: изменение
файла в git-чекауте не должно давать выполнение от root.

```bash
sudo install -d -m 700 -o root -g root /var/backups/content-factory
sudo install -o root -g root -m 0755 \
  /opt/content-factory/scripts/backup_app_data.sh \
  /usr/local/sbin/content-factory-backup
sudo crontab -e
```

```cron
15 3 * * * APP_DATA_DIR=/opt/content-factory/app_data BACKUP_DIR=/var/backups/content-factory /usr/local/sbin/content-factory-backup >> /var/log/content-factory-backup.log 2>&1
```

`APP_DATA_DIR` обязателен: установленная копия не может вычислить корень
репозитория по своему пути.

Проверка:

```bash
sudo env APP_DATA_DIR=/opt/content-factory/app_data \
  BACKUP_DIR=/var/backups/content-factory \
  /usr/local/sbin/content-factory-backup
sudo tar -tzf /var/backups/content-factory/content-factory-config-queries-*.tar.gz
```

Восстановление: остановите `worker`, распакуйте архив в `app_data/`, затем
запустите `docker compose up -d worker` — `init` выставит владельца файлов
`10001`.

## 8. Обновление

```bash
cd /opt/content-factory
sudo git pull
docker compose up -d --build
docker compose run --rm --no-deps worker prefect deploy --all   # если менялся prefect.yaml
sudo install -o root -g root -m 0755 scripts/backup_app_data.sh \
  /usr/local/sbin/content-factory-backup                        # если менялся скрипт
```

`sudo git pull` нужен потому, что корень репозитория принадлежит root.
`app_data` и состояние цикла в `app_data/db/db.sqlite3` при этом сохраняются.
