/*
 * RP1 I/O Controller emulation (Raspberry Pi 5)
 *
 * The RP1 is a custom microcontroller on Pi 5 that handles GPIO, UART, I2C, SPI, SD
 *
 * Copyright (C) 2025 Mister Lobster
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/module.h"
#include "hw/sysbus.h"
#include "hw/sd/sdhci.h"
#include "hw/char/pl011.h"
#include "hw/arm/bcm2712_rp1.h"
#include "hw/arm/bcm2712_peripherals.h"
#include "hw/misc/unimp.h"
#include "trace.h"

static void bcm2712_rp1_init(Object *obj)
{
    BCM2712RP1State *s = BCM2712_RP1(obj);

    memory_region_init(&s->rp1_mr, obj, "bcm2712-rp1", 0x1000000);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->rp1_mr);

    /* GPIO - placeholder */
    memory_region_init(&s->gpio_mr, obj, "gpio", RP1_GPIO_SIZE);

    /* UART0 - PL011 compatible */
    object_initialize_child(obj, "uart0", &s->uart0, TYPE_PL011);
    memory_region_init(&s->uart0.iomem, OBJECT(&s->uart0), "uart0", RP1_UART_SIZE);

    /* UART1 - PL011 compatible */
    object_initialize_child(obj, "uart1", &s->uart1, TYPE_PL011);
    memory_region_init(&s->uart1.iomem, OBJECT(&s->uart1), "uart1", RP1_UART_SIZE);

    /* SD0 */
    object_initialize_child(obj, "sd0", &s->sd0, TYPE_SYSBUS_SDHCI);
    memory_region_init(&s->sd0_mr, OBJECT(&s->sd0), "sd0", RP1_SD_SIZE);

    /* SD1 */
    object_initialize_child(obj, "sd1", &s->sd1, TYPE_SYSBUS_SDHCI);
    memory_region_init(&s->sd1_mr, OBJECT(&s->sd1), "sd1", RP1_SD_SIZE);

    /* I2C placeholders */
    for (int i = 0; i < 6; i++) {
        char name[16];
        snprintf(name, sizeof(name), "i2c%d", i);
        object_initialize_child(obj, name, &s->i2c0 + i, TYPE_UNIMPLEMENTED_DEVICE);
    }

    /* SPI placeholders */
    for (int i = 0; i < 6; i++) {
        char name[16];
        snprintf(name, sizeof(name), "spi%d", i);
        object_initialize_child(obj, name, &s->spi0 + i, TYPE_UNIMPLEMENTED_DEVICE);
    }
}

static void bcm2712_rp1_realize(DeviceState *dev, Error **errp)
{
    BCM2712RP1State *s = BCM2712_RP1(dev);

    /* GPIO placeholder */
    memory_region_add_subregion(&s->rp1_mr, RP1_GPIO_BASE, &s->gpio_mr);

    /* UART0 */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->uart0), errp)) {
        return;
    }
    memory_region_add_subregion(&s->rp1_mr, RP1_UART0_BASE,
                                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->uart0), 0));

    /* UART1 */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->uart1), errp)) {
        return;
    }
    memory_region_add_subregion(&s->rp1_mr, RP1_UART1_BASE,
                                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->uart1), 0));

    /* SD0 */
    object_property_set_uint(OBJECT(&s->sd0), "sd-spec-version", 3, &error_abort);
    object_property_set_uint(OBJECT(&s->sd0), "capareg", BCM2835_SDHC_CAPAREG, &error_abort);
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->sd0), errp)) {
        return;
    }
    memory_region_add_subregion(&s->rp1_mr, RP1_SD0_BASE,
                                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->sd0), 0));

    /* SD1 */
    object_property_set_uint(OBJECT(&s->sd1), "sd-spec-version", 3, &error_abort);
    object_property_set_uint(OBJECT(&s->sd1), "capareg", BCM2835_SDHC_CAPAREG, &error_abort);
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->sd1), errp)) {
        return;
    }
    memory_region_add_subregion(&s->rp1_mr, RP1_SD1_BASE,
                                sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->sd1), 0));

    /* I2C placeholders */
    for (int i = 0; i < 6; i++) {
        hwaddr base = RP1_I2C0_BASE + i * RP1_I2C_SIZE;
        char name[32];
        snprintf(name, sizeof(name), "bcm2712-rp1-i2c%d", i);
        object_initialize_child(OBJECT(dev), name, &s->i2c0 + i, TYPE_UNIMPLEMENTED_DEVICE);
        qdev_prop_set_string(DEVICE(&s->i2c0 + i), "name", name);
        qdev_prop_set_uint64(DEVICE(&s->i2c0 + i), "size", RP1_I2C_SIZE);
        if (!sysbus_realize(SYS_BUS_DEVICE(&s->i2c0 + i), errp)) {
            return;
        }
        memory_region_add_subregion(&s->rp1_mr, base,
                                    sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->i2c0 + i), 0));
    }

    /* SPI placeholders */
    for (int i = 0; i < 6; i++) {
        hwaddr base = RP1_SPI0_BASE + i * RP1_SPI_SIZE;
        char name[32];
        snprintf(name, sizeof(name), "bcm2712-rp1-spi%d", i);
        object_initialize_child(OBJECT(dev), name, &s->spi0 + i, TYPE_UNIMPLEMENTED_DEVICE);
        qdev_prop_set_string(DEVICE(&s->spi0 + i), "name", name);
        qdev_prop_set_uint64(DEVICE(&s->spi0 + i), "size", RP1_SPI_SIZE);
        if (!sysbus_realize(SYS_BUS_DEVICE(&s->spi0 + i), errp)) {
            return;
        }
        memory_region_add_subregion(&s->rp1_mr, base,
                                    sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->spi0 + i), 0));
    }

    /* Export SD bus for board-level connections */
    object_property_add_const_link(OBJECT(s), "sd-bus", OBJECT(&s->sd0.sdbus));
}

static void bcm2712_rp1_class_init(ObjectClass *oc, void *data)
{
    DeviceClass *dc = DEVICE_CLASS(oc);
    dc->realize = bcm2712_rp1_realize;
}

static const TypeInfo bcm2712_rp1_type_info = {
    .name = TYPE_BCM2712_RP1,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2712RP1State),
    .instance_init = bcm2712_rp1_init,
    .class_size = sizeof(BCM2712RP1Class),
    .class_init = bcm2712_rp1_class_init,
};

static void bcm2712_rp1_register_types(void)
{
    type_register_static(&bcm2712_rp1_type_info);
}

type_init(bcm2712_rp1_register_types)