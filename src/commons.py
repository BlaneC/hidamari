import os
from pathlib import Path

LOGGER_NAME = "Hidamari"

PROJECT = "io.github.jeffshee.Hidamari"
DBUS_NAME_SERVER = f"{PROJECT}.server"
DBUS_NAME_PLAYER = f"{PROJECT}.player"

# -------------------------------------------------------------------
# Environment detection
# -------------------------------------------------------------------

IS_FLATPAK = "FLATPAK_ID" in os.environ

# -------------------------------------------------------------------
# XDG base directories (authoritative)
# -------------------------------------------------------------------

def _require_env(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return Path(value)

XDG_CONFIG_HOME = _require_env("XDG_CONFIG_HOME")
XDG_DATA_HOME   = _require_env("XDG_DATA_HOME")
XDG_CACHE_HOME  = _require_env("XDG_CACHE_HOME")

# -------------------------------------------------------------------
# Application directories (persistent)
# -------------------------------------------------------------------

CONFIG_DIR = XDG_CONFIG_HOME / "hidamari"
DATA_DIR   = XDG_DATA_HOME   / "hidamari"
CACHE_DIR  = XDG_CACHE_HOME  / "hidamari"

CONFIG_PATH = CONFIG_DIR / "config.json"

VIDEO_WALLPAPER_DIR = DATA_DIR / "videos"

# Ensure directories exist
for d in (CONFIG_DIR, DATA_DIR, CACHE_DIR, VIDEO_WALLPAPER_DIR):
    d.mkdir(parents=True, exist_ok=True)

# -------------------------------------------------------------------
# Autostart (host only — Flatpak cannot self-autostart)
# -------------------------------------------------------------------

AUTOSTART_DESKTOP_PATH = None

if not IS_FLATPAK:
    AUTOSTART_DIR = XDG_CONFIG_HOME / "autostart"
    AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    AUTOSTART_DESKTOP_PATH = AUTOSTART_DIR / f"{PROJECT}.desktop"

AUTOSTART_DESKTOP_CONTENT = """[Desktop Entry]
Name=Hidamari
Exec=hidamari -b
Icon=io.github.jeffshee.Hidamari
Terminal=false
Type=Application
Categories=GTK;Utility;
StartupNotify=true
"""

# -------------------------------------------------------------------
# Configuration schema
# -------------------------------------------------------------------

MODE_NULL   = "MODE_NULL"
MODE_VIDEO  = "MODE_VIDEO"
MODE_STREAM = "MODE_STREAM"
MODE_WEBPAGE = "MODE_WEBPAGE"

CONFIG_VERSION = 4

CONFIG_KEY_VERSION = "version"
CONFIG_KEY_MODE = "mode"
CONFIG_KEY_DATA_SOURCE = "data_source"
CONFIG_KEY_MUTE = "is_mute"
CONFIG_KEY_VOLUME = "audio_volume"
CONFIG_KEY_STATIC_WALLPAPER = "is_static_wallpaper"
CONFIG_KEY_BLUR_RADIUS = "static_wallpaper_blur_radius"
CONFIG_KEY_PAUSE_WHEN_MAXIMIZED = "is_pause_when_maximized"
CONFIG_KEY_MUTE_WHEN_MAXIMIZED = "is_mute_when_maximized"
CONFIG_KEY_FADE_DURATION_SEC = "fade_duration_sec"
CONFIG_KEY_FADE_INTERVAL = "fade_interval"
CONFIG_KEY_SYSTRAY = "is_show_systray"
CONFIG_KEY_FIRST_TIME = "is_first_time"

# -------------------------------------------------------------------
# Static config template (DO NOT mutate)
# -------------------------------------------------------------------

CONFIG_TEMPLATE = {
    CONFIG_KEY_VERSION: CONFIG_VERSION,
    CONFIG_KEY_MODE: MODE_NULL,
    CONFIG_KEY_DATA_SOURCE: {},
    CONFIG_KEY_MUTE: False,
    CONFIG_KEY_VOLUME: 50,
    CONFIG_KEY_STATIC_WALLPAPER: True,
    CONFIG_KEY_BLUR_RADIUS: 5,
    CONFIG_KEY_PAUSE_WHEN_MAXIMIZED: True,
    CONFIG_KEY_MUTE_WHEN_MAXIMIZED: False,
    CONFIG_KEY_FADE_DURATION_SEC: 1.5,
    CONFIG_KEY_FADE_INTERVAL: 0.1,
    CONFIG_KEY_SYSTRAY: True,
    CONFIG_KEY_FIRST_TIME: True,
}

# -------------------------------------------------------------------
# Monitor-aware defaults (runtime)
# -------------------------------------------------------------------

try:
    from monitor import MonitorInfo
except ModuleNotFoundError:
    from hidamari.monitor import MonitorInfo

def _default_data_sources() -> dict:
    info = MonitorInfo()
    sources = {}

    for monitor in info.monitors():
        sources[monitor["name"]] = ""

    sources["Default"] = ""
    return sources

def default_config() -> dict:
    cfg = dict(_CONFIG_TEMPLATE)
    cfg[CONFIG_KEY_DATA_SOURCE] = _default_data_sources()
    return cfg
