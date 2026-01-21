import sys, subprocess, time, os, re, requests, psutil, json
import urllib3
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QLabel, QCheckBox, 
                             QFrame, QMessageBox, QGridLayout, QTabWidget, QTextEdit, QSpinBox)
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QTimer, QRectF, QThread
from PyQt6.QtGui import QPainter, QColor, QPen, QFont

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
os.environ["QT_QUICK_BACKEND"] = "software"

# --- כותרות ---
API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'X-Requested-With': 'XMLHttpRequest',
    'Connection': 'keep-alive'
}

STREAM_HEADERS = {
    'User-Agent': 'VLC/3.0.18 LibVLC/3.0.18',
    'Accept': '*/*',
    'Connection': 'keep-alive'
}

# --- הגדרות ---
# אנא עדכן את ה-CHAT ID הנכון כאן אחרי בדיקה עם @userinfobot
TELEGRAM_TOKEN = "8307008722:AAHY-QYNYyTnOwjS0q4VGfA0_iUiQBxYHBc"
TELEGRAM_CHAT_ID = "-5125327073" 
CONFIG_FILE = "/root/iptv_config.json"
RECORDINGS_PATH = "/root/Recordings"
DEFAULT_IP = "144.91.86.250"

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, verify=False, timeout=10)
        return (True, "OK") if resp.status_code == 200 else (False, f"Error {resp.status_code}: {resp.text}")
    except Exception as e: return False, str(e)

# --- רכיבים גרפיים ---
class ToolButton(QPushButton):
    def __init__(self, text, icon, color="#2d303e"):
        super().__init__()
        self.setText(f"{icon}  {text}")
        self.setStyleSheet(f"QPushButton {{ background-color: {color}; color: white; border-radius: 10px; padding: 15px; font-weight: bold; border: 1px solid #3b3f51; }} QPushButton:hover {{ background-color: #40e0d0; color: #000; }}")

class ProGauge(QWidget):
    def __init__(self, title, unit, max_val=100, color="#00d4ff"):
        super().__init__()
        self.value=0; self.max_val=max_val; self.title=title; self.unit=unit; self.primary_color=QColor(color); self.setMinimumSize(180,180)
    def set_value(self, val): self.value=val; self.update()
    def paintEvent(self, e):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); w,h=self.width(),self.height(); size=min(w,h)-20; rect=QRectF((w-size)/2,(h-size)/2,size,size)
        p.setPen(QPen(QColor("#2d303e"),12,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap)); p.drawArc(rect,135*16,270*16)
        r=self.value/self.max_val; a=int(270*r); a=270 if a>270 else a; col=self.primary_color if r<0.85 else QColor("#ff2e63")
        p.setPen(QPen(col,12,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap)); p.drawArc(rect,225*16,-a*16)
        p.setPen(QColor("white")); p.setFont(QFont("Segoe UI",22,QFont.Weight.Bold)); p.drawText(rect,Qt.AlignmentFlag.AlignCenter,f"{self.value:.1f}{self.unit}")
        p.setPen(QColor("#a6accd")); p.setFont(QFont("Segoe UI",10)); p.drawText(int(w/2)-50,int(h)-30,100,20,Qt.AlignmentFlag.AlignCenter,self.title)

# --- מנוע הקלטה משופר ---
class RecordingWorker(QThread):
    stats_signal = pyqtSignal(str, dict)
    log_signal = pyqtSignal(str) 
    
    def __init__(self, name, url, config, record_local):
        super().__init__()
        self.channel_name = name; self.url = url; self.iptv_config = config; self.record_local = record_local
        self.is_running = True

    def run(self):
        safe_name = re.sub(r'[\\/*?:"<>|]', "", self.channel_name).strip().replace(" ", "_")
        channel_path = os.path.join(RECORDINGS_PATH, safe_name)
        if self.record_local: os.makedirs(channel_path, exist_ok=True)

        send_telegram(f"🎬 <b>STARTED:</b> {self.channel_name}")
        start_time = time.time()
        
        while self.is_running:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = os.path.join(channel_path, f"{safe_name}_{timestamp}.ts")
            abs_output = os.path.abspath(output_file)
            
            xui_target = ""
            c = self.iptv_config
            
            # --- רישום ב-API ויצירת לינק שידור ---
            if c.get('ip') and c.get('api_port') and c.get('api_path'):
                try:
                    # שימוש בפורט ה-API לרישום
                    api_base = f"http://{c['ip']}:{c['api_port']}"
                    api_url = f"{api_base}{c['api_path']}"
                    
                    if c['user'] and c['pass']:
                        # שימוש בפורט השידור (Stream Port) לדחיפת הוידאו
                        stream_port = c.get('stream_port', c['api_port']) # ברירת מחדל לפורט API אם אין
                        stream_base = f"http://{c['ip']}:{stream_port}"
                        xui_target = f"{stream_base}/live/{c['user']}/{c['pass']}/{safe_name}.ts"
                        
                        try:
                            # רישום מול ה-API
                            requests.post(f"{api_url}?action=add_stream", data={
                                "username":c['user'], "password":c['pass'],
                                "stream_display_name":self.channel_name, "stream_source":["127.0.0.1"],
                                "category_id":c.get('cat_id', '1'), "stream_mode":"live"
                            }, headers=API_HEADERS, verify=False, timeout=5)
                        except: pass
                except: pass

            # --- בניית פקודת FFmpeg ---
            ua = STREAM_HEADERS['User-Agent']
            cmd = ['ffmpeg', '-y', '-reconnect', '1', '-reconnect_at_eof', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5', 
                   '-headers', f'User-Agent: {ua}\r\n', '-i', self.url, '-c', 'copy']

            if xui_target:
                tee_cmd = []
                if self.record_local: tee_cmd.append(f"[f=mpegts]'{abs_output}'")
                tee_cmd.append(f"[f=mpegts:onfail=ignore]{xui_target}")
                cmd.extend(['-f', 'tee', "|".join(tee_cmd)])
            elif self.record_local:
                cmd.extend(['-f', 'mpegts', abs_output])
            else:
                cmd.extend(['-f', 'null', '-'])

            self.log_signal.emit(f"CMD: {' '.join(cmd)}")

            # --- הרצת התהליך ותפיסת שגיאות ---
            try:
                # הפעם אנחנו קוראים את ה-STDERR
                self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                
                # לולאת ניטור
                while self.process.poll() is None and self.is_running:
                    uptime = time.strftime("%H:%M:%S", time.gmtime(time.time() - start_time))
                    
                    # בדיקת דיסק
                    disk = "0.0 MB"
                    if self.record_local and os.path.exists(abs_output):
                         try: disk = f"{os.path.getsize(abs_output)/1048576:.1f} MB"
                         except: pass
                    
                    link_txt = "Local Only"
                    if xui_target: link_txt = f"Panel ({c.get('stream_port')})"
                    
                    self.stats_signal.emit(self.channel_name, {"status":"ACTIVE", "uptime":uptime, "disk":disk, "link":link_txt})
                    time.sleep(2)
                
                # אם התהליך מת, בוא נראה למה
                if self.is_running:
                    _, stderr_output = self.process.communicate()
                    # שליפת 2 השורות האחרונות מהשגיאה
                    last_errors = "\n".join(stderr_output.strip().split('\n')[-2:]) if stderr_output else "No Error Output"
                    
                    self.log_signal.emit(f"❌ {self.channel_name} DIED!")
                    self.log_signal.emit(f"FFMPEG ERROR: {last_errors}")
                    time.sleep(3)

            except Exception as e:
                self.log_signal.emit(f"Exec Error: {e}")
                time.sleep(5)

    def stop(self):
        self.is_running = False
        if self.process:
            try: self.process.terminate(); self.process.wait(timeout=1)
            except: self.process.kill()
        self.wait()

# --- ממשק ---
class XHotelUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("X-HOTEL v33.0 (The Detective)"); self.resize(1600, 1000)
        self.workers={}; self.net_io=psutil.net_io_counters()
        self.setup_ui(); QTimer.singleShot(500, self.restore); self.t=QTimer(); self.t.timeout.connect(self.upd_stats); self.t.start(1000)
        
        ok, msg = send_telegram("✅ <b>SYSTEM ONLINE</b>")
        if not ok: self.add_log(f"TG Init Failed: {msg}")

    def setup_ui(self):
        self.setStyleSheet("QMainWindow{background:#10121b;} QWidget{color:#e0e6ed;font-family:'Segoe UI';} QTabWidget::pane{border:0;background:#10121b;} QTabBar::tab{background:#1f2233;padding:12px 30px;margin:2px;border-radius:6px;font-weight:bold;} QTabBar::tab:selected{background:#00d4ff;color:black;} QLineEdit{background:#1f2233;border:1px solid #3b3f51;padding:10px;color:white;border-radius:6px;} QSpinBox{background:#1f2233;border:1px solid #3b3f51;padding:10px;color:white;border-radius:6px;font-weight:bold;} QTableWidget{background:#151722;border:none;gridline-color:#2d303e;} QHeaderView::section{background:#1f2233;padding:8px;border:none;} QTextEdit{background:#0a0b10;color:#00e676;border-radius:8px;font-family:'Consolas';}")
        main=QWidget(); self.setCentralWidget(main); l=QVBoxLayout(main)
        h=QFrame(); hl=QHBoxLayout(h); lbl=QLabel("COMMAND CENTER"); lbl.setStyleSheet("font-size:24px;font-weight:900;color:#00d4ff;"); hl.addWidget(lbl); l.addWidget(h)
        tabs=QTabWidget(); l.addWidget(tabs)

        t1=QWidget(); t1l=QVBoxLayout(t1); c_f=QFrame(); gl=QGridLayout(c_f); c_f.setStyleSheet("background:#1f2233;border-radius:12px;padding:10px;")
        
        # --- הפרדת פורטים ---
        self.ip=QLineEdit(DEFAULT_IP); 
        self.api_port=QLineEdit("80"); self.api_port.setPlaceholderText("API Port (80)")
        self.stream_port=QLineEdit("8080"); self.stream_port.setPlaceholderText("Stream Port (8080)")
        self.api_path=QLineEdit("/mbmWePBa/api"); self.api_path.setPlaceholderText("API Path")
        
        self.usr=QLineEdit("admin"); self.pw=QLineEdit("MazalTovLanu")
        self.cat_id=QSpinBox(); self.cat_id.setValue(1); self.cat_id.setPrefix("Cat ID: ")
        
        self.m3u=QLineEdit(); self.m3u.setPlaceholderText("Paste M3U URL...")
        
        gl.addWidget(QLabel("SERVER IP"),0,0); gl.addWidget(self.ip,0,1)
        gl.addWidget(QLabel("API PORT"),0,2); gl.addWidget(self.api_port,0,3)
        gl.addWidget(QLabel("STREAM PORT"),0,4); gl.addWidget(self.stream_port,0,5)
        
        gl.addWidget(QLabel("USER"),1,0); gl.addWidget(self.usr,1,1)
        gl.addWidget(QLabel("PASS"),1,2); gl.addWidget(self.pw,1,3)
        gl.addWidget(QLabel("API PATH"),1,4); gl.addWidget(self.api_path,1,5)
        
        gl.addWidget(QLabel("CAT ID"),2,4); gl.addWidget(self.cat_id,2,5)

        btn_check = QPushButton("CHECK API"); btn_check.setStyleSheet("background:#e91e63;color:white;font-weight:bold;padding:10px;"); btn_check.clicked.connect(self.check_connection)
        gl.addWidget(btn_check, 2, 2, 1, 2)
        
        gl.addWidget(self.m3u,2,0,1,2); b=QPushButton("LOAD M3U"); b.setStyleSheet("background:#00d4ff;color:black;font-weight:bold;padding:10px;border-radius:6px;"); b.clicked.connect(self.load_m3u); gl.addWidget(b,2,1)
        t1l.addWidget(c_f)
        
        self.tbl=QTableWidget(0,7); self.tbl.setHorizontalHeaderLabels(["SEL","CHANNEL","REC","STATUS","UPTIME","DISK","ACTION"]); self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); t1l.addWidget(self.tbl)
        acts=QHBoxLayout(); b1=QPushButton("START STREAMING"); b1.setStyleSheet("background:#00e676;color:black;font-weight:bold;padding:15px;"); b1.clicked.connect(self.start_sel); b2=QPushButton("STOP ALL"); b2.setStyleSheet("background:#ff2e63;color:white;font-weight:bold;padding:15px;"); b2.clicked.connect(self.stop_all); acts.addWidget(b1); acts.addWidget(b2); t1l.addLayout(acts); tabs.addTab(t1,"📡 OPERATIONS")

        t2=QWidget(); t2l=QVBoxLayout(t2); gs=QHBoxLayout()
        self.g_cpu=ProGauge("CPU","%"); self.g_ram=ProGauge("RAM","%",color="#aa00ff"); self.g_dl=ProGauge("DL","MB",50,"#00e676"); self.g_ul=ProGauge("UL","MB",50,"#ffea00"); gs.addWidget(self.g_cpu); gs.addWidget(self.g_ram); gs.addWidget(self.g_dl); gs.addWidget(self.g_ul); t2l.addLayout(gs)
        self.log=QTextEdit(); self.log.setReadOnly(True); t2l.addWidget(QLabel("LOGS:")); t2l.addWidget(self.log); tabs.addTab(t2,"📊 METRICS")

        t3=QWidget(); t3l=QGridLayout(t3); t3l.setSpacing(20)
        btn_test=ToolButton("TEST TELEGRAM","📢","#9c27b0"); btn_test.clicked.connect(self.tool_test_tg)
        btn_clean=ToolButton("CLEAN DISK","🧹","#ff9800"); btn_clean.clicked.connect(self.tool_clean_disk)
        btn_reboot=ToolButton("REBOOT","🔄","#d32f2f"); btn_reboot.clicked.connect(self.tool_reboot)
        btn_restart=ToolButton("RESTART APP","🛑","#00bcd4"); btn_restart.clicked.connect(self.tool_restart_app)
        t3l.addWidget(btn_test,0,0); t3l.addWidget(btn_clean,0,1); t3l.addWidget(btn_reboot,1,0); t3l.addWidget(btn_restart,1,1); t3l.addWidget(QLabel("Tools Area"),2,0,1,2,Qt.AlignmentFlag.AlignCenter); tabs.addTab(t3,"🛠️ TOOLS")

    def check_connection(self):
        url = f"http://{self.ip.text()}:{self.api_port.text()}{self.api_path.text()}"
        auth = f"username={self.usr.text()}&password={self.pw.text()}"
        self.add_log(f"Testing API: {url}...")
        try:
            res = requests.get(f"{url}?action=stats&{auth}", headers=API_HEADERS, timeout=8, verify=False)
            if res.status_code == 200:
                QMessageBox.information(self, "Success", "API Connected!")
                self.add_log("API Connection Success.")
            else:
                QMessageBox.critical(self, "Error", f"Failed. Status: {res.status_code}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Connection Failed:\n{e}")

    def tool_test_tg(self): 
        ok, msg = send_telegram("🔔 <b>TEST</b> OK")
        if ok: QMessageBox.information(self,"Success","Message Sent!")
        else: QMessageBox.critical(self, "Telegram Error", f"Failed:\n{msg}")

    def tool_clean_disk(self): os.system("/root/clean_recordings.sh") if QMessageBox.question(self,'C',"Sure?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes else None
    def tool_reboot(self): os.system("reboot") if QMessageBox.question(self,'R',"Sure?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes else None
    def tool_restart_app(self): QApplication.quit(); os.execl(sys.executable, sys.executable, *sys.argv)
    def add_log(self, m): self.log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {m}")
    def upd_stats(self): self.g_cpu.set_value(psutil.cpu_percent()); self.g_ram.set_value(psutil.virtual_memory().percent); n=psutil.net_io_counters(); self.g_dl.set_value((n.bytes_recv-self.net_io.bytes_recv)/1048576); self.g_ul.set_value((n.bytes_sent-self.net_io.bytes_sent)/1048576); self.net_io=n
    
    def load_m3u(self):
        url = self.m3u.text().strip(); 
        if not url: return
        self.add_log(f"Fetching M3U...")
        data = ""
        try:
            r = requests.get(url, headers=STREAM_HEADERS, timeout=30, verify=False)
            if r.status_code == 200: data = r.text
        except: pass
        if not data or len(data) < 50:
            try: data = subprocess.check_output(['curl', '-k', '-L', url], text=True)
            except: pass

        self.tbl.setRowCount(0); self.db = []; name = "Unknown"; count = 0
        for line in data.splitlines():
            line = line.strip()
            if not line: continue
            if "#EXTINF" in line:
                try: name = line.split(',', 1)[1].strip()
                except: name = "Chan " + str(count)
            elif "://" in line and not line.startswith('#'):
                r = self.tbl.rowCount(); self.tbl.insertRow(r); self.db.append({"name":name,"url":line})
                chk=QCheckBox(); cw=QWidget(); cl=QHBoxLayout(cw); cl.addWidget(chk); cl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.tbl.setCellWidget(r,0,cw)
                self.tbl.setItem(r,1,QTableWidgetItem(name))
                rec=QCheckBox(); rec.setChecked(True); rw=QWidget(); rl=QHBoxLayout(rw); rl.addWidget(rec); rl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.tbl.setCellWidget(r,2,rw)
                self.tbl.setItem(r,3,QTableWidgetItem("IDLE")); self.tbl.setItem(r,4,QTableWidgetItem("--")); self.tbl.setItem(r,5,QTableWidgetItem("0 MB")); 
                b=QPushButton("STOP"); b.setStyleSheet("background:#2d303e;color:#ff2e63;"); b.clicked.connect(lambda _,x=name:self.stop_one(x)); self.tbl.setCellWidget(r,6,b)
                count += 1; name = "Unknown"
        self.add_log(f"Loaded {count} channels.")

    def start_sel(self):
        cf={
            "ip":self.ip.text(), "api_port":self.api_port.text(), "stream_port":self.stream_port.text(),
            "api_path":self.api_path.text(), "user":self.usr.text(), "pass":self.pw.text(), 
            "cat_id":str(self.cat_id.value())
        }
        for r in range(self.tbl.rowCount()):
            if self.tbl.cellWidget(r,0).layout().itemAt(0).widget().isChecked():
                n=self.tbl.item(r,1).text(); rec=self.tbl.cellWidget(r,2).layout().itemAt(0).widget().isChecked()
                if n not in self.workers: 
                    w=RecordingWorker(n,self.db[r]['url'],cf,rec)
                    w.stats_signal.connect(self.upd_row); w.log_signal.connect(self.add_log); w.finished_signal.connect(self.handle_failure)
                    self.workers[n]=w; w.start()
        self.save()

    def upd_row(self,n,s):
        for r in range(self.tbl.rowCount()):
            if self.tbl.item(r,1).text()==n: self.tbl.item(r,3).setText(s['status']); self.tbl.item(r,3).setForeground(QColor("#00e676")); self.tbl.item(r,4).setText(s['uptime']); self.tbl.item(r,5).setText(s['disk'])
    
    def handle_failure(self, n):
        self.stop_one(n) 
        for r in range(self.tbl.rowCount()):
            if self.tbl.item(r,1).text()==n: self.tbl.item(r,3).setText("FAILED"); self.tbl.item(r,3).setForeground(QColor("red"))

    def stop_one(self,n): 
        if n in self.workers: self.workers[n].stop(); del self.workers[n]
        for r in range(self.tbl.rowCount()):
            if self.tbl.item(r,1).text()==n: self.tbl.item(r,3).setText("STOPPED"); self.tbl.item(r,3).setForeground(QColor("red"))

    def stop_all(self): 
        for w in list(self.workers.values()): w.stop()
        self.workers.clear(); self.add_log("Stopped All")
        for r in range(self.tbl.rowCount()): self.tbl.item(r,3).setText("STOPPED"); self.tbl.item(r,3).setForeground(QColor("red"))

    def save(self): 
        act=[{"name":n,"rec":w.record_local} for n,w in self.workers.items()]
        data = {
            "ip":self.ip.text(), "api_port":self.api_port.text(), "stream_port":self.stream_port.text(),
            "api_path":self.api_path.text(), "user":self.usr.text(), "pass":self.pw.text(), "m3u":self.m3u.text(), "act":act
        }
        json.dump(data, open(CONFIG_FILE,"w"))
        
    def restore(self):
        if os.path.exists(CONFIG_FILE):
            try:
                s=json.load(open(CONFIG_FILE))
                self.ip.setText(s.get("ip", DEFAULT_IP))
                self.api_port.setText(s.get("api_port", "80"))
                self.stream_port.setText(s.get("stream_port", "8080"))
                self.api_path.setText(s.get("api_path", "/mbmWePBa/api"))
                self.usr.setText(s.get("user",""))
                self.pw.setText(s.get("pass",""))
                self.m3u.setText(s.get("m3u",""))
                if s.get("m3u"): 
                    self.load_m3u(); act={x['name']:x['rec'] for x in s.get('act',[])}
                    for r in range(self.tbl.rowCount()):
                        n = self.tbl.item(r,1).text()
                        if n in act:
                            self.tbl.cellWidget(r,0).layout().itemAt(0).widget().setChecked(True)
                            self.tbl.cellWidget(r,2).layout().itemAt(0).widget().setChecked(act[n])
                    self.start_sel()
            except: pass

if __name__ == "__main__": app=QApplication(sys.argv); w=XHotelUI(); w.show(); sys.exit(app.exec())
