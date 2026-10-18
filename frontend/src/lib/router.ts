import { useEffect, useState } from "react";

/** Hash routes, so every view has a link you can paste: #/ha/changes/PR-186863 */
export function parseHash(hash: string): string[] {
  return hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
}

export function useRoute(): string[] {
  const [parts, setParts] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const on = () => setParts(parseHash(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parts;
}

export function href(...parts: (string | number)[]): string {
  return "#/" + parts.map((p) => encodeURIComponent(String(p))).join("/");
}

export function go(...parts: (string | number)[]): void {
  window.location.hash = href(...parts);
}
