import React, { useEffect } from "react";

/** Report reader modal (demo parity: "readable in place, never
 * download-to-read"). Renders project_knowledge markdown served by
 * /api/doc — tiny renderer, no dependencies: headings, bold/italic/code,
 * lists, tables, hr, paragraphs. */

function inline(s: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let rest = s;
  let k = 0;
  const rx = /(\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`)/;
  while (rest.length) {
    const m = rest.match(rx);
    if (!m || m.index == null) {
      out.push(rest);
      break;
    }
    if (m.index > 0) out.push(rest.slice(0, m.index));
    if (m[2] != null) out.push(<b key={k++}>{m[2]}</b>);
    else if (m[3] != null) out.push(<i key={k++}>{m[3]}</i>);
    else out.push(<code key={k++}>{m[4]}</code>);
    rest = rest.slice(m.index + m[1].length);
  }
  return out;
}

function renderMd(md: string): React.ReactNode[] {
  const lines = md.split("\n");
  const out: React.ReactNode[] = [];
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const ln = lines[i];
    if (!ln.trim()) { i++; continue; }
    const h = ln.match(/^(#{1,4})\s+(.*)/);
    if (h) {
      const Tag = (["h1", "h2", "h3", "h4"][h[1].length - 1]) as any;
      out.push(<Tag key={k++}>{inline(h[2])}</Tag>);
      i++;
      continue;
    }
    if (/^(-{3,}|\*{3,})\s*$/.test(ln)) { out.push(<hr key={k++} />); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(ln)) {
      const rows: string[][] = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      out.push(
        <table key={k++}>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri}>{r.map((c, ci) =>
                ri === 0 ? <th key={ci}>{inline(c)}</th> : <td key={ci}>{inline(c)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      );
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(ln)) {
      const items: string[] = [];
      const ordered = /^\s*\d+\./.test(ln);
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""));
        i++;
      }
      const L = (ordered ? "ol" : "ul") as any;
      out.push(<L key={k++}>{items.map((it, ii) => <li key={ii}>{inline(it)}</li>)}</L>);
      continue;
    }
    const para: string[] = [ln];
    i++;
    while (i < lines.length && lines[i].trim() &&
           !/^(#{1,4}\s|\s*\|.*\|\s*$|\s*([-*]|\d+\.)\s|-{3,})/.test(lines[i])) {
      para.push(lines[i]);
      i++;
    }
    out.push(<p key={k++}>{inline(para.join(" "))}</p>);
  }
  return out;
}

/**
 * Modal reader for project_knowledge markdown documents — path in the
 * header, rendered markdown body, closed via backdrop, ✕, or Escape.
 * @param root0 Props: `path` (project-relative path shown in the header),
 * `content` (raw markdown), `onClose` (close callback).
 * @returns The reader modal.
 */
export function DocReader({ path, content, onClose }: {
  path: string; content: string; onClose: () => void;
}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  const download = () => {
    const blob = new Blob([content], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = path.split("/").pop() || "document.md";
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal doc-reader" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-kind">📄 {path}</span>
          <button className="modal-close" onClick={download} title="download the source">⭳ download</button>
          <button className="modal-close" onClick={onClose} title="close (Esc)">✕</button>
        </div>
        <div className="doc-body">{renderMd(content)}</div>
      </div>
    </div>
  );
}
