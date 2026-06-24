#!/bin/bash
# Garden Coffee · Merge & Build P&L (новая API)
# Копирует pnl_data_raw.json из Downloads, строит pnl_data.json, пушит на GitHub
#
# Перед запуском: выполните pnl_extract_new.js в браузере IIKO
# и скачайте pnl_data_raw.json в ~/Downloads/

REPO="$HOME/coffee-dashboard-repo"
SRC="$HOME/Downloads/pnl_data_raw.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "════════════════════════════════════════"
echo "  Garden Coffee · Merge & Build P&L"
echo "════════════════════════════════════════"
echo ""

# 1. Проверяем файл из браузера
if [ ! -f "$SRC" ]; then
  echo "❌ Файл не найден: $SRC"
  echo ""
  echo "Запустите сначала JS-экстрактор:"
  echo "  1. Откройте https://kofeinya-garden-co-co.iikoweb.ru/dashboard/index.html#/dashboard/414"
  echo "  2. F12 → Console → вставьте содержимое pnl_extract_new.js → Enter"
  echo "  3. Сохраните скачанный pnl_data_raw.json в ~/Downloads/"
  read -p "Enter..." && exit 1
fi

echo "✓ Найден: $SRC ($(du -h "$SRC" | cut -f1))"
cp "$SRC" "$REPO/pnl_data_raw.json"
echo "✓ Скопирован в репо"

# 2. Обновляем скрипты
cp "$SCRIPT_DIR/build_pnl_data.py" "$REPO/build_pnl_data.py"
cp "$SCRIPT_DIR/pnl_connector.py"  "$REPO/pnl_connector.py"
echo "✓ Скрипты обновлены"
echo ""

# 3. Строим pnl_data.json
cd "$REPO"
echo "=== Строим pnl_data.json ==="
python3 build_pnl_data.py
if [ $? -ne 0 ]; then
  echo "❌ Ошибка при построении данных"
  read -p "Enter..." && exit 1
fi

# 4. Пушим на GitHub
echo ""
echo "=== Пушим на GitHub ==="
git add pnl_data.json pnl_data_raw.json build_pnl_data.py pnl_connector.py
git diff --staged --quiet && echo "Нет изменений." && read -p "Enter..." && exit 0
git commit -m "pnl: update from IIKO P&L API $(date '+%Y-%m-%d')"
git pull --rebase --autostash --quiet
git push

echo ""
echo "✓ Готово!"
echo "  https://s4ir1111-cloud.github.io/coffee-dashboard/coffee_finance.html"
echo ""
read -p "Нажмите Enter для закрытия..."
