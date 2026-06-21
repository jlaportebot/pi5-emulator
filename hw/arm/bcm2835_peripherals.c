/*
 * Raspberry Pi emulation (c) 2012 Gregory Estrade
 * Upstreaming code cleanup [including bcm2835_*] (c) 2013 Jan Petrous
 *
 * Rasperry Pi 2 emulation and refactoring Copyright (c) 2015, Microsoft
 * Written by Andrew Baumann
 *
 * This work is licensed under the terms of the GNU GPL, version 2 or later.
 * See the COPYING file in the top-level directory.
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/module.h"
#include "hw/arm/bcm2835_peripherals.h"
#include "hw/misc/bcm2835_mbox_defs.h"
#include "hw/arm/raspi_platform.h"
#include "system/system.h"

/* Peripheral base address on the VC (GPU) system bus */
#define BCM2835_VC_PERI_BASE 0x7e000000

/* Capabilities for SD controller: no DMA, high-speed, default clocks etc. */
#define BCM2835_SDHC_CAPAREG 0x52134b4

/*
 * According to Linux driver & DTS, dma channels 0--10 have separate IRQ,
 * while channels 11--14 share one IRQ:
 */
#define SEPARATE_DMA_IRQ_MAX 10
#define ORGATED_DMA_IRQ_COUNT 4

/* All three I2C controllers share the same IRQ */
#define ORGATED_I2C_IRQ_COUNT 3

void create_unimp(BCMSocPeripheralBaseState *ps,
                  UnimplementedDeviceState *uds,
                  const char *name, hwaddr ofs, hwaddr size)
{
    object_initialize_child(OBJECT(ps), name, uds, TYPE_UNIMPLEMENTED_DEVICE);
    qdev_prop_set_string(DEVICE(uds), "name", name);
    qdev_prop_set_uint64(DEVICE(uds), "size", size);
    sysbus_realize(SYS_BUS_DEVICE(uds), &error_fatal);

    /* Remove unimplemented device iomem from system memory (added by sysbus_init_mmio) */
    {
        MemoryRegion *unimp_iomem = sysbus_mmio_get_region(SYS_BUS_DEVICE(uds), 0);
        fprintf(stderr, "DEBUG: Unimp %s iomem=%p, container=%p\n", name, unimp_iomem, unimp_iomem ? unimp_iomem->container : NULL);
        if (unimp_iomem && unimp_iomem->container) {
            memory_region_del_subregion(unimp_iomem->container, unimp_iomem);
            fprintf(stderr, "DEBUG: Unimp %s iomem removed from container, container now=%p\n", name, unimp_iomem->container);
        }
    }

    fprintf(stderr, "DEBUG: adding unimp %s to peri_mr\n", name);
    memory_region_add_subregion_overlap(&ps->peri_mr, ofs,
                    sysbus_mmio_get_region(SYS_BUS_DEVICE(uds), 0), -1000);
    fprintf(stderr, "DEBUG: unimp %s added to peri_mr successfully\n", name);
}

static void bcm2835_peripherals_init(Object *obj)
{
    BCM2835PeripheralState *s = BCM2835_PERIPHERALS(obj);
    BCMSocPeripheralBaseState *s_base = BCM_SOC_PERIPHERALS_BASE(obj);

    /* Random Number Generator */
    object_initialize_child(obj, "rng", &s->rng, TYPE_BCM2835_RNG);

    /* Thermal */
    object_initialize_child(obj, "thermal", &s->thermal, TYPE_BCM2835_THERMAL);

    /* GPIO */
    object_initialize_child(obj, "gpio", &s->gpio, TYPE_BCM2835_GPIO);

    object_property_add_const_link(OBJECT(&s->gpio), "sdbus-sdhci",
                                   OBJECT(&s_base->sdhci.sdbus));
    object_property_add_const_link(OBJECT(&s->gpio), "sdbus-sdhost",
                                   OBJECT(&s_base->sdhost.sdbus));

    /* Gated DMA interrupts */
    object_initialize_child(obj, "orgated-dma-irq",
                            &s_base->orgated_dma_irq, TYPE_OR_IRQ);
    object_property_set_int(OBJECT(&s_base->orgated_dma_irq), "num-lines",
                            ORGATED_DMA_IRQ_COUNT, &error_abort);
}

static void raspi_peripherals_base_init(Object *obj)
{
    BCMSocPeripheralBaseState *s = BCM_SOC_PERIPHERALS_BASE(obj);
    BCMSocPeripheralBaseClass *bc = BCM_SOC_PERIPHERALS_BASE_GET_CLASS(obj);

    /* Memory region for peripheral devices, which we export to our parent */
    memory_region_init(&s->peri_mr, obj, "bcm2835-peripherals", bc->peri_size);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->peri_mr);

    /* Internal memory region for peripheral bus addresses (not exported) */
    memory_region_init(&s->gpu_bus_mr, obj, "bcm2835-gpu", (uint64_t)1 << 32);

    /* Internal memory region for request/response communication with
     * mailbox-addressable peripherals (not exported)
     */
    memory_region_init(&s->mbox_mr, obj, "bcm2835-mbox",
                       MBOX_CHAN_COUNT << MBOX_AS_CHAN_SHIFT);

    /* Interrupt Controller */
    object_initialize_child(obj, "ic", &s->ic, TYPE_BCM2835_IC);

    /* SYS Timer */
    object_initialize_child(obj, "systimer", &s->systmr,
                            TYPE_BCM2835_SYSTIMER);

    /* UART0 */
    object_initialize_child(obj, "uart0", &s->uart0, TYPE_PL011);

    /* AUX / UART1 */
    object_initialize_child(obj, "aux", &s->aux, TYPE_BCM2835_AUX);

    /* Mailboxes */
    object_initialize_child(obj, "mbox", &s->mboxes, TYPE_BCM2835_MBOX);

    object_property_add_const_link(OBJECT(&s->mboxes), "mbox-mr",
                                   OBJECT(&s->mbox_mr));

    /* Framebuffer */
    object_initialize_child(obj, "fb", &s->fb, TYPE_BCM2835_FB);
    object_property_add_alias(obj, "vcram-size", OBJECT(&s->fb), "vcram-size");
    object_property_add_alias(obj, "vcram-base", OBJECT(&s->fb), "vcram-base");

    fprintf(stderr, "DEBUG: Setting fb dma-mr link\n");
    s->fb.dma_mr = &s->gpu_bus_mr;
    fprintf(stderr, "DEBUG: fb dma-mr link set\n");
    Object *test_link = object_property_get_link(OBJECT(&s->fb), "dma-mr", NULL);
    fprintf(stderr, "DEBUG: fb dma-mr link after set: %p\n", test_link);

    /* OTP */
    object_initialize_child(obj, "bcm2835-otp", &s->otp,
                            TYPE_BCM2835_OTP);

    /* Property channel */
    object_initialize_child(obj, "property", &s->property,
                            TYPE_BCM2835_PROPERTY);
    object_property_add_alias(obj, "board-rev", OBJECT(&s->property),
                              "board-rev");
    object_property_add_alias(obj, "command-line", OBJECT(&s->property),
                              "command-line");

    object_property_add_const_link(OBJECT(&s->property), "fb",
                                   OBJECT(&s->fb));
    object_property_add_const_link(OBJECT(&s->property), "dma-mr",
                                   OBJECT(&s->gpu_bus_mr));
    object_property_add_const_link(OBJECT(&s->property), "otp",
                                   OBJECT(&s->otp));

    /* Extended Mass Media Controller */
    object_initialize_child(obj, "sdhci", &s->sdhci, TYPE_SYSBUS_SDHCI);

    /* SDHOST */
    object_initialize_child(obj, "sdhost", &s->sdhost, TYPE_BCM2835_SDHOST);

    /* DMA Channels */
    object_initialize_child(obj, "dma", &s->dma, TYPE_BCM2835_DMA);

    object_property_add_const_link(OBJECT(&s->dma), "dma-mr",
                                   OBJECT(&s->gpu_bus_mr));

    /* Mphi */
    object_initialize_child(obj, "mphi", &s->mphi, TYPE_BCM2835_MPHI);

    /* DWC2 */
    object_initialize_child(obj, "dwc2", &s->dwc2, TYPE_DWC2_USB);

    /* CPRMAN clock manager */
    object_initialize_child(obj, "cprman", &s->cprman, TYPE_BCM2835_CPRMAN);

    object_property_add_const_link(OBJECT(&s->dwc2), "dma-mr",
                                   OBJECT(&s->gpu_bus_mr));

    /* Power Management */
    object_initialize_child(obj, "powermgt", &s->powermgt,
                            TYPE_BCM2835_POWERMGT);

    /* SPI */
    object_initialize_child(obj, "bcm2835-spi0", &s->spi[0],
                            TYPE_BCM2835_SPI);

    /* I2C */
    object_initialize_child(obj, "bcm2835-i2c0", &s->i2c[0],
                            TYPE_BCM2835_I2C);
    object_initialize_child(obj, "bcm2835-i2c1", &s->i2c[1],
                            TYPE_BCM2835_I2C);
    object_initialize_child(obj, "bcm2835-i2c2", &s->i2c[2],
                            TYPE_BCM2835_I2C);

    object_initialize_child(obj, "orgated-i2c-irq",
                            &s->orgated_i2c_irq, TYPE_OR_IRQ);
    object_property_set_int(OBJECT(&s->orgated_i2c_irq), "num-lines",
                            ORGATED_I2C_IRQ_COUNT, &error_abort);
}

static void bcm2835_peripherals_realize(DeviceState *dev, Error **errp)
{
    MemoryRegion *mphi_mr;
    BCM2835PeripheralState *s = BCM2835_PERIPHERALS(dev);
    BCMSocPeripheralBaseState *s_base = BCM_SOC_PERIPHERALS_BASE(dev);
    int n;

    bcm_soc_peripherals_common_realize(dev, errp);

    /* Extended Mass Media Controller */
    sysbus_connect_irq(SYS_BUS_DEVICE(&s_base->sdhci), 0,
        qdev_get_gpio_in_named(DEVICE(&s_base->ic), BCM2835_IC_GPU_IRQ,
                               INTERRUPT_ARASANSDIO));

     /* Connect DMA 0-12 to the interrupt controller */
    for (n = 0; n <= SEPARATE_DMA_IRQ_MAX; n++) {
        sysbus_connect_irq(SYS_BUS_DEVICE(&s_base->dma), n,
                           qdev_get_gpio_in_named(DEVICE(&s_base->ic),
                                                  BCM2835_IC_GPU_IRQ,
                                                  INTERRUPT_DMA0 + n));
    }

    if (!qdev_realize(DEVICE(&s_base->orgated_dma_irq), NULL, errp)) {
        return;
    }
    for (n = 0; n < ORGATED_DMA_IRQ_COUNT; n++) {
        sysbus_connect_irq(SYS_BUS_DEVICE(&s_base->dma),
                           SEPARATE_DMA_IRQ_MAX + 1 + n,
                           qdev_get_gpio_in(DEVICE(&s_base->orgated_dma_irq), n));
    }
    qdev_connect_gpio_out(DEVICE(&s_base->orgated_dma_irq), 0,
                          qdev_get_gpio_in_named(DEVICE(&s_base->ic),
                              BCM2835_IC_GPU_IRQ,
                              INTERRUPT_DMA0 + SEPARATE_DMA_IRQ_MAX + 1));

    /* Random Number Generator */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->rng), errp)) {
        return;
    }
    memory_region_add_subregion(
        &s_base->peri_mr, RNG_OFFSET,
        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->rng), 0));

    /* THERMAL */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->thermal), errp)) {
        return;
    }
    memory_region_add_subregion(&s_base->peri_mr, THERMAL_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->thermal), 0));

    /* Map MPHI to the peripherals memory map */
    mphi_mr = sysbus_mmio_get_region(SYS_BUS_DEVICE(&s_base->mphi), 0);
    memory_region_add_subregion(&s_base->peri_mr, MPHI_OFFSET, mphi_mr);

    /* GPIO */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->gpio), errp)) {
        return;
    }
    memory_region_add_subregion(
        &s_base->peri_mr, GPIO_OFFSET,
        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->gpio), 0));

    object_property_add_alias(OBJECT(s), "sd-bus", OBJECT(&s->gpio), "sd-bus");
}

void bcm_soc_peripherals_common_realize(DeviceState *dev, Error **errp)
{
    BCMSocPeripheralBaseState *s = BCM_SOC_PERIPHERALS_BASE(dev);
    Object *obj;
    MemoryRegion *ram;
    Error *err = NULL;
    uint64_t ram_size, vcram_size, vcram_base;
    int n;

    obj = object_property_get_link(OBJECT(dev)->parent, "ram", &error_abort);

    ram = MEMORY_REGION(obj);
    ram_size = memory_region_size(ram);

    /* Map peripherals and RAM into the GPU address space. */
    memory_region_init_alias(&s->peri_mr_alias, OBJECT(s),
                             "bcm2835-peripherals-alias", &s->peri_mr, 0,
                             memory_region_size(&s->peri_mr));

    memory_region_add_subregion_overlap(&s->gpu_bus_mr, BCM2835_VC_PERI_BASE,
                                        &s->peri_mr_alias, 1);

    /* RAM is aliased four times (different cache configurations) on the GPU */
    for (n = 0; n < 4; n++) {
        char alias_name[64];
        snprintf(alias_name, sizeof(alias_name), "bcm2835-gpu-ram-alias[%d]", n);
        memory_region_init_alias(&s->ram_alias[n], OBJECT(s),
                                 alias_name, ram, 0, ram_size);
        memory_region_add_subregion_overlap(&s->gpu_bus_mr, (hwaddr)n << 30,
                                            &s->ram_alias[n], 0);
    }

    /* Interrupt Controller */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->ic), errp)) {
        return;
    }

    /* CPRMAN clock manager */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->cprman), errp)) {
        return;
    }
    memory_region_add_subregion(&s->peri_mr, CPRMAN_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->cprman), 0));

    fprintf(stderr, "DEBUG: Trying to get uart-out clock from CPRMAN\n");
    Clock *uart_clk = qdev_get_clock_out(DEVICE(&s->cprman), "uart-out");
    fprintf(stderr, "DEBUG: Got uart_clk = %p\n", uart_clk);
    qdev_connect_clock_in(DEVICE(&s->uart0), "clk", uart_clk);
    fprintf(stderr, "DEBUG: Connected clock\n");

    memory_region_add_subregion(&s->peri_mr, ARMCTRL_IC_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->ic), 0));
    sysbus_pass_irq(SYS_BUS_DEVICE(s), SYS_BUS_DEVICE(&s->ic));

    /* Sys Timer */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->systmr), errp)) {
        return;
    }
    memory_region_add_subregion(&s->peri_mr, ST_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->systmr), 0));
    sysbus_connect_irq(SYS_BUS_DEVICE(&s->systmr), 0,
        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,
                               INTERRUPT_TIMER0));
    sysbus_connect_irq(SYS_BUS_DEVICE(&s->systmr), 1,
        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,
                               INTERRUPT_TIMER1));
    sysbus_connect_irq(SYS_BUS_DEVICE(&s->systmr), 2,
        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,
                               INTERRUPT_TIMER2));
    sysbus_connect_irq(SYS_BUS_DEVICE(&s->systmr), 3,
        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,
                               INTERRUPT_TIMER3));

    /* UART0 */
    if (!object_property_get_link(OBJECT(&s->uart0), "chardev", NULL)) {
        qdev_prop_set_chr(DEVICE(&s->uart0), "chardev", serial_hd(0));
    }
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->uart0), errp)) {
        return;
    }

    memory_region_add_subregion(&s->peri_mr, UART0_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->uart0), 0));
    sysbus_connect_irq(SYS_BUS_DEVICE(&s->uart0), 0,
        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,
                               INTERRUPT_UART0));

    /* AUX / UART1 */
    if (!object_property_get_link(OBJECT(&s->aux), "chardev", NULL)) {
        qdev_prop_set_chr(DEVICE(&s->aux), "chardev", serial_hd(1));
    }

    if (!sysbus_realize(SYS_BUS_DEVICE(&s->aux), errp)) {
        return;
    }

    memory_region_add_subregion(&s->peri_mr, AUX_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->aux), 0));
    sysbus_connect_irq(SYS_BUS_DEVICE(&s->aux), 0,
        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,
                               INTERRUPT_AUX));

    /* Mailboxes */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->mboxes), errp)) {
        return;
    }

    memory_region_add_subregion(&s->peri_mr, ARMCTRL_0_SBM_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->mboxes), 0));
    sysbus_connect_irq(SYS_BUS_DEVICE(&s->mboxes), 0,
        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_ARM_IRQ,
                               INTERRUPT_ARM_MAILBOX));

    /* Framebuffer */
    vcram_size = object_property_get_uint(OBJECT(s), "vcram-size", &err);
    if (err) {
        error_propagate(errp, err);
        return;
    }

    vcram_base = object_property_get_uint(OBJECT(s), "vcram-base", &err);
    if (err) {
        error_propagate(errp, err);
        return;
    }

    if (vcram_base == 0) {
        vcram_base = ram_size - vcram_size;
    }
    vcram_base = MIN(vcram_base, UPPER_RAM_BASE - vcram_size);
    if (!object_property_set_uint(OBJECT(&s->fb), "vcram-base", vcram_base,
                                  errp)) {
        return;
    }

    fprintf(stderr, "DEBUG: Checking dma-mr link for base fb\n");
    Object *dma_mr_obj = object_property_get_link(OBJECT(&s->fb), "dma-mr", NULL);
    fprintf(stderr, "DEBUG: dma-mr link = %p\n", dma_mr_obj);

    if (!sysbus_realize(SYS_BUS_DEVICE(&s->fb), errp)) {
        return;
    }

    fprintf(stderr, "DEBUG: fb iomem container before mbox add: %p\n", s->fb.iomem.container);

    /* Remove the framebuffer's iomem from the root container (added by memory_region_init_io with NULL owner) */
    if (s->fb.iomem.container) {
        fprintf(stderr, "DEBUG: fb iomem has container %p, removing\n", s->fb.iomem.container);
        memory_region_del_subregion(s->fb.iomem.container, &s->fb.iomem);
        fprintf(stderr, "DEBUG: fb iomem container after remove: %p\n", s->fb.iomem.container);
    } else {
        fprintf(stderr, "DEBUG: fb iomem has no container\n");
    }

    fprintf(stderr, "DEBUG: about to add fb iomem alias to mbox_mr, alias container=%p\n", s->fb.fb_iomem_alias.container);

    /* Create an alias of the framebuffer iomem to avoid container issues.
     * Use NULL owner to avoid automatic container assignment, then remove from root container. */
    MemoryRegion *fb_iomem_alias;
    memory_region_init_alias(&s->fb.fb_iomem_alias, NULL,
                             "bcm2835-fb-mbox-alias", &s->fb.iomem, 0, 0x10);
    /* Remove from root container immediately */
    if (s->fb.fb_iomem_alias.container) {
        memory_region_del_subregion(s->fb.fb_iomem_alias.container, &s->fb.fb_iomem_alias);
        fprintf(stderr, "DEBUG: alias: alias removed from container, container now=%p\n", s->fb.fb_iomem_alias.container);
    }
    fb_iomem_alias = &s->fb.fb_iomem_alias;

    fprintf(stderr, "DEBUG: about to add fb iomem alias to mbox_mr, alias container=%p\n", fb_iomem_alias->container);

    /* Add the framebuffer's iomem alias to mbox_mr */
    fprintf(stderr, "DEBUG: calling memory_region_add_subregion with fb_iomem_alias=%p, fb_iomem_alias->container=%p\n", fb_iomem_alias, fb_iomem_alias->container);
    memory_region_add_subregion(&s->mbox_mr, MBOX_CHAN_FB << MBOX_AS_CHAN_SHIFT,
                                fb_iomem_alias);
    fprintf(stderr, "DEBUG: memory_region_add_subregion returned successfully\n");
    sysbus_connect_irq(SYS_BUS_DEVICE(&s->fb), 0,
                       qdev_get_gpio_in(DEVICE(&s->mboxes), MBOX_CHAN_FB));

    /* OTP */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->otp), errp)) {
        return;
    }

    /* Remove OTP iomem from system memory (added by sysbus_init_mmio) */
    {
        MemoryRegion *otp_iomem = sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->otp), 0);
        fprintf(stderr, "DEBUG: OTP iomem=%p, container=%p\n", otp_iomem, otp_iomem ? otp_iomem->container : NULL);
        if (otp_iomem && otp_iomem->container) {
            memory_region_del_subregion(otp_iomem->container, otp_iomem);
            fprintf(stderr, "DEBUG: OTP iomem removed from container, container now=%p\n", otp_iomem->container);
        }
    }

    fprintf(stderr, "DEBUG: adding OTP iomem to peri_mr\n");
    memory_region_add_subregion(&s->peri_mr, OTP_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->otp), 0));
    fprintf(stderr, "DEBUG: OTP added to peri_mr successfully\n");

    /* Property channel */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->property), errp)) {
        return;
    }

    /* Remove Property iomem from system memory (added by sysbus_init_mmio) */
    {
        MemoryRegion *prop_iomem = sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->property), 0);
        fprintf(stderr, "DEBUG: Property iomem=%p, container=%p\n", prop_iomem, prop_iomem ? prop_iomem->container : NULL);
        if (prop_iomem && prop_iomem->container) {
            memory_region_del_subregion(prop_iomem->container, prop_iomem);
            fprintf(stderr, "DEBUG: Property iomem removed from container, container now=%p\n", prop_iomem->container);
        }
    }

    fprintf(stderr, "DEBUG: adding Property iomem to mbox_mr\n");
    memory_region_add_subregion(&s->mbox_mr,
                MBOX_CHAN_PROPERTY << MBOX_AS_CHAN_SHIFT,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->property), 0));
    fprintf(stderr, "DEBUG: Property added to mbox_mr successfully\n");
    sysbus_connect_irq(SYS_BUS_DEVICE(&s->property), 0,
                      qdev_get_gpio_in(DEVICE(&s->mboxes), MBOX_CHAN_PROPERTY));

    /* EMMC1 (SDHCI) */
    object_property_set_uint(OBJECT(&s->sdhci), "sd-spec-version", 3,
                             &error_abort);
    object_property_set_uint(OBJECT(&s->sdhci), "capareg",
                             BCM2835_SDHC_CAPAREG, &error_abort);
    object_property_set_bool(OBJECT(&s->sdhci), "pending-insert-quirk", true,
                             &error_abort);
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->sdhci), errp)) {
        return;
    }

    /* Remove SDHCI iomem from system memory (added by sysbus_init_mmio) */
    {
        MemoryRegion *sdhci_iomem = sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->sdhci), 0);
        fprintf(stderr, "DEBUG: SDHCI iomem=%p, container=%p\n", sdhci_iomem, sdhci_iomem ? sdhci_iomem->container : NULL);
        if (sdhci_iomem && sdhci_iomem->container) {
            memory_region_del_subregion(sdhci_iomem->container, sdhci_iomem);
            fprintf(stderr, "DEBUG: SDHCI iomem removed from container, container now=%p\n", sdhci_iomem->container);
        }
    }

    fprintf(stderr, "DEBUG: adding SDHCI iomem to peri_mr\n");
    memory_region_add_subregion(&s->peri_mr, EMMC1_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->sdhci), 0));
    fprintf(stderr, "DEBUG: SDHCI added to peri_mr successfully\n");

    /* SDHOST */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->sdhost), errp)) {
        return;
    }

    /* Remove SDHOST iomem from system memory (added by sysbus_init_mmio) */
    {
        MemoryRegion *sdhost_iomem = sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->sdhost), 0);
        fprintf(stderr, "DEBUG: SDHOST iomem=%p, container=%p\n", sdhost_iomem, sdhost_iomem ? sdhost_iomem->container : NULL);
        if (sdhost_iomem && sdhost_iomem->container) {
            memory_region_del_subregion(sdhost_iomem->container, sdhost_iomem);
            fprintf(stderr, "DEBUG: SDHOST iomem removed from container, container now=%p\n", sdhost_iomem->container);
        }
    }

    fprintf(stderr, "DEBUG: adding SDHOST iomem to peri_mr\n");
    memory_region_add_subregion(&s->peri_mr, MMCI0_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->sdhost), 0));
    fprintf(stderr, "DEBUG: SDHOST added to peri_mr successfully\n");
    sysbus_connect_irq(SYS_BUS_DEVICE(&s->sdhost), 0,
        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,
                               INTERRUPT_SDIO));

    /* DMA Channels */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->dma), errp)) {
        return;
    }

    /* Remove DMA iomem from system memory (added by sysbus_init_mmio) */
    {
        MemoryRegion *dma_iomem0 = sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->dma), 0);
        fprintf(stderr, "DEBUG: DMA iomem0=%p, container=%p\n", dma_iomem0, dma_iomem0 ? dma_iomem0->container : NULL);
        if (dma_iomem0 && dma_iomem0->container) {
            memory_region_del_subregion(dma_iomem0->container, dma_iomem0);
            fprintf(stderr, "DEBUG: DMA iomem0 removed from container, container now=%p\n", dma_iomem0->container);
        }
    }

    fprintf(stderr, "DEBUG: adding DMA iomem0 to peri_mr\n");
    memory_region_add_subregion(&s->peri_mr, DMA_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->dma), 0));
    fprintf(stderr, "DEBUG: DMA iomem0 added to peri_mr successfully\n");

    {
        MemoryRegion *dma_iomem1 = sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->dma), 1);
        fprintf(stderr, "DEBUG: DMA iomem1=%p, container=%p\n", dma_iomem1, dma_iomem1 ? dma_iomem1->container : NULL);
        if (dma_iomem1 && dma_iomem1->container) {
            memory_region_del_subregion(dma_iomem1->container, dma_iomem1);
            fprintf(stderr, "DEBUG: DMA iomem1 removed from container, container now=%p\n", dma_iomem1->container);
        }
    }

    fprintf(stderr, "DEBUG: adding DMA iomem1 to peri_mr\n");
    memory_region_add_subregion(&s->peri_mr, DMA15_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->dma), 1));
    fprintf(stderr, "DEBUG: DMA iomem1 added to peri_mr successfully\n");

    /* Mphi */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->mphi), errp)) {
        return;
    }

    sysbus_connect_irq(SYS_BUS_DEVICE(&s->mphi), 0,
        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,
                               INTERRUPT_HOSTPORT));

    /* DWC2 */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->dwc2), errp)) {
        return;
    }

    /* Remove DWC2 iomem from system memory (added by sysbus_init_mmio) */
    {
        MemoryRegion *dwc2_iomem = sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->dwc2), 0);
        fprintf(stderr, "DEBUG: DWC2 iomem=%p, container=%p\n", dwc2_iomem, dwc2_iomem ? dwc2_iomem->container : NULL);
        if (dwc2_iomem && dwc2_iomem->container) {
            memory_region_del_subregion(dwc2_iomem->container, dwc2_iomem);
            fprintf(stderr, "DEBUG: DWC2 iomem removed from container, container now=%p\n", dwc2_iomem->container);
        }
    }

    fprintf(stderr, "DEBUG: adding DWC2 iomem to peri_mr\n");
    memory_region_add_subregion(&s->peri_mr, USB_OTG_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->dwc2), 0));
    fprintf(stderr, "DEBUG: DWC2 added to peri_mr successfully\n");
    sysbus_connect_irq(SYS_BUS_DEVICE(&s->dwc2), 0,
        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,
                               INTERRUPT_USB));

    /* Power Management */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->powermgt), errp)) {
        return;
    }

    /* Remove Power Management iomem from system memory (added by sysbus_init_mmio) */
    {
        MemoryRegion *pm_iomem = sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->powermgt), 0);
        fprintf(stderr, "DEBUG: PowerMgt iomem=%p, container=%p\n", pm_iomem, pm_iomem ? pm_iomem->container : NULL);
        if (pm_iomem && pm_iomem->container) {
            memory_region_del_subregion(pm_iomem->container, pm_iomem);
            fprintf(stderr, "DEBUG: PowerMgt iomem removed from container, container now=%p\n", pm_iomem->container);
        }
    }

    fprintf(stderr, "DEBUG: adding PowerMgt iomem to peri_mr\n");
    memory_region_add_subregion(&s->peri_mr, PM_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->powermgt), 0));
    fprintf(stderr, "DEBUG: PowerMgt added to peri_mr successfully\n");

    /* SPI */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->spi[0]), errp)) {
        return;
    }

    /* Remove SPI iomem from system memory (added by sysbus_init_mmio) */
    {
        MemoryRegion *spi_iomem = sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->spi[0]), 0);
        fprintf(stderr, "DEBUG: SPI iomem=%p, container=%p\n", spi_iomem, spi_iomem ? spi_iomem->container : NULL);
        if (spi_iomem && spi_iomem->container) {
            memory_region_del_subregion(spi_iomem->container, spi_iomem);
            fprintf(stderr, "DEBUG: SPI iomem removed from container, container now=%p\n", spi_iomem->container);
        }
    }

    fprintf(stderr, "DEBUG: adding SPI iomem to peri_mr\n");
    memory_region_add_subregion(&s->peri_mr, SPI0_OFFSET,
                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->spi[0]), 0));
    fprintf(stderr, "DEBUG: SPI added to peri_mr successfully\n");
    sysbus_connect_irq(SYS_BUS_DEVICE(&s->spi[0]), 0,
                       qdev_get_gpio_in_named(DEVICE(&s->ic),
                                              BCM2835_IC_GPU_IRQ,
                                              INTERRUPT_SPI));

    /* I2C */
    for (n = 0; n < 3; n++) {
        if (!sysbus_realize(SYS_BUS_DEVICE(&s->i2c[n]), errp)) {
            return;
        }
    }

    /* Remove I2C iomem from system memory (added by sysbus_init_mmio) */
    for (n = 0; n < 3; n++) {
        MemoryRegion *i2c_iomem = sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->i2c[n]), 0);
        fprintf(stderr, "DEBUG: I2C%d iomem=%p, container=%p\n", n, i2c_iomem, i2c_iomem ? i2c_iomem->container : NULL);
        if (i2c_iomem && i2c_iomem->container) {
            memory_region_del_subregion(i2c_iomem->container, i2c_iomem);
            fprintf(stderr, "DEBUG: I2C%d iomem removed from container, container now=%p\n", n, i2c_iomem->container);
        }
    }

    fprintf(stderr, "DEBUG: adding I2C iomem to peri_mr\n");
    memory_region_add_subregion(&s->peri_mr, BSC0_OFFSET,
            sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->i2c[0]), 0));
    memory_region_add_subregion(&s->peri_mr, BSC1_OFFSET,
            sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->i2c[1]), 0));
    memory_region_add_subregion(&s->peri_mr, BSC2_OFFSET,
            sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->i2c[2]), 0));
    fprintf(stderr, "DEBUG: I2C added to peri_mr successfully\n");

    if (!qdev_realize(DEVICE(&s->orgated_i2c_irq), NULL, errp)) {
        return;
    }
    for (n = 0; n < ORGATED_I2C_IRQ_COUNT; n++) {
        sysbus_connect_irq(SYS_BUS_DEVICE(&s->i2c[n]), 0,
                           qdev_get_gpio_in(DEVICE(&s->orgated_i2c_irq), n));
    }
    qdev_connect_gpio_out(DEVICE(&s->orgated_i2c_irq), 0,
                          qdev_get_gpio_in_named(DEVICE(&s->ic),
                                                 BCM2835_IC_GPU_IRQ,
                                                 INTERRUPT_I2C));

    create_unimp(s, &s->txp, "bcm2835-txp", TXP_OFFSET, 0x1000);
    create_unimp(s, &s->armtmr, "bcm2835-sp804", ARMCTRL_TIMER0_1_OFFSET, 0x40);
    create_unimp(s, &s->i2s, "bcm2835-i2s", I2S_OFFSET, 0x100);
    create_unimp(s, &s->smi, "bcm2835-smi", SMI_OFFSET, 0x100);
    create_unimp(s, &s->bscsl, "bcm2835-spis", BSC_SL_OFFSET, 0x100);
    create_unimp(s, &s->dbus, "bcm2835-dbus", DBUS_OFFSET, 0x8000);
    create_unimp(s, &s->ave0, "bcm2835-ave0", AVE0_OFFSET, 0x8000);
    create_unimp(s, &s->v3d, "bcm2835-v3d", V3D_OFFSET, 0x1000);
    create_unimp(s, &s->sdramc, "bcm2835-sdramc", SDRAMC_OFFSET, 0x100);
}

static void bcm2835_peripherals_class_init(ObjectClass *oc, void *data)
{
    DeviceClass *dc = DEVICE_CLASS(oc);
    BCMSocPeripheralBaseClass *bc = BCM_SOC_PERIPHERALS_BASE_CLASS(oc);

    bc->peri_size = 0x1000000;
    dc->realize = bcm2835_peripherals_realize;
}

static const TypeInfo bcm2835_peripherals_types[] = {
    {
        .name = TYPE_BCM2835_PERIPHERALS,
        .parent = TYPE_BCM_SOC_PERIPHERALS_BASE,
        .instance_size = sizeof(BCM2835PeripheralState),
        .instance_init = bcm2835_peripherals_init,
        .class_init = bcm2835_peripherals_class_init,
    }, {
        .name = TYPE_BCM_SOC_PERIPHERALS_BASE,
        .parent = TYPE_SYS_BUS_DEVICE,
        .instance_size = sizeof(BCMSocPeripheralBaseState),
        .instance_init = raspi_peripherals_base_init,
        .class_size = sizeof(BCMSocPeripheralBaseClass),
        .abstract = true,
    }
};

DEFINE_TYPES(bcm2835_peripherals_types)
