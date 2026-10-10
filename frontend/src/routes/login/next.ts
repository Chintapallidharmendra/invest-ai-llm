/**
 * Where to go after sign-in: `next` only if it is a same-origin path (open-redirect
 * protection). `//evil.com`, `/\evil.com`, `https://…` and anything that resolves to
 * another origin are rejected, as is `/login` itself.
 */

export const HOME_PATH = "/";

export function safeNext(next: string | null | undefined, origin = window.location.origin): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return HOME_PATH;
  }
  let url: URL;
  try {
    url = new URL(next, origin);
  } catch {
    return HOME_PATH;
  }
  if (url.origin !== origin || url.pathname === "/login") return HOME_PATH;
  return `${url.pathname}${url.search}${url.hash}`;
}
