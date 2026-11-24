"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import Link from "next/link";

export default function FarmerRegisterPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleRegister(event: FormEvent) {
    event.preventDefault();
    if (loading) return;

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    if (password.length < 6) {
      setError("Password must be at least 6 characters long.");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const response = await fetch("/api/farmer/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          name: name.trim(),
          email: email.trim(),
          password,
        }),
      });

      if (!response.ok) {
        let message = "Unable to create your account.";
        try {
          const data = await response.json();
          if (typeof data?.detail === "string" && data.detail) {
            message = data.detail;
          } else if (typeof data?.error === "string" && data.error) {
            message = data.error;
          }
        } catch {
          // Use fallback message
        }
        throw new Error(message);
      }

      window.location.replace("/farmer/login");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="relative flex min-h-dvh flex-col items-center justify-center overflow-hidden bg-[#f6faf6] px-4 py-10 text-slate-800">
      {/* Top-left decorative leaf */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/images/leaf-top.png"
        alt=""
        className="pointer-events-none absolute left-0 top-0 hidden h-40 w-auto sm:block"
      />

      {/* Bottom landscape backdrop matching the main dashboard */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/images/landscape.jpg"
        alt=""
        className="pointer-events-none absolute inset-x-0 bottom-0 z-0 h-[160px] w-full object-cover object-bottom sm:h-[220px]"
        style={{
          maskImage: "linear-gradient(to bottom, transparent 0%, black 35%)",
          WebkitMaskImage: "linear-gradient(to bottom, transparent 0%, black 35%)",
        }}
      />

      {/* Register Card */}
      <div className="relative z-10 w-full max-w-[420px] rounded-[28px] border border-[#e2e8df] bg-white/95 p-7 shadow-[0_12px_40px_-12px_rgba(22,101,52,0.2)] backdrop-blur-sm sm:p-8">
        {/* Brand Badge */}
        <div className="flex flex-col items-center text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[#1e4d35] text-white shadow-sm">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-6 w-6"
              aria-hidden="true"
            >
              <path d="M7 20h10" />
              <path d="M10 20c5.5-2.5.8-6.4 3-10" />
              <path d="M9.5 9.4c1.1.8 1.8 2.2 2.3 3.7-2 .4-3.5.4-4.8-.3-1.2-.6-2.3-1.9-3-4.2 2.8-.5 4.4 0 5.5.8z" />
              <path d="M14.1 6a7 7 0 0 0-1.1 4c1.9-.1 3.3-.6 4.3-1.4 1-1 1.6-2.3 1.7-4.6-2.7.1-4 1-4.9 2z" />
            </svg>
          </div>

          <h1 className="mt-4 text-2xl font-extrabold tracking-tight text-[#1e4d35]">
            Create Farmer Account
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Register to start using your Smart Farming Assistant
          </p>
        </div>

        {/* Error Alert */}
        {error && (
          <p
            role="alert"
            className="mt-5 rounded-xl border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700"
          >
            {error}
          </p>
        )}

        {/* Form */}
        <form onSubmit={handleRegister} className="mt-6 space-y-4">
          <div>
            <label
              htmlFor="name"
              className="block text-sm font-medium text-slate-700"
            >
              Full name
            </label>
            <input
              id="name"
              type="text"
              autoComplete="name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Ramesh Kumar"
              className="mt-1.5 w-full rounded-xl border border-slate-300 bg-[#f8fbf8] px-3.5 py-2.5 text-[15px] text-slate-900 outline-none transition focus:border-green-600 focus:bg-white focus:ring-4 focus:ring-green-100"
            />
          </div>

          <div>
            <label
              htmlFor="email"
              className="block text-sm font-medium text-slate-700"
            >
              Email address
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="farmer@example.com"
              className="mt-1.5 w-full rounded-xl border border-slate-300 bg-[#f8fbf8] px-3.5 py-2.5 text-[15px] text-slate-900 outline-none transition focus:border-green-600 focus:bg-white focus:ring-4 focus:ring-green-100"
            />
          </div>

          <div>
            <div className="flex items-center justify-between">
              <label
                htmlFor="password"
                className="block text-sm font-medium text-slate-700"
              >
                Password
              </label>
              <button
                type="button"
                onClick={() => setShowPassword((prev) => !prev)}
                className="text-xs font-medium text-green-700 hover:underline"
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>
            <input
              id="password"
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Create a password"
              className="mt-1.5 w-full rounded-xl border border-slate-300 bg-[#f8fbf8] px-3.5 py-2.5 text-[15px] text-slate-900 outline-none transition focus:border-green-600 focus:bg-white focus:ring-4 focus:ring-green-100"
            />
          </div>

          <div>
            <label
              htmlFor="confirm-password"
              className="block text-sm font-medium text-slate-700"
            >
              Confirm password
            </label>
            <input
              id="confirm-password"
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              required
              minLength={6}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Re-enter your password"
              className="mt-1.5 w-full rounded-xl border border-slate-300 bg-[#f8fbf8] px-3.5 py-2.5 text-[15px] text-slate-900 outline-none transition focus:border-green-600 focus:bg-white focus:ring-4 focus:ring-green-100"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="mt-2 w-full rounded-xl bg-green-700 py-3 text-[15px] font-semibold text-white shadow-sm transition hover:bg-green-800 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? "Creating account..." : "Register"}
          </button>
        </form>

        {/* Footer Links */}
        <p className="mt-6 text-center text-sm text-slate-600">
          Already have an account?{" "}
          <Link
            href="/farmer/login"
            className="font-semibold text-green-700 hover:underline"
          >
            Login here
          </Link>
        </p>

        <p className="mt-3 text-center text-xs text-slate-400">
          English · हिन्दी · ಕನ್ನಡ
        </p>
      </div>
    </main>
  );
}