"""VideoForge engine: one prompt -> long narrated MP4. Offline, no credits, no cloud models.

Pipeline: prompt -> scene plan -> Pillow frame per scene -> TTS narration (if available)
-> one fast FFmpeg encode per scene -> lossless concat into final MP4.
"""
import hashlib, math, os, re, shutil, subprocess, sys, tempfile, textwrap

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_BOLD = os.path.join(HERE, "assets", "DejaVuSans-Bold.ttf")
FONT_REG = os.path.join(HERE, "assets", "DejaVuSans.ttf")

RES = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080)}
FPS = 10  # still-image scenes: low fps keeps encoding extremely fast

PALETTES = [
    ((18, 24, 54), (64, 52, 140), (255, 214, 102)),
    ((10, 40, 48), (22, 110, 112), (255, 240, 200)),
    ((46, 18, 36), (140, 52, 90), (255, 190, 160)),
    ((16, 28, 20), (50, 120, 70), (230, 255, 180)),
    ((30, 20, 14), (150, 90, 40), (255, 235, 190)),
    ((12, 14, 20), (48, 62, 96), (160, 220, 255)),
]

TEMPLATES = [
    "Let us begin by looking closely at {t}.",
    "Every great idea about {t} starts with a single question.",
    "Consider what {t} means in everyday life.",
    "History shows that {t} has shaped people in surprising ways.",
    "There is a common mistake people make when thinking about {t}.",
    "Now imagine {t} seen from a completely different angle.",
    "The deeper truth about {t} is simple, yet easy to forget.",
    "Pause for a moment and reflect on {t}.",
    "What would change if you truly understood {t}?",
    "Put {t} into practice, one small step at a time.",
    "Remember this about {t}: progress matters more than perfection.",
    "Carry the lesson of {t} with you into tomorrow.",
]


# ---------------------------------------------------------------- ffmpeg helpers
def find_ffmpeg():
    env = os.environ.get("VIDEOFORGE_FFMPEG")
    if env and os.path.exists(env):
        return env
    return shutil.which("ffmpeg") or "ffmpeg"


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="ignore")[-800:])
    return p.stderr.decode(errors="ignore")


def audio_duration(ffmpeg, path):
    p = subprocess.run([ffmpeg, "-i", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", p.stderr.decode(errors="ignore"))
    if not m:
        return None
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


# ---------------------------------------------------------------- TTS backends
def tts_espeak(text, out_wav):
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        return False
    try:
        run([exe, "-w", out_wav, "-s", "150", text])
        return os.path.exists(out_wav) and os.path.getsize(out_wav) > 1000
    except Exception:
        return False


_android_tts = {"obj": None}


def tts_android(text, out_wav):
    """Android TextToSpeech.synthesizeToFile via pyjnius. Best effort."""
    try:
        import time
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        TTS = autoclass("android.speech.tts.TextToSpeech")
        File = autoclass("java.io.File")
        Bundle = autoclass("android.os.Bundle")
        if _android_tts["obj"] is None:
            from jnius import PythonJavaClass, java_method

            class Init(PythonJavaClass):
                __javainterfaces__ = ["android/speech/tts/TextToSpeech$OnInitListener"]
                ready = False

                @java_method("(I)V")
                def onInit(self, status):
                    Init.ready = status == 0

            listener = Init()
            _android_tts["listener"] = listener
            _android_tts["obj"] = TTS(PythonActivity.mActivity, listener)
            for _ in range(100):
                if Init.ready:
                    break
                time.sleep(0.1)
        tts = _android_tts["obj"]
        if os.path.exists(out_wav):
            os.remove(out_wav)
        tts.synthesizeToFile(text, Bundle(), File(out_wav), "vf")
        last, stable = -1, 0
        for _ in range(300):  # up to ~30 s per scene
            time.sleep(0.1)
            size = os.path.getsize(out_wav) if os.path.exists(out_wav) else 0
            if size > 1000 and size == last:
                stable += 1
                if stable >= 4:
                    return True
            else:
                stable = 0
            last = size
        return os.path.exists(out_wav) and os.path.getsize(out_wav) > 1000
    except Exception:
        return False


def synthesize(text, out_wav):
    if "ANDROID_ARGUMENT" in os.environ:
        return tts_android(text, out_wav)
    return tts_espeak(text, out_wav)


# ---------------------------------------------------------------- planning
def split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p and p.strip()]


def plan_scenes(prompt, minutes, scene_seconds=20):
    """Return list of (heading, narration). Short prompts are expanded into a chaptered outline."""
    base = split_sentences(prompt)
    n_target = max(1, math.ceil(minutes * 60 / scene_seconds))
    topic = (base[0] if base else "this idea").rstrip(".!?")
    if len(topic) > 70:
        topic = topic[:67].rsplit(" ", 1)[0] + "..."
    scenes = []
    if len(base) >= n_target:
        # user supplied enough material: group sentences evenly
        size = math.ceil(len(base) / n_target)
        for i in range(0, len(base), size):
            chunk = " ".join(base[i:i + size])
            scenes.append((f"Part {len(scenes) + 1}", chunk))
    else:
        # expand: weave user's own sentences between template lines
        chapter, k = 1, 0
        scenes.append(("Introduction", f"{topic}."))
        while len(scenes) < n_target:
            if base and k % 3 == 0 and (k // 3) < len(base):
                line = base[k // 3]
            else:
                line = TEMPLATES[k % len(TEMPLATES)].format(t=topic[:1].lower() + topic[1:])
            if k % len(TEMPLATES) == 0 and k:
                chapter += 1
            scenes.append((f"Chapter {chapter}", line))
            k += 1
    return scenes


# ---------------------------------------------------------------- frame rendering
def _font(path, size):
    return ImageFont.truetype(path, size)


def render_frame(heading, text, idx, total, size, out_png):
    w, h = size
    seed = int(hashlib.md5(f"{idx // 6}".encode()).hexdigest(), 16)
    c1, c2, accent = PALETTES[seed % len(PALETTES)]
    img = Image.new("RGB", (w, h), c1)
    # vertical gradient
    px = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        px.line([(0, y), (w, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    d = ImageDraw.Draw(img, "RGBA")
    # decorative circles (deterministic per scene)
    s = int(hashlib.md5(f"c{idx}".encode()).hexdigest(), 16)
    for j in range(5):
        r = int(h * (0.08 + ((s >> (j * 5)) % 20) / 100))
        cx = (s >> (j * 7)) % w
        cy = (s >> (j * 3)) % h
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent + (28,))
    # heading
    hf = _font(FONT_BOLD, max(18, h // 18))
    d.text((w * 0.07, h * 0.09), heading.upper(), font=hf, fill=accent + (255,))
    d.rectangle([w * 0.07, h * 0.09 + h // 14, w * 0.07 + w * 0.12, h * 0.09 + h // 14 + 4], fill=accent + (255,))
    # body text, auto-fit
    box_w = int(w * 0.86)
    fs = max(20, h // 11)
    while fs > 16:
        f = _font(FONT_BOLD, fs)
        avg = max(1, f.getlength("abcdefghij") / 10)
        lines = textwrap.wrap(text, width=max(8, int(box_w / avg)))
        if len(lines) * fs * 1.3 <= h * 0.58:
            break
        fs -= 2
    f = _font(FONT_BOLD, fs)
    y = h * 0.27
    for ln in lines:
        d.text((w * 0.07 + 2, y + 2), ln, font=f, fill=(0, 0, 0, 120))
        d.text((w * 0.07, y), ln, font=f, fill=(255, 255, 255, 255))
        y += fs * 1.3
    # progress bar + counter
    d.rectangle([0, h - 8, w, h], fill=(0, 0, 0, 90))
    d.rectangle([0, h - 8, int(w * (idx + 1) / total), h], fill=accent + (255,))
    cf = _font(FONT_REG, max(14, h // 34))
    d.text((w * 0.07, h * 0.92 - h // 30), f"{idx + 1} / {total}", font=cf, fill=(255, 255, 255, 170))
    img.save(out_png)


# ---------------------------------------------------------------- main build
def generate(prompt, minutes=60, resolution="720p", out_path="video.mp4",
             narrate=True, progress=None, scene_seconds=20):
    """Build the video. progress(fraction, message) is called as work advances."""
    ff = find_ffmpeg()
    size = RES.get(resolution, RES["720p"])
    size = (size[0] // 2 * 2, size[1] // 2 * 2)
    report = progress or (lambda f, m: None)
    scenes = plan_scenes(prompt, minutes, scene_seconds)
    n = len(scenes)
    slot = max(4.0, minutes * 60 / n)
    work = tempfile.mkdtemp(prefix="vforge_")
    clips = []
    try:
        for i, (head, text) in enumerate(scenes):
            report(i / n * 0.95, f"Scene {i + 1}/{n}")
            png = os.path.join(work, f"s{i:05d}.png")
            wav = os.path.join(work, f"s{i:05d}.wav")
            clip = os.path.join(work, f"c{i:05d}.mp4")
            render_frame(head, text, i, n, size, png)
            have_voice = narrate and synthesize(text, wav)
            narr = (audio_duration(ff, wav) if have_voice else None)
            if narr is None:
                narr = len(text.split()) / 2.6
            dur = max(slot, narr + 1.0)
            cmd = [ff, "-y", "-loop", "1", "-framerate", str(FPS), "-i", png]
            if have_voice:
                cmd += ["-i", wav]
            else:
                cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"]
            fade = min(0.5, dur / 4)
            vf = f"fade=t=in:st=0:d={fade},fade=t=out:st={dur - fade:.2f}:d={fade},format=yuv420p"
            cmd += ["-t", f"{dur:.2f}", "-vf", vf, "-af", "apad,aresample=44100",
                    "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
                    "-g", str(FPS * 2), "-r", str(FPS),
                    "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-shortest", clip]
            # -shortest would cut to the shortest stream; apad + -t bounds it instead
            cmd.remove("-shortest")
            run(cmd)
            clips.append(clip)
            for tmp in (png, wav):
                if os.path.exists(tmp):
                    os.remove(tmp)
        report(0.96, "Joining scenes")
        listfile = os.path.join(work, "list.txt")
        with open(listfile, "w") as fh:
            for c in clips:
                fh.write(f"file '{c}'\n")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        run([ff, "-y", "-f", "concat", "-safe", "0", "-i", listfile,
             "-c", "copy", "-movflags", "+faststart", out_path])
        report(1.0, "Done")
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="VideoForge: one prompt -> long MP4")
    ap.add_argument("prompt")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--res", default="720p", choices=list(RES))
    ap.add_argument("--out", default="video.mp4")
    ap.add_argument("--no-voice", action="store_true")
    a = ap.parse_args()
    generate(a.prompt, a.minutes, a.res, a.out, not a.no_voice,
             lambda f, m: print(f"{f * 100:5.1f}%  {m}", flush=True))
    print("Saved", a.out)
