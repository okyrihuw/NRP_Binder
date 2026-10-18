@echo off
setlocal
py -m pip install --upgrade pyinstaller
py -m PyInstaller --noconfirm --clean --onefile --windowed --name "Выдача" main.py
if errorlevel 1 exit /b %errorlevel%
echo.
echo Готово: dist\Выдача.exe
pause
