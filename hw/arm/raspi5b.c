/*
 * Raspberry Pi 5B emulation
 *
 * Copyright (C) 2025 Mister Lobster
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/units.h"
#include "qemu/cutils.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "hw/arm/raspi_platform.h"
#include "hw/registerfields.h"
#include "qemu/error-report.h"
#include "system/device_tree.h"
#include "hw/boards.h"
#include "hw/loader.h"
#include "hw/arm/boot.h"
#include "qom/object.h"
#include "hw/arm/bcm2712.h"
#include "hw/char/pl011.h"
#include "chardev/char-fe.h"
#include "system/system.h"
#include <libfdt.h>
#include <string.h>

#define TYPE_RASPI5B_MACHINE MACHINE_TYPE_NAME("raspi5b")
OBJECT_DECLARE_SIMPLE_TYPE(Raspi5bMachineState, RASPI5B_MACHINE)

struct Raspi5bMachineState {
    RaspiBaseMachineState parent_obj;
    BCM2712State soc;
    void *fdt;
    int fdt_size;
};

/* Memory map for Pi 5:
 * 0x00000000 - 0x3fffffff: Low memory (1GB)
 * 0x40000000 - 0xffffffff: High memory (3GB for 4GB model)
 * 0xf0000000 - 0xffffffff: Peripherals
 */

/* Add second memory node if board RAM exceeds 1GB */
static int raspi5_add_memory_node(void *fdt, hwaddr mem_base, hwaddr mem_len)
{
    int ret;
    uint32_t acells, scells;
    char *nodename = g_strdup_printf("/memory@%" PRIx64, mem_base);

    acells = qemu_fdt_getprop_cell(fdt, "/", "#address-cells",
                                   NULL, &error_fatal);
    scells = qemu_fdt_getprop_cell(fdt, "/", "#size-cells",
                                   NULL, &error_fatal);
    if (acells == 0 || scells == 0) {
        fprintf(stderr, "dtb file invalid (#address-cells or #size-cells 0)\n");
        ret = -1;
    } else {
        qemu_fdt_add_subnode(fdt, nodename);
        qemu_fdt_setprop_string(fdt, nodename, "device_type", "memory");
        ret = qemu_fdt_setprop_sized_cells(fdt, nodename, "reg",
                                           acells, mem_base,
                                           scells, mem_len);
    }

    g_free(nodename);
    return ret;
}

static void raspi5_modify_dtb(const struct arm_boot_info *info, void *fdt)
{
    uint64_t ram_size;

    /* Disable devices not yet implemented */
    const char *nodes_to_remove[] = {
        "brcm,bcm2712-pcie",
        "brcm,bcm2712-rng200",
        "brcm,bcm2712-thermal",
    };

    for (int i = 0; i < ARRAY_SIZE(nodes_to_remove); i++) {
        const char *dev_str = nodes_to_remove[i];

        int offset = fdt_node_offset_by_compatible(fdt, -1, dev_str);
        if (offset >= 0) {
            if (!fdt_nop_node(fdt, offset)) {
                warn_report("bcm2712 dtc: %s has been disabled!", dev_str);
            }
        }
    }

    ram_size = board_ram_size(info->board_id);

    if (info->ram_size > 0x40000000) {
        raspi5_add_memory_node(fdt, 0x40000000, ram_size - 0x40000000);
    }
}

static void raspi5b_machine_init(MachineState *machine)
{
    Raspi5bMachineState *s = RASPI5B_MACHINE(machine);
    RaspiBaseMachineState *s_base = RASPI_BASE_MACHINE(machine);
    RaspiBaseMachineClass *mc = RASPI_BASE_MACHINE_GET_CLASS(machine);
    BCM2712State *soc = &s->soc;

    s_base->binfo.modify_dtb = raspi5_modify_dtb;
    s_base->binfo.board_id = mc->board_rev;

    object_initialize_child(OBJECT(machine), "soc", soc,
                            board_soc_type(mc->board_rev));

    /* RP1 UART0 left unconnected by default (use -serial for second port if needed) */

    raspi_base_machine_init(machine, (BCM283XBaseState *)soc);

    /* For bare-metal kernels (no ARM64 Linux magic header), write a proper
     * bootloader using the Linux bootloader_aarch64 which sets up registers
     * and jumps to the kernel. This handles fixups correctly. */
    if (machine->kernel_filename) {
        gsize len;
        gchar *buf;
        bool has_linux_magic = false;
        if (g_file_get_contents(machine->kernel_filename, &buf, &len, NULL)) {
            if (len > 60 && memcmp(buf + 56, "ARM\x64", 4) == 0) {
                has_linux_magic = true;
            }
            /* Also check for gzip magic (compressed kernel) */
            if (!has_linux_magic && len > 2 && buf[0] == 0x1f && buf[1] == 0x8b) {
                has_linux_magic = true;
            }
            g_free(buf);
        }

        if (!has_linux_magic) {
            /* Bare-metal kernel: write Linux-compatible bootloader at address 0.
             * This uses the same bootloader_aarch64 as Linux, which properly
             * sets up x0=DTB, x1-3=0, x4=kernel_entry, and branches to x4. */
            hwaddr dtb_addr = s_base->binfo.dtb_start;
            hwaddr real_entry = s_base->binfo.entry;
            if (real_entry == 0) {
                real_entry = 0x80000; /* KERNEL64_LOAD_ADDR for raw AArch64 images */
            }

            /* Override entry to 0 so CPU starts at our bootloader at address 0 */
            s_base->binfo.entry = 0;

            /* Set up fixup context for bootloader_aarch64 */
            uint32_t fixupcontext[FIXUP_MAX];
            fixupcontext[FIXUP_ARGPTR_LO] = (uint32_t)dtb_addr;
            fixupcontext[FIXUP_ARGPTR_HI] = (uint32_t)(dtb_addr >> 32);
            fixupcontext[FIXUP_ENTRYPOINT_LO] = (uint32_t)real_entry;
            fixupcontext[FIXUP_ENTRYPOINT_HI] = (uint32_t)(real_entry >> 32);

            /* Use the same bootloader as Linux (bootloader_aarch64 from hw/arm/boot.c) */
            static const ARMInsnFixup bootloader_aarch64[] = {
                { 0x580000c0 }, /* ldr x0, arg ; Load the lower 32-bits of DTB */
                { 0xaa1f03e1 }, /* mov x1, xzr */
                { 0xaa1f03e2 }, /* mov x2, xzr */
                { 0xaa1f03e3 }, /* mov x3, xzr */
                { 0x58000084 }, /* ldr x4, entry ; Load the lower 32-bits of kernel entry */
                { 0xd61f0080 }, /* br x4      ; Jump to the kernel entry point */
                { 0, FIXUP_ARGPTR_LO }, /* arg: .word @DTB Lower 32-bits */
                { 0, FIXUP_ARGPTR_HI }, /* .word @DTB Higher 32-bits */
                { 0, FIXUP_ENTRYPOINT_LO }, /* entry: .word @Kernel Entry Lower 32-bits */
                { 0, FIXUP_ENTRYPOINT_HI }, /* .word @Kernel Entry Higher 32-bits */
                { 0, FIXUP_TERMINATOR }
            };

            arm_write_bootloader("baremetal-bootloader", &address_space_memory, 0,
                                 bootloader_aarch64, fixupcontext);
        }
    }
}

static void raspi5b_machine_class_init(ObjectClass *oc, void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);
    RaspiBaseMachineClass *rmc = RASPI_BASE_MACHINE_CLASS(oc);

    /* Default to 4GB model */
    rmc->board_rev = RPI5_BOARD_REV_4G;
    raspi_machine_class_common_init(mc, rmc->board_rev);
    mc->desc = "Raspberry Pi 5B (revision 1.0)";
    mc->auto_create_sdcard = true;
    mc->init = raspi5b_machine_init;
}

static const TypeInfo raspi5b_machine_type = {
    .name           = TYPE_RASPI5B_MACHINE,
    .parent         = TYPE_RASPI_BASE_MACHINE,
    .instance_size  = sizeof(Raspi5bMachineState),
    .class_init     = raspi5b_machine_class_init,
};

static void raspi5b_machine_register_type(void)
{
    type_register_static(&raspi5b_machine_type);
}

type_init(raspi5b_machine_register_type)