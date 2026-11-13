"""Retain actual local WebView documents across hidden HWND reparenting.

No HX Edit, account, device, foreground activation, remote page or user profile
is used. All native windows stay under never-shown parents.
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tone_browser import ROOT, SDK_VERSION


@pytest.mark.skipif(os.name != 'nt', reason='Windows WebView2/native HWND test')
def test_hidden_native_reparent_keeps_real_local_webview_documents(tmp_path):
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    sdk = ROOT / 'build' / f'tone-browser-{SDK_VERSION}'
    assets = [sdk / name for name in (
        'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll', 'WebView2Loader.dll')]
    if not compiler.is_file() or not all(path.is_file() for path in assets):
        pytest.skip('Local C# compiler/WebView2 SDK unavailable; no download')
    harness = r'''
using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

class NativeReparentChecks {
    [DllImport("user32.dll")] static extern IntPtr GetParent(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    static readonly BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    static void Check(bool condition, string message) { if (!condition) throw new Exception(message); }
    static async Task Deadline(Task work, string phase) {
        if (await Task.WhenAny(work, Task.Delay(10000)) != work) throw new Exception("WebView deadline exceeded: " + phase);
        await work;
    }
    static async Task Prepare(WebView2 view, CoreWebView2Environment environment, string identity) {
        // A hidden parent doesn't recursively create WinForms child handles.
        // Force only handle creation, never visibility, before WebView startup.
        var handle = view.Handle;
        await Deadline(view.EnsureCoreWebView2Async(environment), identity + ": initialize");
        var core = view.CoreWebView2;
        core.Settings.IsWebMessageEnabled = false;
        var address = "https://offline-browser-test.invalid/" + identity;
        var loaded = new TaskCompletionSource<bool>();
        core.ProcessFailed += (sender, args) => loaded.TrySetException(new Exception("Local WebView process failed: " + args.ProcessFailedKind));
        core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
        core.WebResourceRequested += (sender, args) => {
            if (args.Request.Uri == address) {
                var html = "<!doctype html><title>Offline reparent check</title><input id='keep' value='" + identity + "'><script>window.sessionMarker={count:17};</script>";
                args.Response = environment.CreateWebResourceResponse(new MemoryStream(System.Text.Encoding.UTF8.GetBytes(html)), 200, "OK", "Content-Type: text/html");
            } else args.Response = environment.CreateWebResourceResponse(new MemoryStream(), 403, "Offline test", "");
        };
        core.NavigationStarting += (sender, args) => {
            if (args.Uri != address) args.Cancel = true;
        };
        core.NavigationCompleted += (sender, args) => {
            if (!args.IsSuccess) loaded.TrySetException(new Exception("Local document navigation failed: " + args.WebErrorStatus));
        };
        core.DOMContentLoaded += (sender, args) => loaded.TrySetResult(true);
        core.Navigate(address);
        await Deadline(loaded.Task, identity + ": local document");
        Check(await core.ExecuteScriptAsync("document.getElementById('keep').value") == "\"" + identity + "\"", "Local document did not initialize");
    }
    [STAThread]
    static void Main(string[] args) {
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        var host = new NativeWindow();
        host.CreateHandle(new CreateParams { Caption = "Hidden local WebView test parent",
            Style = unchecked((int)0x80000000), ExStyle = 0x08000080,
            X = -32000, Y = -32000, Width = 640, Height = 480 });
        Exception failure = null;
        bool completed = false;
        try {
            using (var pane = (Form)Activator.CreateInstance(typeof(ToneBrowser), Private, null,
                new object[] { host.Handle, args[0], new Rectangle(0, 0, 640, 480), TextWriter.Null, ToneBrowser.WindowMode.Embedded }, null))
            using (var deadline = new Timer { Interval = 25000 }) {
                Action finish = () => typeof(ToneBrowser).GetMethod("CloseFromHost", Private).Invoke(pane, null);
                pane.Shown += async (sender, evt) => {
                    try {
                        Check(!IsWindowVisible(host.Handle) && !IsWindowVisible(pane.Handle), "Test must stay hidden");
                        var options = new CoreWebView2EnvironmentOptions {
                            AdditionalBrowserArguments = "--disable-background-networking --disable-component-update --no-first-run"
                        };
                        var environment = await CoreWebView2Environment.CreateAsync(null, args[0], options);
                        var browser = (WebView2)typeof(ToneBrowser).GetField("browser", Private).GetValue(pane);
                        var details = (WebView2)typeof(ToneBrowser).GetField("details", Private).GetValue(pane);
                        await Prepare(browser, environment, "catalog-retained");
                        await Prepare(details, environment, "card-retained");
                        var browserCore = browser.CoreWebView2;
                        var cardCore = details.CoreWebView2;
                        var paneHandle = pane.Handle;
                        var reparent = typeof(ToneBrowser).GetMethod("ReparentPane", Private);
                        for (int i = 0; i < 10; ++i) {
                            reparent.Invoke(pane, new object[] { ToneBrowser.WindowMode.Floating });
                            var floating = (Form)typeof(ToneBrowser).GetField("floatingHost", Private).GetValue(pane);
                            Check(GetParent(pane.Handle) == floating.Handle, "Floating native parent mismatch");
                            Check(!IsWindowVisible(floating.Handle) && !IsWindowVisible(pane.Handle), "Floating test must remain hidden");
                            reparent.Invoke(pane, new object[] { ToneBrowser.WindowMode.Embedded });
                            Check(GetParent(pane.Handle) == host.Handle && pane.Handle == paneHandle, "Dock changed child handle");
                            Check(Object.ReferenceEquals(browser.CoreWebView2, browserCore) && Object.ReferenceEquals(details.CoreWebView2, cardCore), "WebView replaced");
                            Check(await browserCore.ExecuteScriptAsync("document.getElementById('keep').value + ':' + window.sessionMarker.count") == "\"catalog-retained:17\"", "Catalog document/session changed");
                            Check(await cardCore.ExecuteScriptAsync("document.getElementById('keep').value + ':' + window.sessionMarker.count") == "\"card-retained:17\"", "Card document/session changed");
                            Check(GetForegroundWindow() != pane.Handle && GetForegroundWindow() != floating.Handle, "Test gained foreground");
                        }
                        completed = true;
                    } catch (Exception error) { failure = error; }
                    finally { if (!pane.IsDisposed) finish(); }
                };
                deadline.Tick += (sender, evt) => { failure = new Exception("Native reparent test deadline exceeded"); finish(); };
                deadline.Start(); Application.Run(pane); deadline.Stop();
            }
        } finally { host.DestroyHandle(); }
        if (failure != null) throw failure;
        Check(completed, "Hidden local WebView checks did not complete");
        Console.WriteLine("10 actual hidden WebView round trips passed; local documents retained; no HX Edit or device used");
    }
}
'''
    source = tmp_path / 'native_reparent.cs'
    source.write_text(harness, encoding='utf-8')
    exe = tmp_path / 'native_reparent.exe'
    for asset in assets:
        shutil.copy2(asset, tmp_path / asset.name)
    references = ['System.Windows.Forms.dll', 'System.Drawing.dll', 'System.Web.Extensions.dll',
                  *(str(path) for path in assets[:2])]
    result = subprocess.run([str(compiler), '/nologo', '/target:exe', '/platform:x86', '/codepage:65001',
                             '/main:NativeReparentChecks', f'/out:{exe}',
                             *(f'/reference:{ref}' for ref in references), str(ROOT / 'tone_browser.cs'), str(source)],
                            capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run([str(exe), str(tmp_path / 'temporary-profile')], capture_output=True,
                            text=True, timeout=35, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '10 actual hidden WebView round trips passed' in result.stdout
