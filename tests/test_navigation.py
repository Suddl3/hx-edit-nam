"""Target changes, reconnects and late results without UI, USB or user settings."""
from dataclasses import replace
import unittest

from nam_navigation import NavigationBlocked, NavigationSession, Target


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.nav = NavigationSession()
        self.a = Target('connection-1', 'demo-preset', 0, '1A', 2, 'block-a', 'HD2_AmpNAMLite')
        self.b = replace(self.a, position=5, block_revision='block-b')

    def bind(self, target=None):
        self.nav.observe_selection(target or self.a)
        self.nav.use_selected_block()

    def start(self, name='Tweed', empty=True, owned=False):
        return self.nav.begin_preview(name, compatible_engines=('HD2_AmpNAMLite',),
                                      slot128_empty=empty, owned_preview_verified=owned)

    def preview(self):
        self.bind()
        ticket = self.start()
        self.assertTrue(self.nav.complete_preview(ticket, confirmed=True))
        return ticket

    def test_browse_without_target_does_not_allow_audition(self):
        with self.assertRaises(NavigationBlocked):
            self.start()

    def test_selection_requires_explicit_binding(self):
        self.nav.observe_selection(self.a)
        with self.assertRaises(NavigationBlocked):
            self.start()
        self.nav.use_selected_block()
        self.assertTrue(self.nav.can_apply(self.start()))

    def test_stock_and_unsupported_engine_cannot_bind(self):
        for engine in ('HD2_AmpStock', 'HD2_AmpNAMPedal'):
            self.nav.observe_selection(replace(self.a, engine=engine))
            with self.assertRaises(NavigationBlocked):
                self.nav.use_selected_block()

    def test_capture_architecture_must_match_target(self):
        self.bind()
        with self.assertRaises(NavigationBlocked):
            self.nav.begin_preview('Pedal', compatible_engines=('HD2_AmpNAMPedal',), slot128_empty=True)

    def test_no_fallback_when128_is_unowned(self):
        self.bind()
        with self.assertRaises(NavigationBlocked):
            self.start(empty=False)

    def test_repeat_preview_reuses_owned128(self):
        self.preview()
        second = self.start('British', empty=False, owned=True)
        self.assertEqual(second.slot, 128)
        self.assertTrue(self.nav.complete_preview(second, confirmed=True))

    def test_stale_preview_ownership_does_not_allow_overwrite(self):
        self.preview()
        with self.assertRaises(NavigationBlocked):
            self.start('New', empty=False, owned=False)

    def test_missing_acknowledgements_are_rejected(self):
        self.assertFalse(self.nav.can_apply(None))
        self.assertFalse(self.nav.complete_cleanup(None, target_restored=True, slot_cleared=True))

    def test_superseded_download_cannot_apply(self):
        self.bind()
        first = self.start('Slow download')
        second = self.start('New choice')
        self.assertFalse(self.nav.can_apply(first))
        self.assertFalse(self.nav.complete_preview(first, confirmed=True))
        self.assertTrue(self.nav.can_apply(second))

    def test_change_target_during_download_revokes_result(self):
        self.bind()
        ticket = self.start()
        self.nav.observe_selection(self.b)
        self.assertFalse(self.nav.can_apply(ticket))
        self.assertIsNone(self.nav.bound)

    def test_old_preview_cleanup_precedes_binding_new_target(self):
        ticket = self.preview()
        cleanup = self.nav.observe_selection(self.b)
        self.assertEqual(cleanup.preview, ticket)
        with self.assertRaises(NavigationBlocked):
            self.nav.use_selected_block()
        self.assertTrue(self.nav.complete_cleanup(cleanup, target_restored=True, slot_cleared=True))
        self.assertIsNone(self.nav.bound)
        with self.assertRaises(NavigationBlocked):
            self.start()
        self.assertEqual(self.nav.use_selected_block(), self.b)

    def test_change_back_still_requires_explicit_binding(self):
        self.bind()
        self.nav.observe_selection(self.b)
        self.nav.observe_selection(self.a)
        with self.assertRaises(NavigationBlocked):
            self.start()

    def test_knob_observation_does_not_end_preview(self):
        ticket = self.preview()
        self.assertIsNone(self.nav.observe_selection(self.a))
        self.assertEqual(self.nav.preview, ticket)

    def test_recreated_block_same_position_invalidates_binding(self):
        self.bind()
        ticket = self.start()
        self.nav.observe_selection(replace(self.a, block_revision='replacement'))
        self.assertFalse(self.nav.can_apply(ticket))

    def test_disconnect_preset_and_snapshot_change_reject_old_cleanup(self):
        for target in (None, replace(self.a, connection='connection-2'),
                       replace(self.a, preset='different'), replace(self.a, snapshot=1)):
            self.setUp()
            self.preview()
            cleanup = self.nav.observe_selection(target)
            self.assertFalse(cleanup.same_context)
            self.assertFalse(self.nav.complete_cleanup(cleanup, target_restored=True, slot_cleared=True))
            self.assertIsNotNone(self.nav.preview)

    def test_late_cleanup_cannot_release_new_generation(self):
        self.preview()
        old = self.nav.observe_selection(self.b)
        new = self.nav.observe_selection(replace(self.b, snapshot=1))
        self.assertNotEqual(old, new)
        self.assertFalse(self.nav.complete_cleanup(old, target_restored=True, slot_cleared=True))

    def test_partial_cleanup_keeps_preview_reserved(self):
        self.preview()
        cleanup = self.nav.end_preview()
        for restored, cleared in ((True, False), (False, True), (False, False)):
            self.assertFalse(self.nav.complete_cleanup(cleanup, target_restored=restored, slot_cleared=cleared))
            with self.assertRaises(NavigationBlocked):
                self.nav.use_selected_block()

    def test_unconfirmed_write_requires_cleanup(self):
        self.bind()
        ticket = self.start()
        self.assertFalse(self.nav.complete_preview(ticket, confirmed=False))
        self.assertIsNotNone(self.nav.cleanup)
        with self.assertRaises(NavigationBlocked):
            self.start()

    def test_cancel_during_download_invalidates_ticket(self):
        self.bind()
        ticket = self.start()
        self.nav.end_preview()
        self.assertFalse(self.nav.can_apply(ticket))


if __name__ == '__main__':
    unittest.main()
