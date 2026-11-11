// Exact source method from the tested composed view; no native data.
const fixture = {
    trackLibraryImport(first, count, metadata) {
      for (let slot = first; slot < first + count; slot++) {
        delete verifiedMetadata[slot];
        slotLedger.forget(slot); // A confirmed replacement supersedes old readback, including IR-only imports.
      }
      for (const [slot, item] of Object.entries(metadata)) {
        verifiedMetadata[slot] = item;
        slotLedger.imported(Number(slot));
      }
      mergeLibrary(Object.values(metadata).filter(item => item.kept !== false));
      send({type: 'toneTrace', phase: 'library-import-metadata', metadataCount: Object.keys(metadata).length});
    },
};
