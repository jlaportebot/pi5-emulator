/*
 * VideoCore VII / V3D GPU emulation (Raspberry Pi 5)
 *
 * Copyright (C) 2025 Mister Lobster
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/module.h"
#include "hw/sysbus.h"
#include "hw/display/bcm2835_fb.h"
#include "hw/arm/bcm2712_v3d.h"

#define V3D_REG_SIZE 0x100000

static uint64_t bcm2712_v3d_read(void *opaque, hwaddr offset, unsigned size)
{
    BCM2712V3DState *s = (BCM2712V3DState *)opaque;
    uint32_t val = 0;

    switch (offset) {
    case V3D_IDENT0:
        val = s->ident0 ? s->ident0 : 0x02443356; /* V3D 4.3 */
        break;
    case V3D_IDENT1:
        val = s->ident1 ? s->ident1 : 0x00000000;
        break;
    case V3D_IDENT2:
        val = s->ident2 ? s->ident2 : 0x00000000;
        break;
    case V3D_SCRATCH:
        val = s->scratch;
        break;
    case V3D_GCACTL:
        val = s->gca_ctl;
        break;
    case V3D_GPFIFOCTL:
        val = s->gpfifo_ctl;
        break;
    case V3D_GPCTL:
        val = s->gp_ctl;
        break;
    case V3D_CLE_SQ:
        val = s->cle_sq;
        break;
    case V3D_CLE_WAKEUP:
        val = s->cle_wakeup;
        break;
    case V3D_CLE_INT_CTL:
        val = s->cle_int_ctl;
        break;
    case V3D_CLE_INT_EN:
        val = s->cle_int_en;
        break;
    case V3D_CLE_INT_DIS:
        val = s->cle_int_dis;
        break;
    case V3D_CLE_INT_STAT:
        val = s->cle_int_stat;
        break;
    case V3D_CT0CS:
        val = s->ct0cs;
        break;
    case V3D_CT1CS:
        val = s->ct1cs;
        break;
    case V3D_CT0EA:
        val = s->ct0ea;
        break;
    case V3D_CT0CA:
        val = s->ct0ca;
        break;
    case V3D_CT0RA:
        val = s->ct0ra;
        break;
    case V3D_CT1EA:
        val = s->ct1ea;
        break;
    case V3D_CT1CA:
        val = s->ct1ca;
        break;
    case V3D_CT1RA:
        val = s->ct1ra;
        break;
    case V3D_BFC:
        val = s->bfc;
        break;
    case V3D_PCTRC:
        val = s->pctrc;
        break;
    case V3D_PCTRE:
        val = s->pctre;
        break;
    case V3D_DBGE:
        val = s->dbge;
        break;
    case V3D_DBGC:
        val = s->dbgc;
        break;
    case V3D_DBGR:
        val = s->dbgr;
        break;
    case V3D_DBGD:
        val = s->dbgd;
        break;
    case V3D_SRQPC:
        val = s->srqpc;
        break;
    case V3D_SRQUL:
        val = s->srqul;
        break;
    case V3D_SRQCS:
        val = s->srqcs;
        break;
    case V3D_VPMBASE:
        val = s->vpmbase;
        break;
    case V3D_L2CACTL:
        val = s->l2cactl;
        break;
    case V3D_SLCACTL:
        val = s->slcactl;
        break;
    case V3D_HUB_IDENT:
        val = s->hub_ident ? s->hub_ident : 0x02443356;
        break;
    case V3D_HUB_IDENT0:
        val = s->hub_ident0;
        break;
    case V3D_HUB_IDENT1:
        val = s->hub_ident1;
        break;
    case V3D_HUB_IDENT2:
        val = s->hub_ident2;
        break;
    case V3D_GPU_HLT:
        val = s->gpu_hlt;
        break;
    default:
        /* Unknown register - return 0 */
        break;
    }

    return val;
}

static void bcm2712_v3d_write(void *opaque, hwaddr offset,
                               uint64_t value, unsigned size)
{
    BCM2712V3DState *s = (BCM2712V3DState *)opaque;

    switch (offset) {
    case V3D_SCRATCH:
        s->scratch = value;
        break;
    case V3D_GCACTL:
        s->gca_ctl = value;
        break;
    case V3D_GPFIFOCTL:
        s->gpfifo_ctl = value;
        break;
    case V3D_GPCTL:
        s->gp_ctl = value;
        if (value & V3D_GPCTL_RESET) {
            /* Reset GPU */
            s->cle_sq = 0;
            s->cle_wakeup = 0;
            s->cle_int_stat = 0;
            s->bfc = 0;
        }
        break;
    case V3D_CLE_SQ:
        s->cle_sq = value;
        break;
    case V3D_CLE_WAKEUP:
        s->cle_wakeup = value;
        break;
    case V3D_CLE_INT_CTL:
        s->cle_int_ctl = value;
        break;
    case V3D_CLE_INT_EN:
        s->cle_int_en = value;
        break;
    case V3D_CLE_INT_DIS:
        s->cle_int_dis = value;
        s->cle_int_en &= ~value;
        break;
    case V3D_CLE_INT_STAT:
        s->cle_int_stat = value;
        break;
    case V3D_CT0CS:
        s->ct0cs = value;
        break;
    case V3D_CT1CS:
        s->ct1cs = value;
        break;
    case V3D_CT0EA:
        s->ct0ea = value;
        break;
    case V3D_CT0CA:
        s->ct0ca = value;
        break;
    case V3D_CT0RA:
        s->ct0ra = value;
        break;
    case V3D_CT1EA:
        s->ct1ea = value;
        break;
    case V3D_CT1CA:
        s->ct1ca = value;
        break;
    case V3D_CT1RA:
        s->ct1ra = value;
        break;
    case V3D_BFC:
        s->bfc = value;
        break;
    case V3D_PCTRC:
        s->pctrc = value;
        break;
    case V3D_PCTRE:
        s->pctre = value;
        break;
    case V3D_DBGE:
        s->dbge = value;
        break;
    case V3D_DBGC:
        s->dbgc = value;
        break;
    case V3D_DBGR:
        s->dbgr = value;
        break;
    case V3D_DBGD:
        s->dbgd = value;
        break;
    case V3D_SRQPC:
        s->srqpc = value;
        break;
    case V3D_SRQUL:
        s->srqul = value;
        break;
    case V3D_SRQCS:
        s->srqcs = value;
        break;
    case V3D_VPMBASE:
        s->vpmbase = value;
        break;
    case V3D_L2CACTL:
        s->l2cactl = value;
        break;
    case V3D_SLCACTL:
        s->slcactl = value;
        break;
    case V3D_GPU_HLT:
        s->gpu_hlt = value;
        break;
    default:
        /* Unknown register - ignore write */
        break;
    }
}

static const MemoryRegionOps bcm2712_v3d_ops = {
    .read = bcm2712_v3d_read,
    .write = bcm2712_v3d_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
};

static void bcm2712_v3d_init(Object *obj)
{
    BCM2712V3DState *s = BCM2712_V3D(obj);

    memory_region_init_io(&s->v3d_mr, obj, &bcm2712_v3d_ops, s,
                          "bcm2712-v3d", V3D_REG_SIZE);
    /* Don't call sysbus_init_mmio - we'll map it manually in bcm2712_peripherals_realize */

    /* MMU registers */
    memory_region_init(&s->mmu_mr, obj, "bcm2712-v3d-mmu", V3D_MMU_SIZE);

    /* Cache registers */
    memory_region_init(&s->cache_mr, obj, "bcm2712-v3d-cache", V3D_CACHE_SIZE);

    /* Framebuffer - use BCM2835FB for display output */
    object_initialize_child(obj, "fb", &s->fb, TYPE_BCM2835_FB);
}

static void bcm2712_v3d_realize(DeviceState *dev, Error **errp)
{
    BCM2712V3DState *s = BCM2712_V3D(dev);

    /* Initialize default register values */
    s->ident0 = 0x02443356;  /* V3D 4.3 */
    s->ident1 = 0x00000000;
    s->ident2 = 0x00000000;
    s->hub_ident = 0x02443356;
    s->hub_ident0 = 0x00000000;
    s->hub_ident1 = 0x00000000;
    s->hub_ident2 = 0x00000000;
    s->gca_ctl = V3D_GCACTL_DBGE;
    s->l2cactl = V3D_L2CACTL_L2CCE | V3D_L2CACTL_L2CPE;
    s->slcactl = V3D_SLCACTL_SLCCE | V3D_SLCACTL_SLCPE;

    /* Realize framebuffer with vcram-base and vcram-size for graphical display */
    object_property_set_uint(OBJECT(&s->fb), "vcram-base", 0x8000000, &error_abort);
    object_property_set_uint(OBJECT(&s->fb), "vcram-size", 0x4000000, &error_abort);
    object_property_set_uint(OBJECT(&s->fb), "xres", 1920, &error_abort);
    object_property_set_uint(OBJECT(&s->fb), "yres", 1080, &error_abort);
    object_property_set_uint(OBJECT(&s->fb), "bpp", 32, &error_abort);

    if (!sysbus_realize(SYS_BUS_DEVICE(&s->fb), errp)) {
        return;
    }
}

static void bcm2712_v3d_class_init(ObjectClass *oc, void *data)
{
    DeviceClass *dc = DEVICE_CLASS(oc);
    dc->realize = bcm2712_v3d_realize;
}

static const TypeInfo bcm2712_v3d_type_info = {
    .name = TYPE_BCM2712_V3D,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2712V3DState),
    .instance_init = bcm2712_v3d_init,
    .class_size = sizeof(BCM2712V3DClass),
    .class_init = bcm2712_v3d_class_init,
};

static void bcm2712_v3d_register_types(void)
{
    type_register_static(&bcm2712_v3d_type_info);
}

type_init(bcm2712_v3d_register_types)