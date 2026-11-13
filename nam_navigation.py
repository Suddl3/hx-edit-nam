"""Offline contract for a browser independent of HX Edit's selected block.

This module performs no device operations. The future native navigation adapter
must observe selection/context events, validate a ticket immediately before a
write, and acknowledge cleanup only after native readback. A timeout never grants
ownership of slot128. The existing tile-driven runtime is not wired to this yet.
"""

from dataclasses import dataclass


class NavigationBlocked(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    connection: str
    preset: str
    snapshot: int
    path: str
    position: int
    block_revision: str
    engine: str


@dataclass(frozen=True)
class PreviewTicket:
    generation: int
    target: Target
    capture: str
    slot: int = 128


@dataclass(frozen=True)
class Cleanup:
    generation: int
    preview: PreviewTicket
    reason: str
    same_context: bool


class NavigationSession:
    """Single preview owner; all windows must share this session.

    Selecting another block ends ownership of the audition target. It never binds
    the new block implicitly, even when the user returns to the original block.
    A native adapter must serialize calls on its event thread. Gain/EQ changes do
    not change Target.block_revision; replacement/deletion/recreation does.
    """

    def __init__(self, supported_engines=('HD2_AmpNAMLite',)):
        self.supported_engines = frozenset(supported_engines)
        self.selected = None
        self.bound = None
        self.pending = None
        self.preview = None
        self.cleanup = None
        self.generation = 0
        self.notice = 'Select a NAM block to audition. Browsing is available.'

    def observe_selection(self, selected):
        if selected == self.selected:
            return self.cleanup
        self.selected = selected
        self.generation += 1
        self.pending = None
        self.bound = None
        if self.preview and not self.cleanup:
            self._request_cleanup('selection-changed')
        elif self.cleanup:
            # A preset, snapshot or device change during restoration invalidates
            # its old acknowledgement. Do not release128 on an obsolete reply.
            self.cleanup = Cleanup(self.generation, self.cleanup.preview,
                                   self.cleanup.reason, self._same_context(self.cleanup.preview.target))
        else:
            self.notice = 'Choose Use selected block before auditioning.'
        return self.cleanup

    def _same_context(self, target):
        current = self.selected
        return bool(current and (current.connection, current.preset, current.snapshot) ==
                    (target.connection, target.preset, target.snapshot))

    def use_selected_block(self):
        if self.cleanup or self.preview:
            raise NavigationBlocked('Finish restoring the previous preview first.')
        if not self.selected or self.selected.engine not in self.supported_engines:
            raise NavigationBlocked('Select a compatible NAM block to audition.')
        self.generation += 1
        self.pending = None
        self.bound = self.selected
        self.notice = 'Ready to audition on the selected NAM block.'
        return self.bound

    def begin_preview(self, capture, *, compatible_engines, slot128_empty, owned_preview_verified=False):
        if self.cleanup:
            raise NavigationBlocked('The previous preview needs verified cleanup.')
        if not self.bound or self.bound != self.selected:
            raise NavigationBlocked('Choose Use selected block before auditioning.')
        if self.bound.engine not in compatible_engines:
            raise NavigationBlocked('This capture needs a different supported engine.')
        # Ownership comes from a prior successful preview, never a filename or a
        # free-slot guess. No fallback to127, another free slot or an overwrite.
        owned = bool(owned_preview_verified and self.preview is not None and self.preview.target == self.bound)
        if not slot128_empty and not owned:
            raise NavigationBlocked('Preview slot128 is occupied and is not owned by this session.')
        self.generation += 1
        self.pending = PreviewTicket(self.generation, self.bound, capture)
        self.notice = 'Preparing preview for the named target.'
        return self.pending

    def can_apply(self, ticket):
        """Check immediately before upload and again before assigning the slot."""
        return bool(isinstance(ticket, PreviewTicket) and ticket == self.pending and ticket.generation == self.generation and
                    ticket.target == self.bound == self.selected and not self.cleanup)

    def complete_preview(self, ticket, *, confirmed):
        if not self.can_apply(ticket):
            return False
        self.pending = None
        if confirmed:
            self.preview = ticket
            self.notice = 'Previewing in slot128; preset Save is separate.'
        else:
            # A failed write can leave128 modified. It is not evidence of an
            # empty slot. Keep a cleanup obligation until native readback.
            self.preview = ticket
            self._request_cleanup('preview-not-confirmed')
        return confirmed

    def _request_cleanup(self, reason):
        self.generation += 1
        self.pending = None
        self.bound = None
        if not self.preview:
            return None
        self.cleanup = Cleanup(self.generation, self.preview, reason,
                               self._same_context(self.preview.target))
        self.notice = 'Restoring the previous block; auditioning is paused.'
        return self.cleanup

    def end_preview(self):
        if self.cleanup:
            return self.cleanup
        return self._request_cleanup('end-preview')

    def complete_cleanup(self, cleanup, *, target_restored, slot_cleared):
        """Only native readback can establish both acknowledgements.

        Restore the original Model only; retain the user's current Input/EQ.
        Never use global Undo, delete the block, or restore a preset snapshot.
        If the context changed, the adapter must decline and retain128 for review.
        """
        if not isinstance(cleanup, Cleanup) or cleanup != self.cleanup or not cleanup.same_context:
            return False
        if not target_restored or not slot_cleared:
            self.notice = 'Preview retained in128; verify cleanup before another audition.'
            return False
        self.cleanup = None
        self.preview = None
        self.notice = 'Preview ended. Choose Use selected block to audition again.'
        return True
