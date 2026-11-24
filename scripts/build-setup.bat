@echo off
rem Builds the Windows installer (and the app it contains) into dist\ : dist\bundle\ (what gets installed) and
rem dist\Interceptium-Setup-<version>.exe. Needs Windows, Python 3.14 and the
rem Visual Studio Build Tools ("Desktop development with C++").
setlocal
cd /d "%~dp0.."
where py >nul 2>nul && (set PY=py -3.14) || (set PY=python)
%PY% -m venv .venv || goto :error
call .venv\Scripts\activate.bat || goto :error
python -m pip install -r requirements.txt || goto :error
python -m unittest discover -s tests || goto :error
python -m unittest discover -s setup\tests || goto :error
python tools\build_windows.py || goto :error
echo.
echo Done: see dist\
exit /b 0
:error
echo Build failed.
exit /b 1
