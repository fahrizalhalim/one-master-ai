"use client";

import { useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";
// Kosong = pakai domain yang sama (relatif), dilayani lewat reverse proxy Caddy.
// Untuk dev lokal isi NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 di .env.local

type Message = { role: "user" | "assistant"; content: string; skill?: string };

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [profession, setProfession] = useState("Dosen Teknik Informatika");
  const [loading, setLoading] = useState(false);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;

    setMessages((m) => [...m, { role: "user", content: text }]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch(`${API_URL}/api/v1/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, profession }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Gagal memproses");
      setMessages((m) => [
        ...m,
        { role: "assistant", content: data.answer, skill: data.skill },
      ]);
    } catch (err) {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: `⚠️ Terjadi kesalahan: ${err instanceof Error ? err.message : "tidak diketahui"}. Pastikan backend berjalan di ${API_URL}.`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex h-screen flex-col bg-gray-950 text-gray-100">
      {/* Header */}
      <header className="flex items-center justify-between border-b border-gray-800 px-6 py-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight">
            🤖 ONE MASTER AI
          </h1>
          <p className="text-xs text-gray-400">
            Asisten pekerjaan akademik, bisnis, teknologi & riset
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-gray-400">Profesi:</span>
          <input
            value={profession}
            onChange={(e) => setProfession(e.target.value)}
            className="w-56 rounded-md border border-gray-700 bg-gray-900 px-3 py-1.5 outline-none focus:border-blue-500"
          />
        </label>
      </header>

      {/* Chat area */}
      <div className="flex-1 space-y-4 overflow-y-auto px-6 py-6">
        {messages.length === 0 && (
          <div className="mx-auto mt-24 max-w-md text-center text-gray-500">
            <p className="text-4xl">💬</p>
            <p className="mt-3">
              Mulai percakapan. Contoh: &quot;Buatkan RPS mata kuliah Algoritma
              dengan pendekatan OBE&quot;
            </p>
          </div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[75%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                m.role === "user"
                  ? "bg-blue-600 text-white"
                  : "bg-gray-800 text-gray-100"
              }`}
            >
              {m.skill && (
                <span className="mb-2 inline-block rounded-full bg-emerald-600/30 px-2 py-0.5 text-xs text-emerald-300">
                  skill: {m.skill}
                </span>
              )}
              <p>{m.content}</p>
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="rounded-2xl bg-gray-800 px-4 py-3 text-sm text-gray-400">
              Master AI sedang berpikir…
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-gray-800 px-6 py-4">
        <div className="flex gap-3">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder="Tulis permintaan Anda…"
            className="flex-1 rounded-xl border border-gray-700 bg-gray-900 px-4 py-3 outline-none focus:border-blue-500"
            disabled={loading}
          />
          <button
            onClick={send}
            disabled={loading || !input.trim()}
            className="rounded-xl bg-blue-600 px-6 py-3 font-medium hover:bg-blue-500 disabled:opacity-40"
          >
            Kirim
          </button>
        </div>
      </div>
    </main>
  );
}
