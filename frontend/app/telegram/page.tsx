
"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/app/auth-context";
import TelegramIcon from "@/app/telegram-icon";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

type TelegramStatus = {
  bot_username: string | null;
  linked: boolean;
  telegram_username: string | null;
  linked_at: string | null;
};

export default function TelegramPage() {
  const { token, user, loading } = useAuth();
  const router = useRouter();

  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [linkUrl, setLinkUrl] = useState("");
  const [linkExpiresAt, setLinkExpiresAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const loadStatus = useCallback(async () => {
    if (!token) return;

    try {
      const res = await fetch(
        `${API_URL}/api/v1/telegram-central/status`,
        {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
        }
      );

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Gagal membaca status Telegram");
      }

      setStatus(data);

      if (data.linked) {
        setLinkUrl("");
        setLinkExpiresAt("");
      }

      setError("");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Gagal membaca status Telegram"
      );
    }
  }, [token]);

  useEffect(() => {
    if (!loading && !token) router.replace("/login");
  }, [loading, token, router]);

  useEffect(() => {
    if (token) loadStatus();
  }, [token, loadStatus]);

  // Periksa secara berkala apakah pengguna sudah menekan Start di Telegram.
  useEffect(() => {
    if (!token || !linkUrl || status?.linked) return;

    const timer = window.setInterval(() => {
      loadStatus();
    }, 5000);

    return () => window.clearInterval(timer);
  }, [token, linkUrl, status?.linked, loadStatus]);

  async function generateLink() {
    if (busy || !token) return;

    setBusy(true);
    setError("");
    setMessage("");

    try {
      const res = await fetch(
        `${API_URL}/api/v1/telegram-central/link-code`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        }
      );

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Gagal membuat tautan Telegram");
      }

      setLinkUrl(data.link);
      setLinkExpiresAt(
        new Date(
          Date.now() + Number(data.expires_in_seconds || 900) * 1000
        ).toISOString()
      );

      setMessage("Tautan pengaitan berhasil dibuat.");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Terjadi kesalahan"
      );
    } finally {
      setBusy(false);
    }
  }

  async function unlinkTelegram() {
    if (busy || !token) return;

    const confirmed = window.confirm(
      "Lepaskan akun Telegram dari ONE MASTER AI?\n\n" +
      "Tindakan ini hanya melepaskan hubungan akun Telegram Anda " +
      "dengan akun OM.AI.\n\n" +
      "Akun OM.AI, riwayat percakapan, pesan, dokumen, dan data lainnya " +
      "tetap dipertahankan.\n\n" +
      "Lanjutkan?"
    );

    if (!confirmed) return;

    setBusy(true);
    setError("");
    setMessage("");

    try {
      const res = await fetch(
        `${API_URL}/api/v1/telegram-central/unlink`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        }
      );

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Gagal melepas akun Telegram");
      }

      setLinkUrl("");
      setLinkExpiresAt("");
      setMessage("Akun Telegram berhasil dilepaskan dari OM.AI.");
      await loadStatus();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Terjadi kesalahan"
      );
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

        <h1 className="mt-6 flex items-center gap-3 text-2xl font-bold">
          <TelegramIcon size={30} />
          Integrasi Telegram
        </h1>

        <p className="mt-2 text-sm text-gray-400">
          Hubungkan akun Telegram Anda ke bot pusat ONE MASTER AI.
          Tidak perlu membuat bot sendiri atau memasukkan Bot Token.
        </p>

        <section className="mt-6 rounded-2xl border border-gray-800 bg-gray-900 p-5">
          <h2 className="font-semibold">Status pengaitan</h2>

          {!status ? (
            <p className="mt-3 text-sm text-gray-400">
              Memeriksa status Telegram…
            </p>
          ) : status.linked ? (
            <div className="mt-3">
              <p className="font-semibold text-emerald-400">
                ● Telegram sudah terhubung
              </p>

              <p className="mt-3 text-sm text-gray-300">
                Bot pusat:{" "}
                {status.bot_username ? `@${status.bot_username}` : "Belum dikonfigurasi"}
              </p>

              <p className="mt-1 text-sm text-gray-300">
                Akun Telegram:{" "}
                {status.telegram_username
                  ? `@${status.telegram_username}`
                  : "Terhubung"}
              </p>

              {status.linked_at && (
                <p className="mt-1 text-xs text-gray-500">
                  Terhubung sejak:{" "}
                  {new Date(status.linked_at).toLocaleString("id-ID")}
                </p>
              )}
            </div>
          ) : (
            <div className="mt-3">
              <p className="font-semibold text-amber-400">
                ● Belum terhubung
              </p>
              <p className="mt-2 text-sm text-gray-400">
                Akun OM.AI Anda belum dikaitkan dengan akun Telegram.
              </p>

              {status.bot_username && (
                <p className="mt-2 text-sm text-gray-300">
                  Bot pusat: @{status.bot_username}
                </p>
              )}
            </div>
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

        {status && !status.linked && (
          <section className="mt-5 rounded-2xl border border-blue-800 bg-gray-900 p-5">
            <h2 className="font-semibold text-blue-300">
              Hubungkan akun Telegram
            </h2>

            <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-relaxed text-gray-400">
              <li>Buat tautan pengaitan melalui tombol di bawah.</li>
              <li>Buka tautan tersebut menggunakan aplikasi Telegram.</li>
              <li>Tekan tombol Start pada bot pusat ONE MASTER AI.</li>
              <li>Kembali ke halaman ini untuk memeriksa status pengaitan.</li>
            </ol>

            {!linkUrl ? (
              <button
                type="button"
                onClick={generateLink}
                disabled={busy}
                className="mt-5 w-full rounded-xl bg-blue-600 px-4 py-3 font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
              >
                {busy ? "Membuat tautan…" : "Buat Tautan Pengaitan Telegram"}
              </button>
            ) : (
              <div className="mt-5">
                <a
                  href={linkUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block rounded-xl bg-blue-600 px-4 py-3 text-center font-semibold text-white hover:bg-blue-500"
                >
                  <span className="inline-flex items-center justify-center gap-2">
                    <TelegramIcon size={20} />
                    Buka Telegram dan Tekan Start
                  </span>
                </a>

                {linkExpiresAt && (
                  <p className="mt-3 text-xs text-amber-300">
                    Tautan berlaku sampai sekitar{" "}
                    {new Date(linkExpiresAt).toLocaleString("id-ID")}.
                  </p>
                )}

                <p className="mt-2 text-xs text-gray-500">
                  Tautan bersifat pribadi. Jangan bagikan kepada orang lain.
                  Jika kedaluwarsa, buat tautan baru.
                </p>

                <button
                  type="button"
                  onClick={generateLink}
                  disabled={busy}
                  className="mt-4 w-full rounded-xl border border-gray-700 px-4 py-3 text-sm font-medium hover:bg-gray-800 disabled:opacity-50"
                >
                  {busy ? "Memproses…" : "Buat Tautan Baru"}
                </button>
              </div>
            )}
          </section>
        )}

        {status?.linked && (
          <section className="mt-5 rounded-2xl border border-red-900/70 bg-gray-900 p-5">
            <h2 className="font-semibold text-red-300">
              Lepaskan akun Telegram
            </h2>

            <p className="mt-2 text-sm leading-relaxed text-gray-400">
              Gunakan tindakan ini jika ingin memutus hubungan akun Telegram
              dari akun OM.AI. Setelah dilepaskan, akun Telegram tersebut
              tidak lagi dapat menggunakan akun OM.AI ini melalui bot pusat
              sampai dilakukan pengaitan kembali.
            </p>

            <p className="mt-3 text-xs leading-relaxed text-emerald-300">
              Akun OM.AI, riwayat percakapan, pesan, dokumen, dan data
              pengguna lainnya tidak dihapus.
            </p>

            <button
              type="button"
              onClick={unlinkTelegram}
              disabled={busy}
              className="mt-4 w-full rounded-xl border border-red-700 bg-red-950/50 px-4 py-3 font-semibold text-red-300 hover:bg-red-900/60 disabled:opacity-50"
            >
              {busy ? "Memproses…" : "Lepaskan Akun Telegram"}
            </button>
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
          ONE MASTER AI menggunakan satu bot Telegram pusat untuk seluruh
          pengguna. Pengaitan dilakukan secara pribadi melalui kode sekali
          pakai yang berlaku selama 15 menit. Pesan Telegram diproses melalui
          layanan AI OM.AI dengan identitas akun masing-masing pengguna.
        </p>
      </div>
    </main>
  );
}
