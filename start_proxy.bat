@echo off
title LiteLLM Proxy for Claude Code (NVIDIA NIM)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
echo ===================================================
echo Starting LiteLLM Proxy on http://127.0.0.1:4000 ...
echo Direct connection to NVIDIA NIM (build.nvidia.com)
echo ===================================================
litellm --config "%~dp0litellm_config.yaml" --port 4000 --host 127.0.0.1
pause
