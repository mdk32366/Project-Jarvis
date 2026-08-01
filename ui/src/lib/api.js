// Central API client. Single place for base URL, JWT injection, and error
// handling. In dev, Vite proxies /api to the backend; in prod it's same-origin.

const TOKEN_KEY = "auth_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(status, message, detail = "") {
    super(message);
    this.status = status;
    // The backend's own words, kept alongside the human-facing message. Swallowing
    // this is what made the 2026-08-01 latch a misdiagnosis risk: the screen said
    // "Unauthorized" with total confidence and zero detail, so the real cause
    // (expiry, then a latch) was invisible from the surface.
    this.detail = detail;
  }
}

// A 401 has to reach React state, not just localStorage. `setToken(null)` alone
// clears storage while `AuthProvider`'s `user` stays truthy in memory — and
// `ProtectedRoute` gates on `user`, so nothing redirects and every later request
// goes out header-less and re-401s. That was the latch. AuthProvider registers
// here so the two are cleared together.
let onUnauthorized = null;

export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

async function request(path, { method = "GET", body, form, auth = true } = {}) {
  const headers = {};
  const token = getToken();
  if (auth && token) headers["Authorization"] = `Bearer ${token}`;

  let payload;
  if (form) {
    // OAuth2 password flow expects application/x-www-form-urlencoded.
    payload = new URLSearchParams(form).toString();
    headers["Content-Type"] = "application/x-www-form-urlencoded";
  } else if (body !== undefined) {
    payload = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(`/api${path}`, { method, headers, body: payload });

  if (res.status === 401) {
    const hadToken = Boolean(token);
    let detail = "";
    try {
      detail = (await res.json()).detail ?? "";
    } catch {
      /* non-JSON error body */
    }
    setToken(null);
    // Clear the in-memory identity too, so ProtectedRoute fails its gate and
    // redirects — without this the app latches: header-less requests forever and
    // no way out but a manual reload.
    onUnauthorized?.();
    const message = hadToken
      ? `Your session has expired — please sign in again.${detail ? ` (${detail})` : ""}`
      : detail || "Not authenticated — please sign in.";
    throw new ApiError(401, message, detail);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail, detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  get: (path, opts) => request(path, { ...opts, method: "GET" }),
  post: (path, body, opts) => request(path, { ...opts, method: "POST", body }),
  put: (path, body, opts) => request(path, { ...opts, method: "PUT", body }),
  del: (path, opts) => request(path, { ...opts, method: "DELETE" }),

  // Auth helpers
  login: (username, password) =>
    request("/auth/login", {
      method: "POST",
      form: { username, password },
      auth: false,
    }),
  me: () => request("/auth/me"),
};
