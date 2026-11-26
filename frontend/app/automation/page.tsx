"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/app/auth-context";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

type SchemaProperty = {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  properties?: Record<string, SchemaProperty>;
  items?: SchemaProperty;
};

type Workflow = {
  id: string;
  name: string;
  description: string;
  input_schema: {
    type?: string;
    properties?: Record<string, SchemaProperty>;
    required?: string[];
  };
  timeout_seconds: number;
};

type Execution = {
  id: string;
  workflow_id: string | null;
  workflow_name: string;
  status: string;
  request_data: Record<string, unknown>;
  result_data: Record<string, unknown> | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  created_at: string | null;
};

function dateText(value: string | null) {
  if (!value) return "-";
  return new Date(value).toLocaleString("id-ID", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Jayapura",
  });
}

function statusText(status: string) {
  const labels: Record<string, string> = {
    completed: "Berhasil",
    failed: "Gagal",
    running: "Sedang berjalan",
    queued: "Dalam antrean",
    unknown: "Status belum diketahui",
  };
  return labels[status] || status;
}

function statusClass(status: string) {
  if (status === "completed") return "text-emerald-400";
  if (status === "failed") return "text-red-400";
  if (status === "unknown") return "text-amber-400";
  return "text-blue-300";
}

function initialValue(property: SchemaProperty): unknown {
  if (property.default !== undefined) return property.default;
  if (property.enum?.length) return property.enum[0];
  if (property.type === "boolean") return false;
  if (property.type === "number" || property.type === "integer") return "";
  if (property.type === "array") return [];
  if (property.type === "object") return {};
  return "";
}

function makeInitialInput(workflow: Workflow): Record<string, unknown> {
  const properties = workflow.input_schema?.properties || {};
  return Object.fromEntries(
    Object.entries(properties).map(([key, property]) => [
      key,
      initialValue(property),
    ])
  );
}

export default function AutomationPage() {
  const { token, user, loading } = useAuth();
  const router = useRouter();

  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [inputData, setInputData] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [pageLoading, setPageLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [lastExecution, setLastExecution] = useState<Execution | null>(null);

  const headers = useCallback(
    () => ({
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    }),
    [token]
  );

  const loadData = useCallback(async () => {
    if (!token) return;

    setError("");

    try {
      const [workflowRes, executionRes] = await Promise.all([
        fetch(`${API_URL}/api/v1/automation/workflows`, {
          headers: headers(),
          cache: "no-store",
        }),
        fetch(`${API_URL}/api/v1/automation/executions?limit=30`, {
          headers: headers(),
          cache: "no-store",
        }),
      ]);

      if (!workflowRes.ok || !executionRes.ok) {
        throw new Error("Gagal memuat data Automation.");
      }

      const workflowData = await workflowRes.json();
      const executionData = await executionRes.json();

      const items: Workflow[] = workflowData.items || [];
      setWorkflows(items);
      setExecutions(executionData.items || []);

      setSelectedId((current) => {
        if (items.some((item) => item.id === current)) return current;
        return items[0]?.id || "";
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat data.");
    } finally {
      setPageLoading(false);
    }
  }, [token, headers]);

  useEffect(() => {
    if (!loading && !token) router.replace("/login");
  }, [loading, token, router]);

  useEffect(() => {
    if (token) loadData();
  }, [token, loadData]);

  const selectedWorkflow = workflows.find(
    (item) => item.id === selectedId
  );

  useEffect(() => {
    if (selectedWorkflow) {
      setInputData(makeInitialInput(selectedWorkflow));
      setLastExecution(null);
      setSuccess("");
    }
  }, [selectedId, selectedWorkflow]);

  function updateInput(key: string, value: unknown) {
    setInputData((current) => ({ ...current, [key]: value }));
  }

  function parseInput(property: SchemaProperty, value: unknown) {
    if (property.type === "number" || property.type === "integer") {
      if (value === "" || value === null || value === undefined) return value;
      const number = Number(value);
      if (!Number.isFinite(number)) throw new Error("Input angka tidak valid.");
      if (property.type === "integer" && !Number.isInteger(number)) {
        throw new Error("Input harus berupa bilangan bulat.");
      }
      return number;
    }

    if (property.type === "boolean") return Boolean(value);

    if (property.type === "array" || property.type === "object") {
      if (typeof value === "string") {
        try {
          return JSON.parse(value);
        } catch {
          throw new Error("Format JSON pada input tidak valid.");
        }
      }
    }

    return value;
  }

  async function executeWorkflow(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedWorkflow || busy) return;

    setError("");
    setSuccess("");
    setLastExecution(null);

    const properties = selectedWorkflow.input_schema?.properties || {};
    const required = selectedWorkflow.input_schema?.required || [];

    try {
      const payload: Record<string, unknown> = {};

      for (const [key, property] of Object.entries(properties)) {
        const value = parseInput(property, inputData[key]);

        if (
          required.includes(key) &&
          (value === "" || value === null || value === undefined)
        ) {
          throw new Error(`Kolom "${property.title || key}" wajib diisi.`);
        }

        if (value !== "" && value !== undefined) {
          payload[key] = value;
        }
      }

      setBusy(true);

      const response = await fetch(
        `${API_URL}/api/v1/automation/workflows/${selectedWorkflow.id}/execute`,
        {
          method: "POST",
          headers: headers(),
          body: JSON.stringify({ input_data: payload }),
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : "Eksekusi workflow gagal."
        );
      }

      const execution: Execution = data.execution;
      setLastExecution(execution);

      if (execution.status === "completed") {
        setSuccess(data.message || "Workflow berhasil dijalankan.");
      } else {
        setError(data.message || "Eksekusi belum berhasil.");
      }

      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Eksekusi gagal.");
    } finally {
      setBusy(false);
    }
  }

  function renderField(key: string, property: SchemaProperty) {
    const value = inputData[key] ?? "";
    const required = selectedWorkflow?.input_schema?.required?.includes(key);
    const label = property.title || key;

    return (
      <div key={key} className="space-y-2">
        <label className="block text-sm font-medium text-gray-200">
          {label}
          {required && <span className="ml-1 text-red-400">*</span>}
        </label>

        {property.description && (
          <p className="text-xs text-gray-500">{property.description}</p>
        )}

        {property.enum ? (
          <select
            value={String(value)}
            onChange={(event) => updateInput(key, event.target.value)}
            required={required}
            className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm"
          >
            {property.enum.map((option, index) => (
              <option key={index} value={String(option)}>
                {String(option)}
              </option>
            ))}
          </select>
        ) : property.type === "boolean" ? (
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input
              type="checkbox"
              checked={Boolean(value)}
              onChange={(event) => updateInput(key, event.target.checked)}
              className="h-4 w-4"
            />
            Aktif
          </label>
        ) : property.type === "object" || property.type === "array" ? (
          <textarea
            value={
              typeof value === "string"
                ? value
                : JSON.stringify(value, null, 2)
            }
            onChange={(event) => updateInput(key, event.target.value)}
            rows={5}
            required={required}
            placeholder={property.type === "array" ? "[]" : "{}"}
            className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 font-mono text-sm"
          />
        ) : (
          <input
            type={
              property.type === "number" || property.type === "integer"
                ? "number"
                : "text"
            }
            step={property.type === "integer" ? "1" : "any"}
            value={String(value)}
            onChange={(event) => updateInput(key, event.target.value)}
            required={required}
            className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm"
          />
        )}
      </div>
    );
  }

  if (loading || !token || !user || pageLoading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-950 text-gray-400">
        Memuat Automation…
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-950 px-4 py-8 text-gray-100">
      <div className="mx-auto max-w-5xl">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold">⚙️ Automation</h1>
            <p className="mt-1 text-sm text-gray-400">
              Jalankan workflow otomatis yang telah diberikan akses oleh Admin.
            </p>
          </div>
          <Link
            href="/"
            className="rounded-lg border border-gray-700 px-4 py-2 text-sm hover:bg-gray-800"
          >
            ← Kembali ke Chat
          </Link>
        </header>

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

        <section className="mb-8 rounded-xl border border-gray-800 bg-gray-900/50 p-5">
          <h2 className="mb-4 text-lg font-semibold">Daftar Workflow</h2>

          {workflows.length === 0 ? (
            <div className="rounded-lg border border-dashed border-gray-700 p-6 text-center">
              <p className="text-gray-300">Belum ada workflow yang tersedia.</p>
              <p className="mt-2 text-sm text-gray-500">
                Workflow harus dipublikasikan, diaktifkan, dan diberikan hak
                akses kepada akun Anda oleh Admin.
              </p>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {workflows.map((workflow) => (
                <button
                  key={workflow.id}
                  type="button"
                  onClick={() => setSelectedId(workflow.id)}
                  className={`rounded-xl border p-4 text-left transition ${
                    selectedId === workflow.id
                      ? "border-blue-500 bg-blue-950/40"
                      : "border-gray-700 bg-gray-900 hover:border-gray-500"
                  }`}
                >
                  <p className="font-semibold">{workflow.name}</p>
                  <p className="mt-2 text-sm text-gray-400">
                    {workflow.description || "Tidak ada deskripsi."}
                  </p>
                  <p className="mt-3 text-xs text-gray-500">
                    Batas waktu: {workflow.timeout_seconds} detik
                  </p>
                </button>
              ))}
            </div>
          )}
        </section>

        {selectedWorkflow && (
          <section className="mb-8 rounded-xl border border-gray-800 bg-gray-900/50 p-5">
            <h2 className="mb-2 text-lg font-semibold">
              Jalankan: {selectedWorkflow.name}
            </h2>
            <p className="mb-5 text-sm text-gray-400">
              Isi parameter workflow di bawah ini.
            </p>

            <form onSubmit={executeWorkflow} className="space-y-5">
              {Object.entries(
                selectedWorkflow.input_schema?.properties || {}
              ).map(([key, property]) => renderField(key, property))}

              {Object.keys(
                selectedWorkflow.input_schema?.properties || {}
              ).length === 0 && (
                <p className="text-sm text-gray-500">
                  Workflow ini tidak memerlukan parameter input.
                </p>
              )}

              <button
                type="submit"
                disabled={busy}
                className="w-full rounded-lg bg-blue-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy ? "Menjalankan workflow…" : "▶ Jalankan Workflow"}
              </button>
            </form>
          </section>
        )}

        {lastExecution && (
          <section className="mb-8 rounded-xl border border-gray-800 bg-gray-900/50 p-5">
            <h2 className="mb-3 text-lg font-semibold">Hasil Eksekusi</h2>
            <p className={`mb-3 text-sm font-semibold ${statusClass(lastExecution.status)}`}>
              {statusText(lastExecution.status)}
            </p>

            {lastExecution.error_message && (
              <p className="mb-3 text-sm text-red-300">
                {lastExecution.error_message}
              </p>
            )}

            {lastExecution.result_data && (
              <pre className="overflow-x-auto rounded-lg border border-gray-800 bg-black p-4 text-xs text-gray-300">
                {JSON.stringify(lastExecution.result_data, null, 2)}
              </pre>
            )}

            <p className="mt-3 text-xs text-gray-500">
              ID eksekusi: {lastExecution.id}
            </p>
          </section>
        )}

        <section className="rounded-xl border border-gray-800 bg-gray-900/50 p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">Riwayat Eksekusi</h2>
            <button
              type="button"
              onClick={loadData}
              className="rounded-lg border border-gray-700 px-3 py-2 text-xs hover:bg-gray-800"
            >
              ↻ Muat ulang
            </button>
          </div>

          {executions.length === 0 ? (
            <p className="py-5 text-center text-sm text-gray-500">
              Belum ada riwayat eksekusi.
            </p>
          ) : (
            <div className="space-y-3">
              {executions.map((item) => (
                <div
                  key={item.id}
                  className="rounded-lg border border-gray-800 p-4"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="font-medium">{item.workflow_name}</p>
                    <span className={`text-xs font-semibold ${statusClass(item.status)}`}>
                      {statusText(item.status)}
                    </span>
                  </div>
                  <p className="mt-2 text-xs text-gray-500">
                    {dateText(item.created_at)}
                    {item.duration_ms !== null &&
                      ` · ${item.duration_ms.toLocaleString("id-ID")} ms`}
                  </p>
                  {item.error_message && (
                    <p className="mt-2 text-xs text-red-300">
                      {item.error_message}
                    </p>
                  )}
                  {item.result_data && (
                    <details className="mt-3">
                      <summary className="cursor-pointer text-xs text-blue-300">
                        Lihat hasil
                      </summary>
                      <pre className="mt-2 overflow-x-auto rounded-lg bg-black p-3 text-xs text-gray-300">
                        {JSON.stringify(item.result_data, null, 2)}
                      </pre>
                    </details>
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
