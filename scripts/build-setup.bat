@echo off
rem Builds only the installer. Put Interceptium.exe in dist\ first.
setlocal
cd /d "%~dp0.."
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -m venv .venv || goto :error
call .venv\Scripts\activate.bat || goto :error
python -m pip install -r requirements-build.txt || goto :error
python -m unittest discover -s setup\tests || goto :error
python setup\build_setup.py || goto :error
exit /b 0
:error
echo Build failed.
exit /b 1
