# net.py
"""
Лёгкий HTTP-клиент с requests.Session:
- один общий пул соединений (keep-alive)
- автоповторы на сетевые/серверные ошибки
- без системных прокси
"""
import os
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config as C

# полностью игнорируем системные прокси
for k in ["HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","http_proxy","https_proxy","all_proxy","no_proxy"]:
    os.environ.pop(k, None)

_session = requests.Session()
# адаптеры с пулами и ретраями
retries = Retry(
    total=getattr(C, "HTTP_RETRIES", 2),
    backoff_factor=getattr(C, "HTTP_BACKOFF", 0.3),
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET", "POST"])
)
adapter = HTTPAdapter(
    pool_connections=getattr(C, "HTTP_POOL_SIZE", 30),
    pool_maxsize=getattr(C, "HTTP_POOL_SIZE", 30),
    max_retries=retries,
)
_session.mount("https://", adapter)
_session.mount("http://", adapter)
_session.trust_env = False  # не использовать прокси/переменные окружения
_session.headers.update({"Connection": "keep-alive"})

TG_REQ_TIMEOUT = getattr(C, "TG_REQ_TIMEOUT", 5.0)

def tg_get(token: str, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    r = _session.get(url, params=params or {}, timeout=timeout or TG_REQ_TIMEOUT)
    r.raise_for_status()
    return r.json()

def tg_post(token: str, method: str, data: dict | None = None, timeout: float | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    r = _session.post(url, data=data or {}, timeout=timeout or TG_REQ_TIMEOUT)
    r.raise_for_status()
    return r.json()
