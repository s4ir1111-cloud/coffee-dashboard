"""
Собирает guest_cdp_dashboard_private.html из guest_cdp_data.json
без маскировки телефонов.

Использование:
    python3 build_private_dashboard.py
    python3 build_private_dashboard.py --data другой_файл.json --out мой_дашборд.html

Файл НЕ коммитится в git (добавлен в .gitignore).
"""

import json
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="guest_cdp_data.json",
                        help="Путь к JSON с данными гостей (дефолт: guest_cdp_data.json)")
    parser.add_argument("--out", default="guest_cdp_dashboard_private.html",
                        help="Путь выходного HTML (дефолт: guest_cdp_dashboard_private.html)")
    args = parser.parse_args()

    data_path = BASE_DIR / args.data
    out_path  = BASE_DIR / args.out
    tpl_path  = BASE_DIR / "guest_cdp_dashboard_template.html"

    if not data_path.exists():
        print(f"❌ Файл данных не найден: {data_path}")
        print("   Сначала запусти: python3 build_guest_cdp_data.py")
        raise SystemExit(1)

    if not tpl_path.exists():
        print(f"❌ Шаблон не найден: {tpl_path}")
        raise SystemExit(1)

    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    guest_count = len(data.get("guests", []))
    phones_count = sum(1 for g in data.get("guests", []) if g.get("phone"))
    print(f"  Гостей всего:    {guest_count:,}")
    print(f"  С телефонами:    {phones_count:,}")

    data_str = json.dumps(data, ensure_ascii=False)

    with open(tpl_path, encoding="utf-8") as f:
        html = f.read()

    marker = '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>'
    if marker not in html:
        print("❌ Маркер вставки не найден в шаблоне. Проверь guest_cdp_dashboard_template.html")
        raise SystemExit(1)

    private_html = html.replace(
        marker,
        f"<script>window.__GUEST_DATA__={data_str};</script>\n{marker}",
        1,
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(private_html)

    size_mb = out_path.stat().st_size / 1_048_576
    print(f"\n✅ Сохранено: {out_path}  ({size_mb:.1f} МБ)")
    print("   ⚠️  Файл содержит полные номера телефонов — не публиковать!")


if __name__ == "__main__":
    main()
