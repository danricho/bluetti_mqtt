import asyncio
import itertools
import logging
import re
from typing import Dict, List, Set, Type
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bluetti_mqtt.bus import ParserMessage
from bluetti_mqtt.device import BluettiDevice
from bluetti_mqtt.parser import (
    ControlPageParser,
    DataParser,
    LowerStatusPageParser,
    MidStatusPageParser
)
from bluetti_mqtt.bluetooth.client import BluetoothClient
from bluetti_mqtt.bluetooth.exc import BadConnectionError, ParseError


DEVICE_NAME_RE = re.compile(r'^(AC200M|AC300|EP500P|EP500)(\d+)$')


class BluetoothClientHandler:
    devices: List[BluettiDevice]
    clients: Dict[BluettiDevice, BluetoothClient]

    def __init__(self, addresses: Set[str], bus: asyncio.Queue):
        self.addresses = addresses
        self.bus = bus
        self.devices = []
        self.clients = []

    async def check(self):
        logging.debug(f'Checking we can connect: {self.addresses}')
        devices = await BleakScanner.discover()
        filtered = [d for d in devices if d.address in self.addresses]
        logging.debug(f'Found devices: {filtered}')
        if len(filtered) == len(self.addresses):
            def build_device(device: BLEDevice) -> BluettiDevice:
                match = DEVICE_NAME_RE.match(device.name)
                return BluettiDevice(device.address, match[1], match[2])
            self.devices = [build_device(d) for d in filtered]
        return self.devices
    
    async def run(self):
        loop = asyncio.get_running_loop()

        # Start clients
        logging.debug(f'Connecting to clients: {self.addresses}')
        self.clients = {d: BluetoothClient(d.address) for d in self.devices}
        self.client_tasks = [loop.create_task(c.run()) for c in self.clients.values()]

        # Poll the clients
        logging.debug('Starting to poll clients...')
        await asyncio.gather(*[self._poll(d, c) for d, c in self.clients.items()])
    
    async def _poll(self, device: BluettiDevice, client: BluetoothClient):
        parsers = [LowerStatusPageParser, MidStatusPageParser, ControlPageParser]
        parser: Type[DataParser]
        for parser in itertools.cycle(parsers):
            if not client.is_connected:
                logging.debug(f'Waiting for connection to {device.address} to start polling...')
                await asyncio.sleep(1)
                continue

            command = parser.build_query_command()
            result_future = await client.perform(command)
            try:
                result = await result_future
                parsed = parser(result[3:-2]).parse()
                await self.bus.put(ParserMessage(device, parsed))
            except ParseError:
                logging.debug('Got a parse exception...')
            except BadConnectionError as err:
                logging.debug(f'Needed to disconnect due to error: {err}')



async def scan_devices():
    print('Scanning....')
    devices = await BleakScanner.discover()
    if len(devices) == 0:
        print('0 devices found - something probably went wrong')
    else:
        bluetti_devices = [d for d in devices if DEVICE_NAME_RE.match(d.name)]
        for d in bluetti_devices:
            print(f'Found {d.name}: address {d.address}')