using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Threading;
using System.Windows.Forms;

// One Windows program with the portable folder appended: stub, zip, SHA-256, length.
// The first launch unpacks under Local AppData. Later launches start that copy.
internal static class Program
{
    const int HashLength = 32;
    const int LengthLength = 8;

    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        try
        {
            string exePath = Application.ExecutablePath;
            Payload payload = ReadTrailer(exePath);
            string root = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "SuperBerry",
                "runtime");
            Directory.CreateDirectory(root);
            string current = Path.Combine(root, "current");
            string stampPath = Path.Combine(root, "stamp");
            if (!StampMatches(stampPath, payload.Hash) || FindAppRoot(current) == null)
            {
                if (SystemInformation.UserInteractive)
                {
                    Exception failure = null;
                    using (Form window = OpeningWindow())
                    {
                        window.Shown += delegate
                        {
                            ThreadPool.QueueUserWorkItem(delegate
                            {
                                try
                                {
                                    Extract(exePath, payload, root, current);
                                }
                                catch (Exception ex)
                                {
                                    failure = ex;
                                }
                                try
                                {
                                    window.BeginInvoke(new Action(window.Close));
                                }
                                catch (Exception)
                                {
                                    // The window is already gone.
                                }
                            });
                        };
                        window.ShowDialog();
                    }
                    if (failure != null)
                    {
                        throw failure;
                    }
                }
                else
                {
                    Extract(exePath, payload, root, current);
                }
            }
            string appRoot = FindAppRoot(current);
            if (appRoot == null)
            {
                throw new InvalidDataException("SuperBerry could not find its application files.");
            }
            Launch(appRoot);
        }
        catch (Exception ex)
        {
            string message = Friendly(ex);
            try
            {
                string note = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "SuperBerry",
                    "runtime",
                    "open-error.txt");
                Directory.CreateDirectory(Path.GetDirectoryName(note));
                File.WriteAllText(note, message);
            }
            catch (Exception)
            {
                // The window below is the message the user can read.
            }
            if (SystemInformation.UserInteractive)
            {
                try
                {
                    MessageBox.Show(message, "SuperBerry", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                catch (Exception)
                {
                    // No desktop is available to show the message.
                }
            }
        }
    }

    static string Friendly(Exception ex)
    {
        if (ex is IOException || ex is UnauthorizedAccessException)
        {
            return "Close SuperBerry, then start it again.";
        }
        return ex.Message;
    }

    static Form OpeningWindow()
    {
        Form form = new Form();
        form.Text = "SuperBerry";
        form.FormBorderStyle = FormBorderStyle.FixedDialog;
        form.StartPosition = FormStartPosition.CenterScreen;
        form.MaximizeBox = false;
        form.MinimizeBox = false;
        form.ControlBox = false;
        form.TopMost = true;
        form.ClientSize = new System.Drawing.Size(420, 120);
        Label label = new Label();
        label.Text = "Opening SuperBerry…";
        label.Dock = DockStyle.Fill;
        label.TextAlign = System.Drawing.ContentAlignment.MiddleCenter;
        form.Controls.Add(label);
        return form;
    }

    static void Launch(string appRoot)
    {
        string electron = Path.Combine(appRoot, "electron", "electron.exe");
        string app = Path.Combine(appRoot, "app");
        ProcessStartInfo start = new ProcessStartInfo();
        start.FileName = electron;
        start.Arguments = "\"" + app + "\"";
        start.WorkingDirectory = appRoot;
        start.UseShellExecute = false;
        Process.Start(start);
    }

    static void Extract(string exePath, Payload payload, string root, string current)
    {
        using (Mutex gate = new Mutex(false, "Local\\SuperBerrySetup"))
        {
            bool owned = false;
            try
            {
                owned = gate.WaitOne(TimeSpan.FromMinutes(5));
            }
            catch (AbandonedMutexException)
            {
                owned = true;
            }
            if (!owned)
            {
                throw new InvalidOperationException("SuperBerry is still opening.");
            }
            try
            {
                ExtractUnlocked(exePath, payload, root, current);
            }
            finally
            {
                gate.ReleaseMutex();
            }
        }
    }

    static void ExtractUnlocked(string exePath, Payload payload, string root, string current)
    {
        string stage = Path.Combine(root, "stage");
        string tempZip = Path.Combine(root, "payload.zip");
        if (Directory.Exists(stage))
        {
            Directory.Delete(stage, true);
        }
        Directory.CreateDirectory(stage);
        try
        {
            CopyPayload(exePath, payload, tempZip);
            ExtractZip(tempZip, stage);
            string found = FindAppRoot(stage);
            if (found == null)
            {
                throw new InvalidDataException("SuperBerry could not find its application files.");
            }
            if (Directory.Exists(current))
            {
                Directory.Delete(current, true);
            }
            Directory.Move(found, current);
            if (!string.Equals(found, stage, StringComparison.OrdinalIgnoreCase) && Directory.Exists(stage))
            {
                Directory.Delete(stage, true);
            }
            File.WriteAllBytes(Path.Combine(root, "stamp"), payload.Hash);
        }
        finally
        {
            if (File.Exists(tempZip))
            {
                File.Delete(tempZip);
            }
        }
    }

    static void CopyPayload(string exePath, Payload payload, string tempZip)
    {
        byte[] buffer = new byte[65536];
        using (FileStream input = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        using (FileStream output = new FileStream(tempZip, FileMode.Create, FileAccess.Write, FileShare.None))
        using (SHA256 sha = SHA256.Create())
        {
            input.Seek(payload.ZipOffset, SeekOrigin.Begin);
            long left = payload.ZipLength;
            while (left > 0)
            {
                int count = input.Read(buffer, 0, (int)Math.Min(buffer.Length, left));
                if (count <= 0)
                {
                    throw new EndOfStreamException("SuperBerry's package ended early.");
                }
                sha.TransformBlock(buffer, 0, count, null, 0);
                output.Write(buffer, 0, count);
                left -= count;
            }
            sha.TransformFinalBlock(buffer, 0, 0);
            if (!SameBytes(sha.Hash, payload.Hash))
            {
                throw new InvalidDataException("SuperBerry's package is damaged.");
            }
        }
    }

    static void ExtractZip(string tempZip, string stage)
    {
        string stageFull = Path.GetFullPath(stage);
        if (!stageFull.EndsWith(Path.DirectorySeparatorChar.ToString(), StringComparison.Ordinal))
        {
            stageFull += Path.DirectorySeparatorChar;
        }
        using (ZipArchive archive = ZipFile.OpenRead(tempZip))
        {
            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                string name = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                if (name.Length == 0)
                {
                    continue;
                }
                if (name[0] == Path.DirectorySeparatorChar || Path.IsPathRooted(entry.FullName))
                {
                    throw new InvalidDataException("SuperBerry's package contains an unsafe path.");
                }
                string[] parts = name.Split(Path.DirectorySeparatorChar);
                for (int i = 0; i < parts.Length; i++)
                {
                    if (parts[i] == "..")
                    {
                        throw new InvalidDataException("SuperBerry's package contains an unsafe path.");
                    }
                }
                string target = Path.GetFullPath(Path.Combine(stage, name));
                if (!target.StartsWith(stageFull, StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidDataException("SuperBerry's package contains an unsafe path.");
                }
                if (string.IsNullOrEmpty(entry.Name))
                {
                    Directory.CreateDirectory(target);
                    continue;
                }
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                entry.ExtractToFile(target, true);
            }
        }
    }

    static string FindAppRoot(string folder)
    {
        if (File.Exists(Path.Combine(folder, "electron", "electron.exe")))
        {
            return folder;
        }
        if (!Directory.Exists(folder))
        {
            return null;
        }
        string[] children = Directory.GetDirectories(folder);
        for (int i = 0; i < children.Length; i++)
        {
            if (File.Exists(Path.Combine(children[i], "electron", "electron.exe")))
            {
                return children[i];
            }
        }
        return null;
    }

    static bool StampMatches(string stampPath, byte[] hash)
    {
        if (!File.Exists(stampPath))
        {
            return false;
        }
        byte[] stored = File.ReadAllBytes(stampPath);
        return SameBytes(stored, hash);
    }

    static bool SameBytes(byte[] left, byte[] right)
    {
        if (left == null || right == null || left.Length != right.Length)
        {
            return false;
        }
        for (int i = 0; i < left.Length; i++)
        {
            if (left[i] != right[i])
            {
                return false;
            }
        }
        return true;
    }

    static Payload ReadTrailer(string exePath)
    {
        using (FileStream stream = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        {
            if (stream.Length < HashLength + LengthLength + 4)
            {
                throw new InvalidDataException("SuperBerry's package is damaged.");
            }
            stream.Seek(-LengthLength, SeekOrigin.End);
            byte[] lengthBytes = ReadExact(stream, LengthLength);
            long zipLength = BitConverter.ToInt64(lengthBytes, 0);
            long zipOffset = stream.Length - LengthLength - HashLength - zipLength;
            if (zipLength < 4 || zipOffset < 0)
            {
                throw new InvalidDataException("SuperBerry's package is damaged.");
            }
            stream.Seek(zipOffset + zipLength, SeekOrigin.Begin);
            byte[] hash = ReadExact(stream, HashLength);
            stream.Seek(zipOffset, SeekOrigin.Begin);
            byte[] magic = ReadExact(stream, 2);
            if (magic[0] != (byte)'P' || magic[1] != (byte)'K')
            {
                throw new InvalidDataException("SuperBerry's package is damaged.");
            }
            return new Payload(zipOffset, zipLength, hash);
        }
    }

    static byte[] ReadExact(Stream stream, int count)
    {
        byte[] buffer = new byte[count];
        int filled = 0;
        while (filled < count)
        {
            int read = stream.Read(buffer, filled, count - filled);
            if (read <= 0)
            {
                throw new EndOfStreamException("SuperBerry's package ended early.");
            }
            filled += read;
        }
        return buffer;
    }

    sealed class Payload
    {
        internal readonly long ZipOffset;
        internal readonly long ZipLength;
        internal readonly byte[] Hash;

        internal Payload(long zipOffset, long zipLength, byte[] hash)
        {
            ZipOffset = zipOffset;
            ZipLength = zipLength;
            Hash = hash;
        }
    }
}
