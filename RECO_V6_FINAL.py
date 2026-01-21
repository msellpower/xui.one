import sys, subprocess, threading, time, os, re, requests, psutil, json
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QLabel, QCheckBox, 
                             QFrame, QProgressBar, QMessageBox, QGridLayout, QTabWidget)
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QTimer

# --- הגדרות טלגרם ---
TELEGRAM_TOKEN = "8307008722:AAHY-QYNYyTnOwjS0q4VGfA0_iUiQBxYHBc"
TELEGRAM_CHAT_ID = "-5125327073"

def send_telegram_msg(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, data=payload, timeout=5)
    except: pass

os.environ["QT_QUICK_BACKEND"] = "software"
CONFIG_FILE = "/root/iptv_config.json"

class RecordingWorker(QObject):
    status_changed = pyqtSignal(str, dict)

    def __init__(self, channel_name, url, output_folder, iptv_config=None):
        super().__init__()
        self.channel_name = channel_name
        self.url = url
        self.output_folder = os.path.abspath(output_folder)
        self.iptv_config = iptv_config
        self.is_running = True
        self.process = None

    def get_or_create_category(self, base_url, user, password):
        try:
            auth = f"username={user}&password={password}"
            res = requests.get(f"{base_url}/api.php?action=get_categories&{auth}", timeout=5).json()
            for c in res:
                if c.get('category_name') == "Channels": return c.get('category_id')
            new_cat = requests.post(f"{base_url}/api.php?action=add_category", 
                                    data={"username":user,"password":password,"category_name":"Channels","category_type":"live"}).json()
            return new_cat.get('category_id')
        except: return "1"

    def start_recording(self):
        start_time = time.time()
        safe_name = re.sub(r'[\\/*?:"<>|]', "", self.channel_name).strip().replace(" ", "_")
        channel_path = os.path.join(self.output_folder, safe_name)
        os.makedirs(channel_path, exist_ok=True)
        
        broadcast_link = "---"
        if self.iptv_config and all(self.iptv_config.values()):
            c = self.iptv_config
            base = c['server'].split('/dashboard')[0].rstrip('/')
            cat_id = self.get_or_create_category(base, c['user'], c['pass'])
            requests.post(f"{base}/api.php?action=add_stream", 
                          data={"username":c['user'],"password":c['pass'],"stream_display_name":self.channel_name,
                                "stream_source":["127.0.0.1"],"category_id":cat_id,"stream_mode":"live"})
            broadcast_link = f"{base}/live/{c['user']}/{c['pass']}/{safe_name}.ts"

        send_telegram_msg(f"✅ <b>מערכת הופעלה</b>\nערוץ: {self.channel_name}")

        while self.is_running:
            timestamp = datetime.now().strftime("%H%M%S")
            output_file = os.path.join(channel_path, f"rec_{timestamp}.ts")
            cmd = ['ffmpeg', '-y', '-re', '-i', self.url, '-c', 'copy']
            if broadcast_link != "---":
                cmd.extend(['-f', 'tee', f"[f=mpegts]'{output_file}'|[f=mpegts:onfail=ignore]{broadcast_link}"])
            else:
                cmd.extend([output_file])

            try:
                self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                while self.process.poll() is None and self.is_running:
                    uptime = time.strftime("%H:%M:%S", time.gmtime(time.time() - start_time))
                    size = 0
                    for f in os.listdir(channel_path): size += os.path.getsize(os.path.join(channel_path, f))
                    self.status_changed.emit(self.channel_name, {"status": "Active", "uptime": uptime, "size": f"{size/(1024*1024):.1f}MB", "link": broadcast_link})
                    time.sleep(5)
                if self.is_running: time.sleep(10)
            except: time.sleep(10)

    def stop(self):
        self.is_running = False
        if self.process: self.process.kill()

class IPTVHotelSuite(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hotel IPTV Pro Dashboard v9.0")
        self.resize(1400, 850)
        self.active_workers = {}
        self.output_folder = "/root/Recordings"
        self.init_ui()
        QTimer.singleShot(1000, self.auto_load_last_state)

    def init_ui(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        
        # --- לשונית ניהול (Control) ---
        self.control_tab = QWidget()
        layout = QVBoxLayout(self.control_tab)
        
        conf_f = QFrame(); l = QGridLayout(conf_f)
        self.server_i = QLineEdit("http://144.91.86.250/mbmWePBa")
        self.user_i = QLineEdit("admin")
        self.pass_i = QLineEdit("MazalTovLanu")
        self.m3u_i = QLineEdit()
        l.addWidget(QLabel("Portal URL:"), 0, 0); l.addWidget(self.server_i, 0, 1)
        l.addWidget(QLabel("User:"), 0, 2); l.addWidget(self.user_i, 0, 3)
        l.addWidget(QLabel("Pass:"), 0, 4); l.addWidget(self.pass_i, 0, 5)
        l.addWidget(QLabel("M3U List:"), 1, 0); l.addWidget(self.m3u_i, 1, 1, 1, 4)
        btn_l = QPushButton("LOAD"); btn_l.clicked.connect(self.load_playlist); l.addWidget(btn_l, 1, 5)
        layout.addWidget(conf_f)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Select", "Name", "Status", "Broadcast Link (XUI)", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        
        btns = QHBoxLayout()
        btn_start = QPushButton("START SELECTED"); btn_start.clicked.connect(self.start_selected)
        btn_stop_all = QPushButton("STOP ALL"); btn_stop_all.clicked.connect(self.stop_all)
        btns.addWidget(btn_start); btns.addWidget(btn_stop_all); layout.addLayout(btns)
        
        self.tabs.addTab(self.control_tab, "Live Control")

        # --- לשונית אנליטיקה (Analytics) ---
        self.analytics_tab = QWidget()
        a_layout = QVBoxLayout(self.analytics_tab)
        self.a_table = QTableWidget(0, 4)
        self.a_table.setHorizontalHeaderLabels(["Name", "Uptime", "Disk Usage", "System Impact"])
        self.a_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        a_layout.addWidget(self.a_table)
        
        self.cpu_bar = QProgressBar(); self.ram_bar = QProgressBar()
        a_layout.addWidget(QLabel("Overall CPU Usage:")); a_layout.addWidget(self.cpu_bar)
        a_layout.addWidget(QLabel("Overall RAM Usage:")); a_layout.addWidget(self.ram_bar)
        self.tabs.addTab(self.analytics_tab, "System Analytics")
        
        self.sys_timer = QTimer(); self.sys_timer.timeout.connect(self.update_sys); self.sys_timer.start(3000)

    def update_sys(self):
        self.cpu_bar.setValue(int(psutil.cpu_percent()))
        self.ram_bar.setValue(int(psutil.virtual_memory().percent))

    def load_playlist(self):
        try:
            res = requests.get(self.m3u_i.text(), timeout=10)
            self.channels_data = []
            for line in res.text.splitlines():
                if line.startswith("#EXTINF"): name = re.search(r',([^,]+)$', line).group(1)
                elif line.startswith("http"): self.channels_data.append({'name': name, 'url': line})
            self.refresh_tables()
        except: pass

    def refresh_tables(self):
        self.table.setRowCount(0); self.a_table.setRowCount(0)
        for ch in self.channels_data:
            r = self.table.rowCount(); self.table.insertRow(r); self.a_table.insertRow(r)
            chk = QCheckBox(); cw = QWidget(); cl = QHBoxLayout(cw); cl.addWidget(chk); self.table.setCellWidget(r, 0, cw)
            self.table.setItem(r, 1, QTableWidgetItem(ch['name'])); self.table.setItem(r, 2, QTableWidgetItem("Idle"))
            self.table.setItem(r, 3, QTableWidgetItem("---"))
            btn_stop = QPushButton("Stop"); btn_stop.clicked.connect(lambda _, n=ch['name']: self.stop_single(n))
            self.table.setCellWidget(r, 4, btn_stop)
            self.a_table.setItem(r, 0, QTableWidgetItem(ch['name']))
            self.a_table.setItem(r, 1, QTableWidgetItem("00:00")); self.a_table.setItem(r, 2, QTableWidgetItem("0MB"))

    def start_selected(self):
        conf = {'server': self.server_i.text(), 'user': self.user_i.text(), 'pass': self.pass_i.text()}
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r,0).layout().itemAt(0).widget().isChecked():
                name = self.table.item(r,1).text()
                if name not in self.active_workers:
                    worker = RecordingWorker(name, self.channels_data[r]['url'], self.output_folder, conf)
                    worker.status_changed.connect(self.update_ui); threading.Thread(target=worker.start_recording, daemon=True).start()
                    self.active_workers[name] = worker
        self.save_state()

    def update_ui(self, name, s):
        for r in range(self.table.rowCount()):
            if self.table.item(r, 1).text() == name:
                self.table.item(r, 2).setText(s['status']); self.table.item(r, 3).setText(s['link'])
                self.a_table.item(r, 1).setText(s['uptime']); self.a_table.item(r, 2).setText(s['size'])

    def stop_single(self, name):
        if name in self.active_workers:
            self.active_workers[name].stop(); del self.active_workers[name]
            send_telegram_msg(f"🛑 <b>הקלטה הופסקה</b>\nערוץ: {name}")

    def stop_all(self):
        for w in self.active_workers.values(): w.stop()
        self.active_workers.clear()

    def save_state(self):
        state = {"server": self.server_i.text(), "user": self.user_i.text(), "pass": self.pass_i.text(), "m3u": self.m3u_i.text(), "active": list(self.active_workers.keys())}
        with open(CONFIG_FILE, "w") as f: json.dump(state, f)

    def auto_load_last_state(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                s = json.load(f); self.m3u_i.setText(s.get("m3u","")); self.load_playlist()
                for r in range(self.table.rowCount()):
                    if self.table.item(r,1).text() in s.get("active", []):
                        self.table.cellWidget(r,0).layout().itemAt(0).widget().setChecked(True)
                self.start_selected()

if __name__ == "__main__":
    app = QApplication(sys.argv); win = IPTVHotelSuite(); win.show(); sys.exit(app.exec())
