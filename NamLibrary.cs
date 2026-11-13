using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// Local-only child pane. No network, device or account APIs.
class NamLibrary : Form
{
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int w, int h, uint flags);
    [DllImport("user32.dll")] static extern int GetWindowLong(IntPtr hwnd, int index);
    [DllImport("user32.dll")] static extern int SetWindowLong(IntPtr hwnd, int index, int value);
    [DllImport("user32.dll")] static extern IntPtr GetWindowDpiAwarenessContext(IntPtr hwnd);
    [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern IntPtr SendMessage(IntPtr hwnd,int msg,IntPtr w,string text);
    readonly IntPtr parent;
    readonly TextWriter output;
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly DarkListView table = new DarkListView();
    readonly Button load = new Button();
    readonly Label status = new Label();
    readonly Panel footer = new Panel();
    readonly Panel toolbar = new Panel();
    readonly TextBox search = new TextBox();
    readonly Button clearSearch = new Button();
    readonly Button categoryFilter = new Button();
    readonly ContextMenuStrip filterMenu = new ContextMenuStrip();
    int categoryIndex;
    readonly Label count = new Label();
    readonly Button changeType = new Button();
    readonly Button expand = new Button();
    readonly Label empty = new Label();
    string sharedSelected = "";
    bool applyingView;
    static readonly string[] ViewFilters={"*","amp","amp-cab","pedal","cab","preamp","pedal-amp","amp-pedal-cab","studio","space","outboard","experimental",""};
    readonly ContextMenuStrip typeMenu = new ContextMenuStrip();
    readonly List<Dictionary<string,object>> allRows = new List<Dictionary<string,object>>();
    bool classificationWaiting;
    static readonly string[] GearCodes={"amp","amp-cab","pedal","cab","preamp","pedal-amp","amp-pedal-cab","studio","space","outboard","experimental"};
    static readonly string[] GearLabels={"Amp only","Amp + cab","Pedal","Cab capture","Preamp","Pedal + amp","Pedal + amp + cab","Studio","Space","Outboard","Experimental"};
    static readonly string[] FilterCodes={"","amp-only","amp+cab","pedals","cab","preamp","pedal+amp","pedal+amp+cab","studio","space","outboard","experimental","unidentified"};
    readonly Panel grip = new Panel();
    readonly ToolTip gripHint = new ToolTip();
    readonly Timer timer = new Timer { Interval = 250 };
    readonly Timer resizeTimer = new Timer { Interval = 33 };
    readonly Stopwatch resizeClock = Stopwatch.StartNew();
    readonly LatestResize resizeQueue = new LatestResize();
    readonly RowsComparer sorter = new RowsComparer();
    bool applying;
    bool fittingColumns;
    bool waiting;
    bool busy;
    bool targetReady;
    bool shown;
    Rectangle dock;
    string rowSignature;
    long noticeUntil;
    readonly LoadConfirmation loadConfirmation = new LoadConfirmation();

    // Presentation only. Never authorizes a load or changes verification evidence.
    class LoadConfirmation {
        string identity;
        int slot;
        long receiptEpoch;
        bool sawRefresh;
        public bool Pending {get;private set;}
        public bool Failed {get;private set;}
        public string Message {get;private set;}
        public void Clear(){Pending=false;Failed=false;Message="";identity="";slot=0;sawRefresh=false;receiptEpoch=-1;}
        public void Begin(string id,int destination,long epoch=-1){
            Clear();identity=id;slot=destination;receiptEpoch=epoch;Pending=true;
            Message="Loaded in IR slot "+slot+" — verifying library…";
        }
        public int PendingSlot(string id){return Pending&&identity==id?slot:0;}
        public void ObserveEmpty(long epoch,IEnumerable emptySlots){
            if(!Pending||receiptEpoch<0||epoch<receiptEpoch||emptySlots==null)return;
            foreach(var value in emptySlots)if(value is int&&(int)value==slot){
                Pending=false;Failed=true;Message="IR slot "+slot+" is now empty in HX Edit.";return;
            }
        }
        public void Observe(string state,IEnumerable rows){
            if(!Pending)return;
            if(state=="waiting-for-editor"||state=="verifying"){sawRefresh=true;return;}
            if(state=="complete"||state=="complete-partial"){
                // Require the same capture AND destination, not merely a finished scan.
                if(rows==null)return; // Hidden pane: wait for its next full snapshot.
                foreach(var value in rows){
                    var row=value as Dictionary<string,object>;object slots;
                    if(row==null||TextOf(row,"contentId")!=identity||TextOf(row,"deviceStatus")!="verified"||
                       !row.TryGetValue("verifiedSlots",out slots)||!(slots is IEnumerable))continue;
                    foreach(var candidate in (IEnumerable)slots)if(candidate is int&&(int)candidate==slot){
                        Pending=false;Message="IR slot "+slot+" verified. Preset remains unsaved.";return;
                    }
                }
                // A queued pre-import complete snapshot must not report a failure.
                if(!sawRefresh)return;
                Pending=false;Failed=true;
                Message="Loaded in IR slot "+slot+"; verification did not confirm it.";return;
            }
            if(state.Length==0||state=="inactive")return;
            Pending=false;Failed=true;
            Message="Loaded in IR slot "+slot+(state=="disconnected"?"; device disconnected before verification.":"; library verification interrupted.");
        }
    }

    void UpdateLoadConfirmation(Dictionary<string,object> state){
        if(!loadConfirmation.Pending)return;
        string previous=loadConfirmation.Message;object rows,emptySlots;long epoch;
        if(Int64.TryParse(TextOf(state,"slotEpoch"),out epoch))
            loadConfirmation.ObserveEmpty(epoch,state.TryGetValue("emptySlots",out emptySlots)?emptySlots as IEnumerable:null);
        loadConfirmation.Observe(TextOf(state,"discoveryStatus"),state.TryGetValue("rows",out rows)?rows as IEnumerable:null);
        // Do not replace a newer error or another operation's feedback.
        if(status.Text==previous||status.Text.Length==0){
            status.Text=loadConfirmation.Message;
            noticeUntil=loadConfirmation.Pending||loadConfirmation.Failed?0:resizeClock.ElapsedMilliseconds+4000;
        }
        if(!loadConfirmation.Pending)RenderRows();
    }
    const int ClipChildren = 0x02000000;
    bool addedParentClipping;
    bool resizing;
    bool resizeAvailable;
    string resizeUnavailable = "Sidebar layout has not been verified yet.";
    const int GripWidth = 10;
    int dragX, dragWidth, sidebarWidth = 360;

    // Paint the native header's otherwise unowned trailing area during tracking,
    // not only after a column-change notification arrives on mouse release.
    class DarkListView : ListView {
        [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr hwnd,int msg,IntPtr w,IntPtr l);
        readonly DarkHeader header=new DarkHeader();
        public DarkListView(){DoubleBuffered=true;}
        protected override void OnHandleCreated(EventArgs e){
            base.OnHandleCreated(e);
            var hwnd=SendMessage(Handle,0x101f,IntPtr.Zero,IntPtr.Zero); // LVM_GETHEADER
            if(hwnd!=IntPtr.Zero)header.AssignHandle(hwnd);
        }
        protected override void OnHandleDestroyed(EventArgs e){header.ReleaseHandle();base.OnHandleDestroyed(e);}
    }
    // Native selected-row painting substitutes the system highlight text color,
    // hiding pending/verified slot colors. Own the cells as well as the header.
    static void DrawLibraryCell(DrawListViewSubItemEventArgs args,Font font,bool focused) {
        args.DrawDefault=false;
        // Subitem custom-draw state can carry Selected for unrelated rows.
        // The list item's actual selection is authoritative for hit testing,
        // actions and painting; never OR in the draw-event hint.
        bool selected=args.Item.Selected;
        Color background=selected?(focused?Color.FromArgb(63,45,60):Color.FromArgb(48,48,48)):Color.FromArgb(18,18,18);
        Color foreground=args.ColumnIndex==2?args.SubItem.ForeColor:Color.Gainsboro;
        using(var brush=new SolidBrush(background))args.Graphics.FillRectangle(brush,args.Bounds);
        var textBounds=args.Bounds;textBounds.X+=4;textBounds.Width=Math.Max(0,textBounds.Width-8);
        TextRenderer.DrawText(args.Graphics,args.SubItem.Text,font,textBounds,foreground,background,
            TextFormatFlags.Left|TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis|TextFormatFlags.NoPrefix);
    }
    class DarkHeader : NativeWindow {
        [StructLayout(LayoutKind.Sequential)] struct Rect {public int Left,Top,Right,Bottom;}
        [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr hwnd,out Rect rect);
        [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr hwnd,int msg,IntPtr w,ref Rect rect);
        [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr hwnd,int msg,IntPtr w,IntPtr l);
        static readonly Color Background=Color.FromArgb(42,42,42);
        void FillTail(Graphics g){
            Rect client;if(!GetClientRect(Handle,out client))return;
            int count=SendMessage(Handle,0x1200,IntPtr.Zero,IntPtr.Zero).ToInt32(); // HDM_GETITEMCOUNT
            if(count<0||count>16)return;
            int right=0;
            for(int i=0;i<count;i++){
                Rect item=new Rect();
                if(SendMessage(Handle,0x1207,new IntPtr(i),ref item)==IntPtr.Zero)return; // HDM_GETITEMRECT
                right=Math.Max(right,item.Right);
            }
            if(right<client.Right)using(var brush=new SolidBrush(Background))
                g.FillRectangle(brush,Math.Max(0,right),0,client.Right-Math.Max(0,right),client.Bottom);
        }
        protected override void WndProc(ref Message m){
            if(m.Msg==0x14&&m.WParam!=IntPtr.Zero){ // WM_ERASEBKGND
                Rect r;if(GetClientRect(Handle,out r))using(var g=Graphics.FromHdc(m.WParam))using(var b=new SolidBrush(Background))
                    g.FillRectangle(b,0,0,r.Right,r.Bottom);
                m.Result=new IntPtr(1);return;
            }
            base.WndProc(ref m);
            if(m.Msg==0xf){using(var g=Graphics.FromHwnd(Handle))FillTail(g);}
            else if((m.Msg==0x317||m.Msg==0x318)&&m.WParam!=IntPtr.Zero){using(var g=Graphics.FromHdc(m.WParam))FillTail(g);}
        }
    }

    // One request in flight; pointer motion replaces the queued width, never
    // accumulates native layout commands. No device communication is involved.
    public class LatestResize {
        public bool InFlight, Failed;
        public int? Pending, LastApplied;
        public int Sent;
        long nextSend, deadline;
        public void Offer(int width){if(!Failed)Pending=Math.Max(300,Math.Min(600,width));}
        public int? Take(long now,bool final){
            if(Failed)return null;
            if(InFlight){if(now>=deadline){Failed=true;Pending=null;}return null;}
            if(!Pending.HasValue)return null;
            if(Pending==LastApplied){Pending=null;return null;}
            if(!final&&now<nextSend)return null;
            Sent=Pending.Value;Pending=null;InFlight=true;deadline=now+2000;nextSend=now+33;
            return Sent;
        }
        public void Complete(bool success){
            if(!InFlight||Failed)return;
            InFlight=false;
            if(success)LastApplied=Sent;else{Failed=true;Pending=null;}
        }
        public void CancelPending(){Pending=null;}
    }

    static string TextOf(Dictionary<string,object> value, string key) {
        object result;
        return value.TryGetValue(key, out result) && result != null ? Convert.ToString(result) : "";
    }
    static bool Flag(Dictionary<string,object> value, string key) {
        object result;
        if(!value.TryGetValue(key, out result)||result==null)return false;
        // 1 as well as true: a native 0/1 once reached here as a number and read as false.
        return result is bool ? (bool)result : result is int && (int)result==1;
    }
    public static string[] DateLabel(Dictionary<string,object> row) {
        DateTimeOffset parsed;
        if (DateTimeOffset.TryParse(TextOf(row, "addedAt"), CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
            return new[]{parsed.LocalDateTime.ToString("MMM d, yyyy", CultureInfo.InvariantCulture), parsed.ToString("yyyy-MM-dd"), "Added to library"};
        object evidence;
        if (row.TryGetValue("dateEvidence", out evidence) && evidence is Dictionary<string,object>) {
            var item = (Dictionary<string,object>)evidence;
            DateTime day;
            if (DateTime.TryParseExact(TextOf(item,"day"),"yyyy-MM-dd",CultureInfo.InvariantCulture,DateTimeStyles.None,out day)) {
                bool exact = TextOf(item,"kind") == "added";
                string kind=TextOf(item,"kind");
                string evidenceText=exact?"Recovered added date; day precision":kind=="file-modified"?
                    "File modified date; estimated library date, not a confirmed download. Copies/clones can change it.":kind=="file-created"?
                    "File created date; estimated library date, not a confirmed download. Copies/clones can change it.":
                    "Present in a retained library record by this date; exact added date unavailable";
                return new[]{day.ToString("MMM d, yyyy",CultureInfo.InvariantCulture), day.ToString("yyyy-MM-dd"),evidenceText};
            }
        }
        return new[]{"—", "", "No dated library record available"};
    }
    public class RowsComparer : IComparer {
        public int Column;
        public bool Descending;
        static int FirstSlot(string value) {
            int slot;
            return Int32.TryParse((value??"").Split(new[]{',',' '})[0],out slot)?slot:0;
        }
        public int Compare(object left, object right) {
            var a=(ListViewItem)left; var b=(ListViewItem)right;
            string av=Column==3 ? a.SubItems[3].Tag as string : a.SubItems[Column].Text;
            string bv=Column==3 ? b.SubItems[3].Tag as string : b.SubItems[Column].Text;
            // Missing dates stay last in either direction.
            if (Column==3 && String.IsNullOrEmpty(av) != String.IsNullOrEmpty(bv)) return String.IsNullOrEmpty(av)?1:-1;
            int cmp;
            if(Column==2){
                int ai=FirstSlot(av),bi=FirstSlot(bv);
                if((ai==0)!=(bi==0))return ai==0?1:-1;
                cmp=ai.CompareTo(bi);
            }else cmp=StringComparer.OrdinalIgnoreCase.Compare(av,bv);
            if(cmp==0) cmp=StringComparer.Ordinal.Compare(a.Name,b.Name);
            return Descending ? -cmp : cmp;
        }
    }
    NamLibrary(IntPtr parent, TextWriter output) {
        this.parent=parent; this.output=output;
        TopLevel=false; FormBorderStyle=FormBorderStyle.None; ShowInTaskbar=false;
        Size=new Size(1,1);StartPosition=FormStartPosition.Manual;
        AutoScaleMode=AutoScaleMode.None; BackColor=Color.FromArgb(18,18,18);
        Text="NAM Library";
        search.AccessibleName="Search captures";search.BackColor=Color.FromArgb(35,35,35);search.ForeColor=Color.Gainsboro;
        search.BorderStyle=BorderStyle.FixedSingle;
        search.HandleCreated+=(sender,args)=>SendMessage(search.Handle,0x1501,new IntPtr(1),"Search captures…");
        gripHint.SetToolTip(search,"Search capture names, aliases and types");
        search.TextChanged+=(sender,args)=>{RenderRows();EmitView();};
        search.KeyDown+=(sender,args)=>{if(args.KeyCode==Keys.Escape){search.Clear();args.SuppressKeyPress=true;}};
        clearSearch.Text="×";clearSearch.AccessibleName="Clear search";clearSearch.FlatStyle=FlatStyle.Flat;clearSearch.ForeColor=Color.Silver;
        clearSearch.Click+=(sender,args)=>{search.Clear();search.Focus();};
        categoryFilter.AccessibleName="Capture type filter";
        categoryFilter.BackColor=Color.FromArgb(35,35,35);categoryFilter.ForeColor=Color.Gainsboro;
        categoryFilter.FlatStyle=FlatStyle.Flat;categoryFilter.TextAlign=ContentAlignment.MiddleLeft;categoryFilter.Text="All types ▾";
        categoryFilter.Click+=(sender,args)=>{
            filterMenu.Items.Clear();
            for(int i=0;i<FilterCodes.Length;i++){
                int index=i;var item=new ToolStripMenuItem(FilterLabel(i)){Checked=i==categoryIndex};
                item.Click+=(s,e)=>SetFilter(index);filterMenu.Items.Add(item);
            }
            filterMenu.Show(categoryFilter,new Point(0,categoryFilter.Height));
        };
        foreach(var menu in new[]{filterMenu,typeMenu}){menu.BackColor=Color.FromArgb(35,35,35);menu.ForeColor=Color.Gainsboro;menu.Renderer=new ToolStripSystemRenderer();}
        count.ForeColor=Color.Silver;count.TextAlign=ContentAlignment.MiddleRight;
        toolbar.Controls.AddRange(new Control[]{search,clearSearch,categoryFilter,count});
        changeType.Text="Type…";changeType.SetBounds(146,6,76,27);changeType.FlatStyle=FlatStyle.Flat;changeType.ForeColor=Color.Silver;
        changeType.Enabled=false;changeType.Click+=(sender,args)=>ShowTypeMenu();
        table.View=View.Details; table.FullRowSelect=true;
        table.MultiSelect=false; table.HideSelection=false; table.ShowItemToolTips=true;
        table.BackColor=BackColor; table.ForeColor=Color.Gainsboro;
        table.Font=new Font("Segoe UI",9); table.BorderStyle=BorderStyle.None;
        table.OwnerDraw=true;
        table.DrawColumnHeader+=(sender,args)=>{
            using(var brush=new SolidBrush(Color.FromArgb(42,42,42))) args.Graphics.FillRectangle(brush,args.Bounds);
            TextRenderer.DrawText(args.Graphics,args.Header.Text,table.Font,args.Bounds,Color.Gainsboro,
                TextFormatFlags.Left|TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis);
        };
        table.DrawItem+=(sender,args)=>args.DrawDefault=false;
        table.DrawSubItem+=(sender,args)=>DrawLibraryCell(args,table.Font,table.Focused);
        table.GotFocus+=(sender,args)=>table.Invalidate();
        table.LostFocus+=(sender,args)=>table.Invalidate();
        table.ItemSelectionChanged+=(sender,args)=>table.Invalidate(args.Item.Bounds);
        table.Columns.Add("Name",170); table.Columns.Add("Type",78);
        table.Columns.Add("IR slot",50);table.Columns.Add("Date",100);
        table.ColumnWidthChanged+=(sender,args)=>FillHeader();
        table.ColumnWidthChanging+=(sender,args)=>{
            int minimum=args.ColumnIndex==2?44:50;
            if(args.NewWidth<minimum){args.Cancel=true;args.NewWidth=minimum;}
        };
        table.ListViewItemSorter=sorter;
        table.ColumnClick+=(sender,args)=>{
            sorter.Descending=sorter.Column==args.Column && !sorter.Descending;
            sorter.Column=args.Column; table.Sort();
            string[] labels={"Name","Type","IR slot","Date"};
            for(int i=0;i<labels.Length;i++) table.Columns[i].Text=labels[i]+(i==sorter.Column?(sorter.Descending?" ▼":" ▲"):"");
            EmitView();
        };
        table.SelectedIndexChanged+=(sender,args)=>{if(!applying){if(Selected()!=null)sharedSelected=TextOf(Selected(),"contentId");RefreshLoad();EmitView();}};
        load.Text="Select a capture"; load.SetBounds(8,6,130,27); load.Enabled=false;
        load.BackColor=LoadBlocked; load.ForeColor=Color.Gray; load.FlatStyle=FlatStyle.Flat;
        // A visible response to hover and press: the owner could not tell a click had registered.
        load.FlatAppearance.BorderColor=Color.FromArgb(120,90,116);
        load.FlatAppearance.MouseOverBackColor=Color.FromArgb(128,58,118);
        load.FlatAppearance.MouseDownBackColor=Color.FromArgb(170,84,158);
        load.Paint+=(sender,args)=>{
            if(load.Enabled)return;
            // Disabled (nothing selected, or a request in flight) must look disabled.
            var inside=Rectangle.Inflate(load.ClientRectangle,-1,-1);
            using(var brush=new SolidBrush(LoadBlocked))args.Graphics.FillRectangle(brush,inside);
            TextRenderer.DrawText(args.Graphics,load.Text,load.Font,inside,Color.Gray,
                TextFormatFlags.HorizontalCenter|TextFormatFlags.VerticalCenter);
        };
        load.Click+=(sender,args)=>RequestCapture(false);
        table.DoubleClick+=(sender,args)=>RequestCapture(true);
        status.ForeColor=Color.Silver; status.Text="";
        status.AutoEllipsis=true;
        status.TextChanged+=(sender,args)=>{gripHint.SetToolTip(status,status.Text);LayoutPane();};
        footer.Controls.Add(load); footer.Controls.Add(status);footer.Controls.Add(changeType);
        expand.Text="↗";expand.AccessibleName="Expand NAM library";expand.FlatStyle=FlatStyle.Flat;expand.ForeColor=Color.Silver;
        gripHint.SetToolTip(expand,"Expand this library — same captures, selection and filters");
        expand.Click+=(sender,args)=>output.WriteLine("{\"type\":\"action\",\"action\":\"expand\"}");footer.Controls.Add(expand);
        empty.ForeColor=Color.Silver;empty.BackColor=BackColor;empty.TextAlign=ContentAlignment.TopLeft;
        empty.Padding=new Padding(8,8,8,8);empty.Text="No captures kept on this PC. Browse TONE3000 and choose Save to PC, or import a NAM capture.";
        grip.BackColor=Color.FromArgb(48,48,48);grip.Cursor=Cursors.VSplit;
        grip.Paint+=(sender,args)=>{
            using(var pen=new Pen(resizeAvailable?Color.Gray:Color.DimGray))
                args.Graphics.DrawLine(pen,GripWidth/2,Math.Max(4,grip.Height/2-20),GripWidth/2,grip.Height/2+20);
        };
        grip.MouseDown+=(sender,args)=>{
            if(args.Button!=MouseButtons.Left)return;
            if(!resizeAvailable||resizeQueue.Failed){status.Text=resizeQueue.Failed?"Resize stopped. Reopen the editor before trying again.":resizeUnavailable;return;}
            resizing=true;dragX=grip.PointToScreen(args.Location).X;dragWidth=sidebarWidth;grip.Capture=true;
        };
        grip.MouseMove+=(sender,args)=>{
            if(resizing){
                int width=DragWidth(dragWidth,dragX,grip.PointToScreen(args.Location).X);
                resizeQueue.Offer(width);PumpResize(false);
            }
        };
        grip.MouseUp+=(sender,args)=>{
            if(args.Button!=MouseButtons.Left||!resizing)return;
            int width=DragWidth(dragWidth,dragX,grip.PointToScreen(args.Location).X);
            resizing=false;grip.Capture=false;RequestWidth(width);
        };
        grip.MouseCaptureChanged+=(sender,args)=>{if(!grip.Capture&&resizing){resizing=false;resizeQueue.CancelPending();}};
        grip.DoubleClick+=(sender,args)=>{resizing=false;if(resizeAvailable)RequestWidth(360);else status.Text=resizeUnavailable;};
        Controls.Add(table); Controls.Add(empty); Controls.Add(footer);Controls.Add(grip);Controls.Add(toolbar);
        Resize+=(sender,args)=>LayoutPane();
        LayoutPane();
        timer.Tick+=(sender,args)=>{
            if(!IsWindow(parent)){timer.Stop(); Environment.Exit(0); return;}
            if(noticeUntil>0&&resizeClock.ElapsedMilliseconds>=noticeUntil){noticeUntil=0;status.Text="";}
            bool expected=shown && !IsIconic(parent);if(Visible!=expected)Visible=expected;
            if(Visible&&Bounds!=dock) SetWindowPos(Handle,IntPtr.Zero,dock.X,dock.Y,dock.Width,dock.Height,0x10);
        };
        resizeTimer.Tick+=(sender,args)=>PumpResize(false);
        FormClosed+=(sender,args)=>{
            timer.Dispose();
            resizeTimer.Dispose();
            gripHint.Dispose();
            typeMenu.Dispose();
            filterMenu.Dispose();
            if(addedParentClipping&&IsWindow(parent))SetWindowLong(parent,-16,GetWindowLong(parent,-16)&~ClipChildren);
        };
    }
    void RequestWidth(int width){
        resizeQueue.Offer(width);PumpResize(true);
    }
    void PumpResize(bool final){
        bool failed=resizeQueue.Failed;
        int? width=resizeQueue.Take(resizeClock.ElapsedMilliseconds,final);
        if(!failed&&resizeQueue.Failed){resizing=false;grip.Capture=false;status.Text="Resize confirmation timed out. Reopen the editor before trying again.";}
        if(width.HasValue)output.WriteLine(json.Serialize(new{type="action",action="resize",width=width.Value}));
    }
    static int DragWidth(int width,int start,int end){return (int)Math.Max(300L,Math.Min(600L,(long)width+end-start));}
    void LayoutPane(){
        int width=Math.Max(1,ClientSize.Width-GripWidth),height=Math.Max(1,ClientSize.Height);
        int footerHeight=Math.Min(status.Text.Length>0?62:38,height);
        int top=Math.Min(60,Math.Max(0,height-footerHeight));
        toolbar.SetBounds(0,0,width,top);
        search.SetBounds(8,5,Math.Max(1,width-46),23);clearSearch.SetBounds(width-32,4,25,24);
        categoryFilter.SetBounds(8,32,Math.Max(1,width-82),23);count.SetBounds(width-72,32,64,23);
        table.SetBounds(0,top,width,Math.Max(0,height-footerHeight-top));
        footer.SetBounds(0,height-footerHeight,width,footerHeight);
        expand.SetBounds(Math.Max(226,width-34),6,27,27);
        empty.SetBounds(0,top+25,width,Math.Max(0,height-footerHeight-top-25));
        status.SetBounds(8,36,Math.Max(1,width-16),Math.Max(1,footerHeight-40));
        grip.SetBounds(width,0,GripWidth,height);
        FitColumns();
    }
    void FitColumns(){
        if(table.Columns.Count!=4||fittingColumns)return;
        fittingColumns=true;
        try{table.Columns[0].Width=Math.Max(60,table.ClientSize.Width-table.Columns[1].Width-table.Columns[2].Width-table.Columns[3].Width);}
        finally{fittingColumns=false;}
        FillHeader();
    }
    void FillHeader(){
        if(table.Columns.Count!=4||fittingColumns)return;
        // DarkHeader paints unused space, including during tracking. Growing the
        // Date column here made widths accumulate after expanding/collapsing the
        // sidebar and squeezed the Name column down to its minimum permanently.
        table.Invalidate();
    }
    static string FloorLabel(Dictionary<string,object> row){
        string state=TextOf(row,"deviceStatus");
        if(state=="preview")return "128 · P";
        object value;
        if((state=="verified"||state=="checking")&&row.TryGetValue(state=="checking"?"displaySlots":"verifiedSlots",out value)&&value is IEnumerable){
            var slots=new SortedSet<int>();
            foreach(var slot in (IEnumerable)value){
                int n;if(slot is int)n=(int)slot;else continue;
                if(n>=1&&n<128)slots.Add(n);
            }
            // While a refresh runs the last accepted slots stay as they are (in grey, not green).
            // Only the row whose slot is actually changing is marked, in RenderRows.
            if(slots.Count>0)return String.Join(", ",slots);
        }
        return ""; // Unknown installation is not a missing capture; details explain verification.
    }
    Dictionary<string,object> Selected(){return table.SelectedItems.Count==1 ? table.SelectedItems[0].Tag as Dictionary<string,object> : null;}
    static string FilterLabel(int index){return index==0?"All types":index==FilterCodes.Length-1?"Needs classification":GearLabels[index-1];}
    void SetFilter(int index){
        if(index<0||index>=FilterCodes.Length)return;
        categoryIndex=index;categoryFilter.Text=FilterLabel(index)+" ▾";RenderRows();EmitView();
    }
    void EmitView(){
        if(applying||applyingView)return;
        output.WriteLine(json.Serialize(new{type="action",action="view",view=new{
            selected=sharedSelected.Length==0?null:sharedSelected,query=search.Text,
            filter=ViewFilters[categoryIndex],sortKey=new[]{"name","gear","irSlots","added"}[sorter.Column],
            sortDirection=sorter.Descending?-1:1}}));
    }
    void ApplyView(Dictionary<string,object> view){
        if(view==null)return;
        applyingView=true;
        try{
            sharedSelected=TextOf(view,"selected");
            search.Text=TextOf(view,"query");
            int index=Array.IndexOf(ViewFilters,TextOf(view,"filter"));
            if(index>=0){categoryIndex=index;categoryFilter.Text=FilterLabel(index)+" ▾";}
            int column=Array.IndexOf(new[]{"name","gear","irSlots","added"},TextOf(view,"sortKey"));
            if(column>=0)sorter.Column=column;
            sorter.Descending=TextOf(view,"sortDirection")=="-1";
            string[] labels={"Name","Type","IR slot","Date"};
            for(int i=0;i<labels.Length;i++)table.Columns[i].Text=labels[i]+(i==sorter.Column?(sorter.Descending?" ▼":" ▲"):"");
            RenderRows();
        }finally{applyingView=false;}
    }
    static bool Matches(Dictionary<string,object> row,string query,string group){
        if(group.Length>0&&TextOf(row,"category")!=group)return false;
        query=query.Trim();if(query.Length==0)return true;
        var text=new StringBuilder(TextOf(row,"title")+" "+TextOf(row,"categoryLabel"));object aliases;
        if(row.TryGetValue("aliases",out aliases)&&aliases is IEnumerable&&!(aliases is string))
            foreach(var alias in (IEnumerable)aliases)text.Append(" ").Append(alias);
        string haystack=text.ToString();
        foreach(string word in query.Split((char[])null,StringSplitOptions.RemoveEmptyEntries))
            if(haystack.IndexOf(word,StringComparison.OrdinalIgnoreCase)<0)return false;
        return true;
    }
    static string CategoryDetail(Dictionary<string,object> row){
        var text=new StringBuilder(TextOf(row,"categoryOverride").Length>0?"Your type override. ":"Source-derived type. ");
        text.Append("Type… changes the local label only. Sources:");object evidence;
        if(row.TryGetValue("categoryEvidence",out evidence)&&evidence is IEnumerable)
            foreach(var item in (IEnumerable)evidence){var e=item as Dictionary<string,object>;if(e!=null)text.Append("\n").Append(TextOf(e,"source")).Append(": ").Append(TextOf(e,"gear"));}
        return text.ToString();
    }
    void RenderRows(){
        string selected=sharedSelected.Length>0?sharedSelected:Selected()==null?"":TextOf(Selected(),"contentId");
        string filter=FilterCodes[categoryIndex];
        applying=true;table.BeginUpdate();
        try{
            table.Items.Clear();
            foreach(var row in allRows){
                if(!Matches(row,search.Text,filter))continue;
                var date=DateLabel(row);
                var entry=new ListViewItem(CaptureTitle(row)){Name=TextOf(row,"contentId"),Tag=row,
                    ToolTipText=TextOf(row,"title")+"\n"+TextOf(row,"fileDetail")+"\n"+TextOf(row,"locationDetail")+"\n"+date[2]+"\n"+CategoryDetail(row)};
                entry.SubItems.Add(TextOf(row,"categoryLabel"));entry.UseItemStyleForSubItems=false;
                int provisional=loadConfirmation.PendingSlot(TextOf(row,"contentId"));
                object pendingValue;
                if(provisional==0&&row.TryGetValue("pendingSlots",out pendingValue)&&pendingValue is IEnumerable)
                    foreach(var pending in (IEnumerable)pendingValue)if(pending is int&&(int)pending>=1&&(int)pending<128){provisional=(int)pending;break;}
                var deviceCell=entry.SubItems.Add(provisional>0?provisional+" …":FloorLabel(row));
                deviceCell.ForeColor=provisional>0?Color.Goldenrod:
                    TextOf(row,"deviceStatus")=="verified"?Color.LightGreen:Color.Silver;
                if(provisional>0)entry.ToolTipText+="\nLoaded in IR slot "+provisional+"; content verification pending.";
                entry.SubItems.Add(date[0]).Tag=date[1];table.Items.Add(entry);
                if(entry.Name==selected)entry.Selected=true;
            }
            table.Sort();count.Text=table.Items.Count+" / "+allRows.Count;
            empty.Visible=table.Items.Count==0;
            empty.Text=allRows.Count==0?"No captures kept on this PC. Browse TONE3000 and choose Save to PC, or import a NAM capture.":"No captures match this search or type. Clear the search or choose All types.";
        }finally{table.EndUpdate();applying=false;}
        FitColumns();RefreshLoad();
    }
    void ShowTypeMenu(){
        var row=Selected();if(row==null||classificationWaiting)return;
        typeMenu.Items.Clear();
        var reset=new ToolStripMenuItem("Use source metadata"){Checked=TextOf(row,"categoryOverride").Length==0};
        reset.Click+=(sender,args)=>RequestCategory(null);typeMenu.Items.Add(reset);typeMenu.Items.Add(new ToolStripSeparator());
        for(int i=0;i<GearCodes.Length;i++){
            string code=GearCodes[i];var item=new ToolStripMenuItem(GearLabels[i]){Checked=TextOf(row,"categoryOverride")==code};
            item.Click+=(sender,args)=>RequestCategory(code);typeMenu.Items.Add(item);
        }
        typeMenu.Show(changeType,new Point(0,changeType.Height));
    }
    void RequestCategory(string gear){
        var row=Selected();if(row==null||classificationWaiting||TextOf(row,"categoryRevision").Length!=64)return;
        classificationWaiting=true;RefreshLoad();status.Text="Saving local type…";noticeUntil=0;
        output.WriteLine(json.Serialize(new{type="action",action="classify",contentId=TextOf(row,"contentId"),revision=TextOf(row,"categoryRevision"),gear=gear}));
    }
    static string CaptureTitle(Dictionary<string,object> row){
        string file=TextOf(row,"fileStatus");
        return TextOf(row,"title")+(file=="missing"?" [file missing]":file=="invalid"?" [file unavailable]":"");
    }
    static bool HasShownSlots(Dictionary<string,object> row){
        object value;
        if(row.TryGetValue("displaySlots",out value)&&value is IEnumerable)foreach(var slot in (IEnumerable)value)return true;
        return false;
    }
    public static string LoadLabel(Dictionary<string,object> row){
        if(row==null)return "Select a capture";
        string state=TextOf(row,"deviceStatus");
        // A capture whose slot is being re-checked is still an installed capture: never offer
        // to load a second copy of it while the check runs.
        if(state=="verified"||(state=="checking"&&HasShownSlots(row)))return "Use on Floor";
        string file=TextOf(row,"fileStatus");
        return file=="missing"?"File missing":file=="invalid"?"File unavailable":file=="unchecked"?"File not checked":"Load on Floor";
    }
    // Why the selected capture cannot be loaded or used right now; "" when it can.
    public static string BlockedReason(Dictionary<string,object> row,bool waiting,bool busy,bool pendingConfirmation,bool targetReady){
        if(row==null)return "Select a capture in the list first.";
        if(waiting)return "Still working on the previous request…";
        if(pendingConfirmation)return "Waiting for the last loaded slot to be verified…";
        string file=TextOf(row,"fileStatus");
        // A verified installed copy can be used without its file on this PC.
        if((file=="missing"||file=="invalid")&&TextOf(row,"deviceStatus")!="verified")return "This capture's file is not available on this PC.";
        if(!targetReady)return "Select a NAM block in the signal path first.";
        if(TextOf(row,"deviceStatus")=="checking")return "Checking which slots hold your captures. Try again in a moment.";
        if(busy)return "HX Edit is finishing another change. Try again in a moment.";
        if(!Flag(row,"canLoad"))return "This capture cannot be loaded right now. Try again in a moment.";
        return "";
    }
    // What the slot check is doing, for the status line. Reading a slot back locks HX Edit for a
    // moment; without this the owner could not tell why the editor stopped responding.
    public static string DiscoveryProgress(string state,string checkedText,string totalText){
        int done,total;
        if(state=="verifying"&&Int32.TryParse(checkedText,out done)&&Int32.TryParse(totalText,out total)&&total>0)
            return "Checking Floor slots… "+Math.Max(0,Math.Min(done,total))+" of "+total+". HX Edit pauses while each slot is read.";
        return state=="verifying"||state=="verification-requested"?"Checking Floor slots…":"";
    }
    string progressText="";
    void ShowDiscoveryProgress(string progress){
        if(progress==progressText)return;
        // Replace only our own previous progress line, never a result, error or load confirmation.
        if(!loadConfirmation.Pending&&(status.Text==progressText||status.Text.Length==0)){status.Text=progress;noticeUntil=0;}
        progressText=progress;
    }
    static readonly Color LoadReady=Color.FromArgb(104,44,96),LoadBlocked=Color.FromArgb(44,38,44);
    void RefreshLoad(){var row=Selected();
        string reason=BlockedReason(row,waiting,busy,loadConfirmation.Pending,targetReady);
        // The button stays clickable whenever a capture is selected, so a click always answers:
        // it either starts the action or says why it cannot (see RequestCapture).
        load.Enabled=row!=null&&!waiting;load.Text=LoadLabel(row);
        load.BackColor=reason.Length==0?LoadReady:LoadBlocked;load.ForeColor=reason.Length==0?Color.White:Color.Gray;
        changeType.Enabled=!classificationWaiting&&row!=null&&TextOf(row,"categoryRevision").Length==64;
        gripHint.SetToolTip(changeType,row==null?"Select a capture":CategoryDetail(row));
        gripHint.SetToolTip(load,reason.Length>0?reason:TextOf(row,"fileDetail")+"\n"+TextOf(row,"locationDetail"));}
    void RequestCapture(bool installedOnly){
        var row=Selected();
        string reason=BlockedReason(row,waiting,busy,loadConfirmation.Pending,targetReady);
        if(reason.Length>0){
            // Double-click on a row is a shortcut, not a request the user is waiting on.
            if(!installedOnly&&!waiting){status.Text=reason;noticeUntil=resizeClock.ElapsedMilliseconds+6000;}
            return;
        }
        bool installed=TextOf(row,"deviceStatus")=="verified";
        if(installedOnly&&!installed)return;
        loadConfirmation.Clear();waiting=true;RefreshLoad();status.Text="Checking selected NAM block…";noticeUntil=0;
        output.WriteLine(json.Serialize(new{type="action",action=installed?"select":"load",contentId=TextOf(row,"contentId")}));
    }
    protected override CreateParams CreateParams {get{var settings=base.CreateParams;settings.Parent=parent;settings.Style=(settings.Style&~unchecked((int)0x80000000))|0x40000000;return settings;}}
    void Apply(Dictionary<string,object> message){
        string type=TextOf(message,"type");
        if(type=="close"){Close();return;}
        if(type=="workflowStatus"){
            waiting=Flag(message,"busy");status.Text=TextOf(message,"text");
            noticeUntil=waiting?0:resizeClock.ElapsedMilliseconds+6000;RefreshLoad();return;
        }
        if(type=="classificationResult"){
            classificationWaiting=false;status.Text=TextOf(message,"error");noticeUntil=0;
            if(status.Text.Length==0){status.Text="Local type saved. Source metadata preserved.";noticeUntil=resizeClock.ElapsedMilliseconds+4000;}
            RefreshLoad();return;
        }
        if(type=="resizeResult"){
            if(resizeQueue.Failed)return; // Late acknowledgement cannot restart a timed-out drag.
            string error=TextOf(message,"error");
            resizeQueue.Complete(error.Length==0);
            if(error.Length>0){resizing=false;grip.Capture=false;noticeUntil=0;status.Text=error;}
            return;
        }
        if(type=="result"){
            waiting=false;
            loadConfirmation.Clear();
            status.Text=TextOf(message,"error");
            noticeUntil=0;
            if(status.Text.Length==0){
                int destination;string identity=TextOf(message,"contentId");
                if(!Flag(message,"reused")&&identity.Length==64&&Int32.TryParse(TextOf(message,"savedSlot"),out destination)&&destination>=1&&destination<128){
                    long epoch;if(!Int64.TryParse(TextOf(message,"slotEpoch"),out epoch))epoch=-1;
                    loadConfirmation.Begin(identity,destination,epoch);status.Text=loadConfirmation.Message;
                }else{status.Text=(Flag(message,"reused")?"Selected slot ":"Loaded in slot ")+TextOf(message,"savedSlot")+". Preset remains unsaved.";noticeUntil=resizeClock.ElapsedMilliseconds+4000;}
            }
            RenderRows();return;
        }
        if(type!="state")return;
        var state=message["state"] as Dictionary<string,object>;
        if(state==null)return;
        UpdateLoadConfirmation(state);
        shown=Flag(state,"visible"); if(!shown){resizing=false;resizeQueue.CancelPending();Visible=false;return;}
        var bounds=state["bounds"] as Dictionary<string,object>;
        dock=new Rectangle(Convert.ToInt32(bounds["x"]),Convert.ToInt32(bounds["y"]),Math.Max(1,Convert.ToInt32(bounds["w"])),Math.Max(1,Convert.ToInt32(bounds["h"])));
        if(!addedParentClipping&&(GetWindowLong(parent,-16)&ClipChildren)==0){
            // The native parent must clip child HWNDs when repainting its canvas.
            SetWindowLong(parent,-16,GetWindowLong(parent,-16)|ClipChildren);addedParentClipping=true;
        }
        SetBounds(dock.X,dock.Y,dock.Width,dock.Height); Visible=true;
        object widthValue;
        if(state.TryGetValue("sidebarWidth",out widthValue))sidebarWidth=Convert.ToInt32(widthValue);
        resizeAvailable=Flag(state,"resizeAvailable");
        object resizeInfo;
        var info=state.TryGetValue("resizeStatus",out resizeInfo)?resizeInfo as Dictionary<string,object>:null;
        resizeUnavailable="Sidebar resize unavailable"+(info==null?".":": "+
            (TextOf(info,"detail").Length>0?TextOf(info,"detail"):TextOf(info,"reason")));
        grip.Cursor=resizeAvailable?Cursors.VSplit:Cursors.Help;
        gripHint.SetToolTip(grip,resizeAvailable?"Drag to resize · Double-click to reset width":resizeUnavailable);
        grip.Invalidate();
        busy=Flag(state,"busy");targetReady=Flag(state,"targetReady");
        ShowDiscoveryProgress(DiscoveryProgress(TextOf(state,"discoveryStatus"),TextOf(state,"discoveryChecked"),TextOf(state,"discoveryTotal")));
        string signature=json.Serialize(state["rows"]);
        if(signature!=rowSignature){
            allRows.Clear();foreach(var item in (IEnumerable)state["rows"])allRows.Add((Dictionary<string,object>)item);
            RenderRows();rowSignature=signature;
        }
        object view;
        if(state.TryGetValue("view",out view))ApplyView(view as Dictionary<string,object>);
        RefreshLoad();
    }
    async void Start(TextReader input){
        timer.Start();resizeTimer.Start();output.WriteLine("{\"type\":\"ready\"}");
        await Task.Run(()=>{
            try{for(string line;(line=input.ReadLine())!=null;){
                var message=json.Deserialize<Dictionary<string,object>>(line);
                if(!IsDisposed&&IsHandleCreated)BeginInvoke(new Action(()=>Apply(message)));
            }}catch(Exception){ }
            if(!IsDisposed&&IsHandleCreated)BeginInvoke(new Action(Close));
        });
    }
    [STAThread] static void Main(string[] args){
        if(args.Length>0&&(args[0]=="--self-test"||args[0]=="--render-fixture")){
            try{if(args[0]=="--self-test")SelfTest();else RenderFixture(args[1]);}
            catch(Exception error){Console.Error.WriteLine(error);Environment.ExitCode=1;}return;
        }
        var parent=ParseWindowHandle(args[0]);if(!IsWindow(parent))return;
        SetThreadDpiAwarenessContext(GetWindowDpiAwarenessContext(parent));
        Application.EnableVisualStyles();
        var output=new StreamWriter(Console.OpenStandardOutput(),new UTF8Encoding(false)){AutoFlush=true};
        using(var input=new StreamReader(Console.OpenStandardInput(),Encoding.UTF8)) {
            var pane=new NamLibrary(parent,output);pane.Shown+=(sender,eventArgs)=>pane.Start(input);
            Application.Run(pane);
        }
    }
    static IntPtr ParseWindowHandle(string value){
        uint bits;if(!UInt32.TryParse(value,NumberStyles.Integer,CultureInfo.InvariantCulture,out bits)||bits==0)
            throw new ArgumentException("Invalid parent window handle");
        return IntPtr.Size==4?new IntPtr(unchecked((int)bits)):new IntPtr((long)bits);
    }
    static void RenderFixture(string path){
        // Offscreen WinForms rendering only. No editor/device process or Show().
        Application.EnableVisualStyles();
        using(var writer=new StringWriter())using(var pane=new NamLibrary(IntPtr.Zero,writer)){
            pane.ClientSize=new Size(360,700);var handle=pane.table.Handle;
            string[] names={"Deluxe Reverb","Klon Clean Boost","Plexi Bright","Studio Compressor"};
            string[] types={"Amp + cab","Pedal","Amp","Outboard"};
            string[] groups={"amp+cab","pedals","amp-only","outboard"};
            for(int i=0;i<names.Length;i++)pane.allRows.Add(new Dictionary<string,object>{
                {"contentId",new string((char)('a'+i),64)},{"title",names[i]},{"categoryLabel",types[i]},
                {"category",groups[i]},{"fileStatus","available"},{"fileDetail","Kept on this PC"},
                {"canLoad",true},{"deviceStatus",i==0?"verified":"local-only"},
                {"verifiedSlots",i==0?new object[]{127}:new object[0]},
                {"pendingSlots",i==1?new object[]{21}:new object[0]},
                {"addedAt","2026-11-09"},{"categoryRevision",new string('d',64)}});
            pane.ApplyView(new Dictionary<string,object>{{"selected",new string('b',64)},{"filter","*"},{"query",""},{"sortKey","name"},{"sortDirection",1}});
            pane.LayoutPane();pane.CreateControl();
            using(var bitmap=new Bitmap(pane.ClientSize.Width,pane.ClientSize.Height)){
                using(var graphics=Graphics.FromImage(bitmap))graphics.Clear(pane.BackColor);
                // Render child HWNDs directly: the fixture deliberately never shows a parent.
                foreach(Control control in new Control[]{pane.table,pane.search,pane.clearSearch,pane.categoryFilter,pane.count,pane.load,pane.changeType,pane.expand}){
                    var location=control.Parent==pane?control.Location:new Point(control.Left+control.Parent.Left,control.Top+control.Parent.Top);
                    control.DrawToBitmap(bitmap,new Rectangle(location,control.Size));
                }
                bitmap.Save(path,System.Drawing.Imaging.ImageFormat.Png);
            }
            if(pane.table.Items.Count!=4||pane.table.SelectedItems.Count!=1)throw new Exception("Fixture row selection");
            pane.ApplyView(new Dictionary<string,object>{{"selected",new string('b',64)},{"filter","pedal"},{"query","Klon"},{"sortKey","added"},{"sortDirection",-1}});
            if(pane.table.Items.Count!=1||TextOf(pane.Selected(),"title")!="Klon Clean Boost")throw new Exception("Shared sidebar filters/selection");
            pane.ClientSize=new Size(600,700);pane.ClientSize=new Size(300,700);
            if(pane.table.Width!=290)throw new Exception("Live width fixture");
            Console.WriteLine("Native sidebar fixture passed: rows, selection, shared filters, width, and offscreen render");
        }
    }
    static void SelfTest(){
        if(unchecked((uint)ParseWindowHandle("4294967295").ToInt64())!=UInt32.MaxValue)throw new Exception("High-bit HWND parse");
        // Native subitem custom-draw flags can report Selected for unselected
        // rows. Move a real single selection between rows and deliberately pass
        // that flag on every draw: only the native selected item may highlight.
        using(var writer=new StringWriter())using(var pane=new NamLibrary(IntPtr.Zero,writer)){
            pane.ClientSize=new Size(360,700);
            var handle=pane.table.Handle;
            for(int i=0;i<3;i++){
                var item=new ListViewItem("Capture "+i){UseItemStyleForSubItems=false,
                    Tag=new Dictionary<string,object>{{"contentId",new string((char)('a'+i),64)},{"canLoad",true}}};
                item.SubItems.Add("Amp");item.SubItems.Add("21 …").ForeColor=Color.Goldenrod;
                item.SubItems.Add("Nov 10");pane.table.Items.Add(item);
            }
            foreach(int selected in new[]{1,2,0,-1})foreach(bool focused in new[]{true,false}){
                foreach(ListViewItem item in pane.table.Items)item.Selected=false;
                if(selected>=0)pane.table.Items[selected].Selected=true;
                if(pane.table.SelectedItems.Count!=(selected<0?0:1))throw new Exception("Native single-row selection changed");
                if(selected>=0&&TextOf(pane.Selected(),"contentId")!=new string((char)('a'+selected),64))
                    throw new Exception("Library action target does not follow the selected row");
                if(selected<0&&pane.Selected()!=null)throw new Exception("Cleared selection kept an action target");
                for(int drawRow=0;drawRow<3;drawRow++)using(var bitmap=new Bitmap(100,26))using(var graphics=Graphics.FromImage(bitmap)){
                    var item=pane.table.Items[drawRow];
                    var draw=new DrawListViewSubItemEventArgs(graphics,new Rectangle(0,0,100,26),item,item.SubItems[2],drawRow,2,
                        pane.table.Columns[2],ListViewItemStates.Selected);
                    DrawLibraryCell(draw,pane.table.Font,focused);
                    var expected=drawRow==selected?(focused?Color.FromArgb(63,45,60):Color.FromArgb(48,48,48)):Color.FromArgb(18,18,18);
                    if(bitmap.GetPixel(99,25).ToArgb()!=expected.ToArgb())throw new Exception("Unselected row highlighted by custom-draw flags");
                }
            }
            // Also exercise Windows' actual WM_PRINT/custom-draw path while
            // the form stays hidden. This reproduces the misleading flags on
            // the real ListView, rather than supplying only ideal test flags.
            int nativeDraws=0;
            pane.table.DrawSubItem+=(sender,args)=>nativeDraws++;
            foreach(int selected in new[]{1,2,0,-1}){
                foreach(ListViewItem item in pane.table.Items)item.Selected=false;
                if(selected>=0)pane.table.Items[selected].Selected=true;
                using(var bitmap=new Bitmap(pane.table.Width,pane.table.Height)){
                    pane.table.DrawToBitmap(bitmap,pane.table.ClientRectangle);
                    if(nativeDraws==0)throw new Exception("Native list painting was not exercised");
                    for(int drawRow=0;drawRow<3;drawRow++){
                        var bounds=pane.table.Items[drawRow].GetBounds(ItemBoundsPortion.Entire);
                        var expected=drawRow==selected?Color.FromArgb(48,48,48):Color.FromArgb(18,18,18);
                        if(bitmap.GetPixel(pane.table.Columns[0].Width-2,bounds.Bottom-2).ToArgb()!=expected.ToArgb())
                            throw new Exception("Native ListView painting highlighted the wrong row");
                    }
                }
            }
        }
        // Exercise the actual painter, including selected/focused states. A
        // SubItem.ForeColor assertion alone missed the native selection override.
        using(var font=new Font("Segoe UI",9))foreach(var color in new[]{Color.Goldenrod,Color.LightGreen,Color.Silver})
        foreach(bool selected in new[]{false,true})foreach(bool focused in new[]{false,true})
        using(var bitmap=new Bitmap(100,26))using(var graphics=Graphics.FromImage(bitmap)){
            var item=new ListViewItem("Amp"){UseItemStyleForSubItems=false,Selected=selected};
            item.SubItems.Add("Amp");var slot=item.SubItems.Add("21 …");slot.ForeColor=color;
            var state=selected?ListViewItemStates.Selected:ListViewItemStates.Default;
            var draw=new DrawListViewSubItemEventArgs(graphics,new Rectangle(0,0,100,26),item,slot,0,2,new ColumnHeader(),state);
            DrawLibraryCell(draw,font,focused);
            int matches=0;
            for(int y=0;y<bitmap.Height;y++)for(int x=0;x<bitmap.Width;x++)
                if(bitmap.GetPixel(x,y).ToArgb()==color.ToArgb())matches++;
            if(draw.DrawDefault||matches<3)throw new Exception("Selected slot text color was not painted: "+color+"/"+selected+"/"+focused);
        }
        var captureId=new string('a',64);
        var verifiedCapture=new Dictionary<string,object>{{"contentId",captureId},{"deviceStatus","verified"},{"verifiedSlots",new object[]{17}}};
        var confirmation=new LoadConfirmation();confirmation.Begin(captureId,17);
        confirmation.Observe("complete",new object[0]);
        if(!confirmation.Pending)throw new Exception("Old completed scan must not end new confirmation");
        confirmation.Observe("verifying",null);
        for(int i=0;i<100;i++)confirmation.Observe("verifying",new object[0]);
        if(!confirmation.Pending||!confirmation.Message.Contains("verifying"))throw new Exception("Confirmation must persist throughout scan");
        confirmation.Observe("complete",new object[]{verifiedCapture});
        if(confirmation.Pending||confirmation.Failed||!confirmation.Message.Contains("17 verified"))throw new Exception("Matched destination confirmation");
        confirmation.Begin(captureId,17);confirmation.Observe("complete",new object[]{verifiedCapture});
        if(confirmation.Pending||confirmation.Failed)throw new Exception("Late result can use completed matching scan");
        foreach(string terminal in new[]{"complete","complete-partial"}){
            confirmation.Begin(captureId,17);confirmation.Observe("verifying",null);
            confirmation.Observe(terminal,new object[]{new Dictionary<string,object>{{"contentId",captureId},{"deviceStatus","verified"},{"verifiedSlots",new object[]{18}}}});
            if(confirmation.Pending||!confirmation.Failed)throw new Exception("Wrong destination cannot confirm load");
            confirmation.Begin(captureId,17);confirmation.Observe("verifying",null);
            confirmation.Observe(terminal,new object[]{new Dictionary<string,object>{{"contentId",new string('b',64)},{"deviceStatus","verified"},{"verifiedSlots",new object[]{17}}}});
            if(confirmation.Pending||!confirmation.Failed)throw new Exception("Wrong capture cannot confirm load");
        }
        foreach(string terminal in new[]{"disconnected","discovery-timeout","verification-stopped"}){
            confirmation.Begin(captureId,17);confirmation.Observe(terminal,null);
            if(confirmation.Pending||!confirmation.Failed)throw new Exception("Interrupted verification must not show endless spinner");
            confirmation.Observe("complete",new object[]{verifiedCapture});
            if(!confirmation.Failed)throw new Exception("Late scan must not revive interrupted load");
        }
        using(var writer=new StringWriter())using(var pane=new NamLibrary(IntPtr.Zero,writer)){
            var loaded=new Dictionary<string,object>{{"type","result"},{"contentId",captureId},{"savedSlot",17},{"reused",false}};
            pane.Apply(loaded);
            if(!pane.loadConfirmation.Pending||pane.noticeUntil!=0||!pane.status.Text.Contains("Loaded in IR slot 17"))throw new Exception("Persistent success notice must replace four-second timer");
            pane.Apply(new Dictionary<string,object>{{"type","state"},{"state",new Dictionary<string,object>{{"visible",false},{"discoveryStatus","verifying"}}}});
            if(!pane.loadConfirmation.Pending||pane.noticeUntil!=0)throw new Exception("Hidden pane keeps load confirmation");
            pane.UpdateLoadConfirmation(new Dictionary<string,object>{{"discoveryStatus","complete"},{"rows",new object[]{verifiedCapture}}});
            if(pane.loadConfirmation.Pending||pane.noticeUntil<=0||!pane.status.Text.Contains("17 verified"))throw new Exception("Finished confirmation may expire");
            pane.Apply(loaded);pane.status.Text="Newer operation error";
            pane.UpdateLoadConfirmation(new Dictionary<string,object>{{"discoveryStatus","disconnected"}});
            if(pane.status.Text!="Newer operation error")throw new Exception("Confirmation overwrote newer error");
            pane.Apply(loaded);
            pane.Apply(new Dictionary<string,object>{{"type","state"},{"state",new Dictionary<string,object>{{"visible",false},{"discoveryStatus","disconnected"}}}});
            if(pane.loadConfirmation.Pending||pane.noticeUntil!=0||!pane.status.Text.Contains("disconnected"))throw new Exception("Disconnect feedback persists without rows");
            loaded["reused"]=true;pane.Apply(loaded);
            if(pane.loadConfirmation.Pending||pane.noticeUntil<=0||!pane.status.Text.Contains("Selected slot 17"))throw new Exception("Use on Floor does not wait for an unnecessary scan");
            pane.Apply(new Dictionary<string,object>{{"type","result"},{"error","Load failed"}});
            if(pane.loadConfirmation.Pending||pane.status.Text!="Load failed"||pane.noticeUntil!=0)throw new Exception("Load errors remain explicit");
        }
        confirmation.Begin(captureId,17,8);confirmation.ObserveEmpty(7,new object[]{17});
        if(!confirmation.Pending)throw new Exception("Stale empty snapshot cancelled a completed import");
        confirmation.ObserveEmpty(8,new object[]{18});
        if(!confirmation.Pending)throw new Exception("Unrelated clear cancelled confirmation");
        confirmation.ObserveEmpty(9,new object[]{17});
        if(confirmation.Pending||!confirmation.Message.Contains("now empty"))throw new Exception("Current empty row must clear provisional slot");
        using(var writer=new StringWriter())using(var pane=new NamLibrary(IntPtr.Zero,writer)){
            var local=new Dictionary<string,object>{{"contentId",captureId},{"title","Sample amp"},{"deviceStatus","local-only"},{"canLoad",true},{"fileStatus","ready"}};
            var other=new Dictionary<string,object>{{"contentId",new string('b',64)},{"title","Z other amp"},{"deviceStatus","checking"},{"displaySlots",new object[]{9}},{"canLoad",false}};
            pane.allRows.Add(local);pane.allRows.Add(other);pane.RenderRows();pane.table.Items[0].Selected=true;
            var loaded=new Dictionary<string,object>{{"type","result"},{"contentId",captureId},{"savedSlot",17},{"slotEpoch",8},{"reused",false}};
            pane.Apply(loaded);
            if(pane.table.Items[0].SubItems[2].Text!="17 …"||pane.table.Items[0].SubItems[2].ForeColor!=Color.Goldenrod||pane.load.BackColor!=LoadBlocked)
                throw new Exception("Provisional slot must appear inline immediately without authorizing selection");
            // The button stays clickable so a click can explain itself, but it must not act.
            pane.RequestCapture(false);
            if(writer.ToString().Contains("\"action\":\"load\"")||writer.ToString().Contains("\"action\":\"select\""))
                throw new Exception("A blocked click must send nothing");
            if(pane.status.Text!=BlockedReason(pane.Selected(),false,false,true,false))
                throw new Exception("A blocked click must explain itself: "+pane.status.Text);
            pane.status.Text=pane.loadConfirmation.Message;
            if(TextOf(local,"deviceStatus")!="local-only")throw new Exception("Provisional UI mutated verification evidence");
            if(pane.table.Items[other["contentId"].ToString()].SubItems[2].ForeColor!=Color.Silver||
               pane.table.Items[other["contentId"].ToString()].SubItems[2].Text!="9")
                throw new Exception("Unrelated checking rows keep their slot, unmarked and not amber");
            pane.RenderRows(); // Search/sort/refresh must retain the provisional marker.
            if(pane.table.Items[0].SubItems[2].Text!="17 …")throw new Exception("Provisional marker lost on render");
            pane.UpdateLoadConfirmation(new Dictionary<string,object>{{"discoveryStatus","verifying"},{"slotEpoch",7},{"emptySlots",new object[]{17}}});
            if(pane.table.Items[0].SubItems[2].Text!="17 …")throw new Exception("Old empty snapshot removed marker");
            local["deviceStatus"]="verified";local["verifiedSlots"]=new object[]{17};
            pane.UpdateLoadConfirmation(new Dictionary<string,object>{{"discoveryStatus","complete"},{"rows",new object[]{local}}});
            if(pane.table.Items[0].SubItems[2].Text!="17"||pane.table.Items[0].SubItems[2].ForeColor!=Color.LightGreen)
                throw new Exception("Verified slot must replace amber marker with green");
            local["deviceStatus"]="checking";local["verifiedSlots"]=new object[0];local["displaySlots"]=new object[0];pane.Apply(loaded);
            pane.UpdateLoadConfirmation(new Dictionary<string,object>{{"discoveryStatus","verifying"},{"slotEpoch",9},{"emptySlots",new object[]{17}}});
            if(pane.table.Items[0].SubItems[2].Text.Contains("17")||!pane.status.Text.Contains("now empty"))throw new Exception("Cleared destination still displayed");
        }
        var row=new Dictionary<string,object>{{"dateEvidence",new Dictionary<string,object>{{"day","2026-11-09"},{"kind","seen-by"}}}};
        if(DateLabel(row)[0]!="Nov 9, 2026"||!DateLabel(row)[2].Contains("by this date"))throw new Exception("Date evidence lost");
        row["addedAt"]="2026-11-09T12:00:00-05:00";if(DateLabel(row)[0]!="Nov 9, 2026")throw new Exception("Timestamp precedence");
        if(DateLabel(new Dictionary<string,object>())[0]!="—")throw new Exception("Missing date");
        var absentFile=new Dictionary<string,object>{{"title","Match Brave Hi"},{"fileStatus","missing"}};
        if(CaptureTitle(absentFile)!="Match Brave Hi [file missing]"||LoadLabel(absentFile)!="File missing")throw new Exception("Missing file must be explicit");
        absentFile["deviceStatus"]="verified";
        if(LoadLabel(absentFile)!="Use on Floor")throw new Exception("Installed copy usable without local file");
        absentFile["deviceStatus"]="local-only";absentFile["fileStatus"]="ready";
        if(CaptureTitle(absentFile)!="Match Brave Hi"||LoadLabel(absentFile)!="Load on Floor")throw new Exception("Ready file presentation");
        if(LoadLabel(null)!="Select a capture")throw new Exception("No selection must not offer an action");
        var numericFlag=new Dictionary<string,object>{{"targetReady",1},{"busy",0},{"visible",true}};
        if(!Flag(numericFlag,"targetReady")||Flag(numericFlag,"busy")||!Flag(numericFlag,"visible")||Flag(numericFlag,"absent"))
            throw new Exception("A native 1 is a true flag");
        if(!DiscoveryProgress("verifying","7","26").Contains("7 of 26")||DiscoveryProgress("verifying","","")!="Checking Floor slots…"||
           DiscoveryProgress("complete","26","26").Length!=0)throw new Exception("Slot check progress text");
        var rechecking=new Dictionary<string,object>{{"deviceStatus","checking"},{"displaySlots",new object[]{9}},{"fileStatus","ready"},{"canLoad",false}};
        if(LoadLabel(rechecking)!="Use on Floor")throw new Exception("An installed capture being re-checked must not offer a second upload");
        if(!BlockedReason(rechecking,false,false,false,true).Contains("Checking"))throw new Exception("Re-check reason");
        var ready=new Dictionary<string,object>{{"deviceStatus","verified"},{"fileStatus","missing"},{"canLoad",true}};
        if(BlockedReason(ready,false,false,false,true).Length!=0)throw new Exception("A verified copy is usable");
        if(!BlockedReason(ready,false,false,false,false).Contains("NAM block"))throw new Exception("Missing target reason");
        if(!BlockedReason(null,false,false,false,true).Contains("Select a capture"))throw new Exception("No selection reason");
        if(BlockedReason(ready,false,true,false,true).Length==0||BlockedReason(ready,true,false,false,true).Length==0)throw new Exception("Busy reasons");
        var a=new ListViewItem("z"){Name="a"};var b=new ListViewItem("a"){Name="b"};
        foreach(var item in new[]{a,b}){item.SubItems.Add("Amp");item.SubItems.Add("?");item.SubItems.Add("—").Tag="";}
        a.SubItems[3].Tag="2026-11-09";var sorter=new RowsComparer{Column=3};
        if(sorter.Compare(a,b)>=0)throw new Exception("Missing date sort");sorter.Descending=true;
        if(sorter.Compare(a,b)>=0)throw new Exception("Missing date descending sort");
        sorter.Column=0;sorter.Descending=false;if(sorter.Compare(a,b)<=0)throw new Exception("Name sorting");
        sorter.Column=2;a.SubItems[2].Text="9";b.SubItems[2].Text="127";
        if(sorter.Compare(a,b)>=0)throw new Exception("Numeric IR sorting");
        b.SubItems[2].Text="?";sorter.Descending=true;
        if(sorter.Compare(a,b)>=0)throw new Exception("Unknown IR sort last");
        var fileDate=new Dictionary<string,object>{{"dateEvidence",new Dictionary<string,object>{{"day","2026-11-09"},{"kind","file-modified"}}}};
        if(DateLabel(fileDate)[0]!="Nov 9, 2026"||!DateLabel(fileDate)[2].Contains("not a confirmed download"))throw new Exception("File date uncertainty");
        using(var pane=new NamLibrary(IntPtr.Zero,new StringWriter())){
            // Native column-change notifications require a handle. This control
            // is never shown and never acquires mouse capture or device access.
            var tableHandle=pane.table.Handle;
            if(pane.table.Columns.Count!=4||pane.table.Columns[2].Text!="IR slot"||pane.table.Columns[3].Text!="Date")throw new Exception("Compact columns");
            foreach(int width in new[]{300,360,600}){
                pane.ClientSize=new Size(width,700);pane.LayoutPane();
                if(pane.table.Bottom>pane.footer.Top||pane.footer.Bottom!=700)throw new Exception("Overlapping footer");
                if(pane.status.Right>pane.footer.Width||pane.load.Bottom>pane.footer.Height)throw new Exception("Footer clipping");
                if(pane.grip.Left!=width-GripWidth||pane.grip.Right!=width)throw new Exception("Resize grip");
                if(pane.footer.Height!=38)throw new Exception("Idle footer not compact");
                pane.table.Columns[0].Width=50;pane.table.Columns[1].Width=50;pane.table.Columns[3].Width=50;
                if(pane.table.Columns[3].Width!=50)throw new Exception("Header painting must not grow Date column");
            }
            pane.table.Columns[1].Width=78;pane.table.Columns[2].Width=50;pane.table.Columns[3].Width=100;
            foreach(int width in new[]{600,300,360}){
                pane.ClientSize=new Size(width,700);pane.LayoutPane();
                if(pane.table.Columns[3].Width!=100||pane.table.Columns[0].Width!=pane.table.ClientSize.Width-228)
                    throw new Exception("Default columns should fit after narrowing again");
            }
        }
        using(var writer=new StringWriter())using(var pane=new NamLibrary(IntPtr.Zero,writer)){
            var handle=pane.table.Handle;
            var amp=new Dictionary<string,object>{{"contentId",new string('a',64)},{"title","Deluxe Reverb"},{"category","amp+cab"},{"categoryLabel","Amp + cab"},
                {"categoryRevision",new string('b',64)},{"aliases",new object[]{"Old amp filename"}},{"canLoad",true},{"deviceStatus","verified"},{"verifiedSlots",new object[]{127}}};
            var pedal=new Dictionary<string,object>{{"contentId",new string('c',64)},{"title","Boost"},{"category","pedals"},{"categoryLabel","Pedal"},
                {"categoryRevision",new string('d',64)},{"canLoad",false},{"fileStatus","missing"}};
            pane.allRows.Add(amp);pane.allRows.Add(pedal);pane.RenderRows();
            if(pane.table.Items.Count!=2)throw new Exception("All types");
            pane.table.Items[new string('a',64)].Selected=true;
            pane.RenderRows();if(pane.Selected()!=amp||!pane.load.Enabled)throw new Exception("Selection must survive refresh");
            pane.search.Text="OLD AMP";
            if(pane.table.Items.Count!=1||pane.Selected()!=amp)throw new Exception("Case-insensitive alias search and selection");
            pane.SetFilter(3);
            if(pane.table.Items.Count!=0||pane.Selected()!=null||pane.load.Enabled)throw new Exception("Hidden selection must not load");
            pane.search.Clear();
            if(pane.table.Items.Count!=1||pane.Selected()!=null)throw new Exception("Filter must not auto-select next capture");
            pane.table.Items[0].Selected=true;pane.RefreshLoad();
            if(pane.load.BackColor!=LoadBlocked||!pane.changeType.Enabled)throw new Exception("Missing files still classifiable but not loadable");
            writer.GetStringBuilder().Clear(); // Ignore shared-view notifications; verify action deduplication.
            pane.RequestCategory("amp");pane.RequestCategory("pedal");
            var messages=writer.ToString().Trim().Split('\n');
            if(messages.Length!=1)throw new Exception("Duplicate classification request");
            var request=pane.json.Deserialize<Dictionary<string,object>>(messages[0]);
            if(TextOf(request,"action")!="classify"||TextOf(request,"contentId")!=new string('c',64)||TextOf(request,"revision")!=new string('d',64))throw new Exception("Classification identity/revision");
            pane.Apply(new Dictionary<string,object>{{"type","classificationResult"},{"error","Stale type"}});
            if(pane.classificationWaiting||!pane.changeType.Enabled||pane.status.Text!="Stale type")throw new Exception("Classification failure recovery");
            pedal["category"]="amp-only";pedal["categoryLabel"]="Amp only";pane.RenderRows();
            if(pane.Selected()!=null||pane.table.Items.Count!=0||pane.load.Enabled)throw new Exception("Correction must respect active filter");
            pane.SetFilter(0);
            pane.table.Items[new string('c',64)].Selected=true;writer.GetStringBuilder().Clear();pane.RequestCategory(null);
            request=pane.json.Deserialize<Dictionary<string,object>>(writer.ToString());
            if(!request.ContainsKey("gear")||request["gear"]!=null)throw new Exception("Reset to source metadata");
            if(!Matches(amp,"deluxe","amp+cab")||Matches(amp,"deluxe","pedals")||Matches(amp,"absent",""))throw new Exception("Combined search and filter");
            for(int i=0;i<GearCodes.Length;i++){pane.SetFilter(i+1);if(pane.categoryFilter.Text!=GearLabels[i]+" ▾")throw new Exception("Filter labels");}
        }
        var flow=new LatestResize();flow.Offer(400);
        if(flow.Take(0,false)!=400)throw new Exception("Initial live resize");
        for(int n=0;n<1000;n++){flow.Offer(300+n%301);if(flow.Take(10,false)!=null)throw new Exception("Resize flood");}
        flow.Offer(550);flow.Complete(true);
        if(flow.Take(20,false)!=null||flow.Take(33,false)!=550)throw new Exception("Latest width or throttle");
        flow.Offer(320);if(flow.Take(34,true)!=null)throw new Exception("Final width overlaps request");
        flow.Complete(true);if(flow.Take(35,true)!=320)throw new Exception("Final pointer position lost");
        flow.Complete(true);flow.Offer(320);if(flow.Take(100,false)!=null)throw new Exception("Duplicate resize");
        flow.Offer(450);if(flow.Take(101,false)!=450)throw new Exception("Reverse drag");
        flow.Offer(500);flow.Take(2101,false);flow.Complete(true);
        if(!flow.Failed||flow.Take(3000,true)!=null)throw new Exception("Timeout resumed writes");
        flow=new LatestResize();flow.Offer(420);flow.Take(0,true);flow.Offer(480);flow.Complete(false);
        if(!flow.Failed||flow.Take(100,true)!=null)throw new Exception("Rejected resize retried");
        if(DragWidth(360,364,464)!=460||DragWidth(460,464,364)!=360||
           DragWidth(360,0,10000)!=600||DragWidth(360,10000,0)!=300)
            throw new Exception("Drag delta and limits");
        using(var writer=new StringWriter())using(var pane=new NamLibrary(IntPtr.Zero,writer)){
            pane.RequestWidth(460);
            var sent=pane.json.Deserialize<Dictionary<string,object>>(writer.ToString());
            if(TextOf(sent,"action")!="resize"||Convert.ToInt32(sent["width"])!=460)
                throw new Exception("Resize pipe request");
            pane.Apply(new Dictionary<string,object>{{"type","resizeResult"},{"width",460}});
            if(pane.status.Text!="")throw new Exception("Resize success must stay quiet");
            pane.Apply(new Dictionary<string,object>{{"type","resizeResult"},{"error","Layout mismatch"}});
            if(pane.status.Text!="Layout mismatch")throw new Exception("Resize rejection");
        }
        if(FloorLabel(new Dictionary<string,object>())!=""||
           FloorLabel(new Dictionary<string,object>{{"deviceStatus","local-only"}})!=""||
           FloorLabel(new Dictionary<string,object>{{"deviceStatus","checking"}})!=""||
           FloorLabel(new Dictionary<string,object>{{"deviceStatus","verified"},{"verifiedSlots",new object[]{127,11,127,128,-1,"9"}}})!="11, 127"||
           FloorLabel(new Dictionary<string,object>{{"deviceStatus","verified"}})!=""||
           FloorLabel(new Dictionary<string,object>{{"deviceStatus","preview"}})!="128 · P")throw new Exception("IR slot evidence labels");
        Console.WriteLine("Library table self-tests passed");
    }
}
