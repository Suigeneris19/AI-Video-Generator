"""VideoForge Android/desktop UI (Kivy)."""
import os, shutil, stat, threading

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

import vidgen

ON_ANDROID = "ANDROID_ARGUMENT" in os.environ


def setup_android():
    """Copy bundled ffmpeg to private storage, make executable, pick output dir."""
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.expanduser("~/Videos")
    if ON_ANDROID:
        from android.permissions import Permission, request_permissions
        request_permissions([Permission.WRITE_EXTERNAL_STORAGE, Permission.READ_EXTERNAL_STORAGE])
        from jnius import autoclass
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        ext = act.getExternalFilesDir(None)
        out_dir = os.path.join(ext.getAbsolutePath() if ext else here, "VideoForge")
        src = os.path.join(here, "bin", "ffmpeg.bin")
        dst = os.path.join(act.getFilesDir().getAbsolutePath(), "ffmpeg")
        if os.path.exists(src):
            shutil.copyfile(src, dst)
            os.chmod(dst, os.stat(dst).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            os.environ["VIDEOFORGE_FFMPEG"] = dst
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


class Root(BoxLayout):
    def __init__(self, out_dir, **kw):
        super().__init__(orientation="vertical", padding=16, spacing=10, **kw)
        self.out_dir = out_dir
        self.add_widget(Label(text="VideoForge", font_size="26sp", size_hint_y=None, height=50))
        self.prompt = TextInput(hint_text="Paste your prompt or full script once...", multiline=True)
        self.add_widget(self.prompt)
        row = BoxLayout(size_hint_y=None, height=48, spacing=8)
        self.minutes = TextInput(text="60", hint_text="minutes", input_filter="int", multiline=False)
        self.res = Spinner(text="720p", values=("480p", "720p", "1080p"))
        self.voice = Spinner(text="Voice: on", values=("Voice: on", "Voice: off"))
        for w in (self.minutes, self.res, self.voice):
            row.add_widget(w)
        self.add_widget(row)
        self.btn = Button(text="Generate video", size_hint_y=None, height=56)
        self.btn.bind(on_release=self.start)
        self.add_widget(self.btn)
        self.bar = ProgressBar(max=100, size_hint_y=None, height=16)
        self.add_widget(self.bar)
        self.status = Label(text="Ready", size_hint_y=None, height=70, halign="center")
        self.status.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        self.add_widget(self.status)

    def start(self, *_):
        text = self.prompt.text.strip()
        if not text:
            self.status.text = "Enter a prompt first."
            return
        try:
            mins = max(1, int(self.minutes.text or "60"))
        except ValueError:
            mins = 60
        self.btn.disabled = True
        out = os.path.join(self.out_dir, "videoforge_output.mp4")
        threading.Thread(target=self.work, args=(text, mins, out), daemon=True).start()

    def work(self, text, mins, out):
        def prog(f, m):
            Clock.schedule_once(lambda dt: self.update(f, m))
        try:
            vidgen.generate(text, mins, self.res.text, out, self.voice.text == "Voice: on", prog)
            msg = f"Saved:\n{out}"
            if ON_ANDROID:
                try:
                    movies = "/sdcard/Movies"
                    os.makedirs(movies, exist_ok=True)
                    shutil.copyfile(out, os.path.join(movies, "videoforge_output.mp4"))
                    msg += "\nAlso copied to Movies."
                except Exception:
                    pass
        except Exception as e:
            msg = f"Error: {str(e)[:200]}"
        Clock.schedule_once(lambda dt: self.finish(msg))

    def update(self, f, m):
        self.bar.value = f * 100
        self.status.text = f"{m}  ({f * 100:.0f}%)"

    def finish(self, msg):
        self.status.text = msg
        self.btn.disabled = False


class VideoForgeApp(App):
    def build(self):
        self.title = "VideoForge"
        return Root(setup_android())


if __name__ == "__main__":
    VideoForgeApp().run()
