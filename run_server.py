import os
import subprocess
import sys
from pathlib import Path

port = os.environ.get("PORT", "8501")
app_path = Path(__file__).resolve().parent / "app.py"

sys.exit(
    subprocess.call(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(app_path),
            "--server.headless",
            "true",
            "--server.port",
            port,
        ]
    )
)
