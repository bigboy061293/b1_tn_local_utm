// Bản đồ trực tiếp (F-05) và cảnh báo (F-06): chuyến đang bay, vết ADS-B, Broadcast Remote ID, trạng thái trạm, cảnh báo.
// Vẽ SVG từ hình học khối, không cần nền bản đồ ngoài. Đọc luồng SSE /v1/live/stream: cảnh báo đến ngay, vị trí 5 lần/giây.
import { accessToken, init, login, logout } from "./auth.js";

const main = document.getElementById("main");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hhmmss = (t) => t ? new Date(t).toLocaleTimeString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", hour12: false }) : "—";
const SEV = { critical: ["Nguy cấp", "bad"], warning: ["Cảnh báo", "warn"], info: ["Thông tin", ""] };
const KIND = { LATERAL_DEVIATION: "Lệch tuyến", BOUNDARY: "Vượt ranh", CEILING: "Vượt trần", C2_LINK: "Đường truyền điều khiển",
  LOW_BATTERY: "Năng lượng", SAFETY_FLAG: "Cờ an toàn", TELEMETRY_STALE: "Mất telemetry", HANDOVER: "Chuyển vùng phủ",
  RID_MISMATCH: "Remote ID lệch", RID_UNKNOWN: "Remote ID lạ", TRAFFIC: "Tàu bay có người lái", STATION: "Trạm RIS" };
const LEVEL = { L0: ["Bình thường", "ok"], L1: ["L1 4G/5G", "warn"], L2: ["L2 mất trạm", "bad"], L4: ["L4 lưu điện", "warn"], unknown: ["Chưa có dữ liệu", ""] };
function toast(msg) { const d = document.createElement("div"); d.textContent = msg; document.getElementById("toast").append(d); setTimeout(() => d.remove(), 5000); }

async function api(path, opts = {}) {
  const tok = await accessToken();
  if (!tok) { await login(); throw new Error("cần đăng nhập"); }
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok}` },
                                body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new Error(data?.detail || `lỗi ${r.status}`);
  return data;
}

let me, canAck = false, P, blocks = [], line = null, live = { flights: [], adsb: [], rid: [], stations: [] };
const alerts = new Map();   // id -> cảnh báo đang mở
window.__b1AlertLatency = []; // ms từ giờ nguồn và từ lúc B1 nhận tới lúc hiện trên màn hình (đo bằng Playwright)

function projection(geoms) {
  const pts = geoms.flatMap((g) => g.type === "Polygon" ? g.coordinates[0] : g.coordinates);
  const lons = pts.map((p) => p[0]), lats = pts.map((p) => p[1]);
  const lat0 = (Math.min(...lats) + Math.max(...lats)) / 2, k = Math.cos(lat0 * Math.PI / 180);
  const minX = Math.min(...lons) * k, maxY = Math.max(...lats);
  const w = (Math.max(...lons) - Math.min(...lons)) * k, h = maxY - Math.min(...lats);
  const s = 560 / Math.max(w, h), pad = 24;
  return { f: (lon, lat) => [pad + (lon * k - minX) * s, pad + (maxY - lat) * s], mPerUnit: 111_320 / s, W: w * s + 2 * pad, H: h * s + 2 * pad };
}

function base() {
  P = projection([...blocks.map((b) => b.geometry), line.geometry]);
  const d = (ring, close) => ring.map((p, i) => `${i ? "L" : "M"}${P.f(p[0], p[1]).map((v) => v.toFixed(1)).join(",")}`).join("") + (close ? "Z" : "");
  const band = (m) => (m / P.mPerUnit).toFixed(2);
  document.getElementById("mapwrap").innerHTML = `<svg id="map" viewBox="0 0 ${P.W.toFixed(0)} ${P.H.toFixed(0)}" role="img" aria-label="Bản đồ trực tiếp">
    ${blocks.map((b) => `<path class="blk" data-status="${b.status}" d="${d(b.geometry.coordinates[0], true)}"><title>${esc(b.name)}</title></path>`).join("")}
    <path class="band contingency" stroke-width="${band(2 * line.properties.contingency_half_width_m)}" d="${d(line.geometry.coordinates)}"/>
    <path class="band normal" stroke-width="${band(2 * line.properties.normal_half_width_m)}" d="${d(line.geometry.coordinates)}"/>
    <path class="centerline" d="${d(line.geometry.coordinates)}"/>
    <g id="stations"></g><g id="tracks"></g><g id="flights"></g></svg>`;
}

function drawLive() {
  if (!P) return;
  document.getElementById("stations").innerHTML = live.stations.filter((s) => s.lon != null).map((s) => {
    const [x, y] = P.f(s.lon, s.lat);
    return `<g class="stn" data-level="${s.level}"><rect x="${x - 5}" y="${y - 5}" width="10" height="10"/><text x="${x + 8}" y="${y + 4}">${esc(s.station_id)}</text>
      <title>${esc(s.station_id)}: ${esc((LEVEL[s.level] || [s.level])[0])}</title></g>`;
  }).join("");
  document.getElementById("tracks").innerHTML = [
    ...live.adsb.map((t) => { const [x, y] = P.f(t.lon, t.lat); const r = t.uncertainty_m / P.mPerUnit;
      return `<g class="adsb ${t.status}" transform="translate(${x.toFixed(1)},${y.toFixed(1)})">${r > 1 ? `<circle class="unc" r="${r.toFixed(1)}"/>` : ""}
        <path d="M0,-6L6,0L0,6L-6,0Z"/><text x="8" y="-6">${esc(t.callsign || t.key)} ${t.alt != null ? Math.round(t.alt) + " m" : ""}</text>
        <title>ADS-B ${esc(t.key)} ${esc(t.callsign || "")}; ${t.stations.length} trạm thu: ${esc(t.stations.join(", "))}; ${t.age_s} giây trước</title></g>`; }),
    ...live.rid.filter((t) => !t.flight_id).map((t) => { const [x, y] = P.f(t.lon, t.lat);
      return `<g class="rid" transform="translate(${x.toFixed(1)},${y.toFixed(1)})"><circle r="3.5"/><text x="6" y="12">${esc(t.key)}</text></g>`; }),
  ].join("");
  document.getElementById("flights").innerHTML = live.flights.map((f) => {
    const [x, y] = P.f(f.lon, f.lat);
    const bad = [...alerts.values()].some((a) => a.flight_id === f.flight_id && a.severity === "critical");
    return `<g class="ac ${f.conformance} ${bad ? "alarm" : ""} ${f.age_s > 2 ? "stale" : ""}" data-flight="${f.flight_id}" transform="translate(${x.toFixed(1)},${y.toFixed(1)})">
      <path transform="rotate(${f.track || 0})" d="M0,-9L6,7L0,3L-6,7Z"/><text x="10" y="4">${esc(f.request_ref || f.uas_id)}</text>
      <title>${esc(f.uas_id)}: Km ${f.km}, lệch ${f.lateral_m} m, ${f.alt_agl != null ? Math.round(f.alt_agl) + " m so với mặt đất" : ""}</title></g>`;
  }).join("");
  document.getElementById("flt").innerHTML = live.flights.length ? `<table><tr><th>Chuyến</th><th>Km</th><th>Lệch</th><th>Độ cao</th><th>Pin</th><th>Đường truyền</th><th>Trạm</th><th>Mẫu</th></tr>
    ${live.flights.map((f) => `<tr class="${f.conformance === "nonconforming" ? "nc" : ""}"><td>${esc(f.request_ref || "")}<br><small>${esc(f.uas_id)}</small></td><td>${f.km.toFixed(2)}</td>
      <td>${Math.round(f.lateral_m)} m</td><td>${f.alt_agl != null ? Math.round(f.alt_agl) : "—"} m</td><td>${f.battery_pct != null ? Math.round(f.battery_pct) + "%" : "—"}</td>
      <td>${esc(f.c2)}</td><td>${esc(f.station)}</td><td>${f.age_s.toFixed(1)} s</td></tr>`).join("")}</table>` : `<p class="muted">Không có chuyến đang gửi telemetry.</p>`;
  document.getElementById("stn").innerHTML = live.stations.map((s) => { const [t, c] = LEVEL[s.level] || [s.level, ""]; const w = s.weather || {};
    return `<div class="stnrow"><strong>${esc(s.station_id)}</strong> <span class="badge ${c}">${esc(t)}</span>
      <small>${w.windSpeedMs != null ? `gió ${w.windSpeedMs} m/s, giật ${w.windGustMs ?? "—"} m/s (${esc(w.kind)})` : ""}</small></div>`; }).join("");
}

function drawAlerts() {
  const list = [...alerts.values()].sort((a, b) => (b.severity === "critical") - (a.severity === "critical") || b.raised_at.localeCompare(a.raised_at));
  document.getElementById("alerts").innerHTML = list.length ? list.map((a) => { const [t, c] = SEV[a.severity];
    return `<div class="alertrow ${c}" data-id="${a.id}" data-kind="${a.kind}"><div><span class="badge ${c}">${esc(t)}</span> <strong>${esc(KIND[a.kind] || a.kind)}</strong>
      ${a.state === "acknowledged" ? `<span class="badge">Đã xác nhận</span>` : ""} <small>${hhmmss(a.raised_at)}</small></div>
      <div>${esc(a.message)}</div>
      ${canAck ? `<div class="row">${a.state === "open" ? `<button data-ack="${a.id}">Xác nhận</button>` : ""}<button data-res="${a.id}">Đóng</button></div>` : ""}</div>`; }).join("")
    : `<p class="muted">Không có cảnh báo đang mở.</p>`;
}

function onAlertEvent(e) {
  const a = e.alert;
  if (a.resolved_at) alerts.delete(a.id); else alerts.set(a.id, a);
  drawAlerts();
  if (e.kind === "alert.raised" || e.kind === "alert.escalated") {
    const shown = Date.now();
    const rec = { kind: a.kind, severity: a.severity, from_source_ms: a.t_source ? shown - Date.parse(a.t_source) : null,
                  from_receive_ms: a.t_receive ? shown - Date.parse(a.t_receive) : null };
    window.__b1AlertLatency.push(rec);
    if (a.severity !== "info") document.getElementById("lat").textContent =
      `Cảnh báo gần nhất: ${KIND[a.kind] || a.kind}, hiện sau ${rec.from_source_ms ?? "—"} ms tính từ giờ nguồn (${rec.from_receive_ms ?? "—"} ms từ lúc B1 nhận)`;
  }
}

async function stream() {
  const el = document.getElementById("live");
  let after = null;
  for (;;) {
    try {
      const tok = await accessToken();
      const r = await fetch(`/v1/live/stream${after != null ? `?after=${after}` : ""}`, { headers: { Authorization: `Bearer ${tok}` } });
      if (!r.ok) throw new Error(`luồng lỗi ${r.status}`);
      el.classList.add("on");
      el.querySelector("span").textContent = "Đang nhận dữ liệu trực tiếp";
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
          if (ev === "hello") after = d.seq;
          else if (ev === "alert") { after = d.seq; onAlertEvent(d); drawLive(); }
          else if (ev === "live") { live = d; drawLive(); }
        }
      }
    } catch (e) { el.querySelector("span").textContent = `Mất luồng: ${e.message}. Thử lại…`; }
    el.classList.remove("on");
    await new Promise((res) => setTimeout(res, 2000));
  }
}

async function start() {
  const d = await (await fetch("/v1/disclaimers")).json();
  document.getElementById("disclaimers").innerHTML = `<ul>${d.items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  if (!(await init())) {
    main.innerHTML = `<div class="login card"><h1>Bản đồ trực tiếp</h1><p>Dành cho cán bộ bộ phận quản lý hành lang.</p><button class="primary" id="lg">Đăng nhập</button></div>`;
    document.getElementById("lg").addEventListener("click", login);
    return;
  }
  me = await api("/v1/me");
  canAck = (me.roles.includes("corridor_head") || me.roles.includes("duty_safety_officer")) && me.mfa;
  document.getElementById("who").innerHTML = `<span>${esc(me.display_name)}</span><button id="lo">Đăng xuất</button>`;
  document.getElementById("lo").addEventListener("click", logout);
  main.innerHTML = `<div class="opsgrid live"><section class="card mapcard"><div id="mapwrap"></div>
      <div class="legend"><span><i class="lg-ok"></i>Chuyến tuân thủ</span><span><i class="lg-bad"></i>Không tuân thủ hoặc nguy cấp</span>
      <span><i class="lg-adsb"></i>ADS-B</span><span><i class="lg-rid"></i>Remote ID lạ</span><span><i class="lg-normal"></i>Vùng bình thường ±500 m</span>
      <span><i class="lg-cont"></i>Vùng dự phòng ±750 m</span></div>
      <div class="live" id="live"><i class="dot"></i><span>Đang nối luồng…</span></div><p class="muted" id="lat" style="font-size:12px"></p>
      <p class="muted" style="font-size:12px">Chỉ nhận biết giao thông hợp tác (telemetry, Remote ID, ADS-B). Ngưỡng vùng là giá trị demo (D6).</p></section>
    <section><div class="card"><h2 style="margin-top:0">Cảnh báo đang mở</h2><div id="alerts"></div></div>
      <div class="card"><h2 style="margin-top:0">Chuyến đang bay</h2><div id="flt"></div></div>
      <div class="card"><h2 style="margin-top:0">Trạm RIS</h2><div id="stn"></div></div></section></div>`;
  main.addEventListener("click", async (ev) => {
    const id = ev.target.dataset.ack || ev.target.dataset.res;
    if (!id) return;
    try { const a = await api(`/v1/alerts/${id}/acknowledge`, { method: "POST", body: { resolve: !!ev.target.dataset.res } });
          if (a.resolved_at) alerts.delete(a.id); else alerts.set(a.id, a); drawAlerts(); } catch (e) { toast(e.message); }
  });
  const [st, cl, open, snap] = await Promise.all([api("/v1/airspace/state"), api("/v1/surveillance/centerline"),
                                                  api("/v1/alerts?open=true&limit=500"), api("/v1/live")]);
  const geo = await api("/v1/airspace/blocks");
  blocks = geo.items.map((b) => ({ ...b, status: (st.blocks.find((x) => x.id === b.id) || {}).status }));
  line = cl;
  open.items.forEach((a) => alerts.set(a.id, a));
  live = snap;
  base(); drawLive(); drawAlerts();
  stream();
}

start().catch((e) => { main.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; });
