export function ms(value?: number | null): string {
  if (value === undefined || value === null) return "–";
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)} s`;
  return value < 10 ? `${value.toFixed(1)} ms` : `${Math.round(value)} ms`;
}

export function n(value?: number | null): string {
  if (value === undefined || value === null) return "–";
  return value.toLocaleString("en-US");
}

export function plural(count: number, one: string, many?: string): string {
  return `${n(count)} ${count === 1 ? one : (many ?? `${one}s`)}`;
}

export function ago(ts?: number | string | null): string {
  if (!ts) return "";
  const t = typeof ts === "number" ? ts : Date.parse(ts);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return new Date(t).toISOString().slice(0, 10);
}

export function date(value?: string | null): string {
  if (!value) return "";
  const d = new Date(value.length === 10 ? `${value}T12:00:00Z` : value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function shortDate(value?: string | null): string {
  if (!value) return "";
  const d = new Date(value.length === 10 ? `${value}T12:00:00Z` : value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/** 'homeassistant.components.sensor.EntityCategory' -> ['homeassistant.components.sensor.', 'EntityCategory'] */
export function splitName(id: string, name?: string): [string, string] {
  if (name && id.endsWith(name)) return [id.slice(0, id.length - name.length), name];
  const i = id.lastIndexOf(".");
  return i < 0 ? ["", id] : [id.slice(0, i + 1), id.slice(i + 1)];
}

export function fileName(path: string): string {
  return path.split("/").pop() ?? path;
}

export function agentClass(agent?: string | null): string {
  if (!agent) return "agent-other";
  if (agent.startsWith("sim")) return "agent-sim";
  if (["scout", "scribe", "judge"].includes(agent)) return `agent-${agent}`;
  return "agent-other";
}
