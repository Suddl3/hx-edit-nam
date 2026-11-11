"""NAM presentation aliases refreshed after existing native calls (no interior hook)."""
from audition_diagnostics import replace_once


NAME_HELPERS = r'''
  let displayRefreshing = false;
  let lastModelAlias = null;
  function readbackDisplayItem(slot) {
    // This evidence comes from current-session content verification, not names.
    if (typeof slotLedger === 'undefined') return null;
    const id = slotLedger.contentFor(slot);
    return id ? library.find(item => item.contentId === id) || null : null;
  }
  function boundedNativeText(pointer, capacity = 256) {
    // Frida's length argument means an EXACT byte count, not a maximum.
    // Find the terminator first so padding never becomes part of the alias.
    for (let length = 0; length < capacity; length++) {
      if (pointer.add(length).readU8() === 0)
        return length === 0 ? '' : pointer.readUtf8String(length);
    }
    throw new Error('Unterminated native display label');
  }
  function displayCaptureTitle(slot, raw) {
    // Display aliases never establish installation or authorize Model selection.
    if (!Number.isInteger(slot) || slot < 1 || slot > 128 || typeof raw !== 'string') return raw;
    const readback = readbackDisplayItem(slot);
    if (readback) return readback.title;
    const verified = verifiedMetadata[slot];
    const ownedName = profiles[slot] === raw || (audition?.slot === slot && audition.name === raw);
    if (verified && ownedName) return verified.title;
    const candidates = library.filter(item => item.aliases?.some(alias =>
      alias === raw || (raw.length >= 31 && alias.startsWith(raw))));
    const titles = [...new Set(candidates.map(item => item.title))];
    if (titles.length === 1) return titles[0];
    // Only remove the converter's exact transport prefix, not arbitrary words.
    const match = raw.match(/^T3K\s+\d+\s+[0-9a-f]{4,64}\s+(.+)$/i);
    return match ? match[1].trim() : raw;
  }

  function friendlyModelDisplay(slot, original, physicalName) {
    if (typeof original !== 'string' || typeof physicalName !== 'string' || !physicalName) return original;
    let prefix = '';
    if (original === '') {
      // Stock popup clear temporarily empties its caption. The active Model
      // and physical row have already been validated by modelDisplayTarget.
      prefix = `${slot}: `;
    } else if (original !== physicalName) {
      const match = original.match(/^(\d+:\s*)(.*)$/);
      if (!match || Number(match[1].split(':')[0]) !== slot || match[2] !== physicalName) return original;
      prefix = match[1];
    }
    const title = displayCaptureTitle(slot, physicalName);
    const result = prefix + title;
    // Keep the same bounded text capacity as the stock formatter, even though
    // this path now supplies its own allocation after the function returns.
    let bytes = 0;
    for (const ch of result) {
      const code = ch.codePointAt(0);
      bytes += code <= 0x7f ? 1 : code <= 0x7ff ? 2 : code <= 0xffff ? 3 : 4;
    }
    return bytes <= 255 && !result.includes('\0') ? result : original;
  }
'''

NATIVE_DISPLAY_HOOK = r'''
  // Pinned HX Edit SHA-256 f7faac4996164f9039c9ae3ca8d85cd135c5bceae8a511c8921a1244046cab66.
  // Validate the stock setter call-site, but DO NOT attach inside its function.
  // It already has an entry/onLeave hook. A second interior interceptor confuses
  // x86 return tracking in the installed Frida runtime (isolated probe reproduced).
  const displaySite = address(0x26bfed);
  const expectedDisplaySite = [0x8b,0x8b,0x44,0x01,0x00,0x00,0x8d,0x95,0xf0,0xfe,0xff,0xff];
  if (!expectedDisplaySite.every((value, index) => displaySite.add(index).readU8() === value)) {
    throw new Error('The NAM display formatter does not match the reviewed HX Edit build.');
  }
  function modelDisplayTarget(slider) {
    if (!slider.equals(activeModel) || !hasActiveModel()) return null;
    const label = slider.add(0x7c).readPointer();
    const format = slider.add(0x98).readPointer();
    if (label.isNull() || format.isNull()) return null;
    const labelText = label.add(0x7c).readPointer();
    const displayType = format.add(0x10).readPointer();
    if (labelText.isNull() || displayType.isNull() || labelText.readCString() !== 'Model'
        || displayType.readCString() !== 'ir_select') return null;
    const slot = getValue(slider) + 1;
    if (!Number.isInteger(slot) || slot < 1 || slot > 128) return null;
    const list = irList();
    if (list.isNull()) return null;
    const row = getIrRow(list, slot - 1);
    if (row.isNull() || !row.add(0x10c).readU32()) return null;
    const menu = slider.add(0x144).readPointer();
    if (menu.isNull()) return null;
    return {slot, menu, physicalName: boundedNativeText(row)};
  }
  function refreshDisplayedModelName() {
    if (displayRefreshing || activeModel.isNull()) return;
    displayRefreshing = true;
    try {
      const target = modelDisplayTarget(activeModel);
      if (!target) return;
      // Static calls at RVA 0x26bffe/0x26c014 prove this text-only ABI.
      // Getter returns a char pointer; setter consumes the same transient
      // formatted char buffer used by the stock code, with notify/redraw 0.
      const table = target.menu.readPointer();
      const setter = table.add(0x1b8).readPointer();
      const getter = table.add(0x1bc).readPointer();
      const end = base.add(Process.mainModule.size);
      if ([setter, getter].some(fn => fn.compare(base) < 0 || fn.compare(end) >= 0)) return;
      const readText = new NativeFunction(getter, 'pointer', ['pointer'], 'thiscall');
      const text = readText(target.menu);
      if (text.isNull()) return;
      const original = boundedNativeText(text);
      const previous=lastModelAlias;
      const source=previous && previous.menu.equals(target.menu) && previous.slot===target.slot
        && previous.physicalName===target.physicalName && previous.text===original
        ? previous.source : original;
      const replacement = friendlyModelDisplay(target.slot, source, target.physicalName);
      if (replacement === original) return;
      const setText = new NativeFunction(setter, 'void', ['pointer', 'pointer', 'int'], 'thiscall');
      setText(target.menu, Memory.allocUtf8String(replacement), 0);
      lastModelAlias={menu:target.menu,slot:target.slot,physicalName:target.physicalName,source,text:replacement};
      redraw(target.menu);
    } catch (error) {
      send({type: 'toneTrace', phase: 'model-display-refresh-skipped', reason: String(error)});
    } finally { displayRefreshing = false; }
  }

'''

NATIVE_LIBRARY_LABELS = r'''
  // Stock menu reconstruction calls this no-argument thiscall clear followed by
  // the already-used append routine. Populate explicitly: Frida suppresses the
  // nested append interceptor when reconstruction is called from our callback.
  const menuClearSite=address(0x26e846);
  const menuClearBytes=[0x8b,0x8e,0x44,0x01,0x00,0x00,0xe8,0xef,0xd4,0x13,0x00];
  if(!menuClearBytes.every((b,i)=>menuClearSite.add(i).readU8()===b))
    throw new Error('The NAM popup clear call does not match the reviewed build.');
  const clearModelChoices=method(0x3abd40,'void',[]);
  const appendModelChoice=method(0x3ac110,'pointer',['int','pointer','int','int','int']);
  function rebuildVerifiedModelMenu(slider){
    const target=modelDisplayTarget(slider);
    if(!target)return;
    const list=irList(),choices=[];
    for(let slot=1;slot<=128;slot++){
      const row=getIrRow(list,slot-1);
      if(row.isNull() || !row.add(0x10c).readU32())continue;
      const raw=boundedNativeText(row);
      const imported=profiles[slot]===raw && verifiedMetadata[slot]
        && (slot===128 ? audition?.slot===slot : slotLedger.importCurrent(slot));
      const preview=audition?.slot===slot && audition.name===raw;
      if(!readbackDisplayItem(slot) && !imported && !preview)continue;
      choices.push({slot,label:`${slot}: ${displayCaptureTitle(slot,raw)}`,
        disabled:Number(!!(row.add(0x114).readU32()&0x10))});
    }
    clearModelChoices(target.menu);
    for(const choice of choices)
      appendModelChoice(target.menu,-1,Memory.allocUtf8String(choice.label),choice.slot-1,0,choice.disabled);
    if(!choices.length)
      appendModelChoice(target.menu,-1,Memory.allocUtf8String('No verified NAM captures yet'),-1,0,1);
    // Restore popup selection only; no Model event or device command.
    setValue(target.menu,target.slot-1,0,0);
    refreshDisplayedModelName();
  }

  // The stock IR renderer passes its row text to this copying text setter.
  // Substitute only that call's input string; physical row memory is untouched.
  const irPaintSite=address(0x401af6);
  const irPaintBytes=[0x6a,0x00,0x56,0x89,0x85,0x68,0xfe,0xff,0xff,0xe8,0xfc,0x8a,0xfa,0xff];
  if(!irPaintBytes.every((b,i)=>irPaintSite.add(i).readU8()===b))
    throw new Error('The IR text paint call does not match the reviewed build.');
  Interceptor.attach(address(0x3aa600),{
    onEnter(args){
      if(!this.returnAddress.equals(address(0x401b04)))return;
      try{
        const list=irList();
        if(list.isNull() || !this.context.edi.equals(list))return;
        const row=args[0],slot=row.add(0x100).readS32()+1;
        if(slot<1 || slot>128 || !getIrRow(list,slot-1).equals(row) || !row.add(0x10c).readU32())return;
        const raw=boundedNativeText(row),known=readbackDisplayItem(slot)
          || (slotLedger.importCurrent(slot) && profiles[slot]===raw && verifiedMetadata[slot]);
        if(!known)return;
        this.friendly=Memory.allocUtf8String(known.title);
        args[0]=this.friendly;
      }catch(_){/* Keep native text if this row cannot be verified. */}
    }
  });
'''


def patch_display_helpers(source):
    source = replace_once(source, '  function friendlyMenuLabel(slot, fallback) {',
                          NAME_HELPERS + '\n  function friendlyMenuLabel(slot, fallback) {')
    source = replace_once(source, '''    const item = verifiedMetadata[slot];
    return item ? Memory.allocUtf8String(`${slot}: ${item.title}`) : fallback;''', '''    const original = typeof fallback === 'string' ? fallback : fallback.readUtf8String();
    const match = original.match(/^[0-9]+: (.*)$/);
    if (!match) return fallback;
    const title = displayCaptureTitle(slot, match[1]);
    return title !== match[1] ? Memory.allocUtf8String(`${slot}: ${title}`) : fallback;''')
    source = replace_once(source, "  const constructing = new Map();", NATIVE_DISPLAY_HOOK + NATIVE_LIBRARY_LABELS + "\n  const constructing = new Map();")
    # syncSelection runs immediately after activeModel is assigned and when the
    # cached parameter is attached, so startup needs no knob change to relabel.
    source = replace_once(source, "  function syncSelection() {", "  function syncSelection() {\n    refreshDisplayedModelName();")
    # Calling an existing NativeFunction from our own callback may bypass its
    # Interceptor. Refresh explicitly after our own Model operation returns.
    source = replace_once(source,
        '    handleModelEvent(slider, 0x21617663, popup, NULL); // cva!\n    return true;',
        '    handleModelEvent(slider, 0x21617663, popup, NULL); // cva!\n    refreshDisplayedModelName();\n    return true;')
    source = replace_once(source,
        "    if (parts && Object.hasOwn(profiles, parts[1]) && profiles[parts[1]] === parts[2]) {",
        "    if (parts && (readbackDisplayItem(Number(parts[1])) ||\n        (Object.hasOwn(profiles, parts[1]) && profiles[parts[1]] === parts[2]))) {")
    # A verified menu can exist with no import-owned profiles. Count accepted
    # entries so the empty-library sentinel is not inserted after real entries.
    source = replace_once(source,
        '      return append(menu, index, friendlyMenuLabel(Number(parts[1]), label), id, options, disabled);',
        '      state.count++;\n      return append(menu, index, friendlyMenuLabel(Number(parts[1]), label), id, options, disabled);')
    return source
