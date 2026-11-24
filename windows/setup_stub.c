/* Interceptium Setup stub.
 *
 * The setup's files (the official Python runtime, Interceptium and the setup
 * program) are stored after the end of this .exe in a plain, uncompressed list:
 *
 *     repeated:  u32 name length | UTF-8 name (forward slashes) | u64 size | bytes
 *     trailer:   u64 list offset | u64 list size | "INTCPTM1"
 *
 * The stub copies them into a fresh folder under %TEMP%, runs the setup with the
 * bundled pythonw.exe (signed by the Python Software Foundation), waits for it,
 * then deletes that folder. It writes nowhere else and contacts nothing.
 */
#include "common.h"
#include <stdint.h>

static const char MAGIC[8] = {'I', 'N', 'T', 'C', 'P', 'T', 'M', '1'};
#define CHUNK (1u << 20)

static BOOL quiet_mode(void) {
    return wcsstr(GetCommandLineW(), L"--quiet") != NULL;
}

static int fail(const wchar_t *message) {
    if (!quiet_mode()) MessageBoxW(NULL, message, L"Interceptium Setup", MB_ICONERROR);
    return 2;
}

static BOOL read_exact(HANDLE f, void *buf, DWORD size) {
    DWORD got = 0;
    return ReadFile(f, buf, size, &got, NULL) && got == size;
}

static BOOL seek_to(HANDLE f, uint64_t pos) {
    LARGE_INTEGER li;
    li.QuadPart = (LONGLONG)pos;
    return SetFilePointerEx(f, li, NULL, FILE_BEGIN);
}

/* Names must stay inside the target folder: relative, no "..", no drive letters. */
static BOOL safe_name(const char *name) {
    if (!name[0] || name[0] == '/' || name[0] == '\\' || strchr(name, ':') || strchr(name, '\\')) return FALSE;
    const char *seg = name;
    for (const char *p = name;; p++) {
        if (*p == '/' || *p == 0) {
            size_t n = (size_t)(p - seg);
            if (n == 0 || (n == 2 && seg[0] == '.' && seg[1] == '.') || (n == 1 && seg[0] == '.')) return FALSE;
            if (*p == 0) break;
            seg = p + 1;
        }
    }
    return TRUE;
}

static void make_parents(wchar_t *path) {
    for (wchar_t *p = path + 3; *p; p++) {  /* skip "C:\" */
        if (*p == L'\\') {
            *p = 0;
            CreateDirectoryW(path, NULL);
            *p = L'\\';
        }
    }
}

static BOOL delete_tree(const wchar_t *dir) {
    static wchar_t pattern[PATH_CAP];
    swprintf(pattern, PATH_CAP, L"%ls\\*", dir);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pattern, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!wcscmp(fd.cFileName, L".") || !wcscmp(fd.cFileName, L"..")) continue;
            size_t len = wcslen(dir) + wcslen(fd.cFileName) + 2;
            wchar_t *child = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, len * sizeof(wchar_t));
            if (!child) break;
            swprintf(child, len, L"%ls\\%ls", dir, fd.cFileName);
            if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && !(fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
                delete_tree(child);
            } else {
                SetFileAttributesW(child, FILE_ATTRIBUTE_NORMAL);
                DeleteFileW(child);
            }
            HeapFree(GetProcessHeap(), 0, child);
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    return RemoveDirectoryW(dir);
}

static int unpack(HANDLE f, uint64_t offset, uint64_t size, const wchar_t *target) {
    static wchar_t out_path[PATH_CAP], wide_name[PATH_CAP];
    char *buf = (char *)HeapAlloc(GetProcessHeap(), 0, CHUNK);
    if (!buf) return 0;
    uint64_t pos = offset, end = offset + size;
    int ok = 1;
    while (ok && pos < end) {
        uint32_t name_len;
        uint64_t data_len;
        char name[1024];
        if (!seek_to(f, pos) || !read_exact(f, &name_len, 4) || name_len == 0 || name_len >= sizeof name ||
            !read_exact(f, name, name_len)) { ok = 0; break; }
        name[name_len] = 0;
        if (!read_exact(f, &data_len, 8) || !safe_name(name)) { ok = 0; break; }
        if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, name, -1, wide_name, PATH_CAP)) { ok = 0; break; }
        for (wchar_t *p = wide_name; *p; p++) if (*p == L'/') *p = L'\\';
        swprintf(out_path, PATH_CAP, L"%ls\\%ls", target, wide_name);
        make_parents(out_path);
        HANDLE o = CreateFileW(out_path, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
        if (o == INVALID_HANDLE_VALUE) { ok = 0; break; }
        uint64_t left = data_len;
        while (left && ok) {
            DWORD want = left > CHUNK ? CHUNK : (DWORD)left, wrote = 0;
            if (!read_exact(f, buf, want) || !WriteFile(o, buf, want, &wrote, NULL) || wrote != want) ok = 0;
            left -= want;
        }
        CloseHandle(o);
        pos += 4 + name_len + 8 + data_len;
    }
    HeapFree(GetProcessHeap(), 0, buf);
    return ok && pos == end;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR cmdline, int show) {
    (void)inst; (void)prev; (void)cmdline; (void)show;
    static wchar_t dir[PATH_CAP], exe[PATH_CAP], temp[PATH_CAP], work[PATH_CAP], python[PATH_CAP], script[PATH_CAP];
    if (!own_dir(dir, PATH_CAP, exe, PATH_CAP)) return fail(L"Could not locate the setup file.");

    HANDLE f = CreateFileW(exe, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return fail(L"Could not open the setup file.");
    LARGE_INTEGER total;
    char magic[8];
    uint64_t offset = 0, size = 0;
    BOOL good = GetFileSizeEx(f, &total) && total.QuadPart > 24 && seek_to(f, (uint64_t)total.QuadPart - 24) &&
                read_exact(f, &offset, 8) && read_exact(f, &size, 8) && read_exact(f, magic, 8) &&
                memcmp(magic, MAGIC, 8) == 0 && offset + size + 24 == (uint64_t)total.QuadPart;
    if (!good) { CloseHandle(f); return fail(L"This setup file is damaged or incomplete. Download it again."); }

    DWORD n = GetTempPathW(PATH_CAP, temp);
    if (n == 0 || n >= PATH_CAP) { CloseHandle(f); return fail(L"No temporary folder is available."); }
    swprintf(work, PATH_CAP, L"%lsInterceptiumSetup-%lu", temp, GetCurrentProcessId());
    delete_tree(work);
    if (!CreateDirectoryW(work, NULL)) { CloseHandle(f); return fail(L"Could not create a temporary folder."); }

    int unpacked = unpack(f, offset, size, work);
    CloseHandle(f);
    if (!unpacked) { delete_tree(work); return fail(L"This setup file is damaged or incomplete. Download it again."); }

    swprintf(python, PATH_CAP, L"%ls\\runtime\\pythonw.exe", work);
    swprintf(script, PATH_CAP, L"%ls\\app\\setup\\run_setup.py", work);
    SetEnvironmentVariableW(L"INTERCEPTIUM_SETUP_EXE", exe);
    SetEnvironmentVariableW(L"INTERCEPTIUM_BUNDLE", work);
    int code = run_python(python, script, work, forwarded_args());

    for (int i = 0; i < 20 && !delete_tree(work); i++) Sleep(250);  /* files can stay locked briefly */
    return code < 0 ? fail(L"Could not start the setup.") : code;
}
