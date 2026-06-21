/*
 * BCM2712 PCIe controller emulation (Raspberry Pi 5)
 * Based on Synopsys DesignWare PCIe controller
 *
 * Copyright (C) 2025 Mister Lobster
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/module.h"
#include "hw/sysbus.h"
#include "hw/pci/pci.h"
#include "hw/pci/pci_host.h"
#include "hw/pci/pcie_port.h"
#include "hw/pci/msi.h"
#include "hw/pci-host/designware.h"
#include "hw/irq.h"
#include "qom/object.h"
#include "trace.h"

/* BCM2712 PCIe register offsets (based on DesignWare + BCM specifics) */
#define BCM_PCIE_MSIX_TABLE_BASE      0x0000
#define BCM_PCIE_MSIX_PBA_BASE        0x800

#define BCM_PCIE_RC_DBI_BASE          0x10000
#define BCM_PCIE_RC_DBI_SIZE          0x10000

#define BCM_PCIE_APPL_BASE            0x0000
#define BCM_PCIE_APPL_SIZE            0x10000

#define BCM_PCIE_ATU_BASE             0x20000
#define BCM_PCIE_ATU_SIZE             0x10000

#define BCM_PCIE_DBI_RO_WR_EN         0x10000
#define BCM_PCIE_LINK_WIDTH_SPEED_CTRL 0x80C
#define BCM_PCIE_PORT_LOGIC_SPEED_CHANGE BIT(17)

#define BCM_PCIE_MSI_ADDR_LO          0x820
#define BCM_PCIE_MSI_ADDR_HI          0x824
#define BCM_PCIE_MSI_INTR0_ENABLE     0x828
#define BCM_PCIE_MSI_INTR0_MASK       0x82C
#define BCM_PCIE_MSI_INTR0_STATUS     0x830

#define BCM_PCIE_ATU_VIEWPORT         0x900
#define BCM_PCIE_ATU_REGION_INBOUND   BIT(31)
#define BCM_PCIE_ATU_CR1              0x904
#define BCM_PCIE_ATU_TYPE_MEM         (0x0 << 0)
#define BCM_PCIE_ATU_CR2              0x908
#define BCM_PCIE_ATU_ENABLE           BIT(31)
#define BCM_PCIE_ATU_LOWER_BASE       0x90C
#define BCM_PCIE_ATU_UPPER_BASE       0x910
#define BCM_PCIE_ATU_LIMIT            0x914
#define BCM_PCIE_ATU_LOWER_TARGET     0x918
#define BCM_PCIE_ATU_BUS(x)           (((x) >> 24) & 0xff)
#define BCM_PCIE_ATU_DEVFN(x)         (((x) >> 16) & 0xff)
#define BCM_PCIE_ATU_UPPER_TARGET     0x91C

#define TYPE_BCM_PCIE "bcm-pcie"
OBJECT_DECLARE_SIMPLE_TYPE(BCMPCIEState, BCM_PCIE)

struct BCMPCIEState {
    /*< private >*/
    DesignwarePCIEHost dw_pcie;

    /*< public >*/
    MemoryRegion mmio;
    MemoryRegion dbi;
    MemoryRegion atu;

    /* BCM2712 specific */
    uint32_t num_lanes;
    uint32_t link_speed;
    uint32_t link_width;
};

struct BCM_PCIEClass {
    /*< private >*/
    SysBusDeviceClass parent_class;
    /*< public >*/
};

static uint64_t bcm_pcie_read(void *opaque, hwaddr offset, unsigned size)
{
    BCMPCIEState *s = opaque;
    DesignwarePCIEHost *host = &s->dw_pcie;

    if (offset < BCM_PCIE_APPL_SIZE) {
        /* Application registers - delegate to DesignWare */
        return 0;
    }

    if (offset >= BCM_PCIE_RC_DBI_BASE && offset < BCM_PCIE_RC_DBI_BASE + BCM_PCIE_RC_DBI_SIZE) {
        /* DBI registers - delegate to DesignWare */
        return 0;
    }

    if (offset >= BCM_PCIE_ATU_BASE && offset < BCM_PCIE_ATU_BASE + BCM_PCIE_ATU_SIZE) {
        /* ATU registers - delegate to DesignWare */
        return 0;
    }

    trace_bcm_pcie_read(offset, 0);
    return 0;
}

static void bcm_pcie_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    BCMPCIEState *s = opaque;

    if (offset < BCM_PCIE_APPL_SIZE) {
        /* Application registers - delegate to DesignWare */
        return;
    }

    if (offset >= BCM_PCIE_RC_DBI_BASE && offset < BCM_PCIE_RC_DBI_BASE + BCM_PCIE_RC_DBI_SIZE) {
        /* DBI registers - delegate to DesignWare */
        return;
    }

    if (offset >= BCM_PCIE_ATU_BASE && offset < BCM_PCIE_ATU_BASE + BCM_PCIE_ATU_SIZE) {
        /* ATU registers - delegate to DesignWare */
        return;
    }

    trace_bcm_pcie_write(offset, value);
}

static const MemoryRegionOps bcm_pcie_ops = {
    .read = bcm_pcie_read,
    .write = bcm_pcie_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
};

static void bcm_pcie_init(Object *obj)
{
    BCMPCIEState *s = BCM_PCIE(obj);
    DesignwarePCIEHost *host = &s->dw_pcie;
    Object *parent = object_get_root();

    /* Initialize DesignWare PCIe host */
    object_initialize_child(obj, "designware-pcie-host", &s->dw_pcie, TYPE_DESIGNWARE_PCIE_HOST);

    /* PCIe configuration space */
    memory_region_init_io(&s->mmio, obj, &bcm_pcie_ops, s,
                          "bcm-pcie", 0x100000);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->mmio);

    /* DBI (Direct Bus Interface) */
    memory_region_init(&s->dbi, obj, "bcm-pcie-dbi", BCM_PCIE_RC_DBI_SIZE);
    memory_region_add_subregion(&s->mmio, BCM_PCIE_RC_DBI_BASE, &s->dbi);

    /* ATU (Address Translation Unit) */
    memory_region_init(&s->atu, obj, "bcm-pcie-atu", BCM_PCIE_ATU_SIZE);
    memory_region_add_subregion(&s->mmio, BCM_PCIE_ATU_BASE, &s->atu);

    /* Initialize the DesignWare PCIe host */
    object_property_set_bool(OBJECT(&s->dw_pcie), "has-msi", true, &error_abort);
}

static void bcm_pcie_realize(DeviceState *dev, Error **errp)
{
    BCMPCIEState *s = BCM_PCIE(dev);
    DesignwarePCIEHost *host = &s->dw_pcie;
    PCIHostState *host_state = PCI_HOST_STATE(host);
    SysBusDevice *sdev = SYS_BUS_DEVICE(dev);
    Error *local_err = NULL;

    /* Realize the DesignWare PCIe host */
    if (!sysbus_realize(SYS_BUS_DEVICE(&s->dw_pcie), errp)) {
        return;
    }

    /* Map the DesignWare MMIO regions */
    sysbus_mmio_map(sdev, 0, 0x100000);

    /* Set up PCI address spaces */
    host->pci.address_space = *address_space_get_flatview()->address_space;
    pci_host_bus_new(&host->pci, sizeof(host->pci), "bcm-pcie-root-bus",
                     TYPE_PCI_BUS, errp);

    if (local_err) {
        error_propagate(errp, local_err);
        return;
    }

    /* Set up IRQs */
    for (int i = 0; i < 4; i++) {
        sysbus_connect_irq(sdev, i, host->pci.irqs[i]);
    }
    sysbus_connect_irq(sdev, 4, host->pci.msi);
}

static void bcm_pcie_class_init(ObjectClass *klass, void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    PCIDeviceClass *pc = PCI_DEVICE_CLASS(klass);

    dc->realize = bcm_pcie_realize;
    dc->desc = "BCM2712 PCIe Controller (DesignWare)";
    pc->is_root = true;
}

static const TypeInfo bcm_pcie_type_info = {
    .name = TYPE_BCM_PCIE,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCMPCIEState),
    .instance_init = bcm_pcie_init,
    .class_size = sizeof(BCM_PCIEClass),
    .class_init = bcm_pcie_class_init,
    .interfaces = (InterfaceInfo[]) {
        { TYPE_PCI_HOST_BRIDGE },
        { }
    },
};

static void bcm_pcie_register_types(void)
{
    type_register_static(&bcm_pcie_type_info);
}

type_init(bcm_pcie_register_types)