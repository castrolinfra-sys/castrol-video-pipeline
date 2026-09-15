import "./globals.css";
import { Barlow, Barlow_Semi_Condensed, IBM_Plex_Mono } from "next/font/google";
import { Nav } from "./nav";
import { RefreshButton } from "./refresh-button";
import { signOut } from "./login/actions";
import { currentAdmin } from "@/lib/session";

// Type, chosen for the subject rather than defaulted to.
//
// Barlow is drawn from Californian industrial and transport signage — highway
// gantries, workshop lettering. That is the vernacular this product lives in:
// mechanics, service bays, a Castrol forecourt. It is also a low-contrast
// grotesque, which is what holds up at 14px across a 500-row table where a
// fashionable high-contrast face would fall apart.
//
// Semi Condensed carries every uppercase micro-label — nav, column heads, stat
// labels. Condensed caps at small sizes IS signage, and using one family in two
// widths keeps that voice consistent without a second typeface's personality
// arguing with the first.
//
// Plex Mono only where characters must be told apart by eye: a WhatsApp number
// being matched against a client's spreadsheet needs 0 and O to differ.
const body = Barlow({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-body",
  display: "swap",
});

const display = Barlow_Semi_Condensed({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-display-src",
  display: "swap",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono-src",
  display: "swap",
});

export const metadata = { title: "Castrol pipeline — admin" };

// Matches --bg, so the mobile browser chrome continues the page rather than
// framing it in a different colour.
export const viewport = { themeColor: "#fcfcfb" };

// Nothing here is cacheable. Every page answers "what is true right now".
export const dynamic = "force-dynamic";

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const admin = await currentAdmin();
  return (
    <html lang="en" className={`${body.variable} ${display.variable} ${mono.variable}`}>
      <body>
        <a className="skip" href="#main">
          Skip to content
        </a>
        {admin && (
          <header className="top">
            {/* translate="no" — a brand mark run through page translation comes
                back as a word, not a name. */}
            <div className="brand" translate="no">
              <span className="brand-mark" aria-hidden="true" />
              Castrol<b>pipeline</b>
            </div>
            <Nav />
            <div className="who">
              <RefreshButton />
              <span className="who-email">{admin}</span>
              {/* A password session outlives the browser tab, so there has to
                  be a way to end it deliberately - on a shared machine, and
                  for an account the allowlist has stopped accepting. */}
              <form action={signOut}>
                <button>Sign Out</button>
              </form>
            </div>
          </header>
        )}
        <main id="main">{children}</main>
      </body>
    </html>
  );
}
