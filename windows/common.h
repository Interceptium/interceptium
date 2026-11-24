/* Shared helpers for the Interceptium launcher and setup stub (Windows API only). */
#ifndef INTERCEPTIUM_COMMON_H
#define INTERCEPTIUM_COMMON_H

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <wchar.h>
#include <string.h>

#define PATH_CAP 32768

/* Folder that contains this program, without a trailing backslash. */
static BOOL own_dir(wchar_t *out, DWORD cap, wchar_t *exe_out, DWORD exe_cap) {
    DWORD n = GetModuleFileNameW(NULL, exe_out, exe_cap);
    if (n == 0 || n >= exe_cap) return FALSE;
    wcsncpy(out, exe_out, cap - 1);
    out[cap - 1] = 0;
    wchar_t *slash = wcsrchr(out, L'\\');
    if (!slash) return FALSE;
    *slash = 0;
    return TRUE;
}

/* The arguments this program was started with, minus the program name itself. */
static const wchar_t *forwarded_args(void) {
    const wchar_t *p = GetCommandLineW();
    if (*p == L'"') {
        p++;
        while (*p && *p != L'"') p++;
        if (*p == L'"') p++;
    } else {
        while (*p && *p != L' ' && *p != L'\t') p++;
    }
    while (*p == L' ' || *p == L'\t') p++;
    return p;
}

/* Start `python` on `script` with our arguments, wait, and return its exit code (or -1). */
static int run_python(const wchar_t *python, const wchar_t *script, const wchar_t *workdir, const wchar_t *args) {
    size_t len = wcslen(python) + wcslen(script) + wcslen(args) + 64;
    wchar_t *cmd = (wchar_t *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, len * sizeof(wchar_t));
    if (!cmd) return -1;
    /* -I: isolated (ignores PYTHON* variables and the user site folder); -B: don't write .pyc files */
    swprintf(cmd, len, L"\"%ls\" -I -B \"%ls\" %ls", python, script, args);
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    ZeroMemory(&pi, sizeof pi);
    BOOL ok = CreateProcessW(python, cmd, NULL, NULL, FALSE, 0, NULL, workdir, &si, &pi);
    HeapFree(GetProcessHeap(), 0, cmd);
    if (!ok) return -1;
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = (DWORD)-1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return (int)code;
}

#endif
