[app]
title = VideoForge
package.name = videoforge
package.domain = org.videoforge
source.dir = .
source.include_exts = py,png,ttf,bin
source.include_patterns = assets/*,bin/*
source.exclude_dirs = .github,.buildozer,__pycache__
version = 1.0
requirements = python3,kivy==2.3.0,pillow,pyjnius,android
orientation = portrait
fullscreen = 0
android.permissions = WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE
# API 28 target keeps executing the bundled ffmpeg from app storage allowed
android.api = 28
android.minapi = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
