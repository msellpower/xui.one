import sys, subprocess, time, os, re, requests, psutil, json, urllib3
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QLabel, QCheckBox, 
                             QFrame, QMessageBox, QGridLayout, QTabWidget, QTextEdit, QSpinBox)
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QTimer, QRectF, QThread
from PyQt6.QtGui import QPainter, QColor, QPen, QFont

# --- Settings & Security ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
os.environ["QT_QUICK_BACKEND"] = "software"

CONFIG_FILE = "/root/iptv_config.json"
RECORDINGS_PATH = "/root/Recordings"
TELEGRAM_TOKEN = "8307008722:AAHY-QYNYyTnOwjS0q4VGfA0_iUiQBxYHBc"
TELEGRAM_CHAT_ID = "-5125327073" # וודא שזה ה-ID הנכון!

API_HEADERS = {'User-Agent': 'Mozilla/5.0'}
STREAM_HEADERS = {'User-Agent': 'VLC/3.0.18'}

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, verify=False, timeout=5)
    except: pass

# --- UI Components ---
class ProGauge(QWidget):
    def __init__(self, title, unit, max_val=100, color="#00d4ff"):
        super().__init__()
        self.value=0; self.max_val=max_val; self.title=title; self.unit=unit; self.primary_color=QColor(color); self.setMinimumSize(160,160)
    def set_value(self, val): self.value=val; self.update()
    def paintEvent(self, e):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); w,h=self.width(),self.height(); size=min(w,h)-20; rect=QRectF((w-size)/2,(h-size)/2,size,size)
        p.setPen(QPen(QColor("#2d303e"),10,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap)); p.drawArc(rect,135*16,270*16)
        r=min(self.value/self.max_val, 1); a=int(270*r); col=self.primary_color if r<0.85 else QColor("#ff2e63")
        p.setPen(QPen(col,10,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap)); p.drawArc(rect,225*16,-a*16)
        p.setPen(QColor("white")); p.setFont(QFont("Segoe UI",18,QFont.Weight.Bold)); p.drawText(rect,Qt.AlignmentFlag.AlignCenter,f"{self.value:.1f}{self.unit}")
        p.setPen(QColor("#a6accd")); p.setFont(QFont("Segoe UI",9)); p.drawText(0,int(h)-20,int(w),20,Qt.AlignmentFlag.AlignCenter,self.title)

# --- The Engine (Recording Worker) ---
class RecordingWorker(QThread):
    stats_signal = pyqtSignal(str, dict)
    log_signal = pyqtSignal(str)
    
    def __init__(self, name, url, config, record_local):
        super().__init__()
        self.channel_name = name; self.url = url; self.config = config; self.record_local = record_local
        self.is_running = True; self.process = None

    def run(self):
        safe_name = re.sub(r'[\\/*?:"<>|]', "", self.channel_name).strip().replace(" ", "_")
        target_dir = os.path.join(RECORDINGS_PATH, safe_name)
        if self.record_local: os.makedirs(target_dir, exist_ok=True)
        
        send_telegram(f"📡 <b>Live:</b> {self.channel_name}")
        start_time = time.time()
        
        while self.is_running:
            # הזרמה לפאנל (Stream Port)
            stream_url = f"http://{self.config['ip']}:{self.config['stream_port']}/live/{self.config['user']}/{self.config['pass']}/{safe_name}.ts"
            
            cmd = ['ffmpeg', '-y', '-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5',
                   '-headers', f"User-Agent: {STREAM_HEADERS['User-Agent']}\r\n", '-i', self.url, '-c', 'copy']
            
            outputs = [f"[f=mpegts:onfail=ignore]{stream_url}"]
            if self.record_local:
                ts = datetime.now().strftime("%H%M%S")
                outputs.append(f"[f=mpegts]'{os.path.join(target_dir, f'{ts}.ts')}'")
            
            cmd.extend(['-f', 'tee', "|".join(outputs)])
            
            try:
                self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                while self.process.poll() is None and self.is_running:
                    upt = time.strftime("%H:%M:%S", time.gmtime(time.time() - start_time))
                    self.stats_signal.emit(self.channel_name, {"status":"ACTIVE", "uptime":upt})
                    self.msleep(2000)
                
                if self.is_running:
                    self.log_signal.emit(f"⚠️ {self.channel_name} disconnected. Retrying...")
                    self.msleep(5000)
            except Exception as e:
                self.log_signal.emit(f"❌ Error: {str(e)}"); break

    def stop(self):
        self.is_running = False
        if self.process: self.process.terminate()
        self.wait()

# --- Main Interface ---
class XHotelUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("X-HOTEL v34.0 (SENTINEL)"); self.resize(1400, 900)
        self.workers = {}; self.net_io = psutil.net_io_counters()
        self.setup_ui()
        self.restore()
        
        # טיימרים למערכת
        self.stats_timer = QTimer(); self.stats_timer.timeout.connect(self.update_system_metrics); self.stats_timer.start(1000)
        self.watchdog_timer = QTimer(); self.watchdog_timer.timeout.connect(self.disk_watchdog); self.watchdog_timer.start(60000) # פעם בדקה
        
        send_telegram("🚀 <b>System v34.0 Online</b>")

    def setup_ui(self):
        self.setStyleSheet("QMainWindow{background:#0f111a;} QWidget{color:#e0e6ed;font-family:'Segoe UI';} QLineEdit, QSpinBox{background:#1a1c2a;border:1px solid #333;padding:8px;border-radius:5px;} QPushButton{border-radius:5px;padding:10px;font-weight:bold;} QTableWidget{background:#141621;gridline-color:#2a2d3e; border:none;} QHeaderView::section{background:#1a1c2a; padding:10px; border:none;}")
        
        main = QWidget(); self.setCentralWidget(main); layout = QVBoxLayout(main)
        
        # Top Bar
        top = QHBoxLayout()
        title = QLabel("X-HOTEL SENTINEL"); title.setStyleSheet("font-size:22px; font-weight:bold; color:#00d4ff;")
        self.search = QLineEdit(); self.search.setPlaceholderText("🔍 Search Channels..."); self.search.textChanged.connect(self.filter_table)
        btn_folder = QPushButton("📂 OPEN RECORDINGS"); btn_folder.setStyleSheet("background:#4caf50; color:white;"); btn_folder.clicked.connect(self.open_folder)
        top.addWidget(title); top.addStretch(); top.addWidget(self.search); top.addWidget(btn_folder)
        layout.addLayout(top)

        # Tabs
        tabs = QTabWidget(); layout.addWidget(tabs)
        
        # Tab 1: Control
        t1 = QWidget(); t1l = QVBoxLayout(t1)
        cfg_box = QFrame(); cfg_box.setStyleSheet("background:#1a1c2a; border-radius:10px;"); gl = QGridLayout(cfg_box)
        self.ip=QLineEdit("144.91.86.250"); self.api_port=QLineEdit("80"); self.stream_port=QLineEdit("8080")
        self.usr=QLineEdit("admin"); self.pw=QLineEdit("MazalTovLanu"); self.m3u=QLineEdit()
        gl.addWidget(QLabel("SERVER IP"),0,0); gl.addWidget(self.ip,0,1); gl.addWidget(QLabel("API PORT"),0,2); gl.addWidget(self.api_port,0,3); gl.addWidget(QLabel("STREAM PORT"),0,4); gl.addWidget(self.stream_port,0,5)
        gl.addWidget(QLabel("M3U URL"),1,0); gl.addWidget(self.m3u,1,1,1,3); btn_load = QPushButton("LOAD M3U"); btn_load.setStyleSheet("background:#00d4ff; color:black;"); btn_load.clicked.connect(self.load_m3u); gl.addWidget(btn_load,1,4,1,2)
        t1l.addWidget(cfg_box)
        
        self.tbl = QTableWidget(0, 6); self.tbl.setHorizontalHeaderLabels(["SEL", "CHANNEL", "REC", "STATUS", "UPTIME", "ACTION"])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); t1l.addWidget(self.tbl)
        
        btns = QHBoxLayout(); b_start = QPushButton("▶ START SELECTED"); b_start.setStyleSheet("background:#00e676; color:black; padding:15px;"); b_start.clicked.connect(self.start_selected)
        b_stop = QPushButton("🛑 STOP ALL"); b_stop.setStyleSheet("background:#ff2e63; color:white; padding:15px;"); b_stop.clicked.connect(self.stop_all); btns.addWidget(b_start); btns.addWidget(b_stop)
        t1l.addLayout(btns); tabs.addTab(t1, "📡 BROADCAST")

        # Tab 2: Health
        t2 = QWidget(); t2l = QVBoxLayout(t2); metrics = QHBoxLayout()
        self.g_cpu=ProGauge("CPU","%"); self.g_ram=ProGauge("RAM","%",color="#aa00ff"); self.g_net=ProGauge("TRAFFIC","MB",100,"#00e676")
        metrics.addWidget(self.g_cpu); metrics.addWidget(self.g_ram); metrics.addWidget(self.g_net); t2l.addLayout(metrics)
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setStyleSheet("background:#0a0b10; color:#00e676; font-family:Consolas;"); t2l.addWidget(self.log)
        tabs.addTab(t2, "📊 SYSTEM HEALTH")

    # --- Logic ---
    def open_folder(self): 
        if not os.path.exists(RECORDINGS_PATH): os.makedirs(RECORDINGS_PATH)
        subprocess.Popen(['xdg-open', RECORDINGS_PATH])

    def filter_table(self):
        query = self.search.text().lower()
        for i in range(self.tbl.rowCount()):
            name = self.tbl.item(i, 1).text().lower()
            self.tbl.setRowHidden(i, query not in name)

    def disk_watchdog(self):
        usage = psutil.disk_usage('/')
        if usage.percent > 90:
            self.add_log("🚨 DISK CRITICAL! Cleaning old recordings...")
            # לוגיקה למחיקת קבצים ישנים מעל 24 שעות
            os.system(f"find {RECORDINGS_PATH} -type f -name '*.ts' -mmin +1440 -delete")

    def update_system_metrics(self):
        self.g_cpu.set_value(psutil.cpu_percent())
        self.g_ram.set_value(psutil.virtual_memory().percent)
        n = psutil.net_io_counters()
        diff = (n.bytes_recv + n.bytes_sent - self.net_io.bytes_recv - self.net_io.bytes_sent) / 1048576
        self.g_net.set_value(diff); self.net_io = n

    def add_log(self, m): self.log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {m}")

    def load_m3u(self):
        url = self.m3u.text()
        if not url: return
        try:
            res = requests.get(url, headers=STREAM_HEADERS, timeout=15, verify=False)
            self.tbl.setRowCount(0); self.raw_data = []
            name = "Unknown"
            for line in res.text.splitlines():
                if "#EXTINF" in line: name = line.split(",")[-1]
                elif "http" in line:
                    r = self.tbl.rowCount(); self.tbl.insertRow(r)
                    self.raw_data.append({"name":name, "url":line})
                    cb = QCheckBox(); center = QWidget(); l = QHBoxLayout(center); l.addWidget(cb); l.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.tbl.setCellWidget(r, 0, center)
                    self.tbl.setItem(r, 1, QTableWidgetItem(name))
                    rec = QCheckBox(); rec.setChecked(True); r_center = QWidget(); rl = QHBoxLayout(r_center); rl.addWidget(rec); rl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.tbl.setCellWidget(r, 2, r_center)
                    self.tbl.setItem(r, 3, QTableWidgetItem("IDLE"))
                    self.tbl.setItem(r, 4, QTableWidgetItem("--"))
                    btn = QPushButton("STOP"); btn.setStyleSheet("color:#ff2e63;"); btn.clicked.connect(lambda _, n=name: self.stop_one(n))
                    self.tbl.setCellWidget(r, 5, btn)
            self.add_log(f"Loaded {len(self.raw_data)} channels.")
        except Exception as e: self.add_log(f"Load Error: {e}")

    def start_selected(self):
        conf = {"ip":self.ip.text(), "stream_port":self.stream_port.text(), "user":self.usr.text(), "pass":self.pw.text()}
        for r in range(self.tbl.rowCount()):
            if self.tbl.cellWidget(r, 0).layout().itemAt(0).widget().isChecked():
                name = self.tbl.item(r, 1).text()
                is_rec = self.tbl.cellWidget(r, 2).layout().itemAt(0).widget().isChecked()
                if name not in self.workers:
                    w = RecordingWorker(name, self.raw_data[r]['url'], conf, is_rec)
                    w.stats_signal.connect(self.update_row)
                    w.log_signal.connect(self.add_log)
                    self.workers[name] = w; w.start()
        self.save()

    def update_row(self, name, data):
        for r in range(self.tbl.rowCount()):
            if self.tbl.item(r, 1).text() == name:
                self.tbl.item(r, 3).setText(data['status'])
                self.tbl.item(r, 3).setForeground(QColor("#00e676"))
                self.tbl.item(r, 4).setText(data['uptime'])

    def stop_one(self, name):
        if name in self.workers: self.workers[name].stop(); del self.workers[name]
        self.add_log(f"Stopped: {name}")

    def stop_all(self):
        for w in list(self.workers.values()): w.stop()
        self.workers.clear(); self.add_log("All streams stopped.")

    def save(self):
        data = {"ip":self.ip.text(), "stream_port":self.stream_port.text(), "user":self.usr.text(), "pass":self.pw.text(), "m3u":self.m3u.text()}
        json.dump(data, open(CONFIG_FILE, "w"))

    def restore(self):
        if os.path.exists(CONFIG_FILE):
            try:
                d = json.load(open(CONFIG_FILE))
                self.ip.setText(d.get('ip','')); self.stream_port.setText(d.get('stream_port','8080'))
                self.usr.setText(d.get('user','')); self.pw.setText(d.get('pass','')); self.m3u.setText(d.get('m3u',''))
            except: pass

if __name__ == "__main__":
    app = QApplication(sys.argv); window = XHotelUI(); window.show(); sys.exit(app.exec())
