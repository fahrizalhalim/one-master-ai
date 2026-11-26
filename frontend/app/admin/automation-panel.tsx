"use client";

import { useCallback, useEffect, useState } from "react";
import AutomationBuilder from "./automation-builder";

type AdminUser = {
  id: string;
  email: string;
  full_name: string;
  status: string;
  is_active: boolean;
};

type N8nWorkflow = {
  n8n_workflow_id: string;
  name: string;
  active: boolean;
  is_archived: boolean;
  created_at: string | null;
  updated_at: string | null;
};

type Workflow = {
  id: string;
  n8n_workflow_id: string;
  name: string;
  description: string;
  webhook_configured: boolean;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  access_mode: "all" | "selected";
  is_published: boolean;
  is_active: boolean;
  timeout_seconds: number;
  created_at: string | null;
  updated_at: string | null;
};

type AccessItem = {
  user_id: string;
  email: string;
  full_name: string | null;
  is_allowed: boolean;
  granted_by: string | null;
};

type Execution = {
  id: string;
  workflow_id: string | null;
  workflow_name: string;
  user_id: string;
  status: string;
  request_data: unknown;
  result_data: unknown;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  created_at: string | null;
};

type Props = {
  token: string;
  apiUrl: string;
  users: AdminUser[];
};

const base = "/api/v1/admin/automation";

function pretty(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2);
}

function dateLabel(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("id-ID");
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    running: "Berjalan",
    completed: "Selesai",
    failed: "Gagal",
    unknown: "Belum diketahui",
    queued: "Antrean",
  };
  return labels[status] || status;
}

export default function AutomationPanel({ token, apiUrl, users }: Props) {
  const [n8nItems, setN8nItems] = useState<N8nWorkflow[]>([]);
  const [registry, setRegistry] = useState<Workflow[]>([]);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [accessItems, setAccessItems] = useState<AccessItem[]>([]);
  const [selected, setSelected] = useState<Workflow | null>(null);
  const [selectedExecution, setSelectedExecution] = useState<Execution | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [section, setSection] = useState<"create" | "registry" | "n8n" | "executions">("registry");
  const [executionStatus, setExecutionStatus] = useState("");
  const [executionOffset, setExecutionOffset] = useState(0);
  const [executionTotal, setExecutionTotal] = useState(0);

  const [description, setDescription] = useState("");
  const [webhookPath, setWebhookPath] = useState("");
  const [inputSchema, setInputSchema] = useState("{}");
  const [outputSchema, setOutputSchema] = useState("{}");
  const [accessMode, setAccessMode] = useState<"all" | "selected">("selected");
  const [timeoutSeconds, setTimeoutSeconds] = useState(120);
  const [accessUserId, setAccessUserId] = useState("");
  const [testInput, setTestInput] = useState("{}");
  const [testResult, setTestResult] = useState<any>(null);

  const headers = useCallback(
    (json = false): HeadersInit => ({
      Authorization: `Bearer ${token}`,
      ...(json ? { "Content-Type": "application/json" } : {}),
    }),
    [token]
  );

  const request = useCallback(
    async (path: string, init: RequestInit = {}) => {
      const response = await fetch(`${apiUrl}${path}`, {
        ...init,
        headers: {
          ...headers(Boolean(init.body)),
          ...(init.headers || {}),
        },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : `Permintaan gagal (HTTP ${response.status}).`
        );
      }
      return data;
    },
    [apiUrl, headers]
  );

  const loadRegistry = useCallback(async () => {
    const data = await request(`${base}/registry?limit=100&offset=0`);
    setRegistry(data.items || []);
  }, [request]);

  const loadN8n = useCallback(async () => {
    const data = await request(`${base}/n8n/workflows?limit=100`);
    setN8nItems(data.items || []);
  }, [request]);

  const loadExecutions = useCallback(async (offset = executionOffset) => {
    const params = new URLSearchParams({
      limit: "50",
      offset: String(offset),
    });
    if (executionStatus) params.set("status", executionStatus);
    const data = await request(`${base}/executions?${params.toString()}`);
    setExecutions(data.items || []);
    setExecutionTotal(data.total || 0);
    setExecutionOffset(offset);
  }, [executionOffset, executionStatus, request]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      await Promise.all([loadRegistry(), loadN8n(), loadExecutions(0)]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat Automation.");
    } finally {
      setLoading(false);
    }
  }, [loadRegistry, loadN8n, loadExecutions]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const loadAccess = useCallback(async (workflowId: string) => {
    const data = await request(`${base}/registry/${workflowId}/access`);
    setAccessItems(data.items || []);
  }, [request]);

  function selectWorkflow(item: Workflow) {
    setSelected(item);
    setDescription(item.description || "");
    setWebhookPath("");
    setInputSchema(pretty(item.input_schema));
    setOutputSchema(pretty(item.output_schema));
    setAccessMode(item.access_mode || "selected");
    setTimeoutSeconds(item.timeout_seconds || 120);
    setAccessUserId("");
    setError("");
    void loadAccess(item.id).catch((err) => {
      setError(err instanceof Error ? err.message : "Gagal memuat hak akses.");
    });
  }

  async function importWorkflow(item: N8nWorkflow) {
    if (!window.confirm(`Impor "${item.name}" ke registry OM.AI?\n\nWorkflow hanya didaftarkan. Tidak otomatis dipublikasikan atau diaktifkan.`)) return;
    setBusy(`import-${item.n8n_workflow_id}`);
    setError("");
    setNotice("");
    try {
      const data = await request(`${base}/registry/import`, {
        method: "POST",
        body: JSON.stringify({ n8n_workflow_id: item.n8n_workflow_id }),
      });
      setNotice(data.message || "Workflow berhasil diimpor.");
      await Promise.all([loadRegistry(), loadN8n()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Impor workflow gagal.");
    } finally {
      setBusy(null);
    }
  }

  async function saveConfig() {
    if (!selected) return;
    let input: Record<string, unknown>;
    let output: Record<string, unknown>;
    try {
      input = JSON.parse(inputSchema);
      output = JSON.parse(outputSchema);
      if (!input || typeof input !== "object" || Array.isArray(input)) {
        throw new Error("Input Schema harus berupa JSON object.");
      }
      if (!output || typeof output !== "object" || Array.isArray(output)) {
        throw new Error("Output Schema harus berupa JSON object.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "JSON Schema tidak valid.");
      return;
    }

    const payload: Record<string, unknown> = {
      description,
      input_schema: input,
      output_schema: output,
      access_mode: accessMode,
      timeout_seconds: timeoutSeconds,
    };
    if (!selected.is_published && webhookPath.trim()) {
      payload.webhook_path = webhookPath.trim();
    }

    setBusy("save");
    setError("");
    setNotice("");
    try {
      const data = await request(`${base}/registry/${selected.id}/config`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      setNotice(data.message || "Konfigurasi tersimpan.");
      await loadRegistry();
      if (data.item) selectWorkflow(data.item);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menyimpan konfigurasi.");
    } finally {
      setBusy(null);
    }
  }

  async function testWorkflow() {
    if (!selected) return;

    let inputData: Record<string, unknown>;
    try {
      inputData = JSON.parse(testInput);
      if (!inputData || typeof inputData !== "object" || Array.isArray(inputData)) {
        throw new Error("Input pengujian harus berupa JSON object.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "JSON input tidak valid.");
      return;
    }

    if (!window.confirm(
      `Jalankan pengujian workflow "${selected.name}"?\n\n` +
      "Webhook n8n dapat menjalankan tindakan nyata seperti email, " +
      "perubahan data, atau pengiriman pesan."
    )) return;

    setBusy("test");
    setError("");
    setNotice("");
    setTestResult(null);

    try {
      const data = await request(`${base}/registry/${selected.id}/test`, {
        method: "POST",
        body: JSON.stringify({ input_data: inputData }),
      });
      setTestResult(data.execution || data);
      setNotice(
        data.success
          ? "Pengujian workflow berhasil."
          : "Pengujian selesai tetapi workflow gagal."
      );
      await loadExecutions(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Pengujian workflow gagal.");
    } finally {
      setBusy(null);
    }
  }

  async function changeStatus(
    item: Workflow,
    action: "publish" | "unpublish" | "activate" | "deactivate"
  ) {
    const labels = {
      publish: "Publikasikan",
      unpublish: "Batalkan publikasi",
      activate: "Aktifkan",
      deactivate: "Nonaktifkan",
    };
    const explanations: Record<string, string> = {
      publish: "Workflow akan terlihat di katalog internal setelah diaktifkan dan hak aksesnya terpenuhi.",
      unpublish: "Workflow akan dinonaktifkan dan tidak lagi tersedia bagi pengguna.",
      activate: "Workflow yang sudah dipublikasikan dapat digunakan oleh pengguna yang memiliki akses.",
      deactivate: "Pengguna tidak dapat memulai eksekusi baru workflow ini.",
    };
    if (!window.confirm(`${labels[action]} "${item.name}"?\n\n${explanations[action]}\n\nLanjutkan?`)) return;

    setBusy(action);
    setError("");
    setNotice("");
    try {
      const data = await request(`${base}/registry/${item.id}/${action}`, {
        method: "POST",
      });
      setNotice(data.message || `${labels[action]} berhasil.`);
      await loadRegistry();
      if (selected?.id === item.id && data.item) selectWorkflow(data.item);
    } catch (err) {
      setError(err instanceof Error ? err.message : `${labels[action]} gagal.`);
    } finally {
      setBusy(null);
    }
  }

  async function setUserAccess() {
    if (!selected || !accessUserId) {
      setError("Pilih pengguna terlebih dahulu.");
      return;
    }
    setBusy("access");
    setError("");
    try {
      await request(
        `${base}/registry/${selected.id}/access/${accessUserId}`,
        {
          method: "PUT",
          body: JSON.stringify({ is_allowed: true }),
        }
      );
      await loadAccess(selected.id);
      setNotice("Akses pengguna diberikan.");
      setAccessUserId("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengatur akses.");
    } finally {
      setBusy(null);
    }
  }

  async function revokeAccess(userId: string) {
    if (!selected) return;
    if (!window.confirm("Cabut akses workflow untuk pengguna ini?")) return;
    setBusy(`revoke-${userId}`);
    setError("");
    try {
      await request(`${base}/registry/${selected.id}/access/${userId}`, {
        method: "DELETE",
      });
      await loadAccess(selected.id);
      setNotice("Akses pengguna dicabut.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mencabut akses.");
    } finally {
      setBusy(null);
    }
  }

  async function deleteRegistryWorkflow(item: Workflow) {
    if (
      !window.confirm(
        `Hapus "${item.name}" dari Registry OM.AI?\n\n` +
        "Hak akses pengguna untuk workflow ini akan dihapus, sedangkan workflow di server n8n tetap ada."
      )
    ) return;
    setBusy(`del-reg-${item.id}`);
    setError("");
    setNotice("");
    try {
      const data = await request(`${base}/registry/${item.id}`, {
        method: "DELETE",
      });
      if (selected?.id === item.id) setSelected(null);
      setNotice(data.message || "Workflow dihapus dari Registry OM.AI.");
      await Promise.all([loadRegistry(), loadN8n()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menghapus workflow registry.");
    } finally {
      setBusy(null);
    }
  }

  async function deleteN8nWorkflow(item: N8nWorkflow) {
    if (
      !window.confirm(
        `Hapus permanen workflow "${item.name}" dari server n8n?\n\n` +
        "Tindakan ini tidak dapat dibatalkan."
      )
    ) return;
    setBusy(`del-n8n-${item.n8n_workflow_id}`);
    setError("");
    setNotice("");
    try {
      const data = await request(
        `${base}/n8n/workflows/${encodeURIComponent(item.n8n_workflow_id)}`,
        { method: "DELETE" }
      );
      setNotice(data.message || "Workflow berhasil dihapus dari n8n.");
      await loadN8n();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menghapus workflow dari n8n.");
    } finally {
      setBusy(null);
    }
  }

  async function deleteExecutionItem(item: Execution) {
    if (!window.confirm(`Hapus riwayat eksekusi "${item.workflow_name}"?`)) return;
    setBusy(`del-exec-${item.id}`);
    setError("");
    setNotice("");
    try {
      const data = await request(`${base}/executions/${item.id}`, {
        method: "DELETE",
      });
      if (selectedExecution?.id === item.id) setSelectedExecution(null);
      setNotice(data.message || "Riwayat eksekusi dihapus.");
      await loadExecutions(executionOffset);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menghapus riwayat eksekusi.");
    } finally {
      setBusy(null);
    }
  }

  async function clearExecutions() {
    const label = executionStatus ? `berstatus "${statusLabel(executionStatus)}"` : "seluruhnya";
    if (
      !window.confirm(
        `Hapus semua riwayat eksekusi ${label}?\n\nTindakan ini tidak dapat dibatalkan.`
      )
    ) return;
    setBusy("clear-exec");
    setError("");
    setNotice("");
    try {
      const qs = executionStatus ? `?status=${encodeURIComponent(executionStatus)}` : "";
      const data = await request(`${base}/executions${qs}`, {
        method: "DELETE",
      });
      setSelectedExecution(null);
      setNotice(data.message || "Riwayat eksekusi berhasil dibersihkan.");
      await loadExecutions(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal membersihkan riwayat eksekusi.");
    } finally {
      setBusy(null);
    }
  }

  async function showExecution(item: Execution) {
    setSelectedExecution(item);
    setError("");
    try {
      const data = await request(`${base}/executions/${item.id}`);
      setSelectedExecution(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat detail eksekusi.");
    }
  }

  const activeUsers = users.filter(
    (item) => item.is_active && item.status === "active"
  );
  const registeredIds = new Set(registry.map((item) => item.n8n_workflow_id));
  const availableUsers = activeUsers.filter(
    (item) => !accessItems.some(
      (access) => access.user_id === item.id && access.is_allowed
    )
  );

  return (
    <div className="mt-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold">⚙️ Automation Center</h2>
          <p className="mt-1 text-sm text-gray-400">
            Kelola workflow n8n melalui kontrol akses dan registry OM.AI.
          </p>
        </div>
        <button
          onClick={() => void loadAll()}
          disabled={loading}
          className="rounded-lg bg-gray-700 px-4 py-2 text-sm hover:bg-gray-600 disabled:opacity-50"
        >
          {loading ? "Memuat..." : "↻ Segarkan data"}
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Metric label="Registry OM.AI" value={registry.length} />
        <Metric label="Workflow n8n" value={n8nItems.length} />
        <Metric label="Workflow aktif" value={registry.filter((item) => item.is_active && item.is_published).length} />
        <Metric label="Pengguna aktif" value={activeUsers.length} />
      </div>

      {error && <Notice kind="error">{error}</Notice>}
      {notice && <Notice kind="success">{notice}</Notice>}

      <div className="flex flex-wrap gap-2 rounded-xl bg-gray-900 p-2">
        <SectionButton active={section === "create"} onClick={() => setSection("create")}>
          + Buat Otomatisasi
        </SectionButton>
        <SectionButton active={section === "registry"} onClick={() => setSection("registry")}>
          Registry OM.AI
        </SectionButton>
        <SectionButton active={section === "n8n"} onClick={() => setSection("n8n")}>
          Workflow n8n
        </SectionButton>
        <SectionButton active={section === "executions"} onClick={() => setSection("executions")}>
          Riwayat Eksekusi
        </SectionButton>
      </div>

      {section === "create" && (
        <AutomationBuilder token={token} apiUrl={apiUrl} onCreated={(msg) => { setNotice(msg); void loadAll(); setSection("registry"); }} />
      )}

      {section === "registry" && (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <h3 className="font-semibold">Workflow terdaftar ({registry.length})</h3>
            <button onClick={() => void loadRegistry()} className="text-xs text-blue-300 hover:text-blue-200">
              Muat ulang
            </button>
          </div>
          {registry.length === 0 ? (
            <Empty>Belum ada workflow yang diimpor ke OM.AI.</Empty>
          ) : (
            registry.map((item) => (
              <div key={item.id} className="rounded-xl border border-gray-800 bg-gray-900 p-4">
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div className="min-w-0">
                    <p className="font-semibold">{item.name}</p>
                    <p className="mt-1 break-all text-xs text-gray-500">
                      ID OM.AI: {item.id}
                    </p>
                    <p className="break-all text-xs text-gray-500">
                      ID n8n: {item.n8n_workflow_id}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <StatusPill active={item.is_published} label={item.is_published ? "Dipublikasikan" : "Belum dipublikasikan"} />
                      <StatusPill active={item.is_active} label={item.is_active ? "Aktif di OM.AI" : "Nonaktif di OM.AI"} />
                      <StatusPill active={item.webhook_configured} label={item.webhook_configured ? "Webhook dikonfigurasi" : "Webhook belum diatur"} />
                      <StatusPill active={item.access_mode === "all"} label={item.access_mode === "all" ? "Semua pengguna" : "Pengguna terpilih"} />
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <MiniButton onClick={() => selectWorkflow(item)}>Konfigurasi</MiniButton>
                    {!item.is_published ? (
                      <MiniButton disabled={busy !== null} onClick={() => void changeStatus(item, "publish")}>Publikasikan</MiniButton>
                    ) : (
                      <MiniButton disabled={busy !== null} onClick={() => void changeStatus(item, "unpublish")} danger>Unpublish</MiniButton>
                    )}
                    {item.is_active ? (
                      <MiniButton disabled={busy !== null} onClick={() => void changeStatus(item, "deactivate")} danger>Nonaktifkan</MiniButton>
                    ) : (
                      <MiniButton disabled={busy !== null || !item.is_published} onClick={() => void changeStatus(item, "activate")}>Aktifkan</MiniButton>
                    )}
                    <MiniButton disabled={busy !== null} onClick={() => void deleteRegistryWorkflow(item)} danger>
                      {busy === `del-reg-${item.id}` ? "Menghapus..." : "Hapus"}
                    </MiniButton>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {section === "n8n" && (
        <div className="space-y-3">
          <h3 className="font-semibold">Workflow pada server n8n ({n8nItems.length})</h3>
          {n8nItems.length === 0 ? (
            <Empty>Tidak ada workflow dari n8n atau daftar belum berhasil dimuat.</Empty>
          ) : (
            n8nItems.map((item) => {
              const imported = registeredIds.has(item.n8n_workflow_id);
              return (
                <div key={item.n8n_workflow_id} className="flex flex-col gap-3 rounded-xl border border-gray-800 bg-gray-900 p-4 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <p className="font-semibold">{item.name}</p>
                    <p className="mt-1 break-all text-xs text-gray-500">{item.n8n_workflow_id}</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <StatusPill active={item.active} label={item.active ? "Aktif di n8n" : "Nonaktif di n8n"} />
                      {item.is_archived && <StatusPill active={false} label="Diarsipkan" />}
                      <StatusPill active={imported} label={imported ? "Sudah diimpor" : "Belum diimpor"} />
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      disabled={imported || busy !== null || item.is_archived}
                      onClick={() => void importWorkflow(item)}
                      className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {busy === `import-${item.n8n_workflow_id}` ? "Mengimpor..." : imported ? "Sudah diimpor" : "Impor ke OM.AI"}
                    </button>
                    <button
                      disabled={busy !== null}
                      onClick={() => void deleteN8nWorkflow(item)}
                      className="rounded-lg border border-red-800 px-3 py-2 text-sm font-medium text-red-300 hover:bg-red-950/50 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {busy === `del-n8n-${item.n8n_workflow_id}` ? "Menghapus..." : "Hapus"}
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}

      {section === "executions" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="font-semibold">Riwayat eksekusi ({executionTotal})</h3>
            <div className="flex gap-2">
              <select
                value={executionStatus}
                onChange={(event) => {
                  setExecutionStatus(event.target.value);
                  setExecutionOffset(0);
                }}
                className="rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm"
              >
                <option value="">Semua status</option>
                <option value="running">Berjalan</option>
                <option value="completed">Selesai</option>
                <option value="failed">Gagal</option>
                <option value="unknown">Belum diketahui</option>
                <option value="queued">Antrean</option>
              </select>
              <button
                onClick={() => void loadExecutions(0)}
                className="rounded-lg bg-gray-700 px-3 py-2 text-xs"
              >
                ↻
              </button>
            </div>
          </div>
          {executions.length === 0 ? (
            <Empty>Belum ada riwayat eksekusi untuk filter ini.</Empty>
          ) : (
            executions.map((item) => (
              <div
                key={item.id}
                role="button"
                tabIndex={0}
                onClick={() => void showExecution(item)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    void showExecution(item);
                  }
                }}
                className="w-full cursor-pointer rounded-xl border border-gray-800 bg-gray-900 p-4 text-left hover:border-blue-700"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold">{item.workflow_name}</span>
                  <div className="flex items-center gap-2">
                    <StatusPill active={item.status === "completed"} label={statusLabel(item.status)} />
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={(e) => {
                        e.stopPropagation();
                        void deleteExecutionItem(item);
                      }}
                      className="rounded-lg border border-red-800 px-2.5 py-1 text-xs font-medium text-red-300 hover:bg-red-950/50 disabled:opacity-40"
                    >
                      {busy === `del-exec-${item.id}` ? "..." : "Hapus"}
                    </button>
                  </div>
                </div>
                <p className="mt-2 text-xs text-gray-400">
                  Pengguna: {item.user_id} · {dateLabel(item.created_at)}
                </p>
                <p className="mt-1 text-xs text-gray-500">
                  Durasi: {item.duration_ms == null ? "—" : `${item.duration_ms} ms`}
                </p>
                <p className="mt-2 text-xs text-blue-300">Lihat detail →</p>
              </div>
            ))
          )}
          <div className="flex items-center justify-between">
            <button
              disabled={executionOffset <= 0}
              onClick={() => void loadExecutions(Math.max(0, executionOffset - 50))}
              className="rounded-lg bg-gray-800 px-3 py-2 text-xs disabled:opacity-40"
            >
              ← Sebelumnya
            </button>
            <span className="text-xs text-gray-500">
              {executionTotal === 0 ? 0 : executionOffset + 1}–{Math.min(executionOffset + executions.length, executionTotal)} dari {executionTotal}
            </span>
            <button
              disabled={executionOffset + 50 >= executionTotal}
              onClick={() => void loadExecutions(executionOffset + 50)}
              className="rounded-lg bg-gray-800 px-3 py-2 text-xs disabled:opacity-40"
            >
              Berikutnya →
            </button>
          </div>
        </div>
      )}

      {selected && (
        <div className="space-y-4 rounded-xl border border-blue-800/60 bg-gray-900 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-lg font-bold">Konfigurasi: {selected.name}</h3>
              <p className="mt-1 text-xs text-gray-500">ID registry: {selected.id}</p>
            </div>
            <button
              onClick={() => setSelected(null)}
              className="rounded-lg border border-gray-700 px-3 py-1 text-sm text-gray-300"
            >
              Tutup
            </button>
          </div>

          <div>
            <label className="mb-1 block text-sm text-gray-300">Deskripsi</label>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={3}
              maxLength={5000}
              className="w-full rounded-lg border border-gray-700 bg-gray-950 p-3 text-sm"
            />
          </div>

          <div>
            <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
              <label className="text-sm text-gray-300">Webhook path (48 karakter hex lowercase)</label>
              <div className="flex gap-2">
                {!selected.is_published && (
                  <button
                    type="button"
                    onClick={() => {
                      const bytes = new Uint8Array(24);
                      window.crypto.getRandomValues(bytes);
                      const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
                      setWebhookPath(hex);
                      setNotice("Path webhook 48-hex baru dibuat. Salin ke node Webhook n8n lalu klik Simpan.");
                    }}
                    className="rounded border border-blue-700/70 bg-blue-950/60 px-2.5 py-1 text-xs text-blue-200 hover:bg-blue-900/60"
                  >
                    🎲 Buat Path 48-Hex
                  </button>
                )}
                {webhookPath && !selected.is_published && (
                  <button
                    type="button"
                    onClick={() => {
                      void navigator.clipboard.writeText(webhookPath);
                      setNotice("Webhook path disalin ke clipboard.");
                    }}
                    className="rounded border border-gray-700 bg-gray-800 px-2.5 py-1 text-xs text-gray-200 hover:bg-gray-700"
                  >
                    📋 Salin Path
                  </button>
                )}
              </div>
            </div>
            <input
              value={selected.is_published ? "Tidak dapat diubah saat dipublikasikan" : webhookPath}
              onChange={(event) => setWebhookPath(event.target.value)}
              disabled={selected.is_published}
              maxLength={48}
              placeholder={selected.webhook_configured ? "Path sudah tersimpan; isi hanya jika ingin menggantinya" : "Masukkan path webhook produksi"}
              className="w-full rounded-lg border border-gray-700 bg-gray-950 p-3 font-mono text-sm disabled:opacity-50"
            />
            <p className="mt-1 text-xs text-gray-500">
              Path bukan API key atau nilai autentikasi webhook. Jangan masukkan secret di sini.
            </p>
            {selected.webhook_configured && !selected.is_published && (
              <p className="mt-1 text-xs text-amber-300">
                Path tersimpan di backend. Kosongkan kolom untuk mempertahankan path yang ada.
              </p>
            )}
          </div>

          <div className="rounded-lg border border-blue-900/50 bg-blue-950/20 p-3 text-xs text-blue-200">
            <span className="font-semibold">Variabel Otomatis ke n8n:</span> Backend otomatis menyisipkan{" "}
            <code className="rounded bg-gray-900 px-1">telegram_chat_id</code>,{" "}
            <code className="rounded bg-gray-900 px-1">_omai_context.user_id</code>, dan{" "}
            <code className="rounded bg-gray-900 px-1">_omai_context.execution_id</code> ke body webhook tanpa perlu ditulis di schema.
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                <label className="text-sm text-gray-300">Input JSON Schema</label>
                <div className="flex flex-wrap gap-1">
                  <button
                    type="button"
                    onClick={() => {
                      setInputSchema(JSON.stringify({ type: "object", properties: {} }, null, 2));
                      setTestInput("{}");
                    }}
                    className="rounded bg-gray-800 px-2 py-0.5 text-[11px] text-gray-200 hover:bg-gray-700"
                  >
                    Tanpa Input
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setInputSchema(
                        JSON.stringify(
                          {
                            type: "object",
                            properties: {
                              topik: { type: "string", description: "Topik pencarian (opsional)" },
                            },
                          },
                          null,
                          2
                        )
                      );
                      setTestInput(JSON.stringify({ topik: "teknologi AI" }, null, 2));
                    }}
                    className="rounded bg-gray-800 px-2 py-0.5 text-[11px] text-gray-200 hover:bg-gray-700"
                  >
                    Topik Opsional
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setInputSchema(
                        JSON.stringify(
                          {
                            type: "object",
                            properties: {
                              topik: { type: "string", description: "Topik wajib diisi" },
                            },
                            required: ["topik"],
                          },
                          null,
                          2
                        )
                      );
                      setTestInput(JSON.stringify({ topik: "berita nasional" }, null, 2));
                    }}
                    className="rounded bg-gray-800 px-2 py-0.5 text-[11px] text-gray-200 hover:bg-gray-700"
                  >
                    Topik Wajib
                  </button>
                </div>
              </div>
              <textarea
                value={inputSchema}
                onChange={(event) => setInputSchema(event.target.value)}
                rows={12}
                spellCheck={false}
                className="w-full rounded-lg border border-gray-700 bg-gray-950 p-3 font-mono text-xs"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm text-gray-300">Output JSON Schema</label>
              <textarea
                value={outputSchema}
                onChange={(event) => setOutputSchema(event.target.value)}
                rows={12}
                spellCheck={false}
                className="w-full rounded-lg border border-gray-700 bg-gray-950 p-3 font-mono text-xs"
              />
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm text-gray-300">Mode akses</label>
              <select
                value={accessMode}
                onChange={(event) => setAccessMode(event.target.value as "all" | "selected")}
                className="w-full rounded-lg border border-gray-700 bg-gray-950 p-3 text-sm"
              >
                <option value="selected">Pengguna terpilih</option>
                <option value="all">Semua pengguna aktif</option>
              </select>
              <p className="mt-1 text-xs text-gray-500">
                Mode semua pengguna aktif mengabaikan daftar izin individual saat menentukan akses.
              </p>
            </div>
            <div>
              <label className="mb-1 block text-sm text-gray-300">Timeout (detik, 5–300)</label>
              <input
                type="number"
                min={5}
                max={300}
                value={timeoutSeconds}
                onChange={(event) => setTimeoutSeconds(Number(event.target.value))}
                className="w-full rounded-lg border border-gray-700 bg-gray-950 p-3 text-sm"
              />
            </div>
          </div>

          <div className="rounded-xl border border-amber-800/60 bg-amber-950/20 p-4 space-y-3">
            <h4 className="font-semibold">Uji Workflow</h4>
            <p className="text-xs text-amber-300">
              Pengujian menjalankan webhook n8n secara nyata. Workflow dapat
              mengirim email, mengubah data, atau melakukan tindakan eksternal.
            </p>
            <label className="block text-sm text-gray-300">
              Input JSON pengujian
            </label>
            <textarea
              value={testInput}
              onChange={(event) => setTestInput(event.target.value)}
              rows={8}
              spellCheck={false}
              className="w-full rounded-lg border border-gray-700 bg-gray-950 p-3 font-mono text-xs"
            />
            <button
              disabled={busy !== null || !selected.webhook_configured}
              onClick={() => void testWorkflow()}
              className="rounded-lg bg-amber-700 px-5 py-2.5 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
            >
              {busy === "test" ? "Menjalankan pengujian..." : "▶ Test Workflow"}
            </button>
            {!selected.webhook_configured && (
              <p className="text-xs text-gray-500">
                Konfigurasikan Webhook path sebelum pengujian.
              </p>
            )}
            {testResult && (
              <div className="space-y-2 rounded-lg border border-gray-700 bg-gray-950 p-3">
                <p className="text-sm font-semibold">
                  Status: {testResult.status || "—"}
                </p>
                <p className="text-xs text-gray-400">
                  Durasi: {testResult.duration_ms == null
                    ? "—"
                    : `${testResult.duration_ms} ms`}
                </p>
                {testResult.error_message && (
                  <pre className="whitespace-pre-wrap rounded-lg bg-red-950/40 p-3 text-xs text-red-200">
                    {testResult.error_message}
                  </pre>
                )}
                <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-gray-900 p-3 text-xs">
                  {JSON.stringify(testResult.result_data, null, 2)}
                </pre>
                {testResult.id && (
                  <p className="text-xs text-gray-500">
                    ID eksekusi: {testResult.id}
                  </p>
                )}
              </div>
            )}
          </div>

          <button
            disabled={busy !== null || timeoutSeconds < 5 || timeoutSeconds > 300}
            onClick={() => void saveConfig()}
            className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold hover:bg-blue-500 disabled:opacity-50"
          >
            {busy === "save" ? "Menyimpan..." : "Simpan konfigurasi"}
          </button>

          <div className="border-t border-gray-800 pt-4">
            <h4 className="font-semibold">Hak akses pengguna terpilih</h4>
            <p className="mt-1 text-xs text-gray-400">
              Daftar ini digunakan jika mode akses adalah Pengguna terpilih.
            </p>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <select
                value={accessUserId}
                onChange={(event) => setAccessUserId(event.target.value)}
                className="min-w-0 flex-1 rounded-lg border border-gray-700 bg-gray-950 p-3 text-sm"
              >
                <option value="">Pilih pengguna aktif...</option>
                {availableUsers.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.full_name || item.email} — {item.email}
                  </option>
                ))}
              </select>
              <button
                disabled={!accessUserId || busy !== null}
                onClick={() => void setUserAccess()}
                className="rounded-lg bg-emerald-700 px-4 py-2 text-sm disabled:opacity-40"
              >
                Berikan akses
              </button>
            </div>
            <div className="mt-3 space-y-2">
              {accessItems.length === 0 ? (
                <p className="text-sm text-gray-500">Belum ada catatan akses individual.</p>
              ) : accessItems.map((item) => (
                <div key={item.user_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-gray-800 bg-gray-950 p-3">
                  <div>
                    <p className="text-sm">{item.full_name || item.email}</p>
                    <p className="text-xs text-gray-500">{item.email}</p>
                    <p className="text-xs text-gray-500">{item.is_allowed ? "Diizinkan" : "Tidak diizinkan"}</p>
                  </div>
                  <button
                    disabled={busy !== null}
                    onClick={() => void revokeAccess(item.user_id)}
                    className="rounded-lg border border-red-800 px-3 py-1.5 text-xs text-red-300 disabled:opacity-40"
                  >
                    Cabut catatan akses
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {selectedExecution && (
        <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/80 p-3">
          <div className="my-6 max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-xl border border-gray-700 bg-gray-950 p-5">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-lg font-bold">Detail eksekusi</h3>
              <button
                onClick={() => setSelectedExecution(null)}
                className="rounded-lg border border-gray-700 px-3 py-1 text-sm"
              >
                Tutup
              </button>
            </div>
            <div className="mt-4 space-y-3 text-sm">
              <Detail label="Workflow" value={selectedExecution.workflow_name} />
              <Detail label="ID eksekusi" value={selectedExecution.id} />
              <Detail label="ID pengguna" value={selectedExecution.user_id} />
              <Detail label="Status" value={statusLabel(selectedExecution.status)} />
              <Detail label="Mulai" value={dateLabel(selectedExecution.started_at)} />
              <Detail label="Selesai" value={dateLabel(selectedExecution.finished_at)} />
              <Detail label="Durasi" value={selectedExecution.duration_ms == null ? "—" : `${selectedExecution.duration_ms} ms`} />
              {selectedExecution.error_message && (
                <div>
                  <p className="mb-1 text-gray-400">Pesan kesalahan</p>
                  <pre className="whitespace-pre-wrap rounded-lg bg-red-950/40 p-3 text-xs text-red-200">{selectedExecution.error_message}</pre>
                </div>
              )}
              <JsonPanel label="Data permintaan tersimpan" value={selectedExecution.request_data} />
              <JsonPanel label="Hasil eksekusi" value={selectedExecution.result_data} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
      <p className="text-2xl font-bold">{value}</p>
      <p className="mt-1 text-xs text-gray-400">{label}</p>
    </div>
  );
}

function SectionButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-lg px-4 py-2 text-sm ${
        active ? "bg-blue-600 text-white" : "text-gray-400 hover:bg-gray-800"
      }`}
    >
      {children}
    </button>
  );
}

function MiniButton({
  onClick,
  children,
  disabled = false,
  danger = false,
}: {
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`rounded-lg border px-3 py-2 text-xs font-medium disabled:opacity-40 ${
        danger
          ? "border-red-800 text-red-300 hover:bg-red-950/50"
          : "border-gray-700 text-gray-200 hover:bg-gray-800"
      }`}
    >
      {children}
    </button>
  );
}

function StatusPill({ active, label }: { active: boolean; label: string }) {
  return (
    <span className={`rounded-full px-2 py-1 text-xs ${
      active
        ? "bg-emerald-950 text-emerald-300"
        : "bg-gray-800 text-gray-400"
    }`}>
      {label}
    </span>
  );
}

function Notice({
  kind,
  children,
}: {
  kind: "error" | "success";
  children: React.ReactNode;
}) {
  return (
    <p className={`rounded-lg px-3 py-2 text-sm ${
      kind === "error"
        ? "bg-red-950/50 text-red-300"
        : "bg-emerald-950/50 text-emerald-300"
    }`}>
      {children}
    </p>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-8 text-center text-sm text-gray-400">
      {children}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-gray-500">{label}</p>
      <p className="break-all">{value}</p>
    </div>
  );
}

function JsonPanel({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <p className="mb-1 text-gray-400">{label}</p>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-gray-900 p-3 text-xs text-gray-200">
        {pretty(value)}
      </pre>
    </div>
  );
}
