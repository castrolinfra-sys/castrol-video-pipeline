"use client";

// Password input with a reveal toggle.
//
// The rest of this page is deliberately server-rendered with no client
// JavaScript, and that still holds for the password itself: this component
// flips the input's `type`, it never reads or stores the value. The form still
// posts straight to the server action, so nothing here can see what was typed.

import { useState } from "react";

export function PasswordField() {
  const [show, setShow] = useState(false);

  return (
    <div>
      <label htmlFor="password">Password</label>
      <div className="with-affix">
        <input
          id="password"
          type={show ? "text" : "password"}
          name="password"
          required
          autoComplete="current-password"
          // A revealed password must not be spell-checked or auto-capitalised.
          spellCheck={false}
          autoCapitalize="none"
          autoCorrect="off"
          placeholder="Your password"
        />
        <button
          type="button"
          className="affix"
          onClick={() => setShow((s) => !s)}
          aria-label={show ? "Hide password" : "Show password"}
          aria-pressed={show}
          // Not in the tab order: it sits between the password field and the
          // submit button, and a keyboard user wants Tab to reach Sign In.
          tabIndex={-1}
        >
          {show ? <EyeOff /> : <Eye />}
        </button>
      </div>
    </div>
  );
}

// Decorative — the button carries the name via aria-label.
function Eye() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function EyeOff() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M2 12s3.6-7 10-7c1.7 0 3.2.5 4.5 1.2M22 12s-3.6 7-10 7c-1.7 0-3.2-.5-4.5-1.2" />
      <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
      <path d="m3 3 18 18" />
    </svg>
  );
}
