@echo off
REM One-time setup for Windows. Double-click this file, or run it from cmd.
cd /d "%~dp0"
echo == creating virtualenv ==
py -3 -m venv .venv || python -m venv .venv
call .venv\Scripts\activate.bat
echo == installing dependencies ==
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
echo == downloading ffmpeg (about 100MB, first time only) ==
python -c "import static_ffmpeg; static_ffmpeg.add_paths()"
echo.
echo Done. Next, in this same window:
echo     .venv\Scripts\activate
echo     set ANTHROPIC_API_KEY=sk-ant-...
echo     python -m vlogai.studio --port 8900
pause
