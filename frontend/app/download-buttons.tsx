"use client";

import { useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

/**
 * Tombol unduh DOCX/PDF untuk satu jawaban AI.
 * Mengirim konten jawaban ke backend, menerima file, lalu mengunduhnya.
 */
export default function DownloadButtons({
  token,
  title,
  content,
}: {
  token: string;
  title: string;
  content: string;
}) {
  const [busy, setBusy] = useState<"docx" | "pdf" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function download(format: "docx" | "pdf") {
    setBusy(format);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/api/v1/documents/generate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ title, content, format }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Gagal membuat dokumen");

      // Unduh file lewat endpoint download (dengan token)
      const dl = await fetch(`${API_URL}${data.download_url}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!dl.ok) throw new Error("Gagal mengunduh dokumen");

      const blob = await dl.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = data.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mt-2 flex items-center gap-2">
      <button
        onClick={() => download("docx")}
        disabled={busy !== null}
        className="rounded-full border border-blue-400/40 px-3 py-1 text-xs text-blue-300 hover:bg-blue-600/20 disabled:opacity-40"
        title="Unduh sebagai Word"
      >
        {busy === "docx" ? "⏳ Membuat…" : "📄 Unduh DOCX"}
      </button>
      <button
        onClick={() => download("pdf")}
        disabled={busy !== null}
        className="rounded-full border border-red-400/40 px-3 py-1 text-xs text-red-300 hover:bg-red-600/20 disabled:opacity-40"
        title="Unduh sebagai PDF"
      >
        {busy === "pdf" ? "⏳ Membuat…" : "📕 Unduh PDF"}
      </button>
      {error && <span className="text-xs text-red-400">{error}</span>}
    </div>
  );
}
