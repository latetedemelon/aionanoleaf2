"""Manual smoke test against a real Nanoleaf device.

This talks to real hardware, so it is not part of the automated test suite.
Hold the device's power button for 5-7s, then run:

    python examples/manual_smoke_test.py 192.168.1.28

Pass --deauthorize to revoke the token again when the run finishes.
"""
from __future__ import annotations

import argparse
import asyncio

from aiohttp import ClientSession

import aionanoleaf2


async def main(host: str, revoke: bool) -> None:
    async with ClientSession() as session:
        nanoleaf = aionanoleaf2.Nanoleaf(session, host)
        try:
            await nanoleaf.authorize()
        except aionanoleaf2.Unauthorized as ex:
            print("Not authorized:", ex)
            return

        await nanoleaf.turn_on()
        await nanoleaf.get_info()

        print("Host:", nanoleaf.host)
        print("Port:", nanoleaf.port)
        print("Name:", nanoleaf.name)
        print("Manufacturer:", nanoleaf.manufacturer)
        print("Model:", nanoleaf.model)
        print("Serial number:", nanoleaf.serial_no)
        print("Hardware version:", nanoleaf.hardware_version)
        print("Firmware version:", nanoleaf.firmware_version)
        print("On:", nanoleaf.is_on)
        print("Brightness:", f"{nanoleaf.brightness} [{nanoleaf.brightness_min} - {nanoleaf.brightness_max}]")
        print("Hue:", f"{nanoleaf.hue} [{nanoleaf.hue_min} - {nanoleaf.hue_max}]")
        print("Saturation:", f"{nanoleaf.saturation} [{nanoleaf.saturation_min} - {nanoleaf.saturation_max}]")
        print("Color temperature:", f"{nanoleaf.color_temperature} [{nanoleaf.color_temperature_min} - {nanoleaf.color_temperature_max}]")
        print("Color mode:", nanoleaf.color_mode)
        print("Effects:", nanoleaf.effects_list)
        print("Selected effect:", nanoleaf.selected_effect)
        print("Emersions:", nanoleaf.emersion_list)
        print("Selected emersion:", nanoleaf.selected_emersion)
        print("Panels:", len(nanoleaf.panels))

        await nanoleaf.identify()
        await nanoleaf.turn_off()

        if revoke:
            await nanoleaf.deauthorize()
            print("Token revoked.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="IPv4 address, IPv6 address or hostname of the device")
    parser.add_argument(
        "--deauthorize",
        action="store_true",
        help="revoke the auth token on exit (you will need to re-pair the device)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.host, args.deauthorize))
