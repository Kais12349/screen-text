using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace ScreenText {
    static class Program {
        internal static readonly string Root = AppDomain.CurrentDomain.BaseDirectory;
        internal static readonly string Cache = Path.Combine(Root, "cache");
        [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);
        [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
        [STAThread] static void Main(string[] args) {
            try { SetProcessDpiAwarenessContext(new IntPtr(-4)); }
            catch { SetProcessDPIAware(); }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try {
                if (Path.GetPathRoot(Root).Equals("C:\\", StringComparison.OrdinalIgnoreCase)) {
                    MessageBox.Show("请将整个工具文件夹放在 D 盘、E 盘等非 C 盘后启动。", "截图取字"); return;
                }
                Directory.CreateDirectory(Cache);
                Environment.SetEnvironmentVariable("TEMP", Cache);
                Environment.SetEnvironmentVariable("TMP", Cache);
                if (args.Length > 0 && args[0] == "--self-test") { SelfTest(); return; }
                using (var mutex = new Mutex(false, "Local\\ScreenTextPortable_E_Tools")) {
                    if (!mutex.WaitOne(0, false)) {
                        MessageBox.Show("截图取字已经运行，请查看任务栏窗口。", "截图取字"); return;
                    }
                    try { Application.Run(new MainForm(args.Length > 0 && File.Exists(args[0]) ? args[0] : null)); } finally { mutex.ReleaseMutex(); }
                }
            } catch (Exception ex) { MessageBox.Show(ex.Message, "截图取字", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        }
        internal static OcrResponse Recognize(Bitmap source) {
            string stem = Path.Combine(Cache, Guid.NewGuid().ToString("N"));
            string input = stem + ".png", output = stem + ".json";
            try {
                source.Save(input, ImageFormat.Png);
                var start = new ProcessStartInfo {
                    FileName = Path.Combine(Root, "runtime\\python.exe"),
                    Arguments = "-B \"" + Path.Combine(Root, "ocr.py") + "\" --input \"" + input + "\" --output \"" + output + "\"",
                    UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Root,
                    RedirectStandardError = true, RedirectStandardOutput = true
                };
                using (var process = Process.Start(start)) {
                    Task<string> error = process.StandardError.ReadToEndAsync();
                    Task<string> stdout = process.StandardOutput.ReadToEndAsync();
                    if (!process.WaitForExit(90000)) {
                        process.Kill(); process.WaitForExit(); throw new Exception("识别超时，请缩小截图范围后重试。");
                    }
                    Task.WaitAll(error, stdout);
                    if (!File.Exists(output)) throw new Exception("无法启动识别引擎。" + error.Result);
                    var result = new JavaScriptSerializer().Deserialize<OcrResponse>(File.ReadAllText(output, Encoding.UTF8));
                    if (!String.IsNullOrEmpty(result.error)) throw new Exception(result.error);
                    return result;
                }
            } finally {
                try { if (File.Exists(input)) File.Delete(input); } catch { }
                try { if (File.Exists(output)) File.Delete(output); } catch { }
            }
        }
        static void SelfTest() {
            string report = Path.Combine(Root, "checks", "ocr-result.json");
            Directory.CreateDirectory(Path.GetDirectoryName(report));
            try {
                using (var sample = new Bitmap(1150, 230)) {
                    using (var g = Graphics.FromImage(sample)) using (var font = new Font("Microsoft YaHei", 26)) {
                        g.Clear(Color.White);
                        g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAliasGridFit;
                        g.DrawString("截图取字：中文测试，欢迎使用！", font, Brushes.Black, 25, 20);
                        g.DrawString("Hello world! OCR test 12345, ABC.", font, Brushes.Black, 25, 85);
                        g.DrawString("价格 99.50；电话：1234567890。", font, Brushes.Black, 25, 150);
                    }
                    sample.Save(Path.Combine(Root, "checks", "ocr-sample.png"), ImageFormat.Png);
                    var result = Recognize(sample);
                    File.WriteAllText(report, new JavaScriptSerializer().Serialize(result), new UTF8Encoding(false));
                }
            } catch (Exception ex) {
                File.WriteAllText(report, new JavaScriptSerializer().Serialize(new OcrResponse { error = ex.ToString() }), new UTF8Encoding(false));
                Environment.ExitCode = 1;
            }
        }
    }
    public class OcrResponse {
        public string text { get; set; } public string language { get; set; } public string error { get; set; }
        public bool code { get; set; } public int low_confidence { get; set; }
    }

    sealed class MainForm : Form {
        [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
        readonly System.Windows.Forms.Timer shortcutTimer = new System.Windows.Forms.Timer { Interval = 15 };
        readonly RichTextBox editor = new RichTextBox();
        readonly Label status = new Label();
        readonly Button capture = new Button(), open = new Button(), copy = new Button();
        readonly PictureBox preview = new PictureBox();
        readonly Label hint = new Label();
        bool busy, selecting, shortcutLatched;
        public MainForm(string initialImage) {
            shortcutTimer.Tick += PollShortcut;
            Text = "截图取字"; Name = "ScreenTextMain";
            Font = new Font("Microsoft YaHei UI", 10F);
            BackColor = Color.FromArgb(246, 248, 251);
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(900, 640); MinimumSize = new Size(700, 500);
            var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(22), ColumnCount = 1, RowCount = 6 };
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 54));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 32));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 52));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 50));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 28));
            Controls.Add(layout);
            layout.Controls.Add(new Label { Text = "截图取字", Font = new Font(Font.FontFamily, 22, FontStyle.Bold), ForeColor = Color.FromArgb(26, 38, 56), AutoSize = true }, 0, 0);
            hint.Text = "框选截图 → 检查和修改文字 → 点击复制"; hint.AutoSize = true;
            hint.ForeColor = Color.FromArgb(91, 105, 123); layout.Controls.Add(hint, 0, 1);
            var actions = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false };
            ConfigureButton(capture, "框选截图", "CaptureButton", 135, true);
            ConfigureButton(open, "打开图片", "OpenImageButton", 115, false);
            actions.Controls.Add(capture); actions.Controls.Add(open);
            actions.Controls.Add(new Label { Text = "快捷键 A + S + D（同时按住）", AutoSize = true, Margin = new Padding(15, 11, 0, 0), ForeColor = Color.FromArgb(91, 105, 123) });
            layout.Controls.Add(actions, 0, 2);
            var content = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, Margin = new Padding(0, 4, 0, 0) };
            content.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 30));
            content.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 70));
            var left = new Panel { Dock = DockStyle.Fill, BackColor = Color.White, Margin = new Padding(0, 0, 12, 0), Padding = new Padding(10) };
            var caption = new Label { Text = "截图预览", Dock = DockStyle.Top, Height = 30, ForeColor = Color.FromArgb(91, 105, 123) };
            preview.Dock = DockStyle.Fill; preview.SizeMode = PictureBoxSizeMode.Zoom; preview.BackColor = Color.FromArgb(241, 244, 248);
            left.Controls.Add(preview); left.Controls.Add(caption); content.Controls.Add(left, 0, 0);
            editor.Name = "ResultEditor"; editor.AccessibleName = "识别结果"; editor.Dock = DockStyle.Fill;
            editor.Font = new Font("Consolas", 12); editor.BorderStyle = BorderStyle.FixedSingle;
            editor.WordWrap = false; editor.ScrollBars = RichTextBoxScrollBars.Both;
            editor.DetectUrls = false; editor.AcceptsTab = true; editor.EnableAutoDragDrop = false;
            editor.TextChanged += delegate { copy.Enabled = !busy && !String.IsNullOrWhiteSpace(editor.Text); };
            content.Controls.Add(editor, 1, 0); layout.Controls.Add(content, 0, 3);
            var bottom = new FlowLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(0, 9, 0, 0) };
            ConfigureButton(copy, "复制全部文字", "CopyButton", 155, true); copy.Enabled = false;
            bottom.Controls.Add(copy);
            bottom.Controls.Add(new Label { Text = "可在右侧直接编辑，确认后再复制", AutoSize = true, Margin = new Padding(12, 10, 0, 0), ForeColor = Color.FromArgb(91, 105, 123) });
            layout.Controls.Add(bottom, 0, 4);
            status.Name = "StatusLabel"; status.AutoSize = true; status.Text = "准备就绪 · 中英文、数字、标点 · 离线识别";
            status.ForeColor = Color.FromArgb(91, 105, 123); status.Margin = new Padding(0, 7, 0, 0); layout.Controls.Add(status, 0, 5);
            capture.Click += async delegate { await CaptureAsync(); };
            open.Click += async delegate { await OpenAsync(); };
            copy.Click += delegate { CopyText(); };
            if (initialImage != null) Shown += async delegate {
                try { using (var loaded = new Bitmap(initialImage)) using (var bitmap = new Bitmap(loaded)) await ReadImageAsync(bitmap); }
                catch (Exception ex) { Report(ex); }
            };
        }
        static void ConfigureButton(Button button, string text, string name, int width, bool primary) {
            button.Text = text; button.Name = name; button.Width = width; button.Height = 36;
            button.FlatStyle = FlatStyle.Flat; button.FlatAppearance.BorderSize = primary ? 0 : 1;
            button.BackColor = primary ? Color.FromArgb(39, 95, 216) : Color.White;
            button.ForeColor = primary ? Color.White : Color.FromArgb(45, 55, 72);
            button.Cursor = Cursors.Hand; button.Margin = new Padding(0, 0, 10, 0);
        }
        protected override void OnHandleCreated(EventArgs e) {
            base.OnHandleCreated(e); shortcutTimer.Start();
        }
        protected override void OnHandleDestroyed(EventArgs e) {
            shortcutTimer.Stop(); base.OnHandleDestroyed(e);
        }
        static bool IsKeyDown(Keys key) { return (GetAsyncKeyState((int)key) & 0x8000) != 0; }
        void PollShortcut(object sender, EventArgs e) {
            bool a = IsKeyDown(Keys.A), s = IsKeyDown(Keys.S), d = IsKeyDown(Keys.D);
            // Require all three keys down; rearm only after all three are released.
            if (!a && !s && !d) { shortcutLatched = false; return; }
            if (!a || !s || !d || shortcutLatched) return;
            shortcutLatched = true;
            if (!busy && !selecting) BeginInvoke(new Action(async () => await CaptureAsync()));
        }
        void SetBusy(bool value) { busy = value; capture.Enabled = open.Enabled = !value; copy.Enabled = !value && !String.IsNullOrWhiteSpace(editor.Text); }
        async Task CaptureAsync() {
            if (busy || selecting) return;
            selecting = true;
            try {
                Hide(); await Task.Delay(220);
                Bitmap selected = null;
                var bounds = SystemInformation.VirtualScreen;
                using (var shot = new Bitmap(bounds.Width, bounds.Height, PixelFormat.Format32bppArgb)) {
                    using (var g = Graphics.FromImage(shot)) g.CopyFromScreen(bounds.Location, Point.Empty, bounds.Size, CopyPixelOperation.SourceCopy);
                    using (var overlay = new SelectionForm(shot, bounds)) {
                        if (overlay.ShowDialog() == DialogResult.OK) selected = shot.Clone(overlay.Selection, PixelFormat.Format32bppArgb);
                    }
                }
                Restore();
                if (selected != null) { using (selected) await ReadImageAsync(selected); }
                else status.Text = "已取消截图，原有文字保留。";
            } catch (Exception ex) { Restore(); Report(ex); }
            finally { selecting = false; }
        }
        void Restore() { Show(); if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal; Activate(); }
        async Task OpenAsync() {
            using (var dialog = new OpenFileDialog { Title = "选择需要识别的图片", Filter = "图片|*.png;*.jpg;*.jpeg;*.bmp;*.tif;*.tiff", InitialDirectory = Program.Root, RestoreDirectory = true }) {
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
                try { using (var loaded = new Bitmap(dialog.FileName)) using (var image = new Bitmap(loaded)) await ReadImageAsync(image); }
                catch (Exception ex) { Report(ex); }
            }
        }
        async Task ReadImageAsync(Bitmap bitmap) {
            SetBusy(true); status.Text = "正在识别，请稍候…";
            var previous = preview.Image; preview.Image = new Bitmap(bitmap); if (previous != null) previous.Dispose();
            try {
                var result = await Task.Run(() => Program.Recognize(bitmap));
                if (IsDisposed) return;
                if (String.IsNullOrWhiteSpace(result.text)) { status.Text = "没有识别到文字。请扩大文字或重新框选，原有文字保留。"; return; }
                editor.Text = result.text; editor.SelectionStart = editor.TextLength;
                status.Text = "识别完成 · " + editor.TextLength + " 字符" + (result.code ? " · 代码排版" : "") + " · 请检查字符和标点后复制"; editor.Focus();
            } catch (Exception ex) { if (!IsDisposed) Report(ex); }
            finally { if (!IsDisposed) SetBusy(false); }
        }
        void CopyText() {
            if (String.IsNullOrWhiteSpace(editor.Text)) return;
            try { Clipboard.SetText(editor.Text, TextDataFormat.UnicodeText); status.Text = "已复制当前文字，可以粘贴了。"; }
            catch (ExternalException) { status.Text = "剪贴板正忙，请稍后再点复制。"; }
        }
        void Report(Exception ex) { status.Text = "操作失败，原有文字保留。"; MessageBox.Show(this, ex.Message, "截图取字", MessageBoxButtons.OK, MessageBoxIcon.Warning); }
        protected override void OnFormClosing(FormClosingEventArgs e) {
            if (busy || selecting) { e.Cancel = true; status.Text = "请等待识别完成；框选时可按 Esc 取消。"; }
            base.OnFormClosing(e);
        }
        protected override void Dispose(bool disposing) {
            if (disposing) { shortcutTimer.Dispose(); if (preview.Image != null) preview.Image.Dispose(); }
            base.Dispose(disposing);
        }
    }

    sealed class SelectionForm : Form {
        readonly Bitmap screenshot;
        Point anchor, current;
        bool dragging;
        public Rectangle Selection { get; private set; }
        public SelectionForm(Bitmap screenshot, Rectangle screenBounds) {
            this.screenshot = screenshot;
            FormBorderStyle = FormBorderStyle.None; StartPosition = FormStartPosition.Manual;
            Bounds = screenBounds; TopMost = true; ShowInTaskbar = false;
            DoubleBuffered = true; Cursor = Cursors.Cross; KeyPreview = true;
            AutoScaleMode = AutoScaleMode.None;
            KeyDown += delegate(object sender, KeyEventArgs e) { if (e.KeyCode == Keys.Escape) { DialogResult = DialogResult.Cancel; Close(); } };
            MouseDown += delegate(object sender, MouseEventArgs e) {
                if (e.Button == MouseButtons.Right) { DialogResult = DialogResult.Cancel; Close(); return; }
                if (e.Button != MouseButtons.Left) return;
                anchor = current = e.Location; dragging = true; Capture = true; Invalidate();
            };
            MouseMove += delegate(object sender, MouseEventArgs e) { if (dragging) { current = Clamp(e.Location); Invalidate(); } };
            MouseUp += delegate(object sender, MouseEventArgs e) {
                if (!dragging || e.Button != MouseButtons.Left) return;
                current = Clamp(e.Location); Selection = Rectangle.FromLTRB(Math.Min(anchor.X, current.X), Math.Min(anchor.Y, current.Y), Math.Max(anchor.X, current.X), Math.Max(anchor.Y, current.Y));
                dragging = false; Capture = false;
                if (Selection.Width < 5 || Selection.Height < 5) { Invalidate(); return; }
                DialogResult = DialogResult.OK; Close();
            };
        }
        Point Clamp(Point p) { return new Point(Math.Max(0, Math.Min(ClientSize.Width, p.X)), Math.Max(0, Math.Min(ClientSize.Height, p.Y))); }
        protected override void OnPaint(PaintEventArgs e) {
            e.Graphics.DrawImageUnscaled(screenshot, 0, 0);
            using (var dim = new SolidBrush(Color.FromArgb(120, 0, 0, 0))) e.Graphics.FillRectangle(dim, ClientRectangle);
            if (dragging) {
                var r = Rectangle.FromLTRB(Math.Min(anchor.X, current.X), Math.Min(anchor.Y, current.Y), Math.Max(anchor.X, current.X), Math.Max(anchor.Y, current.Y));
                if (r.Width > 0 && r.Height > 0) e.Graphics.DrawImage(screenshot, r, r, GraphicsUnit.Pixel);
                using (var pen = new Pen(Color.FromArgb(75, 160, 255), 2)) e.Graphics.DrawRectangle(pen, r);
            }
            var mouse = PointToClient(Cursor.Position);
            int x = Math.Max(8, Math.Min(ClientSize.Width - 350, mouse.X + 18));
            int y = Math.Max(8, Math.Min(ClientSize.Height - 44, mouse.Y + 24));
            using (var brush = new SolidBrush(Color.FromArgb(230, 26, 38, 56))) e.Graphics.FillRectangle(brush, x, y, 338, 34);
            using (var font = new Font("Microsoft YaHei UI", 10)) e.Graphics.DrawString("拖动框选 · 松开识别 · Esc / 右键取消", font, Brushes.White, x + 10, y + 7);
        }
    }
}
