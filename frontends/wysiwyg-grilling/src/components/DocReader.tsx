import React, { useEffect, useMemo, useRef } from "react";
import { createPortal } from "react-dom";

/** Report reader modal (demo parity: "readable in place, never
 * download-to-read"). Renders project_knowledge markdown served by
 * /api/doc — tiny renderer, no dependencies: headings, bold/italic/code,
 * lists, tables, hr, paragraphs. v0.3: heading outline + jump rail,
 * word count, portal + capture-phase Escape (Excalidraw binds keys in
 * the capture phase and used to eat the bubble-phase listener). */

/** One outline entry collected while rendering markdown. */
export type Heading = { level: number; text: string; id: string };

/**
 * URL-ish slug for a heading (outline anchors).
 * @param s Heading text.
 * @returns Lowercased dash-joined slug.
 */
export function slugify(s: string): string {
  return s.toLowerCase().replace(/[^\w]+/g, "-").replace(/^-+|-+$/g, "")
    .slice(0, 60) || "section";
}

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

/**
 * Render markdown to React nodes with the dependency-free mini renderer.
 * @param md Raw markdown.
 * @param headings Optional collector — filled with {level, text, id} per
 * h1–h3 so callers can build a jump rail; heading elements carry the id.
 * @returns The rendered nodes.
 */
export function renderMd(md: string, headings?: Heading[]): React.ReactNode[] {
  const lines = md.split("\n");
  const out: React.ReactNode[] = [];
  const seen = new Map<string, number>();
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const ln = lines[i];
    if (!ln.trim()) { i++; continue; }
    const h = ln.match(/^(#{1,4})\s+(.*)/);
    if (h) {
      const Tag = (["h1", "h2", "h3", "h4"][h[1].length - 1]) as any;
      let id: string | undefined;
      if (h[1].length <= 3) {
        const base = slugify(h[2]);
        const n = (seen.get(base) || 0) + 1;
        seen.set(base, n);
        id = n > 1 ? `${base}-${n}` : base;
        headings?.push({ level: h[1].length, text: h[2], id });
      }
      out.push(<Tag key={k++} id={id}>{inline(h[2])}</Tag>);
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
 * Modal reader for project_knowledge markdown documents — title from the
 * first h1, heading jump rail, word count, rendered body; closed via
 * backdrop, ✕, or Escape (capture phase, so Excalidraw can't eat it).
 * @param root0 Props: `path` (project-relative path, header subtitle),
 * `content` (raw markdown), `onClose` (close callback).
 * @returns The reader modal, portaled to document.body.
 */
export function DocReader({ path, content, onClose }: {
  path: string; content: string; onClose: () => void;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", h, true);
    return () => window.removeEventListener("keydown", h, true);
  }, [onClose]);
  useEffect(() => { boxRef.current?.focus(); }, []);
  const { nodes, headings, words, title } = useMemo(() => {
    const hs: Heading[] = [];
    const rendered = renderMd(content, hs);
    return {
      nodes: rendered,
      headings: hs,
      words: content.split(/\s+/).filter(Boolean).length,
      title: hs.find((x) => x.level === 1)?.text ||
        (path.split("/").pop() || path),
    };
  }, [content, path]);
  const jump = (id: string) => {
    const el = bodyRef.current?.querySelector(`#${CSS.escape(id)}`);
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const download = () => {
    const blob = new Blob([content], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = path.split("/").pop() || "document.md";
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return createPortal(
    <div className="modal-backdrop doc-backdrop" onClick={onClose}>
      <div className="modal doc-reader" ref={boxRef} tabIndex={-1}
           onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-kind">📄 {title}</span>
          <span className="doc-meta">{path} · {words} words</span>
          <button className="modal-close" onClick={download} title="download the source">⭳ download</button>
          <button className="modal-close" onClick={onClose} title="close (Esc)">✕</button>
        </div>
        <div className="doc-cols">
          {headings.length > 1 && (
            <nav className="doc-outline">
              {headings.map((hd) => (
                <button key={hd.id} className={`doc-jump lvl${hd.level}`}
                        onClick={() => jump(hd.id)}>{hd.text}</button>
              ))}
            </nav>
          )}
          <div className="doc-body" ref={bodyRef}>{nodes}</div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
