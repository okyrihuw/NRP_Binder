@echo off
setlocal
py -m pip install --upgrade pyinstaller
py -m PyInstaller --noconfirm --clean --onefile --windowed --name "NRP Binder" main.py
if errorlevel 1 exit /b %errorlevel%
echo.
echo Готово: dist\NRP Binder.exe
pause
