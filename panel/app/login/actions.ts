"use server";

// Sign in and out. The password is handled here, on the server, and never
// becomes client component state.
//
// Note what this file does NOT do: it does not check the allowlist. Signing in
// proves who you are; `ADMIN_ALLOWED_EMAILS` decides whether that person gets
// in, and the middleware is the single place that decides it. Putting a second
// check here would mean two places to keep in step, and the one that silently
// drifted would be the one nobody tested.

import { redirect } from "next/navigation";
import { sessionClient } from "@/lib/session";

export async function signIn(formData: FormData) {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const next = String(formData.get("next") || "/");

  const supabase = await sessionClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });

  // One message for every failure. Distinguishing "no such user" from "wrong
  // password" tells an attacker which addresses are real, and this app's whole
  // access model is an allowlist of addresses.
  if (error) redirect("/login?error=1");

  // Not `next` unchecked: an open redirect is a phishing primitive, and this
  // value arrives from the querystring the middleware built. Same-origin paths
  // only.
  redirect(next.startsWith("/") && !next.startsWith("//") ? next : "/");
}

export async function signOut() {
  const supabase = await sessionClient();
  await supabase.auth.signOut();
  redirect("/login");
}
