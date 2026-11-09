"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/app/auth-context";

const PROFESSION_OPTIONS = [
  "Guru", "Dosen", "Mahasiswa", "Pelajar", "Peneliti",
  "Instruktur", "Kepala Sekolah", "Tenaga Kependidikan",
  "Software Developer", "Web Developer", "Mobile Developer",
  "AI Engineer", "AI Specialist", "Data Analyst", "Data Scientist",
  "Machine Learning Engineer", "Cybersecurity Specialist",
  "IT Support", "System Administrator", "UI/UX Designer",
  "Digital Transformation Specialist", "Cloud Engineer",
  "Pengusaha", "Pemilik UMKM", "Manajer", "Direktur",
  "Konsultan Bisnis", "Business Analyst", "Project Manager",
  "Staf Administrasi", "Sekretaris", "Akuntan", "Auditor",
  "Analis Keuangan", "HRD", "Marketing Specialist",
  "Digital Marketer", "Sales Executive", "Dokter", "Dokter Gigi",
  "Perawat", "Bidan", "Apoteker", "Tenaga Kesehatan",
  "Psikolog", "Content Creator", "Penulis", "Editor", "Jurnalis",
  "Desainer Grafis", "Fotografer", "Videografer", "Arsitek",
  "Insinyur", "Teknisi", "Kontraktor", "Quality Control",
  "Operator Produksi", "ASN", "TNI", "Polri", "Advokat",
  "Notaris", "Pekerja Sosial", "Petani", "Peternak", "Nelayan",
  "Ahli Lingkungan", "Praktisi Energi Terbarukan", "Freelancer",
  "Ibu Rumah Tangga", "Pensiunan", "Belum Bekerja", "Lainnya",
];

export default function LoginPage() {
  const { login, register } = useAuth();
  const router = useRouter();

  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [profession, setProfession] = useState("");
  const [organization, setOrganization] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
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

      if (err) {
        setError(err);
        return;
      }

      router.replace("/");
    } catch {
      setError("Tidak dapat terhubung ke server.");
    } finally {
      setBusy(false);
    }
  }

  function showForgotPasswordNotice() {
    setNotice(
      "Untuk mengatur ulang password, silakan hubungi administrator ONE MASTER AI."
    );
  }

  return (
    <main className="flex min-h-[100dvh] items-center justify-center bg-black px-4 py-4 text-white">
      <section className="w-full max-w-md rounded-2xl border border-neutral-800 bg-transparent px-5 pt-0 pb-5 shadow-2xl sm:px-8 sm:pt-0 sm:pb-5">
        <div className="relative top-5 mb-7 aspect-[16/9] overflow-hidden">
          <img
            src="/qris/AIku.jpg"
            alt="Logo ONE MASTER AI"
            className="h-full w-full object-contain"
          />
        </div>

        {mode === "register" && (
          <div className="mb-5">
            <p className="mb-4 text-center text-sm text-neutral-400">
              Buat akun ONE MASTER AI
            </p>
          </div>
        )}

        <form onSubmit={submit} className="space-y-4">
          {mode === "register" && (
            <>
              <Field
                label="Nama Lengkap"
                value={fullName}
                onChange={setFullName}
                placeholder="Nama lengkap Anda"
                required
              />

              <label className="block">
                <span className="mb-2 block text-sm text-neutral-300">
                  Profesi
                </span>
                <input
                  type="text"
                  list="profession-options"
                  value={profession}
                  onChange={(e) => setProfession(e.target.value)}
                  placeholder="Pilih atau ketik profesi"
                  autoComplete="off"
                  className="w-full rounded-lg border border-neutral-700 bg-transparent px-4 py-3 text-base text-white outline-none transition placeholder:text-neutral-600 sm:text-sm focus:border-neutral-400"
                />
                <datalist id="profession-options">
                  {PROFESSION_OPTIONS.map((item) => (
                    <option key={item} value={item} />
                  ))}
                </datalist>
              </label>

              <Field
                label="Organisasi"
                value={organization}
                onChange={setOrganization}
                placeholder="Nama organisasi (opsional)"
              />
            </>
          )}

          <Field
            label="Email"
            type="email"
            value={email}
            onChange={setEmail}
            placeholder="nama@contoh.com"
            required
          />

          <label className="block">
            <span className="mb-2 block text-sm text-neutral-300">
              Password
            </span>

            <div className="relative">
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={
                  mode === "register"
                    ? "Minimal 8 karakter"
                    : "Masukkan password"
                }
                required
                className="w-full rounded-lg border border-neutral-700 bg-transparent px-4 py-3 pr-12 text-base text-white outline-none transition placeholder:text-neutral-600 sm:text-sm focus:border-neutral-400"
              />

              <button
                type="button"
                onClick={() => setShowPassword((value) => !value)}
                aria-label={showPassword ? "Sembunyikan password" : "Tampilkan password"}
                className="absolute inset-y-0 right-0 flex items-center px-4 text-neutral-400 transition hover:text-white"
              >
                {showPassword ? (
                  <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.7">
                    <path d="M3 3l18 18M10.6 10.6a2 2 0 002.8 2.8" />
                    <path d="M9.9 5.2A10.8 10.8 0 0112 5c5 0 8.5 4.5 9 7-.2 1-1 2.3-2.2 3.5M6.2 6.2C3.8 7.8 2.2 10.2 2 12c.5 2.5 4 7 10 7 1.1 0 2.1-.2 3-.5" />
                  </svg>
                ) : (
                  <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.7">
                    <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
                    <circle cx="12" cy="12" r="3" />
                  </svg>
                )}
              </button>
            </div>
          </label>

          {mode === "login" && (
            <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
              <button
                type="button"
                onClick={() => {
                  setMode("register");
                  setError(null);
                  setNotice(null);
                }}
                className="min-h-11 rounded-lg border border-neutral-600 px-4 py-2.5 text-sm font-medium text-neutral-200 transition hover:border-white hover:text-white"
              >
                Sign Up
              </button>

              <button
                type="button"
                onClick={showForgotPasswordNotice}
                className="min-h-11 px-2 py-2.5 text-sm text-neutral-400 transition hover:text-white"
              >
                Lupa password?
              </button>
            </div>
          )}

          {error && (
            <p role="alert" className="rounded-lg border border-red-900 bg-red-950/50 px-3 py-2 text-sm text-red-300">
              {error}
            </p>
          )}

          {notice && (
            <p role="status" className="rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-neutral-300">
              {notice}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="min-h-12 w-full rounded-lg bg-white py-3 font-semibold text-black transition hover:bg-neutral-200 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy
              ? "Memproses..."
              : mode === "login"
                ? "Login"
                : "Buat Akun"}
          </button>

          {mode === "register" && (
            <button
              type="button"
              onClick={() => {
                setMode("login");
                setError(null);
                setNotice(null);
              }}
              className="w-full py-2 min-h-11 px-2 py-2.5 text-sm text-neutral-400 transition hover:text-white"
            >
              Kembali ke Login
            </button>
          )}
        </form>

        <p className="mt-7 text-center text-xs leading-relaxed text-neutral-500">
          Satu asisten AI untuk berbagai kebutuhan Anda.
        </p>
      </section>

      <p className="absolute bottom-3 left-0 w-full px-3 text-center text-[11px] leading-relaxed text-neutral-500">
        Developed by: GMHTech | Fahrizal Halim | 2026
      </p>
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
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-2 block text-sm text-neutral-300">{label}</span>
      <input
        type={type}
        value={value}
        required={required}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-neutral-700 bg-transparent px-4 py-3 text-base text-white outline-none transition placeholder:text-neutral-600 sm:text-sm focus:border-neutral-400"
      />
    </label>
  );
}
