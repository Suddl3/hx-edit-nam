"""Fail-closed import diagnostics. Never substitute or retry a rejected batch."""
from audition_diagnostics import replace_once


GUARD = r'''
function writeToneImportFile(buffer, capacity, file) {
  if (buffer.isNull() || typeof file !== 'string' || file.includes('\0') || file.length + 2 > capacity) {
    throw new Error('The TONE3000 file path does not fit the import dialog.');
  }
  // Frida stops writeUtf16String at the first embedded NUL. HX Edit's
  // OFN_ALLOWMULTISELECT parser scans for a second UTF16 NUL, even for one file.
  buffer.writeUtf16String(file);
  buffer.add((file.length + 1) * 2).writeU16(0);
}
function checkToneImport(files, transfer) {
  const count = files.length;
  const supplied = transfer.supplied === true;
  const exact = count === 1 && typeof files[0] === 'string' &&
    files[0].toLowerCase() === transfer.file.toLowerCase();
  const reason = count !== 1 ? 'file-count' : !supplied ? 'dialog-not-supplied' :
    !exact ? 'file-path-mismatch' : 'accepted';
  // Deliberately exclude filenames, account data and filesystem paths.
  send({type: 'toneTrace', phase: 'import-batch-check', slot: transfer.slot,
    reason, fileCount: count, dialogSupplied: supplied, pathMatches: exact});
  if (reason !== 'accepted') {
    throw new Error('TONE3000 import stopped (' + reason +
      '). No replacement file was transferred. Close the audition before retrying.');
  }
}
'''


def patch_import(source):
    source = replace_once(source, "    buffer.writeUtf16String(file + '\\0');",
                          '    writeToneImportFile(buffer, capacity, file);')
    source = replace_once(source, '  for (let slot = 0; slot < 128; slot++) {',
                          '  for (let slot = 0; slot < 127; slot++) {')
    old = """    if (namOnly && toneImport && (files.length !== 1 || !toneImport.supplied ||
        files[0].toLowerCase() !== toneImport.file.toLowerCase())) {
      throw new Error('HX Edit returned a different file for the TONE3000 import.');
    }"""
    source = replace_once(source, old, '''    if (first + files.length > 127 && !(namOnly && toneImport && toneImport.slot === 128 && files.length === 1 && first === 127)) {
      throw new Error('Slot 128 is reserved for TONE3000 previews. Choose permanent slots 1–127.');
    }
    if (namOnly && toneImport) checkToneImport(files, toneImport);''')
    source = replace_once(source, "'use strict';", "'use strict';\n" + GUARD)
    return replace_once(source,
        "    const { profiles } = requestImport({ type: 'validateImport', files, firstSlot: first + 1, occupied, namOnly });",
        """    const { profiles, libraryProfiles = {} } = requestImport({ type: 'validateImport', files, firstSlot: first + 1, occupied, namOnly });
    namView.trackLibraryImport(first + 1, files.length, libraryProfiles);""")
