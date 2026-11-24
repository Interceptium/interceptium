/* Interceptium.exe: starts the bundled, Python Software Foundation-signed pythonw.exe
 * with Interceptium's code and passes on any arguments. Nothing else. */
#include "common.h"

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR cmdline, int show) {
    (void)inst; (void)prev; (void)cmdline; (void)show;
    static wchar_t dir[PATH_CAP], exe[PATH_CAP], python[PATH_CAP], script[PATH_CAP];
    if (!own_dir(dir, PATH_CAP, exe, PATH_CAP)) return 1;
    swprintf(python, PATH_CAP, L"%ls\\runtime\\pythonw.exe", dir);
    swprintf(script, PATH_CAP, L"%ls\\app\\run_interceptium.py", dir);
    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES || GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL, L"Interceptium's files are missing. Run Repair from Interceptium Setup.",
                    L"Interceptium", MB_ICONERROR);
        return 1;
    }
    SetEnvironmentVariableW(L"INTERCEPTIUM_INSTALL_DIR", dir);
    int code = run_python(python, script, dir, forwarded_args());
    return code < 0 ? 1 : code;
}
