"""Per-panel control against a real Nanoleaf device.

Sets each panel to a different colour over HTTP, then runs a short animation
over the UDP streaming path.

    python examples/digital_twin.py 192.168.1.28 --token YOUR_TOKEN

Omit --token to pair: hold the power button for 5-7s first.
"""
from __future__ import annotations

import argparse
import asyncio
import colorsys

from aiohttp import ClientSession

import aionanoleaf2


async def main(host: str, token: str | None) -> None:
    async with ClientSession() as session:
        nanoleaf = aionanoleaf2.Nanoleaf(session, host, auth_token=token)
        if token is None:
            await nanoleaf.authorize()
            print("Paired. Token:", nanoleaf.auth_token)

        await nanoleaf.turn_on()
        twin = await nanoleaf.digital_twin()
        print(f"{len(twin.panel_ids)} panels: {twin.panel_ids}")

        # One hue per panel, written as a static effect over HTTP.
        count = len(twin.panel_ids)
        for index, panel_id in enumerate(twin.panel_ids):
            red, green, blue = colorsys.hsv_to_rgb(index / count, 1.0, 1.0)
            twin.set_color(panel_id, (int(red * 255), int(green * 255), int(blue * 255)))
        await twin.sync(transition=1.0)
        print("Static rainbow written. Holding for 3s.")
        await asyncio.sleep(3)

        # The same buffer, now pushed over UDP one frame at a time.
        print("Streaming a rotating rainbow for 5s...")
        frames = 0
        async with twin.streaming(restore_effect=True):
            start = asyncio.get_running_loop().time()
            while asyncio.get_running_loop().time() - start < 5:
                offset = (asyncio.get_running_loop().time() - start) / 2
                for index, panel_id in enumerate(twin.panel_ids):
                    hue = (index / count + offset) % 1.0
                    red, green, blue = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
                    twin.set_color(
                        panel_id, (int(red * 255), int(green * 255), int(blue * 255))
                    )
                await twin.sync()
                frames += 1
                await asyncio.sleep(1 / 30)
        print(f"Sent {frames} frames. Previous effect restored.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="IPv4 address, IPv6 address or hostname")
    parser.add_argument("--token", help="an existing auth token; omit to pair")
    args = parser.parse_args()
    asyncio.run(main(args.host, args.token))
