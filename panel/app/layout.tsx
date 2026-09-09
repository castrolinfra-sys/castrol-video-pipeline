import "./globals.css";
import { signOut } from "./login/actions";
import { currentAdmin } from "@/lib/session";

export const metadata = { title: "Castrol pipeline — admin" };

// Nothing here is cacheable. Every page answers "what is true right now".
export const dynamic = "force-dynamic";

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const admin = await currentAdmin();
  return (
    <html lang="en">
      <body>
        {admin && (
          <header className="top">
            <div className="brand">
              CASTROL<span>.</span>pipeline
            </div>
            <nav>
              <a href="/">Jobs</a>
              <a href="/failures">Failures</a>
              <a href="/submissions">Client data</a>
              <a href="/costs">Costs</a>
            </nav>
            <div className="who">
              {admin}
              {/* A password session outlives the browser tab, so there has to
                  be a way to end it deliberately - on a shared machine, and
                  for an account the allowlist has stopped accepting. */}
              <form action={signOut}>
                <button>Sign out</button>
              </form>
            </div>
          </header>
        )}
        <main>{children}</main>
      </body>
    </html>
  );
}
