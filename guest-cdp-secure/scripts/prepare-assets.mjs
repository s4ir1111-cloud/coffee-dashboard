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
dashboard = dashboard.replace("</body>", `<script>
(async()=>{const admin=document.getElementById('secureAdmin');const logout=document.getElementById('secureLogout');try{const response=await fetch('/api/me',{cache:'no-store'});if(!response.ok){location='/login';return}const data=await response.json();if(data.user.role!=='admin')admin.style.display='none'}catch{location='/login'}logout.addEventListener('click',async()=>{logout.disabled=true;await fetch('/api/logout',{method:'POST'});location='/login'})})();
</script></body>`);
await mkdir(targetDir, { recursive: true });
await writeFile(target, dashboard, "utf8");
console.log(`Prepared protected dashboard: ${(info.size / 1024 / 1024).toFixed(1)} MiB`);
