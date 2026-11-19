"""Определение прокси: явная настройка или профиль BitBrowser."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from content_factory.exceptions.errors import ConfigError
from content_factory.schemas.supplier.settings import SupplierConfig

_JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}
_SCHEMES = {
    "http": "http",
    "https": "https",
    "socks5": "socks5",
    "socks5h": "socks5h",
    "socks": "socks5",
}


def _profile_proxy(profile: dict[str, object], profile_id: str) -> str:
    proxy_type = str(profile.get("proxyType") or "").strip().lower()
    if proxy_type in {"", "noproxy", "no_proxy", "none"}:
        raise ConfigError(
            f"у профиля BitBrowser {profile_id} не настроен прокси: задайте SUPPLIER_PROXY вручную"
        )
    scheme = _SCHEMES.get(proxy_type)
    if scheme is None:
        raise ConfigError(f"неподдерживаемый тип прокси BitBrowser: {proxy_type}")

    host = str(profile.get("host") or "").strip()
    port = profile.get("port")
    if not host or port is None or str(port).strip() == "":
        raise ConfigError(f"у профиля BitBrowser {profile_id} прокси без хоста или порта")

    username = urllib.parse.quote(str(profile.get("proxyUserName") or ""), safe="")
    password = urllib.parse.quote(str(profile.get("proxyPassword") or ""), safe="")
    credentials = ""
    if username or password:
        credentials = f"{username}:{password}@"
    return f"{scheme}://{credentials}{host}:{port}"


def fetch_bitbrowser_proxy(base_url: str, profile_id: str, timeout: int = 20) -> str:
    """Прочитать прокси профиля из локального API BitBrowser.

    Args:
        base_url: Корень HTTP API BitBrowser, например ``http://127.0.0.1:54529``.
        profile_id: Идентификатор профиля BitBrowser.
        timeout: Таймаут запроса в секундах.

    Returns:
        URL прокси с закодированными учётными данными.

    Raises:
        ConfigError: API недоступен, профиль не найден или у него нет прокси.
    """
    url = f"{base_url.rstrip('/')}/browser/list"
    payload = json.dumps({"page": 0, "pageSize": 500}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST", headers=_JSON_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as ex:
        raise ConfigError(f"BitBrowser API вернул HTTP {ex.code}") from ex
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        raise ConfigError(f"не удалось подключиться к BitBrowser API {base_url}") from ex

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as ex:
        raise ConfigError("BitBrowser API вернул некорректный JSON") from ex
    if not isinstance(data, dict):
        raise ConfigError("BitBrowser API вернул неожиданный ответ")

    body = data.get("data")
    profiles = body.get("list") if isinstance(body, dict) else None
    if not isinstance(profiles, list):
        raise ConfigError("BitBrowser API не вернул список профилей")

    profile = next(
        (
            item
            for item in profiles
            if isinstance(item, dict) and str(item.get("id") or "") == profile_id
        ),
        None,
    )
    if profile is None:
        raise ConfigError(f"профиль BitBrowser {profile_id} не найден")
    return _profile_proxy(profile, profile_id)


def resolve_proxy(cfg: SupplierConfig) -> str:
    """Вернуть прокси для HTTP-этапов.

    Приоритет: ``SUPPLIER_PROXY``, затем прокси профиля ``SUPPLIER_PROFILE_ID``.

    Raises:
        ConfigError: Нет ни явного прокси, ни пригодного прокси профиля.
    """
    if cfg.proxy:
        return cfg.proxy
    if not cfg.profile_id:
        raise ConfigError(
            "SUPPLIER_PROXY не задан, а SUPPLIER_PROFILE_ID пуст: "
            "укажите прокси или профиль BitBrowser"
        )
    return fetch_bitbrowser_proxy(cfg.bitbrowser_url, cfg.profile_id)
