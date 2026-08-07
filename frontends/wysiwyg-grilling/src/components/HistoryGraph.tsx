import React, { useMemo, useState } from "react";

/** Horizontal git-graph: main on the center lane, branches alternating
 * above/below, bezier fork edges, callout head-labels. Click a node = view
 * that commit; click a head label = switch branch. */
export function HistoryGraph({
  saves, branches, head, headRevn, viewingRevn, checkoutRevn, dirty,
  showArchived, onViewCommit, onSwitchBranch,
}: {
  saves: any[]; branches: any[]; head: string; headRevn: number;
  viewingRevn: number | null; checkoutRevn: number | null; dirty: boolean;
  showArchived: boolean;
  onViewCommit: (revn: number) => void; onSwitchBranch: (name: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  const layout = useMemo(() => {
    const visible = new Set(
      branches.filter((b) => showArchived || !b.archived).map((b) => b.name)
    );
    const shown = saves.filter((s) => visible.has(s.branch));
    const ordered = [...shown].sort((a, b) => a.revn - b.revn);
    // lanes: main center (0), branches alternate above (-1, -2…) / below (+1, +2…)
    const laneOf: Record<string, number> = { main: 0 };
    let i = 0;
    for (const name of branches.map((b) => b.name)) {
      if (name === "main" || !visible.has(name)) continue;
      i++;
      laneOf[name] = i % 2 === 1 ? -Math.ceil(i / 2) : Math.ceil(i / 2);
    }
    const lanes = new Set(ordered.map((s) => laneOf[s.branch] ?? 0));
    lanes.add(0);
    const minLane = Math.min(...lanes), maxLane = Math.max(...lanes);
    const X0 = 46, DX = 56, laneH = 36, PADY = 30;
    const midY = PADY + -minLane * laneH;
    const pos: Record<number, { x: number; y: number; s: any }> = {};
    ordered.forEach((s, idx) => {
      pos[s.revn] = { x: X0 + idx * DX, y: midY + (laneOf[s.branch] ?? 0) * laneH, s };
    });
    const width = X0 + ordered.length * DX + 130;
    const height = PADY * 2 + (maxLane - minLane) * laneH + 8;
    const heads: Record<string, number> = {};
    for (const b of branches) if (visible.has(b.name)) heads[b.name] = b.head;
    return { pos, ordered, width, height: Math.max(height, 76), laneOf, heads, midY };
  }, [saves, branches, showArchived]);

  const { pos, ordered, width, heads } = layout;
  const height = layout.height;

  if (collapsed || !ordered.length)
    return (
      <div className="graph-panel">
        <div className="graph-bar" onClick={() => setCollapsed(false)}>
          ⎇ history graph {ordered.length ? `(${ordered.length} saves)` : "(empty)"} ▸
        </div>
      </div>
    );

  return (
    <div className="graph-panel">
      <div className="graph-bar" onClick={() => setCollapsed(true)}>⎇ history graph ▾</div>
      <div className="graph-scroll">
        <svg className="graph-svg" width={width} height={height}>
          {/* lane lines + fork edges */}
          {ordered.map((s) => {
            const p = pos[s.revn];
            const parent = pos[s.base_revn];
            if (!parent) return null;
            const c = p.y === parent.y
              ? `M ${parent.x} ${parent.y} L ${p.x} ${p.y}`
              : `M ${parent.x} ${parent.y} C ${parent.x + 28} ${parent.y}, ${p.x - 28} ${p.y}, ${p.x} ${p.y}`;
            return (
              <path key={`e${s.revn}`} d={c} fill="none"
                stroke={s.branch === "main" ? "#39415a" : "#4a4258"} strokeWidth={2} />
            );
          })}
          {/* dashed ghost: unsaved fork in progress (detached + edits) */}
          {checkoutRevn != null && dirty && pos[checkoutRevn] && (
            <g>
              <path
                d={`M ${pos[checkoutRevn].x} ${pos[checkoutRevn].y} C ${pos[checkoutRevn].x + 30} ${pos[checkoutRevn].y}, ${pos[checkoutRevn].x + 30} ${pos[checkoutRevn].y - 34}, ${pos[checkoutRevn].x + 56} ${pos[checkoutRevn].y - 34}`}
                fill="none" stroke="#e6a23c" strokeWidth={1.5} strokeDasharray="4 3" />
              <circle cx={pos[checkoutRevn].x + 56} cy={pos[checkoutRevn].y - 34} r={6}
                fill="none" stroke="#e6a23c" strokeWidth={1.5} strokeDasharray="3 2" />
            </g>
          )}
          {/* commit dots + captions */}
          {ordered.map((s) => {
            const p = pos[s.revn];
            const isHead = s.revn === headRevn;
            const isViewed = viewingRevn === s.revn || checkoutRevn === s.revn;
            const color = s.author === "agent" ? "#3ecf8e" : s.author === "out-of-session" ? "#e6a23c" : "#5b9dff";
            return (
              <g key={s.revn} className="commit" onClick={() => onViewCommit(s.revn)}>
                {isHead && <circle cx={p.x} cy={p.y} r={11} fill="none" stroke="#5b9dff" strokeWidth={1.5} opacity={0.55} />}
                {isViewed && <circle cx={p.x} cy={p.y} r={9} fill="none" stroke="#e6a23c" strokeWidth={2} />}
                <circle cx={p.x} cy={p.y} r={5.5} fill={color} stroke="#14161b" strokeWidth={1.5} />
                <text x={p.x} y={p.y + 22} textAnchor="middle">{s.revn}</text>
                <title>{`revn ${s.revn} · ${s.short_id} · ${s.author}\n${s.headline}`}</title>
              </g>
            );
          })}
          {/* callout labels on branch heads */}
          {Object.entries(heads).map(([name, revn]) => {
            const p = pos[revn as number];
            if (!p) return null;
            const w = name.length * 6.6 + 16;
            return (
              <g key={name} className={`head-label ${name === head ? "current" : ""}`}
                onClick={(ev) => { ev.stopPropagation(); onSwitchBranch(name); }}>
                <rect x={p.x + 12} y={p.y - 9} width={w} height={18} rx={4} />
                <text x={p.x + 12 + w / 2} y={p.y + 3.5} textAnchor="middle">{name}</text>
                <title>switch to branch “{name}”</title>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
