"use client";
import { useState } from "react";
type FieldItem = { name: string; label: string; field_type: "string" | "number" | "boolean"; required: boolean };
type StepType = "wait" | "rss" | "http" | "ai" | "code" | "telegram";
type StepItem = { type: StepType; name: string; config: Record<string, string | number> };
type Props = { token: string; apiUrl: string; onCreated: (msg: string) => void };

export default function AutomationBuilder({ token, apiUrl, onCreated }: Props) {
  const [mode, setMode] = useState<"ai" | "template" | "visual">("ai");
  const [aiPrompt, setAiPrompt] = useState("");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [delivery, setDelivery] = useState<"sync" | "async_telegram">("sync");
  const [ackMsg, setAckMsg] = useState("Permintaan diterima. Hasil dikirim ke Telegram.");
  const [accessMode, setAccessMode] = useState<"all" | "selected">("all");
  const [autoPublish, setAutoPublish] = useState(true);
  const [fields, setFields] = useState<FieldItem[]>([]);
  const [steps, setSteps] = useState<StepItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  function loadTemplate(id: string) {
    setMode("visual"); setErr("");
    if (id === "news") {
      setName("Berita Trending (5 Menit)"); setDesc("Ambil berita RSS, tunggu 5 menit, rangkum AI, kirim Telegram.");
      setDelivery("async_telegram"); setFields([]);
      setAckMsg("Dijadwalkan! Ringkasan berita dikirim ke Telegram dalam 5 menit.");
      setSteps([
        { type: "wait", name: "Jeda 5 Menit", config: { amount: 5, unit: "minutes" } },
        { type: "rss", name: "Google News ID", config: { url: "https://news.google.com/rss?hl=id&gl=ID&ceid=ID:id", max_items: 10 } },
        { type: "ai", name: "Rangkum AI", config: { system_prompt: "Rangkum 5 berita trending bahasa Indonesia beserta tautannya.", user_prompt: "Berita:\n{{prev_data}}" } },
        { type: "telegram", name: "Kirim Telegram", config: { template: "{{message}}" } },
      ]);
    } else if (id === "research") {
      setName("Riset Topik AI"); setDesc("Ringkasan riset topik secara instan.");
      setDelivery("sync"); setFields([{ name: "topik", label: "Topik riset", field_type: "string", required: true }]);
      setSteps([{ type: "ai", name: "Analisis AI", config: { system_prompt: "Analisis ringkas, terstruktur, dan akurat.", user_prompt: "Riset topik: {{topik}}" } }]);
    } else if (id === "reminder") {
      setName("Pengingat Telegram"); setDesc("Kirim pengingat ke Telegram setelah jeda waktu.");
      setDelivery("async_telegram"); setAckMsg("Pengingat dicatat dan dikirim ke Telegram.");
      setFields([{ name: "pesan", label: "Isi pengingat", field_type: "string", required: true }]);
      setSteps([{ type: "wait", name: "Tunggu 5 Menit", config: { amount: 5, unit: "minutes" } }, { type: "telegram", name: "Kirim Pengingat", config: { template: "⏰ PENGINGAT:\n{{pesan}}" } }]);
    }
  }
  function addStep(type: StepType) {
    const def: Record<StepType, StepItem> = {
      wait: { type: "wait", name: "Jeda Waktu", config: { amount: 5, unit: "minutes" } },
      rss: { type: "rss", name: "Baca RSS", config: { url: "https://news.google.com/rss?hl=id&gl=ID&ceid=ID:id", max_items: 10 } },
      http: { type: "http", name: "Panggil API", config: { method: "GET", url: "", body_json: "" } },
      ai: { type: "ai", name: "Proses AI", config: { system_prompt: "Jawab ringkas dalam bahasa Indonesia.", user_prompt: "Olah data:\n{{prev_data}}" } },
      code: { type: "code", name: "Kode JS", config: { js_code: "return [{ json: { message: 'Selesai', data: $input.first().json } }];" } },
      telegram: { type: "telegram", name: "Kirim Telegram", config: { template: "{{message}}" } },
    };
    setSteps((prev) => [...prev, def[type]]);
  }
  async function handleCreate() {
    setBusy(true); setErr("");
    try {
      const res = await fetch(apiUrl + "/api/v1/admin/automation/create-workflow", {
        method: "POST", headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
        body: JSON.stringify({ mode: mode === "ai" ? "ai" : "builder", ai_prompt: aiPrompt, name, description: desc, delivery_mode: delivery, ack_message: ackMsg, input_fields: fields, steps, access_mode: accessMode, auto_publish: autoPublish }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Gagal membuat otomatisasi.");
      setAiPrompt(""); setName(""); setDesc(""); setFields([]); setSteps([]);
      onCreated(data.message || "Otomatisasi berhasil dibuat!");
    } catch (e) { setErr(e instanceof Error ? e.message : "Gagal membuat otomatisasi."); }
    finally { setBusy(false); }
  }
  return (
    <div className="space-y-4 rounded-xl border border-blue-800/60 bg-gray-900 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><h3 className="text-lg font-bold">✨ Buat Otomatisasi (Tanpa Buka n8n)</h3>
          <p className="text-xs text-gray-400">Otomatis dibuat di n8n, diaktifkan, dan masuk Registry OM.AI.</p></div>
        <div className="flex flex-wrap gap-2">
          {(["ai", "template", "visual"] as const).map((m) => (
            <button key={m} type="button" onClick={() => setMode(m)} className={"rounded-lg px-3 py-1.5 text-xs font-semibold " + (mode === m ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-300 hover:bg-gray-700")}>
              {m === "ai" ? "🤖 Generator AI" : m === "template" ? "⚡ Template 1-Klik" : "🧩 Penyusun Langkah"}
            </button>
          ))}
        </div>
      </div>
      {err && <p className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{err}</p>}
      {mode === "ai" && (
        <div className="space-y-3 rounded-xl border border-gray-800 bg-gray-950 p-4">
          <label className="block text-sm text-blue-300">Tulis instruksi otomatisasi dalam bahasa Indonesia:</label>
          <textarea rows={4} value={aiPrompt} onChange={(e) => setAiPrompt(e.target.value)} placeholder="Contoh: Ambil berita trending Google News ID, tunggu 5 menit, rangkum 5 berita utama dengan AI, kirim ke Telegram." className="w-full rounded-lg border border-gray-700 bg-gray-900 p-3 text-sm" />
          <div className="flex flex-wrap gap-2 text-xs">
            <button type="button" onClick={() => setAiPrompt("Ambil berita trending hari ini dari RSS Google News Indonesia, tunggu 5 menit, rangkum 5 berita paling penting dengan AI, lalu kirim ke Telegram pengguna.")} className="rounded bg-gray-800 px-2.5 py-1 hover:bg-gray-700">📰 Berita Trending 5 Menit</button>
            <button type="button" onClick={() => setAiPrompt("Terima input topik dari pengguna, buat ringkasan riset mendalam dan poin tindakan dengan AI, lalu balas langsung.")} className="rounded bg-gray-800 px-2.5 py-1 hover:bg-gray-700">🔍 Riset Topik AI</button>
            <button type="button" onClick={() => setAiPrompt("Terima input pesan pengingat dari pengguna, tunggu 5 menit, lalu kirim pengingat ke Telegram pengguna.")} className="rounded bg-gray-800 px-2.5 py-1 hover:bg-gray-700">⏰ Pengingat 5 Menit</button>
          </div>
        </div>
      )}
      {mode === "template" && (
        <div className="grid gap-3 md:grid-cols-3">
          {[
            { id: "news", t: "📰 Berita Trending 5 Menit", d: "RSS Google News → Jeda 5 Menit → Ringkas AI → Telegram." },
            { id: "research", t: "🔍 Riset Topik Instan", d: "Input topik pengguna → Analisis AI → Balas langsung." },
            { id: "reminder", t: "⏰ Pengingat Telegram", d: "Input pesan → Tunggu 5 menit → Kirim Telegram." },
          ].map((x) => (
            <button key={x.id} type="button" onClick={() => loadTemplate(x.id)} className="rounded-xl border border-gray-800 bg-gray-950 p-4 text-left hover:border-blue-600">
              <p className="font-semibold">{x.t}</p><p className="mt-1 text-xs text-gray-400">{x.d}</p>
              <p className="mt-2 text-xs text-blue-400">Pilih & sesuaikan →</p>
            </button>
          ))}
        </div>
      )}
      {mode === "visual" && (
        <div className="space-y-4 rounded-xl border border-gray-800 bg-gray-950 p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Nama Otomatisasi *" className="rounded-lg border border-gray-700 bg-gray-900 p-2 text-sm" />
            <select value={delivery} onChange={(e) => setDelivery(e.target.value as "sync" | "async_telegram")} className="rounded-lg border border-gray-700 bg-gray-900 p-2 text-sm">
              <option value="sync">Instan (Balas langsung Web & Telegram)</option>
              <option value="async_telegram">Tunda (Konfirmasi cepat → Kirim Telegram)</option>
            </select>
          </div>
          <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Deskripsi singkat untuk pengguna..." className="w-full rounded-lg border border-gray-700 bg-gray-900 p-2 text-sm" />
          {delivery === "async_telegram" && <input value={ackMsg} onChange={(e) => setAckMsg(e.target.value)} placeholder="Pesan konfirmasi awal..." className="w-full rounded-lg border border-gray-700 bg-gray-900 p-2 text-sm" />}
          <div className="space-y-2">
            <div className="flex items-center justify-between"><span className="text-xs font-semibold text-gray-300">Input Pengguna ({fields.length})</span>
              <button type="button" onClick={() => setFields([...fields, { name: "topik", label: "Topik", field_type: "string", required: true }])} className="rounded bg-gray-800 px-2.5 py-1 text-xs hover:bg-gray-700">+ Tambah Input</button></div>
            {fields.map((f, i) => (
              <div key={i} className="grid grid-cols-12 gap-2">
                <input value={f.name} onChange={(e) => setFields(fields.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} placeholder="nama_field" className="col-span-4 rounded border border-gray-700 bg-gray-900 p-1.5 text-xs" />
                <input value={f.label} onChange={(e) => setFields(fields.map((x, j) => j === i ? { ...x, label: e.target.value } : x))} placeholder="Label" className="col-span-5 rounded border border-gray-700 bg-gray-900 p-1.5 text-xs" />
                <label className="col-span-2 flex items-center gap-1 text-xs text-gray-300"><input type="checkbox" checked={f.required} onChange={(e) => setFields(fields.map((x, j) => j === i ? { ...x, required: e.target.checked } : x))} />Wajib</label>
                <button type="button" onClick={() => setFields(fields.filter((_, j) => j !== i))} className="col-span-1 text-xs text-red-400">✕</button>
              </div>
            ))}
          </div>
          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-semibold text-gray-300">Langkah Proses ({steps.length})</span>
              <div className="flex flex-wrap gap-1">{(["wait", "rss", "http", "ai", "code", "telegram"] as StepType[]).map((t) => (<button key={t} type="button" onClick={() => addStep(t)} className="rounded bg-gray-800 px-2 py-1 text-[11px] hover:bg-gray-700">+ {t.toUpperCase()}</button>))}</div>
            </div>
            {steps.map((s, i) => (
              <div key={i} className="space-y-2 rounded-lg border border-gray-800 bg-gray-900 p-3">
                <div className="flex items-center justify-between"><span className="text-xs font-semibold text-blue-300">#{i + 1} [{s.type.toUpperCase()}] {s.name}</span><button type="button" onClick={() => setSteps(steps.filter((_, j) => j !== i))} className="text-xs text-red-400">Hapus</button></div>
                {s.type === "wait" && <div className="flex gap-2"><input type="number" value={s.config.amount ?? 5} onChange={(e) => setSteps(steps.map((x, j) => j === i ? { ...x, config: { ...x.config, amount: Number(e.target.value) } } : x))} className="w-24 rounded border border-gray-700 bg-gray-950 p-1.5 text-xs" /><select value={String(s.config.unit || "minutes")} onChange={(e) => setSteps(steps.map((x, j) => j === i ? { ...x, config: { ...x.config, unit: e.target.value } } : x))} className="rounded border border-gray-700 bg-gray-950 p-1.5 text-xs"><option value="seconds">Detik</option><option value="minutes">Menit</option><option value="hours">Jam</option></select></div>}
                {s.type === "rss" && <input value={String(s.config.url || "")} onChange={(e) => setSteps(steps.map((x, j) => j === i ? { ...x, config: { ...x.config, url: e.target.value } } : x))} placeholder="URL RSS" className="w-full rounded border border-gray-700 bg-gray-950 p-1.5 text-xs" />}
                {s.type === "http" && <div className="flex gap-2"><select value={String(s.config.method || "GET")} onChange={(e) => setSteps(steps.map((x, j) => j === i ? { ...x, config: { ...x.config, method: e.target.value } } : x))} className="rounded border border-gray-700 bg-gray-950 p-1.5 text-xs"><option>GET</option><option>POST</option></select><input value={String(s.config.url || "")} onChange={(e) => setSteps(steps.map((x, j) => j === i ? { ...x, config: { ...x.config, url: e.target.value } } : x))} placeholder="https://api..." className="flex-1 rounded border border-gray-700 bg-gray-950 p-1.5 text-xs" /></div>}
                {s.type === "ai" && <textarea rows={2} value={String(s.config.user_prompt || "")} onChange={(e) => setSteps(steps.map((x, j) => j === i ? { ...x, config: { ...x.config, user_prompt: e.target.value } } : x))} placeholder="Prompt AI ({{prev_data}} / {{nama_field}})" className="w-full rounded border border-gray-700 bg-gray-950 p-1.5 text-xs" />}
                {s.type === "code" && <textarea rows={2} value={String(s.config.js_code || "")} onChange={(e) => setSteps(steps.map((x, j) => j === i ? { ...x, config: { ...x.config, js_code: e.target.value } } : x))} className="w-full rounded border border-gray-700 bg-gray-950 p-1.5 font-mono text-xs" />}
                {s.type === "telegram" && <textarea rows={2} value={String(s.config.template || "")} onChange={(e) => setSteps(steps.map((x, j) => j === i ? { ...x, config: { ...x.config, template: e.target.value } } : x))} placeholder="Pesan Telegram ({{message}})" className="w-full rounded border border-gray-700 bg-gray-950 p-1.5 text-xs" />}
              </div>
            ))}
          </div>
        </div>
      )}
      {mode !== "template" && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-800 pt-3">
          <div className="flex flex-wrap items-center gap-4 text-xs text-gray-300">
            <select value={accessMode} onChange={(e) => setAccessMode(e.target.value as "all" | "selected")} className="rounded border border-gray-700 bg-gray-950 px-2.5 py-1.5">
              <option value="all">Akses: Semua user aktif</option><option value="selected">Akses: Hanya terpilih</option>
            </select>
            <label className="flex items-center gap-1.5"><input type="checkbox" checked={autoPublish} onChange={(e) => setAutoPublish(e.target.checked)} />Langsung Publikasikan & Aktifkan</label>
          </div>
          <button type="button" disabled={busy} onClick={() => void handleCreate()} className="rounded-lg bg-blue-600 px-5 py-2 text-sm font-semibold hover:bg-blue-500 disabled:opacity-50">
            {busy ? "Membuat di n8n..." : mode === "ai" ? "✨ Buat dengan AI" : "🚀 Buat & Aktifkan"}
          </button>
        </div>
      )}
    </div>
  );
}
