# -*- mode: python ; coding: utf-8 -*-
# PyInstaller : pyinstaller --noconfirm snappy.spec
import os

datas = []
if os.path.isdir("ffmpeg"):
    datas.append(("ffmpeg", "ffmpeg"))

icon = "assets/snappy.ico" if os.path.isfile("assets/snappy.ico") else None

a = Analysis(
    ["snappy/__main__.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=["soundcard.mediafoundation", "comtypes", "psutil", "snappy.locales.fr"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtQuick"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Snappy",
    console=False,
    icon=icon,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Snappy")
