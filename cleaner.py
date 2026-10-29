"""
Очистка данных: из сырых ответов сайта — в аккуратную таблицу.

Что делаем:
- выбираем нужные поля (у сайта они называются вроде objCommercNm, objAddr);
- приводим типы: числа к числам, даты к датам;
- достаём район из адреса, станцию метро и группу компаний застройщика;
- считаем производные показатели: год сдачи, среднюю площадь квартиры,
  признак «плановый срок уже прошёл».
"""
from __future__ import annotations

import re
from datetime import date

import pandas as pd

# Для каждого нашего поля — список возможных названий в ответе сайта.
# Берётся первое найденное. Точка означает вложенность: developer.devShortCleanNm.
FIELD_MAP = {
    "obj_id":        ["objId", "id"],
    "complex":       ["objCommercNm", "objName", "complexName"],
    "address":       ["objAddr", "address"],
    "developer":     ["developer.shortName", "developer.devShortCleanNm", "developer.devShortNm",
                      "devShortCleanNm", "developer.fullName", "developer"],
    "dev_group":     ["developer.groupName", "developer.developerGroupName", "developerGroupName"],
    "dev_inn":       ["developer.devInn", "devInn"],
    "region_code":   ["rpdRegionCd", "regionCd"],
    "build_type":    ["buildType"],
    "buildings":     ["residentialBuildings"],
    "flats":         ["objElemLivingCnt", "objFlatCnt", "flatsCount"],
    "living_area":   ["objSquareLiving", "objLivingSquare"],
    "floor_min":     ["objFloorMin"],
    "floor_max":     ["objFloorMax"],
    "ready_date":    ["objReady100PercDt", "objReadyDt", "readyDt"],
    "price_m2":      ["objPriceAVG", "objPriceAvg", "priceAvg"],
    "metro":         ["metro", "metroStation", "nearestMetro"],
    "housing_class": ["objLkClassDesc", "objClassDesc"],
    "finishing":     ["objLkFinishTypeDesc", "objFinishTypeDesc"],
    "escrow":        ["objGuarantyEscrowFlg", "escrowFlg"],
    "problem":       ["objProblemFlg", "problemFlag"],
    "lat":           ["latitude", "objLkLatitude", "lat"],
    "lon":           ["longitude", "objLkLongitude", "lon", "lng"],
}

# Подписи колонок для отчёта
COLUMN_TITLES = {
    "obj_id": "ID объекта",
    "complex": "ЖК",
    "address": "Адрес",
    "district": "Район",
    "developer": "Застройщик",
    "dev_group": "Группа компаний",
    "build_type": "Тип объекта",
    "buildings": "Жилых корпусов",
    "dev_inn": "ИНН застройщика",
    "metro": "Ближайшее метро",
    "metro_minutes": "До метро, мин",
    "metro_walk": "До метро пешком",
    "flats": "Квартир",
    "living_area": "Жилая площадь, м²",
    "avg_flat_area": "Средняя площадь квартиры, м²",
    "floor_min": "Этажей (мин.)",
    "floor_max": "Этажей (макс.)",
    "ready_date": "Плановый срок сдачи",
    "ready_year": "Год сдачи",
    "ready_quarter": "Квартал сдачи",
    "deadline_passed": "Плановый срок прошёл",
    "price_m2": "Средняя цена м², ₽",
    "housing_class": "Класс",
    "finishing": "Отделка",
    "escrow": "Эскроу",
    "problem": "Проблемный объект",
    "lat": "Широта",
    "lon": "Долгота",
}

# Районы Санкт-Петербурга: корень названия -> полное название.
# По корню ловим и «Приморский район», и «Приморского р-на».
SPB_DISTRICTS = {
    "Адмиралтейск": "Адмиралтейский", "Василеостровск": "Василеостровский",
    "Выборгск": "Выборгский", "Калининск": "Калининский", "Кировск": "Кировский",
    "Колпинск": "Колпинский", "Красногвардейск": "Красногвардейский",
    "Красносельск": "Красносельский", "Кронштадтск": "Кронштадтский",
    "Курортн": "Курортный", "Московск": "Московский", "Невск": "Невский",
    "Петроградск": "Петроградский", "Петродворцов": "Петродворцовый",
    "Приморск": "Приморский", "Пушкинск": "Пушкинский", "Фрунзенск": "Фрунзенский",
    "Центральн": "Центральный",
}
DISTRICT_PATTERN = re.compile(r"([А-ЯЁ][а-яё-]+)\s+(?:р-н|р-на|район)\b")
DISTRICT_PATTERN_PREFIX = re.compile(r"\b(?:р-н|район)\s+([А-ЯЁ][а-яё-]+)")


# --- Маленькие функции (их удобно проверять тестами) -----------------------------

def get_nested(record: dict, path: str):
    """get_nested({'a': {'b': 1}}, 'a.b') -> 1. Если пути нет — None."""
    value = record
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def pick(record: dict, candidates: list[str]):
    """Возвращает значение первого найденного поля из списка кандидатов."""
    for path in candidates:
        value = get_nested(record, path)
        if value not in (None, ""):
            return value
    return None


def to_number(value):
    """'1 234,5' -> 1234.5; пустые и мусорные значения -> None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def parse_date(value):
    """Понимает '2026-12-31', '2026-12-31T00:00:00', '31.12.2026'. Иначе — None."""
    if not value:
        return None
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return pd.to_datetime(text, format=fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def extract_district(address: str | None, region_code: str = "78") -> str:
    """Находит район в адресе. Для СПб сверяет с официальным списком районов."""
    if not isinstance(address, str) or not address.strip():
        return "Не указан"
    if str(region_code) == "78":
        for stem, name in SPB_DISTRICTS.items():
            if re.search(stem + r"\w*\s+(?:р-н|р-на|район)", address) or \
               re.search(r"(?:р-н|район)\s+" + stem, address):
                return name
        return "Не указан"
    match = DISTRICT_PATTERN.search(address) or DISTRICT_PATTERN_PREFIX.search(address)
    return match.group(1) if match else "Не указан"


# Названия полей, в которых сайт может хранить имя застройщика или станции метро
DEVELOPER_NAME_KEYS = ("devShortCleanNm", "devShortNm", "shortName", "devFullCleanNm",
                       "devFullNm", "fullName", "devName", "orgName", "name")
METRO_NAME_KEYS = ("name", "metroName", "stationName", "nm", "title")


def name_from(value, preferred_keys=()):
    """
    Достаёт название из значения любой формы:
    'ЛСР' -> 'ЛСР'; {'devShortNm': 'ЛСР'} -> 'ЛСР'; [{'name': 'Купчино'}] -> 'Купчино'.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            name = name_from(item, preferred_keys)
            if name:
                return name
        return None
    if isinstance(value, dict):
        for key in preferred_keys:
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
        for key, item in value.items():   # запасной вариант: любое поле с «name»/«nm» в названии
            if isinstance(item, str) and item.strip() and re.search(r"nm|name", key, re.I):
                return item.strip()
        return None
    return str(value)


def walk_from(value):
    """Пешком ли указано время до метро (поле isWalk у станции)."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        for key in ("isWalk", "walk", "byFoot"):
            if key in value:
                return to_flag(value[key])
    return None


def minutes_from(value):
    """Время до метро в минутах из словаря или списка станций (если сайт его отдаёт)."""
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, dict):
        return None
    for key, item in value.items():
        if re.search(r"time|min", key, re.I):
            number = to_number(item)
            if number is not None and 0 < number < 300:
                return number
    return None


def to_flag(value):
    """Флаги сайта бывают 1/0, true/false, 'Да'/'Нет'."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "да", "y", "yes"):
        return True
    if text in ("0", "false", "нет", "n", "no", "none", ""):
        return False
    return True   # любое другое значение флага (например, код проблемы) считаем «да»


# --- Главная функция -------------------------------------------------------------

# Без этих полей отчёт теряет смысл — если их нет, формат сайта, скорее всего, изменился
KEY_FIELDS = ["obj_id", "address", "developer", "flats", "ready_date"]


def missing_fields(records: list[dict], only_key: bool = False) -> list[str]:
    """Какие поля не нашлись ни в одной записи — подсказка, если сайт изменил формат."""
    fields = KEY_FIELDS if only_key else list(FIELD_MAP)
    return [field for field in fields
            if all(pick(r, FIELD_MAP[field]) is None for r in records)]


def clean(records: list[dict], region_code: str = "78", today: date | None = None) -> pd.DataFrame:
    """Превращает список сырых объектов в таблицу pandas."""
    today = today or date.today()
    rows = [{field: pick(r, cands) for field, cands in FIELD_MAP.items()} for r in records]
    df = pd.DataFrame(rows, columns=list(FIELD_MAP))

    for col in ("flats", "living_area", "floor_min", "floor_max", "price_m2", "lat", "lon", "buildings"):
        df[col] = df[col].map(to_number)
    for col in ("escrow", "problem"):
        df[col] = df[col].map(to_flag)
    raw_developer = df["developer"].copy()
    df["developer"] = df["developer"].map(lambda v: name_from(v, DEVELOPER_NAME_KEYS))
    df["metro_minutes"] = df["metro"].map(minutes_from)
    df["metro_walk"] = df["metro"].map(walk_from)
    df["metro"] = df["metro"].map(lambda v: name_from(v, METRO_NAME_KEYS))
    for col in ("complex", "developer", "address", "housing_class", "finishing", "dev_group", "metro",
                "build_type"):
        df[col] = df[col].map(lambda v: re.sub(r"\s+", " ", str(v)).strip() if isinstance(v, str) else None)

    # Даты храним в типе datetime64: так с ними удобно считать (год, квартал, сравнение)
    ready = pd.to_datetime(df["ready_date"].map(parse_date), errors="coerce")
    has_date = ready.notna()
    df["ready_date"] = ready
    df["ready_year"] = ready.dt.year.astype("Int64")
    df["ready_quarter"] = (ready.dt.year.astype("Int64").astype(str) + "-Q"
                           + ready.dt.quarter.astype("Int64").astype(str)).where(has_date, None)
    df["deadline_passed"] = (ready < pd.Timestamp(today)).astype(object).where(has_date, None)
    df["district"] = df["address"].map(lambda a: extract_district(a, region_code))

    df["avg_flat_area"] = (df["living_area"] / df["flats"]).where(df["flats"] > 0).round(1)
    # Нереалистичные значения считаем ошибкой данных
    df.loc[(df["avg_flat_area"] < 15) | (df["avg_flat_area"] > 300), "avg_flat_area"] = None
    df.loc[(df["price_m2"] <= 0), "price_m2"] = None

    # Пустой застройщик заменяем на «Не указан», чтобы сводные таблицы не теряли строки
    if df["developer"].isna().all() and len(df):
        sample = next((v for v in raw_developer if v is not None), None)
        print(f"  ВНИМАНИЕ: не удалось прочитать название застройщика. Пример значения: {str(sample)[:300]}")
    df["developer"] = df["developer"].fillna("Не указан")
    # Если группа компаний на сайте не указана, застройщик считается самостоятельной группой
    df["dev_group"] = df["dev_group"].fillna(df["developer"])

    df = df.drop_duplicates(subset="obj_id") if df["obj_id"].notna().any() else df
    order = ["obj_id", "complex", "address", "district", "metro", "metro_minutes", "metro_walk",
             "dev_group", "developer", "dev_inn", "build_type", "buildings",
             "flats", "living_area", "avg_flat_area", "floor_min", "floor_max",
             "ready_date", "ready_year", "ready_quarter", "deadline_passed",
             "price_m2", "housing_class", "finishing", "escrow", "problem", "lat", "lon"]
    return df[order].reset_index(drop=True)
