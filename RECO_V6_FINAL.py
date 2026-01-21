import sys, subprocess, threading, time, os, re, requests, psutil, json
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QHeaderView, QLabel, QCheckBox, 
                             QFrame, QProgressBar, QMessageBox, QGridLayout, QTabWidget, QTextEdit)
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
    log_msg = pyqtSignal(str)

    def __init__(self, channel_name, url, output_folder, iptv_config=None):
        super().__init__()
        self.channel_name = channel_name
        self.url = url
        self.output_folder = os.path.abspath(output_folder)
        self.iptv_config = iptv_config
        self.is_running = True
        self.process = None

    def get_or_create_category(self, base_url, user, password):
        cat_name = "Channels"
        try:
            self.log_msg.emit(f"🔍 Checking category '{cat_name}'...")
            auth = f"username={user}&password={password}"
            # ניסיון גמיש למציאת ה-API
            api_path = f"{base_url}/api.php"
            res = requests.get(f"{api_path}?action=get_categories&{auth}", timeout=7).json()
            
            for c in res:
                if c.get('category_name') == cat_name:
                    self.log_msg.emit(f"✅ Found category ID: {c.get('category_id')}")
                    return c.get('category_id')
            
            self.log_msg.emit(f"➕ Creating new category '{cat_name}'...")
            new_cat = requests.post(f"{api_path}?action=add_category", 
                                    data={"username":user,"password":password,"category_name":cat_name,"category_type":"live"}).json()
            return new_cat.get('category_id', "1")
        except Exception as e:
            self.log_msg.emit(f"❌ API Category Error: {str(e)}")
            return "1"

    def start_recording(self):
        start_time = time.time()
        safe_name = re.sub(r'[\\/*?:"<>|]', "", self.channel_name).strip().replace(" ", "_")
        channel_path = os.path.join(self.output_folder, safe_name)
        
        # וידוא הרשאות תיקייה
        try:
            os.makedirs(channel_path, exist_ok=True)
            os.chmod(channel_path, 0o777)
        except: pass
        
        broadcast_link = "---"
        if self.iptv_config and all(self.iptv_config.values()):
            c = self.iptv_config
            base = c['server'].split('/dashboard')[0].rstrip('/')
            cat_id = self.get_or_create_category(base, c['user'], c['pass'])
            
            # יצירת/עדכון סטרים
            api_path = f"{base}/api.php"
            requests.post(f"{api_path}?action=add_stream", 
                          data={"username":c['user'],"password":c['pass'],"stream_display_name":self.channel_name,
                                "stream_source":["127.0.0.1"],"category_id":cat_id,"stream_mode":"live"})
            
            broadcast_link = f"{base}/live/{c['user']}/{c['pass']}/{safe_name}.ts"

        send_telegram_msg(f"🚀 <b>ערוץ עלה לאוויר</b>\nשם: {self.channel_name}")

        while self.is_running:
            timestamp = datetime.now().strftime("%H%M%S")
            output_file = os.path.join(channel_path, f"rec_{timestamp}.ts")
            
            # פקודת FFmpeg עם דגלי יציבות
            cmd = ['ffmpeg', '-y', '-re', '-i', self.url, '-c', 'copy', '-f', 'mpegts']
            
            if broadcast_link != "---":
                cmd.extend([f"tee:f=mpegts|{output_file}|{broadcast_link}"])
                # שימוש בכתובת המלאה ל-Tee
                full_cmd = f"ffmpeg -y -re -i \"{self.url}\" -c copy -f tee \"[f=mpegts]{output_file}|[f=mpegts:onfail=ignore]{broadcast_link}\""
                self.process = subprocess.Popen(shlex.split(full_cmd), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            else:
                self.process = subprocess.Popen(cmd + [output_file], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

            try:
                while self.process.poll() is None and self.is_running:
                    uptime = time.strftime("%H:%M:%S", time.gmtime(time.time() - start_time))
                    size = sum(os.path.getsize(os.path.join(channel_path, f)) for f in os.listdir(channel_path) if os.path.isfile(os.path.join(channel_path, f)))
                    self.status_changed.emit(self.channel_name, {"status": "Active", "uptime": uptime, "size": f"{size/(1024*1024):.1f}MB", "link": broadcast_link})
                    time.sleep(5)
                if self.is_running: 
                    self.log_msg.emit(f"⚠️ Reconnecting {self.channel_name}...")
                    time.sleep(10)
            except: time.sleep(10)

    def stop(self):
        self.is_running = False
        if self.process: self.process.kill()

import shlex

class IPTVHotelSuite(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("X-HOTEL IPTV COMMAND CENTER v9.6")
        self.resize(1500, 950)
        self.active_workers = {}
        self.last_net_io = psutil.net_io_counters()
        
        # Styling
        self.setStyleSheet("""
            QMainWindow { background-color: #0f111a; }
            QTabWidget::pane { border: 1px solid #1a1c2c; background: #0f111a; }
            QTabBar::tab { background: #1a1c2c; color: #a6accd; padding: 15px; border-top-left-radius: 10px; border-top-right-radius: 10px; min-width: 150px; }
            QTabBar::tab:selected { background: #292d3e; color: #89ddff; font-weight: bold; }
            QLabel { color: #89ddff; font-weight: bold; }
            QPushButton { background-color: #3b3f51; color: white; border-radius: 8px; font-weight: bold; border: 1px solid #89ddff; }
            QPushButton:hover { background-color: #89ddff; color: #0f111a; }
            QLineEdit { background-color: #1a1c2c; color: white; border: 1px solid #3b3f51; border-radius: 5px; padding: 8px; }
            QTableWidget { background-color: #0f111a; color: #a6accd; border: none; gridline-color: #1a1c2c; }
        """)
        
        self.init_ui()
        QTimer.singleShot(1000, self.auto_load_last_state)
        self.stats_timer = QTimer(); self.stats_timer.timeout.connect(self.update_live_stats); self.stats_timer.start(2000)

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        # Header with Stats
        header = QFrame(); header.setFixedHeight(80)
        h_layout = QHBoxLayout(header)
        self.net_lbl = QLabel("NETWORK: IN 0 KB/s | OUT 0 KB/s")
        self.cpu_lbl = QLabel("CPU: 0%")
        h_layout.addWidget(self.net_lbl); h_layout.addStretch(); h_layout.addWidget(self.cpu_lbl)
        main_layout.addWidget(header)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Tab 1: Control & Streams
        self.tab_control = QWidget(); control_layout = QVBoxLayout(self.tab_control)
        
        # Config Card
        conf_card = QFrame(); conf_card.setStyleSheet("background: #1a1c2c; border-radius: 15px; padding: 10px;")
        grid = QGridLayout(conf_card)
        self.server_i = QLineEdit("http://144.91.86.250/mbmWePBa"); self.user_i = QLineEdit("admin"); self.pass_i = QLineEdit("MazalTovLanu")
        self.m3u_i = QLineEdit(); self.m3u_i.setPlaceholderText("M3U URL Here...")
        grid.addWidget(QLabel("PORTAL URL"), 0, 0); grid.addWidget(self.server_i, 0, 1)
        grid.addWidget(QLabel("USER"), 0, 2); grid.addWidget(self.user_i, 0, 3)
        grid.addWidget(QLabel("PASS"), 0, 4); grid.addWidget(self.pass_i, 0, 5)
        grid.addWidget(QLabel("M3U LIST"), 1, 0); grid.addWidget(self.m3u_i, 1, 1, 1, 4)
        btn_load = QPushButton("LOAD SYSTEM"); btn_load.setFixedHeight(40); btn_load.clicked.connect(self.load_playlist)
        grid.addWidget(btn_load, 1, 5)
        control_layout.addWidget(conf_card)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["SEL", "CHANNEL NAME", "STATUS", "UPTIME", "DISK", "XUI LINK"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        control_layout.addWidget(self.table)
        
        btn_start = QPushButton("🚀 INITIATE SYSTEM FLOW"); btn_start.setFixedHeight(50); btn_start.clicked.connect(self.start_selected)
        control_layout.addWidget(btn_start)
        
        self.tabs.addTab(self.tab_control, "COMMAND CENTER")

        # Tab 2: Logs & System
        self.tab_logs = QWidget(); log_layout = QVBoxLayout(self.tab_logs)
        self.log_output = QTextEdit(); self.log_output.setReadOnly(True); self.log_output.setStyleSheet("background: #000; color: #00ff00; font-family: 'Courier New';")
        log_layout.addWidget(QLabel("SYSTEM DEBUG LOGS:"))
        log_layout.addWidget(self.log_output)
        self.tabs.addTab(self.tab_logs, "SYSTEM LOGS")

    def update_live_stats(self):
        # Network
        new_net = psutil.net_io_counters()
        in_s = (new_net.bytes_recv - self.last_net_io.bytes_recv) / 2048
        out_s = (new_net.bytes_sent - self.last_net_io.bytes_sent) / 2048
        self.net_lbl.setText(f"NETWORK: IN {in_s:.1f} KB/s | OUT {out_s:.1f} KB/s")
        self.cpu_lbl.setText(f"CPU: {psutil.cpu_percent()}% | RAM: {psutil.virtual_memory().percent}%")
        self.last_net_io = new_net

    def add_log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_output.append(f"[{ts}] {msg}")

    def load_playlist(self):
        try:
            res = requests.get(self.m3u_i.text(), timeout=10)
            self.channels_data = []
            for line in res.text.splitlines():
                if line.startswith("#EXTINF"): name = re.search(r',([^,]+)$', line).group(1).strip()
                elif line.startswith("http"): self.channels_data.append({'name': name, 'url': line})
            self.refresh_table(); self.add_log(f"Loaded {len(self.channels_data)} channels.")
        except Exception as e: self.add_log(f"Error loading M3U: {e}")

    def refresh_table(self):
        self.table.setRowCount(0)
        for ch in self.channels_data:
            r = self.table.rowCount(); self.table.insertRow(r)
            chk = QCheckBox(); cw = QWidget(); cl = QHBoxLayout(cw); cl.addWidget(chk); cl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.table.setCellWidget(r, 0, cw)
            self.table.setItem(r, 1, QTableWidgetItem(ch['name']))
            for i in range(2, 6): self.table.setItem(r, i, QTableWidgetItem("---"))

    def start_selected(self):
        conf = {'server': self.server_i.text(), 'user': self.user_i.text(), 'pass': self.pass_i.text()}
        for r in range(self.table.rowCount()):
            chk_widget = self.table.cellWidget(r, 0)
            if chk_widget and chk_widget.layout().itemAt(0).widget().isChecked():
                name = self.table.item(r, 1).text()
                if name not in self.active_workers:
                    worker = RecordingWorker(name, self.channels_data[r]['url'], "/root/Recordings", conf)
                    worker.status_changed.connect(self.update_row)
                    worker.log_msg.connect(self.add_log)
                    threading.Thread(target=worker.start_recording, daemon=True).start()
                    self.active_workers[name] = worker
        self.save_state()

    def update_row(self, name, s):
        for r in range(self.table.rowCount()):
            if self.table.item(r, 1).text() == name:
                self.table.item(r, 2).setText(s['status'])
                self.table.item(r, 3).setText(s['uptime'])
                self.table.item(r, 4).setText(s['size'])
                self.table.item(r, 5).setText(s['link'])
                self.table.item(r, 2).setForeground(Qt.GlobalColor.green)

    def save_state(self):
        state = {"server": self.server_i.text(), "user": self.user_i.text(), "pass": self.pass_i.text(), "m3u": self.m3u_i.text(), "active": list(self.active_workers.keys())}
        with open(CONFIG_FILE, "w") as f: json.dump(state, f)

    def auto_load_last_state(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    s = json.load(f); self.m3u_i.setText(s.get("m3u","")); self.load_playlist()
                    for r in range(self.table.rowCount()):
                        if self.table.item(r,1).text() in s.get("active", []):
                            self.table.cellWidget(r,0).layout().itemAt(0).widget().setChecked(True)
                    self.start_selected(); self.add_log("System restored from last state.")
            except: pass

if __name__ == "__main__":
    app = QApplication(sys.argv); app.setStyle("Fusion"); win = IPTVHotelSuite(); win.show(); sys.exit(app.exec())
