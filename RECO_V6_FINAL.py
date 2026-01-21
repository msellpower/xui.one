import sys, subprocess, threading, time, os, re, requests, psutil, json
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QLabel, QCheckBox, 
                             QFrame, QMessageBox, QGridLayout, QTabWidget, QTextEdit)
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QTimer, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QLinearGradient

# --- הגדרות מערכת ---
os.environ["QT_QUICK_BACKEND"] = "software"
TELEGRAM_TOKEN = "8307008722:AAHY-QYNYyTnOwjS0q4VGfA0_iUiQBxYHBc"
TELEGRAM_CHAT_ID = "-5125327073"
CONFIG_FILE = "/root/iptv_config.json"
RECORDINGS_PATH = "/root/Recordings"

# --- רכיב UX: שעון אנליטי מעוצב (Pro Gauge) ---
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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        
        # מרכוז
        size = min(w, h) - 20
        rect = QRectF((w-size)/2, (h-size)/2, size, size)
        
        # 1. רקע המד (Track) - אפור כהה
        painter.setPen(QPen(QColor("#2d303e"), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 135 * 16, 270 * 16)
        
        # 2. חישוב זווית
        ratio = self.value / self.max_val
        angle = int(270 * ratio)
        if angle > 270: angle = 270
        
        # 3. שינוי צבע דינמי לעומס
        pen_color = self.primary_color
        if ratio > 0.85: pen_color = QColor("#ff2e63") # Red Alert
        
        # 4. ציור הערך
        pen = QPen(pen_color, 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 225 * 16, -angle * 16)
        
        # 5. טקסטים (Typography)
        painter.setPen(QColor("#ffffff"))
        # הערך המרכזי
        font_val = QFont("Segoe UI", 22, QFont.Weight.Bold)
        painter.setFont(font_val)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{self.value:.1f}{self.unit}")
        
        # הכותרת למטה
        font_title = QFont("Segoe UI", 10, QFont.Weight.Normal)
        font_title.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5) # ריווח אותיות יוקרתי
        painter.setFont(font_title)
        painter.setPen(QColor("#a6accd"))
        painter.drawText(int(w/2)-50, int(h)-30, 100, 20, Qt.AlignmentFlag.AlignCenter, self.title.upper())

# --- לוגיקת שרת (Backend) ---
class StreamWorker(QObject):
    stats_signal = pyqtSignal(str, dict)
    log_signal = pyqtSignal(str)

    def __init__(self, name, url, config, record_to_disk=True):
        super().__init__()
        self.name = name; self.url = url; self.config = config; self.record_to_disk = record_to_disk
        self.is_running = True; self.process = None

    def _get_xui_link(self):
        # לוגיקה מקוצרת ליצירת קטגוריה ולינק - זהה לגרסה 10
        try:
            c = self.config
            base = c['server'].split('/dashboard')[0].rstrip('/')
            api = f"{base}/api.php"
            auth = f"username={c['user']}&password={c['pass']}"
            
            # חיפוש/יצירת קטגוריה
            res = requests.get(f"{api}?action=get_categories&{auth}", timeout=5).json()
            cat_id = next((x['category_id'] for x in res if x['category_name']=="Channels"), None)
            if not cat_id:
                res = requests.post(f"{api}?action=add_category", data={**c, "category_name":"Channels","category_type":"live"}).json()
                cat_id = res.get('category_id', "1")

            # יצירת סטרים
            requests.post(f"{api}?action=add_stream", data={"username":c['user'],"password":c['pass'],"stream_display_name":self.name,"stream_source":["127.0.0.1"],"category_id":cat_id,"stream_mode":"live"})
            return f"{base}/live/{c['user']}/{c['pass']}/{self.name.replace(' ','_')}.ts"
        except: return None

    def run(self):
        start_ts = time.time()
        safe_name = self.name.replace(" ", "_")
        folder = os.path.join(RECORDINGS_PATH, safe_name)
        if self.record_to_disk: os.makedirs(folder, exist_ok=True)
        
        xui = self._get_xui_link()
        try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": TELEGRAM_CHAT_ID, "text": f"🟢 <b>ONLINE:</b> {self.name}"}) 
        except: pass

        while self.is_running:
            cmd = ['ffmpeg', '-y', '-rtsp_transport', 'tcp', '-stimeout', '5000000', '-i', self.url, '-c', 'copy', '-f', 'mpegts']
            
            targets = []
            if self.record_to_disk: targets.append(f"[f=mpegts]{os.path.join(folder, datetime.now().strftime('%H%M%S') + '.ts')}")
            if xui: targets.append(f"[f=mpegts:onfail=ignore]{xui}")
            
            if targets: cmd.extend(['-f', 'tee', "|".join(targets)])
            else: cmd.extend(['-f', 'null', '-'])

            try:
                self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                while self.process.poll() is None and self.is_running:
                    uptime = time.strftime("%H:%M:%S", time.gmtime(time.time() - start_ts))
                    disk = 0
                    if self.record_to_disk:
                        try: disk = sum(os.path.getsize(os.path.join(folder,f)) for f in os.listdir(folder))/1048576
                        except: pass
                    self.stats_signal.emit(self.name, {"status": "ACTIVE", "uptime": uptime, "disk": f"{disk:.1f} MB", "link": xui or "N/A"})
                    time.sleep(3)
                if self.is_running: time.sleep(5)
            except: time.sleep(10)

    def stop(self):
        self.is_running = False
        if self.process: self.process.terminate()

# --- ממשק UX/UI ראשי ---
class XHotelUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("X-HOTEL CONTROL CENTER v11.0 (UX Edition)")
        self.resize(1600, 1000)
        self.workers = {}
        self.net_io = psutil.net_io_counters()
        self.setup_styles()
        self.setup_ui()
        QTimer.singleShot(500, self.restore_state)
        self.timer = QTimer(); self.timer.timeout.connect(self.update_analytics); self.timer.start(1000)

    def setup_styles(self):
        # סגנון CSS מקצועי ומודרני
        self.setStyleSheet("""
            QMainWindow { background-color: #10121b; }
            QWidget { font-family: 'Segoe UI', sans-serif; font-size: 14px; color: #e0e6ed; }
            
            /* Tab Styling */
            QTabWidget::pane { border: 1px solid #2d303e; background: #151722; border-radius: 8px; margin: 10px; }
            QTabBar::tab { 
                background: #1f2233; color: #7a8299; 
                padding: 12px 30px; margin-right: 5px; 
                border-top-left-radius: 8px; border-top-right-radius: 8px;
                font-weight: bold; font-size: 13px;
            }
            QTabBar::tab:selected { background: #2d303e; color: #00d4ff; border-bottom: 2px solid #00d4ff; }
            
            /* Inputs & Cards */
            QFrame#Card { background-color: #1f2233; border-radius: 12px; border: 1px solid #2d303e; }
            QLineEdit { 
                background: #10121b; border: 1px solid #3b3f51; border-radius: 6px; 
                padding: 10px; color: #00d4ff; font-weight: bold; selection-background-color: #00d4ff;
            }
            QLineEdit:focus { border: 1px solid #00d4ff; }
            
            /* Buttons */
            QPushButton { 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0093b5, stop:1 #00d4ff);
                color: #000; border-radius: 6px; padding: 10px 20px; font-weight: 800; border: none;
            }
            QPushButton:hover { background: #40e0d0; }
            QPushButton#danger { background: #ff2e63; color: white; }
            QPushButton#danger:hover { background: #ff5c8d; }
            QPushButton#action { background: #2d303e; border: 1px solid #3b3f51; color: #00d4ff; }
            
            /* Table */
            QTableWidget { background: #151722; border: none; gridline-color: #2d303e; }
            QTableWidget::item { padding: 8px; border-bottom: 1px solid #1f2233; }
            QHeaderView::section { 
                background: #1f2233; color: #7a8299; border: none; padding: 8px; 
                font-weight: bold; text-transform: uppercase; letter-spacing: 1px;
            }
            
            /* Logs */
            QTextEdit { background: #0a0b10; border: 1px solid #2d303e; border-radius: 8px; font-family: 'Consolas', monospace; color: #00e676; }
        """)

    def setup_ui(self):
        main = QWidget(); self.setCentralWidget(main); layout = QVBoxLayout(main)
        
        # Header
        head = QFrame(); head_l = QHBoxLayout(head)
        title = QLabel("X-HOTEL CONTROL CENTER"); title.setStyleSheet("font-size: 24px; font-weight: 900; color: #00d4ff; letter-spacing: 2px;")
        head_l.addWidget(title); head_l.addStretch()
        layout.addWidget(head)

        tabs = QTabWidget(); layout.addWidget(tabs)

        # --- Tab 1: Live Control ---
        t1 = QWidget(); t1_l = QVBoxLayout(t1)
        
        # Configuration Card
        conf_card = QFrame(); conf_card.setObjectName("Card"); conf_l = QGridLayout(conf_card)
        conf_l.setContentsMargins(20,20,20,20)
        
        self.url = QLineEdit("http://144.91.86.250/mbmWePBa"); self.user = QLineEdit("admin"); self.pw = QLineEdit("MazalTovLanu")
        self.m3u = QLineEdit(); self.m3u.setPlaceholderText("Paste M3U URL Here...")
        
        conf_l.addWidget(QLabel("PORTAL URL"), 0,0); conf_l.addWidget(self.url, 0,1)
        conf_l.addWidget(QLabel("USER"), 0,2); conf_l.addWidget(self.user, 0,3)
        conf_l.addWidget(QLabel("PASS"), 0,4); conf_l.addWidget(self.pw, 0,5)
        conf_l.addWidget(QLabel("M3U SOURCE"), 1,0); conf_l.addWidget(self.m3u, 1,1,1,4)
        
        load_btn = QPushButton("LOAD DATA"); load_btn.clicked.connect(self.load_m3u)
        conf_l.addWidget(load_btn, 1,5)
        t1_l.addWidget(conf_card)

        # Table
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["SEL", "CHANNEL NAME", "REC MODE", "STATUS", "UPTIME", "DISK USAGE", "CONTROLS"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        t1_l.addWidget(self.table)

        # Footer Actions
        foot = QHBoxLayout()
        start_btn = QPushButton("INITIATE STREAMING"); start_btn.setFixedHeight(50); start_btn.clicked.connect(self.start_selected)
        stop_btn = QPushButton("EMERGENCY STOP"); stop_btn.setObjectName("danger"); stop_btn.setFixedHeight(50); stop_btn.clicked.connect(self.stop_all)
        foot.addWidget(start_btn); foot.addWidget(stop_btn)
        t1_l.addLayout(foot)
        
        tabs.addTab(t1, "📡 LIVE OPERATIONS")

        # --- Tab 2: Analytics ---
        t2 = QWidget(); t2_l = QVBoxLayout(t2)
        
        gauges = QHBoxLayout()
        self.g_cpu = ProGauge("CPU LOAD", "%"); self.g_ram = ProGauge("RAM USAGE", "%", color="#aa00ff")
        self.g_dl = ProGauge("DOWNLOAD", "MB", 50, "#00e676"); self.g_ul = ProGauge("UPLOAD", "MB", 50, "#ffea00")
        gauges.addWidget(self.g_cpu); gauges.addWidget(self.g_ram); gauges.addWidget(self.g_dl); gauges.addWidget(self.g_ul)
        t2_l.addLayout(gauges)

        self.logs = QTextEdit(); self.logs.setReadOnly(True)
        t2_l.addWidget(QLabel("SYSTEM EVENT LOGS:")); t2_l.addWidget(self.logs)
        tabs.addTab(t2, "📊 ANALYTICS & DIAGNOSTICS")

    def log(self, msg):
        self.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def update_analytics(self):
        self.g_cpu.set_value(psutil.cpu_percent())
        self.g_ram.set_value(psutil.virtual_memory().percent)
        
        nio = psutil.net_io_counters()
        dl = (nio.bytes_recv - self.net_io.bytes_recv) / 1024 / 1024
        ul = (nio.bytes_sent - self.net_io.bytes_sent) / 1024 / 1024
        self.g_dl.set_value(dl); self.g_ul.set_value(ul)
        self.net_io = nio

    def load_m3u(self):
        try:
            data = requests.get(self.m3u.text(), timeout=10).text
            self.table.setRowCount(0); self.db = []
            name = "Unknown"
            for line in data.splitlines():
                if "#EXTINF" in line: name = line.split(",")[-1].strip()
                elif line.startswith("http"):
                    r = self.table.rowCount(); self.table.insertRow(r); self.db.append({"name":name,"url":line})
                    
                    chk = QCheckBox(); c_w = QWidget(); l = QHBoxLayout(c_w); l.addWidget(chk); l.setAlignment(Qt.AlignmentFlag.AlignCenter); self.table.setCellWidget(r, 0, c_w)
                    self.table.setItem(r, 1, QTableWidgetItem(name))
                    
                    rec = QCheckBox("Save to Disk"); rec.setChecked(True); r_w = QWidget(); rl = QHBoxLayout(r_w); rl.addWidget(rec); rl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.table.setCellWidget(r, 2, r_w)
                    
                    self.table.setItem(r, 3, QTableWidgetItem("IDLE")); self.table.item(r,3).setForeground(QColor("#7a8299"))
                    self.table.setItem(r, 4, QTableWidgetItem("--:--")); self.table.setItem(r, 5, QTableWidgetItem("0 MB"))
                    
                    btn = QPushButton("STOP"); btn.setObjectName("action"); btn.clicked.connect(lambda _, n=name: self.stop_one(n)); self.table.setCellWidget(r, 6, btn)
            self.log(f"M3U Parsed: {len(self.db)} Channels found.")
        except Exception as e: self.log(f"Error: {e}")

    def start_selected(self):
        conf = {"server":self.url.text(),"user":self.user.text(),"pass":self.pw.text()}
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r,0).layout().itemAt(0).widget().isChecked():
                name = self.table.item(r,1).text()
                rec = self.table.cellWidget(r,2).layout().itemAt(0).widget().isChecked()
                if name not in self.workers:
                    w = StreamWorker(name, self.db[r]["url"], conf, rec)
                    w.stats_signal.connect(self.update_row); self.workers[name] = w
                    threading.Thread(target=w.run, daemon=True).start()
        self.save_state()

    def update_row(self, name, s):
        for r in range(self.table.rowCount()):
            if self.table.item(r,1).text() == name:
                self.table.item(r,3).setText(s['status']); self.table.item(r,3).setForeground(QColor("#00e676"))
                self.table.item(r,4).setText(s['uptime']); self.table.item(r,5).setText(s['disk'])

    def stop_one(self, name):
        if name in self.workers:
            self.workers[name].stop(); del self.workers[name]
            for r in range(self.table.rowCount()):
                if self.table.item(r,1).text()==name: 
                    self.table.item(r,3).setText("STOPPED"); self.table.item(r,3).setForeground(QColor("#ff2e63"))

    def stop_all(self):
        for w in self.workers.values(): w.stop()
        self.workers.clear(); self.log("Emergency Stop Triggered.")

    def save_state(self):
        active = [{"name":n, "rec":w.record_to_disk} for n,w in self.workers.items()]
        with open(CONFIG_FILE, "w") as f: json.dump({"url":self.url.text(),"user":self.user.text(),"pass":self.pw.text(),"m3u":self.m3u.text(),"active":active}, f)

    def restore_state(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE,"r") as f: s = json.load(f)
                self.url.setText(s.get("url","")); self.user.setText(s.get("user","")); self.pw.setText(s.get("pass","")); self.m3u.setText(s.get("m3u",""))
                if s.get("m3u"):
                    self.load_m3u()
                    active = {x['name']:x['rec'] for x in s.get('active',[])}
                    for r in range(self.table.rowCount()):
                        name = self.table.item(r,1).text()
                        if name in active:
                            self.table.cellWidget(r,0).layout().itemAt(0).widget().setChecked(True)
                            self.table.cellWidget(r,2).layout().itemAt(0).widget().setChecked(active[name])
                    self.start_selected()
            except: pass

if __name__ == "__main__":
    app = QApplication(sys.argv); win = XHotelUI(); win.show(); sys.exit(app.exec())
