"""Execute actual C# window handlers with in-memory HWND/WebView stand-ins."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tone_browser import ROOT, SDK_VERSION


def method(source, name):
    start = source.index('    void ' + name + '(')
    first = source.index('{', start)
    depth = 1
    end = first + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


@pytest.mark.skipif(os.name != 'nt', reason='Windows C# compiler required')
def test_repeated_dock_popout_preserves_page_and_session_without_native_windows(tmp_path):
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    sdk = ROOT / 'build' / f'tone-browser-{SDK_VERSION}'
    assemblies = [sdk / f'Microsoft.Web.WebView2.{name}.dll' for name in ('Core', 'WinForms')]
    if not compiler.is_file() or not all(path.is_file() for path in assemblies):
        pytest.skip('Local C# compiler/WebView2 SDK unavailable; no download')
    actual = (ROOT / 'tone_browser.cs').read_text(encoding='utf-8')
    handlers = '\n'.join(method(actual, name) for name in (
        'ReparentPane', 'ApplyPresentationLayout', 'FocusPresentation', 'Receive', 'LayoutPane'))
    harness = r'''
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;
using WindowMode = ToneBrowser.WindowMode;

class PresentationRoundTrips {
    const string CardUrl = "https://hx-edit-nam.invalid/tone";
    readonly IntPtr parent = ToneBrowser.ParseWindowHandle("2147483649");
    readonly IntPtr Handle = new IntPtr(123);
    IntPtr nativeParent;
    WindowMode windowMode = WindowMode.Embedded;
    ToneBrowser.PresentationController presentation;
    Shell floatingHost;
    readonly View browser = new View("https://www.tone3000.com/api/v1/select/tones/keep-this-page");
    readonly View details = new View(CardUrl);
    readonly Part popOut = new Part(), targetLabel = new Part(), targetBar = new Part { Height = 38 };
    readonly Part detailRow = new Part();
    readonly Hints hints = new Hints();
    Rectangle dock = new Rectangle(20, 40, 700, 500), applied, positioned;
    bool closing = false, IsDisposed = false, panelVisible = true, Visible = true;
    bool canRetry, hasTone, library, targetCanEnd, parentIconic;
    string catalogPage, openAfterLoad, cardHtml = "retained-card";
    Uri callback;
    Dictionary<string, object> status = new Dictionary<string, object>();
    int shells, reparentCount, foregroundCalls, cancelCount, positionCalls, raised;
    readonly object previewSession = new object();
    readonly List<string> notifications = new List<string>();

    class Part { public string Text; public bool Enabled = true; public int Height; }
    class Hints { public void SetToolTip(Part control, string text) {} }
    class Core {
        public string Source;
        public readonly object AuthenticationSession = new object();
        public int ScrollY = 475, Navigations;
        public void Navigate(string url) { Source = url; Navigations++; }
    }
    class View {
        public readonly Core CoreWebView2;
        public bool IsHandleCreated = true;
        public View(string url) { CoreWebView2 = new Core { Source = url }; }
        public void Focus() {}
    }
    class Shell {
        public IntPtr Handle = new IntPtr(456);
        public bool IsDisposed = false, Visible;
        public FormWindowState WindowState = FormWindowState.Normal;
        public Size ClientSize = new Size(1000, 700);
        public void Show() { Visible = true; }
        public void Hide() { Visible = false; }
        public void BringToFront() {}
        public void Activate() {}
    }
    PresentationRoundTrips() {
        nativeParent = parent;
        presentation = new ToneBrowser.PresentationController(
            () => panelVisible && dock.Width > 60 && dock.Height > 60,
            ReparentPane, ApplyPresentationLayout, mode => notifications.Add(mode.ToString()), FocusPresentation);
    }
    void EnsureFloatingHost() { if (floatingHost == null) { floatingHost = new Shell(); shells++; } }
    IntPtr SetParent(IntPtr pane, IntPtr destination) {
        Check(pane == Handle); var old = nativeParent; nativeParent = destination; reparentCount++; return old;
    }
    IntPtr GetParent(IntPtr pane) { Check(pane == Handle); return nativeParent; }
    bool SetForegroundWindow(IntPtr target) { Check(target == parent || target == floatingHost.Handle); foregroundCalls++; return true; }
    bool ShowWindow(IntPtr target, int command) { Check(target == parent && command == 9); parentIconic = false; return true; }
    bool IsWindow(IntPtr target) { return target == parent; }
    bool IsIconic(IntPtr target) { Check(target == parent); return parentIconic; }
    bool SetWindowPos(IntPtr pane, IntPtr after, int x, int y, int w, int h, uint flags) {
        Check(pane == Handle && (flags & 0x0010) != 0); positionCalls++;
        if (flags == 0x0013) raised++;
        else positioned = new Rectangle(x, y, w, h);
        return true;
    }
    void Show() { Visible = true; }
    void CloseFromHost() { cancelCount++; }
    void ChangePresentation(WindowMode mode, bool focus) { presentation.SwitchTo(mode, focus); }
    static WindowMode ParseWindowMode(string mode) { return ToneBrowser.ParseWindowMode(mode); }
    static bool Flag(Dictionary<string, object> value, string key) { return ToneBrowser.Flag(value, key); }
    static string TargetText(Dictionary<string, object> value) { return ToneBrowser.TargetText(value); }
    static int DetailHeight(int height, bool local, bool tone) { return ToneBrowser.DetailHeight(height, local, tone); }
    static bool IsToneUrl(string url) { return url != null && url.StartsWith("https://www.tone3000.com/"); }
    void UpdateTargetButtons() {}
    void UpdateCard() {}
    static void Check(bool value) { if (!value) throw new Exception("presentation round-trip failed"); }
    void Command(string mode) { Receive(new Dictionary<string, object> {
        {"type", "presentation"}, {"mode", mode}, {"focus", true}}); }
    void Run() {
        var page = browser.CoreWebView2;
        var card = details.CoreWebView2;
        var auth = page.AuthenticationSession;
        var session = previewSession;
        var url = page.Source;
        for (int i = 0; i < 50; i++) {
            Command("floating");
            Check(nativeParent == floatingHost.Handle && floatingHost.Visible);
            Check(positioned == new Rectangle(0, 0, 1000, 700));
            var shell = floatingHost;
            var nextDock = new Rectangle(40 + i, 60 + i, 650 + i, 450 + i);
            Receive(new Dictionary<string, object> { {"type", "bounds"}, {"visible", true},
                {"x", nextDock.X}, {"y", nextDock.Y}, {"w", nextDock.Width}, {"h", nextDock.Height} });
            Check(dock == nextDock);
            Check(positioned == new Rectangle(0, 0, 1000, 700));
            int focusCount = foregroundCalls;
            LayoutPane(); LayoutPane();
            Check(foregroundCalls == focusCount); // polling never steals focus
            floatingHost.WindowState = FormWindowState.Minimized;
            LayoutPane();
            Check(floatingHost.WindowState == FormWindowState.Minimized);
            Receive(new Dictionary<string, object> { {"type", "focus"} });
            Check(floatingHost.WindowState == FormWindowState.Normal);
            parentIconic = true;
            Command("embedded");
            Check(nativeParent == parent && !floatingHost.Visible && !parentIconic);
            Check(positioned == nextDock);
            Check(Object.ReferenceEquals(shell, floatingHost));
            Check(Object.ReferenceEquals(page, browser.CoreWebView2) && page.Source == url && page.ScrollY == 475);
            Check(Object.ReferenceEquals(auth, page.AuthenticationSession));
            Check(Object.ReferenceEquals(card, details.CoreWebView2) && card.Source == CardUrl);
            Check(Object.ReferenceEquals(session, previewSession) && cardHtml == "retained-card");
            Check(page.Navigations == 0 && card.Navigations == 0 && cancelCount == 0);
            int positions = positionCalls, focus = foregroundCalls, layers = raised;
            LayoutPane(); LayoutPane();
            Check(positionCalls == positions && foregroundCalls == focus);
            // Native selection can raise its banner without moving the panel.
            Receive(new Dictionary<string,object> { {"type", "target"}, {"bound", true}, {"label", "NAM B"} });
            Check(raised == layers + 1 && positioned == nextDock && foregroundCalls == focus);
            Receive(new Dictionary<string, object> { {"type", "bounds"}, {"visible", true},
                {"x", nextDock.X}, {"y", nextDock.Y}, {"w", nextDock.Width}, {"h", nextDock.Height} });
            Check(raised == layers + 2 && positioned == nextDock && foregroundCalls == focus);
        }
        Check(shells == 1 && reparentCount == 100 && notifications.Count == 100);
        Check(unchecked((uint)parent.ToInt64()) == 0x80000001);
        Console.WriteLine("50 presentation round trips passed; actual handlers, no Form or native window created");
    }
    static void Main() { new PresentationRoundTrips().Run(); }
// ACTUAL_HANDLERS
}
'''.replace('// ACTUAL_HANDLERS', handlers)
    harness_path = tmp_path / 'presentation_checks.cs'
    harness_path.write_text(harness, encoding='utf-8')
    exe = tmp_path / 'presentation_checks.exe'
    references = ['System.Windows.Forms.dll', 'System.Drawing.dll', 'System.Web.Extensions.dll']
    for assembly in assemblies:
        shutil.copy2(assembly, tmp_path / assembly.name)
    references.extend(str(path) for path in assemblies)
    result = subprocess.run([str(compiler), '/nologo', '/target:exe', '/platform:x86', '/codepage:65001',
                             '/main:PresentationRoundTrips', f'/out:{exe}',
                             *(f'/reference:{ref}' for ref in references), str(ROOT / 'tone_browser.cs'), str(harness_path)],
                            capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '50 presentation round trips passed' in result.stdout
