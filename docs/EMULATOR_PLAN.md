# Pi 5 emulator plan

The master roadmap lives in the OS repo: `lobster-os/docs/ROADMAP.md`, **Part E**.
This file is only a pointer so the emulator work is discoverable from here.

Quick start:

    ./lobstervm.sh doctor                     # check QEMU fork, kernel, DTB, base disk
    ./lobstervm.sh create dev --display none  # define a VM (own qcow2 disk)
    ./lobstervm.sh start dev --wait           # boot, wait for the desktop
    ./lobstervm.sh shell dev uptime           # run a guest shell command
    ./lobstervm.sh screenshot dev shot.png
    ./lobstervm.sh stop dev

`./lobstervm.sh --help` lists every command.
