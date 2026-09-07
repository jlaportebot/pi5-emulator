/*
 * LobsterOS QEMU Input Device — MMIO ring buffer for guest input events
 *
 * Provides a simple memory-mapped ring buffer that captures mouse and
 * keyboard events from the QEMU display layer and exposes them to the
 * guest kernel for polling. This avoids the need for a full USB/XHCI
 * emulation stack on the raspi5b machine.
 *
 * MMIO Layout (32-bit registers, little-endian):
 *   0x00  MAGIC       (read-only)  — 0x4C4F4231 ("LOB1")
 *   0x04  VERSION     (read-only)  — 0x00010000 (v1.0)
 *   0x08  BUF_SIZE    (read-only)  — ring capacity (32)
 *   0x0C  HEAD        (read-only)  — next slot to read (guest)
 *   0x10  TAIL        (read-only)  — next slot to write (host)
 *   0x14  EVENT_SIZE  (read-only)  — 16 bytes per event slot
 *   0x18  CLEAR       (write-only) — write any value to reset HEAD=TAIL
 *   0x20+ EVENT_DATA  — 32 × 16-byte event slots, starting here
 *
 * Event slot format (each 16 bytes):
 *   [0]    type  : 1=mouse_move, 2=mouse_btn, 3=key_press, 4=key_release
 *   [1]    flags : bit0=shift, bit1=ctrl, bit2=alt, bit3=gui
 *   [2..3] x/y   : mouse x/y (absolute, 0..0x7FFF range, scaled)
 *   [4..5] dx/dy : mouse delta (for move events)
 *   [6]    btn   : bit0=left, bit1=right, bit2=middle
 *   [8]    key   : USB HID scancode (for key events)
 *   [9..15] reserved
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
#include "ui/console.h"
#include "ui/input.h"

#define TYPE_LOBSTER_INPUT "lobster-input"
OBJECT_DECLARE_SIMPLE_TYPE(LobsterInputState, LOBSTER_INPUT)

#define LOBSTER_INPUT_MAGIC   0x4C4F4231u  /* "LOB1" */
#define LOBSTER_INPUT_VERSION 0x00010000u  /* 1.0 */
#define LOBSTER_EVENT_COUNT   32
#define LOBSTER_EVENT_SIZE    16
#define LOBSTER_MMIO_SIZE     (0x20 + LOBSTER_EVENT_COUNT * LOBSTER_EVENT_SIZE)

/* Event types */
#define EV_MOUSE_MOVE   1
#define EV_MOUSE_BTN    2
#define EV_KEY_PRESS     3
#define EV_KEY_RELEASE   4

/* Modifier flag bits */
#define MOD_SHIFT  0x01
#define MOD_CTRL   0x02
#define MOD_ALT    0x04
#define MOD_GUI    0x08

typedef struct {
    uint8_t  type;
    uint8_t  flags;
    uint16_t x;
    uint16_t y;
    int16_t  dx;
    int16_t  dy;
    uint8_t  btn;
    uint8_t  key;
    uint8_t  reserved[6];
} __attribute__((packed)) LobsterEvent;

struct LobsterInputState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;

    /* Ring buffer */
    LobsterEvent events[LOBSTER_EVENT_COUNT];
    uint32_t head;  /* guest read index */
    uint32_t tail;  /* host write index */

    /* Current absolute mouse position (QEMU coordinate space) */
    int abs_x;
    int abs_y;
    int max_x;
    int max_y;

    /* Modifier state */
    uint8_t modifiers;

    /* QEMU input handler state */
    QemuInputHandlerState *mouse_handler;
    QemuInputHandlerState *kbd_handler;
};

/* ─── Ring buffer helpers ────────────────────────────────────────── */

static void push_event(LobsterInputState *s, LobsterEvent ev)
{
    uint32_t next_tail = (s->tail + 1) % LOBSTER_EVENT_COUNT;
    if (next_tail == s->head) {
        /* Buffer full — overwrite oldest by advancing head */
        s->head = (s->head + 1) % LOBSTER_EVENT_COUNT;
    }
    s->events[s->tail] = ev;
    s->tail = next_tail;
}

/* ─── QEMU input handler callbacks ──────────────────────────────── */

static void lobster_mouse_event(DeviceState *dev, QemuConsole *src,
                                InputEvent *evt)
{
    LobsterInputState *s = LOBSTER_INPUT(dev);

    fprintf(stderr, "[lobster-input] Mouse event received: type=%d\n", evt->type);

    switch (evt->type) {
    case INPUT_EVENT_KIND_REL: {
        InputMoveEvent *move = evt->u.rel.data;
        if (move->axis == INPUT_AXIS_X) {
            s->abs_x += move->value;
            if (s->abs_x < 0) s->abs_x = 0;
            if (s->max_x > 0 && s->abs_x > s->max_x) s->abs_x = s->max_x;
        } else if (move->axis == INPUT_AXIS_Y) {
            s->abs_y += move->value;
            if (s->abs_y < 0) s->abs_y = 0;
            if (s->max_y > 0 && s->abs_y > s->max_y) s->abs_y = s->max_y;
        }
        break;
    }
    case INPUT_EVENT_KIND_ABS: {
        InputMoveEvent *move = evt->u.abs.data;
        if (move->axis == INPUT_AXIS_X) {
            s->abs_x = qemu_input_scale_axis(move->value,
                INPUT_EVENT_ABS_MIN, INPUT_EVENT_ABS_MAX, 0, s->max_x);
        } else if (move->axis == INPUT_AXIS_Y) {
            s->abs_y = qemu_input_scale_axis(move->value,
                INPUT_EVENT_ABS_MIN, INPUT_EVENT_ABS_MAX, 0, s->max_y);
        }
        break;
    }
    case INPUT_EVENT_KIND_BTN: {
        InputBtnEvent *btn = evt->u.btn.data;
        LobsterEvent ev = {0};

        /* Get current button state */
        static uint8_t btn_state = 0;
        if (btn->down) {
            if (btn->button == INPUT_BUTTON_LEFT)   btn_state |= 0x01;
            if (btn->button == INPUT_BUTTON_RIGHT)  btn_state |= 0x02;
            if (btn->button == INPUT_BUTTON_MIDDLE) btn_state |= 0x04;
        } else {
            if (btn->button == INPUT_BUTTON_LEFT)   btn_state &= ~0x01;
            if (btn->button == INPUT_BUTTON_RIGHT)  btn_state &= ~0x02;
            if (btn->button == INPUT_BUTTON_MIDDLE) btn_state &= ~0x04;
        }

        fprintf(stderr, "[lobster-input] Button event: down=%d btn=%d state=0x%x\n", btn->down, btn->button, btn_state);

        ev.type = EV_MOUSE_BTN;
        ev.flags = s->modifiers;
        ev.x = (uint16_t)(s->abs_x & 0xFFFF);
        ev.y = (uint16_t)(s->abs_y & 0xFFFF);
        ev.btn = btn_state;
        push_event(s, ev);
        break;
    }
    default:
        break;
    }
}

static void lobster_mouse_sync(DeviceState *dev)
{
    /* Send a move event on sync to update cursor position */
    LobsterInputState *s = LOBSTER_INPUT(dev);
    LobsterEvent ev = {0};
    ev.type = EV_MOUSE_MOVE;
    ev.flags = s->modifiers;
    ev.x = (uint16_t)(s->abs_x & 0xFFFF);
    ev.y = (uint16_t)(s->abs_y & 0xFFFF);
    ev.dx = 0;
    ev.dy = 0;
    push_event(s, ev);
}

static void lobster_kbd_event(DeviceState *dev, QemuConsole *src,
                             InputEvent *evt)
{
    LobsterInputState *s = LOBSTER_INPUT(dev);

    fprintf(stderr, "[lobster-input] Keyboard event received: type=%d\n", evt->type);

    if (evt->type != INPUT_EVENT_KIND_KEY) {
        return;
    }

    InputKeyEvent *key = evt->u.key.data;
    int qcode = qemu_input_key_value_to_qcode(key->key);

    fprintf(stderr, "[lobster-input] Key event: qcode=%d down=%d\n", qcode, key->down);

    /* Track modifier state */
    switch (qcode) {
    case Q_KEY_CODE_SHIFT:
    case Q_KEY_CODE_SHIFT_R:
        if (key->down) s->modifiers |= MOD_SHIFT; else s->modifiers &= ~MOD_SHIFT;
        break;
    case Q_KEY_CODE_CTRL:
    case Q_KEY_CODE_CTRL_R:
        if (key->down) s->modifiers |= MOD_CTRL; else s->modifiers &= ~MOD_CTRL;
        break;
    case Q_KEY_CODE_ALT:
    case Q_KEY_CODE_ALT_R:
        if (key->down) s->modifiers |= MOD_ALT; else s->modifiers &= ~MOD_ALT;
        break;
    case Q_KEY_CODE_META_R:
    case Q_KEY_CODE_META_L:
        if (key->down) s->modifiers |= MOD_GUI; else s->modifiers &= ~MOD_GUI;
        break;
    default:
        break;
    }

    /* Convert QKeyCode to USB HID scancode */
    uint8_t hid_scancode = 0;
    /* USB HID Usage Table (Page 10) — scancodes for standard keys */
    static const uint8_t qcode_to_hid[Q_KEY_CODE__MAX] = {
        [Q_KEY_CODE_ESC] = 0x29,
        [Q_KEY_CODE_1] = 0x1E,
        [Q_KEY_CODE_2] = 0x1F,
        [Q_KEY_CODE_3] = 0x20,
        [Q_KEY_CODE_4] = 0x21,
        [Q_KEY_CODE_5] = 0x22,
        [Q_KEY_CODE_6] = 0x23,
        [Q_KEY_CODE_7] = 0x24,
        [Q_KEY_CODE_8] = 0x25,
        [Q_KEY_CODE_9] = 0x26,
        [Q_KEY_CODE_0] = 0x27,
        [Q_KEY_CODE_MINUS] = 0x2D,
        [Q_KEY_CODE_EQUAL] = 0x2E,
        [Q_KEY_CODE_BACKSPACE] = 0x2A,
        [Q_KEY_CODE_TAB] = 0x2B,
        [Q_KEY_CODE_Q] = 0x14,
        [Q_KEY_CODE_W] = 0x1A,
        [Q_KEY_CODE_E] = 0x08,
        [Q_KEY_CODE_R] = 0x15,
        [Q_KEY_CODE_T] = 0x17,
        [Q_KEY_CODE_Y] = 0x2C,
        [Q_KEY_CODE_U] = 0x18,
        [Q_KEY_CODE_I] = 0x0C,
        [Q_KEY_CODE_O] = 0x12,
        [Q_KEY_CODE_P] = 0x13,
        [Q_KEY_CODE_BRACKET_LEFT] = 0x2F,
        [Q_KEY_CODE_BRACKET_RIGHT] = 0x30,
        [Q_KEY_CODE_RET] = 0x28,
        [Q_KEY_CODE_CTRL] = 0xE0,
        [Q_KEY_CODE_A] = 0x04,
        [Q_KEY_CODE_S] = 0x16,
        [Q_KEY_CODE_D] = 0x07,
        [Q_KEY_CODE_F] = 0x09,
        [Q_KEY_CODE_G] = 0x0A,
        [Q_KEY_CODE_H] = 0x0B,
        [Q_KEY_CODE_J] = 0x0D,
        [Q_KEY_CODE_K] = 0x0E,
        [Q_KEY_CODE_L] = 0x0F,
        [Q_KEY_CODE_SEMICOLON] = 0x33,
        [Q_KEY_CODE_APOSTROPHE] = 0x34,
        [Q_KEY_CODE_GRAVE_ACCENT] = 0x35,
        [Q_KEY_CODE_SHIFT] = 0xE1,
        [Q_KEY_CODE_BACKSLASH] = 0x31,
        [Q_KEY_CODE_Z] = 0x1D,
        [Q_KEY_CODE_X] = 0x1B,
        [Q_KEY_CODE_C] = 0x06,
        [Q_KEY_CODE_V] = 0x19,
        [Q_KEY_CODE_B] = 0x05,
        [Q_KEY_CODE_N] = 0x10,
        [Q_KEY_CODE_M] = 0x11,
        [Q_KEY_CODE_COMMA] = 0x36,
        [Q_KEY_CODE_DOT] = 0x37,
        [Q_KEY_CODE_SLASH] = 0x38,
        [Q_KEY_CODE_SHIFT_R] = 0xE5,
        [Q_KEY_CODE_KP_MULTIPLY] = 0x55,
        [Q_KEY_CODE_ALT] = 0xE2,
        [Q_KEY_CODE_SPC] = 0x2C,
        [Q_KEY_CODE_CAPS_LOCK] = 0x39,
        [Q_KEY_CODE_F1] = 0x3A,
        [Q_KEY_CODE_F2] = 0x3B,
        [Q_KEY_CODE_F3] = 0x3C,
        [Q_KEY_CODE_F4] = 0x3D,
        [Q_KEY_CODE_F5] = 0x3E,
        [Q_KEY_CODE_F6] = 0x3F,
        [Q_KEY_CODE_F7] = 0x40,
        [Q_KEY_CODE_F8] = 0x41,
        [Q_KEY_CODE_F9] = 0x42,
        [Q_KEY_CODE_F10] = 0x43,
        [Q_KEY_CODE_NUM_LOCK] = 0x53,
        [Q_KEY_CODE_SCROLL_LOCK] = 0x47,
        [Q_KEY_CODE_KP_DIVIDE] = 0x54,
        [Q_KEY_CODE_CTRL_R] = 0xE4,
        [Q_KEY_CODE_KP_ADD] = 0x57,
        [Q_KEY_CODE_KP_ENTER] = 0x58,
        [Q_KEY_CODE_KP_1] = 0x59,
        [Q_KEY_CODE_KP_2] = 0x5A,
        [Q_KEY_CODE_KP_3] = 0x5B,
        [Q_KEY_CODE_KP_4] = 0x5C,
        [Q_KEY_CODE_KP_5] = 0x5D,
        [Q_KEY_CODE_KP_6] = 0x5E,
        [Q_KEY_CODE_KP_7] = 0x5F,
        [Q_KEY_CODE_KP_8] = 0x60,
        [Q_KEY_CODE_KP_9] = 0x61,
        [Q_KEY_CODE_KP_0] = 0x62,
        [Q_KEY_CODE_KP_DECIMAL] = 0x63,
        [Q_KEY_CODE_LESS] = 0x64,
        [Q_KEY_CODE_F11] = 0x44,
        [Q_KEY_CODE_F12] = 0x45,
        [Q_KEY_CODE_ALT_R] = 0xE6,
        [Q_KEY_CODE_LEFT] = 0x50,
        [Q_KEY_CODE_DOWN] = 0x51,
        [Q_KEY_CODE_RIGHT] = 0x52,
        [Q_KEY_CODE_UP] = 0x4F,
        [Q_KEY_CODE_META_L] = 0xE3,
        [Q_KEY_CODE_META_R] = 0xE7,
        [Q_KEY_CODE_DELETE] = 0x4C,
        [Q_KEY_CODE_HOME] = 0x4A,
        [Q_KEY_CODE_END] = 0x4D,
        [Q_KEY_CODE_PGUP] = 0x4B,
        [Q_KEY_CODE_PGDN] = 0x4E,
        [Q_KEY_CODE_INSERT] = 0x49,
    };

    if (qcode >= 0 && qcode < Q_KEY_CODE__MAX) {
        hid_scancode = qcode_to_hid[qcode];
    }

    if (hid_scancode == 0) {
        return; /* unmapped key, skip */
    }

    LobsterEvent ev = {0};
    ev.type = key->down ? EV_KEY_PRESS : EV_KEY_RELEASE;
    ev.flags = s->modifiers;
    ev.key = hid_scancode;
    push_event(s, ev);
}

static void lobster_kbd_sync(DeviceState *dev)
{
    /* Nothing to do on sync for keyboard */
}

static const QemuInputHandler lobster_mouse_handler = {
    .name  = "Lobster Mouse",
    .mask  = INPUT_EVENT_MASK_BTN | INPUT_EVENT_MASK_REL | INPUT_EVENT_MASK_ABS,
    .event = lobster_mouse_event,
    .sync  = lobster_mouse_sync,
};

static const QemuInputHandler lobster_kbd_handler = {
    .name  = "Lobster Keyboard",
    .mask  = INPUT_EVENT_MASK_KEY,
    .event = lobster_kbd_event,
    .sync  = lobster_kbd_sync,
};

/* ─── MMIO read/write handlers ──────────────────────────────────── */

static uint64_t lobster_mmio_read(void *opaque, hwaddr offset, unsigned size)
{
    LobsterInputState *s = LOBSTER_INPUT(opaque);

    switch (offset) {
    case 0x00: return LOBSTER_INPUT_MAGIC;
    case 0x04: return LOBSTER_INPUT_VERSION;
    case 0x08: return LOBSTER_EVENT_COUNT;
    case 0x0C: return s->head;
    case 0x10: return s->tail;
    case 0x14: return LOBSTER_EVENT_SIZE;
    case 0x18: return s->modifiers;
    default: {
        /* Event data region starts at 0x20 */
        if (offset >= 0x20) {
            uint32_t slot = (offset - 0x20) / LOBSTER_EVENT_SIZE;
            uint32_t byte_off = (offset - 0x20) % LOBSTER_EVENT_SIZE;
            if (slot < LOBSTER_EVENT_COUNT) {
                /* Return raw bytes from the event slot */
                const uint8_t *data = (const uint8_t *)&s->events[slot];
                uint64_t val = 0;
                for (unsigned i = 0; i < size && (byte_off + i) < LOBSTER_EVENT_SIZE; i++) {
                    val |= ((uint64_t)data[byte_off + i]) << (i * 8);
                }
                return val;
            }
        }
        return 0;
    }
    }
}

static void lobster_mmio_write(void *opaque, hwaddr offset,
                               uint64_t val, unsigned size)
{
    LobsterInputState *s = LOBSTER_INPUT(opaque);

    switch (offset) {
    case 0x18: /* CLEAR — reset head=tail */
        s->head = 0;
        s->tail = 0;
        break;
    default:
        break;
    }
}

static const MemoryRegionOps lobster_mmio_ops = {
    .read = lobster_mmio_read,
    .write = lobster_mmio_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1,
    .valid.max_access_size = 8,
};

/* ─── Device realization ────────────────────────────────────────── */

static void lobster_input_realize(DeviceState *dev, Error **errp)
{
    LobsterInputState *s = LOBSTER_INPUT(dev);

    /* Initialize ring buffer */
    s->head = 0;
    s->tail = 0;
    s->abs_x = 960;   /* Center of 1920 wide */
    s->abs_y = 540;   /* Center of 1080 tall */
    s->max_x = 1919;
    s->max_y = 1079;
    s->modifiers = 0;

    /* Register input handlers with QEMU's display layer */
    s->mouse_handler = qemu_input_handler_register(dev, &lobster_mouse_handler);
    s->kbd_handler = qemu_input_handler_register(dev, &lobster_kbd_handler);
    qemu_input_handler_activate(s->mouse_handler);
    qemu_input_handler_activate(s->kbd_handler);

    /* Bind to the first graphical console by directly setting the handler state.
     * We use the new qemu_input_handler_bind_console() function which takes
     * a QemuConsole* directly. */
    QemuConsole *con = qemu_console_lookup_by_index(0);
    if (con) {
        qemu_input_handler_bind_console(s->mouse_handler, con);
        qemu_input_handler_bind_console(s->kbd_handler, con);
        fprintf(stderr, "[lobster-input] Bound to console index 0\n");
    } else {
        fprintf(stderr, "[lobster-input] No graphical console found at index 0\n");
    }
}

static void lobster_input_unrealize(DeviceState *dev)
{
    LobsterInputState *s = LOBSTER_INPUT(dev);

    if (s->mouse_handler) {
        qemu_input_handler_unregister(s->mouse_handler);
        s->mouse_handler = NULL;
    }
    if (s->kbd_handler) {
        qemu_input_handler_unregister(s->kbd_handler);
        s->kbd_handler = NULL;
    }
}

static void lobster_input_init(Object *obj)
{
    LobsterInputState *s = LOBSTER_INPUT(obj);

    memory_region_init_io(&s->mmio, obj, &lobster_mmio_ops,
                          s, "lobster-input", LOBSTER_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
}

static void lobster_input_class_init(ObjectClass *oc, void *data)
{
    DeviceClass *dc = DEVICE_CLASS(oc);
    dc->realize = lobster_input_realize;
    dc->unrealize = lobster_input_unrealize;
    dc->user_creatable = true;
}

static const TypeInfo lobster_input_info = {
    .name = TYPE_LOBSTER_INPUT,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(LobsterInputState),
    .instance_init = lobster_input_init,
    .class_init = lobster_input_class_init,
};

static void lobster_input_register_type(void)
{
    type_register_static(&lobster_input_info);
}

type_init(lobster_input_register_type)
