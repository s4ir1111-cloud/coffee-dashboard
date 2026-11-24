# Проверяемая аналитика Garden Coffee

`analytics_query.py` — единая точка доступа к текущим и историческим данным.
Она не подменяет отсутствующие значения нулями и возвращает вместе с каждым
результатом формулу, операнды, источник, время обновления и проверки сверки.

## Примеры запросов

```bash
python analytics_query.py current --metric revenue
python analytics_query.py current --metric avg_check --store Московская
python analytics_query.py daily --date 2026-11-24 --metric revenue --store Московская
python analytics_query.py plan --metric pct_vs_expected --store Московская
python analytics_query.py monthly --month 2026-10 --metric revenue --store Океан
python analytics_query.py compare --metric revenue --month-a 2026-10 --month-b 2026-09
python analytics_query.py pnl --month 2026-10 --metric ebitda_pct
python analytics_query.py top --kind drinks --limit 5
python analytics_query.py health
python analytics_query.py catalog
```

Код возврата `0` означает успешный проверенный расчёт. Код `2` означает, что
источник, период, кофейня или нужная метрика отсутствуют; система не выдаёт
догадку вместо данных.

## Источники

- `dashboard_data_view.json`: сегодня, MTD, планы, скидки, товары и кофейни;
- `finance_data.json`: месячная история выручки, заказов и среднего чека;
- `pnl_data.json`: P&L по месяцам и кофейням;
- `analytics/history/daily/`: неизменяемые по датам снимки текущего дашборда;
- `reports/monthly/`: закрытые управленческие отчёты прошлых месяцев.

Гостевые персональные данные намеренно не подключены к общему аналитическому
слою. Для анализа гостевой базы должны использоваться только агрегированные
метрики без телефонов и имён.

## Правила ответа

При запросе цифры или анализа нужно вернуть:

1. значение, период и периметр (сеть или кофейня);
2. формулу и исходные числа;
3. время обновления источника;
4. предупреждение, если период не закрыт или данных недостаточно;
5. результат сверок из блока `verification`.

`analytics_snapshot.py` запускается после каждого обновления продаж. В течение
дня файл текущей даты заменяется более свежим снимком; снимки прошлых дат не
перезаписываются.
