"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export type AttachedFile = { id: string; filename: string; chars: number };

type StorageInfo = {
  status: string;
  eligible: boolean;
  package_code: string | null;
  used_bytes: number;
  used_mb: number;
  quota_bytes: number | null;
  quota_mb: number | null;
  remaining_bytes: number | null;
  remaining_mb: number | null;
  upload_allowed: boolean;
  purge_due: boolean;
};


/**
 * Tombol 📎 upload file (DOCX/PDF/TXT/MD) → dikirim ke backend →
 * menghasilkan file_id yang dilampirkan ke chat berikutnya.
 */
export default function UploadButton({
  token,
  onAttached,
  attached,
  onClear,
}: {
  token: string;
  attached: AttachedFile | null;
  onAttached: (f: AttachedFile) => void;
  onClear: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  const [storageLoading, setStorageLoading] = useState(true);

  const refreshStorage = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/v1/files/storage`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });

      if (!res.ok) {
        throw new Error("Informasi storage belum dapat dimuat.");
      }

      const data = await res.json();
      setStorage(data);
    } catch {
      setStorage(null);
    } finally {
      setStorageLoading(false);
    }
  }, [token]);

  useEffect(() => {
    refreshStorage();
  }, [refreshStorage]);

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_URL}/api/v1/files/upload`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Upload gagal");
      onAttached({
        id: data.id,
        filename: data.filename,
        chars: data.chars,
      });
      await refreshStorage();
      window.dispatchEvent(new Event("oma-storage-updated"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload gagal");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  const usedPercent =
    storage?.quota_bytes && storage.quota_bytes > 0
      ? Math.min(100, (storage.used_bytes / storage.quota_bytes) * 100)
      : 0;

  const packageName = storage?.package_code
    ? storage.package_code.charAt(0).toUpperCase() +
      storage.package_code.slice(1)
    : null;

  const statusLabel: Record<string, string> = {
    active: "Paket aktif",
    grace: "Masa tenggang 7 hari",
    upload_locked: "Upload dikunci",
    purge_due: "Masa penyimpanan berakhir",
    exempt: "Tidak terkena batas storage baru",
    not_eligible: "Belum dikenai kuota paket baru",
    subscription_unavailable: "Status paket perlu diperiksa",
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <input
          ref={inputRef}
          type="file"
          accept=".docx,.pdf,.txt,.md"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
        />
        <button
          onClick={() => inputRef.current?.click()}
          disabled={busy || attached !== null || storage?.upload_allowed === false}
          className="rounded-xl border border-gray-700 px-3 py-2 text-gray-300 hover:bg-gray-800 disabled:opacity-40"
          title="Lampirkan dokumen (DOCX/PDF/TXT/MD, maks 5MB)"
        >
          {busy ? "⏳ Mengunggah…" : "📎"}
        </button>
        {attached && (
          <div className="flex items-center gap-2 rounded-full bg-emerald-600/20 px-3 py-1 text-xs text-emerald-300">
            📎 {attached.filename} ({attached.chars} karakter)
            <button
              onClick={onClear}
              className="text-emerald-400 hover:text-emerald-200"
              title="Lepas lampiran"
            >
              ✕
            </button>
          </div>
        )}
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>
    </div>
  );
}
