/*
 * RP1 I/O Controller emulation (Raspberry Pi 5)
 *
 * The RP1 is a custom microcontroller on Pi 5 that handles GPIO, UART, I2C, SPI, SD
 *
 * Copyright (C) 2025 Mister Lobster
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef BCM2712_RP1_H
#define BCM2712_RP1_H

#include "hw/sysbus.h"
#include "hw/sd/sdhci.h"
#include "hw/char/pl011.h"
#include "hw/misc/unimp.h"

/* RP1 Register offsets */
#define RP1_GPIO_BASE       0x000000
#define RP1_GPIO_SIZE       0x10000
#define RP1_UART0_BASE      0x100000
#define RP1_UART1_BASE      0x110000
#define RP1_UART_SIZE       0x10000
#define RP1_I2C0_BASE       0x200000
#define RP1_I2C1_BASE       0x210000
#define RP1_I2C2_BASE       0x220000
#define RP1_I2C3_BASE       0x230000
#define RP1_I2C4_BASE       0x240000
#define RP1_I2C5_BASE       0x250000
#define RP1_I2C_SIZE        0x10000
#define RP1_SPI0_BASE       0x300000
#define RP1_SPI1_BASE       0x310000
#define RP1_SPI2_BASE       0x320000
#define RP1_SPI3_BASE       0x330000
#define RP1_SPI4_BASE       0x340000
#define RP1_SPI5_BASE       0x350000
#define RP1_SPI_SIZE        0x10000
#define RP1_SD0_BASE        0x400000
#define RP1_SD1_BASE        0x410000
#define RP1_SD_SIZE         0x10000

#define TYPE_BCM2712_RP1 "bcm2712-rp1"
OBJECT_DECLARE_TYPE(BCM2712RP1State, BCM2712RP1Class, BCM2712_RP1)

struct BCM2712RP1State {
    /*< private >*/
    SysBusDevice parent_obj;

    /*< public >*/
    MemoryRegion rp1_mr;

    /* GPIO - use BCM2838 GPIO */
    MemoryRegion gpio_mr;

    /* UARTs - PL011 compatible */
    PL011State uart0;
    PL011State uart1;

    /* I2C - placeholders */
    UnimplementedDeviceState i2c0;
    UnimplementedDeviceState i2c1;
    UnimplementedDeviceState i2c2;
    UnimplementedDeviceState i2c3;
    UnimplementedDeviceState i2c4;
    UnimplementedDeviceState i2c5;

    /* SPI - placeholders */
    UnimplementedDeviceState spi0;
    UnimplementedDeviceState spi1;
    UnimplementedDeviceState spi2;
    UnimplementedDeviceState spi3;
    UnimplementedDeviceState spi4;
    UnimplementedDeviceState spi5;

    /* SD controllers */
    SDHCIState sd0;
    SDHCIState sd1;
    MemoryRegion sd0_mr;
    MemoryRegion sd1_mr;
};

struct BCM2712RP1Class {
    /*< private >*/
    SysBusDeviceClass parent_class;
    /*< public >*/
};

#endif /* BCM2712_RP1_H */