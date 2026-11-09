"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export type User = {
  id: string;
  email: string;
  full_name: string;
  profession: string;
  organization: string;
  role?: string;
  status?: string;
  package_code?: string;
  package_name?: string;
  package_expires_at?: string | null;
  package_days_remaining?: number | null;
  package_is_default?: boolean;
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

  // Pulihkan sesi lalu sinkronkan profil dan paket terbaru dari backend.
  useEffect(() => {
    const t = localStorage.getItem("oma_token");
    const u = localStorage.getItem("oma_user");

    if (!t) {
      setLoading(false);
      return;
    }

    setToken(t);

    if (u) {
      try {
        setUser(JSON.parse(u));
      } catch {
        localStorage.removeItem("oma_user");
      }
    }

    let cancelled = false;

    async function refreshProfile() {
      try {
        const res = await fetch(`${API_URL}/api/v1/auth/me`, {
          headers: { Authorization: `Bearer ${t}` },
        });

        if (res.ok) {
          const freshUser = await res.json();
          if (!cancelled) {
            setUser(freshUser);
            localStorage.setItem("oma_user", JSON.stringify(freshUser));
          }
        }
      } catch {
        // Pertahankan sesi lokal jika backend sementara tidak terjangkau.
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    refreshProfile();

    return () => {
      cancelled = true;
    };
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
      if (!res.ok) throw new Error(data.detail || "Login gagal");
      persist(data.access_token, data.user);
      if (data.user.status === "pending") {
        return "PENDING: Akun Anda berhasil dibuat dan sedang MENUNGGU PERSETUJUAN admin. Anda akan bisa chat setelah disetujui.";
      }
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
      if (data.user.status === "pending") {
        return "PENDING: Pendaftaran Anda berhasil dan sedang MENUNGGU PERSETUJUAN admin. Anda akan bisa chat setelah disetujui.";
      }
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

/** Admin panel route helper: jika user.role admin, tampilkan link ke /admin */
export function isAdmin(user: { role?: string } | null): boolean {
  return user?.role === "admin";
}
