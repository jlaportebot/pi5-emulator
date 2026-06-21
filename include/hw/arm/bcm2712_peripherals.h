/*
 * BCM2712 peripherals emulation (Raspberry Pi 5)
 *
 * Copyright (C) 2025 Mister Lobster
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef BCM2712_PERIPHERALS_H
#define BCM2712_PERIPHERALS_H

#include "hw/arm/bcm2835_peripherals.h"
#include "hw/sd/sdhci.h"
#include "hw/gpio/bcm2838_gpio.h"
#include "hw/arm/bcm2712_rp1.h"
#include "hw/arm/bcm2712_v3d.h"
#include "hw/display/bcm2712_hdmi.h"
#include "hw/audio/bcm2712_audio.h"
#include "hw/net/bcmgenet.h"
#include "hw/usb/hcd-xhci-sysbus.h"
#include "hw/pci-host/bcm_pcie.h"

/* GIC SPI interrupt numbers for BCM2712 (from Linux DT) */
#define GIC_SPI_INTERRUPT_MBOX         33
#define GIC_SPI_INTERRUPT_UART0        121
#define GIC_SPI_INTERRUPT_UART1        122
#define GIC_SPI_INTERRUPT_SDHOST       120
#define GIC_SPI_INTERRUPT_EMMC         126
#define GIC_SPI_INTERRUPT_EMMC2        127
#define GIC_SPI_INTERRUPT_EMMC_EMMC2   128
#define GIC_SPI_INTERRUPT_RNG200       129
#define GIC_SPI_INTERRUPT_THERMAL      130
#define GIC_SPI_INTERRUPT_DMA_0        80
#define GIC_SPI_INTERRUPT_DMA_15       95
#define GIC_SPI_INTERRUPT_DMA_16       96
#define GIC_SPI_INTERRUPT_DMA_31       111

/* GENET Ethernet interrupt */
#define GIC_SPI_INTERRUPT_GENET        157

/* XHCI USB 3.0 interrupt */
#define GIC_SPI_INTERRUPT_XHCI         158

/* HDMI interrupts */
#define GIC_SPI_INTERRUPT_HDMI0        240
#define GIC_SPI_INTERRUPT_HDMI1        241

/* Audio interrupt */
#define GIC_SPI_INTERRUPT_AUDIO        242

/* PCIe controller interrupts (3 lanes, each with INTA-INTD + MSI) */
#define GIC_SPI_INTERRUPT_PCIE0_INTA   209
#define GIC_SPI_INTERRUPT_PCIE0_INTB   210
#define GIC_SPI_INTERRUPT_PCIE0_INTC   211
#define GIC_SPI_INTERRUPT_PCIE0_INTD   212
#define GIC_SPI_INTERRUPT_PCIE0_MSI    213

#define GIC_SPI_INTERRUPT_PCIE1_INTA   219
#define GIC_SPI_INTERRUPT_PCIE1_INTB   220
#define GIC_SPI_INTERRUPT_PCIE1_INTC   221
#define GIC_SPI_INTERRUPT_PCIE1_INTD   222
#define GIC_SPI_INTERRUPT_PCIE1_MSI    223

#define GIC_SPI_INTERRUPT_PCIE2_INTA   229
#define GIC_SPI_INTERRUPT_PCIE2_INTB   230
#define GIC_SPI_INTERRUPT_PCIE2_INTC   231
#define GIC_SPI_INTERRUPT_PCIE2_INTD   232
#define GIC_SPI_INTERRUPT_PCIE2_MSI    233

/* RP1 (I/O controller) interrupts */
#define GIC_SPI_INTERRUPT_RP1_UART0    129
#define GIC_SPI_INTERRUPT_RP1_UART1    130
#define GIC_SPI_INTERRUPT_RP1_I2C0     131
#define GIC_SPI_INTERRUPT_RP1_I2C1     132
#define GIC_SPI_INTERRUPT_RP1_I2C2     133
#define GIC_SPI_INTERRUPT_RP1_I2C3     134
#define GIC_SPI_INTERRUPT_RP1_I2C4     135
#define GIC_SPI_INTERRUPT_RP1_I2C5     136
#define GIC_SPI_INTERRUPT_RP1_SPI0     137
#define GIC_SPI_INTERRUPT_RP1_SPI1     138
#define GIC_SPI_INTERRUPT_RP1_SPI2     139
#define GIC_SPI_INTERRUPT_RP1_SPI3     140
#define GIC_SPI_INTERRUPT_RP1_SPI4     141
#define GIC_SPI_INTERRUPT_RP1_SPI5     142
#define GIC_SPI_INTERRUPT_RP1_GPIO0    143
#define GIC_SPI_INTERRUPT_RP1_GPIO1    144
#define GIC_SPI_INTERRUPT_RP1_GPIO2    145
#define GIC_SPI_INTERRUPT_RP1_GPIO3    146
#define GIC_SPI_INTERRUPT_RP1_SD0      147
#define GIC_SPI_INTERRUPT_RP1_SD1      148

/* GPU (V3D) interrupts */
#define GPU_INTERRUPT_V3D              16
#define GPU_INTERRUPT_V3D_MMU          17
#define GPU_INTERRUPT_V3D_JOB          18
#define GPU_INTERRUPT_V3D_DEBUG        19

/* GPU DMA interrupts (from BCM2835) */
#define GPU_INTERRUPT_DMA0      16
#define GPU_INTERRUPT_DMA1      17
#define GPU_INTERRUPT_DMA2      18
#define GPU_INTERRUPT_DMA3      19
#define GPU_INTERRUPT_DMA4      20
#define GPU_INTERRUPT_DMA5      21
#define GPU_INTERRUPT_DMA6      22
#define GPU_INTERRUPT_DMA7_8    23
#define GPU_INTERRUPT_DMA9_10   24
#define GPU_INTERRUPT_DMA11     25
#define GPU_INTERRUPT_DMA12     26
#define GPU_INTERRUPT_DMA13     27
#define GPU_INTERRUPT_DMA14     28
#define GPU_INTERRUPT_DMA15     31
#define GPU_INTERRUPT_DMA16     32
#define GPU_INTERRUPT_DMA17     33
#define GPU_INTERRUPT_DMA18     34
#define GPU_INTERRUPT_DMA19     35
#define GPU_INTERRUPT_DMA20     36
#define GPU_INTERRUPT_DMA21     37
#define GPU_INTERRUPT_DMA22     38
#define GPU_INTERRUPT_DMA23     39
#define GPU_INTERRUPT_DMA24     40
#define GPU_INTERRUPT_DMA25     41
#define GPU_INTERRUPT_DMA26     42
#define GPU_INTERRUPT_DMA27     43
#define GPU_INTERRUPT_DMA28     44
#define GPU_INTERRUPT_DMA29     45
#define GPU_INTERRUPT_DMA30     46
#define GPU_INTERRUPT_DMA31     47

/* Capabilities for SD controller */
#define BCM2835_SDHC_CAPAREG 0x52134b4

/* BCM2712 specific offsets */
#define BCM2712_RP1_OFFSET             0x400000
#define BCM2712_RP1_SIZE               0x100000
#define BCM2712_V3D_OFFSET             0x500000
#define BCM2712_V3D_SIZE               0x100000

#define TYPE_BCM2712_PERIPHERALS "bcm2712-peripherals"
OBJECT_DECLARE_TYPE(BCM2712PeripheralState, BCM2712PeripheralClass,
                    BCM2712_PERIPHERALS)

struct BCM2712PeripheralState {
    /*< private >*/
    BCMSocPeripheralBaseState parent_obj;

    /*< public >*/
    MemoryRegion peri_mr;
    MemoryRegion peri_alias_mr;

    /* EMMC2 */
    SDHCIState emmc2;

    /* RP1 I/O controller */
    BCM2712RP1State rp1;
    MemoryRegion rp1_mr;

    /* V3D GPU */
    BCM2712V3DState v3d;
    MemoryRegion v3d_mr;

    /* GENET Ethernet controller */
    BCMGenetState genet;
    MemoryRegion genet_mr;

    /* XHCI USB 3.0 controller */
    XHCISysbusState xhci;
    MemoryRegion xhci_mr;

    /* Interrupt OR gates for shared IRQs */
    OrIRQState mmc_irq_orgate;
    OrIRQState emmc_irq_orgate;
    OrIRQState dma_irq_orgate[4]; /* For shared DMA IRQs */

    /* PCIe controllers (3 lanes on Pi 5) - using BCM PCIe controller */
    BCMPCIEState pcie0;
    MemoryRegion pcie0_mr;
    BCMPCIEState pcie1;
    MemoryRegion pcie1_mr;
    BCMPCIEState pcie2;
    MemoryRegion pcie2_mr;

    /* L2 Interrupt Controllers - unimplemented */
    UnimplementedDeviceState l2_intc_v3d;
    UnimplementedDeviceState l2_intc_rp1;
    UnimplementedDeviceState l2_intc_ao;
    UnimplementedDeviceState l2_intc_hdmi0;

    /* DMA remap region */
    MemoryRegion dma_remap_mr;

    /* HDMI controllers */
    BCM2712HDMIState hdmi0;
    MemoryRegion hdmi0_mr;
    BCM2712HDMIState hdmi1;
    MemoryRegion hdmi1_mr;

    /* Audio controller */
    BCM2712AudioState audio;
    MemoryRegion audio_mr;

    /* Thermal sensor (BCM2835 compatible) */
    Bcm2835ThermalState thermal;
    /* RNG (BCM2835 compatible) */
    BCM2835RngState rng;
    /* Power management (BCM2835 compatible) - uses base class */

    /* Unimplemented device placeholders */
    UnimplementedDeviceState asb;
    UnimplementedDeviceState clkisp;
};

struct BCM2712PeripheralClass {
    /*< private >*/
    BCMSocPeripheralBaseClass parent_class;
    /*< public >*/
    uint64_t peri_size;
};

#endif /* BCM2712_PERIPHERALS_H */