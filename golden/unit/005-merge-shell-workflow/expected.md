---
title: "Adding a new storage disk on Fedora"
category: Workflow
confidence: 88
---

# Adding a new storage disk on Fedora

## Summary

To add a new disk on Fedora: use `lsblk` to identify the device, mount
it with `mount /dev/sdX /mnt/data`, add an entry to `/etc/fstab` for
persistence across reboots, then verify with `df -h`.

## Commands

```bash
lsblk
mount /dev/sdX /mnt/data
df -h
```

## Files

- `/etc/fstab`

## Tags

#fedora #storage #disk #workflow
