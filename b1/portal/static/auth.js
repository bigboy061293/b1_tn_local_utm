// Đăng nhập OIDC Authorization Code + PKCE (RFC 7636) với client công khai, không cần thư viện ngoài.
const KEY = "b1.portal.session";
let cfg, meta, session = JSON.parse(sessionStorage.getItem(KEY) || "null");

const b64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const rand = (n = 32) => b64url(crypto.getRandomValues(new Uint8Array(n)));
const redirectUri = () => `${location.origin}/portal/`;

function save(tok) {
  session = { access: tok.access_token, refresh: tok.refresh_token, id: tok.id_token || session?.id,
              exp: Date.now() + (tok.expires_in - 30) * 1000 };
  sessionStorage.setItem(KEY, JSON.stringify(session));
}

async function tokenRequest(params) {
  const r = await fetch(meta.token_endpoint, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
                                               body: new URLSearchParams({ client_id: cfg.client_id, ...params }) });
  if (!r.ok) throw new Error(`đăng nhập lỗi ${r.status}`);
  save(await r.json());
}

export async function init() {
  cfg = await (await fetch("/portal/config.json")).json();
  meta = await (await fetch(`${cfg.issuer}/.well-known/openid-configuration`)).json();
  const q = new URLSearchParams(location.search);
  if (q.get("code")) {
    const st = JSON.parse(sessionStorage.getItem(KEY + ".pkce") || "{}");
    sessionStorage.removeItem(KEY + ".pkce");
    if (q.get("state") !== st.state) throw new Error("state không khớp, thử đăng nhập lại");
    await tokenRequest({ grant_type: "authorization_code", code: q.get("code"), redirect_uri: redirectUri(), code_verifier: st.verifier });
    history.replaceState(null, "", `/portal/${st.hash || ""}`);
  }
  return !!session;
}

export async function login() {
  const verifier = rand(48), state = rand(16);
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  sessionStorage.setItem(KEY + ".pkce", JSON.stringify({ verifier, state, hash: location.hash }));
  const u = new URL(meta.authorization_endpoint);
  Object.entries({ response_type: "code", client_id: cfg.client_id, redirect_uri: redirectUri(), scope: "openid profile",
                   state, code_challenge: challenge, code_challenge_method: "S256", ui_locales: "vi" })
    .forEach(([k, v]) => u.searchParams.set(k, v));
  location.assign(u);
}

export function logout() {
  const id = session?.id;
  sessionStorage.removeItem(KEY);
  session = null;
  const u = new URL(meta.end_session_endpoint);
  u.searchParams.set("client_id", cfg.client_id);
  u.searchParams.set("post_logout_redirect_uri", redirectUri());
  if (id) u.searchParams.set("id_token_hint", id);
  location.assign(u);
}

export async function accessToken() {
  if (!session) return null;
  if (Date.now() > session.exp) {
    try { await tokenRequest({ grant_type: "refresh_token", refresh_token: session.refresh }); }
    catch { sessionStorage.removeItem(KEY); session = null; return null; }
  }
  return session.access;
}
