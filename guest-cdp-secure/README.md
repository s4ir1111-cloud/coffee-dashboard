# Secure Guest CDP

Защищённая версия Guest CDP для Cloudflare Workers + D1.

## Безопасность

- каждый запрос к дашборду проходит через Worker;
- пользователи и сессии хранятся в D1;
- пароли хешируются PBKDF2-SHA-256 с индивидуальной солью;
- сессионная cookie: `HttpOnly`, `Secure`, `SameSite=Strict`;
- есть ограничение неудачных попыток входа;
- администратор создаёт, блокирует пользователей и сбрасывает пароли;
- `public/dashboard.html` генерируется локально и не коммитится.

## Развёртывание

1. `npm install`
2. `npx wrangler login`
3. `npx wrangler d1 create garden-guest-cdp`
4. Записать полученный `database_id` в `wrangler.jsonc`
5. `npx wrangler d1 migrations apply garden-guest-cdp --remote`
6. Создать случайный секрет командой `npx wrangler secret put BOOTSTRAP_TOKEN`
7. `npm run deploy`

Первый администратор создаётся через одноразовую ссылку `/setup?token=...`. После создания первого пользователя endpoint настройки автоматически закрывается. Логин и пароль нельзя добавлять в Git или передавать как аргументы командной строки.
