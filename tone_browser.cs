using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

// An embedded or floating helper. Keeping the WebView in a helper process lets HX Edit
// own the USB connection and its UI thread while Edge runs its own message loop.
sealed class ToneBrowser : Form
{
    internal enum WindowMode { Embedded, Floating }
    const string CardUrl = "https://hx-edit-nam.invalid/tone";
    const string EscapeMessage = "hx-unbound-preview-escape";
    // WebView2 owns a native child message loop. WinForms KeyDown alone does
    // not consistently see keys handled by a focused web document.
    internal const string EscapeScript = @"(() => {
      document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || event.repeat || event.altKey || event.ctrlKey || event.metaKey) return;
        event.preventDefault(); event.stopImmediatePropagation();
        window.chrome.webview.postMessage('hx-unbound-preview-escape');
      }, true);
    })();";
    [DllImport("user32.dll")] static extern IntPtr GetWindowDpiAwarenessContext(IntPtr hwnd);
    [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] static extern IntPtr GetParent(IntPtr hwnd);
    [DllImport("user32.dll", SetLastError = true)] static extern IntPtr SetParent(IntPtr hwnd, IntPtr parent);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr hwnd, int command);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int w, int h, uint flags);

    readonly IntPtr parent;
    readonly string profile;
    readonly TextWriter output;
    readonly WindowMode initialMode;
    WindowMode windowMode = WindowMode.Embedded;
    readonly PresentationController presentation;
    Form floatingHost;
    bool started;
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly WebView2 browser = new WebView2 { Dock = DockStyle.Fill };
    readonly WebView2 details = new WebView2 { Dock = DockStyle.Fill };
    readonly RowStyle detailRow = new RowStyle(SizeType.Absolute, 48);
    readonly Timer timer = new Timer { Interval = 150 };
    readonly Panel targetBar = new Panel { Dock = DockStyle.Top, Height = 38 };
    readonly Label targetLabel = new Label { Dock = DockStyle.Fill, ForeColor = Color.Gainsboro,
        Padding = new Padding(8, 0, 4, 0), TextAlign = ContentAlignment.MiddleLeft,
        Text = "Select a NAM block in HX Edit", AutoEllipsis = true };
    readonly Button popOut = new Button { Text = "Pop out", AutoSize = true, Height = 26 };
    readonly Button endPreview = new Button { Text = "End preview", AutoSize = true, Height = 26, Visible = false };
    readonly ToolTip hints = new ToolTip();
    Rectangle dock;
    Rectangle applied;
    Uri callback;
    bool panelVisible = true;
    string catalogPage;
    string openAfterLoad;   // a tone page to open once the selection catalog has loaded (the block's own tone)
    bool canRetry;
    bool hasTone;
    bool library;
    Dictionary<string, object> status = new Dictionary<string, object>();
    string cardHtml;
    bool closing;
    bool hostClosing;
    bool cancelRequested;
    bool targetCanEnd;

    // This controller contains no WebView/session operations. The live adapter
    // reparents one existing child HWND; toggling must never recreate its handle.
    internal sealed class PresentationController
    {
        readonly Func<bool> canDock;
        readonly Action<WindowMode> attach, layout, notify, focus;
        internal WindowMode Mode { get; private set; }
        internal PresentationController(Func<bool> canDock, Action<WindowMode> attach,
            Action<WindowMode> layout, Action<WindowMode> notify, Action<WindowMode> focus)
        {
            this.canDock = canDock; this.attach = attach; this.layout = layout;
            this.notify = notify; this.focus = focus; Mode = WindowMode.Embedded;
        }
        internal bool SwitchTo(WindowMode next, bool activate)
        {
            if (next == WindowMode.Embedded && !canDock()) return false;
            if (next != Mode) { attach(next); Mode = next; }
            layout(Mode);
            notify(Mode);
            if (activate) focus(Mode);
            return true;
        }
    }

    internal static WindowMode ParseWindowMode(string mode)
    {
        if (mode == "embedded") return WindowMode.Embedded;
        if (mode == "floating") return WindowMode.Floating;
        throw new ArgumentException("Unknown TONE3000 window mode.");
    }

    internal static IntPtr ParseWindowHandle(string value)
    {
        uint handle;
        if (!UInt32.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out handle) || handle == 0)
            throw new ArgumentException("Invalid HX Edit window handle.");
        // Frida serializes the x86 HWND as uint32. Preserve its bits even when
        // the high bit is set; IntPtr(long) would overflow in the x86 helper.
        return IntPtr.Size == 4 ? new IntPtr(unchecked((int)handle)) : new IntPtr((long)handle);
    }

    internal static Rectangle FloatingBounds(Rectangle workingArea)
    {
        // Dock coordinates belong to the editor's client area, not the desktop.
        // Start on its monitor; subsequent movement/resize belongs to the user.
        int width = Math.Min(1100, Math.Max(1, workingArea.Width - 48));
        int height = Math.Min(780, Math.Max(1, workingArea.Height - 48));
        return new Rectangle(workingArea.Left + (workingArea.Width - width) / 2,
            workingArea.Top + (workingArea.Height - height) / 2, width, height);
    }

    internal static int DetailHeight(int height, bool library, bool hasTone)
    {
        height = Math.Max(0, height);
        return Math.Min(height, library ? height : hasTone ? Math.Min(150, Math.Max(130, height - 100)) : 48);
    }

    ToneBrowser(IntPtr parent, string profile, Rectangle bounds, TextWriter output, WindowMode mode)
    {
        this.parent = parent;
        this.profile = profile;
        this.output = output;
        initialMode = mode;
        dock = bounds;
        // Keep managed and native child state aligned. A top-level Form rewrites
        // its native parent to the hidden taskbar owner during handle creation.
        // Pop-out only reparents this unchanged child into its floating shell.
        TopLevel = false;
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        AutoScaleMode = AutoScaleMode.None;
        Text = "TONE3000 · HX Edit NAM";
        BackColor = Color.FromArgb(18, 18, 18);
        Font = new Font("Segoe UI", 9.0f, FontStyle.Regular);
        using (var resource = typeof(ToneBrowser).Assembly.GetManifestResourceStream("ToneBrowser.ico"))
            if (resource != null) Icon = new Icon(resource);
        presentation = new PresentationController(
            () => panelVisible && dock.Width > 60 && dock.Height > 60 && IsWindow(parent),
            ReparentPane, ApplyPresentationLayout,
            current => output.WriteLine(json.Serialize(new { type = "presentation", mode = ModeName(current) })),
            FocusPresentation);

        browser.DefaultBackgroundColor = BackColor;
        details.DefaultBackgroundColor = BackColor;
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 2 };
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(detailRow);
        browser.Margin = details.Margin = Padding.Empty;
        layout.Controls.Add(browser, 0, 0);
        layout.Controls.Add(details, 0, 1);
        Controls.Add(layout);
        var targetActions = new FlowLayoutPanel { Dock = DockStyle.Right, AutoSize = true,
            FlowDirection = FlowDirection.LeftToRight, WrapContents = false, Padding = new Padding(0, 5, 6, 0) };
        foreach (var button in new[] { endPreview, popOut })
        {
            button.FlatStyle = FlatStyle.Flat;
            button.FlatAppearance.BorderColor = Color.FromArgb(81, 66, 87);
            button.BackColor = Color.FromArgb(48, 39, 52);
            button.ForeColor = Color.WhiteSmoke;
            button.Margin = new Padding(4, 0, 0, 0);
            targetActions.Controls.Add(button);
        }
        endPreview.Click += (sender, args) => SendAction("end");
        popOut.Click += (sender, args) => ChangePresentation(
            windowMode == WindowMode.Embedded ? WindowMode.Floating : WindowMode.Embedded, true);
        targetBar.BackColor = Color.FromArgb(33, 29, 37);
        targetBar.Controls.Add(targetLabel);
        if (Icon != null)
            targetBar.Controls.Add(new PictureBox { Dock = DockStyle.Left, Width = 32,
                Image = Icon.ToBitmap(), SizeMode = PictureBoxSizeMode.CenterImage });
        targetBar.Controls.Add(targetActions);
        Controls.Add(targetBar);
        browser.PreviewKeyDown += MarkPreviewEscape;
        details.PreviewKeyDown += MarkPreviewEscape;
        browser.KeyDown += HandlePreviewEscape;
        details.KeyDown += HandlePreviewEscape;
        timer.Tick += (sender, args) => LayoutPane();
        FormClosing += (sender, args) => {
            if (closing) return;
            closing = true;
            timer.Stop();
            if (!hostClosing) RequestCancel("window-close");
        };
        FormClosed += (sender, args) => {
            timer.Dispose(); hints.Dispose();
            if (floatingHost != null) floatingHost.Dispose();
        };
    }

    internal static string ModeName(WindowMode mode)
    {
        return mode == WindowMode.Floating ? "floating" : "embedded";
    }

    void EnsureFloatingHost()
    {
        if (floatingHost != null) return;
        var work = Screen.FromHandle(parent).WorkingArea;
        floatingHost = new Form { Text = Text, Icon = Icon, StartPosition = FormStartPosition.Manual,
            FormBorderStyle = FormBorderStyle.Sizable, ShowInTaskbar = true, AutoScaleMode = AutoScaleMode.None,
            BackColor = BackColor, Bounds = FloatingBounds(work),
            MinimumSize = new Size(Math.Min(640, work.Width), Math.Min(420, work.Height)) };
        floatingHost.Resize += (sender, args) => { if (windowMode == WindowMode.Floating) LayoutPane(); };
        floatingHost.FormClosing += (sender, args) => {
            if (closing) return;
            args.Cancel = true; RequestCancel("window-close");
        };
    }

    void ReparentPane(WindowMode next)
    {
        if (next == WindowMode.Floating) EnsureFloatingHost();
        var destination = next == WindowMode.Floating ? floatingHost.Handle : parent;
        // Both destinations host the unchanged WS_CHILD pane. No TopLevel,
        // ShowInTaskbar or FormBorderStyle mutation on the WebView's owner.
        SetParent(Handle, destination);
        if (GetParent(Handle) != destination)
            throw new InvalidOperationException("Could not move the browser window; its current session was retained.");
        windowMode = next;
        applied = Rectangle.Empty;
    }

    void ApplyPresentationLayout(WindowMode mode)
    {
        popOut.Text = mode == WindowMode.Floating ? "Dock" : "Pop out";
        hints.SetToolTip(popOut, mode == WindowMode.Floating ? "Return to the HX Edit parameter panel" : "Move this browser into its own window");
        if (mode == WindowMode.Floating) floatingHost.Show();
        else if (floatingHost != null) floatingHost.Hide();
        LayoutPane(true);
    }

    void ChangePresentation(WindowMode mode, bool focus)
    {
        try
        {
            if (!presentation.SwitchTo(mode, focus))
                hints.SetToolTip(popOut, "Select a NAM block in HX Edit to dock here.");
        }
        catch (Exception error)
        {
            // A refused reparent is a presentation error, not preview cancel.
            hints.SetToolTip(popOut, error.Message);
            targetLabel.Text = "Window change unavailable · session retained";
        }
    }

    void FocusPresentation(WindowMode mode)
    {
        if (mode == WindowMode.Floating)
        {
            if (floatingHost.WindowState == FormWindowState.Minimized)
                floatingHost.WindowState = FormWindowState.Normal;
            floatingHost.Show(); floatingHost.BringToFront(); floatingHost.Activate();
            SetForegroundWindow(floatingHost.Handle);
        }
        else
        {
            if (IsIconic(parent)) ShowWindow(parent, 9); // SW_RESTORE, explicit open only.
            SetForegroundWindow(parent);
            LayoutPane(true);
        }
        if (browser.IsHandleCreated) browser.Focus();
    }

    void RequestCancel(string reason)
    {
        if (cancelRequested || IsDisposed) return;
        cancelRequested = true;
        SendAction(new Dictionary<string, object> { { "close", reason } });
    }

    void HandleCardAction(string action)
    {
        if (action == null) return;
        if (action == EscapeMessage) RequestCancel("escape");
        else if (action == "finish") RequestCancel("close-button");
        else if (action != "back") SendAction(action);
        else if (catalogPage != null)
        {
            var page = catalogPage;
            catalogPage = null;
            browser.CoreWebView2.Navigate(page);
            UpdateCard();
        }
    }

    void MarkPreviewEscape(object sender, PreviewKeyDownEventArgs args)
    {
        if (args.KeyData == Keys.Escape) args.IsInputKey = true;
    }

    void HandlePreviewEscape(object sender, KeyEventArgs args)
    {
        if (args.KeyData != Keys.Escape) return;
        args.Handled = true;
        args.SuppressKeyPress = true;
        QueueUi(() => RequestCancel("escape"));
    }

    protected override bool ProcessCmdKey(ref Message message, Keys keys)
    {
        if (keys != Keys.Escape) return base.ProcessCmdKey(ref message, keys);
        RequestCancel("escape");
        return true;
    }

    protected override CreateParams CreateParams
    {
        get
        {
            var settings = base.CreateParams;
            settings.Parent = windowMode == WindowMode.Floating && floatingHost != null
                ? floatingHost.Handle : parent;
            settings.Style = (settings.Style & ~unchecked((int)0x80000000)) | 0x40000000;
            return settings;
        }
    }

    [STAThread]
    static void Main(string[] args)
    {
        var output = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true };
        try
        {
            if (args.Length != 6 && args.Length != 7) throw new ArgumentException("Invalid browser arguments.");
            var mode = ParseWindowMode(args.Length == 7 ? args[6] : "embedded");
            var parent = ParseWindowHandle(args[0]);
            if (!IsWindow(parent)) throw new InvalidOperationException("The HX Edit window is unavailable.");
            SetThreadDpiAwarenessContext(GetWindowDpiAwarenessContext(parent));
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (var input = new StreamReader(Console.OpenStandardInput(), Encoding.UTF8))
            using (var pane = new ToneBrowser(parent, args[1], new Rectangle(
                int.Parse(args[2]), int.Parse(args[3]), int.Parse(args[4]), int.Parse(args[5])), output, mode))
            {
                pane.Shown += async (sender, evt) => await pane.Start(input);
                Application.Run(pane);
            }
        }
        catch (Exception error)
        {
            output.WriteLine(new JavaScriptSerializer().Serialize(new { type = "error", text = error.Message }));
        }
    }

    async Task Start(TextReader input)
    {
        if (started) return;
        started = true;
        try
        {
            if (GetParent(Handle) != parent)
                throw new InvalidOperationException("Could not dock the TONE3000 pane.");
            ChangePresentation(initialMode, true);
            timer.Start();
            var env = await CoreWebView2Environment.CreateAsync(userDataFolder: profile);
            if (closing || IsDisposed) return;
            await browser.EnsureCoreWebView2Async(env);
            if (closing || IsDisposed) return;
            var core = browser.CoreWebView2;
            core.Profile.PreferredColorScheme = CoreWebView2PreferredColorScheme.Dark;
            // Catalog messages are accepted only for the one Escape signal;
            // device/library commands still originate from our local card.
            Configure(core, true);
            await core.AddScriptToExecuteOnDocumentCreatedAsync(EscapeScript);
            core.WebMessageReceived += (sender, args) => {
                if (IsToneUrl(args.Source) && IsToneUrl(core.Source) &&
                    ReadWebMessage(args) == EscapeMessage) RequestCancel("escape");
            };
            core.NavigationStarting += (sender, args) => args.Cancel = !CanNavigate(args.Uri);
            core.NavigationCompleted += (sender, args) => {
                // Once the selection catalog (/api/v1/select?authorization_id=…, after any sign-in) is up, open
                // the tone inside the same selection (/api/v1/select/tones/<slug>?authorization_id=…), so its
                // Select Tone still returns here. The slug is the one of the tone's public page /tones/<slug>.
                Uri page, tone;
                if (openAfterLoad == null || !args.IsSuccess || !IsToneUrl(core.Source) ||
                    !Uri.TryCreate(core.Source, UriKind.Absolute, out page) ||
                    page.AbsolutePath.TrimEnd('/') != "/api/v1/select") return;
                var authorization = Regex.Match(page.Query, @"[?&]authorization_id=([^&#]+)");
                var slug = Uri.TryCreate(openAfterLoad, UriKind.Absolute, out tone)
                    ? Regex.Match(tone.AbsolutePath, @"^/tones/([A-Za-z0-9._~-]+)/?$") : Match.Empty;
                openAfterLoad = null;
                if (authorization.Success && slug.Success)
                    core.Navigate("https://www.tone3000.com/api/v1/select/tones/" + slug.Groups[1].Value +
                        "?authorization_id=" + authorization.Groups[1].Value);
            };
            core.NewWindowRequested += (sender, args) => {
                args.Handled = true;
                if (CanNavigate(args.Uri)) core.Navigate(args.Uri);
            };
            core.WindowCloseRequested += (sender, args) => {
                if (catalogPage == null) RequestCancel("window-close");
            };

            await details.EnsureCoreWebView2Async(env);
            if (closing || IsDisposed) return;
            var card = details.CoreWebView2;
            Configure(card, true);
            await card.AddScriptToExecuteOnDocumentCreatedAsync(EscapeScript);
            card.NavigationStarting += (sender, args) => args.Cancel = args.Uri != CardUrl;
            card.NewWindowRequested += (sender, args) => args.Handled = true;
            card.AddWebResourceRequestedFilter(CardUrl, CoreWebView2WebResourceContext.Document);
            card.WebResourceRequested += (sender, args) => {
                if (cardHtml == null || args.Request.Uri != CardUrl) return;
                var html = new MemoryStream(Encoding.UTF8.GetBytes(cardHtml));
                args.Response = env.CreateWebResourceResponse(html, 200, "OK",
                    "Content-Type: text/html; charset=utf-8\r\nCache-Control: no-store");
            };
            card.DOMContentLoaded += (sender, args) => UpdateCard();
            card.WebMessageReceived += (sender, args) => {
                if (args.Source != CardUrl || card.Source != CardUrl) return;
                // Normalize trusted local Close immediately. The Python IPC
                // reader must see close intent even while its worker is busy
                // downloading/converting; a queued plain "finish" is too late.
                HandleCardAction(ReadWebMessage(args));
            };
            output.WriteLine("{\"type\":\"ready\"}");
            await Task.Run(() => ReadCommands(input));
        }
        catch (Exception error)
        {
            if (closing || IsDisposed) return;
            output.WriteLine(json.Serialize(new { type = "error", text = error.Message }));
            CloseFromHost();
        }
    }

    static void Configure(CoreWebView2 core, bool messages)
    {
        core.Settings.AreHostObjectsAllowed = false;
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.AreDefaultContextMenusEnabled = false;
        core.Settings.IsWebMessageEnabled = messages;
        core.LaunchingExternalUriScheme += (sender, args) => args.Cancel = true;
        core.DownloadStarting += (sender, args) => args.Cancel = true;
    }

    static string ReadWebMessage(CoreWebView2WebMessageReceivedEventArgs args)
    {
        // A remote catalog may post non-string messages. They are not our IPC
        // protocol and must not become an unhandled COM exception on the UI.
        try { return args.TryGetWebMessageAsString(); }
        catch (ArgumentException) { return null; }
        catch (InvalidOperationException) { return null; }
        catch (COMException) { return null; }
    }

    void SendAction(object value)
    {
        try { output.WriteLine(json.Serialize(new { type = "action", value })); }
        catch (IOException) { } // the parent process may already have gone away
    }

    void UpdateCard()
    {
        if (closing || IsDisposed || details.IsDisposed) return;
        var card = details.CoreWebView2;
        if (card == null || card.Source != CardUrl) return;
        status["viewing_source"] = catalogPage != null;
        status["can_retry"] = canRetry;
        card.PostWebMessageAsJson(json.Serialize(status));
    }

    void ReadCommands(TextReader input)
    {
        for (string line; (line = input.ReadLine()) != null;)
        {
            var message = json.Deserialize<Dictionary<string, object>>(line);
            if (!QueueUi(() => Receive(message))) return;
        }
        QueueUi(CloseFromHost);
    }

    bool QueueUi(Action action)
    {
        if (closing || IsDisposed || !IsHandleCreated) return false;
        try { BeginInvoke(action); return true; }
        catch (InvalidOperationException) { return false; } // handle closed after the check
    }

    void CloseFromHost()
    {
        if (closing || IsDisposed) return;
        hostClosing = true;
        Close();
    }

    void Receive(Dictionary<string, object> message)
    {
        if (closing || IsDisposed) return;
        if ((string)message["type"] == "close") { CloseFromHost(); return; }
        if ((string)message["type"] == "focus")
        {
            FocusPresentation(windowMode);
            return;
        }
        if ((string)message["type"] == "presentation")
        {
            object value;
            if (!message.TryGetValue("mode", out value)) return;
            try { ChangePresentation(ParseWindowMode(Convert.ToString(value)), Flag(message, "focus")); }
            catch (ArgumentException) { hints.SetToolTip(popOut, "Unknown browser window mode."); }
            return;
        }
        if ((string)message["type"] == "target")
        {
            targetLabel.Text = TargetText(message);
            object text;
            hints.SetToolTip(targetLabel, message.TryGetValue("text", out text) ? Convert.ToString(text) : targetLabel.Text);
            targetCanEnd = Flag(message, "preview") || Flag(message, "quarantined");
            UpdateTargetButtons();
            LayoutPane(true);
            return;
        }
        var core = browser.CoreWebView2;
        switch ((string)message["type"])
        {
            case "navigate":
                canRetry = false;
                catalogPage = null;
                callback = new Uri((string)message["callback"]);
                openAfterLoad = message.ContainsKey("open") && IsToneUrl((string)message["open"])
                    ? (string)message["open"] : null;
                core.Navigate((string)message["url"]);
                UpdateCard();
                break;
            case "card":
                cardHtml = (string)message["html"];
                hasTone = (bool)message["has_tone"];
                library = message.ContainsKey("library") && (bool)message["library"];
                LayoutPane(true);
                details.CoreWebView2.Navigate(CardUrl);
                break;
            case "source":
                var source = (string)message["url"];
                if (catalogPage != null || !IsToneUrl(source) || !IsToneUrl(core.Source)) break;
                catalogPage = core.Source;
                core.Navigate(source);
                UpdateCard();
                break;
            case "status":
                status = message;
                UpdateTargetButtons();
                UpdateCard();
                break;
            case "browseError":
                status["text"] = message["text"];
                canRetry = true;
                UpdateCard();
                break;
            case "bounds":
                panelVisible = (bool)message["visible"];
                if (panelVisible)
                    dock = new Rectangle((int)message["x"], (int)message["y"], (int)message["w"], (int)message["h"]);
                LayoutPane(true);
                break;
        }
    }

    void UpdateTargetButtons()
    {
        endPreview.Visible = targetCanEnd;
        endPreview.Enabled = targetCanEnd && !Flag(status, "busy");
    }

    internal static bool Flag(Dictionary<string, object> message, string key)
    {
        object value;
        return message.TryGetValue(key, out value) && value is bool && (bool)value;
    }

    internal static string TargetText(Dictionary<string, object> message)
    {
        object label;
        if (Flag(message, "bound") && message.TryGetValue("label", out label) &&
            !String.IsNullOrEmpty(Convert.ToString(label)))
            return "Selected: " + Convert.ToString(label);
        if (Flag(message, "quarantined")) return "Preview retained · select End preview";
        if (message.TryGetValue("selected", out label) && !String.IsNullOrEmpty(Convert.ToString(label)))
            return "Selected: " + Convert.ToString(label);
        return "Select a NAM block in HX Edit";
    }

    void LayoutPane(bool raiseForNativeChange = false)
    {
        if (closing || IsDisposed) return;
        if (!IsWindow(parent)) { CloseFromHost(); return; }
        if (windowMode == WindowMode.Floating)
        {
            if (floatingHost == null || floatingHost.IsDisposed) return;
            if (floatingHost.WindowState == FormWindowState.Minimized) return;
            var client = floatingHost.ClientSize;
            if (!Visible) Show();
            var floatingBounds = new Rectangle(0, 0, client.Width, client.Height);
            if (floatingBounds != applied)
            {
                SetWindowPos(Handle, IntPtr.Zero, 0, 0, client.Width, client.Height, 0x0010);
                applied = floatingBounds;
            }
            int height = DetailHeight(client.Height - targetBar.Height, library, hasTone);
            if (detailRow.Height != height) detailRow.Height = height;
            bool canDock = panelVisible && dock.Width > 60 && dock.Height > 60;
            if (popOut.Enabled != canDock)
            {
                popOut.Enabled = canDock;
                hints.SetToolTip(popOut, canDock ? "Return to the HX Edit parameter panel" : "Select a NAM block in HX Edit to dock here.");
            }
            return;
        }
        popOut.Enabled = true;
        bool show = panelVisible && !IsIconic(parent) && dock.Width > 60 && dock.Height > 60;
        if (Visible != show) Visible = show;
        if (!show) return;
        var bounds = dock;
        // The NAM library page covers the TONE3000 catalog while it is open.
        var detailHeight = DetailHeight(dock.Height - targetBar.Height, library, hasTone);
        if (detailRow.Height != detailHeight) detailRow.Height = detailHeight;
        if (bounds != applied)
        {
            SetWindowPos(Handle, IntPtr.Zero, bounds.X, bounds.Y, bounds.Width, bounds.Height, 0x0010);
            applied = bounds;
        }
        else if (raiseForNativeChange)
        {
            // A newly selected native block can raise its banner above our
            // sibling HWND without changing geometry. Repair only z-order:
            // SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE. Never focus from polling.
            SetWindowPos(Handle, IntPtr.Zero, 0, 0, 0, 0, 0x0013);
        }
    }

    bool CanNavigate(string address)
    {
        if (IsToneUrl(address)) return true;
        Uri uri;
        return catalogPage == null && callback != null && Uri.TryCreate(address, UriKind.Absolute, out uri) &&
            uri.GetLeftPart(UriPartial.Path) == callback.GetLeftPart(UriPartial.Path);
    }

    static bool IsToneUrl(string address)
    {
        Uri uri;
        return Uri.TryCreate(address, UriKind.Absolute, out uri) && uri.Scheme == "https" && uri.IsDefaultPort &&
            (uri.Host == "www.tone3000.com" || uri.Host == "tone3000.com");
    }
}
