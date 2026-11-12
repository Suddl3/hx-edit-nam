'use strict';

// HX Edit 3.82 x86. Use the native model selector, IR librarian and undo manager.
// tiles: one TONE3000 entry per NAM block a profile can go to ({label, block, modes}, nam_modes.tiles):
// each is a copy of its block's row, so HX Edit greys it exactly when that block does not fit.
function installToneView(tiles = [{ label: 'TONE3000', block: 'NAM A2 Lite', modes: ['original'] }]) {
  const method = (rva, type, args) => new NativeFunction(base.add(rva), type, ['pointer', ...args], 'thiscall');
  const win = (name, type, args) => new NativeFunction(user32.getExportByName(name), type, args, 'stdcall');
  const post = win('PostMessageW', 'int', ['pointer', 'uint', 'uint', 'uint']);
  const wake = win('RegisterWindowMessageW', 'uint', ['pointer'])(Memory.allocUtf16String('HX Edit NAM TONE3000'));
  const rowCount = method(0x379fb0, 'uint', []);
  const selectedRow = method(0x380780, 'pointer', []);
  const selectRow = method(0x3821d0, 'void', ['int', 'int', 'int', 'int']);
  const clearSelection = method(0x37f690, 'void', ['int', 'int']);
  const appendRow = method(0x380940, 'void', ['int', 'pointer', 'pointer', 'int', 'int', 'pointer', 'int', 'int']);
  const findWidget = method(0x3b7ae0, 'pointer', ['uint', 'int']);
  const widgetOrigin = method(0x3b7e00, 'pointer', ['pointer']);
  const isVisible = method(0x3ba100, 'uint8', ['int']);
  const point = Memory.alloc(8);
  const selectionEvent = 0x216c6273; // sbl!
  const tileLabels = tiles.map(tile => Memory.allocUtf8String(tile.label));
  const tileByBlock = new Map(tiles.map((tile, index) => [tile.block, index]));
  const tileByLabel = new Map(tiles.map((tile, index) => [tile.label, index]));
  // controller -> the model row selected before a tile was clicked: the block's own model
  const lastModel = new Map();
  const title = Memory.allocUtf16String('TONE3000 · HX Edit NAM');
  const eventAddress = base.add(0x217120);
  const eventTypes = ['pointer', 'uint', 'pointer', 'pointer', 'pointer'];
  const modelEvent = new NativeFunction(eventAddress, 'void', eventTypes, 'thiscall');
  let active = null;
  let nextSession = 0;
  let command = null;

  function parameterPanel(list) {
    // editView.xml puts PRVW beside the model selector in the same edit view.
    let widget = list;
    for (let depth = 0; depth < 5 && !widget.isNull(); depth++) {
      const panel = findWidget(widget, 0x50525657, 1); // PRVW
      if (!panel.isNull()) return panel;
      widget = widget.add(0x54).readPointer().add(0x14).readPointer();
    }
    return NULL;
  }

  function panelBounds(panel) {
    if (!isVisible(panel, 1)) return { visible: false };
    widgetOrigin(panel, point);
    const geometry = panel.add(0x54).readPointer();
    return { visible: true, x: point.readS32(), y: point.add(4).readS32(),
      w: geometry.add(0xc).readS32() - geometry.add(4).readS32(),
      h: geometry.add(0x10).readS32() - geometry.add(8).readS32() };
  }

  function stringValue(value) {
    const data = value.add(4).readPointer();
    return data.isNull() ? '' : data.add(0x10).readPointer().readUtf8String();
  }

  function position(controller) {
    return [controller.add(0x34).readPointer(), controller.add(0x80).readS32(),
      controller.add(0x98).readS32(), controller.add(0x9c).readS32()].join(':');
  }

  function block(controller) {
    return stringValue(controller.add(0x84));
  }

  function atPosition() {
    return active && active.valid && position(active.controller) === active.position;
  }

  function atTarget() {
    return atPosition() && block(active.controller) === active.block;
  }

  function undoIndex() {
    return active.editor.add(0x294).readS32();
  }

  function pick(list, name) {
    // select a model list row by its text, as a click would (the caller sends the selection event)
    for (let index = 0, count = rowCount(list); index < count; index++) {
      const row = getIrRow(list, index);
      if (row.isNull() || row.readUtf8String() !== name) continue;
      clearSelection(list, 0, 0);
      selectRow(list, 1, index, 0, 0);
      return true;
    }
    return false;
  }

  function insertNam() {
    // the tile's block: into an empty cell, or in place of another NAM block (HX Edit changes the model)
    const controller = active.controller;
    const list = controller.add(0x58).readPointer();
    if (list.isNull() || !pick(list, active.tile.block)) {
      throw new Error('Reopen the NAM model list to select the destination.');
    }
    modelEvent(controller, selectionEvent, list, NULL, NULL);
  }

  function dismiss() {
    // close the TONE3000 pane (TonePreview cancels; its close undoes the audition)
    if (!active || active.dismissed) return;
    active.dismissed = true;
    send({ type: 'toneDismiss', session: active.id });
  }

  function targetChanged(where, text) {
    // which check failed (trace.log; no names): the block moved or changed, or an edit happened since
    const state = { type: 'toneTrace', reason: 'target-changed', where, valid: active.valid,
      invalidatedBy: active.invalidatedBy || null, undo: undoIndex(), selectionUndo: active.selectionUndo };
    try {
      Object.assign(state, { atPosition: atPosition(), sameBlock: block(active.controller) === active.block,
        blockSet: !!active.block, blockNow: !!block(active.controller) });
    } catch (error) {
      state.readError = String(error.message);
    }
    send(state);
    return new Error(text);
  }

  function invalidate(by) {
    active.invalidatedBy = active.invalidatedBy || by;
    active.valid = false;
    dismiss();
  }

  function ensureEntry(controller) {
    const list = controller.add(0x58).readPointer();
    if (list.isNull()) return;
    const present = new Set();
    const blockRows = new Map();
    for (let index = 0, count = rowCount(list); index < count; index++) {
      const row = getIrRow(list, index);
      if (row.isNull()) continue;
      const name = row.readUtf8String();
      if (tileByLabel.has(name)) present.add(name);
      if (tileByBlock.has(name)) blockRows.set(name, row);
    }
    const selected = selectedRow(list);
    if (!selected.isNull() && !active) {    // (an audition's model switch is not the block's own model)
      const name = selected.readUtf8String();
      if (!tileByLabel.has(name)) lastModel.set(controller.toString(), name);
    }
    tiles.forEach((tile, index) => {
      const row = blockRows.get(tile.block);
      if (present.has(tile.label) || !row) return;
      appendRow(list, -1, tileLabels[index], row.add(0x104).readPointer(), 0,
        row.add(0x100).readS32(), row.add(0x10c).readPointer(), 0, 0);
    });
  }

  Interceptor.attach(base.add(0x380940), {
    onEnter(args) {
      if (!this.context.ecx.readPointer().equals(base.add(0x91fcd0)) || args[1].isNull() || args[5].isNull()) return;
      const tile = tileByBlock.get(args[1].readUtf8String());
      if (tile === undefined) return;
      this.tile = tile;
      this.list = this.context.ecx;
      this.image = args[2];
      this.index = args[4].toInt32();
      this.model = args[5];
    },
    onLeave() {
      if (this.list) appendRow(this.list, -1, tileLabels[this.tile], this.image, 0, this.index, this.model, 0, 0);
    },
  });

  function onModelEvent(controller, event, sender, data, flags) {
    if (event !== selectionEvent || sender.isNull() || !sender.equals(controller.add(0x58).readPointer())) {
      modelEvent(controller, event, sender, data, flags);
      if (event === selectionEvent || event === 0x21617663) ensureEntry(controller);
      return;
    }
    const row = selectedRow(sender);
    const name = row.isNull() ? null : row.readUtf8String();
    const tileIndex = name === null ? undefined : tileByLabel.get(name);
    if (active && name !== null && (tileIndex !== undefined || active.controller.equals(controller))) {
      // Another model or tile while TONE3000 is open: close it first (undoing the audition), then pick that row.
      clearSelection(sender, 0, 0);
      active.pending = { controller, list: sender, name };
      dismiss();
      return;
    }
    if (tileIndex === undefined) {
      if (name !== null) lastModel.set(controller.toString(), name);   // the model the user picked
      return modelEvent(controller, event, sender, data, flags);
    }
    const tile = tiles[tileIndex];
    clearSelection(sender, 0, 0);
    const window = getActiveWindow();
    const editor = controller.add(0x34).readPointer();
    if (window.isNull() || editor.isNull() || !editor.readPointer().add(0xdc).readPointer().equals(base.add(0x51aaf0))) return;
    const panel = parameterPanel(sender);
    const bounds = panel.isNull() ? null : panelBounds(panel);
    if (!bounds || !bounds.visible || bounds.w < 60 || bounds.h < 60) {
      messageBox(window, Memory.allocUtf16String('The TONE3000 pane cannot fit in the current editor view.'), title, 0x40);
      return;
    }
    active = { id: ++nextSession, controller, editor, position: position(controller),
      block: block(controller), window, valid: true, panel, layout: JSON.stringify(bounds),
      tile, model: lastModel.has(controller.toString()) ? lastModel.get(controller.toString()) : null };
    active.selectionUndo = undoIndex();
    send({ type: 'toneTrace', reason: 'tile', tile: tile.block, model: active.model, block: active.block });
    // the block's profile name: a TONE3000 one ends with its model ID, which TonePreview opens the tone of
    send({ type: 'toneStart', session: active.id, window: window.toUInt32(), bounds, tile,
      profile: namView.slotName(namView.modelSlot()) });
  }
  Interceptor.replace(eventAddress, new NativeCallback(onModelEvent, 'void', eventTypes, 'thiscall'));

  function resume(pending) {
    // the row clicked while TONE3000 was open, now that it has closed
    try {
      if (!pending.list.equals(pending.controller.add(0x58).readPointer()) || !pick(pending.list, pending.name)) return;
      onModelEvent(pending.controller, selectionEvent, pending.list, NULL, NULL);
    } catch (error) {
      send({ type: 'toneTrace', reason: 'resume', error: String(error.message) });
    }
  }

  Interceptor.attach(base.add(0x216e00), {
    onEnter() {
      if (active && active.controller.equals(this.context.ecx)) {
        active.panel = NULL;
        invalidate('view-closed');
      }
    },
  });

  Interceptor.attach(base.add(0x218a00), {
    onEnter(args) {
      this.ours = active && active.controller.equals(this.context.ecx);
      if (!this.ours) return;
      const event = this.event = stringValue(args[0]);
      if (event === 'EditBuffer_PresetSavedToDevice') active.deviceSaved = true;
      if (/^EditBuffer_(?:TargetDeviceChanged|DevicePresetChanged|PresetChanged|HelixSnapshotWillChange)$/.test(event)) {
        invalidate(event);
      }
    },
    onLeave() {
      // (Not while inserting or restoring a block: the cell's position changes when it fills or empties.)
      if (this.ours && active && !active.applying && !active.inserting && !active.restoring && !atTarget()) {
        invalidate(`moved after ${this.event}`);
      }
    },
  });

  function reply(request, result) {
    send({ type: 'toneReply', id: request.id, ...result });
  }

  function finishTransfer(transfer) {
    if (toneImport !== transfer) return;
    toneImport = null;
    if (namImportController.equals(transfer.controller)) namImportController = NULL;
  }

  function poll(request, next, timeout = 12000) {
    const deadline = Date.now() + timeout;
    function check() {
      if (!active || request.session !== active.id) return;
      command = { request, next, deadline, check };
      if (!post(active.window, wake, 0, 0)) {
        command = null;
        reply(request, { error: 'HX Edit closed during the TONE3000 operation.' });
      }
    }
    check();
  }

  function awaitImport(request, transfer, imported) {
    poll(request, expired => {
      if (transfer.error) {
        finishTransfer(transfer);
        throw new Error(transfer.error);
      }
      if (!namView.previewName(transfer)) {
        if (!expired && (request.action === 'save' || atTarget())) return null;
        finishTransfer(transfer);
        throw new Error(`HX Edit did not confirm the import. Check User IR ${transfer.slot} before retrying.`);
      }
      finishTransfer(transfer);
      imported();
      return {};
    });
  }

  function applyProfile(request, transfer) {
    // Put an imported audition profile on the block. The first one records the block's own state
    // (active.origin) for Close and, on an empty cell or another NAM model, inserts the tile's block.
    // Later ones only move the block to their slot: nothing is undone between tones, so an inserted
    // block stays. The previous audition's slot is then cleared. (No Undo-count checks: HX Edit adds
    // Undo steps of its own a moment after a Model change.)
    if (!atTarget()) {
      throw targetChanged('apply', `The target changed. The downloaded profile is in User IR ${transfer.slot}.`);
    }
    const previous = active.transfer;
    active.transfer = transfer;
    if (!active.origin) {
      const slot = namView.modelSlot();
      // An empty cell, or a NAM block known to be another model than the tile's. Unknown: left as it is
      // (re-selecting the same model makes HX Edit add an Undo step later).
      active.origin = { undo: undoIndex(), slot, block: active.block, position: active.position,
        structural: slot === null || (active.model !== null && active.model !== active.tile.block) };
      active.inserting = active.origin.structural;
    }
    const undoBefore = undoIndex();
    const deadline = Date.now() + 1500;
    if (active.inserting) insertNam();
    let selecting = false;
    let expectedUndo;
    poll(request, expired => {
      if (!(active.inserting ? active.valid : atTarget())) {
        throw targetChanged('applying', `The target changed. The downloaded profile is in User IR ${transfer.slot}.`);
      }
      if (namView.modelSlot() === null && !expired) return null;
      if (active.inserting) {
        // Inserting into an empty cell gives it a block ID after the native edit. A NAM block that already
        // was the tile's model records nothing: accepted once the deadline passes.
        const same = active.origin.slot !== null && Date.now() >= deadline;
        if (undoIndex() === undoBefore && !expired && !same) return null;
        if (undoIndex() === undoBefore && !same) {
          throw new Error(`HX Edit did not confirm the new block. Check User IR ${transfer.slot}.`);
        }
        active.block = block(active.controller);
        active.position = position(active.controller);   // a filled cell has a new position
        active.inserting = false;
      }
      if (!selecting) {
        expectedUndo = undoIndex() + Number(namView.modelSlot() !== transfer.slot);
        selecting = namView.playPreview(transfer);
      }
      // HX Edit can update the Model widget before recording the edit for Undo.
      if (!selecting || namView.modelSlot() !== transfer.slot || undoIndex() < expectedUndo) {
        if (!expired) return null;
        throw new Error(`Could not apply the profile. Check User IR ${transfer.slot} on Helix.`);
      }
      if (previous && previous !== transfer) namView.clearPreview(previous);
      active.model = active.tile.block;      // the block is the tile's model now
      active.undoAfter = undoIndex();
      return { slot: transfer.slot, freeSlots: namView.freeSlots(transfer.slot) };
    }, 3500);
  }

  function playing(transfer) {
    return transfer && atTarget() && active.undoAfter !== undefined &&
      namView.modelSlot() === transfer.slot && !!namView.previewName(transfer);
  }

  function restore(request) {
    // Close: the block back to active.origin, then the audition slot cleared. A slot change alone is set
    // back as a new edit; an inserted block or a model switch is undone back to origin.undo.
    const { transfer, editor, origin } = active;
    if (!transfer || !origin) return afterRestore(request, {});
    // The block could not be brought back: no message (trace.log has why). The audition slot is cleared
    // when the block does not play it; otherwise it stays, as the block's profile.
    const keep = reason => {
      const state = { type: 'toneTrace', reason, action: request.action,
        origin: { undo: origin.undo, slot: origin.slot, structural: origin.structural }, slot: transfer.slot,
        valid: active.valid, invalidatedBy: active.invalidatedBy || null, deviceSaved: !!active.deviceSaved };
      try {
        Object.assign(state, { undo: undoIndex(), modelSlot: namView.modelSlot(),
          samePosition: position(active.controller) === active.position,
          originPosition: position(active.controller) === origin.position,
          sameBlock: block(active.controller) === active.block, originBlock: block(active.controller) === origin.block,
          preview: !!namView.previewName(transfer) });
        if (namView.modelSlot() !== transfer.slot) namView.clearPreview(transfer);
      } catch (error) {
        state.readError = String(error.message);
      }
      send(state);
      return afterRestore(request, {});
    };
    const clearAudition = () => {
      if (!namView.clearPreview(transfer)) return keep('clear-refused');
      namView.stopPreview();
      poll(request, expired => {
        if (namView.previewSlotEmpty(transfer)) {
          active.transfer = null;
          return afterRestore(request, {});
        }
        return expired ? afterRestore(request, {}) : null;
      }, 3500);
      return {};
    };
    if (!playing(transfer) || active.deviceSaved) return keep('not-restorable');
    active.restoring = true;
    if (!origin.structural) {
      if (!namView.setSlot(origin.slot)) return keep('slot-refused');
    } else {
      const undo = new NativeFunction(editor.readPointer().add(0xdc).readPointer(), 'int', ['pointer'], 'thiscall');
      active.applying = true;
      try {
        for (let step = 0; step < 8 && undoIndex() > origin.undo; step++) {
          if (undo(editor) < 0) break;
        }
      } finally {
        active.applying = false;
      }
      if (undoIndex() !== origin.undo) return keep('undo-failed');
    }
    poll(request, expired => {
      if (!active.valid) return keep('moved-after-undo');
      if (namView.modelSlot() !== origin.slot || block(active.controller) !== origin.block) {
        return expired ? keep('original-not-back') : null;
      }
      active.block = origin.block;
      active.position = position(active.controller);    // an emptied cell is back at its old position
      active.restoring = false;
      return clearAudition();
    }, 3500);
  }

  const commandAddress = base.add(0x35e800);
  const dispatch = new NativeFunction(commandAddress, 'uint8', ['pointer', 'pointer'], 'thiscall');
  Interceptor.replace(commandAddress, new NativeCallback(function (app, event) {
    if (active && active.transfer && !event.isNull() && event.readU32() === 0x1b) {
      messageBox(active.window, Memory.allocUtf16String(
        'Use Save to Helix in TONE3000 before saving a preset with an audition profile.'), title, 0x40);
      return 0;
    }
    return dispatch(app, event);
  }, 'uint8', ['pointer', 'pointer'], 'thiscall'));

  function afterRestore(request, result) {
    // Close finished (or could not bring the block back, see keep): end the session.
    active.restoring = false;
    namView.stopPreview();
    const pending = active.pending;
    active = null;
    reply(request, result);
    if (pending) resume(pending);
    return {};
  }

  function audition(request) {
    // A free User IR slot (128 first); none free: the slot the user chose to replace (request.replace),
    // cleared first, or an error listing the taken slots so TonePreview can ask.
    const load = slot => {
      active.previewSlot = slot;
      const transfer = namView.importPreview(request.file, slot, true);
      awaitImport(request, transfer, () => applyProfile(request, transfer));
    };
    const free = namView.auditionSlot();
    if (free) return load(free);
    const slot = request.replace;
    if (!Number.isInteger(slot) || slot < 1 || slot > AUDITION_SLOT) {
      const error = new Error('No free User IR slot. Choose one to replace.');
      error.replace = namView.usedSlots();
      throw error;
    }
    if (!namView.clearSlot(slot)) throw new Error(`Could not clear User IR ${slot}.`);
    poll(request, expired => {
      if (!namView.slotEmpty(slot)) {
        if (!expired) return null;
        throw new Error(`Could not clear User IR ${slot}.`);
      }
      load(slot);
      return {};
    }, 3500);
  }

  function keepSaved(request, trial, saved) {
    // Save keeps what the audition did to the block (inserted it, switched its model) and only moves it
    // from the audition slot to the saved one. No Undo: undoing the audition first could take the block
    // back out, and the re-insert after it could fail. Then the audition slot is cleared.
    const expected = undoIndex() + 1;
    if (!namView.selectProfile(saved)) throw new Error(`Check User IR ${saved.slot} on Helix.`);
    poll(request, expired => {
      // HX Edit can update the Model widget before recording the edit for Undo.
      if (namView.modelSlot() !== saved.slot || undoIndex() < expected) {
        if (!expired) return null;
        throw new Error(`Could not switch the block to User IR ${saved.slot}. Check it on Helix.`);
      }
      namView.stopPreview();
      namView.clearPreview(trial);
      active.transfer = null;
      active.origin = null;                    // the saved profile is what Close keeps from now on
      active.undoAfter = undefined;
      active.model = active.tile.block;        // the block keeps the tile's model now
      active.selectionUndo = undoIndex();
      return { savedSlot: saved.slot, freeSlots: namView.freeSlots(trial.slot) };
    }, 3500);
  }

  function run(request) {
    if (request.action === 'save') {
      const trial = active.transfer;
      if (!playing(trial) || active.deviceSaved) {
        throw new Error('The audition changed. Reopen TONE3000 on the desired block.');
      }
      const { slot } = request;
      if (!Number.isInteger(slot) || slot < 1 || slot > AUDITION_SLOT || slot === active.previewSlot) {
        throw new Error('Select a different free User IR slot.');
      }
      const saved = namView.importPreview(trial.source, slot);
      awaitImport(request, saved, () => {
        if (!namView.registerProfile(saved)) throw new Error(`Check User IR ${slot} on Helix.`);
        request.savedSlot = slot;
        keepSaved(request, trial, saved);
      });
      return;
    }
    if (request.action === 'audition') {
      if (!atTarget()) throw targetChanged('run', 'The target changed. Select TONE3000 again on the desired block.');
      return audition(request);         // the next tone replaces the current audition: nothing is undone
    }
    restore(request);                   // close
  }

  Interceptor.attach(user32.getExportByName('DispatchMessageW'), {
    onEnter(args) {
      if (!active || !args[0].readPointer().equals(active.window)) return;
      const event = args[0].add(4).readU32();
      if (event === 5 || event === 0xf || event === 0x47 || event === 0x202 || event === wake) {
        this.layoutSession = active.id;
      }
      if (!command || event !== wake) return;
      const { request, next, deadline, check } = command;
      command = null;
      try {
        if (!next) return run(request);
        const result = next(Date.now() >= deadline);
        if (result === null) setTimeout(check, 75);
        else if ('slot' in result || 'savedSlot' in result) reply(request, result);
      } catch (error) {
        const result = { error: String(error.message) };
        if (error.replace) result.replace = error.replace;
        if (request.action !== 'close') {
          const transfer = active.transfer;
          if (playing(transfer)) result.slot = transfer.slot;
          result.freeSlots = namView.freeSlots(active.previewSlot);
          if (request.savedSlot) result.savedSlot = request.savedSlot;
        }
        reply(request, result);
      }
    },
    onLeave() {
      if (!active || active.id !== this.layoutSession) return;
      const bounds = active.panel.isNull() ? { visible: false } : panelBounds(active.panel);
      const layout = JSON.stringify(bounds);
      if (layout === active.layout) return;
      active.layout = layout;
      send({ type: 'toneBounds', session: active.id, bounds });
    },
  });

  function listen() {
    recv('toneCommand', message => {
      const request = message.payload;
      if (active && active.id === request.session && !command) {
        command = { request };
        if (!post(active.window, wake, 0, 0)) {
          command = null;
          reply(request, { error: 'The HX Edit window is unavailable.' });
        }
      } else {
        reply(request, { error: 'This TONE3000 selection is no longer active.' });
      }
      listen();
    });
  }
  listen();
}
