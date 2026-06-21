/*
 * BCM2712 SoC emulation (Raspberry Pi 5)
 *
 * Copyright (C) 2026 Hermes Agent
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef BCM2712_H
#define BCM2712_H

#include "hw/arm/bcm2836.h"
#include "hw/arm/bcm2712_peripherals.h"
#include "hw/intc/arm_gic.h"

/* BCM2712 Peripheral base addresses */
#define BCM2712_PERI_BASE 0x7c000000
#define BCM2712_PERI_SIZE 0x04000000 /* 64MB */

/* GIC-400 base (at SCB bus address space) */
#define BCM2712_GIC_BASE 0x107fff9000ULL
#define BCM2712_GIC_DIST_BASE (BCM2712_GIC_BASE + 0x0000)
#define BCM2712_GIC_CPU_BASE (BCM2712_GIC_BASE + 0x1000)
#define BCM2712_GIC_HYPERVISOR_BASE (BCM2712_GIC_BASE + 0x2000)
#define BCM2712_GIC_VIRTUAL_BASE (BCM2712_GIC_BASE + 0x3000)

/* Number of external interrupt lines for GIC-400 */
#define GIC_NUM_IRQS 512

/* GIC-400 PPI/SGI definitions */
#define PPI(cpu, irq) (GIC_NUM_IRQS + (cpu) * GIC_INTERNAL + GIC_NR_SGIS + irq)

/* VIRTUAL PMU IRQ */
#define VIRTUAL_PMU_IRQ 7

/* Raspberry Pi 5 board revision encoding */
#define RPI5_BOARD_REV_2G 0xc03111 /* Pi 5 2GB */
#define RPI5_BOARD_REV_4G 0xc83111 /* Pi 5 4GB */
#define RPI5_BOARD_REV_8G 0xd03111 /* Pi 5 8GB */
#define RPI5_BOARD_REV_16G 0xd83111 /* Pi 5 16GB */

#define TYPE_BCM2712 "bcm2712"

OBJECT_DECLARE_TYPE(BCM2712State, BCM2712Class, BCM2712)

struct BCM2712State {
    /*< private >*/
    BCM283XBaseState parent_obj;
    /*< public >*/
    BCM2712PeripheralState peripherals;
    GICState gic;
    MemoryRegion sysbus_mr; /* For SCB bus mapping */
};

struct BCM2712Class {
    /*< private >*/
    BCM283XBaseClass parent_class;
    /*< public >*/
};

#endif /* BCM2712_H */