/*
 * VideoCore VII / V3D GPU emulation (Raspberry Pi 5)
 *
 * Copyright (C) 2025 Mister Lobster
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef BCM2712_V3D_H
#define BCM2712_V3D_H

#include "hw/sysbus.h"
#include "hw/display/bcm2835_fb.h"

/* V3D Register offsets */
#define V3D_IDENT0        0x000
#define V3D_IDENT1        0x004
#define V3D_IDENT2        0x008
#define V3D_SCRATCH       0x010
#define V3D_GCACTL        0x014
#define V3D_GCACTL_DBGE   (1 << 0)
#define V3D_GCACTL_FTUD   (1 << 1)
#define V3D_GCACTL_FBR    (1 << 2)
#define V3D_GCACTL_FBP    (1 << 3)
#define V3D_GCACTL_FDB    (1 << 4)
#define V3D_GCACTL_FDG    (1 << 5)
#define V3D_GCACTL_FDM    (1 << 6)
#define V3D_GCACTL_FDE    (1 << 7)
#define V3D_GCACTL_FDD    (1 << 8)
#define V3D_GCACTL_FDT    (1 << 9)
#define V3D_GCACTL_FDA    (1 << 10)
#define V3D_GCACTL_FDS    (1 << 11)
#define V3D_GCACTL_FDV    (1 << 12)
#define V3D_GCACTL_FDW    (1 << 13)
#define V3D_GCACTL_FDX    (1 << 14)
#define V3D_GCACTL_FDY    (1 << 15)
#define V3D_GCACTL_FDZ    (1 << 16)
#define V3D_GCACTL_FDQ    (1 << 17)
#define V3D_GCACTL_FDR    (1 << 18)
#define V3D_GCACTL_FDH    (1 << 19)
#define V3D_GCACTL_FDI    (1 << 20)
#define V3D_GCACTL_FDJ    (1 << 21)
#define V3D_GCACTL_FDK    (1 << 22)
#define V3D_GCACTL_FDL    (1 << 23)
#define V3D_GCACTL_FDM2   (1 << 24)
#define V3D_GCACTL_FDN    (1 << 25)
#define V3D_GCACTL_FDO    (1 << 26)
#define V3D_GCACTL_FDP    (1 << 27)
#define V3D_GCACTL_FDQ2   (1 << 28)
#define V3D_GCACTL_FDR2   (1 << 29)
#define V3D_GCACTL_FDS2   (1 << 30)
#define V3D_GCACTL_FDU    (1 << 31)

#define V3D_GPFIFOCTL     0x018
#define V3D_GPCTL         0x01c
#define V3D_GPCTL_STOP    (1 << 0)
#define V3D_GPCTL_START   (1 << 1)
#define V3D_GPCTL_RESET   (1 << 2)

#define V3D_CLE_SQ        0x020
#define V3D_CLE_WAKEUP    0x024
#define V3D_CLE_INT_CTL   0x028
#define V3D_CLE_INT_EN    0x02c
#define V3D_CLE_INT_DIS   0x030
#define V3D_CLE_INT_STAT  0x034

#define V3D_CT0CS         0x100
#define V3D_CT1CS         0x104
#define V3D_CT0EA         0x108
#define V3D_CT0CA         0x10c
#define V3D_CT0RA         0x110
#define V3D_CT1EA         0x114
#define V3D_CT1CA         0x118
#define V3D_CT1RA         0x11c

#define V3D_BFC           0x130
#define V3D_BFC_BFC       (1 << 0)
#define V3D_BFC_BFE       (1 << 1)

#define V3D_PCTRC         0x140
#define V3D_PCTRE         0x144
#define V3D_PCTRC_S       0x148
#define V3D_PCTRE_S       0x14c

#define V3D_DBGE          0x150
#define V3D_DBGC          0x154
#define V3D_DBGR          0x158
#define V3D_DBGD          0x15c

#define V3D_SQRSV0        0x200
#define V3D_SQRSV1        0x204
#define V3D_SQRSV2        0x208
#define V3D_SQRSV3        0x20c
#define V3D_SQRSV4        0x210
#define V3D_SQRSV5        0x214
#define V3D_SQRSV6        0x218
#define V3D_SQRSV7        0x21c

#define V3D_SRQPC         0x220
#define V3D_SRQUL         0x224
#define V3D_SRQCS         0x228
#define V3D_SRQPC_S       0x22c
#define V3D_SRQUL_S       0x230
#define V3D_SRQCS_S       0x234

#define V3D_VPMBASE       0x300
#define V3D_VPMPCNT0      0x304
#define V3D_VPMPCNT1      0x308
#define V3D_VPMPCNT2      0x30c
#define V3D_VPMPCNT3      0x310
#define V3D_VPMPCNT4      0x314
#define V3D_VPMPCNT5      0x318
#define V3D_VPMPCNT6      0x31c
#define V3D_VPMPCNT7      0x320

#define V3D_PSE_BASE      0x400
#define V3D_PSE_SLICES    4

#define V3D_L2CACTL       0x500
#define V3D_L2CACTL_L2CCE (1 << 0)
#define V3D_L2CACTL_L2CPE (1 << 1)

#define V3D_SLCACTL       0x510
#define V3D_SLCACTL_SLCCE (1 << 0)
#define V3D_SLCACTL_SLCPE (1 << 1)

#define V3D_DBG_PC0       0x600
#define V3D_DBG_PC1       0x604
#define V3D_DBG_PC2       0x608
#define V3D_DBG_PC3       0x60c
#define V3D_DBG_PC4       0x610
#define V3D_DBG_PC5       0x614
#define V3D_DBG_PC6       0x618
#define V3D_DBG_PC7       0x61c
#define V3D_DBG_PC8       0x620
#define V3D_DBG_PC9       0x624
#define V3D_DBG_PC10      0x628
#define V3D_DBG_PC11      0x62c
#define V3D_DBG_PC12      0x630
#define V3D_DBG_PC13      0x634
#define V3D_DBG_PC14      0x638
#define V3D_DBG_PC15      0x63c

#define V3D_HUB_IDENT     0x700
#define V3D_HUB_IDENT0    0x704
#define V3D_HUB_IDENT1    0x708
#define V3D_HUB_IDENT2    0x70c

#define V3D_GPU_HLT       0x800
#define V3D_GPU_HLT_HLT   (1 << 0)

#define V3D_MMU_BASE      0x10000
#define V3D_MMU_SIZE      0x10000

#define V3D_CACHE_BASE    0x20000
#define V3D_CACHE_SIZE    0x10000

#define TYPE_BCM2712_V3D "bcm2712-v3d"
OBJECT_DECLARE_TYPE(BCM2712V3DState, BCM2712V3DClass, BCM2712_V3D)

struct BCM2712V3DState {
    /*< private >*/
    SysBusDevice parent_obj;

    /*< public >*/
    MemoryRegion v3d_mr;
    MemoryRegion mmu_mr;
    MemoryRegion cache_mr;

    /* Framebuffer for display output */
    BCM2835FBState fb;

    /* Register state */
    uint32_t ident0;
    uint32_t ident1;
    uint32_t ident2;
    uint32_t scratch;
    uint32_t gca_ctl;
    uint32_t gpfifo_ctl;
    uint32_t gp_ctl;
    uint32_t cle_sq;
    uint32_t cle_wakeup;
    uint32_t cle_int_ctl;
    uint32_t cle_int_en;
    uint32_t cle_int_dis;
    uint32_t cle_int_stat;
    uint32_t ct0cs;
    uint32_t ct1cs;
    uint32_t ct0ea;
    uint32_t ct0ca;
    uint32_t ct0ra;
    uint32_t ct1ea;
    uint32_t ct1ca;
    uint32_t ct1ra;
    uint32_t bfc;
    uint32_t pctrc;
    uint32_t pctre;
    uint32_t dbge;
    uint32_t dbgc;
    uint32_t dbgr;
    uint32_t dbgd;
    uint32_t sqrsv[8];
    uint32_t srqpc;
    uint32_t srqul;
    uint32_t srqcs;
    uint32_t vpmbase;
    uint32_t vpm_pcnt[8];
    uint32_t l2cactl;
    uint32_t slcactl;
    uint32_t hub_ident;
    uint32_t hub_ident0;
    uint32_t hub_ident1;
    uint32_t hub_ident2;
    uint32_t gpu_hlt;
};

struct BCM2712V3DClass {
    /*< private >*/
    SysBusDeviceClass parent_class;
    /*< public >*/
};

#endif /* BCM2712_V3D_H */