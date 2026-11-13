"""Real WinForms startup under a never-shown local HWND; no WebView initialization.

This covers native handle creation omitted by the in-memory presentation tests.
It does not test rendering, device integration, foreground behavior or WebView
survival across native reparenting.
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tone_browser import ROOT, SDK_VERSION


@pytest.mark.skipif(os.name != 'nt', reason='Windows Forms/native HWND test')
def test_actual_child_form_reaches_shown_with_correct_native_parent(tmp_path):
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    sdk = ROOT / 'build' / f'tone-browser-{SDK_VERSION}'
    assemblies = [sdk / f'Microsoft.Web.WebView2.{name}.dll' for name in ('Core', 'WinForms')]
    if not compiler.is_file() or not all(path.is_file() for path in assemblies):
        pytest.skip('Local C# compiler/WebView2 SDK unavailable; no download')
    harness = r'''
using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using Microsoft.Web.WebView2.WinForms;

class NativeStartupChecks {
    [DllImport("user32.dll")] static extern IntPtr GetParent(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr window);
    [DllImport("user32.dll")] static extern IntPtr GetTopWindow(IntPtr window);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int w, int h, uint flags);
    static readonly BindingFlags PrivateInstance = BindingFlags.Instance | BindingFlags.NonPublic;
    static void Check(bool condition, string message) {
        if (!condition) throw new Exception(message);
    }
    static Form Construct(IntPtr parent, ToneBrowser.WindowMode mode, TextWriter output = null) {
        return (Form)Activator.CreateInstance(typeof(ToneBrowser), PrivateInstance, null,
            new object[] { parent, "unused-no-webview-profile", new Rectangle(0, 0, 640, 480),
                output ?? TextWriter.Null, mode }, null);
    }
    static void CloseFromHost(Form pane) {
        typeof(ToneBrowser).GetMethod("CloseFromHost", PrivateInstance).Invoke(pane, null);
    }
    static void CheckUninitializedWebViews(Form pane) {
        foreach (string name in new[] { "browser", "details" }) {
            var view = (WebView2)typeof(ToneBrowser).GetField(name, PrivateInstance).GetValue(pane);
            Check(view.CoreWebView2 == null, "WebView must remain uninitialized");
            Check(view.Source == null, "No page may be loaded");
        }
    }
    static void CheckMode(IntPtr parent, ToneBrowser.WindowMode mode) {
        IntPtr child;
        var output = new StringWriter();
        using (var pane = Construct(parent, mode, output))
        using (var deadline = new Timer { Interval = 3000 }) {
            // Never enter the message loop unless the child is already attached
            // to our hidden parent. This also makes a future regression fail
            // before any attempt to show a top-level window.
            Check(!pane.TopLevel, "Native child must also be a managed child");
            child = pane.Handle;
            Check(GetParent(child) == parent, "Initial native parent mismatch");
            Check(!IsWindowVisible(parent) && !IsWindowVisible(child), "Native windows must stay hidden");
            CheckUninitializedWebViews(pane);
            Exception failure = null;
            bool shown = false;
            pane.Shown += (sender, args) => {
                try {
                    shown = true;
                    Check(GetParent(pane.Handle) == parent, "Start would reject the parent after Shown");
                    Check(!IsWindowVisible(parent) && !IsWindowVisible(pane.Handle), "A window became visible");
                    CheckUninitializedWebViews(pane);
                    Check(!(bool)typeof(ToneBrowser).GetField("started", PrivateInstance).GetValue(pane),
                        "Production Start must not run in this test");
                    // A native sibling reappears over the docked header without
                    // moving it. No parent or child is ever shown to the user.
                    var banner = new NativeWindow();
                    banner.CreateHandle(new CreateParams { Parent = parent, Style = 0x40000000,
                        X = 0, Y = 0, Width = 640, Height = 38 });
                    try {
                        var layout = typeof(ToneBrowser).GetMethod("LayoutPane", PrivateInstance);
                        layout.Invoke(pane, new object[] { false });
                        SetWindowPos(banner.Handle, IntPtr.Zero, 0, 0, 0, 0, 0x0013);
                        Check(GetTopWindow(parent) == banner.Handle, "Native banner did not cover child");
                        layout.Invoke(pane, new object[] { false });
                        Check(GetTopWindow(parent) == banner.Handle, "Passive timer changed unchanged z-order");
                        layout.Invoke(pane, new object[] { true });
                        Check(GetTopWindow(parent) == child, "Selection event did not restore pane z-order");
                        Check(!IsWindowVisible(parent) && !IsWindowVisible(child), "Layer test became visible");
                    } finally { banner.DestroyHandle(); }
                    var cancel = typeof(ToneBrowser).GetMethod("RequestCancel", PrivateInstance);
                    cancel.Invoke(pane, new object[] { "escape" });
                    cancel.Invoke(pane, new object[] { "window-close" });
                    Check(output.ToString().Trim() == "{\"type\":\"action\",\"value\":{\"close\":\"escape\"}}",
                        "Duplicate Escape/window-close must request only one cleanup with original reason");
                    Check(!pane.IsDisposed && IsWindow(child), "Escape disposed pane before host cleanup");
                    // Exercise the production trusted local-card handler, not
                    // just RequestCancel: Close must signal intent immediately
                    // instead of placing a plain finish behind a slow download.
                    typeof(ToneBrowser).GetField("cancelRequested", PrivateInstance).SetValue(pane, false);
                    output.GetStringBuilder().Clear();
                    var cardAction = typeof(ToneBrowser).GetMethod("HandleCardAction", PrivateInstance);
                    cardAction.Invoke(pane, new object[] { "finish" });
                    cardAction.Invoke(pane, new object[] { "finish" });
                    Check(output.ToString().Trim() == "{\"type\":\"action\",\"value\":{\"close\":\"close-button\"}}",
                        "Card Close did not immediately emit one structured cleanup request");
                    Check(!pane.IsDisposed && IsWindow(child), "Close disposed pane before host cleanup");
                } catch (Exception error) { failure = error; }
                finally { CloseFromHost(pane); }
            };
            deadline.Tick += (sender, args) => {
                failure = new Exception("Shown deadline exceeded");
                CloseFromHost(pane);
            };
            deadline.Start();
            // Same lifecycle as production Main, but no Start subscription:
            // no focus operation, timer layout, browser environment, or network.
            Application.Run(pane);
            deadline.Stop();
            if (failure != null) throw failure;
            Check(shown, "Actual Form.Shown was not raised");
        }
        Check(!IsWindow(child), "Child HWND survived disposal");
    }
    [STAThread]
    static void Main() {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        var host = new NativeWindow();
        host.CreateHandle(new CreateParams { Caption = "Hidden NAM startup test parent",
            Style = unchecked((int)0x80000000), ExStyle = 0x08000080,
            X = -32000, Y = -32000, Width = 640, Height = 480 });
        IntPtr parent = host.Handle;
        try {
            Check(!IsWindowVisible(parent), "Parent must never be shown");
            // Reproduce Candidate56 using the actual constructor and framework:
            // TopLevel=true lets Form.CreateHandle replace the supplied parent
            // with its hidden taskbar owner. No Show/Application.Run here.
            using (var broken = Construct(parent, ToneBrowser.WindowMode.Embedded)) {
                broken.TopLevel = true;
                Check(GetParent(broken.Handle) != parent, "Negative control did not reproduce parent replacement");
                Check(!IsWindowVisible(broken.Handle), "Negative control must stay hidden");
                CheckUninitializedWebViews(broken);
            }
            CheckMode(parent, ToneBrowser.WindowMode.Embedded);
            CheckMode(parent, ToneBrowser.WindowMode.Floating);
            Check(!IsWindowVisible(parent), "Parent became visible");
        } finally { host.DestroyHandle(); }
        Check(!IsWindow(parent), "Parent HWND survived cleanup");
        Console.WriteLine("Actual WinForms startup passed in both modes; negative control reproduced; no WebView initialized");
    }
}
'''
    source = tmp_path / 'native_startup_checks.cs'
    source.write_text(harness, encoding='utf-8')
    exe = tmp_path / 'native_startup_checks.exe'
    references = ['System.Windows.Forms.dll', 'System.Drawing.dll', 'System.Web.Extensions.dll']
    for assembly in assemblies:
        shutil.copy2(assembly, tmp_path / assembly.name)
    references.extend(str(path) for path in assemblies)
    result = subprocess.run([str(compiler), '/nologo', '/target:exe', '/platform:x86', '/codepage:65001',
                             '/main:NativeStartupChecks', f'/out:{exe}',
                             *(f'/reference:{ref}' for ref in references), str(ROOT / 'tone_browser.cs'), str(source)],
                            capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'negative control reproduced; no WebView initialized' in result.stdout
