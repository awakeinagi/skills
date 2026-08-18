/** What the server owns inside an element the user thinks is one thing.
 *
 * A drawn control is not one element. A checkbox is a host plus a box
 * plus a tick; a KPI tile is a rectangle plus a value text; a body block
 * is a transparent rectangle plus five wavy strokes. The server names
 * every one of those relationships with a `<thing>_of` key in
 * `customData` and re-derives the piece at commit, so a piece is never
 * the thing to edit, pin, or inspect — its host is.
 *
 * This lives in its own module because the rule had already drifted: the
 * Inspector and the pin surfaces each carried their own enumerated list
 * of `_of` keys, and both lists were missing `body_of`. One rule, one
 * home, imported by both.
 */

/** The element fields the composed-piece rules read. */
export type ComposedEl = {
  id?: string;
  containerId?: string | null;
  customData?: Record<string, unknown> | null;
};

/**
 * The element a composed piece belongs to, if it is one.
 *
 * The SUFFIX is the rule rather than a list of keys, because a list goes
 * stale silently every time the server learns a new part — which is
 * exactly what happened to the two lists this replaced.
 * @param e Any live scene element.
 * @returns The host element's id, or null when it is not a piece of one.
 */
export function hostIdOf(e: ComposedEl): string | null {
  if (!e) return null;
  if (e.containerId) return String(e.containerId);
  const cd: Record<string, unknown> = e.customData || {};
  const key = Object.keys(cd).find((k) => k.endsWith("_of") && cd[k]);
  return key ? String(cd[key]) : null;
}

/**
 * Is this element a piece the server re-derives, rather than a thing in
 * its own right?
 *
 * Bound text counts (`containerId`), as does anything carrying an `_of`
 * key, as do the `label` and `decoration` roles — a role can mark a piece
 * that names no host, which the suffix alone would miss.
 * @param e Any live scene element.
 * @returns True when the element's host is the thing to act on.
 */
export function isComposedPiece(e: ComposedEl): boolean {
  const cd: Record<string, unknown> = (e && e.customData) || {};
  const role = String(cd.role || "");
  return role === "label" || role === "decoration" || !!hostIdOf(e);
}
