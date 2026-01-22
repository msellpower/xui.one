import sys, subprocess, time, os, re, requests, psutil, json, urllib3
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QLabel, QCheckBox, 
                             QFrame, QMessageBox, QGridLayout, QTabWidget, QTextEdit, QSpinBox)
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QTimer, QRectF, QThread
from PyQt6.QtGui import QPainter, QColor, QPen, QFont

# ביטול אזהרות SSL לחיבורים פנימיים
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
os.environ["QT_QUICK_BACKEND"] = "software"


def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, verify=False, timeout=5)
    except: pass

# --- גרפיקה של המטריקות ---
class ProGauge(QWidget):
    def __init__(self, title, unit, max_val=100):
        super().__init__()
        self.value = 0; self.max_val = max_val; self.title = title; self.unit = unit
        self.setMinimumSize(170, 170)
    
    def set_value(self, val):
        self.value = val; self.update()
    
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height(); size = min(w, h) - 40
        rect = QRectF((w-size)/2, (h-size)/2, size, size)
        
        # רקע הסקאלה
        p.setPen(QPen(QColor("#2d303e"), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 135*16, 270*16)
        
        # צבע לפי ערך
        val_perc = min(self.value / self.max_val, 1.0)
        color = QColor("#00e676") if val_perc < 0.6 else QColor("#ffea00") if val_perc < 0.85 else QColor("#ff2e63")
        
        p.setPen(QPen(color, 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 225*16, -int(270 * val_perc) * 16)
        
        # טקסט מרכזי
        p.setPen(QColor("white")); p.setFont(QFont("Segoe UI", 22, 700))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{int(self.value)}{self.unit}")
        
        # כותרת למטה
        p.setPen(QColor("#a6accd")); p.setFont(QFont("Segoe UI", 10))
        p.drawText(0, int(h)-25, int(w), 25, Qt.AlignmentFlag.AlignCenter, self.title)

# --- מנוע השידור וההקלטה ---
class RecordingWorker(QThread):
    stats_signal = pyqtSignal(str, dict); log_signal = pyqtSignal(str)
    
    def __init__(self, name, url, config, record_local):
        super().__init__()
        self.channel_name = name; self.url = url; self.c = config
        self.record_local = record_local; self.is_running = True

    def run(self):
        # 1. רישום ב-API (עבור Xtream UI)
        api_url = f"http://{self.c['ip']}:{self.c['api_p']}{self.c['path']}?action=add_stream"
        payload = {
            "username": self.c['u'], "password": self.c['p'],
            "stream_display_name": self.channel_name,
            "stream_source": ["127.0.0.1"], "category_id": self.c['cat'], "stream_mode": "live"
        }
        try:
            requests.post(api_url, data=payload, timeout=5, verify=False)
        except Exception as e: self.log_signal.emit(f"API Register Fail: {e}")

        # 2. הכנת FFmpeg
        safe_name = re.sub(r'[^a-zA-Z0-9]', "_", self.channel_name)
        stream_target = f"http://{self.c['ip']}:{self.c['str_p']}/live/{self.c['u']}/{self.c['p']}/{safe_name}.ts"
        
        cmd = ['ffmpeg', '-y', '-reconnect', '1', '-reconnect_streamed', '1', '-i', self.url, '-c', 'copy']
        
        outputs = [f"[f=mpegts:onfail=ignore]{stream_target}"]
        if self.record_local:
            l_dir = os.path.join(RECORDINGS_PATH, safe_name)
            os.makedirs(l_dir, exist_ok=True)
            l_file = os.path.join(l_dir, f"{datetime.now().strftime('%H%M%S')}.ts")
            outputs.append(f"[f=mpegts]'{l_file}'")
        
        cmd.extend(['-f', 'tee', "|".join(outputs)])
        
        send_telegram(f"🎬 <b>Broadcasting:</b> {self.channel_name}")
        
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            start_t = time.time()
            while self.proc.poll() is None and self.is_running:
                upt = time.strftime("%H:%M:%S", time.gmtime(time.time() - start_t))
                self.stats_signal.emit(self.channel_name, {"status": "ACTIVE", "uptime": upt})
                self.msleep(2000)
            
            if self.is_running: send_telegram(f"🆘 <b>Down:</b> {self.channel_name}")
        except Exception as e: self.log_signal.emit(f"Process Error: {e}")

    def stop(self):
        self.is_running = False
        if hasattr(self, 'proc'): self.proc.terminate()
        send_telegram(f"🛑 <b>Stopped:</b> {self.channel_name}")

# --- ממשק הניהול הראשי ---
class XHotelUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("X-HOTEL v37.0 - FINAL BRIDGE"); self.resize(1400, 900)
        self.workers = {}; self.net_io = psutil.net_io_counters()
        self.setup_ui(); self.restore()
        
        self.timer = QTimer(); self.timer.timeout.connect(self.upd_metrics); self.timer.start(1000)

    def setup_ui(self):
        self.setStyleSheet("QMainWindow{background:#0b0d14;} QWidget{color:#e0e6ed;font-family:'Segoe UI';} QLineEdit, QSpinBox{background:#161925;border:1px solid #2d3142;padding:8px;border-radius:5px;} QPushButton{border-radius:5px;padding:10px;font-weight:bold;} QTableWidget{background:#10121b; border:none; gridline-color:#1c1f2b;}")
        main = QWidget(); self.setCentralWidget(main); layout = QVBoxLayout(main)
        
        # Header
        head = QHBoxLayout(); title = QLabel("X-HOTEL CONTROL"); title.setStyleSheet("font-size:24px; color:#00d4ff; font-weight:900;")
        self.search = QLineEdit(); self.search.setPlaceholderText("🔍 Quick Filter..."); self.search.textChanged.connect(self.filter_tbl)
        head.addWidget(title); head.addStretch(); head.addWidget(self.search); layout.addLayout(head)

        tabs = QTabWidget(); layout.addWidget(tabs)
        
        # Tab 1: Control
        t1 = QWidget(); t1l = QVBoxLayout(t1); cfg = QFrame(); cfg.setStyleSheet("background:#161925; border-radius:10px;"); gl = QGridLayout(cfg)
        self.ip=QLineEdit(); self.api_p=QLineEdit("80"); self.str_p=QLineEdit("8080"); self.path=QLineEdit("/mbmWePBa/api")
        self.u=QLineEdit(); self.p=QLineEdit(); self.cat=QSpinBox(); self.cat.setValue(1); self.m3u=QLineEdit()
        
        gl.addWidget(QLabel("PANEL IP"),0,0); gl.addWidget(self.ip,0,1); gl.addWidget(QLabel("API PORT"),0,2); gl.addWidget(self.api_p,0,3); gl.addWidget(QLabel("STREAM PORT"),0,4); gl.addWidget(self.str_p,0,5)
        gl.addWidget(QLabel("USER"),1,0); gl.addWidget(self.u,1,1); gl.addWidget(QLabel("PASS"),1,2); gl.addWidget(self.p,1,3); gl.addWidget(QLabel("API PATH"),1,4); gl.addWidget(self.path,1,5)
        
        btn_chk = QPushButton("⚡ CHECK API"); btn_chk.setStyleSheet("background:#e91e63; color:white;"); btn_chk.clicked.connect(self.check_api)
        gl.addWidget(btn_chk, 2, 2, 1, 2)
        
        gl.addWidget(QLabel("M3U URL"),2,0); gl.addWidget(self.m3u,2,1); btn_load = QPushButton("LOAD"); btn_load.clicked.connect(self.load_m3u); gl.addWidget(btn_load,2,1,1,1,Qt.AlignmentFlag.AlignRight)
        t1l.addWidget(cfg)
        
        self.tbl = QTableWidget(0, 6); self.tbl.setHorizontalHeaderLabels(["SEL", "CHANNEL", "REC", "STATUS", "UPTIME", "ACTION"])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); t1l.addWidget(self.tbl)
        
        ctrl = QHBoxLayout(); b_start = QPushButton("🚀 START BROADCAST"); b_start.setStyleSheet("background:#00e676; color:black;"); b_start.clicked.connect(self.start_sel)
        b_stop = QPushButton("🛑 STOP ALL"); b_stop.setStyleSheet("background:#ff2e63; color:white;"); b_stop.clicked.connect(self.stop_all)
        ctrl.addWidget(b_start); ctrl.addWidget(b_stop); t1l.addLayout(ctrl); tabs.addTab(t1, "📡 STREAMS")

        # Tab 2: Health
        t2 = QWidget(); t2l = QVBoxLayout(t2); met = QHBoxLayout()
        self.g_cpu=ProGauge("CPU LOAD","%"); self.g_ram=ProGauge("MEMORY","%"); self.g_net=ProGauge("TRAFFIC","MB")
        met.addWidget(self.g_cpu); met.addWidget(self.g_ram); met.addWidget(self.g_net); t2l.addLayout(met)
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setStyleSheet("background:#050505; color:#00ff00; font-family:Consolas;"); t2l.addWidget(self.log)
        tabs.addTab(t2, "📊 METRICS")

    def check_api(self):
        url = f"http://{self.ip.text()}:{self.api_p.text()}{self.path.text()}?action=stats&username={self.u.text()}&password={self.p.text()}"
        try:
            r = requests.get(url, timeout=5, verify=False)
            if r.status_code == 200: QMessageBox.information(self, "Success", "Panel Connected Successfully!")
            else: QMessageBox.warning(self, "Failed", f"Server returned error {r.status_code}")
        except Exception as e: QMessageBox.critical(self, "Connection Error", str(e))

    def upd_metrics(self):
        self.g_cpu.set_value(psutil.cpu_percent()); self.g_ram.set_value(psutil.virtual_memory().percent)
        n = psutil.net_io_counters(); self.g_net.set_value((n.bytes_recv + n.bytes_sent - self.net_io.bytes_recv - self.net_io.bytes_sent) / 1048576); self.net_io = n

    def load_m3u(self):
        try:
            r = requests.get(self.m3u.text(), timeout=10, verify=False); self.tbl.setRowCount(0); self.db = []; name = "Unknown"
            for line in r.text.splitlines():
                if "#EXTINF" in line: name = line.split(",")[-1]
                elif "http" in line:
                    idx = self.tbl.rowCount(); self.tbl.insertRow(idx); self.db.append({"name":name,"url":line})
                    cb = QCheckBox(); cw = QWidget(); cl = QHBoxLayout(cw); cl.addWidget(cb); cl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.tbl.setCellWidget(idx, 0, cw)
                    self.tbl.setItem(idx, 1, QTableWidgetItem(name))
                    rec = QCheckBox(); rec.setChecked(True); rw = QWidget(); rl = QHBoxLayout(rw); rl.addWidget(rec); rl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.tbl.setCellWidget(idx, 2, rw)
                    self.tbl.setItem(idx, 3, QTableWidgetItem("IDLE")); self.tbl.setItem(idx, 4, QTableWidgetItem("--"))
                    btn = QPushButton("STOP"); btn.clicked.connect(lambda _, n=name: self.stop_one(n)); self.tbl.setCellWidget(idx, 5, btn)
            self.log.append(f"Loaded {len(self.db)} channels.")
        except Exception as e: QMessageBox.warning(self, "M3U Error", str(e))

    def filter_tbl(self):
        q = self.search.text().lower()
        for i in range(self.tbl.rowCount()): self.tbl.setRowHidden(i, q not in self.tbl.item(i,1).text().lower())

    def start_sel(self):
        conf = {"ip":self.ip.text(),"api_p":self.api_p.text(),"str_p":self.str_p.text(),"path":self.path.text(),"u":self.u.text(),"p":self.p.text(),"cat":self.cat.value()}
        for i in range(self.tbl.rowCount()):
            if self.tbl.cellWidget(i,0).layout().itemAt(0).widget().isChecked():
                name = self.tbl.item(i,1).text()
                if name not in self.workers:
                    w = RecordingWorker(name, self.db[i]['url'], conf, self.tbl.cellWidget(i,2).layout().itemAt(0).widget().isChecked())
                    w.stats_signal.connect(self.upd_row); w.log_signal.connect(lambda m: self.log.append(m))
                    self.workers[name] = w; w.start()
        self.save()

    def upd_row(self, name, data):
        for i in range(self.tbl.rowCount()):
            if self.tbl.item(i,1).text() == name:
                self.tbl.item(i,3).setText(data['status']); self.tbl.item(i,3).setForeground(QColor("#00e676"))
                self.tbl.item(i,4).setText(data['uptime'])

    def stop_one(self, name):
        if name in self.workers: self.workers[name].stop(); del self.workers[name]

    def stop_all(self):
        for w in list(self.workers.values()): w.stop()
        self.workers.clear()

    def save(self):
        d = {"ip":self.ip.text(),"api_p":self.api_p.text(),"str_p":self.str_p.text(),"path":self.path.text(),"u":self.u.text(),"p":self.p.text(),"m3u":self.m3u.text(),"cat":self.cat.value()}
        json.dump(d, open(CONFIG_FILE,"w"))

    def restore(self):
        if os.path.exists(CONFIG_FILE):
            try:
                s = json.load(open(CONFIG_FILE)); self.ip.setText(s['ip']); self.api_p.setText(s['api_p']); self.str_p.setText(s['str_p']); self.path.setText(s['path']); self.u.setText(s['u']); self.p.setText(s['p']); self.m3u.setText(s['m3u']); self.cat.setValue(s['cat'])
            except: pass

if __name__ == "__main__":
    app = QApplication(sys.argv); w = XHotelUI(); w.show(); sys.exit(app.exec())
