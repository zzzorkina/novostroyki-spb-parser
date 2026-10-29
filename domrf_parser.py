"""
Сбор данных из каталога новостроек наш.дом.рф.

Как это работает:
1. Страница каталога в браузере сама подгружает данные в формате JSON
   с адреса API_URL (это видно во вкладке Network инструментов разработчика).
2. Мы обращаемся к тому же адресу напрямую и забираем данные постранично
   (сайт отдаёт по 50 объектов), пока объекты не закончатся.
3. Между запросами делаем паузу, при ошибках повторяем запрос,
   а каждый ответ сохраняем как есть — чтобы не скачивать заново.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from urllib import robotparser

import requests

import config


class NotJsonResponse(Exception):
    """Сайт вернул не JSON (например, страницу защиты от ботов)."""


# --- Вспомогательные функции ---------------------------------------------------

def make_session() -> requests.Session:
    """Одна сессия на весь сбор: переиспользует соединение и хранит заголовки."""
    session = requests.Session()
    session.headers.update(config.HEADERS)
    return session


def robots_allows(url: str, user_agent: str = "*") -> bool:
    """Проверяет robots.txt сайта. Если файл недоступен — считаем, что можно."""
    rp = robotparser.RobotFileParser()
    rp.set_url(config.BASE_URL + "/robots.txt")
    try:
        rp.read()
    except Exception:
        return True
    return rp.can_fetch(user_agent, url)


def polite_pause() -> None:
    """Случайная пауза, чтобы не нагружать сайт."""
    time.sleep(random.uniform(*config.PAUSE_SECONDS))


def save_json(data, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def extract_items(payload) -> tuple[list[dict], int | None]:
    """
    Достаёт из ответа список объектов и их общее число.

    Структуру ответа сайт может поменять, поэтому проверяем несколько
    распространённых вариантов: {"data": {"list": [...], "total": N}},
    {"items": [...]}, просто [...] и т. д.
    """
    if isinstance(payload, list):
        return payload, None

    containers = [payload]
    if isinstance(payload.get("data"), dict):
        containers.insert(0, payload["data"])
    elif isinstance(payload.get("data"), list):
        return payload["data"], payload.get("total")

    for box in containers:
        for key in ("list", "items", "content", "objects", "result"):
            if isinstance(box.get(key), list):
                total = None
                for tkey in ("total", "totalCount", "totalElements", "count"):
                    if isinstance(box.get(tkey), int):
                        total = box[tkey]
                        break
                return box[key], total
    return [], None


def region_matches(item: dict, region_code: str) -> bool:
    """
    Относится ли объект к нужному региону.

    Смотрим все поля, в названии которых есть region/subj/addr:
    код региона совпадает (78) или название региона встречается в адресе.
    """
    code = str(region_code).strip()
    name = config.REGIONS.get(code, "").lower()
    for key, value in item.items():
        if value is None or isinstance(value, (dict, list)):
            continue
        k = key.lower()
        text = str(value).strip()
        if ("region" in k or "subj" in k) and text in (code, code.zfill(2)):
            return True
        if name and ("addr" in k or "region" in k or "subj" in k) and name in text.lower():
            return True
    return False


# --- Запрос одной страницы -----------------------------------------------------

def fetch_page(session: requests.Session, offset: int, extra_params: dict | None = None) -> dict:
    """Загружает одну страницу каталога. При временных ошибках повторяет запрос."""
    params = {"offset": offset, "limit": config.PAGE_SIZE, **config.BASE_PARAMS}
    if extra_params:
        params.update(extra_params)

    last_error = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            response = session.get(config.API_URL, params=params, timeout=config.TIMEOUT)
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {response.status_code}")
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                debug_path = config.RAW_DIR / "debug_response.html"
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                debug_path.write_text(response.text, encoding="utf-8")
                raise NotJsonResponse(
                    "Сайт вернул не JSON, а обычную страницу (возможно, защиту от ботов). "
                    f"Ответ сохранён в {debug_path}"
                )
        except NotJsonResponse:
            raise
        except requests.RequestException as error:
            last_error = error
            wait = 2 ** attempt + random.random()
            print(f"  ! ошибка запроса ({error}), попытка {attempt}/{config.MAX_RETRIES}, жду {wait:.0f} с")
            time.sleep(wait)
    raise ConnectionError(f"Не удалось загрузить страницу offset={offset}: {last_error}")


# --- Сбор всего каталога -------------------------------------------------------

def _paginate(session, extra_params, max_pages, label) -> list[dict]:
    """Проходит по страницам, пока объекты не закончатся."""
    items_all: list[dict] = []
    offset = 0
    page = 0
    total = None
    while page < min(max_pages or config.MAX_PAGES_SAFETY, config.MAX_PAGES_SAFETY):
        payload = fetch_page(session, offset, extra_params)
        save_json(payload, config.RAW_DIR / f"{label}_page_{page:03d}.json")
        items, page_total = extract_items(payload)
        total = page_total or total
        if not items:
            break
        items_all.extend(items)
        page += 1
        # Сдвигаемся на столько, сколько реально пришло: сайт может отдавать
        # меньше, чем мы просим (например, 50 вместо 100)
        offset += len(items)
        progress = f"{len(items_all)} из {total}" if total else f"{len(items_all)}"
        print(f"  страница {page}: получено {len(items)}, всего {progress}")
        if total is not None and len(items_all) >= total:
            break
        if total is None and len(items) < config.PAGE_SIZE:
            break
        polite_pause()
    return items_all


def collect(region_code: str = config.DEFAULT_REGION, max_pages: int | None = None) -> list[dict]:
    """
    Собирает все строящиеся объекты региона.

    Сначала пробуем фильтр по региону на стороне сайта. Если сайт его
    проигнорировал (на первой странице чужие регионы), качаем весь каталог
    и фильтруем сами — это дольше, но надёжно.
    """
    if not robots_allows(config.API_URL):
        raise PermissionError("robots.txt сайта запрещает автоматический сбор с этого адреса — останавливаюсь.")

    session = make_session()

    # Пробуем несколько форматов фильтра по региону: какой сайт поймёт — тем и пользуемся
    region_param = None
    print("Проверяю фильтр по региону...")
    for name, template in config.REGION_PARAM_CANDIDATES:
        params = {name: template.format(code=region_code)}
        first = fetch_page(session, 0, params)
        first_items, _ = extract_items(first)
        if not first_items:
            save_json(first, config.RAW_DIR / "debug_first_page.json")
            raise ValueError(
                "В ответе сайта не нашлось списка объектов. Ответ сохранён в "
                f"{config.RAW_DIR / 'debug_first_page.json'} — по нему можно поправить парсер."
            )
        share = sum(region_matches(i, region_code) for i in first_items) / len(first_items)
        polite_pause()
        if share >= 0.9:
            region_param = params
            print(f"Фильтр {name}={params[name]} работает ({share:.0%} объектов из нужного региона).")
            break

    if region_param:
        print("Собираю данные...")
        items = _paginate(session, region_param, max_pages, label=f"region_{region_code}")
    else:
        print("Сайт не применил фильтр по региону — скачиваю весь каталог и фильтрую сам. "
              "Это займёт около 10 минут.")
        items = _paginate(session, None, max_pages, label="all")

    items = [i for i in items if region_matches(i, region_code)]
    print(f"Готово: {len(items)} объектов в регионе {config.REGIONS.get(region_code, region_code)}.")
    save_json(items, config.RAW_DIR / f"objects_{region_code}.json")
    return items
