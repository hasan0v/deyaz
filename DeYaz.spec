# -*- mode: python ; coding: utf-8 -*-

import sys


version = "1.0.12"
is_windows = sys.platform == "win32"
is_macos = sys.platform == "darwin"
icon = (
    "assets/deyaz.ico" if is_windows else
    "assets/deyaz.icns" if is_macos else
    "assets/deyaz-logo.png"
)
hiddenimports = [
    "credential_store", "openrouter_oauth", "meeting_capture",
    "realtime_transcription", "websocket", "soundcard", "keyring.backends",
]
if is_windows:
    hiddenimports.append("soundcard.mediafoundation")

a = Analysis(
    ["deyaz_app.py"],
    pathex=[],
    binaries=[],
    datas=[("assets", "assets")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    [],
    name="DeYaz",
    exclude_binaries=True,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX-packed binaries are smaller but disproportionately trigger heuristic
    # antivirus detections. Release a conventional executable instead.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
    version="version_info.txt" if is_windows else None,
)

distribution = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DeYaz",
)

if is_macos:
    app = BUNDLE(
        distribution,
        name="DeYaz.app",
        icon=icon,
        bundle_identifier="io.github.hasan0v.deyaz",
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSMicrophoneUsageDescription": "DeYaz danışığı mətnə çevirmək üçün mikrofondan istifadə edir.",
            "NSHighResolutionCapable": True,
        },
    )
