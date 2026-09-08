"use client";

import { createBrowserClient } from "@supabase/ssr";
import { useState } from "react";

export default function Login() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [message, setMessage] = useState("");

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setState("sending");
    const supabase = createBrowserClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    );
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${location.origin}/auth/callback` },
    });
    if (error) {
      setState("error");
      setMessage(error.message);
      return;
    }
    setState("sent");
  }

  return (
    <div className="center card">
      <h1>Pipeline admin</h1>
      {state === "sent" ? (
        <p className="dim">
          Check {email} for a sign-in link. It expires shortly.
        </p>
      ) : (
        <form onSubmit={send}>
          <p className="dim" style={{ marginTop: 0 }}>
            Sign in with a magic link. Only allowlisted addresses can get in.
          </p>
          <input
            type="email"
            required
            value={email}
            placeholder="you@example.com"
            onChange={(e) => setEmail(e.target.value)}
          />
          <button style={{ marginTop: 12 }} disabled={state === "sending"}>
            {state === "sending" ? "Sending…" : "Send link"}
          </button>
          {state === "error" && (
            <p style={{ color: "var(--bad)" }}>{message}</p>
          )}
        </form>
      )}
    </div>
  );
}
