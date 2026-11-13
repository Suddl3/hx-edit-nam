"""Offline presentation/IPC checks. No editor, WebView, network or USB is opened."""

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import struct
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tone_browser import ROOT, SDK_VERSION, ToneBrowser, browser_command, write_browser_icon


class BrowserWindowTests(unittest.TestCase):
    def test_legacy_call_remains_embedded_and_floating_is_explicit(self):
        bounds = dict(x=12, y=34, w=400, h=300)
        args = browser_command('helper.exe', 123, 'profile with spaces', bounds)
        self.assertEqual(args, ['helper.exe', '123', 'profile with spaces', '12', '34', '400', '300', 'embedded'])
        self.assertEqual(browser_command('helper.exe', 123, 'profile', bounds, 'floating')[-1], 'floating')

    def test_unknown_mode_rejected_before_build_profile_or_process(self):
        with patch('tone_browser.prepare_browser') as prepare:
            for mode in ('popout', '', None, 'FLOATING', 'embedded --other'):
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    ToneBrowser(123, {}, Event(), mode=mode)
            prepare.assert_not_called()

    def fake_browser(self, mode):
        browser = ToneBrowser.__new__(ToneBrowser)
        browser.window_mode = mode
        browser.detached = Event()
        browser.process = SimpleNamespace(stdin=io.StringIO())
        browser._initialize_io()
        return browser

    def test_floating_retains_new_dock_geometry_without_restarting_the_browser(self):
        browser = self.fake_browser('floating')
        self.assertTrue(browser.send('bounds', visible=False))
        self.assertTrue(browser.send('bounds', visible=True, x=-200, y=900, w=1, h=1))
        self.assertTrue(browser.send('status', text='Select a NAM block'))
        self.assertTrue(browser.send('presentation', mode='embedded', focus=True))
        self.assertTrue(browser.send('close'))
        self.assertEqual([json.loads(line)['type'] for line, _ in browser._commands.queue],
                         ['bounds', 'bounds', 'status', 'presentation', 'close'])

    def test_presentation_events_update_mode_without_enqueuing_a_preview_action(self):
        browser = self.fake_browser('embedded')
        browser.process.stdout = io.StringIO('{"type":"presentation","mode":"floating"}\n')
        browser.read_events()
        self.assertEqual(browser.window_mode, 'floating')
        self.assertTrue(browser.actions.empty())
        self.assertIsNone(browser.error)

    def test_explicit_close_reason_survives_stdout_end(self):
        for reason in ('escape', 'close-button', 'window-close'):
            with self.subTest(reason=reason):
                browser = self.fake_browser('embedded')
                browser.process.stdout = io.StringIO('{"type":"action","value":{"close":"' + reason + '"}}\n')
                browser.read_events()
                self.assertEqual(browser.actions.get_nowait(), {'close':reason})
                self.assertEqual(browser.close_reason,reason)
                self.assertIsNone(browser.error)

    def test_unexpected_helper_exit_reports_disconnect_not_successful_cleanup(self):
        browser = self.fake_browser('embedded')
        browser.process.stdout = io.StringIO('')
        browser.read_events()
        self.assertEqual(browser.close_reason, 'helper-disconnected')
        self.assertTrue(browser.closed.is_set())

    def test_unknown_presentation_command_is_rejected_before_enqueue(self):
        browser = self.fake_browser('embedded')
        with self.assertRaises(ValueError):
            browser.send('presentation', mode='unknown')
        self.assertTrue(browser._commands.empty())

    def test_stock_icon_is_copied_from_local_resources_without_executing_the_image(self):
        source = ROOT / 'build' / 'HX Edit' / 'HX Edit.exe'
        if not source.is_file():
            self.skipTest('Local stock editor fixture unavailable')
        with tempfile.TemporaryDirectory(prefix='nam-icon-') as folder:
            icon = write_browser_icon(source, Path(folder) / 'ToneBrowser.ico')
            data = icon.read_bytes()
            reserved, kind, count = struct.unpack_from('<HHH', data)
            self.assertEqual((reserved, kind), (0, 1))
            self.assertGreater(count, 0)
            for index in range(count):
                size, offset = struct.unpack_from('<II', data, 6 + index * 16 + 8)
                self.assertGreater(size, 0)
                self.assertGreaterEqual(offset, 6 + count * 16)
                self.assertLessEqual(offset + size, len(data))
            malformed = Path(folder) / 'bad.exe'
            malformed.write_bytes(b'not an executable')
            with self.assertRaises(ValueError):
                write_browser_icon(malformed, Path(folder) / 'bad.ico')

    def test_embedded_still_receives_parameter_panel_bounds(self):
        browser = self.fake_browser('embedded')
        self.assertTrue(browser.send('bounds', visible=True, x=12, y=34, w=400, h=300))
        self.assertEqual(json.loads(browser._commands.get_nowait()[0]),
                         dict(type='bounds', visible=True, x=12, y=34, w=400, h=300))

    def test_parent_detach_prevents_all_commands(self):
        browser = self.fake_browser('floating')
        browser.detached.set()
        self.assertFalse(browser.send('bounds', visible=False))
        self.assertFalse(browser.send('status', text='Ready'))
        self.assertEqual(browser.process.stdin.getvalue(), '')

    @unittest.skipUnless(os.name == 'nt', 'Windows C# compiler/SDK check')
    def test_csharp_helper_compiles_and_pure_layout_checks_run_without_a_form(self):
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
        sdk = ROOT / 'build' / f'tone-browser-{SDK_VERSION}'
        assemblies = [sdk / f'Microsoft.Web.WebView2.{name}.dll' for name in ('Core', 'WinForms')]
        if not compiler.is_file() or not all(path.is_file() for path in assemblies):
            self.skipTest('Requires locally cached WebView2 SDK; this test does not download it')
        harness = r'''
using System;
using System.Collections.Generic;
using System.Drawing;
class BrowserWindowChecks {
    static void Check(bool ok) { if (!ok) throw new Exception("window contract failed"); }
    static void Main() {
        Check(ToneBrowser.ParseWindowMode("embedded") == ToneBrowser.WindowMode.Embedded);
        Check(ToneBrowser.ParseWindowMode("floating") == ToneBrowser.WindowMode.Floating);
        foreach (var value in new uint[] { 1, 0x7fffffff, 0x80000000, 0xffffffff }) {
            var handle = ToneBrowser.ParseWindowHandle(value.ToString());
            Check(unchecked((uint)handle.ToInt64()) == value);
        }
        foreach (var value in new[] { "0", "-1", "4294967296", "not-a-window" }) {
            bool rejected = false;
            try { ToneBrowser.ParseWindowHandle(value); } catch (ArgumentException) { rejected = true; }
            Check(rejected);
        }
        foreach (var mode in new[] { "", "FLOATING", "popout", null }) {
            bool rejected = false;
            try { ToneBrowser.ParseWindowMode(mode); } catch (ArgumentException) { rejected = true; }
            Check(rejected);
        }
        foreach (var area in new[] { new Rectangle(0,0,1920,1080), new Rectangle(-1920,-100,1920,1080),
            new Rectangle(100,100,640,480), new Rectangle(0,0,1,1) }) {
            var box = ToneBrowser.FloatingBounds(area);
            Check(area.Contains(box) && box.Width > 0 && box.Height > 0);
            Check(box.Width <= 1100 && box.Height <= 780);
        }
        Check(ToneBrowser.DetailHeight(700, true, false) == 700);
        Check(ToneBrowser.DetailHeight(700, false, true) == 150);
        Check(ToneBrowser.DetailHeight(700, false, false) == 48);
        Check(ToneBrowser.DetailHeight(20, false, true) == 20);
        Check(ToneBrowser.DetailHeight(-1, false, true) == 0);
        using (var iconData = typeof(ToneBrowser).Assembly.GetManifestResourceStream("ToneBrowser.ico")) {
            if (iconData != null) {
                using (var icon = new Icon(iconData)) Check(icon.Width > 0 && icon.Height > 0);
            }
        }
        var target = new Dictionary<string,object>();
        Check(ToneBrowser.TargetText(target) == "Select a NAM block in HX Edit");
        target["selected"] = "NAM A";
        Check(ToneBrowser.TargetText(target) == "Selected: NAM A");
        target["bound"] = true; target["label"] = "NAM B"; target["text"] = "Ready";
        Check(ToneBrowser.TargetText(target) == "Selected: NAM B");
        target["bound"] = false; target["quarantined"] = true;
        target["text"] = "Preview remains in User IR 128";
        Check(ToneBrowser.TargetText(target).Contains("Preview retained"));
        Check(!ToneBrowser.TargetText(target).Contains("Preview ended"));
        // Actual presentation controller runs against native-operation fakes.
        // Both modes retain the same pane; no WebView creation, navigation,
        // preview cancellation, or command to the device occurs here.
        var calls = new List<string>();
        bool canDock = true, failAttach = false;
        var presentation = new ToneBrowser.PresentationController(() => canDock,
            mode => { if (failAttach) throw new Exception("refused"); calls.Add("attach:" + mode); },
            mode => calls.Add("layout:" + mode), mode => calls.Add("notify:" + mode),
            mode => calls.Add("focus:" + mode));
        Check(presentation.Mode == ToneBrowser.WindowMode.Embedded);
        Check(presentation.SwitchTo(ToneBrowser.WindowMode.Floating, true));
        Check(String.Join(",", calls) == "attach:Floating,layout:Floating,notify:Floating,focus:Floating");
        calls.Clear();
        Check(presentation.SwitchTo(ToneBrowser.WindowMode.Floating, true));
        Check(String.Join(",", calls) == "layout:Floating,notify:Floating,focus:Floating");
        calls.Clear(); canDock = false;
        Check(!presentation.SwitchTo(ToneBrowser.WindowMode.Embedded, true));
        Check(presentation.Mode == ToneBrowser.WindowMode.Floating && calls.Count == 0);
        canDock = true; failAttach = true;
        try { presentation.SwitchTo(ToneBrowser.WindowMode.Embedded, true); } catch (Exception) { }
        Check(presentation.Mode == ToneBrowser.WindowMode.Floating && calls.Count == 0);
        failAttach = false;
        Check(presentation.SwitchTo(ToneBrowser.WindowMode.Embedded, false));
        Check(String.Join(",", calls) == "attach:Embedded,layout:Embedded,notify:Embedded");
        Console.WriteLine("Pure window checks passed; no Form constructed");
    }
}'''
        with tempfile.TemporaryDirectory(prefix='nam-window-') as folder:
            folder = Path(folder)
            source = folder / 'checks.cs'
            source.write_text(harness, encoding='utf-8')
            exe = folder / 'checks.exe'
            references = ['System.Windows.Forms.dll', 'System.Drawing.dll', 'System.Web.Extensions.dll']
            for assembly in assemblies:
                shutil.copy2(assembly, folder / assembly.name)
            references.extend(str(path) for path in assemblies)
            stock = ROOT / 'build' / 'HX Edit' / 'HX Edit.exe'
            icon_flags = []
            if stock.is_file():
                icon = write_browser_icon(stock, folder / 'ToneBrowser.ico')
                icon_flags = [f'/win32icon:{icon}', f'/resource:{icon},ToneBrowser.ico']
            result = subprocess.run([str(compiler), '/nologo', '/target:exe', '/platform:x86',
                                     '/codepage:65001', '/main:BrowserWindowChecks', f'/out:{exe}',
                                     *icon_flags,
                                     *(f'/reference:{ref}' for ref in references),
                                     str(ROOT / 'tone_browser.cs'), str(source)],
                                    capture_output=True, text=True, timeout=30,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('no Form constructed', result.stdout)


if __name__ == '__main__':
    unittest.main()
