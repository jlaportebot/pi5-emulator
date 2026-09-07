/*
 * LobsterOS QEMU Network Device — MMIO-based HTTP proxy + C compiler
 *
 * Provides a simple memory-mapped interface that lets the guest kernel
 * request HTTP fetches via the host's curl binary, and compile C source
 * via the host's aarch64-linux-gnu-gcc cross-compiler. This avoids needing
 * a full network stack or compiler toolchain on the emulated system.
 *
 * Protocol for HTTP fetch:
 *   1. Guest writes URL bytes to DATA region (offset 0x100+)
 *   2. Guest writes URL length to URL_LEN register (0x20)
 *   3. Guest writes 1 to CMD register (0x24) to fetch
 *   4. Device runs curl on the host, stores response in shared buffer
 *   5. Guest polls STATUS register (0x28): 0=idle, 1=fetching, 2=done, 3=error
 *   6. Guest reads RESP_LEN (0x2C) for body length, RESP_CODE (0x30) for HTTP code
 *   7. Guest reads body from RESP_DATA region (offset 0x1000+)
 *   8. Guest writes 2 to CMD to reset (clear buffers)
 *
 * Protocol for C compilation:
 *   1. Guest writes C source bytes to DATA region (offset 0x100+)
 *   2. Guest writes source length to URL_LEN register (0x20)
 *   3. Guest writes 3 to CMD register (0x24) to compile
 *   4. Device writes source to temp file, runs aarch64-linux-gnu-gcc,
 *      reads resulting ELF binary into response buffer
 *   5. Guest polls STATUS register (0x28): 0=idle, 1=compiling, 2=done, 3=error
 *   6. Guest reads RESP_LEN (0x2C) for ELF size, RESP_CODE for 0=ok / 1=compile-error
 *   7. Guest reads ELF from RESP_DATA region (offset 0x1000+)
 *
 * MMIO Layout (32-bit registers):
 *   0x00  MAGIC        (read-only)  — 0x4C4E4554 ("LNET")
 *   0x04  VERSION      (read-only)  — 0x00020000 (v1.1 — added compile)
 *   0x08  BUF_SIZE     (read-only)  — total MMIO size
 *   0x0C  RESERVED
 *   0x10  URL_OFFSET   (read-only)  — 0x100 (where source/URL data starts)
 *   0x14  RESP_OFFSET  (read-only)  — 0x1000 (where response data starts)
 *   0x18  URL_MAX      (read-only)  — 3840 (0xF00 bytes between offset 0x100 and 0x1000)
 *   0x1C  RESP_MAX     (read-only)  — 65536
 *   0x20  URL_LEN      (read/write) — number of bytes written to source/URL region
 *   0x24  CMD          (write-only) — 1=fetch, 2=reset, 3=compile
 *   0x28  STATUS       (read-only)  — 0=idle, 1=busy, 2=done, 3=error
 *   0x2C  RESP_LEN     (read-only)  — response body length
 *   0x30  RESP_CODE    (read-only)  — HTTP code (fetch) or 0=ok/1=error (compile)
 *   0x34  AUTH_MODE    (read/write) — 0=none, 1=Bearer token auth. When set,
 *                                     url_buf is "<token>\n<url>". Token is
 *                                     restricted to [A-Za-z0-9._-] and passed
 *                                     to curl as: -H "Authorization: Bearer X"
 *   0x100+   SRC_DATA  (write)      — URL bytes or C source (up to 3840)
 *   0x1000+  RESP_DATA  (read)       — response body or compiled ELF (up to 65536)
 *
 * Copyright (C) 2026 Mister Lobster
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qapi/error.h"
#include "hw/sysbus.h"
#include "hw/qdev-properties.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include <unistd.h>
#include <ctype.h>
#include <sys/wait.h>

#define TYPE_LOBSTER_NET "lobster-net"
OBJECT_DECLARE_SIMPLE_TYPE(LobsterNetState, LOBSTER_NET)

#define LOBSTER_NET_MAGIC       0x4C4E4554u  /* "LNET" */
#define LOBSTER_NET_VERSION     0x00030000u  /* v1.2 — added Bearer-token auth for HTTP fetch */
#define LOBSTER_URL_MAX         3840          /* 0xF00: space between 0x100 and 0x1000 */
#define LOBSTER_RESP_MAX        524288        /* 512KB for MicroPython binaries */
#define LOBSTER_URL_OFFSET      0x100
#define LOBSTER_RESP_OFFSET     0x1000
#define LOBSTER_MMIO_SIZE       (LOBSTER_RESP_OFFSET + LOBSTER_RESP_MAX)

/* Status codes */
#define NET_STATUS_IDLE         0
#define NET_STATUS_FETCHING     1   /* also used for "compiling" */
#define NET_STATUS_DONE        2
#define NET_STATUS_ERROR        3

/* Commands */
#define NET_CMD_FETCH           1
#define NET_CMD_RESET           2
#define NET_CMD_COMPILE         3

struct LobsterNetState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;

    /* URL/source buffer */
    uint8_t url_buf[LOBSTER_URL_MAX];
    uint32_t url_len;

    /* Response buffer */
    uint8_t resp_buf[LOBSTER_RESP_MAX];
    uint32_t resp_len;
    uint32_t resp_code;

    /* Status */
    uint32_t status;

    /* Auth mode: 0 = none, 1 = Bearer token (carried as "<token>\n<url>"
     * in url_buf). Lets the guest make authenticated API calls (e.g. GitHub). */
    uint32_t auth_mode;
};

/* ─── Host-side HTTP fetch via curl ─────────────────────────────── */

static void lobster_net_fetch(LobsterNetState *s)
{
    char cmd[8192];
    char url[LOBSTER_URL_MAX + 1];
    char token[768] = {0};
    const char *url_ptr = (const char *)s->url_buf;
    uint32_t url_len = s->url_len;
    int has_auth = 0;

    /* When auth_mode is set, the buffer is "<token>\n<url>". Split on the
     * first newline so we only ever append a fixed-format header to curl.
     * Token characters are restricted to [A-Za-z0-9._-] to prevent any
     * shell metacharacter from leaking into the popen() command line. */
    if (s->auth_mode == 1) {
        uint32_t nl = 0;
        while (nl < s->url_len && s->url_buf[nl] != '\n') {
            nl++;
        }
        if (nl < s->url_len && nl > 0) {
            uint32_t tlen = nl < sizeof(token) - 1 ? nl : sizeof(token) - 1;
            uint32_t i;
            for (i = 0; i < tlen; i++) {
                unsigned char c = s->url_buf[i];
                if (!(isalnum(c) || c == '_' || c == '-' || c == '.')) {
                    break;  /* reject token with metacharacters */
                }
            }
            if (i == tlen) {
                memcpy(token, s->url_buf, tlen);
                url_ptr += nl + 1;
                url_len = s->url_len - nl - 1;
                has_auth = 1;
            }
        }
        /* reset auth so the next fetch is anon unless the guest re-sets it */
        s->auth_mode = 0;
    }

    /* Null-terminate URL */
    if (url_len >= sizeof(url)) {
        url_len = sizeof(url) - 1;
    }
    memcpy(url, url_ptr, url_len);
    url[url_len] = '\0';

    /* Validate URL starts with http */
    if (s->url_len < 7 || (strncmp(url, "http://", 7) != 0 &&
                            strncmp(url, "https://", 8) != 0)) {
        s->status = NET_STATUS_ERROR;
        s->resp_code = 0;
        s->resp_len = 0;
        return;
    }

    /* Build curl command: silent, show response code, follow redirects,
     * write body to stdout, max-time 10s. Add optional auth header. */
    if (has_auth) {
        snprintf(cmd, sizeof(cmd),
                 "curl -s -o /dev/stdout -w '\\n%%{http_code}'"
                 " -L --max-time 10 -H 'Authorization: Bearer %s' '%s' 2>/dev/null",
                 token, url);
    } else {
        snprintf(cmd, sizeof(cmd),
                 "curl -s -o /dev/stdout -w '\\n%%{http_code}'"
                 " -L --max-time 10 '%s' 2>/dev/null",
                 url);
    }

    FILE *fp = popen(cmd, "r");
    if (!fp) {
        s->status = NET_STATUS_ERROR;
        s->resp_code = 0;
        s->resp_len = 0;
        return;
    }

    /* Read response body + trailing HTTP code */
    size_t total = fread(s->resp_buf, 1, LOBSTER_RESP_MAX - 16, fp);
    pclose(fp);

    /* The last line is the HTTP status code (curl -w format) */
    s->resp_len = total;
    s->resp_code = 200;  /* default */

    /* Find the last newline — everything after it is the status code */
    if (total > 0) {
        int i;
        for (i = total - 1; i >= 0; i--) {
            if (s->resp_buf[i] == '\n') {
                break;
            }
        }
        if (i >= 0 && i < (int)total - 1) {
            /* Parse HTTP code from the last line */
            char code_str[16] = {0};
            int code_start = i + 1;
            int code_len = total - code_start;
            if (code_len > 0 && code_len < 16) {
                memcpy(code_str, &s->resp_buf[code_start], code_len);
                code_str[code_len] = '\0';
                s->resp_code = (uint32_t)atoi(code_str);
                s->resp_len = code_start;  /* body is everything before the code */
            }
        }
    }

    s->status = NET_STATUS_DONE;

    fprintf(stderr, "[lobster-net] Fetched %s: HTTP %u, %u bytes\n",
            url, s->resp_code, s->resp_len);
}

/* ─── Host-side C compilation via aarch64-linux-gnu-gcc ─────────── */

static void lobster_net_compile(LobsterNetState *s)
{
    char src_path[] = "/tmp/lobster_cc_XXXXXX.c";
    char out_path[] = "/tmp/lobster_cc_XXXXXX.elf";
    char err_path[] = "/tmp/lobster_cc_XXXXXX.err";
    char cmd[1024];
    int src_fd, out_fd, err_fd;

    /* Create temp files: mkstemps handles suffix after XXXXXX */
    src_fd = mkstemps(src_path, 2);  /* suffix ".c" = 2 chars */
    if (src_fd < 0) {
        fprintf(stderr, "[lobster-net] compile: cannot create temp .c file (errno=%d)\n", errno);
        s->status = NET_STATUS_ERROR;
        s->resp_code = 1;
        s->resp_len = 0;
        return;
    }

    /* Write source code to temp file */
    ssize_t written = write(src_fd, s->url_buf, s->url_len);
    close(src_fd);
    if (written != (ssize_t)s->url_len) {
        fprintf(stderr, "[lobster-net] compile: short write to source file\n");
        s->status = NET_STATUS_ERROR;
        s->resp_code = 1;
        s->resp_len = 0;
        return;
    }

    fprintf(stderr, "[lobster-net] compile: wrote %u bytes to %s\n",
            s->url_len, src_path);

    /* Create temp output and error paths */
    out_fd = mkstemps(out_path, 4);  /* suffix ".elf" = 4 chars */
    err_fd = mkstemps(err_path, 4);  /* suffix ".err" = 4 chars */
    if (out_fd < 0 || err_fd < 0) {
        fprintf(stderr, "[lobster-net] compile: cannot create temp output files (out_fd=%d, err_fd=%d, errno=%d)\n",
                out_fd, err_fd, errno);
        s->status = NET_STATUS_ERROR;
        s->resp_code = 1;
        s->resp_len = 0;
        unlink(src_path);
        if (out_fd >= 0) close(out_fd);
        if (err_fd >= 0) close(err_fd);
        return;
    }
    close(out_fd);
    close(err_fd);

    /* Build cross-compiler command:
     * -ffreestanding: no hosted environment, no libc
     * -nostdlib: no standard library link
     * -static: fully static binary
     * -Wl,-Ttext=0x10000000: place text at USER_CODE_START
     * -Wl,--build-id=none: no build-id section
     * -Wl,--nmagic: minimize file alignment (no 64KB page gaps)
     * -O2: optimize for size
     * The resulting binary is a static AArch64 ELF that runs at EL0
     */
    snprintf(cmd, sizeof(cmd),
             "aarch64-linux-gnu-gcc"
             " -ffreestanding -nostdlib -static -O2 -nostartfiles"
             " -ffunction-sections -fdata-sections"
             " -Wl,-Ttext=0x10000000"
             " -Wl,--build-id=none"
             " -Wl,--nmagic"
             " -Wl,--gc-sections"
             " -I/mnt/data/sd-overflow/LobsterOS/lobster-os/tools/libc/include"
             " -L/mnt/data/sd-overflow/LobsterOS/lobster-os/tools/libc"
             " -o %s %s -llobsterc 2>%s",
             out_path, src_path, err_path);

    fprintf(stderr, "[lobster-net] compile: running: %s\n", cmd);

    int ret = system(cmd);

    if (ret != 0) {
        /* Compilation failed — read error and put it in response buffer */
        FILE *efp = fopen(err_path, "r");
        if (efp) {
            size_t err_len = fread(s->resp_buf, 1, LOBSTER_RESP_MAX - 1, efp);
            fclose(efp);
            s->resp_buf[err_len] = '\0';
            s->resp_len = err_len;
        }
        s->resp_code = 1;
        s->status = NET_STATUS_ERROR;

        fprintf(stderr, "[lobster-net] compile: FAILED (ret=%d)\n", ret);
        /* Print error to QEMU stderr too */
        if (s->resp_len > 0) {
            fprintf(stderr, "[lobster-net] compile error: %s\n",
                    (char *)s->resp_buf);
        }
    } else {
        /* Compilation succeeded — read ELF binary into response buffer */
        FILE *ofp = fopen(out_path, "rb");
        if (!ofp) {
            s->status = NET_STATUS_ERROR;
            s->resp_code = 1;
            s->resp_len = 0;
        } else {
            size_t elf_size = fread(s->resp_buf, 1, LOBSTER_RESP_MAX, ofp);
            fclose(ofp);
            s->resp_len = elf_size;
            s->resp_code = 0;
            s->status = NET_STATUS_DONE;

            fprintf(stderr, "[lobster-net] compile: OK, ELF %u bytes\n",
                    s->resp_len);
        }
    }

    /* Cleanup temp files */
    unlink(src_path);
    unlink(out_path);
    unlink(err_path);
}

/* ─── MMIO read/write handlers ──────────────────────────────────── */

static uint64_t lobster_net_mmio_read(void *opaque, hwaddr offset, unsigned size)
{
    LobsterNetState *s = LOBSTER_NET(opaque);

    switch (offset) {
    case 0x00: return LOBSTER_NET_MAGIC;
    case 0x04: return LOBSTER_NET_VERSION;
    case 0x08: return LOBSTER_MMIO_SIZE;
    case 0x0C: return 0;
    case 0x10: return LOBSTER_URL_OFFSET;
    case 0x14: return LOBSTER_RESP_OFFSET;
    case 0x18: return LOBSTER_URL_MAX;
    case 0x1C: return LOBSTER_RESP_MAX;
    case 0x20: return s->url_len;
    case 0x28: return s->status;
    case 0x2C: return s->resp_len;
    case 0x30: return s->resp_code;
    case 0x34: return s->auth_mode;
    default: {
        /* URL/source data region */
        if (offset >= LOBSTER_URL_OFFSET && offset < LOBSTER_RESP_OFFSET) {
            uint32_t idx = offset - LOBSTER_URL_OFFSET;
            if (idx + size <= LOBSTER_URL_MAX) {
                uint64_t val = 0;
                memcpy(&val, &s->url_buf[idx], size);
                return val;
            }
        }
        /* Response data region */
        if (offset >= LOBSTER_RESP_OFFSET) {
            uint32_t idx = offset - LOBSTER_RESP_OFFSET;
            if (idx + size <= LOBSTER_RESP_MAX) {
                uint64_t val = 0;
                memcpy(&val, &s->resp_buf[idx], size);
                return val;
            }
        }
        return 0;
    }
    }
}

static void lobster_net_mmio_write(void *opaque, hwaddr offset,
                                   uint64_t val, unsigned size)
{
    LobsterNetState *s = LOBSTER_NET(opaque);

    switch (offset) {
    case 0x20: /* URL_LEN */
        s->url_len = (uint32_t)val;
        if (s->url_len > LOBSTER_URL_MAX) {
            s->url_len = LOBSTER_URL_MAX;
        }
        break;

    case 0x24: /* CMD */
        switch ((uint32_t)val) {
        case NET_CMD_FETCH:
            s->status = NET_STATUS_FETCHING;
            lobster_net_fetch(s);
            break;
        case NET_CMD_RESET:
            s->status = NET_STATUS_IDLE;
            s->url_len = 0;
            s->resp_len = 0;
            s->resp_code = 0;
            s->auth_mode = 0;
            memset(s->url_buf, 0, LOBSTER_URL_MAX);
            memset(s->resp_buf, 0, LOBSTER_RESP_MAX);
            break;
        case NET_CMD_COMPILE:
            s->status = NET_STATUS_FETCHING;
            lobster_net_compile(s);
            break;
        default:
            break;
        }
        break;

    case 0x30:
    case 0x34: /* AUTH_MODE — 0=none, 1=bearer */
        s->auth_mode = (uint32_t)val & 1;
        break;

    default: {
        /* URL/source data region */
        if (offset >= LOBSTER_URL_OFFSET && offset < LOBSTER_RESP_OFFSET) {
            uint32_t idx = offset - LOBSTER_URL_OFFSET;
            if (idx + size <= LOBSTER_URL_MAX) {
                memcpy(&s->url_buf[idx], &val, size);
            }
        }
        break;
    }
    }
}

static const MemoryRegionOps lobster_net_mmio_ops = {
    .read = lobster_net_mmio_read,
    .write = lobster_net_mmio_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1,
    .valid.max_access_size = 8,
};

/* ─── Device realization ────────────────────────────────────────── */

static void lobster_net_init(Object *obj)
{
    LobsterNetState *s = LOBSTER_NET(obj);

    memory_region_init_io(&s->mmio, obj, &lobster_net_mmio_ops,
                          s, "lobster-net", LOBSTER_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);

    s->url_len = 0;
    s->resp_len = 0;
    s->resp_code = 0;
    s->status = NET_STATUS_IDLE;
    s->auth_mode = 0;
    memset(s->url_buf, 0, LOBSTER_URL_MAX);
    memset(s->resp_buf, 0, LOBSTER_RESP_MAX);
}

static void lobster_net_class_init(ObjectClass *oc, void *data)
{
    DeviceClass *dc = DEVICE_CLASS(oc);
    dc->user_creatable = true;
}

static const TypeInfo lobster_net_info = {
    .name = TYPE_LOBSTER_NET,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(LobsterNetState),
    .instance_init = lobster_net_init,
    .class_init = lobster_net_class_init,
};

static void lobster_net_register_type(void)
{
    type_register_static(&lobster_net_info);
}

type_init(lobster_net_register_type)
