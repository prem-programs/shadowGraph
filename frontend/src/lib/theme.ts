import { createContext, useCallback, useEffect, useState } from "react";
import { storage } from "./api";

export type ThemeChoice = "system" | "light" | "dark";
export const ThemeContext = createContext<string>("light");

function resolved(choice: ThemeChoice): "light" | "dark" {
  if (choice !== "system") return choice;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(() => (storage.get("sg.theme") as ThemeChoice) || "system");
  const [effective, setEffective] = useState(() => resolved(choice));
  useEffect(() => {
    const root = document.documentElement;
    if (choice === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", choice);
    storage.set("sg.theme", choice === "system" ? null : choice);
    // let the new CSS variables apply before canvases restyle
    requestAnimationFrame(() => setEffective(resolved(choice)));
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const on = () => choice === "system" && setEffective(resolved("system"));
    mq?.addEventListener?.("change", on);
    return () => mq?.removeEventListener?.("change", on);
  }, [choice]);
  const cycle = useCallback(() => setChoice((c) => (c === "system" ? (resolved("system") === "dark" ? "light" : "dark") : c === "light" ? "dark" : "light")), []);
  return { choice, effective, cycle };
}
