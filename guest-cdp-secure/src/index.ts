const SESSION_COOKIE = "garden_cdp_session";
const SESSION_TTL_SECONDS = 12 * 60 * 60;
// Cloudflare Workers Web Crypto currently limits PBKDF2 to 100,000 iterations.
const PBKDF2_ITERATIONS = 100_000;

type User = { id: number; username: string; role: "admin" | "viewer"; active: number };

function json(data: unknown, status = 200): Response {
  return Response.json(data, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    },
  });
}

function html(body: string, status = 200): Response {
  return new Response(body, {
    status,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
    },
  });
}

function redirect(location: string): Response {
  return new Response(null, { status: 303, headers: { Location: location, "Cache-Control": "no-store" } });
}

function bytesToBase64(bytes: Uint8Array): string {
  let value = "";
  for (const byte of bytes) value += String.fromCharCode(byte);
  return btoa(value);
}

function base64ToBytes(value: string): Uint8Array<ArrayBuffer> {
  const decoded = atob(value);
  const bytes = new Uint8Array(decoded.length);
  for (let i = 0; i < decoded.length; i += 1) bytes[i] = decoded.charCodeAt(i);
  return bytes;
}

async function sha256(value: string): Promise<string> {
  return bytesToBase64(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value))));
}

async function passwordHash(password: string, saltBase64: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: base64ToBytes(saltBase64), iterations: PBKDF2_ITERATIONS },
    key,
    256,
  );
  return bytesToBase64(new Uint8Array(bits));
}

function secureEqual(a: string, b: string): boolean {
  const aa = new TextEncoder().encode(a);
  const bb = new TextEncoder().encode(b);
  if (aa.length !== bb.length) return false;
  let diff = 0;
  for (let i = 0; i < aa.length; i += 1) diff |= aa[i] ^ bb[i];
  return diff === 0;
}

function cookieValue(request: Request, name: string): string | null {
  const cookie = request.headers.get("Cookie") ?? "";
  for (const part of cookie.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return null;
}

async function currentUser(request: Request, env: Env): Promise<User | null> {
  const token = cookieValue(request, SESSION_COOKIE);
  if (!token) return null;
  const tokenHash = await sha256(token);
  return env.DB.prepare(
    `SELECT u.id, u.username, u.role, u.active
       FROM sessions s JOIN users u ON u.id = s.user_id
      WHERE s.token_hash = ? AND s.expires_at > datetime('now') AND u.active = 1`,
  ).bind(tokenHash).first<User>();
}

function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("Origin");
  return origin === new URL(request.url).origin;
}

function validUsername(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-zА-Яа-яЁё0-9._-]{3,40}$/.test(value);
}

function validPassword(value: unknown): value is string {
  return typeof value === "string" && value.length >= 10 && value.length <= 128;
}

async function readBody(request: Request): Promise<Record<string, unknown>> {
  const type = request.headers.get("Content-Type") ?? "";
  if (type.includes("application/json")) return await request.json<Record<string, unknown>>();
  const form = await request.formData();
  return Object.fromEntries(form.entries());
}

const LOGIN_PAGE = `<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Вход — Guest CDP</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d0f14;color:#e8eaf0;font:14px system-ui}.card{width:min(390px,calc(100% - 32px));padding:30px;background:#161a22;border:1px solid #2a3045;border-radius:16px}h1{margin:0 0 6px;font-size:23px}p{color:#8b93aa;margin:0 0 24px}label{display:block;margin:14px 0 6px}input{width:100%;padding:11px 12px;border-radius:8px;border:1px solid #343b50;background:#0d0f14;color:#fff}button{width:100%;margin-top:20px;padding:11px;border:0;border-radius:8px;background:#f5a623;color:#111;font-weight:700;cursor:pointer}.error{color:#ef5350;min-height:20px;margin-top:12px}</style><div class="card"><h1>Guest <span style="color:#f5a623">CDP</span></h1><p>Защищённый доступ Garden</p><form id="f"><label>Логин</label><input name="username" autocomplete="username" required><label>Пароль</label><input name="password" type="password" autocomplete="current-password" required><button>Войти</button><div class="error" id="e"></div></form></div><script>f.onsubmit=async(e)=>{e.preventDefault();const b=Object.fromEntries(new FormData(f));const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});if(r.ok)location='/';else document.querySelector('#e').textContent=(await r.json()).error||'Ошибка входа'};</script></html>`;

const ADMIN_PAGE = `<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Пользователи — Guest CDP</title><style>
:root{color-scheme:dark}body{margin:0;background:#0d0f14;color:#e8eaf0;font:14px system-ui}main{max-width:900px;margin:auto;padding:28px}header{display:flex;justify-content:space-between;align-items:center}a,button{color:#f5a623}section{background:#161a22;border:1px solid #2a3045;border-radius:12px;padding:18px;margin-top:18px}input,select,button{padding:8px 10px;border-radius:7px;border:1px solid #343b50;background:#0d0f14;color:#fff}button{cursor:pointer}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:9px;border-bottom:1px solid #2a3045}.row{display:flex;gap:8px;flex-wrap:wrap}.msg{min-height:20px;margin-top:10px;color:#66bb6a}</style><main><header><h1>Пользователи</h1><div><a href="/">Дашборд</a> · <a href="#" id="logout">Выйти</a></div></header><section><h2>Добавить пользователя</h2><form id="create" class="row"><input name="username" placeholder="Логин" required><input name="password" type="password" placeholder="Пароль (от 10 символов)" required><select name="role"><option value="viewer">Пользователь</option><option value="admin">Администратор</option></select><button>Создать</button></form><div class="msg" id="msg"></div></section><section><table><thead><tr><th>Логин</th><th>Роль</th><th>Статус</th><th>Действия</th></tr></thead><tbody id="users"></tbody></table></section></main><script>
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));async function load(){const r=await fetch('/api/admin/users');if(r.status===401)return location='/login';if(r.status===403)return location='/';const d=await r.json();users.innerHTML=d.users.map(u=>'<tr><td>'+esc(u.username)+'</td><td>'+esc(u.role)+'</td><td>'+(u.active?'Активен':'Заблокирован')+'</td><td><button data-id="'+u.id+'" data-active="'+u.active+'">'+(u.active?'Блокировать':'Включить')+'</button> <button data-reset="'+u.id+'">Сменить пароль</button></td></tr>').join('')}create.onsubmit=async e=>{e.preventDefault();const b=Object.fromEntries(new FormData(create));const r=await fetch('/api/admin/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});const d=await r.json();msg.textContent=r.ok?'Пользователь создан':d.error;create.reset();await load()};users.onclick=async e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.id){await fetch('/api/admin/users/'+b.dataset.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({active:b.dataset.active!=='1'})})}else{const p=prompt('Новый пароль (от 10 символов)');if(p)await fetch('/api/admin/users/'+b.dataset.reset+'/password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p})})}await load()};logout.onclick=async e=>{e.preventDefault();await fetch('/api/logout',{method:'POST'});location='/login'};load();</script></html>`;

const SETUP_PAGE = `<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Первичная настройка Guest CDP</title><style>:root{color-scheme:dark}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d0f14;color:#e8eaf0;font:14px system-ui}.card{width:min(420px,calc(100% - 32px));padding:28px;background:#161a22;border:1px solid #2a3045;border-radius:16px}input,button{box-sizing:border-box;width:100%;padding:11px;margin-top:10px;border-radius:8px;border:1px solid #343b50;background:#0d0f14;color:#fff}button{background:#f5a623;color:#111;font-weight:700}.error{color:#ef5350;margin-top:10px}</style><div class="card"><h1>Первый администратор</h1><p>Ссылка с токеном работает только до создания первого пользователя.</p><form id="f"><input name="username" placeholder="Логин" autocomplete="username" required><input name="password" type="password" placeholder="Пароль (от 10 символов)" autocomplete="new-password" required><button>Создать администратора</button><div id="e" class="error"></div></form></div><script>f.onsubmit=async(e)=>{e.preventDefault();const b=Object.fromEntries(new FormData(f));b.token=new URLSearchParams(location.search).get('token');const r=await fetch('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});const d=await r.json();if(r.ok)location='/login';else document.querySelector('#e').textContent=d.error||'Ошибка'};</script></html>`;

async function audit(env: Env, actor: number, action: string, target?: number): Promise<void> {
  await env.DB.prepare("INSERT INTO audit_log(actor_user_id, action, target_user_id) VALUES (?, ?, ?)")
    .bind(actor, action, target ?? null).run();
}

async function handleLogin(request: Request, env: Env): Promise<Response> {
  const body = await readBody(request);
  if (!validUsername(body.username) || typeof body.password !== "string") return json({ error: "Неверный логин или пароль" }, 400);
  const ip = request.headers.get("CF-Connecting-IP") ?? "unknown";
  const attemptKey = await sha256(`${ip}:${body.username.toLowerCase()}`);
  const attempt = await env.DB.prepare("SELECT failures, blocked_until FROM login_attempts WHERE key = ?").bind(attemptKey).first<{ failures: number; blocked_until: string | null }>();
  if (attempt?.blocked_until && new Date(`${attempt.blocked_until}Z`) > new Date()) return json({ error: "Слишком много попыток. Повторите позже" }, 429);

  const user = await env.DB.prepare("SELECT id, username, password_hash, password_salt, role, active FROM users WHERE username = ?")
    .bind(body.username).first<User & { password_hash: string; password_salt: string }>();
  const computed = user ? await passwordHash(body.password, user.password_salt) : await passwordHash(body.password, bytesToBase64(new Uint8Array(16)));
  if (!user || !user.active || !secureEqual(computed, user.password_hash)) {
    const failures = (attempt?.failures ?? 0) + 1;
    const blocked = failures >= 5 ? new Date(Date.now() + 15 * 60_000).toISOString().replace("T", " ").slice(0, 19) : null;
    await env.DB.prepare(`INSERT INTO login_attempts(key, failures, blocked_until, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
      ON CONFLICT(key) DO UPDATE SET failures = excluded.failures, blocked_until = excluded.blocked_until, updated_at = CURRENT_TIMESTAMP`)
      .bind(attemptKey, failures, blocked).run();
    return json({ error: "Неверный логин или пароль" }, 401);
  }

  const token = bytesToBase64(crypto.getRandomValues(new Uint8Array(32))).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
  const tokenHash = await sha256(token);
  const expires = new Date(Date.now() + SESSION_TTL_SECONDS * 1000).toISOString().replace("T", " ").slice(0, 19);
  await env.DB.batch([
    env.DB.prepare("DELETE FROM login_attempts WHERE key = ?").bind(attemptKey),
    env.DB.prepare("INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)").bind(tokenHash, user.id, expires),
    env.DB.prepare("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?").bind(user.id),
  ]);
  const response = json({ ok: true, role: user.role });
  response.headers.append("Set-Cookie", `${SESSION_COOKIE}=${token}; Path=/; Max-Age=${SESSION_TTL_SECONDS}; HttpOnly; Secure; SameSite=Strict`);
  return response;
}

async function requireAdmin(request: Request, env: Env): Promise<User | Response> {
  const user = await currentUser(request, env);
  if (!user) return json({ error: "Требуется вход" }, 401);
  if (user.role !== "admin") return json({ error: "Недостаточно прав" }, 403);
  return user;
}

export default {
  async fetch(request, env, ctx): Promise<Response> {
    const url = new URL(request.url);
    const method = request.method;
    try {
      if (["POST", "PUT", "PATCH", "DELETE"].includes(method) && !sameOrigin(request)) return json({ error: "Недопустимый источник запроса" }, 403);
      if (url.pathname === "/setup" && method === "GET") {
        const count = await env.DB.prepare("SELECT COUNT(*) AS count FROM users").first<{ count: number }>();
        if ((count?.count ?? 0) > 0) return redirect("/login");
        if (!secureEqual(url.searchParams.get("token") ?? "", env.BOOTSTRAP_TOKEN)) return new Response("Forbidden", { status: 403 });
        return html(SETUP_PAGE);
      }
      if (url.pathname === "/api/setup" && method === "POST") {
        const body = await readBody(request);
        if (typeof body.token !== "string" || !secureEqual(body.token, env.BOOTSTRAP_TOKEN)) return json({ error: "Недействительный токен настройки" }, 403);
        if (!validUsername(body.username) || !validPassword(body.password)) return json({ error: "Логин: 3–40 символов; пароль: 10–128 символов" }, 400);
        const count = await env.DB.prepare("SELECT COUNT(*) AS count FROM users").first<{ count: number }>();
        if ((count?.count ?? 0) > 0) return json({ error: "Администратор уже создан" }, 409);
        const salt = bytesToBase64(crypto.getRandomValues(new Uint8Array(16)));
        await env.DB.prepare("INSERT INTO users(username, password_hash, password_salt, role) VALUES (?, ?, ?, 'admin')")
          .bind(body.username, await passwordHash(body.password, salt), salt).run();
        return json({ ok: true }, 201);
      }
      if (url.pathname === "/login" && method === "GET") return html(LOGIN_PAGE);
      if (url.pathname === "/api/login" && method === "POST") return handleLogin(request, env);
      if (url.pathname === "/api/logout" && method === "POST") {
        const token = cookieValue(request, SESSION_COOKIE);
        if (token) await env.DB.prepare("DELETE FROM sessions WHERE token_hash = ?").bind(await sha256(token)).run();
        const response = json({ ok: true });
        response.headers.append("Set-Cookie", `${SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict`);
        return response;
      }

      if (url.pathname === "/api/admin/users" && method === "GET") {
        const admin = await requireAdmin(request, env); if (admin instanceof Response) return admin;
        const result = await env.DB.prepare("SELECT id, username, role, active, created_at, last_login_at FROM users ORDER BY username").all();
        return json({ users: result.results });
      }
      if (url.pathname === "/api/admin/users" && method === "POST") {
        const admin = await requireAdmin(request, env); if (admin instanceof Response) return admin;
        const body = await readBody(request);
        if (!validUsername(body.username) || !validPassword(body.password) || !["admin", "viewer"].includes(String(body.role))) return json({ error: "Проверьте логин, пароль и роль" }, 400);
        const salt = bytesToBase64(crypto.getRandomValues(new Uint8Array(16)));
        const result = await env.DB.prepare("INSERT INTO users(username, password_hash, password_salt, role) VALUES (?, ?, ?, ?)")
          .bind(body.username, await passwordHash(body.password, salt), salt, body.role).run();
        await audit(env, admin.id, "user.create", Number(result.meta.last_row_id));
        return json({ ok: true }, 201);
      }

      const statusMatch = url.pathname.match(/^\/api\/admin\/users\/(\d+)$/);
      if (statusMatch && method === "PATCH") {
        const admin = await requireAdmin(request, env); if (admin instanceof Response) return admin;
        const target = Number(statusMatch[1]); const body = await readBody(request);
        if (target === admin.id && body.active === false) return json({ error: "Нельзя заблокировать собственную учётную запись" }, 400);
        await env.DB.prepare("UPDATE users SET active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?").bind(body.active === true ? 1 : 0, target).run();
        if (body.active !== true) await env.DB.prepare("DELETE FROM sessions WHERE user_id = ?").bind(target).run();
        await audit(env, admin.id, body.active === true ? "user.enable" : "user.disable", target);
        return json({ ok: true });
      }

      const passwordMatch = url.pathname.match(/^\/api\/admin\/users\/(\d+)\/password$/);
      if (passwordMatch && method === "POST") {
        const admin = await requireAdmin(request, env); if (admin instanceof Response) return admin;
        const target = Number(passwordMatch[1]); const body = await readBody(request);
        if (!validPassword(body.password)) return json({ error: "Пароль должен содержать 10–128 символов" }, 400);
        const salt = bytesToBase64(crypto.getRandomValues(new Uint8Array(16)));
        await env.DB.batch([
          env.DB.prepare("UPDATE users SET password_hash = ?, password_salt = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?").bind(await passwordHash(body.password, salt), salt, target),
          env.DB.prepare("DELETE FROM sessions WHERE user_id = ?").bind(target),
        ]);
        await audit(env, admin.id, "user.password_reset", target);
        return json({ ok: true });
      }

      const user = await currentUser(request, env);
      if (!user) return url.pathname.startsWith("/api/") ? json({ error: "Требуется вход" }, 401) : redirect("/login");
      if (url.pathname === "/admin") return user.role === "admin" ? html(ADMIN_PAGE) : redirect("/");
      if (url.pathname === "/api/me") return json({ user });
      if (url.pathname === "/" || url.pathname === "/dashboard" || url.pathname === "/dashboard.html") {
        const assetUrl = new URL("/dashboard.html", request.url);
        return env.ASSETS.fetch(new Request(assetUrl, request));
      }
      return new Response("Not found", { status: 404 });
    } catch (error) {
      console.error(JSON.stringify({ event: "request_error", path: url.pathname, error: error instanceof Error ? error.message : String(error) }));
      ctx.waitUntil(env.DB.prepare("DELETE FROM sessions WHERE expires_at <= datetime('now')").run().then(() => undefined));
      return json({ error: "Внутренняя ошибка" }, 500);
    }
  },
} satisfies ExportedHandler<Env>;
