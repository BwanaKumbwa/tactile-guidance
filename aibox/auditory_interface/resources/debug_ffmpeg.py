import os
import shutil
import subprocess

print("PATH from Python:\n", os.environ.get("PATH", ""), "\n")

print("shutil.which('ffmpeg'):", shutil.which("ffmpeg"))

try:
    print("\nRunning: ffmpeg -version\n")
    subprocess.run(["ffmpeg", "-version"], check=True)
    print("\nffmpeg ran successfully from Python.")
except FileNotFoundError as e:
    print("\n[ERROR] FileNotFoundError:", e)
except Exception as e:
    print("\n[ERROR] Other exception:", repr(e))