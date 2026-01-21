import sys, subprocess, threading, time, os, requests, psutil, json
import urllib3
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QLabel, QCheckBox, 
                             QFrame, QMessageBox, QGridLayout, QTabWidget, QTextEdit)
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QTimer, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont

# --- הגדרות ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
os.environ["QT_QUICK_BACKEND"] = "software"
TELEGRAM_TOKEN = "8307008722:AAHY-QYNYyTnOwjS0q4VGfA0_iUiQBxYHBc"
TELEGRAM_CHAT_ID = "-5125327073"
CONFIG_FILE = "/root/iptv_config.json"
RECORDINGS_PATH = "/root/Recordings"

# --- טלגרם ---
def send_telegram(msg):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, verify=False, timeout=5)
    except: pass

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

# --- לוגיקת שידור ---
class StreamWorker(QObject):
    stats_signal = pyqtSignal(str, dict)
    def __init__(self, name, url, config, record): super().__init__(); self.name=name; self.url=url; self.config=config; self.rec=record; self.running=True; self.proc=None
    def _get_xui(self):
        try:
            c=self.config; base=c['server'].split('/dashboard')[0].rstrip('/'); api=f"{base}/api.php"; auth=f"username={c['user']}&password={c['pass']}"
            try: res=requests.get(f"{api}?action=get_categories&{auth}", timeout=5, verify=False).json(); cat=next((x['category_id'] for x in res if x['category_name']=="Channels"), None)
            except: cat=None
            if not cat: 
                try: cat=requests.post(f"{api}?action=add_category", data={**c,"category_name":"Channels","category_type":"live"}, verify=False).json().get('category_id',"1")
                except: cat="1"
            requests.post(f"{api}?action=add_stream", data={"username":c['user'],"password":c['pass'],"stream_display_name":self.name,"stream_source":["127.0.0.1"],"category_id":cat,"stream_mode":"live"}, verify=False)
            return f"{base}/live/{c['user']}/{c['pass']}/{self.name.replace(' ','_')}.ts"
        except: return None
    def run(self):
        start=time.time(); folder=os.path.join(RECORDINGS_PATH, self.name.replace(" ","_")); 
        if self.rec: os.makedirs(folder, exist_ok=True)
        xui=self._get_xui(); send_telegram(f"🎬 <b>STARTED:</b> {self.name}")
        while self.running:
            cmd=['ffmpeg','-y','-rtsp_transport','tcp','-stimeout','5000000','-i',self.url,'-c','copy','-f','mpegts']
            tgts=[]
            if self.rec: tgts.append(f"[f=mpegts]{os.path.join(folder, datetime.now().strftime('%H%M%S') + '.ts')}")
            if xui: tgts.append(f"[f=mpegts:onfail=ignore]{xui}")
            if tgts: cmd.extend(['-f','tee',"|".join(tgts)])
            else: cmd.extend(['-f','null','-'])
            try:
                self.proc=subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                while self.proc.poll() is None and self.running:
                    uptime=time.strftime("%H:%M:%S", time.gmtime(time.time()-start)); d_size=0
                    if self.rec and os.path.exists(folder):
                        try: d_size=sum(os.path.getsize(os.path.join(folder,f)) for f in os.listdir(folder))/1048576
                        except: pass
                    self.stats_signal.emit(self.name, {"status":"ACTIVE","uptime":uptime,"disk":f"{d_size:.1f} MB","link":xui or "N/A"})
                    time.sleep(3)
                if self.running: time.sleep(5)
            except: time.sleep(10)
    def stop(self): self.running=False; self.proc.terminate() if self.proc else None

# --- ממשק ---
class XHotelUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("X-HOTEL MANAGER v16.0 (Universal Parser)"); self.resize(1600, 1000)
        self.workers={}; self.net_io=psutil.net_io_counters()
        self.setup_ui(); QTimer.singleShot(500, self.restore); self.t=QTimer(); self.t.timeout.connect(self.upd_stats); self.t.start(1000)
        send_telegram("✅ <b>SYSTEM ONLINE</b>")

    def setup_ui(self):
        self.setStyleSheet("QMainWindow{background:#10121b;} QWidget{color:#e0e6ed;font-family:'Segoe UI';} QTabWidget::pane{border:0;background:#10121b;} QTabBar::tab{background:#1f2233;padding:12px 30px;margin:2px;border-radius:6px;font-weight:bold;} QTabBar::tab:selected{background:#00d4ff;color:black;} QLineEdit{background:#1f2233;border:1px solid #3b3f51;padding:10px;color:white;border-radius:6px;} QTableWidget{background:#151722;border:none;gridline-color:#2d303e;} QHeaderView::section{background:#1f2233;padding:8px;border:none;} QTextEdit{background:#0a0b10;color:#00e676;border-radius:8px;font-family:'Consolas';}")
        main=QWidget(); self.setCentralWidget(main); l=QVBoxLayout(main)
        h=QFrame(); hl=QHBoxLayout(h); lbl=QLabel("COMMAND CENTER"); lbl.setStyleSheet("font-size:24px;font-weight:900;color:#00d4ff;"); hl.addWidget(lbl); l.addWidget(h)
        tabs=QTabWidget(); l.addWidget(tabs)

        # Tab 1
        t1=QWidget(); t1l=QVBoxLayout(t1); c_f=QFrame(); gl=QGridLayout(c_f); c_f.setStyleSheet("background:#1f2233;border-radius:12px;padding:10px;")
        self.url=QLineEdit("http://144.91.86.250/mbmWePBa"); self.usr=QLineEdit("admin"); self.pw=QLineEdit("MazalTovLanu"); self.m3u=QLineEdit(); self.m3u.setPlaceholderText("Paste M3U URL...")
        gl.addWidget(QLabel("PORTAL"),0,0); gl.addWidget(self.url,0,1); gl.addWidget(QLabel("USER"),0,2); gl.addWidget(self.usr,0,3); gl.addWidget(QLabel("PASS"),0,4); gl.addWidget(self.pw,0,5)
        gl.addWidget(QLabel("M3U"),1,0); gl.addWidget(self.m3u,1,1,1,4); b=QPushButton("LOAD"); b.setStyleSheet("background:#00d4ff;color:black;font-weight:bold;padding:10px;border-radius:6px;"); b.clicked.connect(self.load_m3u); gl.addWidget(b,1,5)
        t1l.addWidget(c_f)
        self.tbl=QTableWidget(0,7); self.tbl.setHorizontalHeaderLabels(["SEL","CHANNEL","REC","STATUS","UPTIME","DISK","ACTION"]); self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); t1l.addWidget(self.tbl)
        acts=QHBoxLayout(); b1=QPushButton("START STREAMING"); b1.setStyleSheet("background:#00e676;color:black;font-weight:bold;padding:15px;"); b1.clicked.connect(self.start_sel); b2=QPushButton("STOP ALL"); b2.setStyleSheet("background:#ff2e63;color:white;font-weight:bold;padding:15px;"); b2.clicked.connect(self.stop_all); acts.addWidget(b1); acts.addWidget(b2); t1l.addLayout(acts)
        tabs.addTab(t1,"📡 OPERATIONS")

        # Tab 2
        t2=QWidget(); t2l=QVBoxLayout(t2); gs=QHBoxLayout()
        self.g_cpu=ProGauge("CPU","%"); self.g_ram=ProGauge("RAM","%",color="#aa00ff"); self.g_dl=ProGauge("DL","MB",50,"#00e676"); self.g_ul=ProGauge("UL","MB",50,"#ffea00"); gs.addWidget(self.g_cpu); gs.addWidget(self.g_ram); gs.addWidget(self.g_dl); gs.addWidget(self.g_ul); t2l.addLayout(gs)
        self.log=QTextEdit(); self.log.setReadOnly(True); t2l.addWidget(QLabel("LOGS:")); t2l.addWidget(self.log); tabs.addTab(t2,"📊 METRICS")

        # Tab 3
        t3=QWidget(); t3l=QGridLayout(t3); t3l.setSpacing(20)
        btn_test=ToolButton("TEST TELEGRAM","📢","#9c27b0"); btn_test.clicked.connect(self.tool_test_tg)
        btn_clean=ToolButton("CLEAN DISK","🧹","#ff9800"); btn_clean.clicked.connect(self.tool_clean_disk)
        btn_reboot=ToolButton("REBOOT","🔄","#d32f2f"); btn_reboot.clicked.connect(self.tool_reboot)
        btn_restart=ToolButton("RESTART APP","🛑","#00bcd4"); btn_restart.clicked.connect(self.tool_restart_app)
        t3l.addWidget(btn_test,0,0); t3l.addWidget(btn_clean,0,1); t3l.addWidget(btn_reboot,1,0); t3l.addWidget(btn_restart,1,1); t3l.addWidget(QLabel("Tools"),2,0,1,2,Qt.AlignmentFlag.AlignCenter); tabs.addTab(t3,"🛠️ TOOLS")

    # --- פונקציות ---
    def tool_test_tg(self): send_telegram("🔔 <b>TEST</b> OK"); QMessageBox.information(self,"TG","Sent.")
    def tool_clean_disk(self): os.system("/root/clean_recordings.sh") if QMessageBox.question(self,'C',"Sure?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes else None
    def tool_reboot(self): os.system("reboot") if QMessageBox.question(self,'R',"Sure?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes else None
    def tool_restart_app(self): QApplication.quit(); os.execl(sys.executable, sys.executable, *sys.argv)
    def add_log(self, m): self.log.append(f"[{datetime.now().strftime('%H:%M')}] {m}")
    def upd_stats(self): self.g_cpu.set_value(psutil.cpu_percent()); self.g_ram.set_value(psutil.virtual_memory().percent); n=psutil.net_io_counters(); self.g_dl.set_value((n.bytes_recv-self.net_io.bytes_recv)/1048576); self.g_ul.set_value((n.bytes_sent-self.net_io.bytes_sent)/1048576); self.net_io=n
    
    # --- הפונקציה המתוקנת - פשוטה כמו בעבר ---
    def load_m3u(self):
        url = self.m3u.text().strip()
        if not url: return
        self.add_log(f"Fetching: {url}")
        
        try:
            # הורדה בסיסית - בלי headers מורכבים שעושים צרות
            response = requests.get(url, verify=False, timeout=20)
            data = response.text
            
            self.tbl.setRowCount(0); self.db = []
            
            # רשימה זמנית לניתוח
            lines = data.split('\n')
            name = "Unknown"
            count = 0
            
            for line in lines:
                line = line.strip()
                if not line: continue
                
                # זיהוי שם
                if line.startswith("#EXTINF"):
                    if "," in line:
                        name = line.split(",")[-1].strip()
                    else:
                        name = "Channel"
                
                # זיהוי לינק - כל מה שלא מתחיל ב-# ונראה כמו לינק
                elif not line.startswith("#") and len(line) > 5:
                    # הוספה לטבלה
                    r = self.tbl.rowCount(); self.tbl.insertRow(r)
                    self.db.append({"name": name, "url": line})
                    
                    # בניית השורה
                    chk=QCheckBox(); cw=QWidget(); cl=QHBoxLayout(cw); cl.addWidget(chk); cl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.tbl.setCellWidget(r,0,cw)
                    self.tbl.setItem(r,1,QTableWidgetItem(name))
                    rec=QCheckBox(); rec.setChecked(True); rw=QWidget(); rl=QHBoxLayout(rw); rl.addWidget(rec); rl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.tbl.setCellWidget(r,2,rw)
                    self.tbl.setItem(r,3,QTableWidgetItem("IDLE")); self.tbl.setItem(r,4,QTableWidgetItem("--")); self.tbl.setItem(r,5,QTableWidgetItem("0 MB"))
                    b=QPushButton("STOP"); b.setStyleSheet("background:#2d303e;color:#ff2e63;"); b.clicked.connect(lambda _,x=name:self.stop_one(x)); self.tbl.setCellWidget(r,6,b)
                    
                    name = "Unknown" # איפוס
                    count += 1
            
            self.add_log(f"Loaded {count} channels.")
            if count == 0:
                QMessageBox.warning(self, "Warning", "0 Channels loaded!\nCheck log for details.")
                self.add_log("DEBUG: File content start: " + data[:100]) # יראה לנו מה הבעיה אם עדיין יש
            
        except Exception as e:
            self.add_log(f"Error: {str(e)}")
            QMessageBox.critical(self, "Error", str(e))

    def start_sel(self):
        cf={"server":self.url.text(),"user":self.usr.text(),"pass":self.pw.text()}
        for r in range(self.tbl.rowCount()):
            if self.tbl.cellWidget(r,0).layout().itemAt(0).widget().isChecked():
                n=self.tbl.item(r,1).text(); rec=self.tbl.cellWidget(r,2).layout().itemAt(0).widget().isChecked()
                if n not in self.workers: w=StreamWorker(n,self.db[r]['url'],cf,rec); w.stats_signal.connect(self.upd_row); self.workers[n]=w; threading.Thread(target=w.run, daemon=True).start()
        self.save()
    def upd_row(self,n,s):
        for r in range(self.tbl.rowCount()):
            if self.tbl.item(r,1).text()==n: self.tbl.item(r,3).setText(s['status']); self.tbl.item(r,3).setForeground(QColor("#00e676")); self.tbl.item(r,4).setText(s['uptime']); self.tbl.item(r,5).setText(s['disk'])
    def stop_one(self,n): 
        if n in self.workers: self.workers[n].stop(); del self.workers[n]
        for r in range(self.tbl.rowCount()):
            if self.tbl.item(r,1).text()==n: self.tbl.item(r,3).setText("STOPPED"); self.tbl.item(r,3).setForeground(QColor("red"))
    def stop_all(self): [w.stop() for w in self.workers.values()]; self.workers.clear(); self.add_log("Stopped All")
    def save(self): act=[{"name":n,"rec":w.rec} for n,w in self.workers.items()]; json.dump({"url":self.url.text(),"user":self.usr.text(),"pass":self.pw.text(),"m3u":self.m3u.text(),"act":act}, open(CONFIG_FILE,"w"))
    def restore(self):
        if os.path.exists(CONFIG_FILE):
            try:
                s=json.load(open(CONFIG_FILE)); self.url.setText(s.get("url","")); self.usr.setText(s.get("user","")); self.pw.setText(s.get("pass","")); self.m3u.setText(s.get("m3u",""))
                if s.get("m3u"): self.load_m3u(); act={x['name']:x['rec'] for x in s.get('act',[])}; [self.tbl.cellWidget(r,0).layout().itemAt(0).widget().setChecked(True) for r in range(self.tbl.rowCount()) if self.tbl.item(r,1).text() in act]; [self.tbl.cellWidget(r,2).layout().itemAt(0).widget().setChecked(act[self.tbl.item(r,1).text()]) for r in range(self.tbl.rowCount()) if self.tbl.item(r,1).text() in act]; self.start_sel()
            except: pass

if __name__ == "__main__": app=QApplication(sys.argv); w=XHotelUI(); w.show(); sys.exit(app.exec())
