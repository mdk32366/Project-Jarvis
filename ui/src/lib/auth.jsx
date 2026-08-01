// Auth context: holds the current user, exposes login/logout, and restores the
// session from a stored token on load.

import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api, setToken, getToken, setUnauthorizedHandler } from "./api.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  // Any 401 from the API layer clears the in-memory identity, not just the stored
  // token. ProtectedRoute gates on `user`, so this is what actually produces the
  // redirect to /login. Clearing storage alone left `user` truthy and the app
  // latched: every subsequent request went out with no Authorization header and
  // re-401'd, escapable only by a manual page reload.
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    // Restore session if a token is present.
    if (!getToken()) {
      setLoading(false);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (username, password) => {
    const { access_token } = await api.login(username, password);
    setToken(access_token);
    setUser(await api.me());
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
