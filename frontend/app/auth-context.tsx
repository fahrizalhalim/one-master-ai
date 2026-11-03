"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export type User = {
  id: string;
  email: string;
  full_name: string;
  profession: string;
  organization: string;
};

type AuthState = {
  token: string | null;
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<string | null>;
  register: (data: {
    email: string;
    password: string;
    full_name: string;
    profession: string;
    organization: string;
  }) => Promise<string | null>;
  logout: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // Pulihkan sesi dari localStorage saat pertama load
  useEffect(() => {
    const t = localStorage.getItem("oma_token");
    const u = localStorage.getItem("oma_user");
    if (t && u) {
      setToken(t);
      setUser(JSON.parse(u));
    }
    setLoading(false);
  }, []);

  const persist = useCallback((newToken: string, newUser: User) => {
    setToken(newToken);
    setUser(newUser);
    localStorage.setItem("oma_token", newToken);
    localStorage.setItem("oma_user", JSON.stringify(newUser));
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await fetch(`${API_URL}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ username: email, password }),
      });
      const data = await res.json();
      if (!res.ok) return data.detail || "Login gagal";
      persist(data.access_token, data.user);
      return null;
    },
    [persist]
  );

  const register = useCallback(
    async (payload: {
      email: string;
      password: string;
      full_name: string;
      profession: string;
      organization: string;
    }) => {
      const res = await fetch(`${API_URL}/api/v1/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        // detail bisa berupa string atau array validasi pydantic
        if (Array.isArray(data.detail)) {
          return data.detail.map((d: { msg: string }) => d.msg).join("; ");
        }
        return data.detail || "Registrasi gagal";
      }
      persist(data.access_token, data.user);
      return null;
    },
    [persist]
  );

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    localStorage.removeItem("oma_token");
    localStorage.removeItem("oma_user");
  }, []);

  return (
    <AuthContext.Provider value={{ token, user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth harus dipakai di dalam AuthProvider");
  return ctx;
}
