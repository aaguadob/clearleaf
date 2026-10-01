# PyInstaller recipe for the desktop app:  pyinstaller packaging/clearleaf.spec
# macOS gives dist/clearleaf.app, Windows dist/clearleaf/clearleaf.exe.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

HERE = Path(SPECPATH)
VERSION = "0.1.0"
MAC = sys.platform == "darwin"

a = Analysis(
    [str(HERE / "clearleaf_app.py")],
    datas=collect_data_files("clearleaf"),  # the editor page, bundled fonts, CJK maps
    excludes=["tkinter", "numpy", "PIL", "pymupdf", "fitz", "pytest", "playwright"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="clearleaf",
    console=False,
    argv_emulation=MAC,  # a PDF opened from the Finder arrives as an argument
    icon=str(HERE / ("icon.icns" if MAC else "icon.ico")),
)
coll = COLLECT(exe, a.binaries, a.datas, name="clearleaf")

if MAC:
    app = BUNDLE(
        coll,
        name="clearleaf.app",
        icon=str(HERE / "icon.icns"),
        bundle_identifier="io.github.aaguadob.clearleaf",
        version=VERSION,
        info_plist={
            "CFBundleName": "clearleaf",
            "CFBundleDisplayName": "clearleaf",
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "MIT License",
            # appears under "Open With" for PDFs, without taking over as the default viewer
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "PDF document",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["com.adobe.pdf"],
            }],
        },
    )
