import socket
import threading
import json
import os
import sys
import time
import datetime
import subprocess
import asyncio
import base64
import io
import webbrowser
from PIL import Image
import pyautogui
import customtkinter as ctk
from customtkinter import filedialog

# Configure CustomTkinter appearance
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# Disable PyAutoGUI failsafe
pyautogui.FAILSAFE = False

# PyCAW setup for Microphone Mute
try:
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from comtypes import CLSCTX_ALL, CoInitialize, CoUninitialize
    PYCAW_AVAILABLE = True
except ImportError:
    PYCAW_AVAILABLE = False

# WINSDK SMTC setup for Windows 11 Media Control
try:
    from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager
    WINSDK_AVAILABLE = True
except ImportError:
    WINSDK_AVAILABLE = False

# PyWin32 setup for native icon extraction & window management
try:
    import win32gui
    import win32ui
    import win32con
    import win32process
    import win32com.client
    PYWIN32_AVAILABLE = True
except ImportError:
    PYWIN32_AVAILABLE = False

# PSUtil setup for PC Telemetry (CPU & RAM) & process enumeration
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


def get_app_dir() -> str:
    """
    Returns the directory path containing the executable or script.
    Crucial for PyInstaller --onefile: ensures config/prefs files are saved
    in the executable's directory rather than the temporary sys._MEIPASS folder.
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_dir() -> str:
    """Returns directory containing bundled data files (sys._MEIPASS or source dir)."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return get_app_dir()


CONFIG_FILE = os.path.join(get_app_dir(), "config.json")
SERVER_PREFS_FILE = os.path.join(get_app_dir(), "server_prefs.json")
PROFILES_LIST = ["Profile 1", "Profile 2", "Profile 3", "Profile 4", "Profile 5"]

DEFAULT_APPS = [
    {"id": "app_calc", "label": "Calculator", "path": "calc.exe", "icon_b64": ""},
    {"id": "app_notepad", "label": "Notepad", "path": "notepad.exe", "icon_b64": ""}
]


def get_local_ips():
    """Retrieve all local IPv4 addresses for user display."""
    ip_list = []
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127."):
                ip_list.append(ip)
    except Exception:
        pass
    return ip_list if ip_list else ["127.0.0.1"]


def resolve_shortcut_target(path: str) -> str:
    """
    If path is a .lnk shortcut file, resolves and returns the target .exe path.
    Otherwise returns original path.
    """
    if not path or not os.path.exists(path):
        return path

    if path.lower().endswith(".lnk"):
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(path)
            target = shortcut.TargetPath
            if target and os.path.exists(target):
                return target
        except Exception as e:
            print(f"[Shortcut Resolve Warning] {e}")
    return path


def focus_or_launch_app(app_path: str) -> bool:
    """
    Checks if application is already running with an open window.
    If running: restores window (if minimized) and brings to foreground via pywin32.
    If not running or no window found: launches app / .lnk shortcut natively.
    """
    if not app_path:
        return False

    target_path = resolve_shortcut_target(app_path)
    target_filename = os.path.basename(target_path).lower() if target_path else ""

    found_hwnd = None

    if PYWIN32_AVAILABLE and target_filename:
        target_pids = set()
        if PSUTIL_AVAILABLE:
            try:
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        pname = proc.info.get('name', '')
                        if pname and pname.lower() == target_filename:
                            target_pids.add(proc.info['pid'])
                    except Exception:
                        pass
            except Exception:
                pass

        def enum_windows_callback(hwnd, extra):
            nonlocal found_hwnd
            if found_hwnd:
                return

            if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title and len(title.strip()) > 0:
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        if target_pids and pid in target_pids:
                            found_hwnd = hwnd
                    except Exception:
                        pass

        try:
            win32gui.EnumWindows(enum_windows_callback, None)
        except Exception as e:
            print(f"[EnumWindows Error] {e}")

    # If window found, unminimize & bring to foreground
    if found_hwnd and PYWIN32_AVAILABLE:
        try:
            if win32gui.IsIconic(found_hwnd):
                win32gui.ShowWindow(found_hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(found_hwnd, win32con.SW_SHOW)

            try:
                win32gui.SetForegroundWindow(found_hwnd)
            except Exception:
                pyautogui.press('alt')
                win32gui.SetForegroundWindow(found_hwnd)

            print(f"[Focus App] Brought window (hwnd={found_hwnd}) to front for '{target_filename}'")
            return True
        except Exception as e:
            print(f"[Focus App Warning] Could not set foreground: {e}")

    # If not running or no window found, launch application / shortcut
    try:
        if os.path.exists(app_path):
            os.startfile(app_path)
            print(f"[Launch App] Started '{app_path}' via os.startfile")
        else:
            subprocess.Popen(app_path, shell=True)
            print(f"[Launch App] Started '{app_path}' via subprocess.Popen")
        return True
    except Exception as e:
        print(f"[Launch App Error] Failed to launch '{app_path}': {e}")
        return False


# ---------------- SERVER PREFERENCES & i18n MANAGER ----------------

class ServerPreferencesManager:
    """Manages persistent server settings (language, port, etc.) saved in executable directory."""
    def __init__(self, filepath=SERVER_PREFS_FILE):
        self.filepath = filepath
        self.prefs = self.load()

    def load(self) -> dict:
        defaults = {
            "language": "en",
            "port": 65432
        }
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    defaults.update(data)
            except Exception as e:
                print(f"[Server Prefs Load Error] {e}")
        return defaults

    def save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.prefs, f, indent=2)
        except Exception as e:
            print(f"[Server Prefs Save Error] {e}")

    def get(self, key: str, default=None):
        return self.prefs.get(key, default)

    def set(self, key: str, value):
        self.prefs[key] = value
        self.save()


server_prefs = ServerPreferencesManager()


class ServerLanguageManager:
    """Manages i18n for server_x64.py via server_lang.json."""
    def __init__(self, default_lang="en"):
        self.active_lang = default_lang
        self.translations = self._load_lang_file()
        self.subscribers = []

    def _load_lang_file(self):
        lang_path = os.path.join(get_resource_dir(), "server_lang.json")
        if os.path.exists(lang_path):
            try:
                with open(lang_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[Server Lang Load Error] {e}")
        return {}

    def set_language(self, lang_code: str):
        if lang_code in self.translations:
            self.active_lang = lang_code
            server_prefs.set("language", lang_code)
            self.notify_subscribers()

    def tr(self, key: str, fallback: str = "", **kwargs) -> str:
        lang_dict = self.translations.get(self.active_lang, {})
        raw = lang_dict.get(key, self.translations.get("en", {}).get(key, fallback if fallback else key))
        if kwargs:
            try:
                return raw.format(**kwargs)
            except Exception:
                pass
        return raw

    def subscribe(self, callback):
        self.subscribers.append(callback)

    def notify_subscribers(self):
        for cb in self.subscribers:
            try:
                cb()
            except Exception as e:
                print(f"[Server Lang Subscriber Error] {e}")


server_lang_mgr = ServerLanguageManager(default_lang=server_prefs.get("language", "en"))


def extract_exe_icon_base64(file_path: str, icon_size: int = 64) -> str:
    """
    Extracts native icon from a Windows .exe file or .lnk shortcut, or loads a custom image file (.png/.jpg/.ico).
    Downsamples to crisp icon_size x icon_size PNG using Image.Resampling.LANCZOS.
    """
    if not file_path or not os.path.exists(file_path):
        return ""

    # Resolve shortcut target first if file is a .lnk
    resolved_path = resolve_shortcut_target(file_path)

    ext = os.path.splitext(resolved_path)[1].lower()
    if ext in [".png", ".jpg", ".jpeg", ".bmp"]:
        try:
            img = Image.open(resolved_path).convert("RGBA")
            img_resized = img.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            img_resized.save(buffer, format="PNG")
            return base64.b64encode(buffer.getvalue()).decode('utf-8')
        except Exception as e:
            print(f"[Direct Image Load Error] {e}")

    if not PYWIN32_AVAILABLE:
        return ""

    try:
        large, small = win32gui.ExtractIconEx(resolved_path, 0)
        hicon = None
        if large and len(large) > 0:
            hicon = large[0]
            if small:
                for h in small:
                    win32gui.DestroyIcon(h)
        elif small and len(small) > 0:
            hicon = small[0]

        if not hicon:
            return ""

        hdc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
        hbmp = win32ui.CreateBitmap()
        hbmp.CreateCompatibleBitmap(hdc, icon_size, icon_size)

        hdc_mem = hdc.CreateCompatibleDC()
        hdc_mem.SelectObject(hbmp)

        win32gui.DrawIconEx(
            hdc_mem.GetSafeHdc(), 0, 0, hicon,
            icon_size, icon_size, 0, None, win32con.DI_NORMAL
        )

        bmpinfo = hbmp.GetInfo()
        bmpstr = hbmp.GetBitmapBits(True)
        img = Image.frombuffer(
            'RGBA', (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
            bmpstr, 'raw', 'BGRA', 0, 1
        )

        win32gui.DestroyIcon(hicon)
        hdc_mem.DeleteDC()
        hdc.DeleteDC()
        win32gui.DeleteObject(hbmp.GetHandle())

        img_resized = img.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img_resized.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode('utf-8')

    except Exception as e:
        print(f"[Icon Extraction Error] {e}")
        return ""


def get_system_telemetry():
    """Reads CPU and RAM usage percentage via psutil."""
    if not PSUTIL_AVAILABLE:
        return {"cpu": 0.0, "ram": 0.0}
    try:
        return {
            "cpu": round(psutil.cpu_percent(interval=None), 1),
            "ram": round(psutil.virtual_memory().percent, 1)
        }
    except Exception:
        return {"cpu": 0.0, "ram": 0.0}


class ConfigManager:
    """Manages local config.json for 5 distinct profile slots stored in executable directory."""
    def __init__(self, filepath=CONFIG_FILE):
        self.filepath = filepath
        self.config = self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    cfg = json.load(f)

                if "profiles" not in cfg or not isinstance(cfg.get("profiles"), dict):
                    old_apps = cfg.get("apps", [])
                    cfg = {
                        "active_profile": "Profile 1",
                        "profiles": {
                            "Profile 1": old_apps,
                            "Profile 2": [],
                            "Profile 3": [],
                            "Profile 4": [],
                            "Profile 5": []
                        }
                    }

                for p in PROFILES_LIST:
                    if p not in cfg["profiles"]:
                        cfg["profiles"][p] = []

                if "active_profile" not in cfg or cfg["active_profile"] not in PROFILES_LIST:
                    cfg["active_profile"] = "Profile 1"

                for pname, apps in cfg["profiles"].items():
                    for app in apps:
                        if app.get("path") and os.path.exists(app["path"]):
                            if not app.get("icon_b64"):
                                app["icon_b64"] = extract_exe_icon_base64(app["path"], icon_size=64)

                return cfg
            except Exception as e:
                print(f"[CONFIG LOAD ERROR] {e}")

        return {
            "active_profile": "Profile 1",
            "profiles": {
                "Profile 1": json.loads(json.dumps(DEFAULT_APPS)),
                "Profile 2": [],
                "Profile 3": [],
                "Profile 4": [],
                "Profile 5": []
            }
        }

    def save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"[CONFIG SAVE ERROR] {e}")

    def get_active_profile_name(self) -> str:
        return self.config.get("active_profile", "Profile 1")

    def set_active_profile(self, profile_name: str) -> bool:
        if profile_name in PROFILES_LIST:
            self.config["active_profile"] = profile_name
            self.save()
            return True
        return False

    def get_active_apps(self) -> list:
        active_p = self.get_active_profile_name()
        return self.config.get("profiles", {}).get(active_p, [])

    def add_app(self, label: str, path: str, icon_file_path: str = ""):
        active_p = self.get_active_profile_name()
        app_id = f"app_{int(time.time() * 1000)}"
        target_icon_path = icon_file_path if icon_file_path and os.path.exists(icon_file_path) else path
        icon_b64 = extract_exe_icon_base64(target_icon_path, icon_size=64)
        new_app = {
            "id": app_id,
            "label": label,
            "path": path,
            "icon_b64": icon_b64
        }
        self.config["profiles"][active_p].append(new_app)
        self.save()
        return new_app

    def update_app(self, app_id: str, new_label: str, new_path: str, custom_icon_path: str = ""):
        active_p = self.get_active_profile_name()
        for app in self.config["profiles"][active_p]:
            if app["id"] == app_id:
                app["label"] = new_label
                app["path"] = new_path

                target_icon_path = custom_icon_path if custom_icon_path and os.path.exists(custom_icon_path) else new_path
                new_b64 = extract_exe_icon_base64(target_icon_path, icon_size=64)
                if new_b64:
                    app["icon_b64"] = new_b64

                self.save()
                return app
        return None

    def delete_app(self, app_id: str):
        active_p = self.get_active_profile_name()
        self.config["profiles"][active_p] = [
            a for a in self.config["profiles"][active_p] if a["id"] != app_id
        ]
        self.save()

    def get_app_by_id(self, app_id: str):
        active_p = self.get_active_profile_name()
        for app in self.config["profiles"][active_p]:
            if app["id"] == app_id:
                return app

        for p_apps in self.config["profiles"].values():
            for app in p_apps:
                if app["id"] == app_id:
                    return app
        return None


class PCServerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(server_lang_mgr.tr("server_title", "RTDeck Server (x64)"))
        self.geometry("980x660")
        self.minsize(820, 540)

        # Set RTDeckServer.ico window icon
        ico_path = os.path.join(get_resource_dir(), "RTDeckServer.ico")
        if os.path.exists(ico_path):
            try:
                self.iconbitmap(ico_path)
            except Exception as e:
                print(f"[Server Window Icon Error] {e}")

        # Config & State
        self.cfg_mgr = ConfigManager()
        self.host = "0.0.0.0"
        self.port = int(server_prefs.get("port", 65432))
        self.server_socket = None
        self.is_running = False

        # Active Clients
        self.clients = set()
        self.clients_lock = threading.Lock()

        # Current State Cache
        self.current_media = {
            "title": "No Media Playing",
            "artist": "Windows Media SMTC",
            "progress": 0.0,
            "is_playing": False
        }
        self.current_telemetry = {"cpu": 0.0, "ram": 0.0}

        # Build UI
        self._create_header_frame()
        self._create_main_layout()

        self.refresh_app_list_ui()

        # Subscribe to language changes
        server_lang_mgr.subscribe(self.retranslate_ui)

        # Start Background Heartbeat Thread
        self.heartbeat_running = True
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()

        # Start Server automatically
        self.start_server()

    def _create_header_frame(self):
        self.header_frame = ctk.CTkFrame(self, corner_radius=10)
        self.header_frame.pack(fill="x", padx=15, pady=(15, 5))

        self.title_label = ctk.CTkLabel(
            self.header_frame,
            text="RTDECK SERVER",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        self.title_label.pack(side="left", padx=15, pady=12)

        # About Modal Button
        self.about_btn = ctk.CTkButton(
            self.header_frame,
            text=server_lang_mgr.tr("about", "ABOUT"),
            width=24,
            height=28,
            fg_color="#34495e",
            hover_color="#415b76",
            command=self.open_about_modal
        )
        self.about_btn.pack(side="left", padx=4, pady=12)

        # Settings Modal Button (⚙️)
        self.settings_btn = ctk.CTkButton(
            self.header_frame,
            text="⚙️",
            width=32,
            height=28,
            fg_color="#34495e",
            hover_color="#415b76",
            command=self.open_settings_modal
        )
        self.settings_btn.pack(side="left", padx=4, pady=12)

        self.status_badge = ctk.CTkLabel(
            self.header_frame,
            text=server_lang_mgr.tr("stopped", "STOPPED"),
            fg_color="#e74c3c",
            text_color="white",
            corner_radius=6,
            font=ctk.CTkFont(size=12, weight="bold"),
            padx=10,
            pady=3
        )
        self.status_badge.pack(side="left", padx=8, pady=12)

        self.toggle_btn = ctk.CTkButton(
            self.header_frame,
            text=server_lang_mgr.tr("stop_server", "Stop Server"),
            fg_color="#c0392b",
            hover_color="#e74c3c",
            width=110,
            command=self.toggle_server
        )
        self.toggle_btn.pack(side="right", padx=15, pady=12)

        ips_str = ", ".join(get_local_ips())
        self.ip_info_label = ctk.CTkLabel(
            self.header_frame,
            text=server_lang_mgr.tr("ip_info", "IP: {ips} | Port: {port}", ips=ips_str, port=self.port),
            font=ctk.CTkFont(size=12),
            text_color="#a0a0a0"
        )
        self.ip_info_label.pack(side="right", padx=15, pady=12)

    def _create_main_layout(self):
        self.main_container = ctk.CTkFrame(self, fg_color="transparent")
        self.main_container.pack(fill="both", expand=True, padx=15, pady=5)

        # ---------------- LEFT: App Launcher & 5-Slot Profile Manager ----------------
        self.left_box = ctk.CTkFrame(self.main_container, width=380, corner_radius=10)
        self.left_box.pack(side="left", fill="both", padx=(0, 5), pady=5)
        self.left_box.pack_propagate(False)

        self.left_title = ctk.CTkLabel(
            self.left_box,
            text=server_lang_mgr.tr("app_manager", "App Launcher & Profile Manager"),
            font=ctk.CTkFont(size=15, weight="bold")
        )
        self.left_title.pack(padx=10, pady=(12, 4))

        # Profile Selector Bar
        profile_bar = ctk.CTkFrame(self.left_box, fg_color="#1f242d", corner_radius=8)
        profile_bar.pack(fill="x", padx=10, pady=5)

        self.p_label = ctk.CTkLabel(profile_bar, text=server_lang_mgr.tr("active_profile", "Active Profile:"), font=ctk.CTkFont(size=12, weight="bold"))
        self.p_label.pack(side="left", padx=10, pady=8)

        self.profile_combo = ctk.CTkOptionMenu(
            profile_bar,
            values=PROFILES_LIST,
            command=self.on_server_profile_changed,
            width=140
        )
        self.profile_combo.set(self.cfg_mgr.get_active_profile_name())
        self.profile_combo.pack(side="right", padx=10, pady=8)

        # Add Form
        self.form_frame = ctk.CTkFrame(self.left_box, fg_color="#2b2b2b", corner_radius=8)
        self.form_frame.pack(fill="x", padx=10, pady=5)

        self.app_name_entry = ctk.CTkEntry(self.form_frame, placeholder_text=server_lang_mgr.tr("app_label_placeholder", "App Label (e.g. Chrome)"))
        self.app_name_entry.pack(fill="x", padx=10, pady=(10, 5))

        path_row = ctk.CTkFrame(self.form_frame, fg_color="transparent")
        path_row.pack(fill="x", padx=10, pady=5)

        self.app_path_entry = ctk.CTkEntry(path_row, placeholder_text=server_lang_mgr.tr("exe_path_placeholder", ".exe Path / Command"))
        self.app_path_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.browse_btn = ctk.CTkButton(
            path_row,
            text=server_lang_mgr.tr("browse", "Browse"),
            width=65,
            fg_color="#34495e",
            hover_color="#415b76",
            command=self.browse_exe_path
        )
        self.browse_btn.pack(side="right")

        self.add_app_btn = ctk.CTkButton(
            self.form_frame,
            text=server_lang_mgr.tr("add_app", "+ Add App to Active Profile"),
            fg_color="#27ae60",
            hover_color="#2ecc71",
            command=self.add_custom_app
        )
        self.add_app_btn.pack(fill="x", padx=10, pady=(5, 10))

        # Apps List Header
        self.apps_list_hdr = ctk.CTkLabel(
            self.left_box,
            text=server_lang_mgr.tr("apps_in_profile", "Apps in {profile}", profile=self.cfg_mgr.get_active_profile_name()),
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self.apps_list_hdr.pack(anchor="w", padx=15, pady=(8, 2))

        self.apps_scroll = ctk.CTkScrollableFrame(self.left_box, corner_radius=8)
        self.apps_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # ---------------- RIGHT: Media, Telemetry & Console ----------------
        self.right_box = ctk.CTkFrame(self.main_container, corner_radius=10)
        self.right_box.pack(side="right", fill="both", expand=True, padx=(5, 0), pady=5)

        self.top_widgets = ctk.CTkFrame(self.right_box, fg_color="transparent")
        self.top_widgets.pack(fill="x", padx=10, pady=5)

        # Telemetry Card
        self.telemetry_box = ctk.CTkFrame(self.top_widgets, fg_color="#1a252f", corner_radius=8)
        self.telemetry_box.pack(side="left", fill="both", expand=True, padx=(0, 5))

        self.telem_hdr = ctk.CTkLabel(
            self.telemetry_box,
            text=server_lang_mgr.tr("pc_telemetry", "💻 PC TELEMETRY (PSUTIL)"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#e67e22"
        )
        self.telem_hdr.pack(anchor="w", padx=10, pady=(6, 2))

        self.cpu_label = ctk.CTkLabel(self.telemetry_box, text="CPU: 0%", font=ctk.CTkFont(size=12, weight="bold"))
        self.cpu_label.pack(anchor="w", padx=10, pady=(0, 1))

        self.ram_label = ctk.CTkLabel(self.telemetry_box, text="RAM: 0%", font=ctk.CTkFont(size=12, weight="bold"))
        self.ram_label.pack(anchor="w", padx=10, pady=(0, 6))

        # SMTC Media Card
        self.media_box = ctk.CTkFrame(self.top_widgets, fg_color="#1f242d", corner_radius=8)
        self.media_box.pack(side="right", fill="both", expand=True, padx=(5, 0))

        self.media_hdr = ctk.CTkLabel(
            self.media_box,
            text=server_lang_mgr.tr("media_monitor", "🎵 WIN11 SMTC MEDIA MONITOR"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#3498db"
        )
        self.media_hdr.pack(anchor="w", padx=10, pady=(6, 2))

        self.media_title_lbl = ctk.CTkLabel(self.media_box, text="No Media Playing", font=ctk.CTkFont(size=13, weight="bold"))
        self.media_title_lbl.pack(anchor="w", padx=10, pady=(0, 1))

        self.media_artist_lbl = ctk.CTkLabel(self.media_box, text="Windows SMTC", font=ctk.CTkFont(size=11), text_color="#aaa")
        self.media_artist_lbl.pack(anchor="w", padx=10, pady=(0, 6))

        # Activity Log Console
        log_hdr_frame = ctk.CTkFrame(self.right_box, fg_color="transparent")
        log_hdr_frame.pack(fill="x", padx=10, pady=(5, 2))

        self.log_title = ctk.CTkLabel(
            log_hdr_frame,
            text=server_lang_mgr.tr("json_console", "Live Two-Way JSON Console"),
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.log_title.pack(side="left", padx=5)

        self.clear_btn = ctk.CTkButton(
            log_hdr_frame,
            text=server_lang_mgr.tr("clear", "Clear"),
            width=60,
            height=24,
            fg_color="#34495e",
            hover_color="#415b76",
            command=self.clear_logs
        )
        self.clear_btn.pack(side="right", padx=5)

        self.log_box = ctk.CTkTextbox(
            self.right_box,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word",
            corner_radius=8
        )
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_box.configure(state="disabled")

    def retranslate_ui(self):
        self.title(server_lang_mgr.tr("server_title", "RTDeck Server (x64)"))
        state_str = server_lang_mgr.tr("running", "RUNNING") if self.is_running else server_lang_mgr.tr("stopped", "STOPPED")
        self.status_badge.configure(text=state_str)
        t_str = server_lang_mgr.tr("stop_server", "Stop Server") if self.is_running else server_lang_mgr.tr("start_server", "Start Server")
        self.toggle_btn.configure(text=t_str)

        ips_str = ", ".join(get_local_ips())
        self.ip_info_label.configure(text=server_lang_mgr.tr("ip_info", "IP: {ips} | Port: {port}", ips=ips_str, port=self.port))

        self.about_btn.configure(text=server_lang_mgr.tr("about", "ABOUT"))
        self.left_title.configure(text=server_lang_mgr.tr("app_manager", "App Launcher & Profile Manager"))
        self.p_label.configure(text=server_lang_mgr.tr("active_profile", "Active Profile:"))
        self.app_name_entry.configure(placeholder_text=server_lang_mgr.tr("app_label_placeholder", "App Label (e.g. Chrome)"))
        self.app_path_entry.configure(placeholder_text=server_lang_mgr.tr("exe_path_placeholder", ".exe Path / Command"))
        self.browse_btn.configure(text=server_lang_mgr.tr("browse", "Browse"))
        self.add_app_btn.configure(text=server_lang_mgr.tr("add_app", "+ Add App to Active Profile"))
        self.telem_hdr.configure(text=server_lang_mgr.tr("pc_telemetry", "💻 PC TELEMETRY (PSUTIL)"))
        self.media_hdr.configure(text=server_lang_mgr.tr("media_monitor", "🎵 WIN11 SMTC MEDIA MONITOR"))
        self.log_title.configure(text=server_lang_mgr.tr("json_console", "Live Two-Way JSON Console"))
        self.clear_btn.configure(text=server_lang_mgr.tr("clear", "Clear"))

        self.refresh_app_list_ui()

    def open_about_modal(self):
        modal = ctk.CTkToplevel(self)
        modal.title(server_lang_mgr.tr("about", "About RTDeck Server"))
        modal.geometry("450x300")
        modal.resizable(False, False)
        modal.attributes("-topmost", True)

        title_lbl = ctk.CTkLabel(modal, text="RTDeck Server (x64)", font=ctk.CTkFont(size=16, weight="bold"))
        title_lbl.pack(anchor="w", padx=20, pady=(15, 8))

        desc = ctk.CTkLabel(
            modal,
            text=server_lang_mgr.tr("server_about_desc", "A lightweight Python TCP Socket server for RTDeck Stream Deck system."),
            font=ctk.CTkFont(size=12),
            wraplength=410
        )
        desc.pack(anchor="w", padx=20, pady=(0, 14))

        author_lbl = ctk.CTkLabel(modal, text=server_lang_mgr.tr("developed_by", "Developed by Julián Caceres"), font=ctk.CTkFont(size=12, weight="bold"))
        author_lbl.pack(anchor="w", padx=20, pady=(0, 2))

        author_link = ctk.CTkLabel(
            modal,
            text="https://github.com/juliancz-a",
            font=ctk.CTkFont(size=11, underline=True),
            text_color="#3498db",
            cursor="hand2"
        )
        author_link.pack(anchor="w", padx=20, pady=(0, 14))
        author_link.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/juliancz-a"))

        license_lbl = ctk.CTkLabel(modal, text=server_lang_mgr.tr("license", "License: MIT License"), font=ctk.CTkFont(size=11), text_color="#aaa")
        license_lbl.pack(anchor="w", padx=20, pady=(0, 15))

        btn_close = ctk.CTkButton(modal, text=server_lang_mgr.tr("close", "Close"), width=90, command=modal.destroy)
        btn_close.pack(anchor="e", padx=20, pady=10)

    def open_settings_modal(self):
        modal = ctk.CTkToplevel(self)
        modal.title(server_lang_mgr.tr("settings", "Server Settings"))
        modal.geometry("380x200")
        modal.resizable(False, False)
        modal.attributes("-topmost", True)

        title_lbl = ctk.CTkLabel(modal, text=server_lang_mgr.tr("settings", "Server Settings"), font=ctk.CTkFont(size=16, weight="bold"))
        title_lbl.pack(anchor="w", padx=20, pady=(15, 12))

        lang_row = ctk.CTkFrame(modal, fg_color="transparent")
        lang_row.pack(fill="x", padx=20, pady=10)

        lbl_l = ctk.CTkLabel(lang_row, text=server_lang_mgr.tr("language", "Language:"), font=ctk.CTkFont(size=13, weight="bold"))
        lbl_l.pack(side="left")

        combo_lang = ctk.CTkOptionMenu(lang_row, values=["en", "es"], width=120)
        combo_lang.set(server_lang_mgr.active_lang)
        combo_lang.pack(side="right")

        def save_settings():
            sel_l = combo_lang.get()
            server_lang_mgr.set_language(sel_l)
            modal.destroy()

        btn_save = ctk.CTkButton(modal, text=server_lang_mgr.tr("save", "Save Changes"), fg_color="#27ae60", hover_color="#2ecc71", command=save_settings)
        btn_save.pack(anchor="e", padx=20, pady=20)

    # ---------------- App & Profile Manager Logic ----------------
    def on_server_profile_changed(self, selected_profile: str):
        if self.cfg_mgr.set_active_profile(selected_profile):
            self.refresh_app_list_ui()
            self.broadcast_config_update()
            self.log_message(f"Active Profile switched to '{selected_profile}' on Server GUI", "CONFIG")

    def browse_exe_path(self):
        filepath = filedialog.askopenfilename(
            title="Select Executable or Shortcut",
            filetypes=[("Executables & Shortcuts", "*.exe;*.lnk"), ("All Files", "*.*")]
        )
        if filepath:
            self.app_path_entry.delete(0, "end")
            self.app_path_entry.insert(0, filepath)

    def add_custom_app(self):
        label = self.app_name_entry.get().strip()
        path = self.app_path_entry.get().strip()
        if not label or not path:
            self.log_message("Please provide both Label and Path for the app.", "WARNING")
            return

        app_item = self.cfg_mgr.add_app(label, path)
        has_icon = "Yes (Base64)" if app_item.get("icon_b64") else "No"

        self.app_name_entry.delete(0, "end")
        self.app_path_entry.delete(0, "end")
        self.refresh_app_list_ui()
        self.broadcast_config_update()
        active_p = self.cfg_mgr.get_active_profile_name()
        self.log_message(f"Added app to '{active_p}': '{label}' (Icon: {has_icon})", "CONFIG")

    def delete_custom_app(self, app_id: str):
        active_p = self.cfg_mgr.get_active_profile_name()
        self.cfg_mgr.delete_app(app_id)
        self.refresh_app_list_ui()
        self.broadcast_config_update()
        self.log_message(f"Deleted app ID '{app_id}' from '{active_p}'", "CONFIG")

    def open_edit_app_modal(self, app_id: str):
        app = self.cfg_mgr.get_app_by_id(app_id)
        if not app:
            return

        modal = ctk.CTkToplevel(self)
        modal.title(f"Edit App: {app['label']}")
        modal.geometry("450x300")
        modal.resizable(False, False)
        modal.attributes("-topmost", True)

        title_lbl = ctk.CTkLabel(modal, text=server_lang_mgr.tr("edit_app", "Edit Custom App"), font=ctk.CTkFont(size=16, weight="bold"))
        title_lbl.pack(anchor="w", padx=20, pady=(15, 10))

        lbl_name = ctk.CTkLabel(modal, text="App Label:")
        lbl_name.pack(anchor="w", padx=20, pady=(2, 0))
        entry_name = ctk.CTkEntry(modal, width=410)
        entry_name.insert(0, app.get("label", ""))
        entry_name.pack(padx=20, pady=(0, 8))

        lbl_path = ctk.CTkLabel(modal, text="Executable Path / Shortcut:")
        lbl_path.pack(anchor="w", padx=20, pady=(2, 0))
        path_row = ctk.CTkFrame(modal, fg_color="transparent")
        path_row.pack(fill="x", padx=20, pady=(0, 8))
        entry_path = ctk.CTkEntry(path_row, width=330)
        entry_path.insert(0, app.get("path", ""))
        entry_path.pack(side="left", padx=(0, 5))

        def browse_new_exe():
            fp = filedialog.askopenfilename(title="Select Executable or Shortcut", filetypes=[("Executables & Shortcuts", "*.exe;*.lnk"), ("All Files", "*.*")])
            if fp:
                entry_path.delete(0, "end")
                entry_path.insert(0, fp)

        btn_bexe = ctk.CTkButton(path_row, text=server_lang_mgr.tr("browse", "Browse"), width=65, command=browse_new_exe)
        btn_bexe.pack(side="right")

        lbl_icon = ctk.CTkLabel(modal, text=server_lang_mgr.tr("custom_icon", "Custom Icon (Optional PNG/ICO/EXE/LNK):"))
        lbl_icon.pack(anchor="w", padx=20, pady=(2, 0))
        icon_row = ctk.CTkFrame(modal, fg_color="transparent")
        icon_row.pack(fill="x", padx=20, pady=(0, 12))
        entry_icon = ctk.CTkEntry(icon_row, width=330, placeholder_text="Leave blank to use .exe/.lnk icon")
        entry_icon.pack(side="left", padx=(0, 5))

        def browse_new_icon():
            fp = filedialog.askopenfilename(title="Select Custom Icon Image", filetypes=[("Images, Shortcuts & Executables", "*.png;*.jpg;*.jpeg;*.ico;*.exe;*.lnk"), ("All Files", "*.*")])
            if fp:
                entry_icon.delete(0, "end")
                entry_icon.insert(0, fp)

        btn_bicon = ctk.CTkButton(icon_row, text=server_lang_mgr.tr("browse", "Browse"), width=65, command=browse_new_icon)
        btn_bicon.pack(side="right")

        def save_edits():
            new_l = entry_name.get().strip()
            new_p = entry_path.get().strip()
            new_i = entry_icon.get().strip()

            if not new_l or not new_p:
                return

            self.cfg_mgr.update_app(app_id, new_l, new_p, custom_icon_path=new_i)
            self.refresh_app_list_ui()
            self.broadcast_config_update()
            self.log_message(f"Updated app '{new_l}'", "CONFIG")
            modal.destroy()

        btn_save = ctk.CTkButton(modal, text=server_lang_mgr.tr("save", "Save Changes"), fg_color="#27ae60", hover_color="#2ecc71", command=save_edits)
        btn_save.pack(anchor="e", padx=20, pady=10)

    def refresh_app_list_ui(self):
        for widget in self.apps_scroll.winfo_children():
            widget.destroy()

        active_p = self.cfg_mgr.get_active_profile_name()
        self.apps_list_hdr.configure(text=server_lang_mgr.tr("apps_in_profile", "Apps in {profile}", profile=active_p))

        apps = self.cfg_mgr.get_active_apps()
        if not apps:
            no_app_lbl = ctk.CTkLabel(self.apps_scroll, text=server_lang_mgr.tr("no_apps", "No apps configured in {profile}.", profile=active_p), text_color="#888")
            no_app_lbl.pack(pady=10)
            return

        for app in apps:
            item_frame = ctk.CTkFrame(self.apps_scroll, fg_color="#23272e", corner_radius=6)
            item_frame.pack(fill="x", pady=4, padx=2)

            action_frame = ctk.CTkFrame(item_frame, fg_color="transparent")
            action_frame.pack(side="right", padx=6, pady=4)

            edit_btn = ctk.CTkButton(
                action_frame,
                text="✎",
                width=32,
                height=26,
                fg_color="#2980b9",
                hover_color="#3498db",
                command=lambda aid=app["id"]: self.open_edit_app_modal(aid)
            )
            edit_btn.pack(side="left", padx=2)

            del_btn = ctk.CTkButton(
                action_frame,
                text="❌",
                width=32,
                height=26,
                fg_color="#c0392b",
                hover_color="#e74c3c",
                command=lambda aid=app["id"]: self.delete_custom_app(aid)
            )
            del_btn.pack(side="left", padx=2)

            text_frame = ctk.CTkFrame(item_frame, fg_color="transparent")
            text_frame.pack(side="left", fill="both", expand=True, padx=8, pady=4)

            icon_indicator = "🖼️ " if app.get("icon_b64") else "📄 "
            lbl = ctk.CTkLabel(text_frame, text=icon_indicator + app["label"], font=ctk.CTkFont(size=12, weight="bold"), anchor="w")
            lbl.pack(fill="x")

            path_lbl = ctk.CTkLabel(text_frame, text=app["path"], font=ctk.CTkFont(size=10), text_color="#888", anchor="w")
            path_lbl.pack(fill="x")

    # ---------------- Combined Heartbeat Monitor Loop ----------------
    def _heartbeat_loop(self):
        loop = None
        if WINSDK_AVAILABLE:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        async def get_smtc_session():
            try:
                manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
                session = manager.get_current_session()
                if session:
                    props = await session.try_get_media_properties_async()
                    timeline = session.get_timeline_properties()
                    playback = session.get_playback_info()

                    title = props.title if props and props.title else "No Media Playing"
                    artist = props.artist if props and props.artist else "Unknown Artist"

                    progress = 0.0
                    if timeline and timeline.end_time.total_seconds() > 0:
                        pos = timeline.position.total_seconds()
                        end = timeline.end_time.total_seconds()
                        progress = min(1.0, max(0.0, pos / end))

                    is_playing = (playback.playback_status == 4) if playback else False

                    return {
                        "title": title,
                        "artist": artist,
                        "progress": round(progress, 3),
                        "is_playing": is_playing
                    }
            except Exception:
                pass

            return {
                "title": "No Media Playing",
                "artist": "Windows SMTC",
                "progress": 0.0,
                "is_playing": False
            }

        while self.heartbeat_running:
            try:
                if WINSDK_AVAILABLE and loop:
                    media_info = loop.run_until_complete(get_smtc_session())
                else:
                    media_info = self.current_media

                telem_info = get_system_telemetry()

                self.current_media = media_info
                self.current_telemetry = telem_info

                self.after(0, lambda m=media_info, t=telem_info: self._update_dashboard_ui(m, t))

                heartbeat_payload = {
                    "type": "heartbeat",
                    "media": media_info,
                    "telemetry": telem_info
                }
                self.broadcast_json(heartbeat_payload)

            except Exception:
                pass

            time.sleep(1.0)

    def _update_dashboard_ui(self, media: dict, telem: dict):
        self.media_title_lbl.configure(text=media["title"])
        self.media_artist_lbl.configure(text=media["artist"])
        self.cpu_label.configure(text=f"CPU: {telem['cpu']}%")
        self.ram_label.configure(text=f"RAM: {telem['ram']}%")

    # ---------------- Server & Networking ----------------
    def start_server(self):
        if self.is_running:
            return

        self.is_running = True
        self.status_badge.configure(text=server_lang_mgr.tr("running", "RUNNING"), fg_color="#2ecc71")
        self.toggle_btn.configure(text=server_lang_mgr.tr("stop_server", "Stop Server"), fg_color="#c0392b", hover_color="#e74c3c")
        self.log_message(f"Starting Two-Way JSON TCP Server on port {self.port}...", "SERVER")

        self.listen_thread = threading.Thread(target=self._server_listen_loop, daemon=True)
        self.listen_thread.start()

    def stop_server(self):
        if not self.is_running:
            return

        self.is_running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
            self.server_socket = None

        with self.clients_lock:
            for client_sock in list(self.clients):
                try:
                    client_sock.close()
                except Exception:
                    pass
            self.clients.clear()

        self.status_badge.configure(text=server_lang_mgr.tr("stopped", "STOPPED"), fg_color="#e74c3c")
        self.toggle_btn.configure(text=server_lang_mgr.tr("start_server", "Start Server"), fg_color="#27ae60", hover_color="#2ecc71")
        self.log_message("Server stopped.", "SERVER")

    def toggle_server(self):
        if self.is_running:
            self.stop_server()
        else:
            self.start_server()

    def _server_listen_loop(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)
            self.log_message(f"Server ready on port {self.port}", "SUCCESS")
        except Exception as e:
            self.log_message(f"Bind Error: {e}", "ERROR")
            self.after(0, self.stop_server)
            return

        while self.is_running:
            try:
                conn, addr = self.server_socket.accept()
                with self.clients_lock:
                    self.clients.add(conn)

                client_thread = threading.Thread(
                    target=self._client_handler,
                    args=(conn, addr),
                    daemon=True
                )
                client_thread.start()
            except Exception:
                break

    def _send_json(self, sock: socket.socket, payload: dict):
        try:
            data = (json.dumps(payload) + "\n").encode('utf-8')
            sock.sendall(data)
        except Exception:
            pass

    def broadcast_json(self, payload: dict):
        with self.clients_lock:
            dead_clients = set()
            for sock in self.clients:
                try:
                    data = (json.dumps(payload) + "\n").encode('utf-8')
                    sock.sendall(data)
                except Exception:
                    dead_clients.add(sock)

            for dead in dead_clients:
                self.clients.remove(dead)

    def broadcast_config_update(self):
        payload = {
            "type": "config_update",
            "active_profile": self.cfg_mgr.get_active_profile_name(),
            "profiles": PROFILES_LIST,
            "apps": self.cfg_mgr.get_active_apps()
        }
        self.broadcast_json(payload)

    def _client_handler(self, conn: socket.socket, addr):
        client_ip = f"{addr[0]}:{addr[1]}"
        self.log_message(f"Tablet connected from {client_ip}", "NET")

        init_payload = {
            "type": "init",
            "active_profile": self.cfg_mgr.get_active_profile_name(),
            "profiles": PROFILES_LIST,
            "apps": self.cfg_mgr.get_active_apps(),
            "media": self.current_media,
            "telemetry": self.current_telemetry
        }
        self._send_json(conn, init_payload)

        buffer = ""
        try:
            while self.is_running:
                data = conn.recv(1024)
                if not data:
                    break
                buffer += data.decode('utf-8', errors='ignore')

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        try:
                            msg = json.loads(line.strip())
                            self._process_client_message(msg, client_ip)
                        except json.JSONDecodeError:
                            self.log_message(f"Invalid JSON from {client_ip}: {line}", "WARNING")

        except Exception:
            pass
        finally:
            with self.clients_lock:
                self.clients.discard(conn)
            try:
                conn.close()
            except Exception:
                pass
            self.log_message(f"Tablet disconnected: {client_ip}", "NET")

    def _process_client_message(self, msg: dict, source: str):
        msg_type = msg.get("type")

        if msg_type == "action":
            action = msg.get("action")

            if action == "switch_profile":
                target_profile = msg.get("profile")
                if target_profile in PROFILES_LIST:
                    if self.cfg_mgr.set_active_profile(target_profile):
                        self.after(0, lambda: self.profile_combo.set(target_profile))
                        self.after(0, self.refresh_app_list_ui)
                        self.broadcast_config_update()
                        self.log_message(f"Profile switched to '{target_profile}' by {source}", "CONFIG")

            elif action == "launch":
                app_id = msg.get("id")
                app = self.cfg_mgr.get_app_by_id(app_id)
                if app:
                    self.log_message(f"Launching/Focusing '{app['label']}' ({app['path']}) requested by {source}", "ACTION")
                    focus_or_launch_app(app["path"])

            elif action == "media_key":
                key = msg.get("key")
                self.log_message(f"Media Key '{key}' requested by {source}", "ACTION")
                if key == "VOL_UP":
                    pyautogui.press("volumeup")
                elif key == "VOL_DOWN":
                    pyautogui.press("volumedown")
                elif key == "PLAY_PAUSE":
                    pyautogui.press("playpause")
                elif key == "NEXT_TRACK":
                    pyautogui.press("nexttrack")
                elif key == "MIC_MUTE":
                    self.toggle_mic_mute()

    def toggle_mic_mute(self):
        if not PYCAW_AVAILABLE:
            self.log_message("Mic Mute unavailable: pycaw/comtypes not installed.", "ERROR")
            return

        def _worker():
            CoInitialize()
            try:
                mic = None
                if hasattr(AudioUtilities, "GetMicrophone"):
                    try:
                        mic = AudioUtilities.GetMicrophone()
                    except Exception:
                        mic = None

                if mic is None:
                    from pycaw.pycaw import MMDeviceEnumerator
                    enumerator = MMDeviceEnumerator()
                    mic = enumerator.GetDefaultAudioEndpoint(1, 0)

                if mic:
                    interface = mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    volume = interface.QueryInterface(IAudioEndpointVolume)
                    current_mute = volume.GetMute()
                    new_mute = not current_mute
                    volume.SetMute(new_mute, None)
                    state_str = "MUTED 🔇" if new_mute else "UNMUTED 🎙️"
                    self.log_message(f"Mic Mute Toggled -> {state_str}", "ACTION")
            except Exception as e:
                self.log_message(f"Mic Mute Error: {e}", "ERROR")
            finally:
                CoUninitialize()

        threading.Thread(target=_worker, daemon=True).start()

    def log_message(self, message: str, level: str = "INFO"):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] [{level}] {message}\n"

        def _append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", formatted)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.after(0, _append)

    def clear_logs(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def on_closing(self):
        self.heartbeat_running = False
        self.stop_server()
        self.destroy()


if __name__ == "__main__":
    app = PCServerApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
