# PyInstaller runtime hook (runs before the app's own code in a packaged build).
# In a macOS .app bundle PyInstaller splits OpenCV's files between Contents/Frameworks and
# Contents/Resources, and cv2's Python bootstrap then fails with "recursion is detected during
# loading of cv2 binary extensions". Loading the compiled module directly avoids that bootstrap;
# the app only uses OpenCV's core functions, which all live in the compiled module.
import glob
import importlib.machinery
import importlib.util
import os
import sys


def _load_native_cv2():
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return
    roots = [base, os.path.join(os.path.dirname(base), "Resources")]
    for root in roots:
        for path in glob.glob(os.path.join(root, "cv2", "cv2*.so")) + glob.glob(os.path.join(root, "cv2", "cv2*.pyd")):
            loader = importlib.machinery.ExtensionFileLoader("cv2", path)
            spec = importlib.util.spec_from_file_location("cv2", path, loader=loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            sys.modules["cv2"] = mod
            return


if sys.platform == "darwin" or os.environ.get("LEDWALL_NATIVE_CV2"):
    _load_native_cv2()
