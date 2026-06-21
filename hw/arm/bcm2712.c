/*
 * BCM2712 SoC emulation (Raspberry Pi 5)
 *
 * Copyright (C) 2026 Hermes Agent
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/module.h"
#include "hw/arm/bcm2712.h"
#include "hw/sysbus.h"
#include "hw/intc/arm_gic.h"
#include "hw/arm/bcm2712_peripherals.h"
#include "hw/boards.h"
#include "trace.h"
#include "target/arm/cpu.h"

/* GIC-400 interrupt assignments from Linux device tree */
#define GIC_SPI_INTERRUPT_MBOX 33
#define GIC_SPI_INTERRUPT_TIMER0 64
#define GIC_SPI_INTERRUPT_TIMER1 65
#define GIC_SPI_INTERRUPT_TIMER2 66
#define GIC_SPI_INTERRUPT_TIMER3 67
#define GIC_SPI_INTERRUPT_PISP_BE 72
#define GIC_SPI_INTERRUPT_DWC2 73
#define GIC_SPI_INTERRUPT_DMA0 80
#define GIC_SPI_INTERRUPT_DMA1 81
#define GIC_SPI_INTERRUPT_DMA2 82
#define GIC_SPI_INTERRUPT_DMA3 83
#define GIC_SPI_INTERRUPT_DMA4 84
#define GIC_SPI_INTERRUPT_DMA5 85
#define GIC_SPI_INTERRUPT_DMA6 86
#define GIC_SPI_INTERRUPT_DMA7 87
#define GIC_SPI_INTERRUPT_DMA8 88
#define GIC_SPI_INTERRUPT_DMA9 89
#define GIC_SPI_INTERRUPT_DMA10 90
#define GIC_SPI_INTERRUPT_DMA11 91
#define GIC_SPI_INTERRUPT_L2_INT_V3D 97
#define GIC_SPI_INTERRUPT_VIDEO_DECODE 98
#define GIC_SPI_INTERRUPT_PIXELVALVE0 101
#define GIC_SPI_INTERRUPT_PIXELVALVE1 110
#define GIC_SPI_INTERRUPT_I2C 117
#define GIC_SPI_INTERRUPT_SPI 118
#define GIC_SPI_INTERRUPT_SDHOST 120
#define GIC_SPI_INTERRUPT_UART0 121
#define GIC_SPI_INTERRUPT_PCIE0_MSI 213
#define GIC_SPI_INTERRUPT_PCIE0_INTA 209
#define GIC_SPI_INTERRUPT_PCIE0_INTB 210
#define GIC_SPI_INTERRUPT_PCIE0_INTC 211
#define GIC_SPI_INTERRUPT_PCIE0_INTD 212
#define GIC_SPI_INTERRUPT_PCIE1_MSI 223
#define GIC_SPI_INTERRUPT_PCIE1_INTA 219
#define GIC_SPI_INTERRUPT_PCIE1_INTB 220
#define GIC_SPI_INTERRUPT_PCIE1_INTC 221
#define GIC_SPI_INTERRUPT_PCIE1_INTD 222
#define GIC_SPI_INTERRUPT_PCIE2_MSI 233
#define GIC_SPI_INTERRUPT_PCIE2_INTA 229
#define GIC_SPI_INTERRUPT_PCIE2_INTB 230
#define GIC_SPI_INTERRUPT_PCIE2_INTC 231
#define GIC_SPI_INTERRUPT_PCIE2_INTD 232
#define GIC_SPI_INTERRUPT_L2_INT_RP1 238
#define GIC_SPI_INTERRUPT_L2_INT_AO 239
#define GIC_SPI_INTERRUPT_L2_INT_HDMI0 242
#define GIC_SPI_INTERRUPT_L2_INT_1 243
#define GIC_SPI_INTERRUPT_L2_INT_2 244
#define GIC_SPI_INTERRUPT_L2_INT_3 245
#define GIC_SPI_INTERRUPT_L2_INT_4 247
#define GIC_SPI_INTERRUPT_V3D 249
#define GIC_SPI_INTERRUPT_V3D_MMU 250
#define GIC_SPI_INTERRUPT_EMMC1 274
#define GIC_SPI_INTERRUPT_AON_UART0 276
#define GIC_SPI_INTERRUPT_AON_UART1 277

/* PPI assignments for Cortex-A76 */
#define PPI_TIMER 13
#define PPI_VTIMER 14
#define PPI_HYP_TIMER 15
#define PPI_MAINTENANCE 9

/* SCB bus base address (high peripherals) - matches Pi 5 device tree soc@107c000000 */
#define BCM2712_SCB_BASE 0x107c000000ULL

/* DMA remap: 30-bit DMA range from 0xc0000000 -> 0x00000000 */
#define DMA_REMAP_BASE 0xc0000000
#define DMA_REMAP_SIZE 0x40000000 /* 1GB */

static void bcm2712_gic_set_irq(void *opaque, int irq, int level)
{
    BCM2712State *s = (BCM2712State *)opaque;
    trace_bcm2712_gic_set_irq(irq, level);
    qemu_set_irq(qdev_get_gpio_in(DEVICE(&s->gic), irq), level);
}

static void bcm2712_init(Object *obj)
{
    BCM2712State *s = BCM2712(obj);

    fprintf(stderr, "DEBUG: bcm2712_init called for %p\\n", obj);

    /* Initialize peripherals container */
    object_initialize_child(obj, "peripherals", &s->peripherals,
                            TYPE_BCM2712_PERIPHERALS);
    object_property_add_alias(obj, "board-rev", OBJECT(&s->peripherals),
                              "board-rev");
    object_property_add_alias(obj, "vcram-size", OBJECT(&s->peripherals),
                              "vcram-size");
    object_property_add_alias(obj, "vcram-base", OBJECT(&s->peripherals),
                              "vcram-base");
    object_property_add_alias(obj, "command-line", OBJECT(&s->peripherals),
                              "command-line");

    /* Initialize GIC-400 */
    object_initialize_child(obj, "gic", &s->gic, TYPE_ARM_GIC);

    /* Initialize memory region for SCB bus */
    memory_region_init(&s->sysbus_mr, obj, "bcm2712-scb-bus", UINT64_MAX);
}

static void bcm2712_realize(DeviceState *dev, Error **errp)
{
    BCM2712State *s = BCM2712(dev);
    BCM2712PeripheralState *ps = BCM2712_PERIPHERALS(&s->peripherals);
    BCMSocPeripheralBaseState *ps_base = BCM_SOC_PERIPHERALS_BASE(&s->peripherals);
    DeviceState *gicdev = NULL;
    int i;
    Error *local_err = NULL;

    fprintf(stderr, "bcm2712_realize: start\n");

    /* Realize peripherals container */
    fprintf(stderr, "bcm2712_realize: realizing peripherals\n");
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->peripherals), errp)) {
        fprintf(stderr, "bcm2712_realize: sysbus_realize failed\n");
        return;
    }
    fprintf(stderr, "bcm2712_realize: peripherals realized\n");

    /* The parent class (TYPE_BCM_SOC_PERIPHERALS_BASE) instance_init raspi_peripherals_base_init
     * initializes the parent's peri_mr and calls sysbus_init_mmio on it (for non-BCM2712).
     * For BCM2712, we skip sysbus_init_mmio, but the parent's peri_mr is still initialized.
     * sysbus_realize() might still map it at address 0. We need to unmap it. */
    fprintf(stderr, "bcm2712_realize: getting parent peri_mr\n");
    MemoryRegion *parent_peri_mr = sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->peripherals), 0);
    fprintf(stderr, "bcm2712_realize: parent_peri_mr = %p\n", parent_peri_mr);
    if (parent_peri_mr) {
        /* Try to remove from system_memory first */
        fprintf(stderr, "bcm2712_realize: deleting parent_peri_mr from system_memory\n");
        memory_region_del_subregion(get_system_memory(), parent_peri_mr);
        /* If that fails, try to find and remove from any container */
        fprintf(stderr, "bcm2712_realize: checking parent_peri_mr->container\n");
        if (parent_peri_mr->container) {
            fprintf(stderr, "bcm2712_realize: deleting parent_peri_mr from container\n");
            memory_region_del_subregion(parent_peri_mr->container, parent_peri_mr);
        }
    }
    fprintf(stderr, "bcm2712_realize: parent peri_mr handled\n");

    /* Map SOC peripheral region (32-bit alias: 0x7c000000-0x7fffffff) */
    /* Use negative priority to overlay on top of RAM (lower number = higher priority) */
    /* Peri_mr is not auto-mapped (no sysbus_init_mmio), so map it manually with priority -1. */
    fprintf(stderr, "bcm2712_realize: mapping peri_mr at 0x7c000000\n");
    memory_region_add_subregion_overlap(get_system_memory(), BCM2712_PERI_BASE,
                                        &ps->peri_mr, -1);
    fprintf(stderr, "bcm2712_realize: peri_mr mapped\n");

    /* Map SCB bus region (0x107c000000+) */
    memory_region_add_subregion(get_system_memory(), BCM2712_SCB_BASE, &s->sysbus_mr);

    /* Map BCM2835 UART0 (PL011) at 0xFE201000 for qemu-compatible kernels */
    {
        MemoryRegion *uart0_mr = sysbus_mmio_get_region(SYS_BUS_DEVICE(&ps_base->uart0), 0);
        memory_region_add_subregion(get_system_memory(), 0xfe201000ULL, uart0_mr);
    }

    /* Initialize GIC-400 */
    if (!object_property_set_uint(OBJECT(&s->gic), "revision", 2, &local_err)) {
        error_propagate(errp, local_err);
        return;
    }
    if (!object_property_set_uint(OBJECT(&s->gic), "num-cpu", 4, &local_err)) {
        error_propagate(errp, local_err);
        return;
    }
    if (!object_property_set_uint(OBJECT(&s->gic), "num-irq", GIC_NUM_IRQS + GIC_INTERNAL, &local_err)) {
        error_propagate(errp, local_err);
        return;
    }
    if (!object_property_set_bool(OBJECT(&s->gic), "has-virtualization-extensions", true, &local_err)) {
        error_propagate(errp, local_err);
        return;
    }

    if (!sysbus_realize(SYS_BUS_DEVICE(&s->gic), errp)) {
        return;
    }

    /* Map GIC-400 registers */
    sysbus_mmio_map(SYS_BUS_DEVICE(&s->gic), 0, BCM2712_GIC_DIST_BASE);
    sysbus_mmio_map(SYS_BUS_DEVICE(&s->gic), 1, BCM2712_GIC_CPU_BASE);
    sysbus_mmio_map(SYS_BUS_DEVICE(&s->gic), 2, BCM2712_GIC_HYPERVISOR_BASE);
    sysbus_mmio_map(SYS_BUS_DEVICE(&s->gic), 3, BCM2712_GIC_VIRTUAL_BASE);

    gicdev = DEVICE(&s->gic);

    /* Use CPUs from base class (created in bcm283x_base_init) */
    BCM283XBaseState *s_base = BCM283X_BASE(dev);
    for (i = 0; i < 4; i++) {
        ARMCPU *cpu = ARM_CPU(&s_base->cpu[i].core);

        /* Set MPIDR: cluster 0, core i */
        object_property_set_int(OBJECT(cpu), "mp-affinity", i, &error_abort);

        /* Set CBAR (CPU local registers base) to match hardware */
        object_property_set_int(OBJECT(cpu), "reset-cbar", BCM2712_PERI_BASE, &error_abort);

        /* Start powered off if not enabled */
        object_property_set_bool(OBJECT(cpu), "start-powered-off", false, &error_abort);

        if (!qdev_realize(DEVICE(cpu), NULL, errp)) {
            return;
        }

        /* Connect CPU IRQ, FIQ, VIRQ, VFIQ to GIC */
        sysbus_connect_irq(SYS_BUS_DEVICE(&s->gic), i,
                           qdev_get_gpio_in(DEVICE(cpu), ARM_CPU_IRQ));
        sysbus_connect_irq(SYS_BUS_DEVICE(&s->gic), i + 4,
                           qdev_get_gpio_in(DEVICE(cpu), ARM_CPU_FIQ));
        sysbus_connect_irq(SYS_BUS_DEVICE(&s->gic), i + 8,
                           qdev_get_gpio_in(DEVICE(cpu), ARM_CPU_VIRQ));
        sysbus_connect_irq(SYS_BUS_DEVICE(&s->gic), i + 12,
                           qdev_get_gpio_in(DEVICE(cpu), ARM_CPU_VFIQ));

        /* Connect PPIs */
        sysbus_connect_irq(SYS_BUS_DEVICE(&s->gic), i + 16,
                           qdev_get_gpio_in(gicdev, PPI(i, PPI_MAINTENANCE)));

        /* Connect timers */
        qdev_connect_gpio_out(DEVICE(cpu), GTIMER_PHYS,
                              qdev_get_gpio_in(gicdev, PPI(i, PPI_TIMER)));
        qdev_connect_gpio_out(DEVICE(cpu), GTIMER_VIRT,
                              qdev_get_gpio_in(gicdev, PPI(i, PPI_VTIMER)));
        qdev_connect_gpio_out(DEVICE(cpu), GTIMER_HYP,
                              qdev_get_gpio_in(gicdev, PPI(i, PPI_HYP_TIMER)));

        /* Connect PMU interrupt */
        qdev_connect_gpio_out_named(DEVICE(cpu), "pmu-interrupt", 0,
                                    qdev_get_gpio_in(gicdev, PPI(i, VIRTUAL_PMU_IRQ)));
    }

    /* Connect SOC peripherals to GIC */
    /* Base peripherals from BCMSocPeripheralBaseState */
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps_base->uart0), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_UART0));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps_base->aux), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_UART1));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps_base->sdhost), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_SDHOST));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps_base->mboxes), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_MBOX));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps_base->systmr), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_TIMER0));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps_base->systmr), 1,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_TIMER1));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps_base->systmr), 2,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_TIMER2));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps_base->systmr), 3,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_TIMER3));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps_base->dwc2), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_DWC2));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps_base->i2c[0]), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_I2C));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps_base->spi[0]), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_SPI));

    /* EMMC2 */
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->emmc2), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_EMMC2));

    /* RP1 UARTs */
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->rp1.uart0), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_RP1_UART0));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->rp1.uart1), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_RP1_UART1));

    /* V3D GPU */
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->v3d), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_V3D));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->v3d), 1,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_V3D_MMU));

    /* GENET Ethernet controller */
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->genet), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_GENET));

    /* XHCI USB 3.0 controller */
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->xhci), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_XHCI));

    /* PCIe controllers (unimplemented) - DISABLED for now */
    /*    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie0), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE0_INTA));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie0), 1,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE0_INTB));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie0), 2,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE0_INTC));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie0), 3,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE0_INTD));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie0), 4,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE0_MSI));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie1), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE1_INTA));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie1), 1,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE1_INTB));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie1), 2,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE1_INTC));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie1), 3,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE1_INTD));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie1), 4,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE1_MSI));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie2), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE2_INTA));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie2), 1,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE2_INTB));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie2), 2,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE2_INTC));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie2), 3,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE2_INTD));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->pcie2), 4,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_PCIE2_MSI)); */

    /* L2 INTC interrupts */
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->l2_intc_v3d), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_L2_INT_V3D));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->l2_intc_rp1), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_L2_INT_RP1));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->l2_intc_ao), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_L2_INT_AO));
    sysbus_connect_irq(SYS_BUS_DEVICE(&ps->l2_intc_hdmi0), 0,
                       qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_L2_INT_HDMI0));

    /* HDMI controller interrupts */
                       sysbus_connect_irq(SYS_BUS_DEVICE(&ps->hdmi0), 0,
                                          qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_HDMI0));
                       sysbus_connect_irq(SYS_BUS_DEVICE(&ps->hdmi1), 0,
                                          qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_HDMI1));

                       /* Thermal sensor interrupt */
                       sysbus_connect_irq(SYS_BUS_DEVICE(&ps->thermal), 0,
                                          qdev_get_gpio_in(gicdev, GIC_SPI_INTERRUPT_THERMAL));

                       /* DMA remap region: 0xc0000000 -> 0x00000000 (30-bit DMA) */
                       memory_region_init_alias(&ps->dma_remap_mr, OBJECT(s), "dma-remap",
                                                get_system_memory(), 0x00000000, DMA_REMAP_SIZE);
                       memory_region_add_subregion(get_system_memory(), DMA_REMAP_BASE,
                                                   &ps->dma_remap_mr);

                       /* Pass through inbound GPIO lines to the GIC */
                       qdev_init_gpio_in(dev, bcm2712_gic_set_irq, GIC_NUM_IRQS);

                       /* Pass through outbound IRQ lines from the GIC to peripherals */
                       qdev_pass_gpios(DEVICE(&s->gic), DEVICE(&s->peripherals), NULL);
}

static void bcm2712_class_init(ObjectClass *oc, void *data)
{
    DeviceClass *dc = DEVICE_CLASS(oc);
    BCM283XBaseClass *bc_base = BCM283X_BASE_CLASS(oc);

    bc_base->cpu_type = ARM_CPU_TYPE_NAME("cortex-a76");
    bc_base->core_count = 4;
    bc_base->peri_base = BCM2712_PERI_BASE;
    bc_base->ctrl_base = BCM2712_GIC_BASE;
    bc_base->clusterid = 0x0;
    dc->realize = bcm2712_realize;
    dc->desc = "BCM2712 SoC (Raspberry Pi 5)";
}

static const TypeInfo bcm2712_type = {
    .name           = TYPE_BCM2712,
    .parent         = TYPE_BCM283X_BASE,
    .instance_size  = sizeof(BCM2712State),
    .instance_init  = bcm2712_init,
    .class_size     = sizeof(BCM2712Class),
    .class_init     = bcm2712_class_init,
};

static void bcm2712_register_types(void)
{
    type_register_static(&bcm2712_type);
}

type_init(bcm2712_register_types);