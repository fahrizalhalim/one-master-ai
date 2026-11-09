"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/app/auth-context";
import DownloadButtons from "@/app/download-buttons";
import UploadButton, { type AttachedFile } from "@/app/upload-button";
import { useConversations } from "@/app/use-conversations";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

type Message = { role: "user" | "assistant"; content: string; skill?: string };

const PACKAGE_DISPLAY: Record<string, { icon: string; name: string }> = {
  starter: { icon: "🎓", name: "Starter" },
  regular: { icon: "⚡", name: "Reguler" },
  professional: { icon: "🏆", name: "Profesional" },
  business: { icon: "👑", name: "Bisnis" },

  // Alias kode paket lama di database.
  basic: { icon: "🎓", name: "Starter" },
  pro: { icon: "⚡", name: "Reguler" },
  premium: { icon: "🏆", name: "Profesional" },
};

function PackageStatus({ user }: { user: {
  role?: string;
  package_code?: string;
  package_name?: string;
  package_days_remaining?: number | null;
  package_is_default?: boolean;
} }) {
  if (user.role === "admin") {
    return (
      <p className="shrink-0 text-xs font-bold tracking-wider text-gray-300">
        ADMIN
      </p>
    );
  }

  const info = PACKAGE_DISPLAY[user.package_code || "starter"] || PACKAGE_DISPLAY.starter;
  const name = user.package_name || info.name;
  if (user.package_is_default !== false) {
    return (
      <p className="shrink-0 truncate text-xs font-bold text-gray-200" title={`Paket ${name}`}>
        <span className="mr-1 inline-block text-[1.5em] leading-none align-middle">{info.icon}</span>
        Paket {name}
      </p>
    );
  }

  const detail = `${user.package_days_remaining ?? 1} hari tersisa`;

  return (
    <p className="shrink-0 truncate text-xs font-bold text-gray-200" title={`Paket ${name} · ${detail}`}>
      <span className="mr-1 inline-block text-[1.5em] leading-none align-middle">{info.icon}</span>
      Paket {name} · {detail}
    </p>
  );
}

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
  const [modelId, setModelId] = useState("groq-gpt-oss-120b");
  const [modelOptions, setModelOptions] = useState<{ id: string; label: string }[]>([
    { id: "groq-gpt-oss-120b", label: "Groq · GPT-OSS 120B" },
    { id: "default", label: "Ai.sumopod (manual/cadangan)" },
  ]);
  const [busy, setBusy] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true);
  const [attached, setAttached] = useState<AttachedFile | null>(null);
  const [quota, setQuota] = useState<{
    unlimited: boolean;
    daily_used: number;
    daily_limit: number | null;
    daily_remaining: number | null;
    topup_balance: number | null;
  } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Muat statistik pemakaian dan saldo token
  const loadQuota = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch(`${API_URL}/api/v1/quota`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) setQuota(await res.json());
    } catch {
      /* kuota bersifat opsional */
    }
  }, [token]);

  useEffect(() => {
    if (token) loadQuota();
  }, [token, loadQuota]);

  // Muat pilihan model yang tersedia dari backend.
  useEffect(() => {
    if (!token) return;

    let active = true;

    fetch(`${API_URL}/api/v1/models`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (res) => {
        if (!res.ok) return;
        const data = await res.json();
        if (active && Array.isArray(data.models) && data.models.length > 0) {
          setModelOptions(data.models);
          if (!data.models.some((m: { id: string }) => m.id === modelId)) {
            setModelId("default");
          }
        }
      })
      .catch(() => {
        /* selector tetap menggunakan model utama */
      });

    return () => {
      active = false;
    };
  }, [token, modelId]);

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
          model_id: modelId,
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
      loadQuota();
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
    <main className="flex h-[100dvh] min-h-0 overflow-hidden overscroll-none bg-gray-950 text-gray-100">
      {/* Sidebar riwayat */}
      <aside
        className={`fixed inset-y-0 left-0 z-20 w-72 shrink-0 transform border-r border-gray-800 bg-gray-900 transition-all duration-300 md:static md:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        } ${sidebarCollapsed ? "md:-ml-72" : "md:ml-0"}`}
      >
        <div className="flex h-full flex-col">
          <div className="border-b border-gray-800 p-4">
            <div className="mb-3 flex items-center justify-between md:hidden">
              <span className="text-sm font-semibold text-gray-300">Menu</span>
              <button
                onClick={() => setSidebarOpen(false)}
                aria-label="Tutup sidebar"
                className="rounded-lg border border-gray-700 px-3 py-1 text-lg text-gray-300 hover:bg-gray-800"
              >
                ×
              </button>
            </div>
            <button
              onClick={startNew}
              className="w-full rounded-xl bg-blue-600 py-2.5 text-sm font-semibold hover:bg-blue-500"
            >
              ＋ Percakapan Baru
            </button>
          </div>
          <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto overscroll-contain p-2">
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
          <div className="max-h-[55dvh] shrink-0 overflow-y-auto border-t border-gray-800 p-3 md:max-h-none">
            <div className="mb-3 min-w-0">
              <div className="flex min-w-0 items-center justify-between gap-2">
                <p className="min-w-0 truncate text-sm font-semibold text-gray-200">
                  {user.full_name || user.email}
                </p>
                <PackageStatus user={user} />
              </div>
              {user.profession && (
                <p className="mt-0.5 truncate text-xs text-gray-500">
                  {user.profession}
                </p>
              )}
            </div>

            {quota && (
              <div className="mb-3 rounded-xl border border-gray-800 bg-gray-950/60 p-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-semibold text-gray-300">Kuota AI</p>
                  {quota.unlimited ? (
                    <span className="text-xs font-medium text-emerald-300">Tanpa batas</span>
                  ) : (
                    <span className="text-xs text-gray-400">Harian</span>
                  )}
                </div>

                {!quota.unlimited && (
                  <>
                    <div className="mt-3 flex items-center justify-between gap-2 text-xs">
                      <span className="text-gray-400">Pemakaian Hari Ini</span>
                      <span className="font-medium text-gray-200">
                        {quota.daily_used.toLocaleString("id-ID")} / {quota.daily_limit?.toLocaleString("id-ID") ?? "—"}
                      </span>
                    </div>
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-800">
                      <div
                        className="h-full rounded-full bg-blue-500"
                        style={{
                          width: `${quota.daily_limit && quota.daily_limit > 0
                            ? Math.min(100, (quota.daily_used / quota.daily_limit) * 100)
                            : 0}%`,
                        }}
                      />
                    </div>
                    <div className="mt-2 flex justify-between gap-2 text-xs">
                      <span className="text-gray-400">Sisa kuota</span>
                      <span className="text-emerald-300">
                        {quota.daily_remaining?.toLocaleString("id-ID") ?? "—"} token
                      </span>
                    </div>
                    <div className="mt-2 flex justify-between gap-2 border-t border-gray-800 pt-2 text-xs">
                      <span className="text-gray-400">Saldo Top Up</span>
                      <span className="text-gray-200">
                        {(quota.topup_balance ?? 0).toLocaleString("id-ID")} token
                      </span>
                    </div>
                  </>
                )}
              </div>
            )}

            <div className="flex flex-col gap-2">
              <a href="/telegram" className="rounded-lg border border-gray-800 px-3 py-2 text-center text-sm text-gray-300 transition hover:bg-gray-800">
                🤖 Integrasi Telegram
              </a>
              {user.role !== "admin" && (
                <a href="/topup" className="rounded-lg border border-gray-800 px-3 py-2 text-center text-sm text-gray-300 transition hover:bg-gray-800">
                  💳 Top Up Token
                </a>
              )}
              {user.role === "admin" && (
                <a href="/admin" className="rounded-lg border border-gray-800 px-3 py-2 text-center text-sm text-gray-300 transition hover:bg-gray-800">
                  👑 Panel Admin
                </a>
              )}
              <button
                onClick={logout}
                className="w-full rounded-lg border border-gray-800 px-3 py-2 text-sm text-gray-400 transition hover:bg-gray-800 hover:text-white"
              >
                Keluar
              </button>
            </div>
          </div>
        </div>
      </aside>

      {/* Overlay khusus HP */}
      {sidebarOpen && (
        <button
          type="button"
          aria-label="Tutup menu"
          onClick={() => setSidebarOpen(false)}
          className="fixed inset-0 z-10 bg-black/60 md:hidden"
        />
      )}

      {/* Area utama */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Header mobile */}
        <header className="flex shrink-0 items-center justify-between border-b border-gray-800 px-3 py-2.5 pt-[calc(env(safe-area-inset-top)+0.625rem)] md:hidden">
          <button
            onClick={() => setSidebarOpen(true)}
            aria-label="Buka menu"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-gray-800 text-lg text-gray-300 transition hover:bg-gray-800"
          >
            ☰
          </button>

          <div className="flex min-w-0 flex-1 items-center justify-center gap-2 px-2">
            <img src="/logo-gamalama.png" alt="Logo ONE MASTER AI" className="h-7 w-7 shrink-0 rounded-full object-cover" />
            <span className="truncate text-sm font-bold tracking-wide text-gray-100">
              ONE MASTER AI
            </span>
          </div>

          <button
            onClick={() => setSidebarOpen(true)}
            aria-label="Buka profil dan pengaturan"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-gray-700 bg-gray-900 text-sm font-semibold text-gray-200"
          >
            {(user.full_name || user.email).charAt(0).toUpperCase()}
          </button>
        </header>

                <header className="hidden shrink-0 border-b border-gray-800 px-5 py-3 md:block">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setSidebarCollapsed((value) => !value)}
              aria-label={sidebarCollapsed ? "Tampilkan sidebar" : "Sembunyikan sidebar"}
              title={sidebarCollapsed ? "Tampilkan menu" : "Sembunyikan menu"}
              className="flex h-10 w-10 items-center justify-center rounded-xl border border-gray-800 text-lg text-gray-300 transition hover:bg-gray-800"
            >
              ☰
            </button>
            <img src="/logo-gamalama.png" alt="Logo ONE MASTER AI" className="h-8 w-8 rounded-full object-cover" />
            <h1 className="text-base font-bold tracking-wide text-gray-100">
              ONE MASTER AI
            </h1>
          </div>
        </header>

        {/* Chat area */}
        <div className="min-h-0 flex-1 space-y-4 overflow-x-hidden overflow-y-auto overscroll-contain px-3 py-4 sm:px-6 sm:py-6">
          {messages.length === 0 && (
            <div className="mx-auto mt-16 max-w-md px-2 text-center text-gray-500 sm:mt-24">
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
                className={`max-w-[90%] break-words whitespace-pre-wrap [overflow-wrap:anywhere] rounded-2xl px-3 py-3 text-sm leading-relaxed sm:max-w-[75%] sm:px-4 ${
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

                {m.role === "assistant" && m.content.startsWith("⚠️") && (
                  <div className="mt-4 rounded-xl border border-amber-700/50 bg-gray-900 p-3">
                    <p className="mb-2 font-semibold text-amber-300">
                      Silakan hubungi Admin ONE MASTER AI
                    </p>
                    <p className="text-sm text-gray-300">
                      Fahrizal Halim (Mr. Ofu)
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <a
                        href="https://wa.me/6281331885883"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded-lg bg-green-700 px-3 py-2 text-xs font-medium text-white hover:bg-green-600"
                      >
                        WhatsApp Admin
                      </a>
                      <a
                        href="mailto:fahrizal.halim@gmail.com"
                        className="rounded-lg bg-blue-700 px-3 py-2 text-xs font-medium text-white hover:bg-blue-600"
                      >
                        Email Admin
                      </a>
                    </div>
                    <p className="mt-3 text-xs text-gray-400">
                      Telegram: 081331885883
                    </p>
                    <p className="mt-1 text-xs text-gray-500">
                      Sertakan pesan kesalahan di atas saat menghubungi admin.
                    </p>
                  </div>
                )}

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
        <div className="shrink-0 border-t border-gray-800 px-3 py-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] sm:px-6 sm:py-4">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder={attached ? `Tanya tentang "${attached.filename}"…` : "Tulis permintaan Anda…"}
            className="w-full rounded-xl border border-gray-800 bg-gray-900 px-4 py-3 text-base outline-none transition focus:border-blue-500"
            disabled={busy}
          />

          <div className="mt-3 flex min-w-0 items-center gap-1.5 sm:gap-2">
            <UploadButton
              token={token}
              attached={attached}
              onAttached={setAttached}
              onClear={() => setAttached(null)}
            />

            <select
              id="model-selector"
              aria-label="Model AI"
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              disabled={busy}
              style={{
                width: `${Math.max(8, ...modelOptions.map((model) => model.label.length)) + 4}ch`,
                maxWidth: "calc(100vw - 150px)",
              }}
              className="min-w-0 shrink rounded-lg border border-gray-800 bg-gray-900 px-2 py-2 text-xs outline-none transition focus:border-blue-500 sm:text-sm"
            >
              {modelOptions.map((model) => (
                <option key={model.id} value={model.id}>{model.label}</option>
              ))}
            </select>

            <div className="flex-1" />

            <button
              onClick={send}
              disabled={busy || (!input.trim() && !attached)}
              className="shrink-0 rounded-xl bg-blue-600 px-4 py-3 text-sm font-medium transition hover:bg-blue-500 disabled:opacity-40 sm:px-6 sm:text-base"
            >
              Kirim
            </button>
          </div>
        </div>
      </div>
    </main>
  );
}
