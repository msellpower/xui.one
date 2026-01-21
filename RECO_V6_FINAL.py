import sys, subprocess, threading, time, os, re, requests, psutil, json
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QLabel, QCheckBox, 
                             QFrame, QMessageBox, QGridLayout, QTabWidget, QTextEdit)
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QTimer, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont

# --- הגדרות מערכת ---
os.environ["QT_QUICK_BACKEND"] = "software"
TELEGRAM_TOKEN = "8307008722:AAHY-QYNYyTnOwjS0q4VGfA0_iUiQBxYHBc"
TELEGRAM_CHAT_ID = "-5125327073"
CONFIG_FILE = "/root/iptv_config.json"
RECORDINGS_PATH = "/root/Recordings"

# --- רכיב UX: כפתור כלי עבודה ---
class ToolButton(QPushButton):
    def __init__(self, text, icon, color="#2d303e"):
        super().__init__()
        self.setText(f"{icon}  {text}")
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {color}; color: white; border-radius: 10px;
                padding: 15px; font-size: 14px; font-weight: bold; border: 1px solid #3b3f51;
            }}
            QPushButton:hover {{ background-color: #40e0d0; color: #000; }}
        """)

# --- רכיב UX: שעון אנליטי ---
class ProGauge(QWidget):
    def __init__(self, title, unit, max_val=100, color="#00d4ff"):
        super().__init__()
        self.value = 0
        self.max_val = max_val
        self.title = title
        self.unit = unit
        self.primary_color = QColor(color)
        self.setMinimumSize(180, 180)

    def set_value(self, val):
        self.value = val
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        size = min(w, h) - 20
        rect = QRectF((w-size)/2, (h-size)/2, size, size)
        
        # רקע
        p.setPen(QPen(QColor("#2d303e"), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 135*16, 270*16)
        
        # ערך
        ratio = self.value / self.max_val
        angle = int(270 * ratio)
        if angle > 270: angle = 270
        
        col = self.primary_color if ratio < 0.85 else QColor("#ff2e63")
        p.setPen(QPen(col, 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 225*16, -angle*16)
        
        # טקסט
        p.setPen(QColor("white"))
        p.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{self.value:.1f}{self.unit}")
        
        p.setPen(QColor("#a6accd"))
        p.setFont(QFont("Segoe UI", 10))
        p.drawText(int(w/2)-50, int(h)-30, 100, 20, Qt.AlignmentFlag.AlignCenter, self.title)

# --- לוגיקת שרת ---
class StreamWorker(QObject):
    stats_signal = pyqtSignal(str, dict)
    
    def __init__(self, name, url, config, record):
        super().__init__()
        self.name = name
        self.url = url
        self.config = config
        self.rec = record
        self.running = True
        self.proc = None

    def _get_xui(self):
        try:
            c = self.config
            base = c['server'].split('/dashboard')[0].rstrip('/')
            api = f"{base}/api.php"
            auth = f"username={c['user']}&password={c['pass']}"
            
            # בדיקת קטגוריה
            try:
                res = requests.get(f"{api}?action=get_categories&{auth}", timeout=5).json()
                cat = next((x['category_id'] for x in res if x['category_name']=="Channels"), None)
            except: cat = None
            
            # יצירה אם לא קיים
            if not cat:
                try:
                    res = requests.post(f"{api}?action=add_category", data={**c, "category_name":"Channels", "category_type":"live"}).json()
                    cat = res.get('category_id', "1")
                except: cat = "1"
            
            # יצירת סטרים
            requests.post(f"{api}?action=add_stream", data={
                "username": c['user'], "password": c['pass'],
                "stream_display_name": self.name, "stream_source": ["127.0.0.1"],
                "category_id": cat, "stream_mode": "live"
            })
            return f"{base}/live/{c['user']}/{c['pass']}/{self.name.replace(' ','_')}.ts"
        except:
            return None

    def run(self):
        start = time.time()
        folder = os.path.join(RECORDINGS_PATH, self.name.replace(" ", "_"))
        if self.rec:
            os.makedirs(folder, exist_ok=True)
            
        xui = self._get_xui()
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                          data={"chat_id": TELEGRAM_CHAT_ID, "text": f"🟢 {self.name} Started"})
        except: pass
        
        while self.running:
            # פקודת FFmpeg יציבה
            cmd = ['ffmpeg', '-y', '-rtsp_transport', 'tcp', '-stimeout', '5000000', '-i', self.url, '-c', 'copy', '-f', 'mpegts']
            tgts = []
            
            if self.rec:
                tgts.append(f"[f=mpegts]{os.path.join(folder, datetime.now().strftime('%H%M%S') + '.ts')}")
            if xui:
                tgts.append(f"[f=mpegts:onfail=ignore]{xui}")
                
            if tgts:
                cmd.extend(['-f', 'tee', "|".join(tgts)])
            else:
                cmd.extend(['-f', 'null', '-'])
            
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                while self.proc.poll() is None and self.running:
                    uptime = time.strftime("%H:%M:%S", time.gmtime(time.time() - start))
                    d_size = 0
                    if self.rec and os.path.exists(folder):
                        try: d_size = sum(os.path.getsize(os.path.join(folder, f)) for f in os.listdir(folder)) / 1048576
                        except: pass
                    self.stats_signal.emit(self.name, {"status": "ACTIVE", "uptime": uptime, "disk": f"{d_size:.1f} MB", "link": xui or "N/A"})
                    time.sleep(3)
                if self.running: time.sleep(5)
            except: time.sleep(10)

    def stop(self):
        self.running = False
        if self.proc: self.proc.terminate()

# --- ממשק ראשי ---
class XHotelUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("X-HOTEL MANAGER v12.0 (Production)")
        self.resize(1600, 1000)
        self.workers = {}
        self.net_io = psutil.net_io_counters()
        self.setup_ui()
        QTimer.singleShot(500, self.restore)
        self.t = QTimer()
        self.t.timeout.connect(self.upd_stats)
        self.t.start(1000)

    def setup_ui(self):
        self.setStyleSheet("""
            QMainWindow{background:#10121b;} 
            QWidget{color:#e0e6ed;font-family:'Segoe UI';} 
            QTabWidget::pane{border:0;background:#10121b;} 
            QTabBar::tab{background:#1f2233;padding:12px 30px;margin:2px;border-radius:6px;font-weight:bold;} 
            QTabBar::tab:selected{background:#00d4ff;color:black;} 
            QLineEdit{background:#1f2233;border:1px solid #3b3f51;padding:10px;color:white;border-radius:6px;} 
            QTableWidget{background:#151722;border:none;gridline-color:#2d303e;} 
            QHeaderView::section{background:#1f2233;padding:8px;border:none;} 
            QTextEdit{background:#0a0b10;color:#00e676;border-radius:8px;font-family:'Consolas';}
        """)
        main = QWidget()
        self.setCentralWidget(main)
        l = QVBoxLayout(main)
        
        # Header
        h = QFrame()
        hl = QHBoxLayout(h)
        lbl = QLabel("COMMAND CENTER")
        lbl.setStyleSheet("font-size:24px;font-weight:900;color:#00d4ff;")
        hl.addWidget(lbl)
        l.addWidget(h)
        
        tabs = QTabWidget()
        l.addWidget(tabs)

        # Tab 1: Live
        t1 = QWidget()
        t1l = QVBoxLayout(t1)
        c_f = QFrame()
        gl = QGridLayout(c_f)
        c_f.setStyleSheet("background:#1f2233;border-radius:12px;padding:10px;")
        
        self.url = QLineEdit("http://144.91.86.250/mbmWePBa")
        self.usr = QLineEdit("admin")
        self.pw = QLineEdit("MazalTovLanu")
        self.m3u = QLineEdit()
        self.m3u.setPlaceholderText("Paste M3U URL...")
        
        gl.addWidget(QLabel("PORTAL"), 0, 0)
        gl.addWidget(self.url, 0, 1)
        gl.addWidget(QLabel("USER"), 0, 2)
        gl.addWidget(self.usr, 0, 3)
        gl.addWidget(QLabel("PASS"), 0, 4)
        gl.addWidget(self.pw, 0, 5)
        gl.addWidget(QLabel("M3U"), 1, 0)
        gl.addWidget(self.m3u, 1, 1, 1, 4)
        
        b = QPushButton("LOAD")
        b.setStyleSheet("background:#00d4ff;color:black;font-weight:bold;padding:10px;border-radius:6px;")
        b.clicked.connect(self.load_m3u)
        gl.addWidget(b, 1, 5)
        t1l.addWidget(c_f)
        
        self.tbl = QTableWidget(0, 7)
        self.tbl.setHorizontalHeaderLabels(["SEL", "CHANNEL", "REC", "STATUS", "UPTIME", "DISK", "ACTION"])
        self.tbl.horizontalHeader().setSectionResizeMode(1)
        t1l.addWidget(self.tbl)
        
        acts = QHBoxLayout()
        b1 = QPushButton("START STREAMING")
        b1.setStyleSheet("background:#00e676;color:black;font-weight:bold;padding:15px;")
        b1.clicked.connect(self.start_sel)
        
        b2 = QPushButton("STOP ALL")
        b2.setStyleSheet("background:#ff2e63;color:white;font-weight:bold;padding:15px;")
        b2.clicked.connect(self.stop_all)
        
        acts.addWidget(b1)
        acts.addWidget(b2)
        t1l.addLayout(acts)
        tabs.addTab(t1, "📡 OPERATIONS")

        # Tab 2: Analytics
        t2 = QWidget()
        t2l = QVBoxLayout(t2)
        gs = QHBoxLayout()
        self.g_cpu = ProGauge("CPU", "%")
        self.g_ram = ProGauge("RAM", "%", color="#aa00ff")
        self.g_dl = ProGauge("DL", "MB", 50, "#00e676")
        self.g_ul = ProGauge("UL", "MB", 50, "#ffea00")
        
        gs.addWidget(self.g_cpu)
        gs.addWidget(self.g_ram)
        gs.addWidget(self.g_dl)
        gs.addWidget(self.g_ul)
        t2l.addLayout(gs)
        
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        t2l.addWidget(QLabel("LOGS:"))
        t2l.addWidget(self.log)
        tabs.addTab(t2, "📊 METRICS")

        # Tab 3: System Tools
        t3 = QWidget()
        t3l = QGridLayout(t3)
        t3l.setSpacing(20)
        
        btn_open = ToolButton("OPEN RECORDINGS", "📂", "#3f51b5")
        btn_open.clicked.connect(self.tool_open_folder)
        btn_clean = ToolButton("CLEAN DISK NOW", "🧹", "#ff9800")
        btn_clean.clicked.connect(self.tool_clean_disk)
        btn_reboot = ToolButton("REBOOT SERVER", "🔄", "#d32f2f")
        btn_reboot.clicked.connect(self.tool_reboot)
        btn_restart = ToolButton("RESTART APP", "🛑", "#00bcd4")
        btn_restart.clicked.connect(self.tool_restart_app)
        
        t3l.addWidget(btn_open, 0, 0)
        t3l.addWidget(btn_clean, 0, 1)
        t3l.addWidget(btn_reboot, 1, 0)
        t3l.addWidget(btn_restart, 1, 1)
        t3l.addWidget(QLabel("System Tools Area - Use with caution"), 2, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
        
        tabs.addTab(t3, "🛠️ SYSTEM TOOLS")

    # --- פונקציות ---
    def tool_open_folder(self):
        try:
            subprocess.Popen(['xdg-open', RECORDINGS_PATH])
            self.add_log("Opening recordings folder...")
        except:
            QMessageBox.warning(self, "Error", "Cannot open folder (No GUI File Manager found).")

    def tool_clean_disk(self):
        reply = QMessageBox.question(self, 'Clean Disk', "Delete old recordings?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            os.system("/root/clean_recordings.sh")
            self.add_log("Disk cleanup executed.")

    def tool_reboot(self):
        reply = QMessageBox.critical(self, 'REBOOT', "Reboot Server?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            os.system("reboot")

    def tool_restart_app(self):
        QApplication.quit()
        os.execl(sys.executable, sys.executable, *sys.argv)

    def add_log(self, m):
        self.log.append(f"[{datetime.now().strftime('%H:%M')}] {m}")

    def upd_stats(self):
        self.g_cpu.set_value(psutil.cpu_percent())
        self.g_ram.set_value(psutil.virtual_memory().percent)
        n = psutil.net_io_counters()
        self.g_dl.set_value((n.bytes_recv - self.net_io.bytes_recv) / 1048576)
        self.g_ul.set_value((n.bytes_sent - self.net_io.bytes_sent) / 1048576)
        self.net_io = n

    def load_m3u(self):
        try:
            d = requests.get(self.m3u.text(), timeout=10).text
            self.tbl.setRowCount(0)
            self.db = []
            n = "Cam"
            for l in d.splitlines():
                if "#EXTINF" in l: n = l.split(",")[-1].strip()
                elif l.startswith("http"):
                    r = self.tbl.rowCount()
                    self.tbl.insertRow(r)
                    self.db.append({"name": n, "url": l})
                    
                    chk = QCheckBox()
                    cw = QWidget()
                    cl = QHBoxLayout(cw)
                    cl.addWidget(chk)
                    cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.tbl.setCellWidget(r, 0, cw)
                    
                    self.tbl.setItem(r, 1, QTableWidgetItem(n))
                    
                    rec = QCheckBox()
                    rec.setChecked(True)
                    rw = QWidget()
                    rl = QHBoxLayout(rw)
                    rl.addWidget(rec)
                    rl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.tbl.setCellWidget(r, 2, rw)
                    
                    self.tbl.setItem(r, 3, QTableWidgetItem("IDLE"))
                    self.tbl.setItem(r, 4, QTableWidgetItem("--"))
                    self.tbl.setItem(r, 5, QTableWidgetItem("0 MB"))
                    
                    b = QPushButton("STOP")
                    b.setStyleSheet("background:#2d303e;color:#ff2e63;")
                    b.clicked.connect(lambda _, x=n: self.stop_one(x))
                    self.tbl.setCellWidget(r, 6, b)
            self.add_log(f"Loaded {len(self.db)} chans")
        except Exception as e:
            self.add_log(f"Err: {e}")

    def start_sel(self):
        cf = {"server": self.url.text(), "user": self.usr.text(), "pass": self.pw.text()}
        for r in range(self.tbl.rowCount()):
            if self.tbl.cellWidget(r, 0).layout().itemAt(0).widget().isChecked():
                n = self.tbl.item(r, 1).text()
                rec = self.tbl.cellWidget(r, 2).layout().itemAt(0).widget().isChecked()
                if n not in self.workers:
                    w = StreamWorker(n, self.db[r]['url'], cf, rec)
                    w.stats_signal.connect(self.upd_row)
                    self.workers[n] = w
                    threading.Thread(target=w.run, daemon=True).start()
        self.save()

    def upd_row(self, n, s):
        for r in range(self.tbl.rowCount()):
            if self.tbl.item(r, 1).text() == n:
                self.tbl.item(r, 3).setText(s['status'])
                self.tbl.item(r, 3).setForeground(QColor("#00e676"))
                self.tbl.item(r, 4).setText(s['uptime'])
                self.tbl.item(r, 5).setText(s['disk'])

    def stop_one(self, n):
        if n in self.workers:
            self.workers[n].stop()
            del self.workers[n]
        for r in range(self.tbl.rowCount()):
            if self.tbl.item(r, 1).text() == n:
                self.tbl.item(r, 3).setText("STOPPED")
                self.tbl.item(r, 3).setForeground(QColor("red"))

    def stop_all(self):
        [w.stop() for w in self.workers.values()]
        self.workers.clear()
        self.add_log("Stopped All")

    def save(self):
        act = [{"name": n, "rec": w.rec} for n, w in self.workers.items()]
        with open(CONFIG_FILE, "w") as f:
            json.dump({"url": self.url.text(), "user": self.usr.text(), "pass": self.pw.text(), "m3u": self.m3u.text(), "act": act}, f)

    def restore(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as f:
                    s = json.load(f)
                self.url.setText(s.get("url", ""))
                self.usr.setText(s.get("user", ""))
                self.pw.setText(s.get("pass", ""))
                self.m3u.setText(s.get("m3u", ""))
                if s.get("m3u"):
                    self.load_m3u()
                    act = {x['name']: x['rec'] for x in s.get('act', [])}
                    for r in range(self.tbl.rowCount()):
                        n = self.tbl.item(r, 1).text()
                        if n in act:
                            self.tbl.cellWidget(r, 0).layout().itemAt(0).widget().setChecked(True)
                            self.tbl.cellWidget(r, 2).layout().itemAt(0).widget().setChecked(act[n])
                    self.start_sel()
            except: pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = XHotelUI()
    w.show()
    sys.exit(app.exec())
