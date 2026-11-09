"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/app/auth-context";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

type TelegramStatus = {
  connected: boolean;
  status: string;
  bot_username?: string | null;
  owner_linked?: boolean;
};

export default function TelegramPage() {
  const { token, user, loading } = useAuth();
  const router = useRouter();

  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [botToken, setBotToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [activationLink, setActivationLink] = useState("");

  const loadStatus = useCallback(async () => {
    if (!token) return;

    try {
      const res = await fetch(`${API_URL}/api/v1/telegram/status`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Gagal membaca status Telegram");

      setStatus(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal membaca status");
    }
  }, [token]);

  useEffect(() => {
    if (!loading && !token) router.replace("/login");
  }, [loading, token, router]);

  useEffect(() => {
    if (token) loadStatus();
  }, [token, loadStatus]);

  async function connectBot(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();

    if (!botToken.trim() || busy || !token) return;

    setBusy(true);
    setError("");
    setMessage("");

    try {
      const res = await fetch(`${API_URL}/api/v1/telegram/connect`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ bot_token: botToken.trim() }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Gagal menghubungkan bot");
      }

      setBotToken("");
      setMessage(data.message || "Bot berhasil didaftarkan.");
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Terjadi kesalahan");
    } finally {
      setBusy(false);
    }
  }

  async function disconnectBot() {
    if (busy || !token) return;

    const confirmed = window.confirm(
      "Hapus koneksi Telegram?\n\n" +
      "Webhook bot akan dilepas dan riwayat update Telegram untuk " +
      "koneksi ini akan ikut terhapus. Riwayat percakapan AI tidak dihapus.\n\n" +
      "Bot Token tidak dicabut dari BotFather. Lanjutkan?"
    );

    if (!confirmed) return;

    setBusy(true);
    setError("");
    setMessage("");
    setActivationLink("");

    try {
      const res = await fetch(`${API_URL}/api/v1/telegram/disconnect`, {
        method: "DELETE",
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Gagal menghapus koneksi Telegram");
      }

      setMessage(data.message || "Koneksi Telegram berhasil dihapus.");
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Terjadi kesalahan");
    } finally {
      setBusy(false);
    }
  }

  async function activateBot() {
    if (busy || !token) return;

    setBusy(true);
    setError("");
    setMessage("");
    setActivationLink("");

    try {
      const res = await fetch(`${API_URL}/api/v1/telegram/activate`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Gagal mengaktifkan bot Telegram");
      }

      setActivationLink(data.link_url || "");
      setMessage(data.message || "Webhook berhasil diaktifkan.");
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Terjadi kesalahan");
    } finally {
      setBusy(false);
    }
  }

  if (loading || !token || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-950 text-gray-400">
        Memuat…
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-950 px-4 py-6 text-gray-100 sm:px-8">
      <div className="mx-auto max-w-2xl">
        <a href="/" className="text-sm text-blue-400 hover:text-blue-300">
          ← Kembali ke ONE MASTER AI
        </a>

        <h1 className="mt-6 text-2xl font-bold">
          🤖 Integrasi Telegram
        </h1>
        <p className="mt-2 text-sm text-gray-400">
          Hubungkan bot Telegram milik Anda dengan ONE MASTER AI.
        </p>

        <section className="mt-6 rounded-2xl border border-gray-800 bg-gray-900 p-5">
          <h2 className="font-semibold">Status koneksi</h2>

          {status ? (
            <div className="mt-3">
              <p className={
                status.status === "active"
                  ? "font-semibold text-emerald-400"
                  : status.status === "pending"
                  ? "font-semibold text-amber-400"
                  : status.status === "approved"
                  ? "font-semibold text-blue-400"
                  : status.status === "rejected"
                  ? "font-semibold text-red-400"
                  : "text-gray-400"
              }>
                {status.status === "active"
                  ? "● Aktif"
                  : status.status === "pending"
                  ? "● Menunggu persetujuan admin"
                  : status.status === "approved"
                  ? "● Disetujui — menunggu aktivasi teknis"
                  : status.status === "rejected"
                  ? "● Permintaan ditolak"
                  : "● Belum terhubung"}
              </p>

              {status.bot_username && (
                <p className="mt-2 text-sm text-gray-300">
                  Bot: @{status.bot_username}
                </p>
              )}

              {status.status === "pending" && (
                <p className="mt-2 text-sm text-amber-300">
                  Token sudah divalidasi dan disimpan. Permintaan sedang menunggu
                  persetujuan administrator.
                </p>
              )}

              {status.status === "approved" && (
                <p className="mt-2 text-sm text-blue-300">
                  Administrator telah menyetujui pendaftaran bot. Webhook dan
                  pengaitan akun Telegram masih menunggu aktivasi teknis.
                </p>
              )}

              {status.status === "rejected" && (
                <p className="mt-2 text-sm text-red-300">
                  Permintaan koneksi bot ini ditolak oleh administrator.
                </p>
              )}

              {status.status === "active" && (
                <p className="mt-2 text-sm text-gray-400">
                  Pengaitan pemilik: {status.owner_linked ? "Sudah terhubung" : "Belum terhubung"}
                </p>
              )}
            </div>
          ) : (
            <p className="mt-3 text-sm text-gray-400">
              Memeriksa status koneksi…
            </p>
          )}

          <button
            type="button"
            onClick={loadStatus}
            disabled={busy}
            className="mt-4 rounded-lg border border-gray-700 px-4 py-2 text-sm hover:bg-gray-800 disabled:opacity-50"
          >
            Perbarui status
          </button>
        </section>

        {status && status.status !== "not_connected" && (
          <section className="mt-5 rounded-2xl border border-red-900/70 bg-gray-900 p-5">
            <h2 className="font-semibold text-red-300">
              Hapus koneksi Telegram
            </h2>

            <p className="mt-2 text-sm leading-relaxed text-gray-400">
              Hapus integrasi bot ini dari ONE MASTER AI. Webhook akan dilepas
              dan riwayat update Telegram pada koneksi ini ikut dihapus.
              Riwayat percakapan AI tetap tersimpan.
            </p>

            <p className="mt-2 text-xs text-amber-300">
              Penghapusan koneksi tidak mencabut Bot Token dari BotFather.
              Untuk mencabut token, lakukan secara terpisah melalui BotFather.
            </p>

            <button
              type="button"
              onClick={disconnectBot}
              disabled={busy}
              className="mt-4 w-full rounded-xl border border-red-700 bg-red-950/50 px-4 py-3 font-semibold text-red-300 hover:bg-red-900/60 disabled:opacity-50"
            >
              {busy ? "Menghapus koneksi…" : "Hapus Koneksi Telegram"}
            </button>
          </section>
        )}

        {status?.status === "approved" && (
          <section className="mt-5 rounded-2xl border border-blue-800 bg-gray-900 p-5">
            <h2 className="font-semibold text-blue-300">
              Aktivasi bot Telegram
            </h2>

            <p className="mt-2 text-sm leading-relaxed text-gray-400">
              Bot sudah disetujui administrator. Aktifkan webhook agar
              Telegram dapat mengirim proses pengaitan pemilik ke ONE MASTER AI.
            </p>

            <button
              type="button"
              onClick={activateBot}
              disabled={busy}
              className="mt-4 w-full rounded-xl bg-blue-600 px-4 py-3 font-semibold hover:bg-blue-500 disabled:opacity-50"
            >
              {busy ? "Mengaktifkan…" : "Aktifkan Bot Telegram"}
            </button>
          </section>
        )}

        {activationLink && (
          <section className="mt-5 rounded-2xl border border-emerald-800 bg-emerald-950/30 p-5">
            <h2 className="font-semibold text-emerald-300">
              Hubungkan akun Telegram Anda
            </h2>

            <p className="mt-2 text-sm leading-relaxed text-gray-300">
              Aktivasi webhook berhasil. Buka tautan berikut menggunakan
              akun Telegram yang ingin dihubungkan, kemudian tekan Start.
            </p>

            <a
              href={activationLink}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-4 block break-all rounded-lg border border-emerald-800 bg-gray-950 p-3 text-sm text-emerald-300 underline hover:text-emerald-200"
            >
              Buka bot Telegram dan tekan Start
            </a>

            <p className="mt-3 text-xs text-amber-300">
              Tautan pengaitan berlaku selama 15 menit. Jangan bagikan tautan
              ini kepada orang lain.
            </p>

            <button
              type="button"
              onClick={loadStatus}
              disabled={busy}
              className="mt-4 rounded-lg border border-gray-700 px-4 py-2 text-sm hover:bg-gray-800 disabled:opacity-50"
            >
              Periksa status pengaitan
            </button>
          </section>
        )}

        {(!status || status.status === "not_connected") && (
          <section className="mt-5 rounded-2xl border border-gray-800 bg-gray-900 p-5">
            <h2 className="font-semibold">Hubungkan bot baru</h2>

            <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm text-gray-400">
              <li>Buka Telegram dan cari akun resmi @BotFather.</li>
              <li>Buat bot baru menggunakan perintah /newbot.</li>
              <li>Salin Bot Token yang diberikan BotFather.</li>
              <li>Masukkan token pada formulir di bawah.</li>
            </ol>

            <form onSubmit={connectBot} className="mt-5 space-y-3">
              <label className="block text-sm font-medium">
                Bot Token
              </label>

              <input
                type="password"
                value={botToken}
                onChange={(e) => setBotToken(e.target.value)}
                placeholder="Masukkan Bot Token dari BotFather"
                autoComplete="off"
                required
                className="w-full rounded-xl border border-gray-700 bg-gray-950 px-4 py-3 text-sm outline-none focus:border-blue-500"
              />

              <p className="text-xs text-gray-500">
                Token dikirim langsung ke backend ONE MASTER AI dan
                disimpan dalam bentuk terenkripsi. Jangan bagikan token
                kepada orang lain.
              </p>

              <button
                type="submit"
                disabled={busy || !botToken.trim()}
                className="w-full rounded-xl bg-blue-600 px-4 py-3 font-semibold hover:bg-blue-500 disabled:opacity-50"
              >
                {busy ? "Memproses…" : "Hubungkan Bot Telegram"}
              </button>
            </form>
          </section>
        )}

        {message && (
          <p className="mt-4 rounded-xl border border-emerald-700/50 bg-emerald-950/30 p-4 text-sm text-emerald-300">
            {message}
          </p>
        )}

        {error && (
          <p className="mt-4 rounded-xl border border-red-700/50 bg-red-950/30 p-4 text-sm text-red-300">
            {error}
          </p>
        )}

        <p className="mt-6 text-xs leading-relaxed text-gray-500">
          Catatan: aktivasi memasang webhook dan memungkinkan pengaitan
          pemilik Telegram. Pemrosesan percakapan AI dan penggunaan kuota
          melalui Telegram masih merupakan tahap implementasi berikutnya.
        </p>
      </div>
    </main>
  );
}
