/**
 * The 2026-08-01 "Unauthorized" latch.
 *
 * SYMPTOM: after a good turn, three consecutive chat messages returned a bare
 * `Unauthorized`, content-independent and uniform, with no redirect to login.
 *
 * MECHANISM: the access token expires (24h TTL). The first 401 fires
 * `setToken(null)`, which clears STORAGE but never the React `user` state in
 * AuthProvider. `ProtectedRoute` gates on `user`, still truthy in memory, so no
 * redirect happens. Every later request goes out with NO Authorization header,
 * re-401s, and wipes an already-empty token. A transient, self-correctable
 * condition (an expired token) converts into a permanent one with no
 * self-clearing path — only a manual page reload escapes.
 *
 * This is the third latch of the same family in two days (relay body, calendar
 * liveness, session auth). The tell is identical every time: uniform, mid-session
 * onset, right after something worked.
 *
 * These tests are the regression. They must be RED before the fix.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProtectedRoute from "../components/ProtectedRoute.jsx";
import { AuthProvider, useAuth } from "../lib/auth.jsx";
import { api, setToken } from "../lib/api.js";

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 401 ? "Unauthorized" : "Error",
    json: async () => body,
  };
}

/** A page that reports auth state and can fire an authenticated request. */
function Probe() {
  const { user } = useAuth();
  return (
    <div>
      <span data-testid="user">{user ? user.username : "none"}</span>
      <button onClick={() => api.post("/chat", { message: "hi" }).catch((e) => {
        document.getElementById("err").textContent = e.message;
      })}>
        send
      </button>
      <p id="err" />
    </div>
  );
}

function renderApp() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<div data-testid="login">LOGIN PAGE</div>} />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <Probe />
              </ProtectedRoute>
            }
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  setToken("a-valid-looking-token");
});

describe("401 latch", () => {
  it("test_401_clears_user_state_and_redirects_to_login", async () => {
    // Session restores fine, then the token expires mid-session.
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(200, { username: "matt" }))   // api.me() on mount
      .mockResolvedValue(jsonResponse(401, { detail: "Could not validate credentials" }));
    vi.stubGlobal("fetch", fetchMock);

    renderApp();
    await screen.findByTestId("user");
    expect(screen.getByTestId("user")).toHaveTextContent("matt");

    await userEvent.click(screen.getByRole("button", { name: "send" }));

    // THE REGRESSION: the 401 must clear identity and bounce to /login,
    // with NO page reload.
    await waitFor(() => expect(screen.getByTestId("login")).toBeInTheDocument());
  });

  it("test_expired_token_does_not_latch", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(200, { username: "matt" }))
      .mockResolvedValue(jsonResponse(401, { detail: "Could not validate credentials" }));
    vi.stubGlobal("fetch", fetchMock);

    renderApp();
    await screen.findByTestId("user");

    await userEvent.click(screen.getByRole("button", { name: "send" }));
    await waitFor(() => expect(screen.queryByTestId("login")).toBeInTheDocument());

    // Once bounced, the app must not sit there issuing header-less requests.
    // Count the calls that carried no Authorization header AFTER the first 401 —
    // the latch's signature was an unbounded stream of them.
    const headerless = fetchMock.mock.calls.filter(
      ([, init]) => !init?.headers?.Authorization,
    );
    expect(headerless.length).toBeLessThanOrEqual(1);
  });
});

describe("error surface", () => {
  it("test_error_surface_shows_backend_detail_not_hardcoded_string", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      jsonResponse(401, { detail: "Could not validate credentials" }),
    ));

    // The UI substituted a bare "Unauthorized" for whatever the backend said,
    // which is what made this a misdiagnosis risk: the surface reported the
    // failure with total confidence and zero detail.
    await expect(api.post("/chat", { message: "hi" })).rejects.toThrow(
      /session|expired|credential/i,
    );
    await expect(api.post("/chat", { message: "hi" })).rejects.not.toThrow(
      /^Unauthorized$/,
    );
  });
});
