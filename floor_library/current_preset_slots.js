'use strict';

const CURRENT_PRESET_GLOBAL_METADATA = Object.freeze([
  'global','variax','dt0','dt1','dtdual',
  'powercab0','powercab1','powercabdual','footswitch'
]);

// Pure collector for the complete native SF node tree, not a saved HLX file.
// catalog maps EVERY reviewed model symbol to its ir_select parameter names;
// an empty parameter list positively identifies a known non-IR model.
// Unknown snapshot/controller representations fail closed instead of silently
// dropping references. The native adapter must supply all global/DSP nodes.
function collectCurrentPresetSlots(tree, context, catalog) {
  const incomplete = reason => ({...context, complete:false, references:[], reason});
  const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const scalar = value => value === null || ['boolean','number','string'].includes(typeof value);
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const slot = value => Number.isInteger(value) && value >= 1 && value <= 128;
  if (!record(tree) || !record(tree.globalNodes) || !record(tree.global) || !record(tree.dsp0) || !record(tree.dsp1) ||
      !record(catalog) || !context || typeof context.presetId !== 'string' ||
      !Number.isInteger(context.revision)) return incomplete('invalid-tree');
  if (Object.keys(tree).some(key=>!['globalNodes','global','dsp0','dsp1'].includes(key)))
    return incomplete('unknown-tree-section');
  if (Object.entries(tree.global).some(([key,value])=>!key.startsWith('@') || !scalar(value)))
    return incomplete('unsupported-global-properties');
  const references = [];
  for (const dsp of ['dsp0','dsp1']) {
    if (['inputA','inputB','outputA','outputB','split','join'].some(name=>!own(tree[dsp],name)))
      return incomplete('missing-path-nodes');
    if (Object.keys(tree[dsp]).length > 64) return incomplete('too-many-nodes');
    for (const [name, node] of Object.entries(tree[dsp])) {
      if (!record(node) || typeof node['@model'] !== 'string') return incomplete('unknown-node');
      const model = node['@model'];
      // Stock HX Edit's pre-patch ID380 export substituted this host-only
      // placeholder for NAM and omitted Index. It is not proof of a non-IR
      // block, even though a host resource definition exists for the symbol.
      if (model.startsWith('HelixPlugin_')) return incomplete('host-placeholder-model');
      if (!own(catalog,model) || !Array.isArray(catalog[model])) return incomplete('unknown-model');
      for (const parameter of catalog[model]) {
        if (!own(node,parameter) || !slot(node[parameter])) return incomplete('invalid-slot-value');
        references.push({slot:node[parameter], blockId:`${dsp}:${name}`, parameter});
      }
      // The reviewed SF model stores parameter values directly as scalars.
      // A nested assignment/value representation has not yet been decoded.
      if (Object.values(node).some(value=>!scalar(value))) return incomplete('unsupported-node-value');
    }
  }
  const snapshotMetadata = new Set(['@name','@tempo','@valid','@custom_name','@ledcolor','@pedalstate']);
  const globals = tree.globalNodes;
  for (let snapshot=0;snapshot<8;snapshot++) {
    const node=globals[`snapshot${snapshot}`];
    if (!record(node)) return incomplete('missing-snapshot');
    for (const [key,value] of Object.entries(node)) {
      if (snapshotMetadata.has(key)) {
        if (!scalar(value)) return incomplete('invalid-snapshot-metadata');
      } else if (key==='blocks') {
        if (!record(value)) return incomplete('invalid-snapshot-blocks');
        for (const [dsp,blocks] of Object.entries(value)) {
          if (!['dsp0','dsp1'].includes(dsp) || !record(blocks)) return incomplete('unknown-snapshot-path');
          for (const [name,enabled] of Object.entries(blocks)) {
            if (!own(tree[dsp],name) || typeof enabled!=='boolean') return incomplete('unknown-snapshot-block');
          }
        }
      } else return incomplete('unsupported-snapshot-values');
    }
  }
  // Non-snapshot scalar metadata (global, variax, powercab, DT) cannot contain
  // an IR selection assignment. Any structured global payload needs its own
  // reviewed decoder; even an unfamiliar controller must not be ignored.
  const knownGlobalNodes = new Set(CURRENT_PRESET_GLOBAL_METADATA);
  for (const [name,node] of Object.entries(globals)) {
    if (/^snapshot[0-7]$/.test(name)) continue;
    // The stock SF emitter (86A408..86A414) omits zero-property nodes entirely.
    // Our complete native decoder retains them as {}. An exactly empty record
    // contains no reference values; this is not permission to ignore an unknown
    // populated controller/command node. Required snapshots were checked above.
    if (record(node) && Object.keys(node).length===0) continue;
    if (!knownGlobalNodes.has(name)) return incomplete('unknown-global-node');
    if (!record(node) || Object.values(node).some(value=>!scalar(value)))
      return incomplete('unsupported-global-values');
  }
  return {...context, complete:true, references};
}
