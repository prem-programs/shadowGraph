import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import { useEffect, useRef } from "react";
import type { ImpactReport } from "../lib/ha";

function v(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

/** The blast radius as a small directed graph: change → removed names → integrations that use them. */
export function ImpactGraph({ r, themeKey }: { r: ImpactReport; themeKey: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const els: ElementDefinition[] = [];
    const cid = "change";
    els.push({ data: { id: cid, label: r.change.number ? `#${r.change.number}` : "change", kind: "change" } });
    const syms = [...r.api.removed.filter((s) => !s.consequence_of), ...r.api.deprecated, ...r.api.signature].slice(0, 14);
    const symIds = new Set(syms.map((s) => s.id));
    for (const s of syms) {
      els.push({ data: { id: s.id, label: s.name, kind: "symbol" } });
      els.push({ data: { id: `${cid}>${s.id}`, source: cid, target: s.id } });
    }
    const consumers = [...r.consumers].sort((a, b) => (a.level === "safe" ? 1 : 0) - (b.level === "safe" ? 1 : 0)).slice(0, 36);
    for (const c of consumers) {
      els.push({ data: { id: c.id, label: c.name.length > 22 ? `${c.name.slice(0, 21)}…` : c.name, kind: c.kind, level: c.level } });
      for (const s of c.symbols) {
        const target = symIds.has(s) ? s : syms.find((x) => s.startsWith(`${x.id}.`))?.id;
        if (target) els.push({ data: { id: `${target}>${c.id}`, source: target, target: c.id } });
      }
    }
    for (const x of r.cascade.slice(0, 12)) {
      els.push({ data: { id: x.id, label: x.name, kind: "cascade", level: "breaks" } });
      for (const b of x.because) if (consumers.some((c) => c.id === b)) els.push({ data: { id: `${b}>${x.id}`, source: b, target: x.id } });
    }
    const c = {
      ink: v("--ink"), ink3: v("--ink-3"), rule: v("--rule-2"), sheet: v("--sheet"), breaks: v("--breaks"),
      retires: v("--retires"), review: v("--review"), clear: v("--clear"),
    };
    cy.current?.destroy();
    cy.current = cytoscape({
      container: ref.current,
      elements: els,
      minZoom: 0.3,
      maxZoom: 2.5,
      layout: { name: "breadthfirst", directed: true, roots: [cid], spacingFactor: 1.05, padding: 16, avoidOverlap: true },
      style: [
        { selector: "node", style: {
          label: "data(label)", "font-family": "IBM Plex Mono, monospace", "font-size": 9, color: c.ink,
          "text-valign": "bottom", "text-margin-y": 3, width: 12, height: 12, "background-color": c.sheet,
          "border-width": 1.5, "border-color": c.ink3, "text-background-color": c.sheet, "text-background-opacity": 0.85,
          "text-background-padding": "1px" } },
        { selector: "node[kind = 'change']", style: { shape: "rectangle", width: 18, height: 18, "background-color": c.ink, "border-color": c.ink, "font-size": 11 } },
        { selector: "node[kind = 'symbol']", style: { shape: "diamond", width: 15, height: 15, "border-color": c.ink } },
        { selector: "node[level = 'breaks']", style: { "background-color": c.breaks, "border-color": c.breaks } },
        { selector: "node[level = 'may_break'], node[level = 'warns']", style: { "background-color": c.retires, "border-color": c.retires } },
        { selector: "node[level = 'review']", style: { "border-color": c.review, "border-width": 2.5 } },
        { selector: "node[level = 'safe']", style: { "border-color": c.clear } },
        { selector: "node[kind = 'cascade']", style: { shape: "triangle" } },
        { selector: "edge", style: { width: 1, "line-color": c.rule, "target-arrow-color": c.rule, "target-arrow-shape": "triangle", "arrow-scale": 0.6, "curve-style": "bezier" } },
      ],
    });
    return () => { cy.current?.destroy(); cy.current = null; };
  }, [r, themeKey]);
  return <div className="impact-graph" ref={ref} role="img" aria-label="Graph of the change, the names it removes and the integrations that use them" />;
}
