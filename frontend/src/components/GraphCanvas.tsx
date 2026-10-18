import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import { X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphData, Measure } from "../lib/types";

try {
  cytoscape.use(fcose);
} catch {
  /* already registered (hot reload) */
}

export interface Overlay {
  removed: [string, string][];
  islands: string[][];
  chains: string[][];
}

interface Props {
  graph: GraphData | null;
  view: "live" | "shadow";
  onView: (v: "live" | "shadow") => void;
  shadowAvailable: boolean;
  overlay: Overlay;
  highlight: string[];
  measure: Measure | null;
  themeKey: string;
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

function styles(): cytoscape.StylesheetJson {
  const c = {
    node: cssVar("--node"), border: cssVar("--node-border"), text: cssVar("--text"), muted: cssVar("--muted"),
    edge: cssVar("--edge"), accent: cssVar("--accent"), block: cssVar("--block"), bg: cssVar("--bg"),
  };
  return [
    {
      selector: "node",
      style: {
        "background-color": c.node, "border-color": c.border, "border-width": 1.5,
        width: "mapData(size, 0, 1, 18, 46)", height: "mapData(size, 0, 1, 18, 46)",
        label: "data(id)", "font-size": 10, color: c.text, "text-valign": "bottom", "text-margin-y": 4,
        "text-background-color": c.bg, "text-background-opacity": 0.85, "text-background-padding": "1px",
        "font-family": "Inter, system-ui, sans-serif",
      },
    },
    { selector: "node[tier = 0]", style: { "border-width": 3 } },
    {
      selector: "edge",
      style: {
        width: 1.2, "line-color": c.edge, "target-arrow-color": c.edge, "target-arrow-shape": "triangle",
        "arrow-scale": 0.7, "curve-style": "bezier", opacity: 0.9,
      },
    },
    { selector: ".dim", style: { opacity: 0.28 } },
    { selector: "node.island", style: { "background-color": c.block, "background-opacity": 0.22, "border-color": c.block, "border-width": 2.5 } },
    { selector: "edge.cut", style: { "line-color": c.block, "target-arrow-color": c.block, "line-style": "dashed", width: 2 } },
    { selector: "edge.chain", style: { "line-color": c.accent, "target-arrow-color": c.accent, width: 2.5 } },
    { selector: "node.hl", style: { "border-color": c.accent, "border-width": 3.5 } },
    { selector: "node:selected", style: { "border-color": c.accent, "border-width": 3.5 } },
  ];
}

export function GraphCanvas({ graph, view, onView, shadowAvailable, overlay, highlight, measure, themeKey }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const [picked, setPicked] = useState<string | null>(null);

  const elements = useMemo<ElementDefinition[]>(() => {
    if (!graph) return [];
    const max = Math.max(1, ...graph.services.map((s) => s.betweenness));
    const n = graph.services.length;
    return [
      ...graph.services.map((s, i) => ({
        group: "nodes" as const,
        data: { id: s.id, tier: s.tier, size: Math.sqrt(s.betweenness / max) },
        position: { x: 300 * Math.cos((2 * Math.PI * i) / n), y: 300 * Math.sin((2 * Math.PI * i) / n) },
      })),
      ...graph.calls.map((e) => ({
        group: "edges" as const, data: { id: `${e.source}->${e.target}`, source: e.source, target: e.target, via: e.via },
      })),
    ];
  }, [graph]);

  useEffect(() => {
    if (!ref.current || !elements.length) return;
    const cy = cytoscape({
      container: ref.current, elements, style: styles(), minZoom: 0.3, maxZoom: 2.5,
      layout: { name: "fcose", randomize: false, quality: "proof", animate: false, nodeRepulsion: () => 9000,
                idealEdgeLength: () => 70, padding: 40 } as cytoscape.LayoutOptions,
    });
    cy.on("tap", "node", (evt) => setPicked(evt.target.id()));
    cy.on("tap", (evt) => { if (evt.target === cy) setPicked(null); });
    cyRef.current = cy;
    // refit whenever the panel changes size (window resize, chat drawer opening, narrow layouts)
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => { cy.resize(); cy.fit(undefined, 40); });
    });
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
      cy.destroy();
      cyRef.current = null;
    };
  }, [elements]);

  useEffect(() => {
    // the theme attribute is set by the parent after this effect runs: restyle on the next frame
    const frame = requestAnimationFrame(() => cyRef.current?.style(styles()));
    return () => cancelAnimationFrame(frame);
  }, [themeKey]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("island cut chain hl dim");
      if (view === "shadow") {
        for (const [s, t] of overlay.removed) cy.getElementById(`${s}->${t}`).addClass("cut");
        for (const island of overlay.islands) for (const id of island) cy.getElementById(id).addClass("island");
        for (const chain of overlay.chains) {
          for (let i = 0; i < chain.length - 1; i++) {
            const edge = cy.getElementById(`${chain[i]}->${chain[i + 1]}`);
            if (!edge.hasClass("cut")) edge.addClass("chain"); // a cut edge stays red
          }
        }
      }
      if (highlight.length) {
        const set = new Set(highlight);
        cy.nodes().forEach((node) => { node.addClass(set.has(node.id()) ? "hl" : "dim"); });
      }
    });
  }, [overlay, view, highlight, elements]);

  const service = graph?.services.find((s) => s.id === picked);
  const change = measure?.betweenness.find((b) => b.service === picked);
  return (
    <section className="panel" aria-label="Company graph">
      <div className="panel-head">
        <h2>Company graph</h2>
        <span className="muted small">{graph ? `${graph.services.length} services · ${graph.calls.length} calls` : ""}</span>
        <span className="spacer" />
        <div className="seg" role="group" aria-label="Graph view">
          <button aria-pressed={view === "live"} onClick={() => onView("live")}>Live</button>
          <button aria-pressed={view === "shadow"} onClick={() => onView("shadow")} disabled={!shadowAvailable}
                  title={shadowAvailable ? "What the shadow copy looked like after the change" : "Run a check first"}>
            Shadow
          </button>
        </div>
      </div>
      <div className="graph-wrap">
        <div className="graph" ref={ref} role="img"
             aria-label={view === "shadow" ? "Service graph after the simulated change" : "Live service graph"} />
        {!graph && <div className="empty">Loading the graph…</div>}
        <div className="legend">
          <span>Node size = betweenness · thick ring = tier 0</span>
          {view === "shadow" && <span><i className="cut" />removed call</span>}
          {view === "shadow" && <span><b />cut off</span>}
        </div>
        {service && (
          <div className="node-card">
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <h3>{service.id}</h3>
              <button className="btn btn-ghost" style={{ height: 22, padding: "0 4px" }} onClick={() => setPicked(null)}
                      aria-label="Close"><X size={14} /></button>
            </div>
            <div className="small muted">{service.description}</div>
            <div className="small" style={{ marginTop: 6 }}>Tier {service.tier} · {service.team ?? "no team"}</div>
            <div className="small">Betweenness {service.betweenness.toFixed(2)}
              {change && view === "shadow" ? ` → ${change.shadow.toFixed(2)} in the shadow` : ""}</div>
          </div>
        )}
      </div>
    </section>
  );
}
