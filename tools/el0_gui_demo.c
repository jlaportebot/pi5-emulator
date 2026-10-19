/* EL0 GUI demo — animated gradient + click interactivity
 * through the SYS_WIN_* syscalls (110-113 + poll 114). */

typedef unsigned long u64;

static inline u64 syscall6(u64 n, u64 a, u64 b, u64 c, u64 d, u64 e, u64 f) {
    register u64 x8 asm("x8") = n;
    register u64 x0 asm("x0") = a;
    register u64 x1 asm("x1") = b;
    register u64 x2 asm("x2") = c;
    register u64 x3 asm("x3") = d;
    register u64 x4 asm("x4") = e;
    register u64 x5 asm("x5") = f;
    asm volatile("svc #0"
                 : "+r"(x0)
                 : "r"(x8), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5)
                 : "memory");
    return x0;
}

#define SYS_EXIT       0
#define SYS_SLEEP      7
#define SYS_WIN_CREATE 110
#define SYS_WIN_FILL   111
#define SYS_WIN_FLUSH  112
#define SYS_WIN_CLOSE  113
#define SYS_WIN_POLL   114

#define W 280
#define H 180

static const char title[] = "EL0 App";

void _start(void) {
    u64 win = syscall6(SYS_WIN_CREATE, 60, 600, W, H, (u64)title, 0);
    if (win == ~0UL) {
        syscall6(SYS_EXIT, 1, 0, 0, 0, 0, 0);
    }

    u64 hue = 0; /* click-rotated phase offset */

    for (u64 frame = 0; frame < 300; frame++) {
        for (u64 band = 0; band < 18; band++) {
            u64 phase = (band * 14 + frame * 4 + hue) & 0xFF;
            u64 r = phase;
            u64 g = (band * 8 + frame * 2 + hue) & 0xFF;
            u64 b = 255 - phase;
            u64 color = 0xFF000000u | (r << 16) | (g << 8) | b;
            syscall6(SYS_WIN_FILL, win, 0, band * (H / 18), W, H / 18 + 1, color);
        }
        syscall6(SYS_WIN_FLUSH, win, 0, 0, 0, 0, 0);

        /* Poll input: any press inside rotates the hue. */
        for (u64 k = 0; k < 8; k++) {
            u64 ev = syscall6(SYS_WIN_POLL, win, 0, 0, 0, 0, 0);
            if (ev == 0 || ev == ~0UL) break;
            if ((ev & 0xFF) == 1) {          /* press */
                hue += 64;
            }
        }

        syscall6(SYS_SLEEP, 33, 0, 0, 0, 0, 0);
    }

    syscall6(SYS_WIN_CLOSE, win, 0, 0, 0, 0, 0);
    syscall6(SYS_EXIT, 0, 0, 0, 0, 0, 0);
}
