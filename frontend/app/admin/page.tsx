"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/app/auth-context";
import TelegramIcon from "@/app/telegram-icon";

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

type AdminPackage = {
  id: string;
  code: string;
  name: string;
  description: string;
  price_idr: number;
  token_amount: number;
  daily_token_limit: number;
  duration_days: number;
  max_users: number | null;
  active_users: number;
  available_slots: number | null;
  is_full: boolean;
  sort_order: number;
  is_active: boolean;
  qris_image: string | null;
  qris_image_url: string | null;
  has_qris: boolean;
};

type PackageForm = {
  code: string;
  name: string;
  description: string;
  price_idr: string;
  token_amount: string;
  daily_token_limit: string;
  duration_days: string;
  max_users: string;
  sort_order: string;
  is_active: boolean;
};

type TelegramCentralReviewItem = {
  update_id: number;
  user_id: string | null;
  email: string | null;
  full_name: string | null;
  telegram_user_id: number;
  status: string;
  error_code: string | null;
  created_at: string | null;
  updated_at: string | null;
};

type TelegramCentralUser = {
  user_id: string;
  email: string;
  full_name: string | null;
  telegram_user_id: number;
  telegram_username: string | null;
  link_is_active: boolean;
  user_status: string;
  user_is_active: boolean;
  linked_at: string | null;
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
  const [tab, setTab] = useState<
  | "users"
  | "skills"
  | "tokens"
  | "pending"
  | "topups"
  | "packages"
  | "review"
  | "telegram-central"
>("users");
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [tokenRows, setTokenRows] = useState<TokenRow[]>([]);
  const [pending, setPending] = useState<AdminUser[]>([]);

const [telegramCentralReview, setTelegramCentralReview] =
  useState<TelegramCentralReviewItem[]>([]);
const [telegramCentralUsers, setTelegramCentralUsers] =
  useState<TelegramCentralUser[]>([]);
const [telegramCentralSection, setTelegramCentralSection] =
  useState<"users" | "monitoring">("users");
  const [topupRequests, setTopupRequests] = useState<TopUpAdminRequest[]>([]);
  const [adminPackages, setAdminPackages] = useState<AdminPackage[]>([]);
  const [packageEditor, setPackageEditor] = useState<PackageForm | null>(null);
  const [editingPackageCode, setEditingPackageCode] = useState<string | null>(null);
  const [packageBusy, setPackageBusy] = useState(false);
  const [qrisFiles, setQrisFiles] = useState<Record<string, File | null>>({});
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
        const [s, u, k, tk, pd, rv, tp, pk, tc, tu] = await Promise.all([
fetch(`${API_URL}/api/v1/admin/stats`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/admin/users`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/admin/skills`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/admin/tokens`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/admin/pending`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/admin/reservations/review`, { headers: headers() }),
        fetch(`${API_URL}/api/v1/topup/admin/requests?status_filter=pending`, {
          headers: headers(),
        }),
        fetch(`${API_URL}/api/v1/topup/admin/packages`, {
          headers: headers(),
        }),

        fetch(`${API_URL}/api/v1/admin/telegram-central/review`, {
          headers: headers(),
        }),
        fetch(`${API_URL}/api/v1/admin/telegram-central/users`, {
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


if (tc.ok) {
  const centralData = await tc.json();
  setTelegramCentralReview(centralData.items || []);
}
if (tu.ok) {
  const centralUsersData = await tu.json();
  setTelegramCentralUsers(centralUsersData.items || []);
}
      if (rv.ok) {
        const reviewData = await rv.json();
        setReservationReviews(reviewData.items || []);
      }
      if (tp.ok) {
        const topupData = await tp.json();
        setTopupRequests(topupData.requests || []);
      }
      if (pk.ok) {
        const packageData = await pk.json();
        setAdminPackages(packageData.packages || []);
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

  function openNewPackage() {
    setEditingPackageCode(null);
    setPackageEditor({
      code: "",
      name: "",
      description: "",
      price_idr: "17000",
      token_amount: "50000",
      daily_token_limit: "50000",
      duration_days: "7",
      max_users: "10",
      sort_order: String(adminPackages.length + 1),
      is_active: true,
    });
    setError(null);
  }

  function openEditPackage(pkg: AdminPackage) {
    setEditingPackageCode(pkg.code);
    setPackageEditor({
      code: pkg.code,
      name: pkg.name,
      description: pkg.description || "",
      price_idr: String(pkg.price_idr),
      token_amount: String(pkg.token_amount),
      daily_token_limit: String(pkg.daily_token_limit),
      duration_days: String(pkg.duration_days),
      max_users: pkg.max_users === null ? "" : String(pkg.max_users),
      sort_order: String(pkg.sort_order),
      is_active: pkg.is_active,
    });
    setError(null);
  }

  async function savePackage(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!packageEditor || packageBusy) return;

    const form = packageEditor;
    const positiveFields = [
      ["Harga", form.price_idr],
      ["Jumlah token", form.token_amount],
      ["Batas token harian", form.daily_token_limit],
      ["Durasi", form.duration_days],
    ] as const;

    for (const [label, raw] of positiveFields) {
      const value = Number(raw);
      if (!Number.isSafeInteger(value) || value <= 0) {
        setError(`${label} harus berupa bilangan bulat positif.`);
        return;
      }
    }

    const maxUsers = form.max_users.trim() === ""
      ? null
      : Number(form.max_users);

    if (
      maxUsers !== null &&
      (!Number.isSafeInteger(maxUsers) || maxUsers < 1)
    ) {
      setError("Kapasitas harus bilangan bulat minimal 1 atau dikosongkan untuk tanpa batas.");
      return;
    }

    const sortOrder = Number(form.sort_order);
    if (!Number.isSafeInteger(sortOrder) || sortOrder < 0) {
      setError("Urutan paket harus bilangan bulat nol atau lebih.");
      return;
    }

    if (!form.name.trim()) {
      setError("Nama paket wajib diisi.");
      return;
    }

    if (
      !editingPackageCode &&
      !/^[a-z0-9][a-z0-9_-]*$/.test(form.code)
    ) {
      setError("Kode paket hanya boleh berisi huruf kecil, angka, garis bawah, atau tanda hubung.");
      return;
    }

    const payload = {
      name: form.name.trim(),
      description: form.description.trim(),
      price_idr: Number(form.price_idr),
      token_amount: Number(form.token_amount),
      daily_token_limit: Number(form.daily_token_limit),
      duration_days: Number(form.duration_days),
      max_users: maxUsers,
      sort_order: sortOrder,
      is_active: form.is_active,
    };

    setPackageBusy(true);
    setError(null);

    try {
      const res = await fetch(
        editingPackageCode
          ? `${API_URL}/api/v1/topup/admin/packages/${encodeURIComponent(editingPackageCode)}`
          : `${API_URL}/api/v1/topup/admin/packages`,
        {
          method: editingPackageCode ? "PATCH" : "POST",
          headers: {
            ...headers(),
            "Content-Type": "application/json",
          },
          body: JSON.stringify(
            editingPackageCode ? payload : { ...payload, code: form.code }
          ),
        }
      );

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        setError(data.detail || "Gagal menyimpan paket.");
        return;
      }

      setPackageEditor(null);
      setEditingPackageCode(null);
      await load();
      flash(data.message || "Paket berhasil disimpan.");
    } catch {
      setError("Tidak dapat menghubungi server saat menyimpan paket.");
    } finally {
      setPackageBusy(false);
    }
  }

  async function uploadPackageQris(code: string) {
    const file = qrisFiles[code];
    if (!file || packageBusy) return;

    if (!["image/png", "image/jpeg"].includes(file.type)) {
      setError("QRIS harus berupa gambar PNG atau JPG.");
      return;
    }

    if (file.size <= 0 || file.size > 5 * 1024 * 1024) {
      setError("Ukuran gambar QRIS harus lebih dari 0 dan maksimal 5 MB.");
      return;
    }

    const body = new FormData();
    body.append("image", file);

    setPackageBusy(true);
    setError(null);

    try {
      const res = await fetch(
        `${API_URL}/api/v1/topup/admin/packages/${encodeURIComponent(code)}/qris`,
        {
          method: "POST",
          headers: headers(),
          body,
        }
      );

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        setError(data.detail || "Gagal mengunggah QRIS.");
        return;
      }

      setQrisFiles((current) => ({ ...current, [code]: null }));
      await load();
      flash(data.message || "QRIS berhasil diunggah.");
    } catch {
      setError("Tidak dapat menghubungi server saat mengunggah QRIS.");
    } finally {
      setPackageBusy(false);
    }
  }

  async function togglePackage(pkg: AdminPackage) {
    const nextActive = !pkg.is_active;

    const confirmed = window.confirm(
      `${nextActive ? "Aktifkan" : "Nonaktifkan"} paket ${pkg.name}?\n\n` +
      (nextActive
        ? "Paket akan ditampilkan kembali jika QRIS tersedia."
        : "Paket tidak dapat dipilih oleh pelanggan baru. Riwayat transaksi dan pelanggan lama tidak dihapus.")
    );

    if (!confirmed || packageBusy) return;

    setPackageBusy(true);
    setError(null);

    try {
      const res = await fetch(
        `${API_URL}/api/v1/topup/admin/packages/${encodeURIComponent(pkg.code)}`,
        {
          method: "PATCH",
          headers: {
            ...headers(),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ is_active: nextActive }),
        }
      );

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        setError(data.detail || "Gagal mengubah status paket.");
        return;
      }

      await load();
      flash(data.message || "Status paket berhasil diperbarui.");
    } catch {
      setError("Tidak dapat menghubungi server saat mengubah status paket.");
    } finally {
      setPackageBusy(false);
    }
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
              onClick={() => setTab("packages")}
              className={`min-w-max shrink-0 rounded-lg px-3 py-3 font-medium ${
                tab === "packages" ? "bg-blue-600" : "text-gray-400"
              }`}
            >
              📦 Paket
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
                onClick={() => setTab("telegram-central")}
                className={`relative min-w-max shrink-0 rounded-lg px-3 py-3 font-medium ${
                  tab === "telegram-central" ? "bg-blue-600" : "text-gray-400"
                }`}
              >
                <span className="flex items-center gap-2">
                  <TelegramIcon size={18} />
                  Telegram Pusat
                </span>

                {telegramCentralReview.length > 0 && (
                  <span className="absolute -top-1 -right-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-amber-500 px-1 text-xs text-black">
                    {telegramCentralReview.length}
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

{/* Monitoring Telegram Pusat */}
{tab === "telegram-central" && (
  <div className="mt-6 space-y-4">
    <div className="flex flex-wrap gap-2">
      <button
        onClick={() => setTelegramCentralSection("users")}
        className={`rounded-lg px-4 py-2 text-sm font-medium ${
          telegramCentralSection === "users"
            ? "bg-blue-600 text-white"
            : "bg-gray-800 text-gray-300"
        }`}
      >
        Pengguna Terhubung ({telegramCentralUsers.length})
      </button>
      <button
        onClick={() => setTelegramCentralSection("monitoring")}
        className={`rounded-lg px-4 py-2 text-sm font-medium ${
          telegramCentralSection === "monitoring"
            ? "bg-blue-600 text-white"
            : "bg-gray-800 text-gray-300"
        }`}
      >
        Monitoring Pesan ({telegramCentralReview.length})
      </button>
    </div>

    {telegramCentralSection === "users" ? (
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">
              Pengguna Telegram Terhubung
            </h2>
            <p className="text-sm text-gray-400">
              Daftar akun OM.AI yang memiliki identitas Telegram terhubung.
            </p>
          </div>
          <button
            onClick={load}
            className="rounded-lg bg-gray-700 px-3 py-2 text-xs hover:bg-gray-600"
          >
            ↻ Muat Ulang
          </button>
        </div>

        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <p className="text-sm text-gray-400">Total pengguna terhubung</p>
          <p className="mt-1 text-2xl font-bold">
            {telegramCentralUsers.length}
          </p>
        </div>

        {telegramCentralUsers.length === 0 ? (
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-8 text-center text-gray-400">
            Belum ada pengguna Telegram yang terhubung.
          </div>
        ) : (
          <div className="space-y-3">
            {telegramCentralUsers.map((item) => (
              <div
                key={item.user_id}
                className="rounded-xl border border-gray-800 bg-gray-900 p-4"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="font-semibold">
                      {item.full_name || "Nama tidak tersedia"}
                    </p>
                    <p className="text-sm text-gray-400">{item.email}</p>
                  </div>
                  <span className={`rounded-full px-3 py-1 text-xs ${
                    item.link_is_active && item.user_is_active &&
                    item.user_status === "active"
                      ? "bg-emerald-900/50 text-emerald-300"
                      : "bg-amber-900/50 text-amber-300"
                  }`}>
                    {item.link_is_active ? "Terhubung" : "Tidak aktif"}
                  </span>
                </div>

                <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                  <div>
                    <span className="text-gray-500">Telegram:</span>{" "}
                    <span className="text-gray-300">
                      {item.telegram_username
                        ? `@${item.telegram_username.replace(/^@/, "")}`
                        : "Username tidak tersedia"}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-500">Telegram ID:</span>{" "}
                    <span className="text-gray-300">
                      {item.telegram_user_id}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-500">Status akun OM.AI:</span>{" "}
                    <span className="text-gray-300">
                      {item.user_status} / {item.user_is_active ? "Aktif" : "Nonaktif"}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-500">Tanggal pengaitan:</span>{" "}
                    <span className="text-gray-300">
                      {item.linked_at
                        ? new Date(item.linked_at).toLocaleString("id-ID")
                        : "—"}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    ) : (
      <>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 className="text-lg font-semibold">
          Monitoring Telegram Pusat
        </h2>
        <p className="text-sm text-gray-400">
          Pemeriksaan pengiriman pesan dan pemulihan jawaban Telegram.
        </p>
      </div>

      <button
        onClick={load}
        className="rounded-lg bg-gray-700 px-3 py-2 text-xs hover:bg-gray-600"
      >
        ↻ Muat Ulang
      </button>
    </div>

    <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
      <p className="text-sm text-gray-400">
        Total kasus yang perlu diperiksa
      </p>
      <p className="mt-1 text-2xl font-bold">
        {telegramCentralReview.length}
      </p>
    </div>

    {telegramCentralReview.length === 0 ? (
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-8 text-center text-gray-400">
        <p className="text-2xl">✅</p>
        <p className="mt-2 font-medium">
          Tidak ada kasus Telegram yang perlu diperiksa.
        </p>
      </div>
    ) : (
      <div className="space-y-3">
        {telegramCentralReview.map((item) => {
          const retryAllowed =
            (item.status === "needs_review" &&
              [
                "chat_result_uncertain",
                "chat_processing_uncertain",
              ].includes(item.error_code || "")) ||
            (item.status === "reply_pending" &&
              item.error_code === "telegram_send_failed");

          const manualOnly =
            item.error_code === "telegram_delivery_uncertain";

          return (
            <div
              key={item.update_id}
              className="rounded-xl border border-gray-800 bg-gray-900 p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="font-semibold">
                    {item.full_name || "Nama tidak tersedia"}
                  </p>
                  <p className="text-sm text-gray-400">
                    {item.email || "Email tidak tersedia"}
                  </p>
                </div>

                <span
                  className={`rounded-full px-3 py-1 text-xs font-medium ${
                    manualOnly
                      ? "bg-red-900/50 text-red-300"
                      : retryAllowed
                        ? "bg-amber-900/50 text-amber-300"
                        : "bg-gray-800 text-gray-300"
                  }`}
                >
                  {manualOnly
                    ? "Pemeriksaan Manual"
                    : retryAllowed
                      ? "Dapat Dipulihkan"
                      : "Sedang Diproses"}
                </span>
              </div>

              <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                <div>
                  <span className="text-gray-500">Update ID:</span>{" "}
                  <span className="text-gray-300">{item.update_id}</span>
                </div>
                <div>
                  <span className="text-gray-500">Telegram ID:</span>{" "}
                  <span className="text-gray-300">{item.telegram_user_id}</span>
                </div>
                <div>
                  <span className="text-gray-500">Status:</span>{" "}
                  <span className="text-gray-300">{item.status}</span>
                </div>
                <div>
                  <span className="text-gray-500">Error:</span>{" "}
                  <span className="text-gray-300">{item.error_code || "—"}</span>
                </div>
                <div>
                  <span className="text-gray-500">Dibuat:</span>{" "}
                  <span className="text-gray-300">
                    {item.created_at
                      ? new Date(item.created_at).toLocaleString("id-ID")
                      : "—"}
                  </span>
                </div>
                <div>
                  <span className="text-gray-500">Diperbarui:</span>{" "}
                  <span className="text-gray-300">
                    {item.updated_at
                      ? new Date(item.updated_at).toLocaleString("id-ID")
                      : "—"}
                  </span>
                </div>
              </div>

              {manualOnly && (
                <div className="mt-3 rounded-lg border border-red-800/50 bg-red-950/30 p-3 text-xs text-red-300">
                  Pengiriman sebelumnya mungkin sudah diterima pengguna.
                  Jangan kirim ulang secara otomatis untuk mencegah pesan
                  ganda. Periksa riwayat pengiriman secara manual.
                </div>
              )}

              {retryAllowed && (
                <div className="mt-4">
                  <button
                    onClick={() =>
                      requestAdminPassword({
                        title:
                          `Verifikasi retry Telegram untuk Update ID ${item.update_id}. ` +
                          "Backend akan memeriksa jawaban tersimpan sebelum mengirim ulang.",
                        url:
                          `${API_URL}/api/v1/admin/telegram-central/` +
                          `${item.update_id}/retry-delivery`,
                        method: "POST",
                        body: {},
                        successMessage: "Permintaan pemulihan Telegram diproses.",
                      })
                    }
                    className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-500"
                  >
                    ↻ Verifikasi & Retry Pengiriman
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    )}
      </>
    )}
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

          {/* Manajemen paket langganan */}
          {tab === "packages" && (
            <div className="mt-6 space-y-5">
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-800 bg-gray-900 p-4">
                <div>
                  <h2 className="text-lg font-semibold">📦 Manajemen Paket</h2>
                  <p className="mt-1 text-xs text-gray-400">
                    Kelola harga, token, batas harian, durasi, kapasitas, dan QRIS setiap paket.
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={load}
                    className="rounded-lg border border-gray-700 px-3 py-2 text-sm hover:bg-gray-800"
                  >
                    ↻ Muat Ulang
                  </button>
                  <button
                    type="button"
                    onClick={openNewPackage}
                    className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500"
                  >
                    + Tambah Paket
                  </button>
                </div>
              </div>

              {packageEditor && (
                <form
                  onSubmit={savePackage}
                  className="space-y-4 rounded-xl border border-blue-700/50 bg-gray-900 p-4"
                >
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="font-semibold">
                      {editingPackageCode ? "Edit Paket" : "Buat Paket Baru"}
                    </h3>
                    <button
                      type="button"
                      onClick={() => {
                        setPackageEditor(null);
                        setEditingPackageCode(null);
                        setError(null);
                      }}
                      className="rounded-lg border border-gray-700 px-3 py-1 text-sm text-gray-300 hover:bg-gray-800"
                    >
                      Batal
                    </button>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2">
                    {!editingPackageCode && (
                      <label className="space-y-1 text-sm">
                        <span className="text-gray-300">Kode paket</span>
                        <input
                          required
                          pattern="[a-z0-9][a-z0-9_-]*"
                          maxLength={50}
                          value={packageEditor.code}
                          onChange={(event) =>
                            setPackageEditor({ ...packageEditor, code: event.target.value })
                          }
                          placeholder="contoh: premium"
                          className="w-full rounded-lg border border-gray-700 bg-black px-3 py-2 text-white outline-none focus:border-blue-500"
                        />
                      </label>
                    )}

                    <label className="space-y-1 text-sm">
                      <span className="text-gray-300">Nama paket</span>
                      <input
                        required
                        maxLength={100}
                        value={packageEditor.name}
                        onChange={(event) =>
                          setPackageEditor({ ...packageEditor, name: event.target.value })
                        }
                        className="w-full rounded-lg border border-gray-700 bg-black px-3 py-2 text-white outline-none focus:border-blue-500"
                      />
                    </label>

                    <label className="space-y-1 text-sm">
                      <span className="text-gray-300">Harga (Rupiah)</span>
                      <input
                        required
                        type="number"
                        min="1"
                        step="1"
                        value={packageEditor.price_idr}
                        onChange={(event) =>
                          setPackageEditor({ ...packageEditor, price_idr: event.target.value })
                        }
                        className="w-full rounded-lg border border-gray-700 bg-black px-3 py-2 text-white outline-none focus:border-blue-500"
                      />
                    </label>

                    <label className="space-y-1 text-sm">
                      <span className="text-gray-300">Jumlah token</span>
                      <input
                        required
                        type="number"
                        min="1"
                        step="1"
                        value={packageEditor.token_amount}
                        onChange={(event) =>
                          setPackageEditor({ ...packageEditor, token_amount: event.target.value })
                        }
                        className="w-full rounded-lg border border-gray-700 bg-black px-3 py-2 text-white outline-none focus:border-blue-500"
                      />
                    </label>

                    <label className="space-y-1 text-sm">
                      <span className="text-gray-300">Batas token harian</span>
                      <input
                        required
                        type="number"
                        min="1"
                        step="1"
                        value={packageEditor.daily_token_limit}
                        onChange={(event) =>
                          setPackageEditor({ ...packageEditor, daily_token_limit: event.target.value })
                        }
                        className="w-full rounded-lg border border-gray-700 bg-black px-3 py-2 text-white outline-none focus:border-blue-500"
                      />
                    </label>

                    <label className="space-y-1 text-sm">
                      <span className="text-gray-300">Durasi (hari)</span>
                      <input
                        required
                        type="number"
                        min="1"
                        max="365"
                        step="1"
                        value={packageEditor.duration_days}
                        onChange={(event) =>
                          setPackageEditor({ ...packageEditor, duration_days: event.target.value })
                        }
                        className="w-full rounded-lg border border-gray-700 bg-black px-3 py-2 text-white outline-none focus:border-blue-500"
                      />
                    </label>

                    <label className="space-y-1 text-sm">
                      <span className="text-gray-300">Kapasitas pengguna aktif</span>
                      <input
                        type="number"
                        min="1"
                        step="1"
                        value={packageEditor.max_users}
                        onChange={(event) =>
                          setPackageEditor({ ...packageEditor, max_users: event.target.value })
                        }
                        placeholder="Kosong = tidak terbatas"
                        className="w-full rounded-lg border border-gray-700 bg-black px-3 py-2 text-white outline-none focus:border-blue-500"
                      />
                    </label>

                    <label className="space-y-1 text-sm">
                      <span className="text-gray-300">Urutan tampilan</span>
                      <input
                        required
                        type="number"
                        min="0"
                        step="1"
                        value={packageEditor.sort_order}
                        onChange={(event) =>
                          setPackageEditor({ ...packageEditor, sort_order: event.target.value })
                        }
                        className="w-full rounded-lg border border-gray-700 bg-black px-3 py-2 text-white outline-none focus:border-blue-500"
                      />
                    </label>
                  </div>

                  <label className="block space-y-1 text-sm">
                    <span className="text-gray-300">Deskripsi</span>
                    <textarea
                      rows={2}
                      value={packageEditor.description}
                      onChange={(event) =>
                        setPackageEditor({ ...packageEditor, description: event.target.value })
                      }
                      className="w-full rounded-lg border border-gray-700 bg-black px-3 py-2 text-white outline-none focus:border-blue-500"
                    />
                  </label>

                  <label className="flex items-center gap-2 text-sm text-gray-300">
                    <input
                      type="checkbox"
                      checked={packageEditor.is_active}
                      onChange={(event) =>
                        setPackageEditor({ ...packageEditor, is_active: event.target.checked })
                      }
                      className="h-4 w-4 accent-blue-600"
                    />
                    Paket aktif
                  </label>

                  <p className="text-xs text-gray-500">
                    Kapasitas kosong berarti tidak terbatas. Jika kapasitas dikurangi di bawah jumlah pelanggan aktif, pelanggan lama tetap aktif dan pendaftaran baru akan dibatasi.
                  </p>

                  <button
                    type="submit"
                    disabled={packageBusy}
                    className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
                  >
                    {packageBusy ? "Menyimpan..." : "Simpan Paket"}
                  </button>
                </form>
              )}

              {adminPackages.length === 0 ? (
                <div className="rounded-xl border border-gray-800 bg-gray-900 p-8 text-center text-gray-500">
                  Belum ada data paket atau data belum berhasil dimuat.
                </div>
              ) : (
                <div className="grid gap-4 md:grid-cols-2">
                  {adminPackages.map((pkg) => (
                    <section
                      key={pkg.id}
                      className="space-y-3 rounded-xl border border-gray-800 bg-gray-900 p-4"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <h3 className="text-lg font-bold">{pkg.name}</h3>
                          <p className="text-xs text-gray-500">
                            Kode: {pkg.code} · Urutan: {pkg.sort_order}
                          </p>
                        </div>
                        <span
                          className={`rounded-full px-2 py-1 text-xs ${
                            pkg.is_active
                              ? "bg-emerald-900/50 text-emerald-300"
                              : "bg-gray-800 text-gray-400"
                          }`}
                        >
                          {pkg.is_active ? "Aktif" : "Nonaktif"}
                        </span>
                      </div>

                      <div className="grid grid-cols-2 gap-2 text-sm">
                        <div className="rounded-lg bg-black/40 p-3">
                          <p className="text-xs text-gray-500">Harga</p>
                          <p className="font-semibold">
                            {new Intl.NumberFormat("id-ID", {
                              style: "currency",
                              currency: "IDR",
                              maximumFractionDigits: 0,
                            }).format(pkg.price_idr)}
                          </p>
                        </div>
                        <div className="rounded-lg bg-black/40 p-3">
                          <p className="text-xs text-gray-500">Token paket</p>
                          <p className="font-semibold">
                            {pkg.token_amount.toLocaleString("id-ID")}
                          </p>
                        </div>
                        <div className="rounded-lg bg-black/40 p-3">
                          <p className="text-xs text-gray-500">Batas harian</p>
                          <p className="font-semibold">
                            {pkg.daily_token_limit.toLocaleString("id-ID")}
                          </p>
                        </div>
                        <div className="rounded-lg bg-black/40 p-3">
                          <p className="text-xs text-gray-500">Durasi</p>
                          <p className="font-semibold">{pkg.duration_days} hari</p>
                        </div>
                        <div className="rounded-lg bg-black/40 p-3">
                          <p className="text-xs text-gray-500">Pengguna aktif</p>
                          <p className="font-semibold">
                            {pkg.active_users.toLocaleString("id-ID")}
                          </p>
                        </div>
                        <div className="rounded-lg bg-black/40 p-3">
                          <p className="text-xs text-gray-500">Slot tersedia</p>
                          <p className="font-semibold">
                            {pkg.max_users === null
                              ? "Tidak terbatas"
                              : `${Math.max(0, pkg.available_slots ?? 0)} / ${pkg.max_users}`}
                          </p>
                        </div>
                      </div>

                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        <span className={pkg.has_qris ? "text-emerald-300" : "text-amber-300"}>
                          {pkg.has_qris ? "✓ QRIS tersedia" : "⚠ QRIS belum tersedia"}
                        </span>
                        {pkg.is_full && (
                          <span className="text-red-300">Kapasitas penuh</span>
                        )}
                      </div>

                      {pkg.qris_image_url && (
                        <div className="rounded-lg border border-gray-800 bg-black p-3">
                          <p className="mb-2 text-xs text-gray-400">QRIS saat ini</p>
                          <img
                            key={`${pkg.code}-${pkg.qris_image_url}`}
                            src={`${API_URL}${pkg.qris_image_url}`}
                            alt={`QRIS ${pkg.name}`}
                            className="mx-auto max-h-48 w-auto max-w-full object-contain"
                          />
                        </div>
                      )}

                      <div className="space-y-2 border-t border-gray-800 pt-3">
                        <label className="block text-xs text-gray-400">
                          Unggah/ganti QRIS (PNG/JPG, maksimal 5 MB)
                          <input
                            type="file"
                            accept="image/png,image/jpeg"
                            disabled={packageBusy}
                            onChange={(event) =>
                              setQrisFiles((current) => ({
                                ...current,
                                [pkg.code]: event.target.files?.[0] || null,
                              }))
                            }
                            className="mt-2 block w-full text-xs text-gray-300 file:mr-3 file:rounded-lg file:border-0 file:bg-gray-700 file:px-3 file:py-2 file:text-white"
                          />
                        </label>

                        {qrisFiles[pkg.code] && (
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="max-w-[65%] truncate text-xs text-gray-400">
                              {qrisFiles[pkg.code]?.name}
                            </span>
                            <button
                              type="button"
                              disabled={packageBusy}
                              onClick={() => uploadPackageQris(pkg.code)}
                              className="rounded-lg bg-emerald-700 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-600 disabled:opacity-50"
                            >
                              {packageBusy ? "Mengunggah..." : "Unggah QRIS"}
                            </button>
                          </div>
                        )}
                      </div>

                      <div className="flex flex-wrap gap-2 border-t border-gray-800 pt-3">
                        <button
                          type="button"
                          onClick={() => openEditPackage(pkg)}
                          className="rounded-lg border border-blue-600/50 px-3 py-2 text-xs text-blue-300 hover:bg-blue-900/30"
                        >
                          ✏️ Edit Paket
                        </button>
                        <button
                          type="button"
                          disabled={packageBusy}
                          onClick={() => togglePackage(pkg)}
                          className={`rounded-lg border px-3 py-2 text-xs disabled:opacity-50 ${
                            pkg.is_active
                              ? "border-red-700/50 text-red-300 hover:bg-red-900/30"
                              : "border-emerald-700/50 text-emerald-300 hover:bg-emerald-900/30"
                          }`}
                        >
                          {pkg.is_active ? "Nonaktifkan" : "Aktifkan"}
                        </button>
                      </div>
                    </section>
                  ))}
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
