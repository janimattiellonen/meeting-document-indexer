import { isRouteErrorResponse, Links, Meta, NavLink, Outlet, Scripts, ScrollRestoration } from "react-router";

import type { Route } from "./+types/root";
import "./app.css";

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fi">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <Meta />
        <Links />
      </head>
      <body className="min-h-screen bg-stone-50 text-stone-900 antialiased dark:bg-stone-950 dark:text-stone-100">
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

export function meta() {
  return [{ title: "Pöytäkirjat" }];
}

function Header() {
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-3 py-1.5 text-sm font-medium ${
      isActive
        ? "bg-stone-900 text-white dark:bg-stone-100 dark:text-stone-900"
        : "text-stone-600 hover:bg-stone-200 dark:text-stone-300 dark:hover:bg-stone-800"
    }`;
  return (
    <header className="border-b border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900">
      <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
        <span className="font-semibold tracking-tight">Pöytäkirjat</span>
        <nav className="flex gap-1">
          <NavLink to="/" end className={link}>
            Haku
          </NavLink>
          <NavLink to="/kokoukset" className={link}>
            Kokoukset
          </NavLink>
        </nav>
      </div>
    </header>
  );
}

export default function App() {
  return (
    <>
      <Header />
      <main className="mx-auto max-w-6xl px-4 py-8">
        <Outlet />
      </main>
    </>
  );
}

export function HydrateFallback() {
  return <p className="p-8 text-stone-500">Ladataan…</p>;
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  let message = "Jokin meni pieleen";
  let details = "Odottamaton virhe.";
  if (isRouteErrorResponse(error)) {
    message = error.status === 404 ? "Ei löytynyt" : `Virhe ${error.status}`;
    details =
      error.status === 404
        ? "Sivua tai kokousta ei löytynyt."
        : error.status < 500
          ? "Palvelin ei hyväksynyt pyyntöä. Tarkista hakusanat ja rajaukset."
          : "Palvelin ei vastannut. Onko taustapalvelu käynnissä (`uv run mi serve`)?";
  } else if (import.meta.env.DEV && error instanceof Error) {
    details = error.message;
  }
  return (
    <>
      <Header />
      <main className="mx-auto max-w-6xl px-4 py-8">
        <h1 className="text-xl font-semibold">{message}</h1>
        <p className="mt-2 text-stone-600 dark:text-stone-400">{details}</p>
      </main>
    </>
  );
}
