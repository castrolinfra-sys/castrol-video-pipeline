import "./globals.css";
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
            <div className="who">{admin}</div>
          </header>
        )}
        <main>{children}</main>
      </body>
    </html>
  );
}
