import sys
import glob
import time
import random
import ctypes
import logging
import pathlib
import subprocess
from threading import Timer
from gi.repository import GLib
import locale
locale.setlocale(locale.LC_NUMERIC, "C")
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gio, Gdk

import vlc
from pydbus import SessionBus
from PIL import Image, ImageFilter

try:
    import os
    sys.path.insert(1, os.path.join(sys.path[0], '..'))
    from player.base_player import BasePlayer
    from menu import build_menu
    from hidamari.commons import *
    from utils import ActiveHandler, ConfigUtil, is_gnome, is_wayland, is_nvidia_proprietary, is_vdpau_ok, is_flatpak
    from yt_utils import get_formats, get_best_audio, get_optimal_video
except ModuleNotFoundError:
    from hidamari.player.base_player import BasePlayer
    from hidamari.menu import build_menu
    from hidamari.commons import *
    from hidamari.utils import ActiveHandler, ConfigUtil, is_gnome, is_wayland, is_nvidia_proprietary, is_vdpau_ok, is_flatpak
    from hidamari.yt_utils import get_formats, get_best_audio, get_optimal_video

logger = logging.getLogger(LOGGER_NAME)

if is_wayland():
    from hidamari.utils import WindowHandlerGnomeWayland as WindowHandler
else:
    try:
        from utils import WindowHandler
    except ModuleNotFoundError:
        from hidamari.utils import WindowHandler


class Fade:
    def __init__(self):
        self._source_id = None

    def start(self, cur, target, step,
              fade_interval=None, fade_interval_ms=None,
              update_callback=None, complete_callback=None):

        # Backward compatibility
        if fade_interval_ms is None:
            if fade_interval is None:
                raise ValueError("fade_interval or fade_interval_ms must be provided")
            fade_interval_ms = int(fade_interval * 1000)

        self.cancel()

        def tick():
            nonlocal cur
            cur += step

            done = (step < 0 and cur <= target) or (step > 0 and cur >= target)
            if done:
                cur = target

            if update_callback:
                update_callback(int(cur))

            if done:
                if complete_callback:
                    complete_callback()
                self._source_id = None
                return False

            return True

        self._source_id = GLib.timeout_add(fade_interval_ms, tick)

    def cancel(self):
        if self._source_id is not None:
            GLib.source_remove(self._source_id)
            self._source_id = None

class VideoBackend:
    def play(self): ...
    def stop(self): ...
    def pause(self): ...
    def is_playing(self) -> bool: ...
    def set_volume(self, vol: int): ...
    def get_volume(self) -> int: ...
    def set_mute(self, mute: bool): ...
    def set_position(self, pos: float): ...
    def get_position(self) -> float: ...
    def set_media(self, source: str): ...

class VLCBackend(VideoBackend):
    def __init__(self):
        vlc_options = [
            "--no-disable-screensaver",
            "--drop-late-frames",
            "--skip-frames",
            "--quiet",
        ]
        self.instance = vlc.Instance(vlc_options)
        self.player = self.instance.media_player_new()

    def attach(self, widget: Gtk.DrawingArea):
        self.player.set_xwindow(widget.get_window().get_xid())

    def set_media(self, source: str):
        media = self.instance.media_new(source)
        media.add_option("input-repeat=65535")
        self.player.set_media(media)

    def play(self): self.player.play()
    def stop(self): self.player.stop()
    def pause(self): self.player.pause()
    def is_playing(self): return bool(self.player.is_playing())
    def set_volume(self, v): self.player.audio_set_volume(v)
    def get_volume(self): return self.player.audio_get_volume()
    def set_mute(self, m): self.player.audio_set_mute(m)
    def set_position(self, p): self.player.set_position(p)
    def get_position(self): return self.player.get_position()

# class MPVBackend(VideoBackend):
#     def __init__(self, xid: int):
#         print("Using MPV backend")
#         self.mpv = MPV(
#             wid=str(xid),
#             vo="gpu",
#             hwdec="auto",
#             loop="inf",
#             osc="no",
#             input_default_bindings=False,
#             input_vo_keyboard=False,
#         )
#         self._loaded = False

#         @self.mpv.event_callback('file-loaded')
#         def _on_loaded(event):
#             self._loaded = True

#     def set_media(self, source: str):
#         self.mpv.play(source)

#     def play(self): self.mpv.pause = False
#     def stop(self): self.mpv.stop()
#     def pause(self): self.mpv.pause = True
#     def is_playing(self): return not self.mpv.pause
#     def set_volume(self, v): self.mpv.volume = v
#     def get_volume(self): return int(self.mpv.volume)
#     def set_mute(self, m): self.mpv.mute = m
#     def set_position(self, p):
#         if not self._loaded:
#             return
#         self.mpv.seek(p * 100, reference="absolute-percent")
#     def get_position(self): return (self.mpv.percent_pos or 0) / 100.0


class PlayerWindow(Gtk.ApplicationWindow):
    def __init__(self, name, width, height, *, application):
        super().__init__(application=application)

        self.name = name
        self.width = width
        self.height = height

        # 🔑 IMPORTANT: use a private attribute
        self._video_widget = Gtk.DrawingArea()
        self.add(self._video_widget)
        self._video_widget.show()

        self.fade = Fade()
        self.backend: VideoBackend | None = None
        self._pending_source = None
        # self.use_mpv = use_mpv
        
        self._video_widget.connect("realize", self._on_video_realize)

    def _on_video_realize(self, widget):
        # print("MPV:", self.use_mpv)

        # if self.use_mpv:
        #     xid = widget.get_window().get_xid()
        #     self.backend = MPVBackend(xid)
        # else:
        self.backend = VLCBackend()
        self.backend.attach(widget)

        if self._pending_source:
            self.backend.set_media(self._pending_source)
            self._pending_source = None

    def _attach_vlc(self, widget):
        if self.backend:
            self.backend.attach(widget)

    # ---- Playback API (backend-agnostic) ----

    def set_media(self, source: str):
        if self.backend is None:
            self._pending_source = source
        else:
            self.backend.set_media(source)

    def play(self):
        if self.backend:
            self.backend.play()

    def stop(self):
        if self.backend:
            self.backend.stop()

    def pause(self):
        self.backend.pause()

    def is_playing(self):
        if self.backend:
            return self.backend.is_playing()
        else:
            return False

    def set_volume(self, v: int):
        if self.backend:
            self.backend.set_volume(v)

    def get_volume(self):
        if self.backend:
            return self.backend.get_volume()
        else:
            return 0

    def set_mute(self, m: bool):
        if self.backend:
            self.backend.set_mute(m)

    def set_position(self, p: float):
        if self.backend:
            self.backend.set_position(p)

    def get_position(self):
        if self.backend:
            return self.backend.get_position()
        else:
            return 0

    # ---- Fade helpers ----

    def play_fade(self, target, fade_duration_sec, fade_interval):
        self.play()
        cur = 0
        step = (target - cur) / (fade_duration_sec / fade_interval)
        self.fade.cancel()
        self.fade.start(
            cur,
            target,
            step,
            fade_interval=fade_interval,
            update_callback=self.set_volume
        )

    def pause_fade(self, fade_duration_sec, fade_interval):
        cur = self.get_volume()
        step = -cur / (fade_duration_sec / fade_interval)
        self.fade.cancel()
        self.fade.start(
            cur,
            0,
            step,
            fade_interval=fade_interval,
            update_callback=self.set_volume,
            complete_callback=self.stop
        )

    # def centercrop(self, video_width=None, video_height=None):
    #     # Getting dimension from libvlc is not reliable enough (need to consider timing)
    #     if (video_width, video_height) == (None, None):
    #         video_width, video_height = self.__vlc_widget.player.video_get_size()
    #         if video_width == 0 or video_height == 0:
    #             logger.warning("[CenterCrop] video_get_size is not ready yet")
    #             return
    #     logger.debug(f"[CenterCrop] Dimension {video_width}x{video_height}")
    #     window_ratio = self.width / self.height
    #     video_ratio = video_width / video_height
    #     if window_ratio == video_ratio:
    #         return
    #     elif video_ratio < window_ratio:
    #         # If window is wider than video
    #         # For example video ratio (4:3)=1.33..., window ratio (16:9)=1.77...
    #         crop_height = video_width / window_ratio
    #         top_offset = (video_height - crop_height) / 2
    #         crop_geometry = f"{int(video_width)}x{int(crop_height+top_offset)}+0+{int(top_offset)}"

    #     else:
    #         # If video is wider than window
    #         crop_width = video_height * window_ratio
    #         left_offset = (video_width - crop_width) / 2
    #         crop_geometry = f"{int(crop_width+left_offset)}x{int(video_height)}+{int(left_offset)}+0"

    #     # Crop geometry WxH+L+T: Width x Height + Left Offset + top Offset
    #     logger.debug(f"[CenterCrop] Crop geometry: {crop_geometry}")
    #     self.__vlc_widget.player.video_set_crop_geometry(crop_geometry)

    # def add_audio_track(self, audio):
    #     self.__vlc_widget.player.add_slave(vlc.MediaSlaveType(1), audio, True)

    # def _on_button_press_event(self, widget, event):
    #     if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:
    #         if not self.menu:
    #             self.menu = build_menu(MODE_VIDEO)
    #         self.menu.popup_at_pointer()
    #         return True
    #     return False

    # def get_name(self):
    #     return self.name


class VideoPlayer(BasePlayer):
    """
    <node>
    <interface name='io.github.jeffshee.hidamari.player'>
        <property name="mode" type="s" access="read"/>
        <property name="data_source" type="s" access="readwrite"/>
        <property name="volume" type="i" access="readwrite"/>
        <property name="is_mute" type="b" access="readwrite"/>
        <property name="is_playing" type="b" access="read"/>
        <property name="is_paused_by_user" type="b" access="readwrite"/>
        <method name='reload_config'/>
        <method name='pause_playback'/>
        <method name='start_playback'/>
        <method name='quit_player'/>
    </interface>
    </node>
    """

    def __init__(self, *args, **kwargs):
        #self.use_mpv = use_mpv
        super().__init__(*args, **kwargs)

        # We need to initialize X11 threads so we can use hardware decoding.
        # `libX11.so.6` fix for Fedora 33
        x11 = None
        if is_wayland() and is_nvidia_proprietary() and not is_vdpau_ok():
            logger.warning(
                "Proprietary Nvidia driver detected! HW Acceleration is not yet working in Wayland.")
        else:
            for lib in ["libX11.so", "libX11.so.6"]:
                try:
                    x11 = ctypes.cdll.LoadLibrary(lib)
                except OSError:
                    pass
                if x11 is not None:
                    x11.XInitThreads()
                    break

        self.config = None
        #self.use_mpv = use_mpv
        self.reload_config()

        # Static wallpaper (currently for GNOME only)
        if is_gnome():
            self.original_wallpaper_uri = None
            self.original_wallpaper_uri_dark = None
            if is_flatpak():
                try:
                    self.original_wallpaper_uri = subprocess.check_output(
                        "flatpak-spawn --host gsettings get org.gnome.desktop.background picture-uri", shell=True, encoding='UTF-8')
                    self.original_wallpaper_uri_dark = subprocess.check_output(
                        "flatpak-spawn --host gsettings get org.gnome.desktop.background picture-uri-dark", shell=True, encoding='UTF-8')
                except subprocess.CalledProcessError as e:
                    logger.error(f"[StaticWallpaper] {e}")
            else:
                gso = Gio.Settings.new("org.gnome.desktop.background")
                self.original_wallpaper_uri = gso.get_string("picture-uri")
                self.original_wallpaper_uri_dark = gso.get_string(
                    "picture-uri-dark")

        # Handler should be created after everything initialized
        self.active_handler, self.window_handler = None, None
        self.is_any_maximized, self.is_any_fullscreen = False, False
        self.is_paused_by_user = False

    def new_window(self, gdk_monitor):
        rect = gdk_monitor.get_geometry()
        return PlayerWindow(gdk_monitor.get_model(), rect.width, rect.height, application=self)

    def do_activate(self):
        super().do_activate()
        self.data_source = self.config[CONFIG_KEY_DATA_SOURCE]

    def _on_monitor_added(self, _, gdk_monitor, *args):
        super()._on_monitor_added(_, gdk_monitor, *args)
        self.monitor_sync()

    def _on_active_changed(self, active):
        if active:
            self.pause_playback()
        else:
            if self._should_playback_start():
                self.start_playback()
            else:
                self.pause_playback()

    def _on_window_state_changed(self, state):
        self.is_any_maximized, self.is_any_fullscreen = state["is_any_maximized"], state["is_any_fullscreen"]
        logger.info(f"is_any_maximized: {self.is_any_maximized}, is_any_fullscreen: {self.is_any_fullscreen}")

        if self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED]:
            if self._should_playback_start():
                self.start_playback()
            else:
                self.pause_playback()
        elif self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED]:
            for monitor, window in self.windows.items():
                if not monitor.is_primary():
                    continue
                if self.is_any_fullscreen or self.is_any_maximized:
                    window.volume_fade(target=0, fade_duration_sec=self.config[CONFIG_KEY_FADE_DURATION_SEC],
                                fade_interval=self.config[CONFIG_KEY_FADE_INTERVAL])
                else:
                    window.volume_fade(target=self.volume, fade_duration_sec=self.config[CONFIG_KEY_FADE_DURATION_SEC],
                                fade_interval=self.config[CONFIG_KEY_FADE_INTERVAL])
        
    def _should_playback_start(self):
        if self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED] and (self.is_any_maximized or self.is_any_fullscreen):
            return False
        if self.is_paused_by_user:
            return False
        return True

    @property
    def mode(self):
        return self.config[CONFIG_KEY_MODE]

    @property
    def data_source(self):
        return self.config[CONFIG_KEY_DATA_SOURCE]

    @data_source.setter
    def data_source(self, data_source):
        self.config[CONFIG_KEY_DATA_SOURCE] = data_source

        if self.mode == MODE_VIDEO:
            # Get the dimension of the video
            video_width, video_height = {}, {}
            try:
                for monitor,video in data_source.items():
                    # fallback to Default video
                    if len(video) == 0:
                        video = data_source['Default']
                    dimension = subprocess.check_output([
                        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                        '-show_entries', 'stream=width,height', '-of',
                        'csv=s=x:p=0', video
                        ], shell=False, encoding='UTF-8').replace('\n', '')
                    dimension = dimension.split("x")
                    video_width[monitor] = int(dimension[0])
                    video_height[monitor] = int(dimension[1])
            except subprocess.CalledProcessError:
                for monitor, video in data_source.items():
                    video_width.setdefault(monitor, None)
                    video_height.setdefault(monitor, None)
                    
            for (monitor, window) in self.windows.items():
                source = data_source[monitor.get_model()] if monitor.get_model() in data_source and len(data_source[monitor.get_model()]) != 0 else data_source['Default']
                logger.info(f"Setting source {source} to {monitor.get_model()}")
                window.set_media(source)

                if not monitor.is_primary():
                    window.set_mute(True)

                window.set_position(0.0)
                # if monitor.get_model() not in data_source or len(data_source[monitor.get_model()]) == 0:
                #     window.centercrop(video_width['Default'], video_height['Default'])
                # else:                
                #     window.centercrop(video_width[monitor.get_model()], video_height[monitor.get_model()])

        elif self.mode == MODE_STREAM:
            source = data_source['Default']
            formats = get_formats(source)
            max_height = max(
                self.windows, key=lambda m: m.get_geometry().height).get_geometry().height
            video_url, video_width, video_height = get_optimal_video(
                formats, max_height)
            audio_url = get_best_audio(formats)

            for monitor, window in self.windows.items():
                media = window.media_new(video_url)
                media.add_option("input-repeat=65535")
                window.set_media(media)
                if monitor.is_primary():
                    window.add_audio_track(audio_url)
                else:
                    # `get_optimal_video` now might return video with audio.
                    media.add_option("no-audio")
                window.set_position(0.0)
                # window.centercrop(video_width, video_height)
        else:
            raise ValueError("Invalid mode")

        self.volume = self.config[CONFIG_KEY_VOLUME]
        self.is_mute = self.config[CONFIG_KEY_MUTE]
        self.start_playback()

        # Everything is initialized. Create handlers if haven't.
        if not self.active_handler:
            self.active_handler = ActiveHandler(self._on_active_changed)
        if not self.window_handler:
            self.window_handler = WindowHandler(self._on_window_state_changed)

        if self.config[CONFIG_KEY_STATIC_WALLPAPER] and self.mode == MODE_VIDEO:
            self.set_static_wallpaper()
        else:
            self.set_original_wallpaper()

    @property
    def volume(self):
        return self.config[CONFIG_KEY_VOLUME]

    @volume.setter
    def volume(self, volume):
        self.config[CONFIG_KEY_VOLUME] = volume
        for monitor in self.windows:
            if monitor.is_primary():
                self.windows[monitor].set_volume(volume)

    @property
    def is_mute(self):
        return self.config[CONFIG_KEY_MUTE]

    @is_mute.setter
    def is_mute(self, is_mute):
        self.config[CONFIG_KEY_MUTE] = is_mute
        for monitor, window in self.windows.items():
            if monitor.is_primary():
                window.set_mute(is_mute)

    @property
    def is_playing(self):
        return not self.is_paused_by_user

    def pause_playback(self):
        for monitor, window in self.windows.items():
            window.pause_fade(fade_duration_sec=self.config[CONFIG_KEY_FADE_DURATION_SEC],
                              fade_interval=self.config[CONFIG_KEY_FADE_INTERVAL])

    def start_playback(self):
        if self._should_playback_start():
            for monitor, window in self.windows.items():
                window.play_fade(target=self.volume, fade_duration_sec=self.config[CONFIG_KEY_FADE_DURATION_SEC],
                            fade_interval=self.config[CONFIG_KEY_FADE_INTERVAL])

    def monitor_sync(self):
        primary_monitor = None
        for monitor, window in self.windows.items():
            if monitor.is_primary:
                primary_monitor = monitor
                break
        if primary_monitor:
            for monitor, window in self.windows.items():
                if monitor == primary_monitor:
                    continue
                # `set_position()` method require the playback to be enabled before calling
                window.play()
                window.set_position(
                    self.windows[primary_monitor].get_position())
                window.play() if self.windows[primary_monitor].is_playing(
                ) else window.pause()

    def set_static_wallpaper(self):
        # Currently for GNOME only
        if not is_gnome():
            return
        # Get the duration of the video
        try:
            duration = float(subprocess.check_output([
                'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', self.data_source['Default']
            ], shell = False))
        except subprocess.CalledProcessError:
            duration = 0
        # Find the golden ratio
        ss = time.strftime('%H:%M:%S', time.gmtime(duration / 3.14))
        # Extract the frame
        static_wallpaper_path = os.path.join(
            CONFIG_DIR, "static-{:06d}.png".format(random.randint(0, 999999)))
        ret = subprocess.run([
            'ffmpeg', '-y', '-ss', ss, '-i', self.data_source['Default'],
            '-vframes', '1', static_wallpaper_path
        ], shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        if ret.returncode == 0 and os.path.isfile(static_wallpaper_path):
            blur_wallpaper = Image.open(static_wallpaper_path)
            blur_wallpaper = blur_wallpaper.filter(
                ImageFilter.GaussianBlur(self.config["static_wallpaper_blur_radius"]))
            blur_wallpaper.save(static_wallpaper_path)
            static_wallpaper_uri = pathlib.Path(
                static_wallpaper_path).resolve().as_uri()
            if is_flatpak():
                try:
                    subprocess.run(
                        ['flatpak-spawn', '--host', 'gsettings', 'set', 'org.gnome.desktop.background', 'picture-uri', static_wallpaper_uri], shell=False)
                    subprocess.run(
                        ['flatpak-spawn', '--host', 'gsettings', 'set', 'org.gnome.desktop.background', 'picture-uri-dark', static_wallpaper_uri], shell=False)
                except subprocess.CalledProcessError as e:
                    logger.error(f"[StaticWallpaper] {e}")
            else:
                gso = Gio.Settings.new("org.gnome.desktop.background")
                gso.set_string("picture-uri", static_wallpaper_uri)
                gso.set_string("picture-uri-dark", static_wallpaper_uri)

    def set_original_wallpaper(self):
        # Currently for GNOME only
        if not is_gnome():
            return
        if is_flatpak():
            try:
                if self.original_wallpaper_uri is not None:
                    subprocess.run(
                        ['flatpak-spawn', '--host', 'gsettings', 'set', 'org.gnome.desktop.background', 'picture-uri', self.original_wallpaper_uri], shell=False)
                if self.original_wallpaper_uri_dark is not None:
                    subprocess.run(
                        ['flatpak-spawn', '--host', 'gsettings', 'set', 'org.gnome.desktop.background', 'picture-uri-dark', self.original_wallpaper_uri], shell=False)
            except subprocess.CalledProcessError as e:
                logger.error(f"[StaticWallpaper] {e}")
        else:
            gso = Gio.Settings.new("org.gnome.desktop.background")
            gso.set_string("picture-uri", self.original_wallpaper_uri)
            gso.set_string("picture-uri-dark",
                           self.original_wallpaper_uri_dark)
        # Purge the generated static wallpaper (and leftover if any)
        for f in glob.glob(os.path.join(CONFIG_DIR, "static-*.png")):
            os.remove(f)

    def reload_config(self):
        self.config = ConfigUtil().load()

    def quit_player(self):
        self.set_original_wallpaper()
        super().quit_player()


def main():
    bus = SessionBus()
    app = VideoPlayer()
    try:
        bus.publish(DBUS_NAME_PLAYER, app)
    except RuntimeError as e:
        logger.error(e)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
