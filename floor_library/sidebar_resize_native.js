'use strict';

// Static native ABI evidence: sidebar-resize-static-audit-20261109.md.
// Live native layout/repaint remains UNVERIFIED. No hooks or device calls here.
function createNamSidebarResize(api, options = {}) {
  const policy = { minimum: 300, maximum: 600, initial: 360,
    editorMinimum: 640, depth: 8, ...options };
  const ids = { library: 0x6c697677, editor: 0x65647677 };
  let requested = policy.initial, busy = false, faulted = false, owned = null;
  let lastLayout = null;
  const same = (a, b) => api.same(a, b);
  const valid = value => value && !api.isNull(value);
  function box(widget) {
    const b = api.bounds(widget);
    if (!b || ![b.x, b.y, b.w, b.h].every(Number.isInteger) ||
        b.w < 0 || b.h <= 0 || b.w > 20000 || b.h > 20000 ||
        Math.abs(b.x) > 20000 || Math.abs(b.y) > 20000) throw Error('Invalid native bounds');
    return b;
  }
  function canonical(widget) {
    const result = api.canonical(widget); // Must bound and validate proxy traversal.
    if (!valid(result)) throw Error('Invalid native widget');
    return result;
  }
  function parent(widget) {
    return api.getParent(canonical(widget));
  }
  function anchors(view) {
    const right=api.getAnchor(canonical(view.library), 1);
    const left=api.getAnchor(canonical(view.editor), 3);
    if (![right,left].every(Number.isInteger) || right < -1 || left !== view.e.x)
      throw Error('Unrecognized native anchors');
    return {right,left};
  }
  function resolve() {
    let ancestor = api.library();
    const seen = [];
    for (let depth = 0; valid(ancestor) && depth < policy.depth; depth++) {
      ancestor = canonical(ancestor);
      if (seen.some(value => same(value, ancestor))) throw Error('Native parent cycle');
      seen.push(ancestor);
      // Direct children only: never accept a same-ID nested librarian by accident.
      const library = api.findWidget(ancestor, ids.library, 0);
      const editor = api.findWidget(ancestor, ids.editor, 0);
      if (valid(library) && valid(editor)) {
        if (same(library, editor) || !same(parent(library), ancestor) ||
            !same(parent(editor), ancestor)) throw Error('Native sibling ownership mismatch');
        const p = box(ancestor), l = box(canonical(library)), e = box(canonical(editor));
        // Numeric layout evidence only; no pointers, preset or capture data.
        lastLayout = {depth, parent:p, library:l, editor:e,
          right:api.getAnchor(canonical(library), 1), left:api.getAnchor(canonical(editor), 3)};
        const knownResize = owned && same(owned.ancestor, ancestor) &&
          same(owned.library, library) && same(owned.editor, editor) &&
          api.getAnchor(canonical(library), 1) === owned.right &&
          api.getAnchor(canonical(editor), 3) === owned.left;
        if (p.w <= 0 || e.w <= 0 || (!knownResize && l.w <= 0) ||
            l.x !== 8 || (!knownResize && e.x !== l.x + l.w) || e.x + e.w !== p.w ||
            l.y < 0 || e.y < 0 || l.y + l.h > p.h || e.y + e.h > p.h)
          throw Error('Unrecognized native sidebar layout');
        return { ancestor, library, editor, p, l, e };
      }
      ancestor = parent(ancestor);
    }
    throw Error('Native sidebar siblings not found');
  }
  function apply(width, remember) {
    if (!api.ready()) return { ok: false, reason: 'gui-thread-required' };
    if (busy) return { ok: false, reason: 'busy' };
    if (faulted) return { ok: false, reason: 'native-layout-fault' };
    if (!Number.isFinite(width)) return { ok: false, reason: 'invalid-width' };
    busy = true;
    let mutationStarted = false;
    try {
      const target = Math.max(policy.minimum, Math.min(policy.maximum, Math.round(width)));
      const view = resolve();
      const maximum = Math.min(policy.maximum, view.p.w - view.l.x - policy.editorMinimum);
      if (maximum < policy.minimum) return { ok: false, reason: 'window-too-narrow' };
      const actual = Math.min(target, maximum);
      const right = view.p.w - view.l.x - actual;
      const left = view.l.x + actual;
      const verifiedAnchors=anchors(view);
      const oldRight=verifiedAnchors.right, oldLeft=verifiedAnchors.left;
      if (remember) requested = target;
      if (view.l.w === actual && oldLeft === left)
        return { ok: true, changed: false, width: actual, requested };
      mutationStarted = true;
      // Native setters invoke normal relayout; never write geometry memory directly.
      if (oldRight !== right) api.setAnchor(canonical(view.library), 1, right, 1);
      if (oldLeft !== left) api.setAnchor(canonical(view.editor), 3, left, 1);
      const l = box(canonical(view.library)), e = box(canonical(view.editor));
      if (l.w !== actual || e.x !== left || e.w < policy.editorMinimum)
        throw Error('Native resize did not confirm');
      owned = {ancestor:view.ancestor, library:view.library, editor:view.editor, right, left};
      return { ok: true, changed: true, width: actual, requested };
    } catch (error) {
      // A partial native mutation disables subsequent attempts in this instance.
      if (mutationStarted) faulted = true;
      return { ok: false, reason: mutationStarted ? 'native-layout-fault' : 'layout-unavailable',
        detail: String(error.message || error) };
    } finally { busy = false; }
  }
  function inspect() {
    if (!api.ready() || busy || faulted) return {available:false,
      reason:!api.ready() ? 'gui-thread-required' : busy ? 'busy' : 'native-layout-fault'};
    lastLayout = null;
    try { const view=resolve(); anchors(view);
      const available=view.p.w-view.l.x-policy.editorMinimum>=policy.minimum;
      return {available, reason:available ? '' : 'window-too-narrow', width:view.l.w, requested,
        layout:lastLayout};
    } catch (error) { return {available:false, reason:'layout-unavailable',
      detail:String(error.message || error), layout:lastLayout}; }
  }
  return { requestWidth: width => apply(width, true), refresh: () => apply(requested, false),
    inspect, reset: () => { owned=null; faulted=false; } };
}

// Optional adapter for integration into the existing GUI-thread dispatcher.
// `method` is the existing pinned thiscall wrapper; caller supplies thread guard.
function createNamSidebarNativeApi(method, getLibrary, isGuiThread) {
  const getParent = method(0x3b8790, 'pointer', []);
  const findWidget = method(0x3b7ae0, 'pointer', ['uint', 'int']);
  const getAnchor = method(0x3b7f30, 'int', ['int']);
  const setAnchor = method(0x3bbef0, 'void', ['int', 'int', 'int']);
  function canonical(widget) {
    const seen = [];
    for (let n = 0; n < 8; n++) {
      if (!widget || widget.isNull() || seen.some(p => p.equals(widget)))
        throw Error('Invalid native proxy chain');
      seen.push(widget);
      const geometry = widget.add(0x54).readPointer();
      if (geometry.isNull()) throw Error('Missing native geometry');
      const proxy = geometry.add(0xb4).readPointer();
      if (proxy.isNull()) return widget;
      widget = proxy;
    }
    throw Error('Native proxy depth exceeded');
  }
  return { ready: isGuiThread, library: getLibrary, canonical, getParent, findWidget,
    getAnchor, setAnchor, same: (a,b) => !!a && !!b && a.equals(b),
    isNull: p => p.isNull(), bounds(widget) {
      const g = canonical(widget).add(0x54).readPointer();
      const x = g.add(4).readS32(), y = g.add(8).readS32();
      return {x, y, w: g.add(12).readS32()-x, h: g.add(16).readS32()-y};
    } };
}

if (typeof module !== 'undefined') module.exports = {createNamSidebarResize, createNamSidebarNativeApi};
