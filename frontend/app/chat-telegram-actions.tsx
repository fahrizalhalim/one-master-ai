"use client";
import { useState } from "react";

export type ChatAction =
  | { type: "command"; label: string; value: string }
  | { type: "download_file"; label: string; file_id: string; filename?: string }
  | { type: "run_automation"; label: string; workflow_id: string; workflow_name: string; fields: Array<{ name: string; label: string; required?: boolean }> };

export const SLASH_SUGGESTIONS: Array<{ cmd: string; desc: string; adminOnly?: boolean }> = [
  { cmd: "/automation", desc: "Daftar otomatisasi & tombol jalankan (Admin)", adminOnly: true },
  { cmd: "/automation_history", desc: "Riwayat 10 eksekusi otomatisasi (Admin)", adminOnly: true },
  { cmd: "/daftar_file", desc: "Daftar file di penyimpanan akun" },
  { cmd: "/cari_file ", desc: "Cari file berdasarkan kata kunci" },
  { cmd: "/folder", desc: "Lihat daftar folder penyimpanan" },
  { cmd: "/buat_folder ", desc: "Buat folder baru" },
  { cmd: "/baca ", desc: "Baca isi file (masukkan ID file)" },
  { cmd: "/unduh ", desc: "Unduh file (masukkan ID file)" },
  { cmd: "/storage", desc: "Cek kapasitas penyimpanan terpakai" },
  { cmd: "/status", desc: "Cek status akun, paket & sisa kuota" },
  { cmd: "/help", desc: "Tampilkan semua perintah cepat" },
];

export function ChatActionButtons({
  token, apiUrl, actions, onRunCommand, onAppendMessage,
}: {
  token: string; apiUrl: string; actions: ChatAction[];
  onRunCommand: (cmd: string) => void;
  onAppendMessage: (text: string, skill?: string) => void;
}) {
  const [activeWf, setActiveWf] = useState<Extract<ChatAction, { type: "run_automation" }> | null>(null);
  const [formVals, setFormVals] = useState<Record<string, string>>({});
  const [runningId, setRunningId] = useState<string | null>(null);

  async function execWorkflow(wf: Extract<ChatAction, { type: "run_automation" }>, params: Record<string, string>) {
    setRunningId(wf.workflow_id);
    try {
      const res = await fetch(`${apiUrl}/api/v1/automation/workflows/${wf.workflow_id}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ input_data: params }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Eksekusi gagal.");
      const out = data.execution?.result_data;
      const msg = (out && (out.message || out.text)) || data.message || "Workflow berhasil dijalankan.";
      onAppendMessage(`✅ [${wf.workflow_name}]\n${typeof msg === "string" ? msg : JSON.stringify(msg, null, 2)}`, "automation");
      setActiveWf(null); setFormVals({});
    } catch (e) {
      onAppendMessage(`❌ [${wf.workflow_name}] Gagal: ${e instanceof Error ? e.message : "Error"}`, "automation");
    } finally { setRunningId(null); }
  }

  async function downloadFile(fileId: string, preferredName?: string) {
    try {
      const res = await fetch(`${apiUrl}/api/v1/files/${encodeURIComponent(fileId)}/download`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error("File tidak ditemukan atau gagal diunduh.");
      const disp = res.headers.get("content-disposition") || "";
      const match = disp.match(/filename\*?=(?:UTF-8\x27\x27)?\"?([^\";]+)\"?/i);
      let rawName = (preferredName || (match ? decodeURIComponent(match[1]) : "") || `Dokumen_${fileId}.docx`).trim();
      if (!/\.(docx|pdf|xlsx|pptx|txt|csv|md|png|jpg|jpeg)$/i.test(rawName)) {
        rawName = `${rawName}.docx`;
      }
      const rawBlob = await res.blob();
      const mime = rawName.toLowerCase().endsWith(".pdf")
        ? "application/pdf"
        : rawName.toLowerCase().endsWith(".docx")
        ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        : (rawBlob.type || "application/octet-stream");
      const blob = new Blob([await rawBlob.arrayBuffer()], { type: mime });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = rawName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      onAppendMessage(`❌ Unduh gagal: ${e instanceof Error ? e.message : "Error"}`, "workspace");
    }
  }

  return (
    <div className="mt-3 space-y-2 border-t border-gray-700/70 pt-2.5">
      <div className="flex flex-wrap gap-1.5">
        {actions.map((act, idx) => (
          <button key={idx} type="button" disabled={runningId !== null}
            onClick={() => {
              if (act.type === "command") onRunCommand(act.value);
              else if (act.type === "download_file") void downloadFile(act.file_id, act.filename);
              else if (act.type === "run_automation") {
                if (!act.fields || act.fields.length === 0) void execWorkflow(act, {});
                else { setActiveWf(act); setFormVals({}); }
              }
            }}
            className="rounded-lg border border-blue-500/40 bg-blue-950/40 px-3 py-1.5 text-xs font-medium text-blue-200 transition hover:bg-blue-800/50 disabled:opacity-40">
            {act.type === "run_automation" && runningId === act.workflow_id ? "⏳ Menjalankan..." : act.label}
          </button>
        ))}
      </div>
      {activeWf && (
        <div className="space-y-2 rounded-xl border border-gray-700 bg-gray-900 p-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-blue-300">Parameter: {activeWf.workflow_name}</span>
            <button type="button" onClick={() => setActiveWf(null)} className="text-xs text-gray-400">✕</button>
          </div>
          {activeWf.fields.map((f) => (
            <input key={f.name} value={formVals[f.name] || ""} onChange={(e) => setFormVals({ ...formVals, [f.name]: e.target.value })}
              placeholder={`${f.label}${f.required ? " *" : ""}`} className="w-full rounded-lg border border-gray-700 bg-gray-950 px-2.5 py-1.5 text-xs text-white" />
          ))}
          <button type="button" disabled={runningId !== null} onClick={() => void execWorkflow(activeWf, formVals)}
            className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-40">
            {runningId ? "Menjalankan..." : "▶️ Jalankan Sekarang"}
          </button>
        </div>
      )}
    </div>
  );
}
