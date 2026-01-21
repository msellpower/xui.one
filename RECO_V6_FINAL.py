import sys, subprocess, threading, time, os, re, requests, psutil, json, math
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QLabel, QCheckBox, 
                             QFrame, QProgressBar, QMessageBox, QGridLayout, QTabWidget, QTextEdit)
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QTimer, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont

# --- הגדרות טלגרם ---
TELEGRAM_TOKEN = "8307008722:AAHY-QYNYyTnOwjS0q4VGfA0_iUiQBxYHBc"
TELEGRAM_CHAT_ID = "-5125327073"

# --- הגדרות מערכת ---
os.environ["QT_QUICK_BACKEND"] = "software"
CONFIG_FILE = "/root/iptv_config.json"
RECORDINGS_PATH = "/root/Recordings"

# --- רכיב גרפי: שעון אנליטי (Gauge) ---
class AnalogGauge(QWidget):
    def __init__(self, title="Metric", max_val=100, unit="%"):
        super().__init__()
        self.value = 0
        self.max_val = max_val
        self.title = title
        self.unit = unit
        self.setMinimumSize(150, 150)

    def set_value(self, val):
        self.value = val
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # חישובים
        w, h = self.width(), self.height()
        size = min(w, h)
        rect = QRectF((w-size)/2 + 10, (h-size)/2 + 10, size-20, size-20)
        
        # רקע
        painter.setPen(QPen(QColor("#2c2f33"), 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 135 * 16, 270 * 16)

        # קשת הערך (צבע משתנה לפי עומס)
        if self.value > self.max_val * 0.8: color = QColor("#ff4d4d") # Red
        elif self.value > self.max_val * 0.5: color = QColor("#ffcc00") # Yellow
        else: color = QColor("#00e676") # Green
        
        painter.setPen(QPen(color, 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        angle = int(270 * (self.value / self.max_val))
        if angle > 270: angle = 270
        painter.drawArc(rect, 225 * 16, -angle * 16)

        # טקסט
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{self.value:.1f}{self.unit}")
        
        painter.setFont(QFont("Arial", 10))
        painter.drawText(int(w/2)-50, int(h)-20, 100, 20, Qt.AlignmentFlag.AlignCenter, self.title)

# --- לוגיקת שידור (Backend) ---
class StreamWorker(QObject):
    stats_signal = pyqtSignal(str, dict)
    log_signal = pyqtSignal(str)

    def __init__(self, name, url, config, record_to_disk=True):
        super().__init__()
        self.name = name
        self.url = url
        self.config = config
        self.record_to_disk = record_to_disk
        self.is_running = True
        self.process = None

    def _send_telegram(self, msg):
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=3)
        except: pass

    def _get_api_stream_url(self):
        """פונקציה חכמה ליצירת קטגוריה וסטרים ב-XUI"""
        if not self.config or not self.config.get('server'): return None
        
        c = self.config
        base = c['server'].split('/dashboard')[0].rstrip('/')
        api = f"{base}/api.php"
        auth = f"username={c['user']}&password={c['pass']}"
        
        try:
            # 1. השגת ID קטגוריה
            cat_id = "1"
            cats = requests.get(f"{api}?action=get_categories&{auth}", timeout=5).json()
            found = False
            for cat in cats:
                if cat.get('category_name') == "Channels":
                    cat_id = cat.get('category_id')
                    found = True
                    break
            
            if not found:
                self.log_signal.emit(f"Creating category 'Channels'...")
                res = requests.post(f"{api}?action=add_category", data={**c, "category_name": "Channels", "category_type": "live"}).json()
                cat_id = res.get('category_id', "1")

            # 2. יצירת סטרים
            safe_name = re.sub(r'[\\/*?:"<>|]', "", self.name).strip()
            requests.post(f"{api}?action=add_stream", data={
                "username": c['user'], "password": c['pass'],
                "stream_display_name": self.name, "stream_source": ["127.0.0.1"],
                "category_id": cat_id, "stream_mode": "live"
            })
            
            return f"{base}/live/{c['user']}/{c['pass']}/{safe_name}.ts"
        except Exception as e:
            self.log_signal.emit(f"API Error ({self.name}): {e}")
            return None

    def run(self):
        start_ts = time.time()
        safe_name = self.name.replace(" ", "_")
        folder = os.path.join(RECORDINGS_PATH, safe_name)
        if self.record_to_disk:
            os.makedirs(folder, exist_ok=True)

        xui_link = self._get_api_stream_url()
        self._send_telegram(f"🎬 <b>ערוץ הופעל:</b> {self.name}\n<b>הקלטה:</b> {'פעילה' if self.record_to_disk else 'כבויה'}")

        while self.is_running:
            # בניית פקודת FFmpeg לאמינות מקסימלית (Production Grade)
            cmd = [
                'ffmpeg', '-y', 
                '-rtsp_transport', 'tcp', # מונע מריחות בשידור
                '-stimeout', '5000000',   # Timeout של 5 שניות אם המצלמה נופלת
                '-i', self.url,
                '-c', 'copy', '-f', 'mpegts'
            ]

            output_targets = []
            
            # יעד 1: הקלטה לדיסק (אם נבחר)
            current_file = ""
            if self.record_to_disk:
                timestamp = datetime.now().strftime("%H%M%S")
                current_file = os.path.join(folder, f"{timestamp}.ts")
                output_targets.append(f"[f=mpegts]{current_file}")
            
            # יעד 2: שידור ל-XUI
            if xui_link:
                output_targets.append(f"[f=mpegts:onfail=ignore]{xui_link}")
            
            # הרכבת ה-TEE Muxer
            if output_targets:
                tee_cmd = "|".join(output_targets)
                cmd.extend(['-f', 'tee', tee_cmd])
            else:
                # Fallback למקרה שאין לאן לשדר
                cmd.extend(['-f', 'null', '-'])

            self.log_signal.emit(f"Starting FFmpeg for {self.name}")
            
            try:
                self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                
                # לולאת ניטור
                while self.process.poll() is None and self.is_running:
                    uptime = time.strftime("%H:%M:%S", time.gmtime(time.time() - start_ts))
                    
                    disk_usage = 0
                    if self.record_to_disk:
                        try: disk_usage = sum(os.path.getsize(os.path.join(folder, f)) for f in os.listdir(folder))/1048576
                        except: pass
                    
                    self.stats_signal.emit(self.name, {
                        "status": "Active", "uptime": uptime, 
                        "disk": f"{disk_usage:.1f} MB", "link": xui_link or "N/A"
                    })
                    time.sleep(3)
                
                if self.is_running:
                    self.log_signal.emit(f"⚠️ Stream {self.name} dropped. Reconnecting...")
                    self._send_telegram(f"⚠️ <b>ניתוק זוהה:</b> {self.name}\nמנסה להתחבר מחדש...")
                    time.sleep(5)
            except Exception as e:
                self.log_signal.emit(f"Critical Error: {e}")
                time.sleep(10)

    def stop(self):
        self.is_running = False
        if self.process: self.process.terminate()

# --- ממשק משתמש (GUI) ---
class HotelDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("XUI Hotel Enterprise Manager v10.0")
        self.resize(1600, 950)
        self.workers = {}
        self.net_io = psutil.net_io_counters()
        self.setup_ui()
        self.apply_styles()
        QTimer.singleShot(1000, self.restore_state)

    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #121212; color: #e0e0e0; }
            QTabWidget::pane { border: 1px solid #333; background: #1e1e1e; }
            QTabBar::tab { background: #2c2c2c; padding: 12px 25px; margin-right: 2px; color: #aaa; }
            QTabBar::tab:selected { background: #007acc; color: white; font-weight: bold; }
            QLineEdit { background: #252526; border: 1px solid #3e3e42; color: white; padding: 5px; }
            QPushButton { background: #0e639c; color: white; border: none; padding: 8px; font-weight: bold; }
            QPushButton:hover { background: #1177bb; }
            QPushButton#stop_btn { background: #d32f2f; }
            QTableWidget { background: #1e1e1e; border: none; gridline-color: #333; }
            QHeaderView::section { background: #252526; padding: 5px; border: none; font-weight: bold; }
        """)

    def setup_ui(self):
        tabs = QTabWidget(); self.setCentralWidget(tabs)
        
        # --- TAB 1: OPERATIONS ---
        ops_widget = QWidget(); ops_layout = QVBoxLayout(ops_widget)
        
        # Config Area
        conf_group = QFrame(); conf_layout = QGridLayout(conf_group)
        self.url_in = QLineEdit("http://144.91.86.250/mbmWePBa")
        self.user_in = QLineEdit("admin"); self.pass_in = QLineEdit("MazalTovLanu")
        self.m3u_in = QLineEdit(); self.m3u_in.setPlaceholderText("Paste M3U URL...")
        
        conf_layout.addWidget(QLabel("Portal:"), 0, 0); conf_layout.addWidget(self.url_in, 0, 1)
        conf_layout.addWidget(QLabel("User:"), 0, 2); conf_layout.addWidget(self.user_in, 0, 3)
        conf_layout.addWidget(QLabel("Pass:"), 0, 4); conf_layout.addWidget(self.pass_in, 0, 5)
        conf_layout.addWidget(QLabel("M3U List:"), 1, 0); conf_layout.addWidget(self.m3u_in, 1, 1, 1, 4)
        load_btn = QPushButton("Load Channels"); load_btn.clicked.connect(self.load_m3u)
        conf_layout.addWidget(load_btn, 1, 5)
        ops_layout.addWidget(conf_group)

        # Table
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Sel", "Channel", "Rec?", "Status", "Uptime", "Storage", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 50); self.table.setColumnWidth(2, 50)
        ops_layout.addWidget(self.table)
        
        # Global Actions
        act_layout = QHBoxLayout()
        start_all = QPushButton("START SELECTED"); start_all.clicked.connect(self.start_selected)
        stop_all = QPushButton("STOP ALL SYSTEM"); stop_all.setObjectName("stop_btn"); stop_all.clicked.connect(self.stop_all)
        act_layout.addWidget(start_all); act_layout.addWidget(stop_all)
        ops_layout.addLayout(act_layout)
        
        tabs.addTab(ops_widget, "📡 Live Operations")

        # --- TAB 2: ANALYTICS (SCALES) ---
        stats_widget = QWidget(); stats_layout = QGridLayout(stats_widget)
        
        self.cpu_gauge = AnalogGauge("CPU Load", 100, "%")
        self.ram_gauge = AnalogGauge("RAM Usage", 100, "%")
        self.net_in_gauge = AnalogGauge("Network IN", 50, "MB/s") # Scale up to 50MB/s
        self.net_out_gauge = AnalogGauge("Network OUT", 50, "MB/s")
        
        stats_layout.addWidget(self.cpu_gauge, 0, 0); stats_layout.addWidget(self.ram_gauge, 0, 1)
        stats_layout.addWidget(self.net_in_gauge, 1, 0); stats_layout.addWidget(self.net_out_gauge, 1, 1)
        
        # Logs Console
        self.logs = QTextEdit(); self.logs.setReadOnly(True)
        stats_layout.addWidget(QLabel("System Logs:"), 2, 0)
        stats_layout.addWidget(self.logs, 3, 0, 1, 2)
        
        tabs.addTab(stats_widget, "📊 Analytics & Health")

        # Updates Timer
        self.timer = QTimer(); self.timer.timeout.connect(self.update_system_stats); self.timer.start(1500)

    def update_system_stats(self):
        # CPU/RAM
        self.cpu_gauge.set_value(psutil.cpu_percent())
        self.ram_gauge.set_value(psutil.virtual_memory().percent)
        
        # Network Speed Calc
        new_io = psutil.net_io_counters()
        down = (new_io.bytes_recv - self.net_io.bytes_recv) / (1024 * 1024) # MB
        up = (new_io.bytes_sent - self.net_io.bytes_sent) / (1024 * 1024)   # MB
        self.net_in_gauge.set_value(down * 0.66) # Adjusted for per-second view
        self.net_out_gauge.set_value(up * 0.66)
        self.net_io = new_io

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{ts}] {msg}")
        # Auto scroll
        sb = self.logs.verticalScrollBar()
        sb.setValue(sb.maximum())

    def load_m3u(self):
        url = self.m3u_in.text()
        if not url: return
        try:
            self.log("Downloading M3U...")
            data = requests.get(url, timeout=10).text
            self.table.setRowCount(0)
            self.channels_db = []
            
            name = "Unknown"
            for line in data.splitlines():
                if "#EXTINF" in line:
                    name = line.split(",")[-1].strip()
                elif line.startswith("http"):
                    r = self.table.rowCount()
                    self.table.insertRow(r)
                    
                    # Checkbox Select
                    chk = QCheckBox(); chk_w = QWidget(); l = QHBoxLayout(chk_w); l.addWidget(chk); l.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.table.setCellWidget(r, 0, chk_w)
                    
                    # Name
                    self.table.setItem(r, 1, QTableWidgetItem(name))
                    
                    # Record Toggle (New Feature)
                    rec_chk = QCheckBox(); rec_chk.setChecked(True) # Default ON
                    rec_w = QWidget(); rl = QHBoxLayout(rec_w); rl.addWidget(rec_chk); rl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.table.setCellWidget(r, 2, rec_w)
                    
                    self.table.setItem(r, 3, QTableWidgetItem("Idle"))
                    self.table.setItem(r, 4, QTableWidgetItem("00:00"))
                    self.table.setItem(r, 5, QTableWidgetItem("0 MB"))
                    
                    # Individual Action
                    btn = QPushButton("Toggle"); btn.clicked.connect(lambda _, n=name: self.toggle_single(n))
                    self.table.setCellWidget(r, 6, btn)
                    
                    self.channels_db.append({"name": name, "url": line})
            self.log(f"Loaded {len(self.channels_db)} channels.")
        except Exception as e:
            self.log(f"Error loading M3U: {e}")

    def start_selected(self):
        conf = {"server": self.url_in.text(), "user": self.user_in.text(), "pass": self.pass_in.text()}
        
        for r in range(self.table.rowCount()):
            # Check if selected
            sel_chk = self.table.cellWidget(r, 0).layout().itemAt(0).widget()
            if sel_chk.isChecked():
                name = self.table.item(r, 1).text()
                # Check record status
                rec_chk = self.table.cellWidget(r, 2).layout().itemAt(0).widget()
                should_rec = rec_chk.isChecked()
                
                if name not in self.workers:
                    url = self.channels_db[r]["url"]
                    self.start_worker(name, url, conf, should_rec)
        
        self.save_state()

    def start_worker(self, name, url, conf, record):
        w = StreamWorker(name, url, conf, record)
        w.stats_signal.connect(self.update_row_stats)
        w.log_signal.connect(self.log)
        threading.Thread(target=w.run, daemon=True).start()
        self.workers[name] = w

    def toggle_single(self, name):
        if name in self.workers:
            self.workers[name].stop()
            del self.workers[name]
            self.log(f"Stopped {name}")
            # Update UI to Idle
            for r in range(self.table.rowCount()):
                if self.table.item(r, 1).text() == name:
                    self.table.item(r, 3).setText("Stopped")
                    self.table.item(r, 3).setForeground(Qt.GlobalColor.red)
        else:
            # Find and start
            self.log(f"Starting {name} manually...")
            # Logic to find URL and start (Simplified for bulk logic)
            self.start_selected() 

    def update_row_stats(self, name, data):
        for r in range(self.table.rowCount()):
            if self.table.item(r, 1).text() == name:
                item_status = self.table.item(r, 3)
                item_status.setText(data['status'])
                item_status.setForeground(QColor("#00e676")) # Green
                
                self.table.item(r, 4).setText(data['uptime'])
                self.table.item(r, 5).setText(data['disk'])

    def stop_all(self):
        for w in self.workers.values(): w.stop()
        self.workers.clear()
        self.log("All streams stopped.")

    def save_state(self):
        active_list = []
        for name, w in self.workers.items():
            active_list.append({"name": name, "rec": w.record_to_disk})
            
        state = {
            "url": self.url_in.text(), "user": self.user_in.text(), "pass": self.pass_in.text(),
            "m3u": self.m3u_in.text(), "active": active_list
        }
        with open(CONFIG_FILE, "w") as f: json.dump(state, f)

    def restore_state(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f: s = json.load(f)
                self.url_in.setText(s.get("url","")); self.user_in.setText(s.get("user",""))
                self.pass_in.setText(s.get("pass","")); self.m3u_in.setText(s.get("m3u",""))
                
                if s.get("m3u"):
                    self.load_m3u()
                    # Restore active
                    active_map = {item["name"]: item["rec"] for item in s.get("active", [])}
                    
                    conf = {"server": self.url_in.text(), "user": self.user_in.text(), "pass": self.pass_in.text()}
                    
                    for r in range(self.table.rowCount()):
                        name = self.table.item(r, 1).text()
                        if name in active_map:
                            # Set Selected
                            self.table.cellWidget(r, 0).layout().itemAt(0).widget().setChecked(True)
                            # Set Recording Preference
                            self.table.cellWidget(r, 2).layout().itemAt(0).widget().setChecked(active_map[name])
                            
                            # Auto Start
                            url = self.channels_db[r]["url"]
                            self.start_worker(name, url, conf, active_map[name])
            except Exception as e: self.log(f"Restore Error: {e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = HotelDashboard()
    win.show()
    sys.exit(app.exec())
