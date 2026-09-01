import subprocess
import sys
import os

def start_litellm():
    config_path = os.path.join(os.path.dirname(__file__), "litellm_config.yaml")
    cmd = [
        sys.executable,
        "-m",
        "litellm",
        "--config",
        config_path,
        "--port",
        "4000",
        "--host",
        "127.0.0.1"
    ]
    print(f"Starting LiteLLM Proxy on http://127.0.0.1:4000 using config {config_path}...")
    subprocess.run(cmd)

if __name__ == "__main__":
    start_litellm()
