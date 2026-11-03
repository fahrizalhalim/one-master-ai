"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/app/auth-context";
import DownloadButtons from "@/app/download-buttons";
import UploadButton, { type AttachedFile } from "@/app/upload-button";
import { useConversations } from "@/app/use-conversations";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

type Message = { role: "user" | "assistant"; content: string; skill?: string };

export default function Home() {
  const { token, user, loading, logout } = useAuth();
  const router = useRouter();

  const {
    conversations,
    activeId,
    refresh,
    loadConversation,
    newConversation,
    deleteConversation,
  } = useConversations(token);

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [attached, setAttached] = useState<AttachedFile | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Guard: belum login -> ke /login
  useEffect(() => {
    if (!loading && !token) router.replace("/login");
  }, [loading, token, router]);

  // Auto-scroll ke bawah saat pesan baru
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  async function pickConversation(id: string) {
    const msgs = await loadConversation(id);
    if (msgs) {
      setMessages(
        msgs.map((m) => ({
          role: m.role as "user" | "assistant",
          content: m.content,
          skill: m.skill || undefined,
        }))
      );
      setSidebarOpen(false);
    }
  }

  function startNew() {
    newConversation();
    setMessages([]);
    setSidebarOpen(false);
  }

  async function removeConversation(id: string) {
    await deleteConversation(id);
    setMessages([]);
  }

  async function send() {
    const text = input.trim();
    if ((!text && !attached) || busy || !token) return;

    const displayText = attached
      ? `📎 ${attached.filename}\n${text || "Analisis dokumen ini."}`
      : text;
    setMessages((m) => [...m, { role: "user", content: displayText }]);
    setInput("");
    const fileId = attached?.id;
    setAttached(null);
    setBusy(true);

    try {
      const res = await fetch(`${API_URL}/api/v1/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          message: text || "Analisis dokumen ini.",
          conversation_id: activeId,
          file_id: fileId,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Gagal memproses");
      setMessages((m) => [
        ...m,
        { role: "assistant", content: data.answer, skill: data.skill },
      ]);
      // Percakapan baru mungkin saja terbuat — refresh sidebar
      refresh();
    } catch (err) {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: `⚠️ Terjadi kesalahan: ${err instanceof Error ? err.message : "tidak diketahui"}.`,
        },
      ]);
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
    <main className="flex h-screen bg-gray-950 text-gray-100">
      {/* Sidebar riwayat */}
      <aside
        className={`fixed inset-y-0 left-0 z-20 w-72 transform border-r border-gray-800 bg-gray-900 transition-transform md:static md:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-full flex-col">
          <div className="border-b border-gray-800 p-4">
            <button
              onClick={startNew}
              className="w-full rounded-xl bg-blue-600 py-2.5 text-sm font-semibold hover:bg-blue-500"
            >
              ＋ Percakapan Baru
            </button>
          </div>
          <nav className="flex-1 space-y-1 overflow-y-auto p-2">
            {conversations.length === 0 && (
              <p className="px-3 py-6 text-center text-xs text-gray-500">
                Belum ada riwayat percakapan
              </p>
            )}
            {conversations.map((c) => (
              <div
                key={c.id}
                className={`group flex items-center gap-1 rounded-lg px-2 ${
                  activeId === c.id ? "bg-gray-800" : "hover:bg-gray-800/60"
                }`}
              >
                <button
                  onClick={() => pickConversation(c.id)}
                  className="flex-1 truncate py-2.5 text-left text-sm text-gray-300"
                  title={c.title}
                >
                  {c.title}
                </button>
                <button
                  onClick={() => removeConversation(c.id)}
                  className="rounded p-1.5 text-gray-500 opacity-0 hover:text-red-400 group-hover:opacity-100"
                  title="Hapus"
                >
                  🗑
                </button>
              </div>
            ))}
          </nav>
          <div className="border-t border-gray-800 p-4">
            <p className="mb-2 truncate text-xs text-gray-400">
              {user.full_name || user.email}
            </p>
            <div className="flex flex-col gap-2">
              {user.role === "admin" && (
                <a
                  href="/admin"
                  className="rounded-lg border border-amber-600/50 py-2 text-center text-sm text-amber-300 hover:bg-amber-900/30"
                >
                  👑 Panel Admin
                </a>
              )}
              <button
                onClick={logout}
                className="w-full rounded-lg border border-gray-700 py-2 text-sm text-gray-300 hover:bg-gray-800"
              >
                Keluar
              </button>
            </div>
          </div>
        </div>
      </aside>

      {/* Area utama */}
      <div className="flex flex-1 flex-col">
        {/* Header */}
        <header className="flex items-center justify-between border-b border-gray-800 px-4 py-3 md:hidden">
          <button
            onClick={() => setSidebarOpen(true)}
            className="rounded-lg border border-gray-700 px-3 py-1.5 text-sm"
          >
            ☰ Riwayat
          </button>
          <h1 className="text-lg font-bold">🤖 ONE MASTER AI</h1>
          <div className="w-16" />
        </header>
        <header className="hidden border-b border-gray-800 px-6 py-4 md:block">
          <h1 className="text-xl font-bold tracking-tight">🤖 ONE MASTER AI</h1>
          <p className="text-xs text-gray-400">
            {user.full_name || user.email} · {user.profession}
          </p>
        </header>

        {/* Chat area */}
        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-6">
          {messages.length === 0 && (
            <div className="mx-auto mt-24 max-w-md text-center text-gray-500">
              <p className="text-4xl">💬</p>
              <p className="mt-3">
                Selamat datang, {user.full_name || user.email}! Tulis permintaan
                Anda — percakapan otomatis tersimpan di riwayat.
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
                {m.role === "assistant" && !m.content.startsWith("⚠️") && (
                  <DownloadButtons
                    token={token}
                    title={messages[0]?.role === "user" ? messages[0].content.slice(0, 60) : "Dokumen ONE MASTER AI"}
                    content={m.content}
                  />
                )}
              </div>
            </div>
          ))}
          {busy && (
            <div className="flex justify-start">
              <div className="rounded-2xl bg-gray-800 px-4 py-3 text-sm text-gray-400">
                Master AI sedang berpikir…
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="border-t border-gray-800 px-6 py-4">
          <UploadButton
            token={token}
            attached={attached}
            onAttached={setAttached}
            onClear={() => setAttached(null)}
          />
          <div className="mt-3 flex gap-3">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder={attached ? `Tanya tentang "${attached.filename}"…` : "Tulis permintaan Anda…"}
              className="flex-1 rounded-xl border border-gray-700 bg-gray-900 px-4 py-3 outline-none focus:border-blue-500"
              disabled={busy}
            />
            <button
              onClick={send}
              disabled={busy || (!input.trim() && !attached)}
              className="rounded-xl bg-blue-600 px-6 py-3 font-medium hover:bg-blue-500 disabled:opacity-40"
            >
              Kirim
            </button>
          </div>
        </div>
      </div>
    </main>
  );
}
