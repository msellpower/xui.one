import sys, subprocess, threading, time, os, re, requests, psutil, json
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QLabel, QCheckBox, 
                             QFrame, QProgressBar, QMessageBox, QGridLayout)
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QTimer

# --- הגדרות טלגרם ---
TELEGRAM_TOKEN = "8307008722:AAHY-QYNYyTnOwjS0q4VGfA0_iUiQBxYHBc"
TELEGRAM_CHAT_ID = "כאן_תדביק_את_המספר_שקיבלת_מהבוט" # המספר שקיבלת (למשל 12345678)

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
        self.start_time = None
        self.process = None
        self.error_count = 0

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

    def get_folder_size(self, path):
        total_size = 0
        if not os.path.exists(path): return 0
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp): total_size += os.path.getsize(fp)
        return total_size / (1024 * 1024)

    def start_recording(self):
        self.start_time = time.time()
        safe_name = re.sub(r'[\\/*?:"<>|]', "", self.channel_name).strip().replace(" ", "_")
        channel_path = os.path.join(self.output_folder, safe_name)
        os.makedirs(channel_path, exist_ok=True)
        
        broadcast_link = "---"
        if self.iptv_config and self.iptv_config.get('server'):
            c = self.iptv_config
            base = c['server'].split('/dashboard')[0].rstrip('/')
            cat_id = self.get_or_create_category(base, c['user'], c['pass'])
            requests.post(f"{base}/api.php?action=add_stream", 
                          data={"username":c['user'],"password":c['pass'],"stream_display_name":self.channel_name,
                                "stream_source":["127.0.0.1"],"category_id":cat_id,"stream_mode":"live"})
            broadcast_link = f"{base}/live/{c['user']}/{c['pass']}/{safe_name}.ts"

        send_telegram_msg(f"✅ <b>מערכת הופעלה</b>\nערוץ: {self.channel_name}\nסטטוס: התחלת שידור והקלטה")

        while self.is_running:
            timestamp = datetime.now().strftime("%H%M%S")
            output_file = os.path.join(channel_path, f"rec_{timestamp}.ts")
            cmd = ['ffmpeg', '-y', '-re', '-i', self.url, '-c', 'copy']
            if broadcast_link != "---":
                cmd.extend(['-f', 'tee', f"[f=mpegts]'{output_file}'|[f=mpegts:onfail=ignore]{broadcast_link}"])
            else:
                cmd.extend([output_file])

            try:
                self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                while self.process.poll() is None and self.is_running:
                    uptime = time.strftime("%H:%M:%S", time.gmtime(time.time() - self.start_time))
                    size = self.get_folder_size(channel_path)
                    self.status_changed.emit(self.channel_name, {"status": "Active", "uptime": uptime, "size": f"{size:.2f} MB", "link": broadcast_link})
                    time.sleep(5)

                if self.is_running:
                    self.error_count += 1
                    send_telegram_msg(f"⚠️ <b>ניתוק בערוץ</b>\nערוץ: {self.channel_name}\nניסיון חיבור מחדש: {self.error_count}")
                    time.sleep(10)
            except Exception as e:
                send_telegram_msg(f"❌ <b>שגיאה קריטית</b>\nערוץ: {self.channel_name}\n{str(e)}")
                time.sleep(20)

    def stop(self):
        self.is_running = False
        if self.process: self.process.kill()

class IPTVHotelSuite(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hotel IPTV Analytics Dashboard v8.5")
        self.resize(1400, 850)
        self.setStyleSheet("QMainWindow { background-color: #1e1e2e; } QLabel { color: #cdd6f4; } QTableWidget { background-color: #1e1e2e; color: #cdd6f4; }")
        self.active_workers, self.all_channels = {}, []
        self.output_folder = "/root/Recordings"
        self.init_ui()
        QTimer.singleShot(1000, self.auto_load_last_state)
        self.sys_timer = QTimer(); self.sys_timer.timeout.connect(self.update_sys_stats); self.sys_timer.start(2000)

    def init_ui(self):
        central = QWidget(); self.setCentralWidget(central); main_layout = QVBoxLayout(central)
        stats_frame = QFrame(); stats_layout = QHBoxLayout(stats_frame)
        self.cpu_bar = QProgressBar(); self.ram_bar = QProgressBar()
        stats_layout.addWidget(QLabel("CPU:")); stats_layout.addWidget(self.cpu_bar)
        stats_layout.addWidget(QLabel("RAM:")); stats_layout.addWidget(self.ram_bar); main_layout.addWidget(stats_frame)
        
        conf_f = QFrame(); l = QGridLayout(conf_f)
        self.server_i = QLineEdit("http://144.91.86.250/mbmWePBa"); self.user_i = QLineEdit("admin"); self.pass_i = QLineEdit("MazalTovLanu")
        l.addWidget(QLabel("XUI:"), 0, 0); l.addWidget(self.server_i, 0, 1); l.addWidget(QLabel("User:"), 0, 2); l.addWidget(self.user_i, 0, 3)
        self.m3u_i = QLineEdit(); l.addWidget(QLabel("M3U:"), 1, 0); l.addWidget(self.m3u_i, 1, 1, 1, 3)
        btn_l = QPushButton("LOAD"); btn_l.clicked.connect(self.load_playlist); l.addWidget(btn_l, 1, 4); main_layout.addWidget(conf_f)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Select", "Name", "Status", "Uptime", "Storage", "Source", "XUI Link"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        main_layout.addWidget(self.table)

        btn_s = QPushButton("🚀 START & SAVE STATE"); btn_s.clicked.connect(self.start_selected)
        btn_s.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; height: 50px;"); main_layout.addWidget(btn_s)

    def update_sys_stats(self):
        self.cpu_bar.setValue(int(psutil.cpu_percent())); self.ram_bar.setValue(int(psutil.virtual_memory().percent))

    def load_playlist(self):
        try:
            res = requests.get(self.m3u_i.text(), timeout=10)
            self.all_channels = []
            name = "Cam"
            for line in res.text.splitlines():
                if line.startswith("#EXTINF"): 
                    m = re.search(r',([^,]+)$', line); name = m.group(1).strip() if m else "Cam"
                elif line.startswith("http"): self.all_channels.append({'name': name, 'url': line})
            self.refresh_table()
        except: pass

    def refresh_table(self):
        self.table.setRowCount(0)
        for ch in self.all_channels:
            r = self.table.rowCount(); self.table.insertRow(r)
            chk = QCheckBox(); cw = QWidget(); cl = QHBoxLayout(cw); cl.addWidget(chk); cl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.table.setCellWidget(r, 0, cw)
            self.table.setItem(r, 1, QTableWidgetItem(ch['name'])); self.table.setItem(r, 2, QTableWidgetItem("Idle"))
            self.table.setItem(r, 3, QTableWidgetItem("00:00")); self.table.setItem(r, 4, QTableWidgetItem("0MB"))
            self.table.setItem(r, 5, QTableWidgetItem(ch['url'])); self.table.setItem(r, 6, QTableWidgetItem("---"))

    def start_selected(self):
        conf = {'server': self.server_i.text(), 'user': self.user_i.text(), 'pass': self.pass_i.text()}
        for r in range(self.table.rowCount()):
            chk = self.table.cellWidget(r, 0).layout().itemAt(0).widget()
            if chk.isChecked():
                name, url = self.table.item(r, 1).text(), self.table.item(r, 5).text()
                if name not in self.active_workers:
                    worker = RecordingWorker(name, url, self.output_folder, conf)
                    worker.status_changed.connect(self.update_row); threading.Thread(target=worker.start_recording, daemon=True).start()
                    self.active_workers[name] = worker
        self.save_state()

    def update_row(self, name, s):
        for r in range(self.table.rowCount()):
            if self.table.item(r, 1).text() == name:
                self.table.item(r, 2).setText(s['status']); self.table.item(r, 3).setText(s['uptime'])
                self.table.item(r, 4).setText(s['size']); self.table.item(r, 6).setText(s['link'])
                self.table.item(r, 2).setForeground(Qt.GlobalColor.green)

    def save_state(self):
        state = {"server": self.server_i.text(), "user": self.user_i.text(), "pass": self.pass_i.text(), "m3u_url": self.m3u_i.text(), "active_channels": list(self.active_workers.keys())}
        with open(CONFIG_FILE, "w") as f: json.dump(state, f)

    def auto_load_last_state(self):
        if not os.path.exists(CONFIG_FILE): return
        with open(CONFIG_FILE, "r") as f:
            state = json.load(f); self.m3u_i.setText(state.get("m3u_url", "")); self.load_playlist()
            active_list = state.get("active_channels", [])
            for r in range(self.table.rowCount()):
                if self.table.item(r, 1).text() in active_list:
                    self.table.cellWidget(r, 0).layout().itemAt(0).widget().setChecked(True)
            self.start_selected()

if __name__ == "__main__":
    app = QApplication(sys.argv); app.setStyle("Fusion"); win = IPTVHotelSuite(); win.show(); sys.exit(app.exec())
