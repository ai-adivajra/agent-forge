---
title: "Identifying the installed GPU on Linux using lspci"
category: Command
confidence: 88
---

# Identifying the installed GPU on Linux using lspci

## Summary

Use `lspci | grep -i vga` to identify the installed GPU model and PCI
address. Add `-v` and filter for the VGA entry to see the kernel driver
in use — for AMD cards this shows `amdgpu` under "Kernel driver in use".
The `lspci` command is part of the `pciutils` package.

## Commands

```bash
lspci | grep -i vga
lspci -v | grep -A 10 VGA
```

## Tags

#linux #gpu #hardware #lspci #pciutils
