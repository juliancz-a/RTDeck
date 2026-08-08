import socket
import threading
import queue
import json
import os
import sys
import time
import webbrowser
import tkinter as tk
from tkinter import ttk

# PyWinStyles setup for Windows Title Bar styling
try:
    import pywinstyles
    PYWINSTYLES_AVAILABLE = True
except ImportError:
    PYWINSTYLES_AVAILABLE = False


def get_app_dir() -> str:
    """
    Returns the directory path containing the executable or script.
    Crucial for PyInstaller --onefile: ensures config/prefs files are saved
    in the executable's directory rather than the temporary sys._MEIPASS folder.
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(relative_path: str) -> str:
    """
    Get absolute path to bundled resource (works for dev and PyInstaller --onefile).
    PyInstaller extracts bundled data files into sys._MEIPASS at runtime.
    """
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = get_app_dir()
    return os.path.join(base_path, relative_path)


CLIENT_PREFS_FILE = os.path.join(get_app_dir(), "client_prefs.json")


# ---------------- CLIENT PREFERENCES MANAGER ----------------

class ClientPreferencesManager:
    """Manages persistent client settings (theme, language, fullscreen, last_ip, last_port)."""
    def __init__(self, filepath=CLIENT_PREFS_FILE):
        self.filepath = filepath
        self.prefs = self.load()

    def load(self) -> dict:
        defaults = {
            "theme": "dark",
            "language": "en",
            "fullscreen": False,
            "last_ip": "127.0.0.1",
            "last_port": "65432"
        }
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    defaults.update(data)
            except Exception as e:
                print(f"[Client Prefs Load Error] {e}")
        return defaults

    def save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.prefs, f, indent=2)
        except Exception as e:
            print(f"[Client Prefs Save Error] {e}")

    def get(self, key: str, default=None):
        return self.prefs.get(key, default)

    def set(self, key: str, value):
        self.prefs[key] = value
        self.save()


client_prefs = ClientPreferencesManager()


def apply_azure_theme(root: tk.Tk):
    """Loads and applies the Azure TCL theme if present."""
    tcl_path = resource_path("azure.tcl")
    if os.path.exists(tcl_path):
        try:
            clean_path = tcl_path.replace("\\", "/")
            root.tk.call("source", clean_path)
            theme_mode = client_prefs.get("theme", "dark")
            root.tk.call("set_theme", theme_mode)
        except Exception as e:
            print(f"[Theme Warning] Could not load azure.tcl from '{tcl_path}': {e}")


def b64_to_photo_image(b64_str: str):
    """Decodes a Base64 PNG string directly to a tk.PhotoImage (No Pillow required)."""
    if not b64_str:
        return None
    try:
        return tk.PhotoImage(data=b64_str)
    except Exception as e:
        print(f"[tk.PhotoImage Error] {e}")
        return None


# ---------------- i18n LANGUAGE MANAGER ----------------

class LanguageManager:
    """Manages internationalization (i18n) via lang.json."""
    def __init__(self, default_lang="en"):
        self.active_lang = default_lang
        self.translations = self._load_lang_file()
        self.subscribers = []

    def _load_lang_file(self):
        lang_path = resource_path("lang.json")
        if os.path.exists(lang_path):
            try:
                with open(lang_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[Lang Load Error] {e}")
        return {}

    def set_language(self, lang_code: str):
        if lang_code in self.translations:
            self.active_lang = lang_code
            client_prefs.set("language", lang_code)
            self.notify_subscribers()

    def tr(self, key: str, fallback: str = "") -> str:
        lang_dict = self.translations.get(self.active_lang, {})
        if key in lang_dict:
            return lang_dict[key]
        return self.translations.get("en", {}).get(key, fallback if fallback else key)

    def subscribe(self, callback):
        self.subscribers.append(callback)

    def notify_subscribers(self):
        for cb in self.subscribers:
            try:
                cb()
            except Exception as e:
                print(f"[Lang Subscriber Error] {e}")


# ---------------- LOCAL PNG ICON MANAGER ----------------

class IconManager:
    """Loads, resizes (via subsample), and caches local PNG icons from icons/ folder without Pillow."""
    def __init__(self):
        self.cache = {}

    def get_icon(self, icon_name: str, subsample_factor: int = 10) -> tk.PhotoImage:
        cache_key = f"{icon_name}_{subsample_factor}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        filename_map = {
            "info": "information-circle.png",
            "settings": "settings.png",
            "vol_up": "sound-increase.png",
            "vol_down": "sound-reduce.png",
            "play": "play.png",
            "pause": "stop-circle.png",
            "next_track": "fast-forward-circle.png",
            "mic_unmuted": "microphone.png",
            "mic_muted": "microphone-disable.png",
            "prev_page": "left-direction-circle.png",
            "next_page": "right-direction-circle.png"
        }

        filename = filename_map.get(icon_name, "")
        if not filename:
            return None

        icon_path = resource_path(os.path.join("icons", filename))
        if os.path.exists(icon_path):
            try:
                img = tk.PhotoImage(file=icon_path)
                if subsample_factor > 1:
                    img = img.subsample(subsample_factor, subsample_factor)
                self.cache[cache_key] = img
                return img
            except Exception as e:
                print(f"[Icon Load Error '{icon_name}'] {e}")
        return None


# Global singletons
lang_mgr = LanguageManager(default_lang=client_prefs.get("language", "en"))
icon_mgr = IconManager()


class TwoWayNetworkClient:
    """Handles line-delimited JSON TCP networking in a background thread."""
    def __init__(self, message_callback, status_callback):
        self.host = client_prefs.get("last_ip", "127.0.0.1")
        self.port = int(client_prefs.get("last_port", "65432"))
        self.sock = None
        self.is_connected = False
        self.running = True
        self.send_queue = queue.Queue()
        self.message_callback = message_callback
        self.status_callback = status_callback

        self.worker_thread = threading.Thread(target=self._network_loop, daemon=True)
        self.worker_thread.start()

    def connect(self, host: str, port: int):
        self.host = host.strip()
        self.port = int(port)
        client_prefs.set("last_ip", self.host)
        client_prefs.set("last_port", str(self.port))
        self.disconnect()
        self.send_queue.put(('CONNECT', None))

    def disconnect(self):
        self.is_connected = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self.status_callback(lang_mgr.tr("disconnected", "Disconnected"), "#e74c3c")

    def send_json(self, payload: dict):
        self.send_queue.put(('SEND', payload))

    def _network_loop(self):
        while self.running:
            try:
                try:
                    msg_type, payload = self.send_queue.get(timeout=0.1)

                    if msg_type == 'CONNECT':
                        self.status_callback(lang_mgr.tr("connecting", "Connecting..."), "#f39c12")
                        try:
                            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            self.sock.settimeout(3.0)
                            self.sock.connect((self.host, self.port))
                            self.sock.settimeout(None)
                            self.is_connected = True
                            self.status_callback(f"{lang_mgr.tr('connected', 'Connected to')} {self.host}", "#2ecc71")

                            rx_thread = threading.Thread(target=self._receive_loop, daemon=True)
                            rx_thread.start()
                        except Exception as e:
                            self.is_connected = False
                            if self.sock:
                                self.sock.close()
                                self.sock = None
                            self.status_callback(f"{lang_mgr.tr('failed', 'Failed')}: {e}", "#e74c3c")

                    elif msg_type == 'SEND':
                        if self.is_connected and self.sock:
                            try:
                                data = (json.dumps(payload) + "\n").encode('utf-8')
                                self.sock.sendall(data)
                            except Exception as e:
                                self.disconnect()
                                self.status_callback(f"Send Failed: {e}", "#e74c3c")

                except queue.Empty:
                    pass

            except Exception as e:
                print(f"[Network Loop Exception] {e}")

    def _receive_loop(self):
        buffer = ""
        sock = self.sock
        while self.is_connected and sock == self.sock:
            try:
                data = sock.recv(1024)
                if not data:
                    break
                buffer += data.decode('utf-8', errors='ignore')

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        try:
                            msg = json.loads(line.strip())
                            self.message_callback(msg)
                        except json.JSONDecodeError:
                            pass
            except Exception:
                break

        if sock == self.sock:
            self.disconnect()


# ---------------- TTK MODULAR UI WIDGETS ----------------

class TelemetryWidget(ttk.LabelFrame):
    """Component: Live PC Telemetry (CPU % and RAM %)."""
    def __init__(self, master, **kwargs):
        super().__init__(master, text=f" {lang_mgr.tr('telemetry', 'PC Telemetry')} ", padding=(10, 6), **kwargs)

        cpu_frame = ttk.Frame(self)
        cpu_frame.pack(fill="x", pady=2)
        self.cpu_text = ttk.Label(cpu_frame, text="CPU: 0%", font=("Segoe UI", 9, "bold"), width=10)
        self.cpu_text.pack(side="left")
        self.cpu_bar = ttk.Progressbar(cpu_frame, maximum=100.0, length=120)
        self.cpu_bar.pack(side="right", fill="x", expand=True, padx=(5, 0))

        ram_frame = ttk.Frame(self)
        ram_frame.pack(fill="x", pady=2)
        self.ram_text = ttk.Label(ram_frame, text="RAM: 0%", font=("Segoe UI", 9, "bold"), width=10)
        self.ram_text.pack(side="left")
        self.ram_bar = ttk.Progressbar(ram_frame, maximum=100.0, length=120)
        self.ram_bar.pack(side="right", fill="x", expand=True, padx=(5, 0))

        lang_mgr.subscribe(self.retranslate_ui)

    def retranslate_ui(self):
        self.configure(text=f" {lang_mgr.tr('telemetry', 'PC Telemetry')} ")

    def update_telemetry(self, cpu: float, ram: float):
        self.cpu_text.configure(text=f"CPU: {cpu}%")
        self.cpu_bar['value'] = max(0.0, min(100.0, cpu))

        self.ram_text.configure(text=f"RAM: {ram}%")
        self.ram_bar['value'] = max(0.0, min(100.0, ram))


class MediaWidget(ttk.LabelFrame):
    """Component: SMTC Now Playing Media Card."""
    def __init__(self, master, **kwargs):
        super().__init__(master, text=f" {lang_mgr.tr('now_playing', 'Now Playing')} ", padding=(10, 6), **kwargs)

        self.track_title_label = ttk.Label(self, text=lang_mgr.tr("no_media", "No Media Playing"), font=("Segoe UI", 11, "bold"))
        self.track_title_label.pack(anchor="w", pady=(0, 1))

        self.track_artist_label = ttk.Label(self, text="Windows SMTC", font=("Segoe UI", 9), foreground="#aaaaaa")
        self.track_artist_label.pack(anchor="w", pady=(0, 4))

        self.media_progress = ttk.Progressbar(self, maximum=1.0)
        self.media_progress.pack(fill="x", pady=(0, 4))

        lang_mgr.subscribe(self.retranslate_ui)

    def retranslate_ui(self):
        self.configure(text=f" {lang_mgr.tr('now_playing', 'Now Playing')} ")

    def update_media(self, title: str, artist: str, progress: float):
        self.track_title_label.configure(text=title if title else lang_mgr.tr("no_media", "No Media Playing"))
        self.track_artist_label.configure(text=artist if artist else "Windows SMTC")
        self.media_progress['value'] = max(0.0, min(1.0, progress))


class MacroGridWidget(ttk.Frame):
    """
    Component: Hardware Media Action Controls using Local PNG Icons.
    Includes 5 controls: Vol Up, Vol Down, Play/Pause, Next Track, Mic Mute.
    """
    def __init__(self, master, send_action_callback, **kwargs):
        super().__init__(master, **kwargs)
        self.send_action_callback = send_action_callback

        self.columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.icon_vol_up = icon_mgr.get_icon("vol_up", subsample_factor=10)
        self.icon_vol_down = icon_mgr.get_icon("vol_down", subsample_factor=10)
        self.icon_play = icon_mgr.get_icon("play", subsample_factor=10)
        self.icon_pause = icon_mgr.get_icon("pause", subsample_factor=10)
        self.icon_next = icon_mgr.get_icon("next_track", subsample_factor=10)
        self.icon_mic_unmuted = icon_mgr.get_icon("mic_unmuted", subsample_factor=10)
        self.icon_mic_muted = icon_mgr.get_icon("mic_muted", subsample_factor=10)

        # 1. Volume Up
        self.vol_up_btn = ttk.Button(
            self,
            text=f" {lang_mgr.tr('vol_up', 'Vol Up')}",
            image=self.icon_vol_up,
            compound="left",
            command=lambda: self.send_action_callback("VOL_UP")
        )
        self.vol_up_btn.grid(row=0, column=0, padx=3, pady=4, ipady=6, sticky="nsew")

        # 2. Volume Down
        self.vol_down_btn = ttk.Button(
            self,
            text=f" {lang_mgr.tr('vol_down', 'Vol Down')}",
            image=self.icon_vol_down,
            compound="left",
            command=lambda: self.send_action_callback("VOL_DOWN")
        )
        self.vol_down_btn.grid(row=0, column=1, padx=3, pady=4, ipady=6, sticky="nsew")

        # 3. Play / Pause Stateful Toggle
        self.play_var = tk.BooleanVar(value=False)
        self.play_btn = ttk.Checkbutton(
            self,
            text=f" {lang_mgr.tr('play', 'Play')}",
            image=self.icon_play,
            compound="left",
            variable=self.play_var,
            style="Toggle.TButton",
            command=self.on_play_toggle
        )
        self.play_btn.grid(row=0, column=2, padx=3, pady=4, ipady=6, sticky="nsew")

        # 4. Next Track
        self.next_track_btn = ttk.Button(
            self,
            text=f" {lang_mgr.tr('next_track', 'Next Track')}",
            image=self.icon_next,
            compound="left",
            command=lambda: self.send_action_callback("NEXT_TRACK")
        )
        self.next_track_btn.grid(row=0, column=3, padx=3, pady=4, ipady=6, sticky="nsew")

        # 5. Mic Mute Stateful Toggle
        self.mic_var = tk.BooleanVar(value=False)
        self.mic_btn = ttk.Checkbutton(
            self,
            text=f" {lang_mgr.tr('mic_unmuted', 'Unmuted')}",
            image=self.icon_mic_unmuted,
            compound="left",
            variable=self.mic_var,
            style="Toggle.TButton",
            command=self.on_mic_toggle
        )
        self.mic_btn.grid(row=0, column=4, padx=3, pady=4, ipady=6, sticky="nsew")

        lang_mgr.subscribe(self.retranslate_ui)

    def retranslate_ui(self):
        self.vol_up_btn.configure(text=f" {lang_mgr.tr('vol_up', 'Vol Up')}")
        self.vol_down_btn.configure(text=f" {lang_mgr.tr('vol_down', 'Vol Down')}")
        self.next_track_btn.configure(text=f" {lang_mgr.tr('next_track', 'Next Track')}")

        is_playing = self.play_var.get()
        p_text = lang_mgr.tr('pause', 'Pause') if is_playing else lang_mgr.tr('play', 'Play')
        self.play_btn.configure(text=f" {p_text}")

        is_muted = self.mic_var.get()
        m_text = lang_mgr.tr('mic_muted', 'Muted') if is_muted else lang_mgr.tr('mic_unmuted', 'Unmuted')
        self.mic_btn.configure(text=f" {m_text}")

    def on_play_toggle(self):
        is_playing = self.play_var.get()
        new_text = lang_mgr.tr('pause', 'Pause') if is_playing else lang_mgr.tr('play', 'Play')
        new_img = self.icon_pause if is_playing else self.icon_play
        self.play_btn.configure(text=f" {new_text}", image=new_img)
        self.send_action_callback("PLAY_PAUSE")

    def on_mic_toggle(self):
        is_muted = self.mic_var.get()
        new_text = lang_mgr.tr('mic_muted', 'Muted') if is_muted else lang_mgr.tr('mic_unmuted', 'Unmuted')
        new_img = self.icon_mic_muted if is_muted else self.icon_mic_unmuted
        self.mic_btn.configure(text=f" {new_text}", image=new_img)
        self.send_action_callback("MIC_MUTE")


class AppLauncherWidget(ttk.LabelFrame):
    """Component: Dynamic Custom Apps with Pagination."""
    def __init__(self, master, send_launch_callback, **kwargs):
        super().__init__(master, text=f" {lang_mgr.tr('custom_apps', 'Custom Apps')} ", padding=(12, 8), **kwargs)
        self.send_launch_callback = send_launch_callback

        self.apps_data = []
        self.current_page = 0
        self.current_layout = "Small Grid"
        self.image_cache = {}

        self._create_header_bar()

        self.grid_container = ttk.Frame(self)
        self.grid_container.pack(fill="both", expand=True, pady=5)

        lang_mgr.subscribe(self.retranslate_ui)

    def _create_header_bar(self):
        nav_bar = ttk.Frame(self)
        nav_bar.pack(fill="x", pady=(0, 6))

        self.layout_label = ttk.Label(nav_bar, text=f"{lang_mgr.tr('layout', 'Layout:')} ", font=("Segoe UI", 9, "bold"))
        self.layout_label.pack(side="left", padx=(0, 4))

        self.view_combo = ttk.Combobox(
            nav_bar,
            values=[
                lang_mgr.tr("small_grid", "Small Grid"),
                lang_mgr.tr("large_grid", "Large Grid"),
                lang_mgr.tr("list_view", "List View")
            ],
            state="readonly",
            width=14
        )
        self.view_combo.set(lang_mgr.tr("small_grid", "Small Grid"))
        self.view_combo.bind("<<ComboboxSelected>>", self.on_view_changed)
        self.view_combo.pack(side="left", padx=(0, 10))

        self.icon_prev = icon_mgr.get_icon("prev_page", subsample_factor=12)
        self.icon_next = icon_mgr.get_icon("next_page", subsample_factor=12)

        self.next_btn = ttk.Button(
            nav_bar, image=self.icon_next, compound="left", command=self.next_page, width=4
        )
        self.next_btn.pack(side="right", padx=2)

        self.page_label = ttk.Label(nav_bar, text="Page 1/1", font=("Segoe UI", 9, "bold"))
        self.page_label.pack(side="right", padx=8)

        self.prev_btn = ttk.Button(
            nav_bar, image=self.icon_prev, compound="left", command=self.prev_page, width=4
        )
        self.prev_btn.pack(side="right", padx=2)

    def retranslate_ui(self):
        self.configure(text=f" {lang_mgr.tr('custom_apps', 'Custom Apps')} ")
        self.layout_label.configure(text=f"{lang_mgr.tr('layout', 'Layout:')} ")

        sg = lang_mgr.tr("small_grid", "Small Grid")
        lg = lang_mgr.tr("large_grid", "Large Grid")
        lv = lang_mgr.tr("list_view", "List View")
        self.view_combo.configure(values=[sg, lg, lv])

        if self.current_layout == "Small Grid":
            self.view_combo.set(sg)
        elif self.current_layout == "Large Grid":
            self.view_combo.set(lg)
        else:
            self.view_combo.set(lv)

        self.render_apps()

    def on_view_changed(self, event=None):
        val = self.view_combo.get()
        if val in [lang_mgr.tr("small_grid", "Small Grid"), "Small Grid"]:
            self.current_layout = "Small Grid"
        elif val in [lang_mgr.tr("large_grid", "Large Grid"), "Large Grid"]:
            self.current_layout = "Large Grid"
        else:
            self.current_layout = "List View"

        self.current_page = 0
        self.render_apps()

    def set_apps(self, apps: list):
        self.apps_data = apps
        self.current_page = 0
        self.render_apps()

    def _get_items_per_page(self):
        if self.current_layout == "Small Grid":
            return 8
        elif self.current_layout == "Large Grid":
            return 4
        else:
            return 5

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.render_apps()

    def next_page(self):
        items_per_page = self._get_items_per_page()
        max_pages = max(1, (len(self.apps_data) + items_per_page - 1) // items_per_page)
        if self.current_page < max_pages - 1:
            self.current_page += 1
            self.render_apps()

    def render_apps(self):
        for widget in self.grid_container.winfo_children():
            widget.destroy()
        self.image_cache.clear()

        if not self.apps_data:
            no_app_lbl = ttk.Label(
                self.grid_container,
                text=lang_mgr.tr("no_apps", "No custom apps configured in this profile."),
                foreground="#888"
            )
            no_app_lbl.pack(pady=30)
            self.page_label.configure(text=f"{lang_mgr.tr('page', 'Page')} 0/0")
            return

        items_per_page = self._get_items_per_page()
        total_items = len(self.apps_data)
        max_pages = max(1, (total_items + items_per_page - 1) // items_per_page)

        self.current_page = min(self.current_page, max_pages - 1)
        self.page_label.configure(text=f"{lang_mgr.tr('page', 'Page')} {self.current_page + 1}/{max_pages}")

        start_idx = self.current_page * items_per_page
        end_idx = min(start_idx + items_per_page, total_items)
        page_apps = self.apps_data[start_idx:end_idx]

        if self.current_layout == "Small Grid":
            self._render_grid(page_apps, cols=4, padding=8, ipady=14)
        elif self.current_layout == "Large Grid":
            self._render_grid(page_apps, cols=2, padding=16, ipady=28)
        else:
            self._render_list(page_apps, ipady=12)

    def _render_grid(self, apps_page: list, cols: int, padding: int, ipady: int):
        for c in range(cols):
            self.grid_container.columnconfigure(c, weight=1)

        rows = (len(apps_page) + cols - 1) // cols
        for r in range(rows):
            self.grid_container.rowconfigure(r, weight=1)

        for idx, app in enumerate(apps_page):
            row = idx // cols
            col = idx % cols

            app_id = app.get("id")
            icon_b64 = app.get("icon_b64")

            photo_img = None
            if icon_b64:
                photo_img = b64_to_photo_image(icon_b64)
                if photo_img:
                    self.image_cache[app_id] = photo_img

            btn = ttk.Button(
                self.grid_container,
                text=app['label'],
                image=photo_img if photo_img else "",
                compound="top" if photo_img else "none",
                command=lambda aid=app_id: self.send_launch_callback(aid)
            )

            if photo_img:
                btn.image = photo_img

            btn.grid(row=row, column=col, padx=padding, pady=padding, ipady=ipady, ipadx=10, sticky="nsew")

    def _render_list(self, apps_page: list, ipady: int):
        for app in apps_page:
            app_id = app.get("id")
            icon_b64 = app.get("icon_b64")

            photo_img = None
            if icon_b64:
                photo_img = b64_to_photo_image(icon_b64)
                if photo_img:
                    self.image_cache[app_id] = photo_img

            btn = ttk.Button(
                self.grid_container,
                text=f"   {app['label']}   ({app['path']})",
                image=photo_img if photo_img else "",
                compound="left" if photo_img else "none",
                command=lambda aid=app_id: self.send_launch_callback(aid)
            )

            if photo_img:
                btn.image = photo_img

            btn.pack(fill="x", padx=6, pady=6, ipady=ipady)


# ---------------- MAIN TABLET APPLICATION ----------------

class TabletStreamDeckApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("RTDeck Client (ARM32)")
        self.geometry("950x620")
        self.minsize(900, 480)

        # Apply Azure Theme from saved preferences
        apply_azure_theme(self)

        # Set RTDeck.ico window icon
        ico_path = resource_path("RTDeck.ico")
        if os.path.exists(ico_path):
            try:
                self.iconbitmap(ico_path)
            except Exception as e:
                print(f"[Window Icon Error] {e}")

        # Apply Native PyWinStyles dark title bar if available
        if PYWINSTYLES_AVAILABLE:
            try:
                pywinstyles.apply_style(self, client_prefs.get("theme", "dark"))
            except Exception as e:
                print(f"[pywinstyles warning] {e}")

        # Restore saved Fullscreen mode
        is_fs = bool(client_prefs.get("fullscreen", False))
        self.fullscreen_var = tk.BooleanVar(value=is_fs)
        if is_fs:
            self.attributes("-fullscreen", True)
            self.bind("<Escape>", lambda e: self._exit_fullscreen())

        # Target connection settings
        self.target_ip = client_prefs.get("last_ip", "127.0.0.1")
        self.target_port = client_prefs.get("last_port", "65432")

        # Network Manager
        self.net_client = TwoWayNetworkClient(
            message_callback=self.on_json_received,
            status_callback=self.update_status_ui
        )

        # Main Container Frame
        self.main_frame = ttk.Frame(self, padding=12)
        self.main_frame.pack(fill="both", expand=True)

        self._create_header_frame()
        self._create_top_dashboard()
        self._create_macro_grid()
        self._create_app_launcher_widget()

        lang_mgr.subscribe(self.retranslate_ui)

    def _create_header_frame(self):
        header_frame = ttk.Frame(self.main_frame)
        header_frame.pack(fill="x", pady=(0, 8))

        # BRAND LOGO ICON + TITLE ([Logo] RTDeck)
        brand_frame = ttk.Frame(header_frame)
        brand_frame.pack(side="left", padx=(0, 10))

        ico_path = resource_path("RTDeck.ico")
        self.brand_icon_img = None
        if os.path.exists(ico_path):
            try:
                full_ico = tk.PhotoImage(file=ico_path)
                self.brand_icon_img = full_ico.subsample(2, 2)
            except Exception:
                pass

        if self.brand_icon_img:
            logo_lbl = ttk.Label(brand_frame, image=self.brand_icon_img)
            logo_lbl.pack(side="left", padx=(0, 4))

        self.title_lbl = ttk.Label(brand_frame, text="RTDeck", font=("Segoe UI", 13, "bold"))
        self.title_lbl.pack(side="left")

        # ICON-ONLY About Button (info.png)
        self.icon_about = icon_mgr.get_icon("info", subsample_factor=10)
        self.about_btn = ttk.Button(
            header_frame,
            image=self.icon_about,
            width=3,
            command=self.open_about_modal
        )
        self.about_btn.pack(side="left", padx=2)

        # ICON-ONLY Settings Button (settings.png)
        self.icon_settings = icon_mgr.get_icon("settings", subsample_factor=10)
        self.settings_btn = ttk.Button(
            header_frame,
            image=self.icon_settings,
            width=3,
            command=self.open_settings_modal
        )
        self.settings_btn.pack(side="left", padx=2)

        # NETWORK CONNECTION MODAL BUTTON
        self.network_btn = ttk.Button(
            header_frame,
            text=lang_mgr.tr("connect", "Connect"),
            width=10,
            command=self.open_network_modal
        )
        self.network_btn.pack(side="left", padx=(10, 10))

        # DIRECT Profile Selector Combobox (No "Profile:" label text!)
        self.profile_combo = ttk.Combobox(
            header_frame,
            values=["Profile 1", "Profile 2", "Profile 3", "Profile 4", "Profile 5"],
            state="readonly",
            width=11
        )
        self.profile_combo.set("Profile 1")
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_client_profile_selected)
        self.profile_combo.pack(side="left", padx=4)

        # Connection Status Indicator on far right
        self.status_label = ttk.Label(header_frame, text=lang_mgr.tr("disconnected", "Disconnected"), font=("Segoe UI", 9, "bold"), foreground="#e74c3c")
        self.status_label.pack(side="right")

    def _create_top_dashboard(self):
        dashboard = ttk.Frame(self.main_frame)
        dashboard.pack(fill="x", pady=4)

        self.telemetry_widget = TelemetryWidget(dashboard, width=280)
        self.telemetry_widget.pack(side="left", fill="both", padx=(0, 4))

        self.media_widget = MediaWidget(dashboard)
        self.media_widget.pack(side="right", fill="both", expand=True, padx=(4, 0))

    def _create_macro_grid(self):
        self.macro_widget = MacroGridWidget(self.main_frame, send_action_callback=self.send_media_action)
        self.macro_widget.pack(fill="x", pady=4)

    def _create_app_launcher_widget(self):
        self.app_launcher_widget = AppLauncherWidget(self.main_frame, send_launch_callback=self.send_launch_action)
        self.app_launcher_widget.pack(fill="both", expand=True, pady=(4, 0))

    def retranslate_ui(self):
        self.title(lang_mgr.tr("title", "RTDeck Client (ARM32)"))
        self.network_btn.configure(text=lang_mgr.tr("connect", "Connect"))

    def open_network_modal(self):
        modal = tk.Toplevel(self)
        modal.title(lang_mgr.tr("network", "Network Connection"))
        modal.geometry("420x260")
        modal.resizable(False, False)

        apply_azure_theme(modal)
        ico_path = resource_path("RTDeck.ico")
        if os.path.exists(ico_path):
            try:
                modal.iconbitmap(ico_path)
            except Exception:
                pass

        modal.transient(self)
        modal.grab_set()

        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 210
        y = self.winfo_y() + (self.winfo_height() // 2) - 130
        modal.geometry(f"420x260+{x}+{y}")

        container = ttk.Frame(modal, padding=20)
        container.pack(fill="both", expand=True)

        title = ttk.Label(container, text=lang_mgr.tr("network", "Network Connection"), font=("Segoe UI", 15, "bold"))
        title.pack(anchor="w", pady=(0, 12))

        # IP Row
        ip_row = ttk.Frame(container)
        ip_row.pack(fill="x", pady=6)
        ttk.Label(ip_row, text=lang_mgr.tr("pc_ip", "PC IP:"), font=("Segoe UI", 10, "bold"), width=10).pack(side="left")
        entry_ip = ttk.Entry(ip_row)
        entry_ip.insert(0, self.target_ip)
        entry_ip.pack(side="right", fill="x", expand=True)

        # Port Row
        port_row = ttk.Frame(container)
        port_row.pack(fill="x", pady=6)
        ttk.Label(port_row, text=lang_mgr.tr("port", "Port:"), font=("Segoe UI", 10, "bold"), width=10).pack(side="left")
        entry_port = ttk.Entry(port_row)
        entry_port.insert(0, self.target_port)
        entry_port.pack(side="right", fill="x", expand=True)

        # Action Buttons Row
        btn_row = ttk.Frame(container)
        btn_row.pack(fill="x", pady=(15, 0))

        def do_connect():
            self.target_ip = entry_ip.get().strip()
            self.target_port = entry_port.get().strip()
            self.net_client.connect(self.target_ip, self.target_port)
            modal.destroy()

        def do_disconnect():
            self.net_client.disconnect()
            modal.destroy()

        btn_conn = ttk.Button(btn_row, text=lang_mgr.tr("connect", "Connect"), style="Accent.TButton", command=do_connect)
        btn_conn.pack(side="left", padx=(0, 5))

        btn_disc = ttk.Button(btn_row, text=lang_mgr.tr("disconnect", "Disconnect"), command=do_disconnect)
        btn_disc.pack(side="left", padx=5)

        btn_close = ttk.Button(btn_row, text=lang_mgr.tr("close", "Close"), command=modal.destroy)
        btn_close.pack(side="right")

    def toggle_fullscreen(self):
        is_full = self.fullscreen_var.get()
        client_prefs.set("fullscreen", is_full)
        self.attributes("-fullscreen", is_full)
        if is_full:
            self.bind("<Escape>", lambda e: self._exit_fullscreen())
        else:
            self.unbind("<Escape>")

    def _exit_fullscreen(self):
        self.fullscreen_var.set(False)
        client_prefs.set("fullscreen", False)
        self.attributes("-fullscreen", False)

    def open_settings_modal(self):
        modal = tk.Toplevel(self)
        modal.title(lang_mgr.tr("settings", "Settings"))
        modal.geometry("440x280")
        modal.resizable(False, False)

        apply_azure_theme(modal)
        ico_path = resource_path("RTDeck.ico")
        if os.path.exists(ico_path):
            try:
                modal.iconbitmap(ico_path)
            except Exception:
                pass

        modal.transient(self)
        modal.grab_set()

        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 220
        y = self.winfo_y() + (self.winfo_height() // 2) - 140
        modal.geometry(f"440x280+{x}+{y}")

        container = ttk.Frame(modal, padding=20)
        container.pack(fill="both", expand=True)

        title = ttk.Label(container, text=lang_mgr.tr("settings", "Settings"), font=("Segoe UI", 15, "bold"))
        title.pack(anchor="w", pady=(0, 15))

        # 1. Theme COMBOBOX Selector (Dark Mode / Light Mode)
        theme_frame = ttk.Frame(container)
        theme_frame.pack(fill="x", pady=8)
        ttk.Label(theme_frame, text=lang_mgr.tr("theme", "Azure Theme:"), font=("Segoe UI", 10, "bold")).pack(side="left")
        
        dark_str = lang_mgr.tr("dark_mode", "Dark Mode")
        light_str = lang_mgr.tr("light_mode", "Light Mode")

        combo_theme = ttk.Combobox(theme_frame, values=[dark_str, light_str], state="readonly", width=12)
        
        current_theme_mode = client_prefs.get("theme", "dark")
        combo_theme.set(dark_str if current_theme_mode == "dark" else light_str)
        combo_theme.pack(side="right")

        def on_theme_select(event=None):
            sel_t = combo_theme.get()
            new_mode = "dark" if sel_t in [dark_str, "Dark Mode"] else "light"
            client_prefs.set("theme", new_mode)
            try:
                self.tk.call("set_theme", new_mode)
            except Exception as e:
                print(f"[Theme Select Error] {e}")

        combo_theme.bind("<<ComboboxSelected>>", on_theme_select)

        # 2. Fullscreen Toggle Checkbox
        fs_frame = ttk.Frame(container)
        fs_frame.pack(fill="x", pady=8)
        chk_fs = ttk.Checkbutton(
            fs_frame,
            text=lang_mgr.tr("fullscreen", "Fullscreen Mode"),
            variable=self.fullscreen_var,
            command=self.toggle_fullscreen
        )
        chk_fs.pack(side="left")

        # 3. i18n Language Dropdown
        lang_frame = ttk.Frame(container)
        lang_frame.pack(fill="x", pady=8)
        ttk.Label(lang_frame, text=lang_mgr.tr("language", "Language:"), font=("Segoe UI", 10, "bold")).pack(side="left")
        
        lang_combo = ttk.Combobox(lang_frame, values=["en", "es"], state="readonly", width=12)
        lang_combo.set(lang_mgr.active_lang)
        lang_combo.pack(side="right")

        def on_lang_select(event=None):
            selected_l = lang_combo.get()
            lang_mgr.set_language(selected_l)
            modal.title(lang_mgr.tr("settings", "Settings"))
            title.configure(text=lang_mgr.tr("settings", "Settings"))
            chk_fs.configure(text=lang_mgr.tr("fullscreen", "Fullscreen Mode"))

        lang_combo.bind("<<ComboboxSelected>>", on_lang_select)

        # Close Button
        close_btn = ttk.Button(container, text=lang_mgr.tr("close", "Close"), style="Accent.TButton", command=modal.destroy, width=10)
        close_btn.pack(anchor="e", pady=(15, 0))

    def open_about_modal(self):
        modal = tk.Toplevel(self)
        modal.title(lang_mgr.tr("about", "About"))
        modal.geometry("480x340")
        modal.resizable(False, False)

        apply_azure_theme(modal)
        ico_path = resource_path("RTDeck.ico")
        if os.path.exists(ico_path):
            try:
                modal.iconbitmap(ico_path)
            except Exception:
                pass

        modal.transient(self)
        modal.grab_set()

        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 240
        y = self.winfo_y() + (self.winfo_height() // 2) - 170
        modal.geometry(f"480x340+{x}+{y}")

        container = ttk.Frame(modal, padding=20)
        container.pack(fill="both", expand=True)

        title = ttk.Label(container, text=lang_mgr.tr("title", "RTDeck Client (ARM32)"), font=("Segoe UI", 15, "bold"))
        title.pack(anchor="w", pady=(0, 8))

        desc = ttk.Label(
            container,
            text=lang_mgr.tr("about_desc", "A lightweight, Python-based Stream Deck client for Windows RT."),
            font=("Segoe UI", 10),
            wraplength=430
        )
        desc.pack(anchor="w", pady=(0, 14))

        author_lbl = ttk.Label(container, text=lang_mgr.tr("developed_by", "Developed by Julián Caceres"), font=("Segoe UI", 10, "bold"))
        author_lbl.pack(anchor="w", pady=(0, 2))

        author_link = ttk.Label(
            container,
            text="https://github.com/juliancz-a",
            font=("Segoe UI", 9, "underline"),
            foreground="#3498db",
            cursor="hand2"
        )
        author_link.pack(anchor="w", pady=(0, 12))
        author_link.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/juliancz-a"))

        theme_lbl = ttk.Label(container, text=lang_mgr.tr("theme_credit", "UI Theme: Azure-ttk-theme by rdbende"), font=("Segoe UI", 10, "bold"))
        theme_lbl.pack(anchor="w", pady=(0, 2))

        theme_link = ttk.Label(
            container,
            text="https://github.com/rdbende/Azure-ttk-theme",
            font=("Segoe UI", 9, "underline"),
            foreground="#3498db",
            cursor="hand2"
        )
        theme_link.pack(anchor="w", pady=(0, 14))
        theme_link.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/rdbende/Azure-ttk-theme"))

        license_lbl = ttk.Label(container, text=lang_mgr.tr("license", "License: MIT License"), font=("Segoe UI", 9, "italic"), foreground="#aaaaaa")
        license_lbl.pack(anchor="w", pady=(0, 15))

        close_btn = ttk.Button(container, text=lang_mgr.tr("close", "Close"), style="Accent.TButton", command=modal.destroy, width=10)
        close_btn.pack(anchor="e")

    # ---------------- Network Handlers ----------------
    def on_client_profile_selected(self, event=None):
        selected_profile = self.profile_combo.get()
        payload = {
            "type": "action",
            "action": "switch_profile",
            "profile": selected_profile
        }
        self.net_client.send_json(payload)

    def send_media_action(self, key_code: str):
        payload = {
            "type": "action",
            "action": "media_key",
            "key": key_code
        }
        self.net_client.send_json(payload)

    def send_launch_action(self, app_id: str):
        payload = {
            "type": "action",
            "action": "launch",
            "id": app_id
        }
        self.net_client.send_json(payload)

    def on_json_received(self, msg: dict):
        msg_type = msg.get("type")

        if msg_type == "init":
            active_p = msg.get("active_profile", "Profile 1")
            profiles = msg.get("profiles", ["Profile 1", "Profile 2", "Profile 3", "Profile 4", "Profile 5"])
            apps = msg.get("apps", [])
            media = msg.get("media", {})
            telemetry = msg.get("telemetry", {})

            def _update_init():
                self.profile_combo.configure(values=profiles)
                self.profile_combo.set(active_p)
                self.app_launcher_widget.set_apps(apps)
                self.media_widget.update_media(
                    media.get("title"), media.get("artist"), media.get("progress", 0.0)
                )
                self.telemetry_widget.update_telemetry(
                    telemetry.get("cpu", 0.0), telemetry.get("ram", 0.0)
                )

            self.after(0, _update_init)

        elif msg_type == "config_update":
            active_p = msg.get("active_profile", "Profile 1")
            profiles = msg.get("profiles", ["Profile 1", "Profile 2", "Profile 3", "Profile 4", "Profile 5"])
            apps = msg.get("apps", [])

            def _update_cfg():
                self.profile_combo.configure(values=profiles)
                self.profile_combo.set(active_p)
                self.app_launcher_widget.set_apps(apps)

            self.after(0, _update_cfg)

        elif msg_type == "heartbeat":
            media = msg.get("media", {})
            telemetry = msg.get("telemetry", {})
            self.after(0, lambda: self.media_widget.update_media(
                media.get("title"), media.get("artist"), media.get("progress", 0.0)
            ))
            self.after(0, lambda: self.telemetry_widget.update_telemetry(
                telemetry.get("cpu", 0.0), telemetry.get("ram", 0.0)
            ))

    def update_status_ui(self, message: str, color: str):
        self.after(0, lambda: self.status_label.configure(text=message, foreground=color))

    def on_closing(self):
        self.net_client.disconnect()
        self.net_client.running = False
        self.destroy()


if __name__ == "__main__":
    app = TabletStreamDeckApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
