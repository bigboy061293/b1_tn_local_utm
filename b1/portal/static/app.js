import { init, login, logout, accessToken } from "./auth.js";

// ---------- tiện ích ----------
const $ = (s, el = document) => el.querySelector(s);
const main = $("#main");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (d) => d ? new Date(d).toLocaleDateString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }) : "";
const fmtTime = (d) => d ? new Date(d).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit", year: "numeric" }) : "";
const isoDay = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const addDays = (s, n) => { const d = new Date(`${s}T00:00:00`); d.setDate(d.getDate() + n); return isoDay(d); };
const toast = (msg) => { const d = document.createElement("div"); d.textContent = msg; $("#toast").append(d); setTimeout(() => d.remove(), 5000); };

const DOC_KINDS = {
  registration_certificate: "Giấy chứng nhận đăng ký", temporary_registration: "Đăng ký tạm thời",
  technical_status_certificate: "Giấy xác nhận tình trạng kỹ thuật", pilot_licence: "Giấy phép người điều khiển",
  test_permit: "Giấy phép thử nghiệm", flight_permit: "Phép bay", insurance: "Bảo hiểm",
};
const STATUS = {
  provisional: ["Số tạm, hồ sơ chưa đủ", "warn"], queued: ["Đang chờ xếp lịch", ""], scheduled: ["Đã xếp lịch", "ok"],
  partial: ["Xếp được một phần", "warn"], not_scheduled: ["Không xếp được", "bad"], expired: ["Hết hạn giữ chỗ", "bad"],
  cancelled: ["Đã huỷ", "bad"], requested: ["Đang chờ", ""], submitted: ["Đã nộp, chờ xác nhận", "warn"],
  verified: ["Đã xác nhận", "ok"], rejected: ["Bị từ chối", "bad"],
};
const REASON = { BLOCK_TAKEN: "Khối đã xếp cho yêu cầu ưu tiên hơn", CORRIDOR_FULL: "Hành lang đủ 2 chuyến", BLOCK_CLOSED: "Khối chưa mở",
  INELIGIBLE: "Hồ sơ chưa đủ", SLOT_PASSED: "Đã qua khung", FINAL_CHECK_FAILED: "Không qua xác nhận cuối T − 3",
  CUSTOMER_CANCELLED: "Khách huỷ", HOLD_EXPIRED: "Hết hạn giữ chỗ" };
const RULE = { OWNERSHIP: "Phương tiện, người điều khiển không hợp lệ", REGISTRATION: "Đăng ký phương tiện",
  TECHNICAL_STATUS: "Tình trạng kỹ thuật", PILOT_LICENCE: "Giấy phép người điều khiển", FLIGHT_MGMT_DEVICE: "Thiết bị quản lý hoạt động bay",
  PERMIT: "Giấy phép thử nghiệm hoặc phép bay", CONFORMANCE: "CT-1…CT-8", INSURANCE: "Bảo hiểm" };
const badge = (s) => { const [t, c] = STATUS[s] || [s, ""]; return `<span class="badge ${c}">${esc(t)}</span>`; };

async function api(path, opts = {}) {
  const tok = await accessToken();
  if (!tok) { await login(); throw new Error("cần đăng nhập"); }
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok}`, ...(opts.headers || {}) },
                                body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  const data = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) {
    const d = data?.detail;
    throw new Error(Array.isArray(d) ? d.map((x) => `${x.loc?.slice(-1)[0]}: ${x.msg}`).join("; ") : d || `lỗi ${r.status}`);
  }
  return data;
}
const formData = (form) => Object.fromEntries([...new FormData(form)].filter(([, v]) => v !== ""));
const missingList = (missing) => `<ul>${missing.map((m) => `<li><strong>${esc(m.message)}</strong> <span class="mono">${esc(m.detail)}</span></li>`).join("")}</ul>`;

// ---------- dữ liệu dùng chung ----------
let me, customer, params;
async function lists() {
  const [ac, pi] = await Promise.all([api("/v1/registry/aircraft"), api("/v1/registry/pilots")]);
  return { aircraft: ac.items, pilots: pi.items };
}

// ---------- trang ----------
const pages = {
  async "/"() {
    const [n, r] = await Promise.all([api("/v1/notifications"), api("/v1/scheduling/requests")]);
    const active = r.items.filter((x) => !["cancelled", "expired"].includes(x.status));
    main.innerHTML = `<h1>Xin chào, ${esc(customer.name)}</h1>
      <div class="grid2">
        <div class="card"><div class="muted">Hạng khách hàng</div><div style="font-size:24px;font-weight:700">${esc(customer.tier)}</div></div>
        <div class="card"><div class="muted">Yêu cầu đang theo dõi</div><div style="font-size:24px;font-weight:700">${active.length}</div></div>
        <div class="card"><div class="muted">Thông báo chưa đọc</div><div style="font-size:24px;font-weight:700">${n.items.filter((x) => !x.read_at).length}</div></div>
      </div>
      <h2>Thông báo</h2>
      ${n.items.length ? n.items.map((x) => `<div class="notif ${x.read_at ? "read" : ""}" data-id="${x.id}">
          <div class="t">${esc(x.title)}</div><div>${esc(x.body)}</div><small>${fmtTime(x.created_at)}</small></div>`).join("")
        : `<p class="muted">Chưa có thông báo.</p>`}`;
    main.querySelectorAll(".notif:not(.read)").forEach((el) => el.addEventListener("click", async () => {
      await api(`/v1/notifications/${el.dataset.id}/read`, { method: "POST" }); el.classList.add("read");
    }));
  },

  async "/new"() {
    const { aircraft, pilots } = await lists();
    const start = addDays(isoDay(new Date()), 1);
    main.innerHTML = `<h1>Đặt lịch bay</h1>
      <form class="card" id="f">
        <div class="grid2">
          <div><label>Phương tiện bay</label><select name="aircraft_id" required>${aircraft.map((a) => `<option value="${a.id}">${esc(a.model || "")} · ${esc(a.uas_id)}</option>`).join("")}</select></div>
          <div><label>Người điều khiển</label><select name="pilot_id" required>${pilots.map((p) => `<option value="${p.id}">${esc(p.full_name)}</option>`).join("")}</select></div>
          <div><label>Nhóm ưu tiên (Phụ lục 05)</label><select name="priority_group">${[1, 2, 3, 4].map((g) => `<option ${g === 3 ? "selected" : ""}>${g}</option>`).join("")}</select></div>
          <div><label>Hình thức bay</label><div class="inline"><label><input type="checkbox" name="bvlos" checked> Ngoài tầm nhìn</label></div></div>
        </div>
        <h2>Khối</h2>
        <div class="inline">${["block-1", "block-2", "block-3", "block-4"].map((b, i) => {
          const open = params.open_blocks.includes(b);
          return `<label><input type="checkbox" name="block" value="${b}" ${open ? "" : "disabled"} ${i === 0 && open ? "checked" : ""}> Khối ${i + 1}${open ? "" : " (chưa mở)"}</label>`; }).join("")}</div>
        <h2>Khung bay</h2>
        <div class="inline">
          <div><label>Từ ngày</label><input type="date" name="from" value="${start}" min="${start}" max="${addDays(start, params.booking_window_days - 1)}"></div>
          <div><label>Số ngày (tối đa ${params.max_request_days})</label><select name="ndays">${[1, 2, 3, 4, 5].slice(0, params.max_request_days).map((n) => `<option>${n}</option>`).join("")}</select></div>
        </div>
        <p class="muted">Chọn khung trong bảng. Mỗi khung ${params.slot_hours} giờ. Ô xám là khối đã kín.</p>
        <div id="grid"></div>
        <div id="check"></div>
        <p><button class="primary" id="submit" type="submit">Gửi yêu cầu</button></p>
      </form><div id="result"></div>`;
    const f = $("#f"), picked = new Set();
    const blocks = () => [...f.querySelectorAll("input[name=block]:checked")].map((x) => x.value);
    async function grid() {
      const from = f.from.value, to = addDays(from, Number(f.ndays.value) - 1);
      const av = (await api(`/v1/scheduling/availability?start=${from}&end=${to}`)).items;
      const days = [...new Set(av.map((x) => x.day))];
      for (const k of [...picked]) if (!days.includes(k.split("|")[0])) picked.delete(k);
      $("#grid").innerHTML = `<table class="slots"><tr><th></th>${params.slot_starts.map((s, i) => `<th>Khung ${i + 1}<br><span class="muted">${s}</span></th>`).join("")}</tr>
        ${days.map((d) => `<tr><th>${fmtDate(`${d}T12:00:00`)}</th>${av.filter((x) => x.day === d).map((x) => {
          const k = `${d}|${x.slot}`, free = blocks().every((b) => x.free_blocks.includes(b));
          if (!free) picked.delete(k);
          return `<td><button type="button" class="slot ${free ? "free" : "taken"} ${picked.has(k) ? "picked" : ""}" data-k="${k}" ${free ? "" : "disabled"}>
            ${picked.has(k) ? "Đã chọn" : free ? "Còn trống" : "Đã kín"}${x.waiting_items ? `<br><small>${x.waiting_items} đang chờ</small>` : ""}</button></td>`; }).join("")}</tr>`).join("")}</table>`;
      $("#grid").querySelectorAll(".slot.free").forEach((b) => b.addEventListener("click", () => { picked.has(b.dataset.k) ? picked.delete(b.dataset.k) : picked.add(b.dataset.k); grid(); }));
      check();
    }
    async function check() {
      const box = $("#check");
      if (!picked.size) { box.innerHTML = ""; return; }
      const days = [...picked].map((k) => k.split("|")[0]).sort();
      const q = new URLSearchParams({ aircraft_id: f.aircraft_id.value, pilot_id: f.pilot_id.value, bvlos: f.bvlos.checked,
        start: `${days[0]}T00:00:00+07:00`, end: `${days.at(-1)}T23:59:59+07:00` });
      const e = await api(`/v1/registry/eligibility?${q}`);
      box.innerHTML = e.eligible ? `<div class="alert ok">Hồ sơ đủ điều kiện cho các ngày đã chọn. Yêu cầu sẽ nhận số thứ tự chính thức.</div>`
        : `<div class="alert warn">Hồ sơ còn thiếu. Vẫn gửi được: yêu cầu nhận số tạm và được giữ chỗ ${params.provisional_hold_workdays} ngày làm việc để bổ sung.${missingList(e.missing)}</div>`;
    }
    ["from", "ndays", "aircraft_id", "pilot_id", "bvlos"].forEach((n) => f[n].addEventListener("change", n === "from" || n === "ndays" ? grid : check));
    f.querySelectorAll("input[name=block]").forEach((x) => x.addEventListener("change", grid));
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!picked.size) return toast("Chọn ít nhất một khung bay");
      const body = { aircraft_id: f.aircraft_id.value, pilot_id: f.pilot_id.value, priority_group: Number(f.priority_group.value),
        bvlos: f.bvlos.checked, block_ids: blocks(), items: [...picked].map((k) => ({ day: k.split("|")[0], slot: Number(k.split("|")[1]) })) };
      $("#submit").disabled = true;
      try {
        const r = await api("/v1/scheduling/requests", { method: "POST", body });
        $("#result").innerHTML = r.provisional
          ? `<div class="alert warn"><strong>Đã nhận, cấp số tạm ${esc(r.queue_no)}.</strong> Hồ sơ còn thiếu, cần bổ sung trước ${fmtTime(r.hold_until)}.${missingList(r.missing)}</div>`
          : `<div class="alert ok"><strong>Đã nhận, số thứ tự ${esc(r.queue_no)}</strong> lúc ${fmtTime(r.queue_at)}. Vị trí trong hàng chờ: ${r.queue_position}. Kết quả xếp lịch có sau kỳ xếp lịch 16:00.</div>`;
        picked.clear(); grid();
      } catch (e) { $("#result").innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; }
      finally { $("#submit").disabled = false; }
    });
    grid();
  },

  async "/requests"() {
    const r = await api("/v1/scheduling/requests");
    main.innerHTML = `<h1>Yêu cầu của tôi</h1>${r.items.length ? `<table><tr><th>Số thứ tự</th><th>Nhận lúc</th><th>Khối</th><th>Trạng thái</th></tr>
      ${r.items.slice().reverse().map((x) => `<tr class="link" data-id="${x.id}"><td class="mono">${esc(x.queue_no)}</td><td>${fmtTime(x.queue_at)}</td>
      <td>${esc(x.block_ids.join(", "))}</td><td>${badge(x.status)}</td></tr>`).join("")}</table>` : `<p class="muted">Chưa có yêu cầu. <a href="#/new">Đặt lịch bay</a></p>`}`;
    main.querySelectorAll("tr.link").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/requests/${tr.dataset.id}`; }));
  },

  async "/requests/:id"(id) {
    const x = await api(`/v1/scheduling/requests/${id}`);
    const canCancel = !["cancelled", "expired"].includes(x.status);
    main.innerHTML = `<h1>Yêu cầu <span class="mono">${esc(x.queue_no)}</span> ${badge(x.status)}</h1>
      <div class="card grid2">
        <div><label>Nhận lúc</label>${fmtTime(x.queue_at)}</div>
        <div><label>Phương tiện</label><span class="mono">${esc(x.uas_id)}</span></div>
        <div><label>Nhóm ưu tiên · khối</label>${x.priority_group} · ${esc(x.block_ids.join(", "))}</div>
        <div><label>Vị trí trong hàng chờ</label>${x.queue_position ?? "—"}</div>
        ${x.provisional ? `<div><label>Hạn bổ sung hồ sơ</label>${fmtTime(x.hold_until)}</div>` : ""}
      </div>
      <h2>Khung bay</h2>
      <table><tr><th>Ngày</th><th>Giờ</th><th>Trạng thái</th><th>Ghi chú</th></tr>
      ${x.items.map((i) => `<tr><td>${fmtDate(i.starts_at)}</td><td>${fmtTime(i.starts_at).split(" ")[0]} – ${fmtTime(i.ends_at).split(" ")[0]}</td>
        <td>${badge(i.status)}</td><td>${esc(REASON[i.last_reason] || i.last_reason || "")}</td></tr>`).join("")}</table>
      ${x.block_reasons.length ? `<h2>Lịch sử chặn</h2><table><tr><th>Lúc</th><th>Giai đoạn</th><th>Quy tắc</th><th>Chi tiết</th></tr>
        ${x.block_reasons.slice().reverse().map((b) => `<tr><td>${fmtTime(b.at)}</td><td>${esc({ submit: "Khi nộp", recheck: "Kiểm lại", run: "Kỳ xếp lịch", final: "Xác nhận T − 3" }[b.stage] || b.stage)}</td>
        <td>${esc(RULE[b.rule] || b.rule)}</td><td>${esc(b.detail || "")}</td></tr>`).join("")}</table>` : ""}
      <p class="inline">${x.provisional ? `<button class="primary" id="recheck">Kiểm lại hồ sơ</button>` : ""}
        ${canCancel ? `<button class="danger" id="cancel">Huỷ yêu cầu</button>` : ""}</p><div id="msg"></div>`;
    $("#recheck")?.addEventListener("click", async () => {
      const r = await api(`/v1/scheduling/requests/${id}/recheck`, { method: "POST" });
      if (r.provisional) $("#msg").innerHTML = `<div class="alert warn">Hồ sơ vẫn thiếu.${missingList(r.missing)}</div>`;
      else { toast(`Đã nhận số chính thức ${r.queue_no}`); pages["/requests/:id"](id); }
    });
    $("#cancel")?.addEventListener("click", async () => {
      if (!window.confirm("Huỷ yêu cầu này?")) return;
      await api(`/v1/scheduling/requests/${id}/cancel`, { method: "POST" }); toast("Đã huỷ yêu cầu"); pages["/requests/:id"](id);
    });
  },

  async "/aircraft"() {
    const { aircraft } = await lists();
    main.innerHTML = `<h1>Phương tiện bay</h1>
      <table><tr><th>UAS ID</th><th>Số đăng ký VN</th><th>Mẫu</th><th>MTOW · tốc độ</th><th>Thiết bị quản lý hoạt động bay</th><th>Cấu hình</th><th></th></tr>
      ${aircraft.map((a) => `<tr><td class="mono">${esc(a.uas_id)}</td><td class="mono">${esc(a.vn_registration_no || "—")}</td><td>${esc(a.model || "")}</td>
        <td>${a.mtow_kg} kg · ${a.max_speed_ms} m/s</td><td>${esc(a.flight_mgmt_device_id || "—")} ${a.flight_mgmt_device_id ? (a.flight_mgmt_device_verified ? badge("verified") : badge("submitted")) : ""}</td>
        <td class="mono">${esc(a.config_ref)}</td><td>${a.vn_registration_no ? `<button data-verify="${a.id}">Tra I-02</button>` : ""}</td></tr>`).join("")}</table>
      <h2>Thêm phương tiện</h2>
      <form class="card" id="f"><div class="grid2">
        <div><label>UAS ID (số sê-ri ANSI/CTA-2063-A)</label><input name="uas_id" required minlength="4"></div>
        <div><label>Số đăng ký Việt Nam</label><input name="vn_registration_no"></div>
        <div><label>Mẫu</label><input name="model"></div>
        <div><label>Khối lượng cất cánh tối đa (kg)</label><input name="mtow_kg" type="number" step="0.1" min="0.1" required></div>
        <div><label>Tốc độ lớn nhất (m/s)</label><input name="max_speed_ms" type="number" step="0.1" min="0.1" required></div>
        <div><label>Thiết bị quản lý hoạt động bay</label><input name="flight_mgmt_device_id"></div>
        <div><label>Phiên bản phần mềm bay tự động, cấu hình vô tuyến</label><input name="config_ref" placeholder="fw-1.0/radio-A"></div>
        <div><label>Trạng thái</label><div class="inline"><label><input type="checkbox" name="in_testing" checked> Đang thử nghiệm</label></div></div>
      </div><p class="muted">Đổi cấu hình sau khi đã thử CT-1…8 sẽ làm kết quả cũ mất hiệu lực.</p><button class="primary">Thêm</button></form>`;
    main.querySelectorAll("[data-verify]").forEach((b) => b.addEventListener("click", async () => {
      const r = await api(`/v1/registry/aircraft/${b.dataset.verify}/verify-registration`, { method: "POST" });
      toast(r.verified ? "Đã xác nhận đăng ký qua I-02" : `Không xác nhận được: ${r.problems.join("; ")}`); pages["/aircraft"]();
    }));
    $("#f").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const d = formData(ev.target); d.in_testing = ev.target.in_testing.checked; d.mtow_kg = Number(d.mtow_kg); d.max_speed_ms = Number(d.max_speed_ms);
      try { await api("/v1/registry/aircraft", { method: "POST", body: d }); toast("Đã thêm phương tiện"); pages["/aircraft"](); }
      catch (e) { toast(e.message); }
    });
  },

  async "/pilots"() {
    const { pilots } = await lists();
    main.innerHTML = `<h1>Người điều khiển</h1>
      <table><tr><th>Họ tên</th><th>Mã định danh</th></tr>${pilots.map((p) => `<tr><td>${esc(p.full_name)}</td><td class="mono">${esc(p.unified_id || "—")}</td></tr>`).join("")}</table>
      <h2>Thêm người điều khiển</h2>
      <form class="card" id="f"><div class="grid2"><div><label>Họ tên</label><input name="full_name" required></div>
        <div><label>Mã định danh thống nhất</label><input name="unified_id"></div></div><p><button class="primary">Thêm</button></p></form>`;
    $("#f").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try { await api("/v1/registry/pilots", { method: "POST", body: formData(ev.target) }); toast("Đã thêm"); pages["/pilots"](); }
      catch (e) { toast(e.message); }
    });
  },

  async "/documents"() {
    const [{ aircraft, pilots }, docs] = await Promise.all([lists(), api("/v1/registry/documents")]);
    const owner = (d) => d.owner_type === "customer" ? customer.name
      : d.owner_type === "aircraft" ? (aircraft.find((a) => a.id === d.owner_id)?.uas_id || "") : (pilots.find((p) => p.id === d.owner_id)?.full_name || "");
    main.innerHTML = `<h1>Giấy tờ</h1>
      <table><tr><th>Loại</th><th>Số</th><th>Của</th><th>Hiệu lực</th><th>Trạng thái</th></tr>
      ${docs.items.map((d) => `<tr><td>${esc(DOC_KINDS[d.kind] || d.kind)}</td><td class="mono">${esc(d.number)}</td><td>${esc(owner(d))}</td>
        <td>${fmtDate(d.valid_from)} – ${d.valid_to ? fmtDate(d.valid_to) : "không thời hạn"}</td><td>${badge(d.status)} ${d.verified_via === "I-02" ? '<span class="badge">I-02</span>' : ""}</td></tr>`).join("")}</table>
      <h2>Nộp giấy tờ</h2>
      <form class="card" id="f"><div class="grid2">
        <div><label>Loại</label><select name="kind">${Object.entries(DOC_KINDS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></div>
        <div><label>Của</label><select name="owner">
          <option value="customer|${customer.id}">Khách hàng: ${esc(customer.name)}</option>
          ${aircraft.map((a) => `<option value="aircraft|${a.id}">Phương tiện: ${esc(a.uas_id)}</option>`).join("")}
          ${pilots.map((p) => `<option value="pilot|${p.id}">Người điều khiển: ${esc(p.full_name)}</option>`).join("")}</select></div>
        <div><label>Số</label><input name="number" required></div>
        <div><label>Hiệu lực từ</label><input name="valid_from" type="date"></div>
        <div><label>Hiệu lực đến</label><input name="valid_to" type="date"></div>
      </div><p class="muted">Giấy tờ được tính khi cán bộ hành lang đã xác nhận. Tải tệp lên sẽ có ở bản sau.</p><button class="primary">Nộp</button></form>`;
    $("#f").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const d = formData(ev.target); const [owner_type, owner_id] = d.owner.split("|"); delete d.owner;
      if (d.valid_from) d.valid_from = `${d.valid_from}T00:00:00+07:00`;
      if (d.valid_to) d.valid_to = `${d.valid_to}T23:59:59+07:00`;
      try { await api("/v1/registry/documents", { method: "POST", body: { ...d, owner_type, owner_id } }); toast("Đã nộp, chờ cán bộ xác nhận"); pages["/documents"](); }
      catch (e) { toast(e.message); }
    });
  },
};

// ---------- khung ----------
async function route() {
  const h = location.hash.replace(/^#/, "") || "/";
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.getAttribute("href") === `#${h}` || (h.startsWith("/requests/") && a.getAttribute("href") === "#/requests")));
  const m = h.match(/^\/requests\/([0-9a-f-]{36})$/);
  try { await (m ? pages["/requests/:id"](m[1]) : (pages[h] || pages["/"])()); main.focus(); }
  catch (e) { main.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; }
}

async function start() {
  const disc = await (await fetch("/v1/disclaimers")).json();
  $("#disclaimers").innerHTML = `<ul>${disc.items.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>`;
  let signedIn;
  try { signedIn = await init(); } catch (e) { main.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; return; }
  if (!signedIn) {
    $("#nav").style.visibility = "hidden";
    main.innerHTML = `<div class="login card"><h1>Cổng khách hàng B1</h1><p>Đặt lịch bay, theo dõi hồ sơ và kết quả xếp lịch tại hành lang thử nghiệm Tây Ninh.</p>
      <p><button class="primary" id="login">Đăng nhập</button></p></div>`;
    $("#login").addEventListener("click", login);
    return;
  }
  try {
    me = await api("/v1/me");
    customer = await api("/v1/registry/customers/me");
    params = await api("/v1/scheduling/params");
  } catch (e) {
    main.innerHTML = `<div class="alert bad">${esc(e.message)}. Tài khoản cần được bộ phận quản lý hành lang cấp và gắn với khách hàng.</div>`;
    $("#who").innerHTML = `<button id="out">Đăng xuất</button>`; $("#out").addEventListener("click", logout);
    return;
  }
  $("#who").innerHTML = `<span>${esc(me.display_name)} · ${esc(customer.name)}</span><button id="out">Đăng xuất</button>`;
  $("#out").addEventListener("click", logout);
  window.addEventListener("hashchange", route);
  route();
}
start();
