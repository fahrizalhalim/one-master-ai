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
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // Pulihkan token dan profil yang tersimpan saat aplikasi dibuka.
  useEffect(() => {
    const storedToken = localStorage.getItem("oma_token");
    const storedUser = localStorage.getItem("oma_user");

    if (!storedToken) {
      setLoading(false);
      return;
    }

    setToken(storedToken);

    if (storedUser) {
      try {
        setUser(JSON.parse(storedUser));
      } catch {
        localStorage.removeItem("oma_user");
      }
    }
  }, []);

  // Periksa sesi aktif dan deteksi pencabutan dari perangkat lain.
  // Efek ini ikut berjalan setiap kali token berubah setelah login.
  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }

    let cancelled = false;

    async function refreshProfile() {
      try {
        const res = await fetch(`${API_URL}/api/v1/auth/me`, {
          headers: { Authorization: `Bearer ${token}` },
        });

        if (cancelled) return;

        if (res.ok) {
          const freshUser = await res.json();

          if (!cancelled && localStorage.getItem("oma_token") === token) {
            setUser(freshUser);
            localStorage.setItem("oma_user", JSON.stringify(freshUser));
          }
        } else if (
          res.status === 401 &&
          localStorage.getItem("oma_token") === token
        ) {
          // Sesi ini dicabut dari perangkat lain.
          setToken(null);
          setUser(null);
          localStorage.removeItem("oma_token");
          localStorage.removeItem("oma_user");
        }
      } catch {
        // Pertahankan sesi jika backend sementara tidak terjangkau.
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    refreshProfile();
    const sessionCheck = window.setInterval(refreshProfile, 30000);

    return () => {
      cancelled = true;
      window.clearInterval(sessionCheck);
    };
  }, [token]);

  // Sinkronkan perubahan login/logout dari tab browser lain.
  useEffect(() => {
    function syncSession(event: StorageEvent) {
      if (
        event.key !== "oma_token" &&
        event.key !== "oma_user" &&
        event.key !== null
      ) return;

      const storedToken = localStorage.getItem("oma_token");
      const storedUser = localStorage.getItem("oma_user");

      setToken(storedToken);

      if (!storedToken || !storedUser) {
        setUser(null);
        return;
      }

      try {
        setUser(JSON.parse(storedUser));
      } catch {
        setUser(null);
      }
    }

    window.addEventListener("storage", syncSession);
    return () => window.removeEventListener("storage", syncSession);
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

  const logout = useCallback(async () => {
    const confirmed = window.confirm(
      "Apakah Anda yakin ingin keluar?\n\n" +
      "Tindakan ini akan mengeluarkan akun Anda dari semua perangkat dan sesi web yang sedang aktif."
    );
    if (!confirmed) return;

    const currentToken = localStorage.getItem("oma_token") || token;

    if (currentToken) {
      try {
        const res = await fetch(`${API_URL}/api/v1/auth/logout-all`, {
          method: "POST",
          headers: { Authorization: `Bearer ${currentToken}` },
        });

        if (!res.ok) {
          let detail = "Tidak dapat mengeluarkan semua sesi. Silakan coba lagi.";
          try {
            const data = await res.json();
            if (typeof data.detail === "string") detail = data.detail;
          } catch {
            // Gunakan pesan default.
          }
          window.alert(detail);
          return;
        }
      } catch {
        window.alert(
          "Koneksi ke server gagal. Sesi belum dipastikan dicabut. Periksa koneksi lalu coba lagi."
        );
        return;
      }
    }

    setToken(null);
    setUser(null);
    localStorage.removeItem("oma_token");
    localStorage.removeItem("oma_user");
    window.location.replace("/login");
  }, [token]);

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
