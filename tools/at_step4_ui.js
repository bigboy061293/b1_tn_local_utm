// Đo độ trễ cảnh báo trên màn hình vận hành /ops/live bằng Playwright (bước 4, bản sao số).
// node tools/at_step4_ui.js <base> <tệp token> <số giây> <ảnh chụp>
// Token phát triển đặt sẵn vào phiên của trang; tài liệu OIDC của bên cấp danh tính được giả lập vì bản sao số không chạy Keycloak.
const { chromium } = require("playwright");
const fs = require("fs");
(async () => {
  const [base, tokFile, secs, shot] = process.argv.slice(2);
  const tok = fs.readFileSync(tokFile, "utf8").trim();
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  await ctx.addInitScript((t) => sessionStorage.setItem("b1.ops.session", JSON.stringify({ access: t, exp: Date.now() + 3 * 3600e3 })), tok);
  await ctx.route("**/.well-known/openid-configuration", (r) => r.fulfill({ json: {
    authorization_endpoint: "https://idp.invalid/auth", token_endpoint: "https://idp.invalid/token", end_session_endpoint: "https://idp.invalid/logout" } }));
  const p = await ctx.newPage();
  const errs = [];
  p.on("pageerror", (e) => errs.push(String(e)));
  await p.goto(`${base}/ops/live`);
  await p.waitForSelector("#map");
  const t0 = Date.now();
  let shotDone = false;
  while (Date.now() - t0 < Number(secs) * 1000) {
    await p.waitForTimeout(1000);
    if (!shotDone && await p.$(".alertrow.bad") && await p.$("#map .ac")) {
      await p.waitForTimeout(300);
      await p.screenshot({ path: shot });
      shotDone = true;
    }
  }
  if (!shotDone) await p.screenshot({ path: shot });
  const lat = (await p.evaluate(() => window.__b1AlertLatency)).filter((x) => x.severity !== "info" && x.from_source_ms != null);
  const q = (xs, f) => { const s = [...xs].sort((a, c) => a - c); return s.length ? s[Math.min(s.length - 1, Math.round(f * (s.length - 1)))] : null; };
  const src = lat.map((x) => x.from_source_ms), rcv = lat.map((x) => x.from_receive_ms);
  console.log(JSON.stringify({ n: lat.length, from_source: { p50_ms: q(src, 0.5), p95_ms: q(src, 0.95), max_ms: q(src, 1) },
    from_b1_receive: { p50_ms: q(rcv, 0.5), p95_ms: q(rcv, 0.95), max_ms: q(rcv, 1) }, page_errors: errs, screenshot: shot }));
  await b.close();
})();
