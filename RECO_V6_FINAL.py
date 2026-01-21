import sys, subprocess, time, os, re, requests, psutil, json, urllib3
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QLabel, QCheckBox, 
                             QFrame, QMessageBox, QGridLayout, QTabWidget, QTextEdit, QSpinBox)
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QTimer, QRectF, QThread
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QLinearGradient

# --- הגדרות אבטחה וסביבה ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
os.environ["QT_QUICK_BACKEND"] = "software"

CONFIG_FILE = "/root/iptv_config.json"
RECORDINGS_PATH = "/root/Recordings"
TELEGRAM_TOKEN = "8307008722:AAHY-QYNYyTnOwjS0q4VGfA0_iUiQBxYHBc"
TELEGRAM_CHAT_ID = "-1003477621724"

API_HEADERS = {'User-Agent': 'Mozilla/5.0'}
STREAM_HEADERS = {'User-Agent': 'VLC/3.0.18'}

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, verify=False, timeout=5)
    except: pass

# --- גרפיקה משופרת למטריקות ---
class ProGauge(QWidget):
    def __init__(self, title, unit, max_val=100):
        super().__init__()
        self.value=0; self.max_val=max_val; self.title=title; self.unit=unit; self.setMinimumSize(180,180)
    
    def set_value(self, val): self.value=val; self.update()
    
    def paintEvent(self, e):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); w,h=self.width(),self.height(); size=min(w,h)-30; rect=QRectF((w-size)/2,(h-size)/2,size,size)
        
        # רקע הסקאלה
        p.setPen(QPen(QColor("#2d303e"),14,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap)); p.drawArc(rect,135*16,270*16)
        
        # צבע משתנה לפי ערך
        val_perc = self.value / self.max_val
        if val_perc < 0.6: color = QColor("#00e676") # ירוק
        elif val_perc < 0.85: color = QColor("#ffea00") # צהוב
        else: color = QColor("#ff2e63") # אדום
        
        p.setPen(QPen(color,14,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
        p.drawArc(rect,225*16,-int(270*val_perc)*16)
        
        # טקסט
        p.setPen(QColor("white")); p.setFont(QFont("Segoe UI",22,QFont.Weight.Bold)); p.drawText(rect,Qt.AlignmentFlag.AlignCenter,f"{int(self.value)}{self.unit}")
        p.setPen(QColor("#a6accd")); p.setFont(QFont("Segoe UI",10)); p.drawText(0,int(h)-25,int(w),25,Qt.AlignmentFlag.AlignCenter,self.title)

# --- מנוע הקלטה ושידור (כולל API) ---
class RecordingWorker(QThread):
    stats_signal = pyqtSignal(str, dict)
    log_signal = pyqtSignal(str)
    
    def __init__(self, name, url, config, record_local):
        super().__init__()
        self.channel_name = name; self.url = url; self.config = config; self.record_local = record_local
        self.is_running = True

    def run(self):
        safe_name = re.sub(r'[\\/*?:"<>|]', "", self.channel_name).strip().replace(" ", "_")
        c = self.config
        
        # 1. רישום ב-API של הפאנל (החזרתי את הלוגיקה)
        api_base = f"http://{c['ip']}:{c['api_port']}{c['api_path']}"
        try:
            requests.post(f"{api_base}?action=add_stream", data={
                "username":c['user'], "password":c['pass'],
                "stream_display_name":self.channel_name, "stream_source":["127.0.0.1"],
                "category_id":c['cat_id'], "stream_mode":"live"
            }, headers=API_HEADERS, verify=False, timeout=5)
        except Exception as e: self.log_signal.emit(f"API Register Error: {e}")

        # 2. שליחת הודעה לטלגרם על התחלה
        send_telegram(f"🎬 <b>STARTED:</b> {self.channel_name}\n📡 Source: {self.url[:30]}...")
        
        # 3. הכנת פקודת FFmpeg
        stream_target = f"http://{c['ip']}:{c['stream_port']}/live/{c['user']}/{c['pass']}/{safe_name}.ts"
        cmd = ['ffmpeg', '-y', '-reconnect', '1', '-reconnect_streamed', '1', '-headers', f"User-Agent: {STREAM_HEADERS['User-Agent']}\r\n", '-i', self.url, '-c', 'copy']
        
        outputs = [f"[f=mpegts:onfail=ignore]{stream_target}"]
        if self.record_local:
            os.makedirs(os.path.join(RECORDINGS_PATH, safe_name), exist_ok=True)
            out_file = os.path.join(RECORDINGS_PATH, safe_name, f"{datetime.now().strftime('%H%M%S')}.ts")
            outputs.append(f"[f=mpegts]'{out_file}'")
        
        cmd.extend(['-f', 'tee', "|".join(outputs)])
        
        try:
            self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            start_time = time.time()
            
            while self.process.poll() is None and self.is_running:
                upt = time.strftime("%H:%M:%S", time.gmtime(time.time() - start_time))
                self.stats_signal.emit(self.channel_name, {"status":"ACTIVE", "uptime":upt})
                self.msleep(2000)
            
            # אם הלולאה נגמרה והערוץ נפל
            if self.is_running:
                send_telegram(f"🆘 <b>STREAM DROPPED:</b> {self.channel_name}")
                self.log_signal.emit(f"❌ {self.channel_name} died unexpectedly.")
        except Exception as e:
            self.log_signal.emit(f"Exec Error: {e}")

    def stop(self):
        self.is_running = False
        if hasattr(self, 'process'): self.process.terminate()
        send_telegram(f"🛑 <b>STOPPED:</b> {self.channel_name}")
        self.wait()

# --- ממשק משתמש ---
class XHotelUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("X-HOTEL v35.0 (FULL RECOVERY)"); self.resize(1500, 950)
        self.workers = {}; self.net_io = psutil.net_io_counters()
        self.setup_ui()
        self.restore()
        
        self.t = QTimer(); self.t.timeout.connect(self.upd_metrics); self.t.start(1000)
        send_telegram("✅ <b>SYSTEM ONLINE</b> - All functions restored.")

    def setup_ui(self):
        self.setStyleSheet("QMainWindow{background:#10121b;} QWidget{color:#e0e6ed;font-family:'Segoe UI';} QLineEdit, QSpinBox{background:#1f2233;border:1px solid #3b3f51;padding:8px;border-radius:6px;} QPushButton{border-radius:6px;padding:10px;font-weight:bold;} QTableWidget{background:#151722; border:none;} QHeaderView::section{background:#1f2233; padding:10px;}")
        
        main = QWidget(); self.setCentralWidget(main); layout = QVBoxLayout(main)
        
        # Header
        head = QHBoxLayout(); lbl = QLabel("X-HOTEL CONTROL PANEL"); lbl.setStyleSheet("font-size:26px; font-weight:900; color:#00d4ff;")
        self.search = QLineEdit(); self.search.setPlaceholderText("🔍 Quick Search..."); self.search.textChanged.connect(self.filter_tbl)
        head.addWidget(lbl); head.addStretch(); head.addWidget(self.search); layout.addLayout(head)

        tabs = QTabWidget(); layout.addWidget(tabs)
        
        # Tab 1: Configuration
        t1 = QWidget(); t1l = QVBoxLayout(t1); cfg = QFrame(); cfg.setStyleSheet("background:#1f2233; border-radius:12px;"); gl = QGridLayout(cfg)
        self.ip=QLineEdit(); self.api_p=QLineEdit("80"); self.str_p=QLineEdit("8080"); self.api_path=QLineEdit("/mbmWePBa/api")
        self.usr=QLineEdit(); self.pw=QLineEdit(); self.cat_id=QSpinBox(); self.cat_id.setValue(1); self.m3u=QLineEdit()
        
        gl.addWidget(QLabel("SERVER IP"),0,0); gl.addWidget(self.ip,0,1); gl.addWidget(QLabel("API PORT"),0,2); gl.addWidget(self.api_p,0,3); gl.addWidget(QLabel("STREAM PORT"),0,4); gl.addWidget(self.str_p,0,5)
        gl.addWidget(QLabel("USER"),1,0); gl.addWidget(self.usr,1,1); gl.addWidget(QLabel("PASS"),1,2); gl.addWidget(self.pw,1,3); gl.addWidget(QLabel("API PATH"),1,4); gl.addWidget(self.api_path,1,5)
        gl.addWidget(QLabel("CAT ID"),2,4); gl.addWidget(self.cat_id,2,5)
        
        btn_chk = QPushButton("⚡ CHECK CONNECTION"); btn_chk.setStyleSheet("background:#e91e63; color:white;"); btn_chk.clicked.connect(self.check_api)
        gl.addWidget(btn_chk, 2, 2, 1, 2)
        
        gl.addWidget(QLabel("M3U URL"),2,0); gl.addWidget(self.m3u,2,1); btn_load = QPushButton("LOAD M3U"); btn_load.setStyleSheet("background:#00d4ff; color:black;"); btn_load.clicked.connect(self.load_m3u); gl.addWidget(btn_load,2,1,1,1,Qt.AlignmentFlag.AlignRight)
        
        t1l.addWidget(cfg)
        self.tbl = QTableWidget(0, 6); self.tbl.setHorizontalHeaderLabels(["SEL", "CHANNEL", "REC", "STATUS", "UPTIME", "ACTION"])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); t1l.addWidget(self.tbl)
        
        ctrl = QHBoxLayout(); b_start = QPushButton("🚀 START STREAMING"); b_start.setStyleSheet("background:#00e676; color:black; padding:15px;"); b_start.clicked.connect(self.start_sel)
        b_stop = QPushButton("🛑 STOP ALL"); b_stop.setStyleSheet("background:#ff2e63; color:white; padding:15px;"); b_stop.clicked.connect(self.stop_all)
        ctrl.addWidget(b_start); ctrl.addWidget(b_stop); t1l.addLayout(ctrl); tabs.addTab(t1, "📡 BROADCAST")

        # Tab 2: Metrics
        t2 = QWidget(); t2l = QVBoxLayout(t2); met = QHBoxLayout()
        self.g_cpu=ProGauge("CPU USAGE","%"); self.g_ram=ProGauge("RAM MEMORY","%"); self.g_net=ProGauge("NETWORK","MB")
        met.addWidget(self.g_cpu); met.addWidget(self.g_ram); met.addWidget(self.g_net); t2l.addLayout(met)
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setStyleSheet("background:#0a0b10; color:#00e676; font-family:Consolas;"); t2l.addWidget(self.log)
        tabs.addTab(t2, "📊 HEALTH & LOGS")

        # Tab 3: Tools
        t3 = QWidget(); t3l = QGridLayout(t3)
        btn_f = QPushButton("📂 OPEN RECORDINGS"); btn_f.clicked.connect(lambda: subprocess.Popen(['xdg-open', RECORDINGS_PATH]))
        btn_tg = QPushButton("📢 TEST TELEGRAM"); btn_tg.clicked.connect(lambda: send_telegram("Test message from X-Hotel!"))
        t3l.addWidget(btn_f,0,0); t3l.addWidget(btn_tg,0,1); tabs.addTab(t3, "🛠️ TOOLS")

    def check_api(self):
        url = f"http://{self.ip.text()}:{self.api_p.text()}{self.api_path.text()}?action=stats&username={self.usr.text()}&password={self.pw.text()}"
        try:
            r = requests.get(url, timeout=5, verify=False)
            if r.status_code == 200: QMessageBox.information(self, "Success", "Panel Connected!")
            else: QMessageBox.warning(self, "Error", f"Code: {r.status_code}")
        except Exception as e: QMessageBox.critical(self, "Failed", str(e))

    def upd_metrics(self):
        self.g_cpu.set_value(psutil.cpu_percent())
        self.g_ram.set_value(psutil.virtual_memory().percent)
        n = psutil.net_io_counters()
        self.g_net.set_value((n.bytes_recv + n.bytes_sent - self.net_io.bytes_recv - self.net_io.bytes_sent) / 1048576)
        self.net_io = n

    def load_m3u(self):
        try:
            r = requests.get(self.m3u.text(), headers=STREAM_HEADERS, timeout=10, verify=False)
            self.tbl.setRowCount(0); self.db = []; name = "Unknown"
            for line in r.text.splitlines():
                if "#EXTINF" in line: name = line.split(",")[-1]
                elif "http" in line:
                    idx = self.tbl.rowCount(); self.tbl.insertRow(idx); self.db.append({"name":name,"url":line})
                    cb = QCheckBox(); cw = QWidget(); cl = QHBoxLayout(cw); cl.addWidget(cb); cl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.tbl.setCellWidget(idx, 0, cw)
                    self.tbl.setItem(idx, 1, QTableWidgetItem(name))
                    rec = QCheckBox(); rec.setChecked(True); rw = QWidget(); rl = QHBoxLayout(rw); rl.addWidget(rec); rl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.tbl.setCellWidget(idx, 2, rw)
                    self.tbl.setItem(idx, 3, QTableWidgetItem("IDLE"))
                    self.tbl.setItem(idx, 4, QTableWidgetItem("--"))
                    btn = QPushButton("STOP"); btn.clicked.connect(lambda _, n=name: self.stop_one(n)); self.tbl.setCellWidget(idx, 5, btn)
            self.add_log(f"Loaded {len(self.db)} channels.")
        except: pass

    def filter_tbl(self):
        q = self.search.text().lower()
        for i in range(self.tbl.rowCount()): self.tbl.setRowHidden(i, q not in self.tbl.item(i,1).text().lower())

    def start_sel(self):
        c = {"ip":self.ip.text(), "api_port":self.api_p.text(), "stream_port":self.str_p.text(), "api_path":self.api_path.text(), "user":self.usr.text(), "pass":self.pw.text(), "cat_id":str(self.cat_id.value())}
        for i in range(self.tbl.rowCount()):
            if self.tbl.cellWidget(i,0).layout().itemAt(0).widget().isChecked():
                name = self.tbl.item(i,1).text()
                if name not in self.workers:
                    w = RecordingWorker(name, self.db[i]['url'], c, self.tbl.cellWidget(i,2).layout().itemAt(0).widget().isChecked())
                    w.stats_signal.connect(self.upd_row); w.log_signal.connect(self.add_log)
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

    def add_log(self, m): self.log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {m}")
    def save(self):
        d = {"ip":self.ip.text(),"api_p":self.api_p.text(),"str_p":self.str_p.text(),"path":self.api_path.text(),"u":self.usr.text(),"p":self.pw.text(),"m3u":self.m3u.text(),"cat":self.cat_id.value()}
        json.dump(d, open(CONFIG_FILE,"w"))
    def restore(self):
        if os.path.exists(CONFIG_FILE):
            try:
                s = json.load(open(CONFIG_FILE)); self.ip.setText(s['ip']); self.api_p.setText(s['api_p']); self.str_p.setText(s['str_p']); self.api_path.setText(s['path']); self.usr.setText(s['u']); self.pw.setText(s['p']); self.m3u.setText(s['m3u']); self.cat_id.setValue(s['cat'])
            except: pass

if __name__ == "__main__":
    app = QApplication(sys.argv); w = XHotelUI(); w.show(); sys.exit(app.exec())
