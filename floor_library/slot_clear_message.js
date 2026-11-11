'use strict';

function slotClearBlockedMessage(decision) {
  if (decision.reason === 'submission-unconfirmed')
    return 'The editor could not confirm the Clear request. Check the IR list before trying again.';
  if (decision.reason === 'used-by-current-preset' && decision.slots.length) {
    const label=decision.slots.length===1?'IR slot':'IR slots';
    return `${label} ${decision.slots.join(', ')} ${decision.slots.length===1?'is':'are'} used by the current preset.\n\n`+
      'Switch to a preset that does not use these slots, then try Clear again.\n\nNothing was deleted.';
  }
  if (decision.reason === 'preset-changed')
    return 'The current preset changed while checking its IR slots.\n\nWait for the preset to finish loading, then try Clear again. Nothing was deleted.';
  return 'Clear is blocked because the current preset\'s IR references could not be verified.\n\nNothing was deleted. Try a fully loaded stock preset without IR blocks.';
}
