import React from "react";

/** Tiny schematic rendering of a scene — enough to recognize an artifact in
 * the filmstrip without paying for a real Excalidraw export. */
export function SceneThumb({ elements }: { elements: any[] }) {
  const els = (elements || []).filter((e) => !e.isDeleted);
  if (!els.length)
    return (
      <svg viewBox="0 0 120 64">
        <rect x="30" y="20" width="60" height="24" rx="4" fill="none" stroke="#c9c2b2" strokeDasharray="3 3" />
      </svg>
    );
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const e of els) {
    if (Math.abs(e.x) > 1e9) continue;
    minX = Math.min(minX, e.x);
    minY = Math.min(minY, e.y);
    maxX = Math.max(maxX, e.x + (e.width || 0));
    maxY = Math.max(maxY, e.y + (e.height || 0));
  }
  if (!isFinite(minX)) { minX = 0; minY = 0; maxX = 100; maxY = 60; }
  const pad = 12;
  const vb = `${minX - pad} ${minY - pad} ${maxX - minX + pad * 2} ${maxY - minY + pad * 2}`;
  const sw = Math.max((maxX - minX) / 60, 1.5);
  return (
    <svg viewBox={vb} preserveAspectRatio="xMidYMid meet">
      {els.map((e) => {
        const role = e.customData?.role;
        const stroke = role === "pin" ? "#d9930d" : role === "annotation" ? "#8a7f3d" : "#555043";
        if (e.type === "rectangle" || e.type === "frame")
          return <rect key={e.id} x={e.x} y={e.y} width={e.width} height={e.height} fill={e.type === "frame" ? "none" : "#efece2"} stroke={stroke} strokeWidth={sw} strokeDasharray={e.type === "frame" ? `${sw * 3} ${sw * 2}` : undefined} rx={sw} />;
        if (e.type === "ellipse")
          return <ellipse key={e.id} cx={e.x + e.width / 2} cy={e.y + e.height / 2} rx={e.width / 2} ry={e.height / 2} fill="#efece2" stroke={stroke} strokeWidth={sw} />;
        if (e.type === "diamond") {
          const cx = e.x + e.width / 2, cy = e.y + e.height / 2;
          return <polygon key={e.id} points={`${cx},${e.y} ${e.x + e.width},${cy} ${cx},${e.y + e.height} ${e.x},${cy}`} fill="#efece2" stroke={stroke} strokeWidth={sw} />;
        }
        if ((e.type === "arrow" || e.type === "line") && e.points?.length >= 2) {
          const pts = e.points.map((p: number[]) => `${e.x + p[0]},${e.y + p[1]}`).join(" ");
          return <polyline key={e.id} points={pts} fill="none" stroke={stroke} strokeWidth={sw} />;
        }
        if (e.type === "text" && !e.containerId)
          return <rect key={e.id} x={e.x} y={e.y} width={Math.max(e.width, 8)} height={Math.max(e.height, 4)} fill={role === "pin" ? "#e8bd6d" : "#ddd6c4"} rx={sw} />;
        return null;
      })}
    </svg>
  );
}
