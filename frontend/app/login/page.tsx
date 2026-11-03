"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/app/auth-context";

export default function LoginPage() {
  const { login, register } = useAuth();
  const router = useRouter();

  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [profession, setProfession] = useState("");
  const [organization, setOrganization] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const err =
        mode === "login"
          ? await login(email, password)
          : await register({
              email,
              password,
              full_name: fullName,
              profession: profession || "umum",
              organization,
            });
      if (err) setError(err);
      else router.replace("/");
    } catch {
      setError("Tidak dapat terhubung ke server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-gray-950 px-4 text-gray-100">
      <div className="w-full max-w-md rounded-2xl border border-gray-800 bg-gray-900 p-8 shadow-xl">
        <h1 className="text-center text-2xl font-bold">
          🤖 ONE MASTER AI
        </h1>
        <p className="mt-1 text-center text-sm text-gray-400">
          {mode === "login" ? "Masuk ke akun Anda" : "Buat akun baru"}
        </p>

        {/* Toggle mode */}
        <div className="mt-6 grid grid-cols-2 gap-2 rounded-xl bg-gray-800 p-1 text-sm">
          <button
            onClick={() => setMode("login")}
            className={`rounded-lg py-2 font-medium transition ${
              mode === "login" ? "bg-blue-600" : "text-gray-400"
            }`}
          >
            Masuk
          </button>
          <button
            onClick={() => setMode("register")}
            className={`rounded-lg py-2 font-medium transition ${
              mode === "register" ? "bg-blue-600" : "text-gray-400"
            }`}
          >
            Daftar
          </button>
        </div>

        <form onSubmit={submit} className="mt-6 space-y-4">
          {mode === "register" && (
            <>
              <Field
                label="Nama Lengkap"
                value={fullName}
                onChange={setFullName}
                placeholder="Fahrizal Halim"
              />
              <Field
                label="Profesi"
                value={profession}
                onChange={setProfession}
                placeholder="Dosen Teknik Informatika"
              />
              <Field
                label="Organisasi"
                value={organization}
                onChange={setOrganization}
                placeholder="UMMU"
              />
            </>
          )}

          <Field
            label="Email"
            type="email"
            value={email}
            onChange={setEmail}
            placeholder="nama@contoh.ac.id"
            required
          />
          <Field
            label="Password"
            type="password"
            value={password}
            onChange={setPassword}
            placeholder={mode === "register" ? "minimal 8 karakter" : "password Anda"}
            required
          />

          {error && (
            <p className="rounded-lg bg-red-900/40 px-3 py-2 text-sm text-red-300">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-xl bg-blue-600 py-3 font-semibold hover:bg-blue-500 disabled:opacity-40"
          >
            {busy
              ? "Memproses…"
              : mode === "login"
                ? "Masuk"
                : "Daftar Sekarang"}
          </button>
        </form>
      </div>
    </main>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  required = false,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm text-gray-400">{label}</span>
      <input
        type={type}
        value={value}
        required={required}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-xl border border-gray-700 bg-gray-950 px-4 py-2.5 outline-none focus:border-blue-500"
      />
    </label>
  );
}
