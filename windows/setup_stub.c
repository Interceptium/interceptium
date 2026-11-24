/* Interceptium Setup stub.
 *
 * The setup's files (the official Python runtime, Interceptium and the setup
 * program) are stored after the end of this .exe as a list:
 *
 *     repeated:  u32 name length | UTF-8 name (forward slashes) | u64 size | bytes
 *     trailer:   u64 block offset | u64 block size | "INTCPTM1" (list stored as is)
 *                                                  | "INTCPTM2" (list compressed with
 *                                                    Windows' built-in LZMS, Cabinet API)
 *
 * The stub copies them into a fresh folder under %TEMP%, runs the setup with the
 * bundled pythonw.exe (signed by the Python Software Foundation), waits for it,
 * then deletes that folder. It writes nowhere else and contacts nothing.
 */
#include "common.h"
#include <compressapi.h>
#include <stdint.h>

static const char MAGIC_PLAIN[8] = {'I', 'N', 'T', 'C', 'P', 'T', 'M', '1'};
static const char MAGIC_LZMS[8] = {'I', 'N', 'T', 'C', 'P', 'T', 'M', '2'};
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

static BOOL write_all(HANDLE o, const unsigned char *data, uint64_t size) {
    while (size) {
        DWORD want = size > CHUNK ? CHUNK : (DWORD)size, wrote = 0;
        if (!WriteFile(o, data, want, &wrote, NULL) || wrote != want) return FALSE;
        data += want;
        size -= want;
    }
    return TRUE;
}

/* Write every file in the (uncompressed) list `list` below `target`. */
static int unpack(const unsigned char *list, uint64_t size, const wchar_t *target) {
    static wchar_t out_path[PATH_CAP], wide_name[PATH_CAP];
    uint64_t pos = 0;
    while (pos < size) {
        uint32_t name_len;
        uint64_t data_len;
        char name[1024];
        if (size - pos < 4) return 0;
        memcpy(&name_len, list + pos, 4);
        if (name_len == 0 || name_len >= sizeof name || size - pos - 4 < (uint64_t)name_len + 8) return 0;
        memcpy(name, list + pos + 4, name_len);
        name[name_len] = 0;
        memcpy(&data_len, list + pos + 4 + name_len, 8);
        uint64_t start = pos + 12 + name_len;
        if (!safe_name(name) || data_len > size - start) return 0;
        if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, name, -1, wide_name, PATH_CAP)) return 0;
        for (wchar_t *p = wide_name; *p; p++) if (*p == L'/') *p = L'\\';
        swprintf(out_path, PATH_CAP, L"%ls\\%ls", target, wide_name);
        make_parents(out_path);
        HANDLE o = CreateFileW(out_path, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
        if (o == INVALID_HANDLE_VALUE) return 0;
        BOOL ok = write_all(o, list + start, data_len);
        CloseHandle(o);
        if (!ok) return 0;
        pos = start + data_len;
    }
    return pos == size;
}

/* Read the stored block; if compressed, expand it with Windows' own LZMS decompressor. */
static unsigned char *load_list(HANDLE f, uint64_t offset, uint64_t size, BOOL compressed, uint64_t *out_size) {
    HANDLE heap = GetProcessHeap();
    if (size == 0 || size > ((uint64_t)1 << 31)) return NULL;
    unsigned char *block = (unsigned char *)HeapAlloc(heap, 0, (SIZE_T)size);
    if (!block) return NULL;
    uint64_t got = 0;
    BOOL ok = seek_to(f, offset);
    while (ok && got < size) {
        DWORD want = size - got > CHUNK ? CHUNK : (DWORD)(size - got);
        ok = read_exact(f, block + got, want);
        got += want;
    }
    if (!ok) { HeapFree(heap, 0, block); return NULL; }
    if (!compressed) { *out_size = size; return block; }

    DECOMPRESSOR_HANDLE d = NULL;
    unsigned char *list = NULL;
    SIZE_T needed = 0;
    if (CreateDecompressor(COMPRESS_ALGORITHM_LZMS, NULL, &d)) {
        Decompress(d, block, (SIZE_T)size, NULL, 0, &needed);  /* asks for the output size */
        if (needed && needed <= ((SIZE_T)1 << 31)) list = (unsigned char *)HeapAlloc(heap, 0, needed);
        if (list && !Decompress(d, block, (SIZE_T)size, list, needed, &needed)) {
            HeapFree(heap, 0, list);
            list = NULL;
        }
        CloseDecompressor(d);
    }
    HeapFree(heap, 0, block);
    *out_size = needed;
    return list;
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
                (memcmp(magic, MAGIC_PLAIN, 8) == 0 || memcmp(magic, MAGIC_LZMS, 8) == 0) &&
                offset + size + 24 == (uint64_t)total.QuadPart;
    if (!good) { CloseHandle(f); return fail(L"This setup file is damaged or incomplete. Download it again."); }
    uint64_t list_size = 0;
    unsigned char *list = load_list(f, offset, size, memcmp(magic, MAGIC_LZMS, 8) == 0, &list_size);
    CloseHandle(f);
    if (!list) return fail(L"This setup file is damaged or incomplete. Download it again.");

    DWORD n = GetTempPathW(PATH_CAP, temp);
    if (n == 0 || n >= PATH_CAP) { HeapFree(GetProcessHeap(), 0, list); return fail(L"No temporary folder is available."); }
    swprintf(work, PATH_CAP, L"%lsInterceptiumSetup-%lu", temp, GetCurrentProcessId());
    delete_tree(work);
    if (!CreateDirectoryW(work, NULL)) {
        HeapFree(GetProcessHeap(), 0, list);
        return fail(L"Could not create a temporary folder.");
    }

    int unpacked = unpack(list, list_size, work);
    HeapFree(GetProcessHeap(), 0, list);
    if (!unpacked) { delete_tree(work); return fail(L"This setup file is damaged or incomplete. Download it again."); }

    swprintf(python, PATH_CAP, L"%ls\\runtime\\pythonw.exe", work);
    swprintf(script, PATH_CAP, L"%ls\\app\\setup\\run_setup.py", work);
    SetEnvironmentVariableW(L"INTERCEPTIUM_SETUP_EXE", exe);
    SetEnvironmentVariableW(L"INTERCEPTIUM_BUNDLE", work);
    int code = run_python(python, script, work, forwarded_args());

    for (int i = 0; i < 20 && !delete_tree(work); i++) Sleep(250);  /* files can stay locked briefly */
    return code < 0 ? fail(L"Could not start the setup.") : code;
}
