#!/usr/bin/env python3
"""Install the U30 Air-only charging, SOC and Host integration in a Linux 7.2 tree."""
from pathlib import Path
import shutil
import sys


def append_once(path: Path, marker: str, text: str) -> None:
    current = path.read_text()
    if marker not in current:
        path.write_text(current.rstrip() + "\n\n" + text.strip() + "\n")


if len(sys.argv) != 2:
    raise SystemExit("usage: install.py <linux-7.2 tree>")
tree = Path(sys.argv[1]).resolve()
version = (tree / "Makefile").read_text().splitlines()
version = next((line.split("=", 1)[1].strip() for line in version if line.startswith("VERSION =")), "")
patchlevel = next((line.split("=", 1)[1].strip() for line in (tree / "Makefile").read_text().splitlines()
                  if line.startswith("PATCHLEVEL =")), "")
if (version, patchlevel) != ("7", "2"):
    raise SystemExit(f"U30 Air FCC/Host integration supports Linux 7.2.x only, got {version}.{patchlevel}")

here = Path(__file__).resolve().parent
source = here / "fcc-learning" / "source"
power = tree / "drivers" / "power" / "supply"
files = {
    "sc27xx_fuel_gauge.c": power / "sc27xx_fuel_gauge.c",
    "fcc-state.h": power / "fcc-state.h",
    "fcc-quiet-policy.h": power / "fcc-quiet-policy.h",
    "fcc-policy.h": power / "fcc-policy.h",
    "fcc-kernel.inc": power / "fcc-kernel.inc",
    "fgu-soc-policy.h": power / "fgu-soc-policy.h",
    "fgu-soc-reconcile.inc": power / "fgu-soc-reconcile.inc",
    "sgm41511-native.c": power / "sgm41511-native.c",
    "sgm41511-policy.h": power / "sgm41511-policy.h",
    "host-policy.h": power / "host-policy.h",
    "android-charge-policy.h": power / "android-charge-policy.h",
    "charge-limit-policy.h": power / "charge-limit-policy.h",
    "android-charge.inc": power / "android-charge.inc",
}
for name, destination in files.items():
    original = source / name
    if not original.is_file():
        raise SystemExit(f"missing U30 Air source: {original}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original, destination)

# The local DWC3 UDC list is used only to avoid changing an attached gadget to
# Host. It does not touch the charger or enable VBUS.
usb_header = tree / "include" / "linux" / "usb" / "u30air-usb.h"
usb_header.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(here / "source" / "u30air-usb.h", usb_header)
udc = tree / "drivers" / "usb" / "gadget" / "udc" / "core.c"
udc_text = udc.read_text()
if "usb_u30air_gadget_busy" not in udc_text:
    if "udc_lock" not in udc_text or "udc_list" not in udc_text:
        raise SystemExit("the 7.2 UDC core no longer exposes the expected local UDC list")
    include = '#include <linux/usb/u30air-usb.h>'
    if include not in udc_text:
        marker = "#include <linux/usb/gadget.h>"
        if marker not in udc_text:
            raise SystemExit("cannot locate the USB gadget include in drivers/usb/gadget/udc/core.c")
        udc_text = udc_text.replace(marker, marker + "\n" + include, 1)
    udc_text = udc_text.rstrip() + "\n\n" + (here / "source" / "udc-observer.c.inc").read_text().strip() + "\n"
    udc.write_text(udc_text)

makefile = power / "Makefile"
append_once(makefile, "sgm41511-native.o",
            "obj-$(CONFIG_CHARGER_SGM41511_NATIVE) += sgm41511-native.o")
kconfig = power / "Kconfig"
append_once(kconfig, "config CHARGER_SGM41511_NATIVE", """
config CHARGER_SGM41511_NATIVE
	tristate "U30 Air SGM41511 charger and external-powered Host policy"
	depends on I2C && POWER_SUPPLY && REGULATOR && USB_ROLE_SWITCH && USB_GADGET
	depends on ARCH_SPRD
	help
	  Use the U30 Air's SGM41511 for its battery charging and guarded USB Host
	  role. Host requests require valid external input and keep OTG boost off.
""")

print(f"Installed U30 Air FCC/Host sources in {tree}")

# Restore the UMP9620 Type-C power sink stack. It leaves DWC3 data-role
# ownership with mu300-usb and negotiates only the stock fixed 5V PDO.
typec_source = here / "typec"
for relative in (
    "drivers/usb/typec/sc27xx_typec.c",
    "drivers/usb/typec/tcpm/sc27xx_pd.c",
    "drivers/usb/typec/tcpm/sprd_tcpm.c",
    "include/linux/usb/sprd_typec.h",
    "include/linux/usb/sprd_tcpm.h",
    "include/linux/usb/sprd_pd.h",
    "include/linux/usb/sprd_pd_ado.h",
    "include/linux/usb/sprd_pd_bdo.h",
    "include/linux/usb/sprd_pd_ext_sdb.h",
    "include/linux/usb/sprd_pd_vdo.h",
    "include/linux/usb/sprd_commonphy.h",
):
    destination = tree / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(typec_source / relative, destination)
for relative, marker in (
    ("drivers/usb/typec/Kconfig", "config USB_SC27XX_TYPEC"),
    ("drivers/usb/typec/tcpm/Kconfig", "config SPRD_TYPEC_TCPM"),
):
    append_once(tree / relative, marker, (typec_source / relative).read_text())
append_once(tree / "drivers/usb/typec/Makefile", "CONFIG_USB_SC27XX_TYPEC",
            "obj-$(CONFIG_USB_SC27XX_TYPEC) += sc27xx_typec.o")
append_once(tree / "drivers/usb/typec/Makefile", "CONFIG_SPRD_TYPEC_TCPM",
            "obj-$(CONFIG_SPRD_TYPEC_TCPM) += tcpm/")
append_once(tree / "drivers/usb/typec/tcpm/Makefile", "CONFIG_SPRD_TYPEC_TCPM",
            "obj-$(CONFIG_SPRD_TYPEC_TCPM) += sprd_tcpm.o")
append_once(tree / "drivers/usb/typec/tcpm/Makefile", "CONFIG_SC27XX_PD",
            "obj-$(CONFIG_SC27XX_PD) += sc27xx_pd.o")
