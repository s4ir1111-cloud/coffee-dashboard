import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const source = path.join(root, "guest_cdp_dashboard.html");
const targetDir = path.join(root, "guest-cdp-secure", "public");
const target = path.join(targetDir, "dashboard.html");

const info = await stat(source);
if (info.size < 100_000) throw new Error("Dashboard source is unexpectedly small");
let dashboard = await readFile(source, "utf8");
const headerMarker = '<span class="hbadge" id="fileBadge">Загрузка данных…</span>';
if (!dashboard.includes(headerMarker)) throw new Error("Dashboard header marker not found");
dashboard = dashboard.replace(headerMarker, `${headerMarker}
    <a class="btn btn-ghost" id="secureAdmin" href="/admin">⚙ Пользователи</a>
    <button class="btn btn-ghost" id="secureLogout" type="button">Выйти</button>`);
dashboard = dashboard.replace(
  /<a class="btn btn-ghost" href="https:\/\/github\.com\/s4ir1111-cloud\/coffee-dashboard\/actions\/workflows\/update-guest-cdp\.yml"[^>]*>↻ Обновить данные<\/a>/,
  '<button class="btn btn-ghost" id="secureUpdate" type="button">↻ Обновить данные</button>',
);
dashboard = dashboard.replace("</body>", `<script>
(async()=>{const admin=document.getElementById('secureAdmin');const logout=document.getElementById('secureLogout');const update=document.getElementById('secureUpdate');try{const response=await fetch('/api/me',{cache:'no-store'});if(!response.ok){location='/login';return}const data=await response.json();if(data.user.role!=='admin'){admin.style.display='none';update.style.display='none'}}catch{location='/login'}update.addEventListener('click',async()=>{if(!confirm('Запустить обновление данных? Оно займёт около 10 минут.'))return;update.disabled=true;update.textContent='⏳ Запускаю…';try{const r=await fetch('/api/admin/update',{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(d.error||'Ошибка запуска');update.textContent='⏳ Обновление идёт';const started=Date.now();const poll=async()=>{const sr=await fetch('/api/admin/update-status',{cache:'no-store'});if(!sr.ok)throw new Error('Статус недоступен');const sd=await sr.json();const run=sd.run;if(run&&new Date(run.created_at).getTime()>=started-60000){if(run.status==='completed'){if(run.conclusion==='success'){update.textContent='✓ Данные обновлены';setTimeout(()=>location.reload(),1500)}else{update.textContent='⚠ Ошибка обновления';update.disabled=false}return}}setTimeout(poll,15000)};setTimeout(poll,5000)}catch(error){update.textContent='↻ Обновить данные';update.disabled=false;alert(error.message)}});logout.addEventListener('click',async()=>{logout.disabled=true;await fetch('/api/logout',{method:'POST'});location='/login'})})();
</script></body>`);
await mkdir(targetDir, { recursive: true });
await writeFile(target, dashboard, "utf8");
console.log(`Prepared protected dashboard: ${(info.size / 1024 / 1024).toFixed(1)} MiB`);
