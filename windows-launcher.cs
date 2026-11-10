using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class WindowsLauncher
{
    [STAThread]
    static void Main(string[] args)
    {
        var root = AppDomain.CurrentDomain.BaseDirectory;
        var python = Path.Combine(root, "runtime", "pythonw.exe");
        var entry = Path.Combine(root, "app", "start-hx-edit-nam.pyw");
        try
        {
            if (!File.Exists(python) || !File.Exists(entry))
                throw new FileNotFoundException("Extract the entire HX Edit NAM ZIP before starting the app.");
            var start = new ProcessStartInfo(python, "-B -X utf8 \"" + entry + "\"")
            {
                UseShellExecute = false,
                WorkingDirectory = root
            };
            if (Array.IndexOf(args, "--lt-nam") >= 0) start.Arguments += " --lt-nam";
            var hx = Array.IndexOf(args, "--hx-nam");
            if (hx >= 0)
            {
                if (hx + 1 >= args.Length || (args[hx + 1] != "stomp" && args[hx + 1] != "effects"))
                    throw new ArgumentException("Use --hx-nam stomp or --hx-nam effects.");
                start.Arguments += " --hx-nam " + args[hx + 1];
            }
            using (Process.Start(start)) { }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "HX Edit NAM", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
