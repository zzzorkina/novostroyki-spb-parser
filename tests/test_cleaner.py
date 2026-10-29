from datetime import date

import cleaner


def test_get_nested_and_pick():
    record = {"developer": {"devShortCleanNm": "ДемоСтрой"}, "objAddr": ""}
    assert cleaner.get_nested(record, "developer.devShortCleanNm") == "ДемоСтрой"
    assert cleaner.get_nested(record, "developer.missing") is None
    # пустая строка пропускается, берётся следующий кандидат
    assert cleaner.pick({"objAddr": "", "address": "СПб"}, ["objAddr", "address"]) == "СПб"


def test_to_number():
    assert cleaner.to_number("1 234,5") == 1234.5
    assert cleaner.to_number(42) == 42.0
    assert cleaner.to_number("нет данных") is None
    assert cleaner.to_number(True) is None


def test_parse_date_formats():
    assert cleaner.parse_date("2026-12-31") == date(2026, 12, 31)
    assert cleaner.parse_date("2026-12-31T00:00:00") == date(2026, 12, 31)
    assert cleaner.parse_date("31.12.2026") == date(2026, 12, 31)
    assert cleaner.parse_date("скоро") is None
    assert cleaner.parse_date(None) is None


def test_extract_district_spb():
    assert cleaner.extract_district("г Санкт-Петербург, р-н Приморский, ул. Тестовая") == "Приморский"
    assert cleaner.extract_district("Санкт-Петербург, Московский район, пр. Учебный") == "Московский"
    assert cleaner.extract_district("Санкт-Петербург, Петродворцовый р-н, д. 1") == "Петродворцовый"
    assert cleaner.extract_district("Санкт-Петербург, муниципальный округ Демо") == "Не указан"
    assert cleaner.extract_district(None) == "Не указан"


def test_extract_district_other_region():
    address = "Ленинградская обл, Всеволожский р-н, г Мурино"
    assert cleaner.extract_district(address, region_code="47") == "Всеволожский"


def test_clean_builds_table():
    records = [
        {"objId": 1, "objCommercNm": "ЖК  Демо", "objAddr": "Санкт-Петербург, Невский район",
         "developer": {"devShortCleanNm": "ДемоСтрой"}, "objElemLivingCnt": 100,
         "objSquareLiving": 4000, "objReady100PercDt": "2025-03-31", "objFloorMax": 12},
        {"objId": 1, "objCommercNm": "дубль", "objAddr": "", "developer": {}},   # дубль по ID
        {"objId": 2, "objCommercNm": "ЖК Мини", "objAddr": "Санкт-Петербург",
         "developer": {"devShortCleanNm": "Тест"}, "objElemLivingCnt": 10,
         "objSquareLiving": 50, "objReady100PercDt": "2027-12-31"},
    ]
    df = cleaner.clean(records, today=date(2026, 1, 1))
    assert len(df) == 2                                  # дубль удалён
    first = df.iloc[0]
    assert first["complex"] == "ЖК Демо"                 # лишние пробелы убраны
    assert first["district"] == "Невский"
    assert first["avg_flat_area"] == 40.0
    assert first["ready_year"] == 2025
    assert first["ready_quarter"] == "2025-Q1"
    assert bool(first["deadline_passed"]) is True
    # 50 м² на 10 квартир = 5 м² — нереалистично, считаем ошибкой данных
    assert df.iloc[1]["avg_flat_area"] is None or df.iloc[1]["avg_flat_area"] != df.iloc[1]["avg_flat_area"]


def test_missing_fields():
    records = [{"objId": 1, "objAddr": "СПб"}]
    assert "developer" in cleaner.missing_fields(records, only_key=True)
    assert "obj_id" not in cleaner.missing_fields(records)


def test_real_site_format():
    """Запись в том виде, в каком её отдаёт наш.дом.рф (октябрь 2026)."""
    record = {
        "hobjId": 122958, "objId": 42385,
        "developer": {"devId": 11388, "shortName": "БТК", "fullName": "БТК", "groupName": "Лидер Групп",
                      "orgForm": {"id": 4, "shortForm": "ООО"}, "companyGroup": 5665, "devInn": "7805276607"},
        "rpdRegionCd": 78, "objAddr": "Ленинский проспект, д. 153, (участок 1)",
        "metro": {"id": "15.224", "name": "Московская", "line": "Московско-Петроградская",
                  "time": 20.4, "isWalk": True, "colors": ["#0078C9"]},
        "objCommercNm": "ЙЕС Лидер", "objFloorMax": 26, "objElemLivingCnt": 0,
        "objReady100PercDt": "2027-12-31", "objSquareLiving": 0, "problemFlag": "NONE", "objProblemFlg": 0,
        "residentialBuildings": 0, "buildType": "Нежилое", "objPriceAVG": 310000,
    }
    no_group = {"objId": 73768, "developer": {"devId": 16981, "shortName": "ВЕГА СЗ"},
                "objAddr": "Пригородный", "objElemLivingCnt": 780, "objSquareLiving": 40154.23,
                "residentialBuildings": 1, "buildType": "Жилое", "problemFlag": "NONE"}
    df = cleaner.clean([record, no_group], today=date(2026, 10, 29))
    first, second = df.iloc[0], df.iloc[1]
    assert first["developer"] == "БТК"
    assert first["dev_group"] == "Лидер Групп"
    assert second["dev_group"] == "ВЕГА СЗ"           # группы нет — застройщик сам себе группа
    assert first["metro"] == "Московская"
    assert first["metro_minutes"] == 20.4
    assert bool(first["metro_walk"]) is True
    assert first["price_m2"] == 310000
    assert bool(first["problem"]) is False            # "NONE" — значит, проблем нет
    assert first["build_type"] == "Нежилое"
    assert second["buildings"] == 1
