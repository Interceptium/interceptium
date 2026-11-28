@echo off
rem Builds Interceptium for Windows. Needs Rust (https://rustup.rs) with the MSVC toolchain
rem and the Visual Studio Build Tools ("Desktop development with C++").
rem Output: dist\Interceptium-Setup-1.0.2.exe (the app, which is also its own setup).
setlocal
cd /d "%~dp0\.."
cargo build --release --locked || exit /b 1
if not exist dist mkdir dist
for /f "tokens=3 delims= " %%v in ('findstr /b "version" Cargo.toml') do set VERSION=%%~v& goto :got
:got
copy /y target\release\interceptium.exe "dist\Interceptium-Setup-%VERSION%.exe" >nul || exit /b 1
echo Built dist\Interceptium-Setup-%VERSION%.exe
