'use strict';

// Stock uint vector at the deletion-queue boundary. Return one-based slots.
// Bound every read before touching native array storage; never truncate a batch.
function readClearSlotSelection(vector, expectedVtable) {
  if (vector.isNull() || !vector.readPointer().equals(expectedVtable))
    throw new Error('Unknown Clear selection');
  const capacity = vector.add(12).readU32();
  const begin = vector.add(16).readU32(), end = vector.add(20).readU32();
  if (begin > end || end > capacity || capacity > 4096 || end - begin < 1 || end - begin > 128)
    throw new Error('Invalid Clear selection bounds');
  const data = vector.add(8).readPointer();
  if (data.isNull()) throw new Error('Missing Clear selection');
  const slots = [];
  for (let index = begin; index < end; index++) {
    const value = data.add(index * 4).readU32();
    if (value >= 128) throw new Error('Invalid Clear slot');
    slots.push(value + 1);
  }
  if (new Set(slots).size !== slots.length) throw new Error('Repeated Clear slot');
  return slots;
}
