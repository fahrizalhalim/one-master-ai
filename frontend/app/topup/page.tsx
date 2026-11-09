"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/app/auth-context";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

type Package = {
  code: string;
  name: string;
  amount_idr: number;
  token_amount: number;
};

type TopUpRequest = {
  id: string;
  package_code: string;
  amount_idr: number;
  token_amount: number;
  status: "pending" | "approved" | "rejected";
  admin_note: string;
  created_at: string | null;
  reviewed_at: string | null;
};

function rupiah(value: number) {
  return new Intl.NumberFormat("id-ID", {
    style: "currency",
    currency: "IDR",
    maximumFractionDigits: 0,
  }).format(value);
}

function tanggal(value: string | null) {
  if (!value) return "-";
  return new Date(value).toLocaleString("id-ID", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Jayapura",
  });
}

export default function TopUpPage() {
  const { token, user, loading } = useAuth();
  const router = useRouter();

  const [packages, setPackages] = useState<Package[]>([]);
  const [requests, setRequests] = useState<TopUpRequest[]>([]);
  const [selected, setSelected] = useState("");
  const [proof, setProof] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const headers = useCallback(
    () => ({ Authorization: `Bearer ${token}` }),
    [token]
  );

  const loadData = useCallback(async () => {
    if (!token) return;

    try {
      const [packageRes, requestRes] = await Promise.all([
        fetch(`${API_URL}/api/v1/topup/packages`),
        fetch(`${API_URL}/api/v1/topup/requests`, {
          headers: headers(),
        }),
      ]);

      if (!packageRes.ok) throw new Error("Gagal memuat paket Top Up.");
      if (!requestRes.ok) throw new Error("Gagal memuat riwayat Top Up.");

      const packageData = await packageRes.json();
      const requestData = await requestRes.json();

      setPackages(packageData.packages || []);
      setRequests(requestData.requests || []);

      if (!selected && packageData.packages?.length) {
        setSelected(packageData.packages[0].code);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat data.");
    }
  }, [token, headers, selected]);

  useEffect(() => {
    if (!loading && !token) router.replace("/login");
  }, [loading, token, router]);

  useEffect(() => {
    if (token) loadData();
  }, [token, loadData]);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setSuccess("");

    if (!selected) {
      setError("Silakan pilih paket Top Up.");
      return;
    }

    if (!proof) {
      setError("Silakan unggah bukti pembayaran.");
      return;
    }

    if (proof.size > 5 * 1024 * 1024) {
      setError("Ukuran file maksimal 5 MB.");
      return;
    }

    setBusy(true);

    try {
      const body = new FormData();
      body.append("package_code", selected);
      body.append("proof", proof);

      const res = await fetch(`${API_URL}/api/v1/topup/requests`, {
        method: "POST",
        headers: headers(),
        body,
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : "Pengajuan Top Up gagal."
        );
      }

      setSuccess(data.message || "Pengajuan berhasil dikirim.");
      setProof(null);

      const input = document.getElementById(
        "payment-proof"
      ) as HTMLInputElement | null;

      if (input) input.value = "";

      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Pengajuan gagal.");
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
    <main className="min-h-screen bg-gray-950 px-4 py-8 text-gray-100">
      <div className="mx-auto max-w-4xl">
        <div className="mb-8 flex items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold">Top Up Token</h1>
            <p className="mt-1 text-sm text-gray-400">
              Pilih paket untuk menambah saldo ONE MASTER AI.
            </p>
          </div>
          <Link
            href="/"
            className="rounded-lg border border-gray-700 px-4 py-2 text-sm hover:bg-gray-800"
          >
            ← Kembali ke Chat
          </Link>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-red-800 bg-red-950/40 p-3 text-sm text-red-300">
            {error}
          </div>
        )}

        {success && (
          <div className="mb-4 rounded-lg border border-green-800 bg-green-950/40 p-3 text-sm text-green-300">
            {success}
          </div>
        )}

        <section className="mb-8">
          <h2 className="mb-3 text-lg font-semibold">Pilih Paket</h2>

          <div className="grid gap-3 sm:grid-cols-2">
            {packages.map((item) => (
              <button
                key={item.code}
                type="button"
                onClick={() => setSelected(item.code)}
                className={`rounded-xl border p-4 text-left transition ${
                  selected === item.code
                    ? "border-blue-500 bg-blue-950/50"
                    : "border-gray-700 bg-gray-900 hover:border-gray-500"
                }`}
              >
                <p className="font-semibold">{item.name}</p>
                <p className="mt-2 text-xl font-bold text-white">
                  {rupiah(item.amount_idr)}
                </p>
                <p className="mt-1 text-sm text-blue-300">
                  {item.token_amount.toLocaleString("id-ID")} token
                </p>
                {selected === item.code && (
                  <p className="mt-2 text-xs text-blue-300">
                    ✓ Paket dipilih
                  </p>
                )}
              </button>
            ))}
          </div>
        </section>

        <section className="mb-8 rounded-xl border border-gray-800 bg-gray-900 p-5">
          <h2 className="text-lg font-semibold">Pembayaran QRIS</h2>

          <div className="mt-4 flex flex-col items-center rounded-lg border border-gray-700 bg-white p-4">
            {selected ? (
              <img
                key={selected}
                src={`/qris/qris-${selected}.png`}
                alt={`QRIS paket ${selected}`}
                className="h-auto w-full max-w-sm object-contain"
              />
            ) : (
              <p className="py-12 text-gray-500">Pilih paket terlebih dahulu.</p>
            )}
          </div>
          <p className="mt-3 text-center text-xs text-gray-400">
            Pastikan nominal pembayaran sesuai paket yang dipilih.
          </p>

          <p className="mt-4 text-sm text-gray-400">
            Nominal paket terpilih:
          </p>
          <p className="text-xl font-bold">
            {rupiah(
              packages.find((item) => item.code === selected)?.amount_idr || 0
            )}
          </p>
        </section>

        <section className="mb-8 rounded-xl border border-gray-800 bg-gray-900 p-5">
          <h2 className="text-lg font-semibold">Unggah Bukti Pembayaran</h2>
          <p className="mt-1 text-sm text-gray-400">
            Format JPG, PNG, atau PDF. Maksimal 5 MB.
          </p>

          <form onSubmit={submit} className="mt-4 space-y-4">
            <input
              id="payment-proof"
              type="file"
              accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
              onChange={(e) => setProof(e.target.files?.[0] || null)}
              className="block w-full rounded-lg border border-gray-700 bg-gray-950 p-3 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-blue-700 file:px-3 file:py-2 file:text-white"
            />

            {proof && (
              <p className="text-sm text-gray-300">
                File dipilih: {proof.name}
              </p>
            )}

            <button
              type="submit"
              disabled={busy || !proof || !selected}
              className="w-full rounded-lg bg-blue-600 px-4 py-3 font-semibold hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? "Mengirim pengajuan…" : "Kirim Bukti Pembayaran"}
            </button>
          </form>

          <p className="mt-3 text-xs text-gray-500">
            Pengajuan akan menunggu pemeriksaan admin. Token baru masuk
            setelah pembayaran disetujui.
          </p>
        </section>

        <section className="rounded-xl border border-gray-800 bg-gray-900 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold">Riwayat Top Up</h2>
            <button
              type="button"
              onClick={loadData}
              className="text-sm text-blue-300 hover:text-blue-200"
            >
              Muat ulang
            </button>
          </div>

          {requests.length === 0 ? (
            <p className="py-6 text-center text-sm text-gray-500">
              Belum ada pengajuan Top Up.
            </p>
          ) : (
            <div className="space-y-3">
              {requests.map((item) => (
                <div
                  key={item.id}
                  className="rounded-lg border border-gray-700 p-4"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="font-semibold">
                      {rupiah(item.amount_idr)}
                    </p>
                    <span
                      className={`rounded-full px-3 py-1 text-xs font-semibold ${
                        item.status === "approved"
                          ? "bg-green-900 text-green-300"
                          : item.status === "rejected"
                            ? "bg-red-900 text-red-300"
                            : "bg-amber-900 text-amber-300"
                      }`}
                    >
                      {item.status === "approved"
                        ? "Disetujui"
                        : item.status === "rejected"
                          ? "Ditolak"
                          : "Menunggu verifikasi"}
                    </span>
                  </div>

                  <p className="mt-1 text-sm text-blue-300">
                    {item.token_amount.toLocaleString("id-ID")} token
                  </p>
                  <p className="mt-2 text-xs text-gray-500">
                    Diajukan: {tanggal(item.created_at)}
                  </p>

                  {item.admin_note && (
                    <p className="mt-2 text-sm text-gray-300">
                      Catatan admin: {item.admin_note}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
