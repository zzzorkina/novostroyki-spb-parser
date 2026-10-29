"""
Точка входа. Примеры запуска:

    python main.py                  # собрать данные по Санкт-Петербургу и построить отчёт
    python main.py --region 47      # то же для Ленинградской области
    python main.py --demo           # проверить код на демо-данных, без интернета
    python main.py --from-raw data/raw/objects_78.json   # пересобрать отчёт без повторного парсинга
    python main.py --max-pages 2    # быстрый пробный запуск: только 2 страницы
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

import analysis
import cleaner
import config
import domrf_parser


def main(argv=None) -> int:
    args = argparse.ArgumentParser(description="Парсер новостроек с наш.дом.рф")
    args.add_argument("--region", default=config.DEFAULT_REGION,
                      help="код региона: 78 — СПб, 47 — Ленобласть, 77 — Москва")
    args.add_argument("--demo", action="store_true", help="запуск на демо-данных")
    args.add_argument("--from-raw", help="путь к сохранённому JSON со списком объектов")
    args.add_argument("--max-pages", type=int, help="ограничить число страниц (для пробы)")
    opts = args.parse_args(argv)

    region = str(opts.region)
    region_name = {"78": "Санкт-Петербург", "47": "Ленинградская область",
                   "77": "Москва", "50": "Московская область"}.get(region, f"регион {region}")
    mode = "ДЕМО" if opts.demo else "реальные данные"

    # 1. Получаем сырые данные
    print(f"== 1/3 Сбор данных ({region_name})")
    if opts.demo:
        payload = domrf_parser.load_json(config.DEMO_FILE)
        records, _ = domrf_parser.extract_items(payload)
        print(f"Загружено демо-объектов: {len(records)}")
    elif opts.from_raw:
        payload = domrf_parser.load_json(opts.from_raw)
        records, _ = domrf_parser.extract_items(payload) if isinstance(payload, dict) else (payload, None)
        print(f"Загружено из файла: {len(records)}")
    else:
        try:
            records = domrf_parser.collect(region, max_pages=opts.max_pages)
        except Exception as error:  # понятное сообщение вместо длинной ошибки
            print(f"\nСбор остановлен: {error}")
            print("Если ошибка непонятна — пришлите этот текст и файлы из папки data/raw.")
            return 1

    if not records:
        print("Объектов не найдено — отчёт не строю.")
        return 1

    # 2. Чистим
    print("== 2/3 Очистка")
    key_missing = cleaner.missing_fields(records, only_key=True)
    if key_missing:
        print(f"  ВНИМАНИЕ: не нашлись ключевые поля: {', '.join(key_missing)}. Похоже, сайт изменил формат.")
        print(f"  Поля первого объекта: {', '.join(list(records[0])[:40])}")
        print("  Пришлите этот вывод — поправим названия полей в cleaner.py (FIELD_MAP).")
    optional_missing = [f for f in cleaner.missing_fields(records) if f not in key_missing]
    if optional_missing:
        print(f"  Нет данных для необязательных полей: {', '.join(optional_missing)} — пропускаю их.")
    df = cleaner.clean(records, region_code=region)
    print(f"  Объектов в таблице: {len(df)}, жилых комплексов: {df['complex'].nunique()}, "
          f"групп компаний: {df['dev_group'].nunique()}.")

    # 3. Анализируем
    print("== 3/3 Отчёт")
    info = {
        "Регион": region_name,
        "Источник": "наш.дом.рф — каталог новостроек (ЕИСЖС)",
        "Дата сбора": datetime.now().strftime("%d.%m.%Y"),
        "Режим": mode.upper() if opts.demo else mode,
    }
    result = analysis.run(df, info, config.OUTPUT_DIR)
    print(f"  Excel-отчёт: {result['excel']}")
    print(f"  CSV для Power BI: {result['csv']}")
    print(f"  Графики: {config.OUTPUT_DIR / 'charts'} ({len(result['charts'])} шт.)")
    if result["map"]:
        print(f"  Карта: {result['map']}")
    print(f"  Выводы: {result['summary']}")
    print("\nГотово!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
