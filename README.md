# VideoForge

One prompt in, one long narrated MP4 out. Fully offline, no credits, no cloud models.

## Get the APK (free, ~15 min, no coding)
1. Create a free account at github.com and make a new repository.
2. Upload every file in this folder (drag and drop the unzipped contents; keep the `.github` folder).
3. Open the repo's **Actions** tab, choose **Build APK**, press **Run workflow**.
4. When it finishes, open the run and download the **VideoForge-APK** artifact. Unzip it and copy the `.apk` to your phone.
5. Allow "Install unknown apps" on the phone and install.

## Use it
Paste a prompt or full script, set minutes (60+), pick resolution, press **Generate video**.
- Short prompt: it expands into a chaptered outline to fill the time.
- Full script: each part becomes a scene; length follows the narration, never shorter than your minutes.
- Output: app's external files folder (`VideoForge/`), plus a copy in `Movies/` when permitted.

## Run on a computer instead (works today)
```
pip install pillow
# install ffmpeg and (optional, for voice) espeak-ng
python vidgen.py "Your prompt or script" --minutes 60 --res 720p --out my_video.mp4
```
