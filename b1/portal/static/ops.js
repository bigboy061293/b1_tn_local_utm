// Trang vận hành – Vùng trời (F-03): bản đồ trạng thái khối cập nhật theo luồng sự kiện, kế hoạch kích hoạt, gói dữ liệu, hiệp đồng.
import { accessToken, init, login, logout } from "./auth.js";

const main = document.getElementById("main");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (t) => t ? new Date(t).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", hour12: false, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
const STATE = { planned: ["Đã lập", ""], coordinated: ["Đã hiệp đồng", "warn"], active: ["Đang kích hoạt", "ok"], closed: ["Đã đóng", ""],
  cancelled: ["Đã huỷ", ""], rejected: ["Hiệp đồng không đồng ý", "bad"] };
const badge = (s) => { const [t, c] = STATE[s] || [s, ""]; return `<span class="badge ${c}">${esc(t)}</span>`; };
function toast(msg) { const d = document.createElement("div"); d.textContent = msg; document.getElementById("toast").append(d); setTimeout(() => d.remove(), 5000); }

async function api(path, opts = {}) {
  const tok = await accessToken();
  if (!tok) { await login(); throw new Error("cần đăng nhập"); }
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok}`, ...(opts.headers || {}) },
                                body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (opts.raw) { if (!r.ok) throw new Error(`lỗi ${r.status}`); return r; }
  const data = await r.json().catch(() => null);
  if (!r.ok) {
    const d = data?.detail;
    throw new Error(Array.isArray(d) ? d.map((x) => `${x.loc?.slice(-1)[0]}: ${x.msg}`).join("; ") : (d?.message || d || `lỗi ${r.status}`));
  }
  return data;
}

let me, canManage = false, blocks = [], fences = [], acts = [], lastSeq = null;

// ---------- bản đồ (SVG, không cần nền bản đồ ngoài nên chạy được trong mạng vận hành kín) ----------
function project(features) {
  const pts = features.flatMap((g) => g.type === "Polygon" ? g.coordinates[0] : g.coordinates);
  const lons = pts.map((p) => p[0]), lats = pts.map((p) => p[1]);
  const lat0 = (Math.min(...lats) + Math.max(...lats)) / 2, k = Math.cos(lat0 * Math.PI / 180);
  const minX = Math.min(...lons) * k, maxY = Math.max(...lats);
  const w = (Math.max(...lons) - Math.min(...lons)) * k, h = maxY - Math.min(...lats);
  const s = 400 / Math.max(w, h), pad = 20;
  return { f: ([lon, lat]) => [pad + (lon * k - minX) * s, pad + (maxY - lat) * s], W: w * s + 2 * pad, H: h * s + 2 * pad };
}

function drawMap() {
  const P = project([...blocks.map((b) => b.geometry), ...fences.map((f) => f.geometry)]);
  const path = (ring) => ring.map((p, i) => `${i ? "L" : "M"}${P.f(p).map((v) => v.toFixed(1)).join(",")}`).join("") + "Z";
  const upcoming = new Set(acts.filter((a) => a.state === "coordinated").map((a) => a.block_id));
  document.getElementById("mapwrap").innerHTML = `<svg id="map" viewBox="0 0 ${P.W.toFixed(0)} ${P.H.toFixed(0)}" role="img" aria-label="Bản đồ trạng thái khối">
    ${blocks.map((b) => `<path class="blk ${upcoming.has(b.id) ? "upcoming" : ""}" data-block="${b.id}" data-status="${b.status}" d="${path(b.geometry.coordinates[0])}"><title>${esc(b.name)}: ${b.status === "active" ? "đang kích hoạt" : "chưa kích hoạt"}</title></path>`).join("")}
    ${fences.map((f) => `<path class="fence" d="${f.geometry.coordinates.map((p, i) => `${i ? "L" : "M"}${P.f(p).join(",")}`).join("")}"><title>${esc(f.properties.name)}: rào địa lý cứng</title></path>`).join("")}
    ${blocks.map((b) => { const c = b.geometry.coordinates[0].slice(0, -1); const m = c.reduce((a, p) => [a[0] + p[0] / c.length, a[1] + p[1] / c.length], [0, 0]); const [x, y] = P.f(m); return `<text x="${x}" y="${y}" text-anchor="middle">${esc(b.name)}</text>`; }).join("")}
  </svg>`;
}

// ---------- dữ liệu ----------
async function refresh() {
  const now = Date.now();
  const [st, list] = await Promise.all([
    api("/v1/airspace/state"),
    api(`/v1/airspace/activations?start=${new Date(now - 6 * 3600e3).toISOString()}&end=${new Date(now + 7 * 86400e3).toISOString()}`),
  ]);
  blocks = st.blocks;
  lastSeq = st.last_seq;
  acts = list.items;
  drawMap();
  renderActs();
}

function renderActs() {
  const live = acts.filter((a) => !["closed", "cancelled", "rejected"].includes(a.state));
  const done = acts.filter((a) => !live.includes(a));
  const row = (a) => `<tr data-id="${a.id}"><td>${esc(a.block_id.replace("block-", "Khối "))}</td><td>${fmt(a.starts_at)} – ${fmt(a.ends_at)}</td>
    <td>${a.floor_m}–${a.ceiling_m} m ${esc(a.ceiling_ref)}</td><td>${a.flights.length}</td><td>${badge(a.state)}${a.coordination_ref ? `<br><small class="muted">${esc(a.coordination_ref)}</small>` : ""}</td>
    <td><div class="row"><button data-act="pkg">Gói dữ liệu</button>
      ${canManage && a.state === "planned" ? `<button data-act="coord">Ghi hiệp đồng</button><button data-act="cancel" class="danger">Huỷ</button>` : ""}
      ${canManage && a.state === "coordinated" ? `<button data-act="activate" class="primary">Kích hoạt</button><button data-act="cancel" class="danger">Huỷ</button>` : ""}
      ${canManage && a.state === "active" ? `<button data-act="close" class="danger">Đóng khối</button>` : ""}</div></td></tr>`;
  const table = (items) => items.length ? `<table class="acts"><tr><th>Khối</th><th>Khung giờ (giờ VN)</th><th>Tầng</th><th>Chuyến</th><th>Trạng thái</th><th></th></tr>${items.map(row).join("")}</table>` : `<p class="muted">Không có.</p>`;
  document.getElementById("acts").innerHTML = `<h2>Kế hoạch kích hoạt</h2>${table(live)}<h2>Đã xong</h2>${table(done.slice(-10))}`;
  document.querySelectorAll("#acts button").forEach((b) => b.addEventListener("click", () => action(b.closest("tr").dataset.id, b.dataset.act).catch((e) => toast(e.message))));
}

async function action(id, act) {
  if (act === "pkg") return showPackage(id);
  if (act === "coord") return coordForm(id);
  if (act === "cancel" && !confirmInline(id, "huỷ")) return;
  if (act === "close" && !confirmInline(id, "đóng")) return;
  await api(`/v1/airspace/activations/${id}/${act}`, { method: "POST" });
  toast({ activate: "Đã kích hoạt khối", close: "Đã đóng khối", cancel: "Đã huỷ kế hoạch" }[act]);
}

// Xác nhận bằng lần bấm thứ hai trên cùng nút (không dùng hộp thoại chặn của trình duyệt).
const armed = new Map();
function confirmInline(id, verb) {
  const key = `${id}:${verb}`;
  if (armed.get(key) > Date.now()) { armed.delete(key); return true; }
  armed.set(key, Date.now() + 4000);
  toast(`Bấm lần nữa trong 4 giây để ${verb}.`);
  return false;
}

async function showPackage(id) {
  const r = await api(`/v1/airspace/activations/${id}/package?format=text`, { raw: true });
  const text = await r.text(), sha = r.headers.get("X-Package-SHA256");
  const dlg = dialog(`<h2>Gói dữ liệu kích hoạt</h2><p class="muted mono">SHA-256: ${esc(sha)}</p><pre class="pkg">${esc(text)}</pre>
    <div class="inline"><button data-f="text">Tải văn bản</button><button data-f="geojson">Tải GeoJSON</button><button data-f="ed269">Tải ED-269</button><button data-close>Đóng</button></div>`);
  dlg.querySelectorAll("button[data-f]").forEach((b) => b.addEventListener("click", async () => {
    const f = b.dataset.f, res = await api(`/v1/airspace/activations/${id}/package?format=${f}`, { raw: true });
    const blob = await res.blob(), a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `goi-kich-hoat-${id.slice(0, 8)}.${f === "text" ? "txt" : f === "geojson" ? "geojson" : "ed269.json"}`; a.click();
  }));
}

async function coordForm(id) {
  const r = await api(`/v1/airspace/activations/${id}/package?format=text`, { raw: true });
  const sha = r.headers.get("X-Package-SHA256");
  const dlg = dialog(`<h2>Ghi kết quả hiệp đồng</h2>
    <p class="muted">Kết quả gắn với gói có mã băm <span class="mono">${esc(sha.slice(0, 16))}…</span>. Gói đổi sau khi gửi thì phải gửi lại.</p>
    <form id="cf" class="grid2"><div><label>Kết quả</label><select name="result"><option value="approved">Đồng ý</option><option value="rejected">Không đồng ý</option></select></div>
    <div><label>Số công văn, số điện hiệp đồng</label><input name="reference" required></div>
    <div><label>Cơ quan, người hiệp đồng</label><input name="counterpart" required></div>
    <div><label>Ghi chú</label><input name="note"></div>
    <div class="inline"><button class="primary">Ghi</button><button type="button" data-close>Huỷ</button></div></form>`);
  dlg.querySelector("#cf").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = Object.fromEntries([...new FormData(ev.target)].filter(([, v]) => v !== ""));
    try { await api(`/v1/airspace/activations/${id}/coordination`, { method: "POST", body: { ...body, package_sha256: sha } }); dlg.close(); toast("Đã ghi hiệp đồng"); }
    catch (e) { toast(e.message); }
  });
}

function dialog(html) {
  const d = document.createElement("dialog");
  d.innerHTML = html;
  d.addEventListener("close", () => d.remove());
  d.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => d.close()));
  document.body.append(d);
  d.showModal();
  return d;
}

// ---------- luồng sự kiện ----------
async function stream(after) {
  const el = document.getElementById("live");
  for (;;) {
    try {
      const tok = await accessToken();
      const r = await fetch(`/v1/airspace/stream${after != null ? `?after=${after}` : ""}`, { headers: { Authorization: `Bearer ${tok}` } });
      if (!r.ok) throw new Error(`luồng lỗi ${r.status}`);
      el.classList.add("on");
      const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += value;
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          const ev = /^event: (.*)$/m.exec(chunk)?.[1], data = /^data: (.*)$/m.exec(chunk)?.[1];
          if (!data) continue;
          const d = JSON.parse(data);
          if (ev === "hello") { after = d.seq; continue; }
          after = d.seq;
          await refresh();
          const lag = Date.now() - new Date(d.at).getTime();
          el.querySelector("span").textContent = `Sự kiện #${d.seq}: ${d.kind} (${d.block_id || ""}) lúc ${fmt(d.at)}; lên bản đồ sau ${lag} ms`;
          el.dataset.lastSeq = d.seq;
        }
      }
    } catch (e) { el.querySelector("span").textContent = `Mất luồng sự kiện: ${e.message}. Thử lại…`; }
    el.classList.remove("on");
    await new Promise((res) => setTimeout(res, 2000));
  }
}

// ---------- khởi động ----------
async function start() {
  const d = await (await fetch("/v1/disclaimers")).json();
  document.getElementById("disclaimers").innerHTML = `<ul>${d.items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  if (!(await init())) {
    main.innerHTML = `<div class="login card"><h1>Trang vận hành</h1><p>Dành cho cán bộ bộ phận quản lý hành lang.</p><button class="primary" id="lg">Đăng nhập</button></div>`;
    document.getElementById("lg").addEventListener("click", login);
    return;
  }
  me = await api("/v1/me");
  canManage = me.roles.includes("corridor_head") && me.mfa;
  document.getElementById("who").innerHTML = `<span>${esc(me.display_name)}</span><button id="lo">Đăng xuất</button>`;
  document.getElementById("lo").addEventListener("click", logout);
  const cons = await api("/v1/airspace/constraints");
  fences = cons.features.filter((f) => f.properties.kind === "hard_geofence" && f.geometry);
  main.innerHTML = `<h1>Vùng trời</h1>
    ${me.roles.includes("corridor_head") && !me.mfa ? `<div class="alert warn">Tài khoản chưa xác thực đa yếu tố: chỉ xem, không thao tác được.</div>` : ""}
    <div class="opsgrid"><section class="card mapcard"><div id="mapwrap"></div>
      <div class="legend"><span><i style="background:#2e9d5b"></i>Đang kích hoạt</span><span><i style="background:#e9c46a"></i>Đã hiệp đồng, chờ kích hoạt</span>
      <span><i style="background:#c9d1da"></i>Chưa kích hoạt</span><span><i style="background:#c0392b"></i>Rào địa lý cứng</span></div>
      <div class="live" id="live"><i class="dot"></i><span>Đang nối luồng sự kiện…</span></div>
      <p class="muted" style="font-size:12px">Hình học khối là dự thảo theo KML hành lang, chưa phải vùng trời đã phê duyệt.</p></section>
    <section>${canManage ? `<div class="card"><h2 style="margin-top:0">Lập kế hoạch</h2>
      <form id="pf" class="inline"><label>Ngày <input type="date" name="day" required></label><button class="primary">Lập theo lịch đã xếp</button></form>
      <form id="mf" class="inline" style="margin-top:10px"><label>Khối <select name="block_id">${["block-1", "block-2", "block-3", "block-4"].map((b) => `<option value="${b}">${b.replace("block-", "Khối ")}</option>`).join("")}</select></label>
        <label>Từ <input type="datetime-local" name="starts_at" required></label><label>Đến <input type="datetime-local" name="ends_at" required></label><button>Lập thủ công</button></form></div>` : ""}
      <div id="acts"></div></section></div>`;
  if (canManage) {
    document.getElementById("pf").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try { const r = await api("/v1/airspace/activations/plan", { method: "POST", body: { day: ev.target.day.value } });
            toast(`Đã lập ${r.created.length} kế hoạch${r.skipped.length ? `, bỏ qua ${r.skipped.length}` : ""}`); } catch (e) { toast(e.message); }
    });
    document.getElementById("mf").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const f = ev.target, iso = (v) => new Date(`${v}:00+07:00`).toISOString();
      try { await api("/v1/airspace/activations", { method: "POST", body: { block_id: f.block_id.value, starts_at: iso(f.starts_at.value), ends_at: iso(f.ends_at.value) } }); toast("Đã lập kế hoạch"); }
      catch (e) { toast(e.message); }
    });
  }
  await refresh();
  stream(lastSeq);
}

start().catch((e) => { main.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; });
