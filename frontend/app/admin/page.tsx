"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/app/auth-context";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

type AdminUser = {
  id: string;
  email: string;
  full_name: string;
  profession: string;
  organization: string;
  role: string;
  is_active: boolean;
  created_at: string;
};

type Stats = {
  total_users: number;
  active_users: number;
  total_conversations: number;
  total_messages: number;
  total_tokens: number;
};

type Skill = { name: string; instruction_file: string; chars: number };

type TokenRow = {
  email: string;
  full_name: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  requests: number;
};

export default function AdminPage() {
  const { token, user, loading, logout } = useAuth();
  const router = useRouter();

  const [stats, setStats] = useState<Stats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [tab, setTab] = useState<"users" | "skills" | "tokens">("users");
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [tokenRows, setTokenRows] = useState<TokenRow[]>([]);

  const headers = useCallback(
    () => ({ Authorization: `Bearer ${token}` }),
    [token]
  );

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [s, u, k, tk] = await Promise.all([
        fetch(`${API_URL}/api/v1/admin/stats`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/admin/users`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/admin/skills`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/admin/tokens`, { headers: headers() }),
      ]);
      if (s.status === 403) {
        router.replace("/");
        return;
      }
      if (s.ok) setStats(await s.json());
      if (u.ok) setUsers(await u.json());
      if (k.ok) setSkills(await k.json());
      if (tk.ok) setTokenRows(await tk.json());
    } catch {
      setError("Gagal memuat data admin.");
    }
  }, [token, headers, router]);

  useEffect(() => {
    if (!loading && !token) router.replace("/login");
  }, [loading, token, router]);

  useEffect(() => {
    if (token) load();
  }, [token, load]);

  function flash(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 2500);
  }

  async function toggleActive(u: AdminUser) {
    const res = await fetch(`${API_URL}/api/v1/admin/users/${u.id}/active`, {
      method: "PATCH",
      headers: { ...headers(), "Content-Type": "application/json" },
      body: JSON.stringify({ is_active: !u.is_active }),
    });
    const data = await res.json();
    if (!res.ok) {
      setError(data.detail || "Gagal");
      return;
    }
    setUsers((list) =>
      list.map((x) => (x.id === u.id ? { ...x, is_active: data.is_active } : x))
    );
    flash(`${u.email} ${data.is_active ? "diaktifkan" : "dinonaktifkan"}.`);
  }

  async function toggleRole(u: AdminUser) {
    const newRole = u.role === "admin" ? "user" : "admin";
    const res = await fetch(`${API_URL}/api/v1/admin/users/${u.id}/role`, {
      method: "PATCH",
      headers: { ...headers(), "Content-Type": "application/json" },
      body: JSON.stringify({ role: newRole }),
    });
    const data = await res.json();
    if (!res.ok) {
      setError(data.detail || "Gagal");
      return;
    }
    setUsers((list) =>
      list.map((x) => (x.id === u.id ? { ...x, role: data.role } : x))
    );
    flash(`Role ${u.email} → ${data.role}.`);
  }

  async function removeUser(u: AdminUser) {
    const res = await fetch(`${API_URL}/api/v1/admin/users/${u.id}`, {
      method: "DELETE",
      headers: headers(),
    });
    const data = await res.json();
    if (!res.ok) {
      setError(data.detail || "Gagal");
      return;
    }
    setUsers((list) => list.filter((x) => x.id !== u.id));
    flash(`${u.email} dihapus.`);
    load();
  }

  async function resetPassword(u: AdminUser) {
    const newPw = prompt(
      `Password baru untuk ${u.email} (min. 8 karakter):`
    );
    if (!newPw) return;
    const res = await fetch(
      `${API_URL}/api/v1/admin/users/${u.id}/reset-password`,
      {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        body: JSON.stringify({ new_password: newPw }),
      }
    );
    const data = await res.json();
    if (!res.ok) {
      setError(data.detail || "Gagal");
      return;
    }
    flash(`Password ${u.email} direset.`);
  }

  if (loading || !token || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-950 text-gray-400">
        Memuat…
      </main>
    );
  }

  const isAdmin = user.role === "admin";

  return (
    <main className="min-h-screen bg-gray-950 text-gray-100">
      {/* Header */}
      <header className="flex items-center justify-between border-b border-gray-800 px-6 py-4">
        <h1 className="text-xl font-bold">👑 Panel SuperAdmin</h1>
        <div className="flex gap-2">
          <a
            href="/"
            className="rounded-lg border border-gray-700 px-4 py-1.5 text-sm text-gray-300 hover:bg-gray-800"
          >
            ← Chat
          </a>
          <button
            onClick={logout}
            className="rounded-lg border border-gray-700 px-4 py-1.5 text-sm text-gray-300 hover:bg-gray-800"
          >
            Keluar
          </button>
        </div>
      </header>

      {!isAdmin ? (
        <div className="mx-auto mt-24 max-w-md text-center">
          <p className="text-5xl">⛔</p>
          <p className="mt-4 text-gray-400">
            Halaman ini khusus administrator. Role Anda:{" "}
            <span className="text-gray-200">{user.role}</span>
          </p>
          <p className="mt-2 text-sm text-gray-500">
            Minta admin utama mengangkat role Anda melalui panel admin.
          </p>
        </div>
      ) : (
        <div className="mx-auto max-w-6xl px-6 py-8">
          {/* Statistik */}
          <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
            {stats && (
              <>
                <StatCard icon="👥" label="Total User" value={stats.total_users} />
                <StatCard icon="✅" label="User Aktif" value={stats.active_users} />
                <StatCard icon="💬" label="Percakapan" value={stats.total_conversations} />
                <StatCard icon="✉️" label="Total Pesan" value={stats.total_messages} />
                <StatCard icon="🎯" label="Total Token" value={stats.total_tokens} />
              </>
            )}
          </div>

          {/* Tabs */}
          <div className="mt-8 grid max-w-md grid-cols-3 gap-2 rounded-xl bg-gray-900 p-1 text-sm">
            <button
              onClick={() => setTab("users")}
              className={`rounded-lg py-2 font-medium ${
                tab === "users" ? "bg-blue-600" : "text-gray-400"
              }`}
            >
              👥 Pengguna
            </button>
            <button
              onClick={() => setTab("tokens")}
              className={`rounded-lg py-2 font-medium ${
                tab === "tokens" ? "bg-blue-600" : "text-gray-400"
              }`}
            >
              🎯 Token
            </button>
            <button
              onClick={() => setTab("skills")}
              className={`rounded-lg py-2 font-medium ${
                tab === "skills" ? "bg-blue-600" : "text-gray-400"
              }`}
            >
              🧩 Skill
            </button>
          </div>

          {error && (
            <p className="mt-4 rounded-lg bg-red-900/40 px-3 py-2 text-sm text-red-300">
              {error}
            </p>
          )}
          {toast && (
            <p className="mt-4 rounded-lg bg-emerald-900/40 px-3 py-2 text-sm text-emerald-300">
              {toast}
            </p>
          )}

          {/* Tabel user */}
          {tab === "users" && (
            <div className="mt-6 overflow-x-auto rounded-xl border border-gray-800">
              <table className="w-full text-left text-sm">
                <thead className="bg-gray-900 text-gray-400">
                  <tr>
                    <th className="px-4 py-3">Email</th>
                    <th className="px-4 py-3">Nama</th>
                    <th className="px-4 py-3">Profesi</th>
                    <th className="px-4 py-3">Role</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Aksi</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id} className="border-t border-gray-800">
                      <td className="px-4 py-3">{u.email}</td>
                      <td className="px-4 py-3">{u.full_name || "—"}</td>
                      <td className="px-4 py-3 text-gray-400">{u.profession}</td>
                      <td className="px-4 py-3">
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs ${
                            u.role === "admin"
                              ? "bg-amber-600/30 text-amber-300"
                              : "bg-gray-700 text-gray-300"
                          }`}
                        >
                          {u.role}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs ${
                            u.is_active
                              ? "bg-emerald-600/30 text-emerald-300"
                              : "bg-red-600/30 text-red-300"
                          }`}
                        >
                          {u.is_active ? "aktif" : "nonaktif"}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1">
                          <MiniBtn onClick={() => toggleActive(u)}>
                            {u.is_active ? "Nonaktifkan" : "Aktifkan"}
                          </MiniBtn>
                          <MiniBtn onClick={() => toggleRole(u)}>
                            {u.role === "admin" ? "→ User" : "→ Admin"}
                          </MiniBtn>
                          <MiniBtn onClick={() => resetPassword(u)}>Reset PW</MiniBtn>
                          <MiniBtn danger onClick={() => removeUser(u)}>
                            Hapus
                          </MiniBtn>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Tabel token per user */}
          {tab === "tokens" && (
            <div className="mt-6 overflow-x-auto rounded-xl border border-gray-800">
              <table className="w-full text-left text-sm">
                <thead className="bg-gray-900 text-gray-400">
                  <tr>
                    <th className="px-4 py-3">Email</th>
                    <th className="px-4 py-3">Nama</th>
                    <th className="px-4 py-3 text-right">Permintaan</th>
                    <th className="px-4 py-3 text-right">Input</th>
                    <th className="px-4 py-3 text-right">Output</th>
                    <th className="px-4 py-3 text-right">Total Token</th>
                  </tr>
                </thead>
                <tbody>
                  {tokenRows.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-4 py-6 text-center text-gray-500">
                        Belum ada pemakaian token tercatat
                      </td>
                    </tr>
                  )}
                  {tokenRows.map((r) => (
                    <tr key={r.email} className="border-t border-gray-800">
                      <td className="px-4 py-3">{r.email}</td>
                      <td className="px-4 py-3">{r.full_name || "—"}</td>
                      <td className="px-4 py-3 text-right">{r.requests}</td>
                      <td className="px-4 py-3 text-right text-gray-400">
                        {r.prompt_tokens.toLocaleString("id-ID")}
                      </td>
                      <td className="px-4 py-3 text-right text-gray-400">
                        {r.completion_tokens.toLocaleString("id-ID")}
                      </td>
                      <td className="px-4 py-3 text-right font-semibold text-amber-300">
                        {r.total_tokens.toLocaleString("id-ID")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="px-4 py-3 text-xs text-gray-500">
                * Estimasi biaya ≈ total_token ÷ 1.000.000 × harga per juta token
                di dashboard Sumopod Anda.
              </p>
            </div>
          )}

          {/* Daftar skill */}
          {tab === "skills" && (
            <div className="mt-6 grid gap-3 md:grid-cols-2">
              {skills.map((s) => (
                <div
                  key={s.name}
                  className="rounded-xl border border-gray-800 bg-gray-900 p-4"
                >
                  <p className="font-semibold">🧩 {s.name}</p>
                  <p className="mt-1 text-xs text-gray-400">
                    {s.instruction_file} · {s.chars} karakter
                  </p>
                </div>
              ))}
              <p className="text-xs text-gray-500 md:col-span-2">
                * Mengubah isi skill bisa dilakukan lewat file di
                backend/app/skills/ lalu rebuild.
              </p>
            </div>
          )}
        </div>
      )}
    </main>
  );
}

function StatCard({
  icon,
  label,
  value,
}: {
  icon: string;
  label: string;
  value: number;
}) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
      <p className="text-2xl">{icon}</p>
      <p className="mt-1 text-2xl font-bold">{value}</p>
      <p className="text-xs text-gray-400">{label}</p>
    </div>
  );
}

function MiniBtn({
  children,
  onClick,
  danger = false,
}: {
  children: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-md border px-2 py-1 text-xs ${
        danger
          ? "border-red-700/50 text-red-300 hover:bg-red-900/40"
          : "border-gray-700 text-gray-300 hover:bg-gray-800"
      }`}
    >
      {children}
    </button>
  );
}
