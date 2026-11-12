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
  status: string;
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

type TelegramPendingItem = {
  id: string;
  user_id: string;
  bot_username: string;
  status: string;
  created_at: string | null;
  user_name: string;
  user_email: string;
};

type TelegramLinkedItem = {
  id: string;
  user_id: string;
  user_email: string;
  user_name: string;
  telegram_username: string | null;
  telegram_id: string;
  bot_username: string;
  status: string;
  created_at: string | null;
};

type TokenRow = {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  requests: number;
  topup_balance: number;
  daily_limit: number | null;
  daily_used: number;
  daily_remaining: number | null;
};

type ReservationReview = {
  reservation_id: string;
  user_id: string;
  email: string;
  full_name: string;
  reservation_status: string;
  result_status: string | null;
  reserved_topup: number;
  reserved_daily: number;
  actual_tokens: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  skill: string | null;
  assistant_message_id: string | null;
  result_preview: string | null;
  reference: string | null;
  description: string | null;
  created_at: string | null;
  expires_at: string | null;
  completed_at: string | null;
  result_created_at: string | null;
};

type TopUpAdminRequest = {
  id: string;
  package_code: string;
  amount_idr: number;
  token_amount: number;
  status: "pending" | "approved" | "rejected";
  admin_note: string;
  created_at: string | null;
  reviewed_at: string | null;
  user_id: string;
  user_name: string;
  user_email: string;
};

type ProtectedAdminAction = {
  title: string;
  url: string;
  method: "POST" | "PATCH" | "DELETE";
  body: Record<string, unknown>;
  successMessage: string;
};

export default function AdminPage() {
  const { token, user, loading, logout } = useAuth();
  const router = useRouter();

  const [stats, setStats] = useState<Stats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [tab, setTab] = useState<"users" | "skills" | "tokens" | "pending" | "topups" | "review" | "telegram">("users");
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [tokenRows, setTokenRows] = useState<TokenRow[]>([]);
  const [pending, setPending] = useState<AdminUser[]>([]);
  const [telegramPending, setTelegramPending] = useState<TelegramPendingItem[]>([]);
  const [telegramLinked, setTelegramLinked] = useState<TelegramLinkedItem[]>([]);
  const [topupRequests, setTopupRequests] = useState<TopUpAdminRequest[]>([]);
  const [reservationReviews, setReservationReviews] = useState<ReservationReview[]>([]);
  const [topupBusy, setTopupBusy] = useState<string | null>(null);
  const [proofUrl, setProofUrl] = useState<string | null>(null);
  const [proofType, setProofType] = useState("");
  const [proofBusy, setProofBusy] = useState<string | null>(null);
  const [pendingAdminAction, setPendingAdminAction] =
    useState<ProtectedAdminAction | null>(null);
  const [adminPassword, setAdminPassword] = useState("");
  const [adminActionBusy, setAdminActionBusy] = useState(false);
  const headers = useCallback(
    () => ({ Authorization: `Bearer ${token}` }),
    [token]
  );

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [s, u, k, tk, pd, rv, tp, tg, tl] = await Promise.all([
        fetch(`${API_URL}/api/v1/admin/stats`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/admin/users`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/admin/skills`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/admin/tokens`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/admin/pending`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/admin/reservations/review`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/topup/admin/requests?status_filter=pending`, {
          headers: headers(),
        }),
        fetch(`${API_URL}/api/v1/admin/telegram/pending`, {
          headers: headers(),
        }),
        fetch(`${API_URL}/api/v1/admin/telegram/linked`, {
          headers: headers(),
        }),
      ]);
      if (s.status === 403) {
        router.replace("/");
        return;
      }
      if (s.ok) setStats(await s.json());
      if (u.ok) setUsers(await u.json());
      if (k.ok) setSkills(await k.json());
      if (tk.ok) setTokenRows(await tk.json());
      if (pd.ok) setPending(await pd.json());
      if (tg.ok) setTelegramPending(await tg.json());
      if (tl.ok) setTelegramLinked(await tl.json());
      if (rv.ok) {
        const reviewData = await rv.json();
        setReservationReviews(reviewData.items || []);
      }
      if (tp.ok) {
        const topupData = await tp.json();
        setTopupRequests(topupData.requests || []);
      }
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

  useEffect(() => {
    if (!token || user?.role !== "admin") return;
    const timer = window.setInterval(() => load(), 30000);
    return () => window.clearInterval(timer);
  }, [token, user?.role, load]);

  useEffect(() => {
    return () => {
      if (proofUrl) URL.revokeObjectURL(proofUrl);
    };
  }, [proofUrl]);

  function flash(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 2500);
  }

  function requestAdminPassword(action: ProtectedAdminAction) {
    setError(null);
    setAdminPassword("");
    setPendingAdminAction(action);
  }

  async function submitAdminPassword() {
    if (!pendingAdminAction || adminActionBusy) return;

    if (!adminPassword.trim()) {
      setError("Masukkan password login admin.");
      return;
    }

    setAdminActionBusy(true);
    setError(null);

    try {
      const res = await fetch(pendingAdminAction.url, {
        method: pendingAdminAction.method,
        headers: {
          ...headers(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          ...pendingAdminAction.body,
          admin_password: adminPassword,
        }),
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        setError(data.detail || "Tindakan admin gagal.");
        setAdminPassword("");
        return;
      }

      const successMessage =
        data.detail || pendingAdminAction.successMessage;

      setPendingAdminAction(null);
      setAdminPassword("");
      await load();
      flash(successMessage);
    } catch {
      setError("Tidak dapat menghubungi server.");
    } finally {
      setAdminActionBusy(false);
    }
  }

  function settleReservation(r: ReservationReview) {
    const maxInteger = 2_147_483_647;
    const prompt = r.prompt_tokens;
    const completion = r.completion_tokens;
    const total = r.total_tokens;

    if (
      !["pending", "needs_review"].includes(r.reservation_status) ||
      !["received", "needs_review"].includes(r.result_status || "") ||
      prompt === null ||
      completion === null ||
      total === null ||
      !Number.isSafeInteger(prompt) ||
      !Number.isSafeInteger(completion) ||
      !Number.isSafeInteger(total) ||
      prompt < 0 ||
      completion < 0 ||
      total <= 0 ||
      prompt > maxInteger ||
      completion > maxInteger ||
      total > maxInteger ||
      prompt + completion !== total ||
      !r.skill
    ) {
      setError("Bukti token belum memenuhi syarat settlement. Periksa status dan data token.");
      return;
    }

    const confirmed = window.confirm(
      `Setujui settlement reservasi untuk ${r.email}?\\n\\n` +
      `Token input: ${prompt.toLocaleString("id-ID")}\\n` +
      `Token output: ${completion.toLocaleString("id-ID")}\\n` +
      `Total pemakaian aktual: ${total.toLocaleString("id-ID")} token\\n\\n` +
      "Saldo reservasi akan disesuaikan berdasarkan pemakaian aktual. Tindakan ini tidak dapat dibatalkan."
    );

    if (!confirmed) return;

    requestAdminPassword({
      title: `Konfirmasi settlement ${total.toLocaleString("id-ID")} token untuk ${r.email}`,
      url: `${API_URL}/api/v1/admin/reservations/${r.reservation_id}/settle`,
      method: "POST",
      body: {},
      successMessage: `Settlement ${total.toLocaleString("id-ID")} token untuk ${r.email} berhasil.`,
    });
  }

  async function resetTokenBalance(r: TokenRow) {
    const confirmed = window.confirm(
      `Reset saldo token ${r.email}?\n\n` +
      `Saldo saat ini: ${r.topup_balance.toLocaleString("id-ID")} token.\n` +
      `Saldo akan menjadi 0. Riwayat pemakaian tidak berubah.`
    );
    if (!confirmed) return;

    requestAdminPassword({
      title: `Konfirmasi reset saldo token ${r.email}`,
      url: `${API_URL}/api/v1/admin/tokens/${r.user_id}/reset-balance`,
      method: "POST",
      body: {},
      successMessage: `Saldo token ${r.email} berhasil direset.`,
    });
  }

  async function addTokenBalance(r: TokenRow) {
    const raw = window.prompt(
      `Jumlah token yang akan ditambahkan untuk ${r.email}:`,
      "100000"
    );

    if (raw === null) return;

    const amount = Number(raw.trim());

    if (!Number.isSafeInteger(amount) || amount <= 0) {
      setError("Jumlah token harus bilangan bulat positif yang valid.");
      return;
    }

    const confirmed = window.confirm(
      `Tambahkan ${amount.toLocaleString("id-ID")} token ke ${r.email}?\n\n` +
      `Saldo saat ini: ${r.topup_balance.toLocaleString("id-ID")} token.\n` +
      `Saldo setelah penambahan: ${(r.topup_balance + amount).toLocaleString("id-ID")} token.`
    );

    if (!confirmed) return;

    requestAdminPassword({
      title: `Konfirmasi penambahan ${amount.toLocaleString("id-ID")} token untuk ${r.email}`,
      url: `${API_URL}/api/v1/admin/tokens/${r.user_id}/add-balance`,
      method: "POST",
      body: { amount },
      successMessage: `Saldo token ${r.email} berhasil ditambahkan.`,
    });
  }

  async function toggleActive(u: AdminUser) {
    const nextActive = !u.is_active;

    requestAdminPassword({
      title: `Konfirmasi ${nextActive ? "mengaktifkan" : "menonaktifkan"} akun ${u.email}`,
      url: `${API_URL}/api/v1/admin/users/${u.id}/active`,
      method: "PATCH",
      body: { is_active: nextActive },
      successMessage: `${u.email} ${nextActive ? "diaktifkan" : "dinonaktifkan"}.`,
    });
  }

  async function toggleRole(u: AdminUser) {
    const newRole = u.role === "admin" ? "user" : "admin";

    requestAdminPassword({
      title: `Konfirmasi perubahan role ${u.email} menjadi ${newRole}`,
      url: `${API_URL}/api/v1/admin/users/${u.id}/role`,
      method: "PATCH",
      body: { role: newRole },
      successMessage: `Role ${u.email} berhasil diubah menjadi ${newRole}.`,
    });
  }

  async function removeUser(u: AdminUser) {
    requestAdminPassword({
      title: `HAPUS PERMANEN akun ${u.email}. Masukkan password login admin untuk melanjutkan.`,
      url: `${API_URL}/api/v1/admin/users/${u.id}`,
      method: "DELETE",
      body: {},
      successMessage: `Akun ${u.email} berhasil dihapus.`,
    });
  }

  async function resetPassword(u: AdminUser) {
    const newPw = window.prompt(
      `Password baru untuk ${u.email} (minimal 8 karakter):`
    );

    if (newPw === null) return;

    if (newPw.length < 8) {
      setError("Password baru minimal 8 karakter.");
      return;
    }

    requestAdminPassword({
      title: `Konfirmasi reset password akun ${u.email}`,
      url: `${API_URL}/api/v1/admin/users/${u.id}/reset-password`,
      method: "POST",
      body: { new_password: newPw },
      successMessage: `Password ${u.email} berhasil direset.`,
    });
  }

  async function decide(u: AdminUser, decision: "approve" | "reject") {
    const res = await fetch(`${API_URL}/api/v1/admin/users/${u.id}/decision`, {
      method: "POST",
      headers: { ...headers(), "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    const data = await res.json();
    if (!res.ok) {
      setError(data.detail || "Gagal");
      return;
    }
    setPending((list) => list.filter((x) => x.id !== u.id));
    setUsers((list) =>
      list.map((x) => (x.id === u.id ? { ...x, status: data.status } : x))
    );
    flash(
      decision === "approve"
        ? `✅ ${u.email} DISETUJUI — bisa login & chat.`
        : `⛔ ${u.email} DITOLAK.`
    );
    load();
  }

  async function decideTelegram(
    account: TelegramPendingItem,
    decision: "approve" | "reject"
  ) {
    setError(null);

    try {
      const res = await fetch(
        `${API_URL}/api/v1/admin/telegram/${account.id}/decision`,
        {
          method: "POST",
          headers: { ...headers(), "Content-Type": "application/json" },
          body: JSON.stringify({ decision }),
        }
      );

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        setError(data.detail || "Gagal memproses persetujuan Telegram.");
        return;
      }

      setTelegramPending((items) =>
        items.filter((item) => item.id !== account.id)
      );

      flash(
        decision === "approve"
          ? `✅ @${account.bot_username} disetujui. Menunggu aktivasi teknis.`
          : `⛔ @${account.bot_username} ditolak.`
      );

      await load();
    } catch {
      setError("Tidak dapat menghubungi server untuk memproses Telegram.");
    }
  }

  function changeTelegramStatus(
    account: TelegramLinkedItem,
    isActive: boolean
  ) {
    requestAdminPassword({
      title: `${isActive ? "Aktifkan" : "Nonaktifkan"} koneksi Telegram @${account.bot_username} untuk ${account.user_email}`,
      url: `${API_URL}/api/v1/admin/telegram/${account.id}/status`,
      method: "PATCH",
      body: { is_active: isActive },
      successMessage: isActive
        ? `Koneksi Telegram @${account.bot_username} berhasil diaktifkan.`
        : `Koneksi Telegram @${account.bot_username} berhasil dinonaktifkan.`,
    });
  }

  function removeTelegram(account: TelegramLinkedItem) {
    const confirmed = window.confirm(
      `Hapus koneksi Telegram @${account.bot_username} dari akun ${account.user_email}? Webhook akan dilepas dan pengguna harus menghubungkan bot kembali. Akun pengguna dan riwayat percakapan tidak dihapus.`
    );

    if (!confirmed) return;

    requestAdminPassword({
      title: `Hapus koneksi Telegram @${account.bot_username} milik ${account.user_email}`,
      url: `${API_URL}/api/v1/admin/telegram/${account.id}`,
      method: "DELETE",
      body: {},
      successMessage: `Koneksi Telegram @${account.bot_username} berhasil dihapus.`,
    });
  }

  async function openTopupProof(request: TopUpAdminRequest) {
    setProofBusy(request.id);
    setError(null);

    try {
      const res = await fetch(
        `${API_URL}/api/v1/topup/admin/requests/${request.id}/proof`,
        { headers: headers() }
      );

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Gagal membuka bukti pembayaran.");
      }

      const blob = await res.blob();
      if (proofUrl) URL.revokeObjectURL(proofUrl);
      setProofType(res.headers.get("content-type") || blob.type);
      setProofUrl(URL.createObjectURL(blob));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal membuka bukti pembayaran.");
    } finally {
      setProofBusy(null);
    }
  }

  async function reviewTopup(
    request: TopUpAdminRequest,
    decision: "approved" | "rejected"
  ) {
    let note = "";

    if (decision === "approved") {
      if (!window.confirm(
        `Setujui Top Up ${request.user_email} sebesar ${request.token_amount.toLocaleString("id-ID")} token?`
      )) return;
    } else {
      const answer = window.prompt(
        `Alasan penolakan untuk ${request.user_email} (boleh dikosongkan):`
      );
      if (answer === null) return;
      note = answer;
    }

    setTopupBusy(request.id);
    setError(null);

    try {
      const res = await fetch(
        `${API_URL}/api/v1/topup/admin/requests/${request.id}/review`,
        {
          method: "POST",
          headers: {
            ...headers(),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            decision,
            admin_note: note,
          }),
        }
      );

      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || "Gagal memproses pengajuan Top Up.");
      }

      flash(data.message || "Pengajuan berhasil diproses.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memproses Top Up.");
    } finally {
      setTopupBusy(null);
    }
  }

  if (loading || !token || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-black text-gray-400">
        Memuat…
      </main>
    );
  }

  const isAdmin = user.role === "admin";

  return (
    <main className="min-h-screen bg-black text-gray-100">
      {/* Header */}
      <header className="flex items-center justify-between gap-2 border-b border-gray-800 px-3 py-3 sm:px-6 sm:py-4">
        <h1 className="text-base font-bold sm:text-xl">👑 Panel SuperAdmin</h1>
        <div className="flex shrink-0 gap-2">
          <a
            href="/"
            className="rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-300 hover:bg-gray-800 sm:px-4 sm:text-sm"
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
        <div className="mx-auto max-w-6xl px-3 py-4 sm:px-6 sm:py-8">
          {/* Statistik */}
          <div className="grid grid-cols-2 gap-2 sm:gap-4 md:grid-cols-5">
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
          <div className="mt-5 flex max-w-full gap-2 overflow-x-auto rounded-xl bg-gray-900 p-2 text-sm">
            <button
              onClick={() => setTab("topups")}
              className={`relative min-w-max shrink-0 rounded-lg px-3 py-3 font-medium ${
                tab === "topups" ? "bg-blue-600" : "text-gray-400"
              }`}
            >
              💳 Pembayaran
              {topupRequests.length > 0 && (
                <span className="absolute -top-1 -right-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-red-500 px-1 text-xs text-white">
                  {topupRequests.length}
                </span>
              )}
            </button>
            <button
              onClick={() => setTab("users")}
              className={`min-w-max shrink-0 rounded-lg px-3 py-3 font-medium ${
                tab === "users" ? "bg-blue-600" : "text-gray-400"
              }`}
            >
              👥 Pengguna
            </button>
            <button
              onClick={() => setTab("pending")}
              className={`relative min-w-max shrink-0 rounded-lg px-3 py-3 font-medium ${
                tab === "pending" ? "bg-blue-600" : "text-gray-400"
              }`}
            >
              ⏳ Persetujuan
              {pending.length > 0 && (
                <span className="absolute -top-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-red-500 text-xs text-white">
                  {pending.length}
                </span>
              )}
            </button>
            <button
              onClick={() => setTab("telegram")}
              className={`relative min-w-max shrink-0 rounded-lg px-3 py-3 font-medium ${
                tab === "telegram" ? "bg-blue-600" : "text-gray-400"
              }`}
            >
              🤖 Telegram Tertaut
              {(telegramLinked.length + telegramPending.length) > 0 && (
                <span className="absolute -top-1 -right-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-red-500 px-1 text-xs text-white">
                  {telegramLinked.length + telegramPending.length}
                </span>
              )}
            </button>

            <button
              onClick={() => setTab("tokens")}
              className={`min-w-max shrink-0 rounded-lg px-3 py-3 font-medium ${
                tab === "tokens" ? "bg-blue-600" : "text-gray-400"
              }`}
            >
              🎯 Token
            </button>

            <button
              onClick={() => setTab("review")}
              className={`relative min-w-max shrink-0 rounded-lg px-3 py-3 font-medium ${
                tab === "review" ? "bg-blue-600" : "text-gray-400"
              }`}
            >
              🛡️ Review Token
              {reservationReviews.length > 0 && (
                <span className="absolute -top-1 -right-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-amber-500 px-1 text-xs text-black">
                  {reservationReviews.length}
                </span>
              )}
            </button>
            <button
              onClick={() => setTab("skills")}
              className={`min-w-max shrink-0 rounded-lg px-3 py-3 font-medium ${
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

          {/* Tab persetujuan pendaftaran */}
          {tab === "pending" && (
            <div className="mt-6 space-y-3">
              {pending.length === 0 ? (
                <div className="rounded-xl border border-gray-800 bg-gray-900 p-8 text-center text-gray-500">
                  ✅ Tidak ada pendaftar yang menunggu persetujuan
                </div>
              ) : (
                pending.map((u) => (
                  <div
                    key={u.id}
                    className="flex flex-col gap-3 rounded-xl border border-amber-700/40 bg-gray-900 p-4 md:flex-row md:items-center md:justify-between"
                  >
                    <div>
                      <p className="font-semibold">
                        {u.full_name || "—"}{" "}
                        <span className="text-sm font-normal text-gray-400">
                          ({u.email})
                        </span>
                      </p>
                      <p className="mt-0.5 text-xs text-gray-400">
                        {u.profession || "umum"}
                        {u.organization ? ` · ${u.organization}` : ""} ·{" "}
                        mendaftar {new Date(u.created_at).toLocaleDateString("id-ID")}
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => decide(u, "approve")}
                        className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500"
                      >
                        ✅ Setujui
                      </button>
                      <button
                        onClick={() => decide(u, "reject")}
                        className="rounded-lg border border-red-700/50 px-4 py-2 text-sm font-semibold text-red-300 hover:bg-red-900/40"
                      >
                        ⛔ Tolak
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Telegram Tertaut */}
          {tab === "telegram" && (
            <div className="mt-6 space-y-8">
              <section className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-semibold">Akun Telegram Tertaut</h2>
                    <p className="text-sm text-gray-400">
                      Daftar pengguna yang sudah menghubungkan akun Telegram.
                    </p>
                  </div>
                  <button
                    onClick={load}
                    className="rounded-lg bg-gray-700 px-3 py-2 text-xs hover:bg-gray-600"
                  >
                    ↻ Muat Ulang
                  </button>
                </div>

                {telegramLinked.length === 0 ? (
                  <div className="rounded-xl border border-gray-800 bg-gray-900 p-8 text-center text-gray-500">
                    Belum ada akun Telegram yang tertaut.
                  </div>
                ) : (
                  <div className="overflow-x-auto rounded-xl border border-gray-800">
                    <table className="w-full min-w-[760px] text-left text-sm">
                      <thead className="bg-gray-900 text-gray-400">
                        <tr>
                          <th className="px-4 py-3">Email</th>
                          <th className="px-4 py-3">Nama</th>
                          <th className="px-4 py-3">Telegram</th>
                          <th className="px-4 py-3">Bot</th>
                          <th className="px-4 py-3">Status</th>
                          <th className="px-4 py-3">Tindakan</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-800">
                        {telegramLinked.map((account) => (
                          <tr key={account.id} className="bg-gray-950/50">
                            <td className="px-4 py-3 text-gray-300">
                              {account.user_email}
                            </td>
                            <td className="px-4 py-3">
                              {account.user_name || "—"}
                            </td>
                            <td className="px-4 py-3">
                              <div className="font-medium text-sky-300">
                                {account.telegram_username
                                  ? `@${account.telegram_username}`
                                  : "Username tidak tersedia"}
                              </div>
                              <div className="text-xs text-gray-500">
                                ID: {account.telegram_id}
                              </div>
                            </td>
                            <td className="px-4 py-3 text-gray-300">
                              @{account.bot_username}
                            </td>
                            <td className="px-4 py-3">
                              <span
                                className={`rounded-full px-2 py-1 text-xs ${
                                  account.status === "active"
                                    ? "bg-emerald-900/50 text-emerald-300"
                                    : "bg-amber-900/50 text-amber-300"
                                }`}
                              >
                                {account.status === "active"
                                  ? "Aktif"
                                  : account.status === "disabled"
                                    ? "Nonaktif"
                                    : account.status}
                              </span>
                            </td>
                            <td className="px-4 py-3">
                              <div className="flex flex-wrap gap-2">
                                {account.status === "active" ? (
                                  <button
                                    onClick={() =>
                                      changeTelegramStatus(account, false)
                                    }
                                    className="rounded-lg border border-amber-700/60 px-3 py-2 text-xs text-amber-300 hover:bg-amber-900/30"
                                  >
                                    Nonaktifkan
                                  </button>
                                ) : account.status === "disabled" ? (
                                  <button
                                    onClick={() =>
                                      changeTelegramStatus(account, true)
                                    }
                                    className="rounded-lg bg-emerald-700 px-3 py-2 text-xs text-white hover:bg-emerald-600"
                                  >
                                    Aktifkan
                                  </button>
                                ) : (
                                  <span className="text-xs text-gray-500">
                                    Tidak tersedia
                                  </span>
                                )}
                                <button
                                  onClick={() => removeTelegram(account)}
                                  className="rounded-lg border border-red-700/60 px-3 py-2 text-xs text-red-300 hover:bg-red-900/30"
                                >
                                  Hapus
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              <section className="space-y-3 border-t border-gray-800 pt-6">
                <div>
                  <h2 className="text-lg font-semibold">
                    Permintaan Koneksi Bot Menunggu Persetujuan
                  </h2>
                  <p className="text-sm text-gray-400">
                    Persetujuan pendaftaran bot sebelum pengguna menautkan akun Telegram.
                  </p>
                </div>

                {telegramPending.length === 0 ? (
                  <div className="rounded-xl border border-gray-800 bg-gray-900 p-6 text-center text-gray-500">
                    Tidak ada permintaan koneksi Telegram yang menunggu.
                  </div>
                ) : (
                  telegramPending.map((account) => (
                    <div
                      key={account.id}
                      className="flex flex-col gap-3 rounded-xl border border-amber-700/40 bg-gray-900 p-4 md:flex-row md:items-center md:justify-between"
                    >
                      <div>
                        <p className="font-semibold">🤖 @{account.bot_username}</p>
                        <p className="mt-1 text-sm text-gray-300">
                          {account.user_name || "Nama tidak tersedia"} ({account.user_email})
                        </p>
                        <p className="mt-1 text-xs text-gray-500">
                          Diajukan{" "}
                          {account.created_at
                            ? new Date(account.created_at).toLocaleString("id-ID")
                            : "—"}
                        </p>
                      </div>
                      <div className="flex gap-2">
                        <button
                          onClick={() => decideTelegram(account, "approve")}
                          className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500"
                        >
                          ✅ Setujui
                        </button>
                        <button
                          onClick={() => decideTelegram(account, "reject")}
                          className="rounded-lg border border-red-700/50 px-4 py-2 text-sm font-semibold text-red-300 hover:bg-red-900/40"
                        >
                          ⛔ Tolak
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </section>
            </div>
          )}

          {/* Tab persetujuan pembayaran Top Up */}
          {tab === "topups" && (
            <div className="mt-6 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-800 bg-gray-900 p-4">
                <div>
                  <h2 className="font-semibold">Persetujuan Pembayaran Top Up</h2>
                  <p className="mt-1 text-xs text-gray-400">
                    Periksa bukti QRIS sebelum menyetujui penambahan saldo token.
                  </p>
                </div>
                <button
                  onClick={load}
                  className="rounded-lg bg-gray-700 px-3 py-2 text-xs hover:bg-gray-600"
                >
                  ↻ Muat Ulang
                </button>
              </div>

              {topupRequests.length === 0 ? (
                <div className="rounded-xl border border-gray-800 bg-gray-900 p-8 text-center text-gray-500">
                  ✅ Tidak ada pembayaran yang menunggu persetujuan.
                </div>
              ) : (
                topupRequests.map((request) => (
                  <div
                    key={request.id}
                    className="rounded-xl border border-amber-700/40 bg-gray-900 p-4"
                  >
                    <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                      <div className="min-w-0">
                        <p className="font-semibold">
                          {request.user_name || "Pengguna"}
                        </p>
                        <p className="text-sm text-gray-400">{request.user_email}</p>
                        <div className="mt-3 space-y-1 text-sm">
                          <p>
                            Paket: <span className="font-semibold">{request.package_code}</span>
                          </p>
                          <p>
                            Pembayaran:{" "}
                            <span className="font-semibold text-amber-300">
                              {new Intl.NumberFormat("id-ID", {
                                style: "currency",
                                currency: "IDR",
                                maximumFractionDigits: 0,
                              }).format(request.amount_idr)}
                            </span>
                          </p>
                          <p>
                            Token:{" "}
                            <span className="font-semibold text-emerald-300">
                              {request.token_amount.toLocaleString("id-ID")}
                            </span>
                          </p>
                          <p className="text-xs text-gray-500">
                            Diajukan:{" "}
                            {request.created_at
                              ? new Date(request.created_at).toLocaleString("id-ID", {
                                  dateStyle: "medium",
                                  timeStyle: "short",
                                  timeZone: "Asia/Jayapura",
                                })
                              : "-"}
                          </p>
                        </div>
                      </div>

                      <div className="flex flex-wrap gap-2">
                        <button
                          onClick={() => openTopupProof(request)}
                          disabled={proofBusy === request.id}
                          className="rounded-lg border border-blue-600/50 px-3 py-2 text-sm text-blue-300 hover:bg-blue-900/30 disabled:opacity-50"
                        >
                          {proofBusy === request.id ? "Memuat..." : "📎 Lihat Bukti"}
                        </button>
                        <button
                          onClick={() => reviewTopup(request, "approved")}
                          disabled={topupBusy === request.id}
                          className="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
                        >
                          {topupBusy === request.id ? "Memproses..." : "✅ Setujui"}
                        </button>
                        <button
                          onClick={() => reviewTopup(request, "rejected")}
                          disabled={topupBusy === request.id}
                          className="rounded-lg border border-red-700/50 px-3 py-2 text-sm text-red-300 hover:bg-red-900/30 disabled:opacity-50"
                        >
                          ⛔ Tolak
                        </button>
                      </div>
                    </div>
                  </div>
                ))
              )}

              {proofUrl && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4">
                  <div className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-xl border border-gray-700 bg-gray-900 p-4">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <h3 className="font-semibold">Bukti Pembayaran</h3>
                      <button
                        onClick={() => {
                          URL.revokeObjectURL(proofUrl);
                          setProofUrl(null);
                          setProofType("");
                        }}
                        className="rounded-lg border border-gray-700 px-3 py-1 text-sm hover:bg-gray-800"
                      >
                        ✕ Tutup
                      </button>
                    </div>
                    {proofType.startsWith("image/") ? (
                      <img
                        src={proofUrl}
                        alt="Bukti pembayaran Top Up"
                        className="max-h-[75vh] w-full rounded-lg object-contain"
                      />
                    ) : (
                      <iframe
                        src={proofUrl}
                        title="Bukti pembayaran PDF"
                        className="h-[75vh] w-full rounded-lg bg-white"
                      />
                    )}
                  </div>
                </div>
              )}
            </div>
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
                            u.status === "active"
                              ? "bg-emerald-600/30 text-emerald-300"
                              : u.status === "pending"
                                ? "bg-amber-600/30 text-amber-300"
                                : "bg-red-600/30 text-red-300"
                          }`}
                        >
                          {u.status === "active"
                            ? "aktif"
                            : u.status === "pending"
                              ? "⏳ menunggu"
                              : "ditolak"}
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
                    <th className="px-4 py-3 text-right">Pemakaian Hari Ini</th>
                    <th className="px-4 py-3 text-right">Batas Harian</th>
                    <th className="px-4 py-3 text-right">Saldo Token</th>
                    <th className="px-4 py-3 text-center">Aksi</th>
                  </tr>
                </thead>
                <tbody>
                  {tokenRows.length === 0 && (
                    <tr>
                      <td colSpan={10} className="px-4 py-6 text-center text-gray-500">
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
                      <td className="px-4 py-3 text-right text-blue-300">
                        {r.daily_used.toLocaleString("id-ID")}
                      </td>
                      <td className="px-4 py-3 text-right text-gray-300">
                        {r.daily_limit === null
                          ? "Tanpa batas"
                          : r.daily_limit.toLocaleString("id-ID")}
                        <p className="text-xs text-gray-500">
                          Sisa: {r.daily_remaining === null
                            ? "—"
                            : r.daily_remaining.toLocaleString("id-ID")}
                        </p>
                      </td>
                      <td className="px-4 py-3 text-right font-semibold text-emerald-300">
                        {r.topup_balance.toLocaleString("id-ID")}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {r.role !== "admin" && (
                          <div className="flex flex-wrap justify-center gap-2">
                          <button
                            type="button"
                            onClick={() => addTokenBalance(r)}
                            className="rounded-lg border border-emerald-700 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-950"
                          >
                            Tambah Saldo
                          </button>
                          <button
                            type="button"
                            onClick={() => resetTokenBalance(r)}
                            disabled={r.topup_balance === 0}
                            className="rounded-lg border border-red-700 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-950 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            Reset Saldo
                          </button>
                          </div>
                        )}
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

          {/* Monitoring Review Token - baca saja */}
          {tab === "review" && (
            <div className="mt-6 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-800 bg-gray-900 p-4">
                <div>
                  <h2 className="font-semibold">Monitoring Reservasi Token</h2>
                  <p className="mt-1 text-xs text-gray-400">
                    Periksa bukti hasil AI dan setujui settlement setelah data token diverifikasi.
                  </p>
                </div>
                <button
                  onClick={load}
                  className="rounded-lg bg-gray-700 px-3 py-2 text-xs hover:bg-gray-600"
                >
                  ↻ Muat Ulang
                </button>
              </div>

              {reservationReviews.length === 0 ? (
                <div className="rounded-xl border border-gray-800 bg-gray-900 p-8 text-center text-gray-400">
                  ✅ Tidak ada reservasi yang memerlukan review.
                </div>
              ) : (
                reservationReviews.map((r) => (
                  <div
                    key={r.reservation_id}
                    className="space-y-3 rounded-xl border border-amber-700/40 bg-gray-900 p-4"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <p className="font-semibold text-white">
                          {r.full_name || "Pengguna"}
                        </p>
                        <p className="text-sm text-gray-400">{r.email}</p>
                      </div>
                      <div className="flex flex-wrap gap-2 text-xs">
                        <span className="rounded-lg bg-amber-900/50 px-2 py-1 text-amber-300">
                          Reservasi: {r.reservation_status}
                        </span>
                        {r.result_status && (
                          <span className="rounded-lg bg-blue-900/50 px-2 py-1 text-blue-300">
                            Hasil: {r.result_status}
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-3">
                      <div>
                        <p className="text-xs text-gray-500">Cadangan Top Up</p>
                        <p className="font-semibold">
                          {r.reserved_topup.toLocaleString("id-ID")}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-gray-500">Token Aktual</p>
                        <p className="font-semibold">
                          {r.actual_tokens.toLocaleString("id-ID")}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-gray-500">Total Hasil AI</p>
                        <p className="font-semibold text-amber-300">
                          {r.total_tokens == null
                            ? "Belum tersedia"
                            : r.total_tokens.toLocaleString("id-ID")}
                        </p>
                      </div>
                    </div>

                    <div className="grid gap-2 text-xs text-gray-400 md:grid-cols-2">
                      <p className="break-all">
                        ID reservasi: {r.reservation_id}
                      </p>
                      <p>Skill: {r.skill || "—"}</p>
                      <p>
                        Input: {r.prompt_tokens == null
                          ? "—"
                          : r.prompt_tokens.toLocaleString("id-ID")}
                        {" · "}
                        Output: {r.completion_tokens == null
                          ? "—"
                          : r.completion_tokens.toLocaleString("id-ID")}
                      </p>
                      <p>
                        Dibuat: {r.created_at
                          ? new Date(r.created_at).toLocaleString("id-ID")
                          : "—"}
                      </p>
                    </div>

                    {r.description && (
                      <p className="break-words rounded-lg bg-black p-3 text-xs text-gray-400">
                        Keterangan: {r.description}
                      </p>
                    )}

                    {r.result_preview && (
                      <details className="rounded-lg bg-black p-3">
                        <summary className="cursor-pointer text-sm text-blue-300">
                          Lihat ringkasan jawaban AI
                        </summary>
                        <p className="mt-3 whitespace-pre-wrap break-words text-sm text-gray-300">
                          {r.result_preview}
                        </p>
                      </details>
                    )}

                    {["pending", "needs_review"].includes(r.reservation_status) &&
                      ["received", "needs_review"].includes(r.result_status || "") &&
                      r.prompt_tokens !== null &&
                      r.completion_tokens !== null &&
                      r.total_tokens !== null &&
                      Number.isSafeInteger(r.prompt_tokens) &&
                      Number.isSafeInteger(r.completion_tokens) &&
                      Number.isSafeInteger(r.total_tokens) &&
                      r.prompt_tokens >= 0 &&
                      r.completion_tokens >= 0 &&
                      r.total_tokens > 0 &&
                      r.prompt_tokens <= 2147483647 &&
                      r.completion_tokens <= 2147483647 &&
                      r.total_tokens <= 2147483647 &&
                      r.prompt_tokens + r.completion_tokens === r.total_tokens &&
                      !!r.skill && (
                        <div className="border-t border-gray-800 pt-3">
                          <button
                            onClick={() => settleReservation(r)}
                            className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-600"
                          >
                            ✓ Setujui Settlement
                          </button>
                          <p className="mt-2 text-xs text-gray-500">
                            Memerlukan konfirmasi password Admin. Periksa bukti token sebelum menyetujui.
                          </p>
                        </div>
                      )}
                  </div>
                ))
              )}
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
      {pendingAdminAction && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="admin-password-title"
            className="w-full max-w-md rounded-2xl border border-gray-700 bg-gray-900 p-5 shadow-2xl"
          >
            <h2
              id="admin-password-title"
              className="text-lg font-semibold text-white"
            >
              Verifikasi Password Admin
            </h2>

            <p className="mt-2 text-sm text-gray-300">
              {pendingAdminAction.title}
            </p>

            <p className="mt-2 text-xs text-gray-400">
              Masukkan password akun admin yang sedang login untuk melanjutkan.
            </p>

            <form
              className="mt-4 space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                submitAdminPassword();
              }}
            >
              <input
                type="password"
                autoComplete="current-password"
                autoFocus
                value={adminPassword}
                onChange={(event) => setAdminPassword(event.target.value)}
                placeholder="Password login admin"
                disabled={adminActionBusy}
                className="w-full rounded-lg border border-gray-700 bg-black px-3 py-3 text-sm text-white outline-none focus:border-blue-500"
              />

              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  disabled={adminActionBusy}
                  onClick={() => {
                    setPendingAdminAction(null);
                    setAdminPassword("");
                    setError(null);
                  }}
                  className="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800 disabled:opacity-50"
                >
                  Batal
                </button>

                <button
                  type="submit"
                  disabled={adminActionBusy || !adminPassword.trim()}
                  className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {adminActionBusy ? "Memproses..." : "Konfirmasi"}
                </button>
              </div>
            </form>
          </div>
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
