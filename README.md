# bl60x-flash
[![PyPI version](https://badge.fury.io/py/bl60x-flash.svg)](https://badge.fury.io/py/bl60x-flash)

Tool to program the flash on Bouffalo Labs BL602 and BL604 chips.

Talks via serial port to the bootrom, so there is no risk of bricking your device.

Tested with the [Pine64 PineCone](https://www.pine64.org/2020/10/28/nutcracker-challenge-blob-free-wifi-ble/), but should work the same for other BL60x evaluation boards. **Make sure to set the jumper for IO8 to the H(igh) position so the board resets into bootrom mode**.

Tested with the DT-BL10 DevKit. Press D8, press and release EN, then release D8 to enter the bootrom mode.

## Usage

Install via PyPI:

    pip install bl60x-flash

Then invoke with serial port and firmware binary, e.g.:

    bl60x-flash COM4 bl602_demo_wifi.bin

Currently, the tool will only flash the area starting at 0x10000, leaving out others such as the partition table. This will change as we learn more about these chips.
