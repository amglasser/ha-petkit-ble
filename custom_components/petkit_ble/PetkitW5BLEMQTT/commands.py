import json
import asyncio
from .utils import Utils

class Commands:
    def __init__(self, ble_manager, device, logger):
        self.ble_manager = ble_manager
        self.device = device
        self.logger = logger  # Use the centralized logger
        self.sequence = 0
        # Secret used by cmd 86 (sync). Overwritten by init_device() once the
        # device id is known.
        self.secret = [0, 0, 0, 0, 0, 0, 13, 37]

    def increment_sequence(self):
        self.sequence += 1

        # If sequence is too large, we'll reset
        if self.sequence > 255:
            self.sequence = 0

    def init_device_data(self, service_data=None, rssi=None):
        """Identify the device model from the advertisement service data.

        service_data is the dict of {uuid: bytes} from the advertisement. The
        model identifier is the 6th byte of the concatenated service data.
        Unknown or missing data falls back to a generic profile instead of
        raising.
        """
        if rssi is not None:
            self.device.status = {"rssi": rssi}

        identifier = None
        if service_data:
            discovered_bytes = Utils.bytes_to_unsigned_integers(Utils.combine_byte_arrays(service_data))
            self.logger.debug(f"Received ServiceData {discovered_bytes}")
            if len(discovered_bytes) > 5:
                identifier = discovered_bytes[5]

        device_properties = Utils.get_device_properties(identifier if identifier is not None else -1)

        self.device.name = device_properties['name']
        self.device.name_readable = device_properties['name'].replace("_", " ") # Replace _ with space
        self.device.alias = device_properties['alias']
        self.device.product_name = device_properties['product_name']
        self.device.device_type = device_properties['device_type']
        self.device.type_code = device_properties['type_code']

    async def _wait_for(self, predicate, timeout):
        """Poll until predicate() is true or timeout (seconds) expires."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.1)
        return bool(predicate())

    async def init_session(self):
        """Run the Petkit handshake on a fresh BLE connection.

        Order follows the reference flow of the upstream library:
        213 (identify) -> 73 (init) -> 86 (sync with secret) -> 84 (set time),
        then read firmware, battery, state and config.
        Raises TimeoutError when the device never answers the identify command.
        """
        # 1. Identify (device id + serial). Retry a few times, the first frame
        #    after connecting is sometimes lost.
        # A device may legitimately report an all-zero id, so "answered" means
        # either a non-zero id or a serial number was parsed from the reply.
        def identified() -> bool:
            return bool(self.device.device_id) or self.device.serial not in ("Uninitialized", 0, "")

        for _ in range(4):
            await self.get_device_details()
            if await self._wait_for(identified, 2.0):
                break
        if not identified():
            raise TimeoutError("Device did not answer the identify command (213)")

        # 2. Init + sync. Retry once when the device does not confirm.
        #    The confirmation belongs to the current session, so clear the old one.
        self.device.device_initialized = 0
        for attempt in range(2):
            await self.init_device()
            await asyncio.sleep(0.75)
            await self.get_device_sync()
            if await self._wait_for(lambda: bool(self.device.device_initialized), 3.0):
                break
            self.logger.debug(f"Device did not confirm sync (attempt {attempt + 1})")
        if not self.device.device_initialized:
            self.logger.warning("Device did not confirm the sync command; continuing anyway")

        await asyncio.sleep(0.5)
        await self.set_datetime()
        await asyncio.sleep(0.5)

        # 3. Read everything once so entities have values straight away.
        await self.get_device_info()
        await asyncio.sleep(0.5)
        await self.get_battery()
        await asyncio.sleep(0.5)
        await self.get_device_state()
        await asyncio.sleep(0.5)
        await self.get_device_config()
        await asyncio.sleep(0.5)
        await self.get_device_update()
        await asyncio.sleep(0.5)

    async def init_device_connection(self):
        # Basically this function secures the sequence
        # of which we're sending the commands.
        # At the same time, we're ensuring we're getting
        # the necessary parameters registered - e.g. serial
        await self.get_device_details()
        await asyncio.sleep(1.5)
        
        await self.init_device()
        
        await self.get_device_sync()
        await asyncio.sleep(0.75)
        await self.set_datetime()
        await asyncio.sleep(0.75)
        
        while self.device.serial in "Uninitialized" or self.device.serial == 0:
            await self.get_device_details()
            await asyncio.sleep(1.5)
        
        if not self.device.device_initialized:
            await self.init_device()
            await asyncio.sleep(3.0)
            
            await self.ble_manager.disconnect_device(self.mac)
            await asyncio.sleep(1.0)
            await self.ble_manager.connect_device(self.mac)
            await asyncio.sleep(1.0)
            await self.init_device_connection()

        await self.get_device_info()
        await asyncio.sleep(0.75)
        await self.get_device_type()
        await asyncio.sleep(0.75)
        await self.get_battery()
        await asyncio.sleep(0.75)
        await self.get_device_state()
        await asyncio.sleep(0.75)
        await self.get_device_config()

    async def get_battery(self):
        cmd = 66                            # Command for getting device details
        type = 1                            # Sending 1
        seq = self.sequence                 # Example sequence number       
        data = [0, 0]                       # Placeholders
        
        bytes = Utils.build_command(seq, cmd, type, data)
        await self.ble_manager.message_producer(bytes)
        
        self.increment_sequence()
        
        self.logger.info(f"Queued command: {cmd}")
        return

    async def init_device(self):
        cmd = 73                            # Command for getting device details
        type = 1                            # Sending 1
        seq = self.sequence                 # Example sequence number       

        # In case you initialize the device using this class
        # the device_id will be erased after CMD 73
        # there seems to be somekind of validation of device_id vs secret
        # Should you want to control the device through 
        # the Petkit app, of some strange reason you will need to power cycle the device

        # Reverse the device_id_bytes array
        # replace the last two zeroes with 13 37 
        # and pad the array with zeroes to use as secret
        self.secret = Utils.pad_array(Utils.replace_last_two_if_zero(Utils.reverse_unsigned_array(self.device.device_id_bytes)), 8)
        
        # Pad the device_id_bytes with zeroes
        device_id = Utils.pad_array(self.device.device_id_bytes, 8)
        
        data = [0, 0] + device_id + self.secret    # Placeholders
        self.logger.debug(f"Device ID: {device_id}")
        self.logger.debug(f"Secret: {self.secret}")
        self.logger.debug(f"Data: {data}")
        
        bytes = Utils.build_command(seq, cmd, type, data)
        await self.ble_manager.message_producer(bytes)
        
        self.increment_sequence()
        
        self.logger.info(f"Queued command: {cmd}")
        return

    async def set_datetime(self):
        cmd = 84                            # Command for getting device details
        type = 1                            # Sending 1
        seq = self.sequence                 # Example sequence number       
        data = Utils.time_in_bytes()        # Datetime data
        
        bytes = Utils.build_command(seq, cmd, type, data)
        await self.ble_manager.message_producer(bytes)
        
        self.increment_sequence()
        
        self.logger.info(f"Queued command: {cmd}")
        return

    async def get_device_sync(self):
        cmd = 86                            # Command for getting device details
        type = 1                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number
        #data = [0, 0, 253, 54, 124, 210, 241, 44]   # What's going on here?
        data = [0, 0] + self.secret         # What's going on here?
        
        bytes = Utils.build_command(seq, cmd, type, data)
        await self.ble_manager.message_producer(bytes)
        
        self.increment_sequence()
        
        self.logger.info(f"Queued command: {cmd}")
        return

    async def get_device_info(self):
        cmd = 200                           # Command for getting device details
        type = 1                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number
        data = [] 
        
        bytes = Utils.build_command(seq, cmd, type, data)
        await self.ble_manager.message_producer(bytes)
        
        self.increment_sequence()
        
        self.logger.info(f"Queued command: {cmd}")
        return
        
    async def get_device_type(self):
        cmd = 201                           # Command for getting device details
        type = 1                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number
        data = [] 
        
        bytes = Utils.build_command(seq, cmd, type, data)
        await self.ble_manager.message_producer(bytes)
        
        self.increment_sequence()
        
        self.logger.info(f"Queued command: {cmd}")
        return
        
    async def get_device_state(self):
        cmd = 210                           # Command for getting device details
        type = 1                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number
        data = [0, 0] 
        
        bytes = Utils.build_command(seq, cmd, type, data)
        await self.ble_manager.message_producer(bytes)
        
        self.increment_sequence()
        
        self.logger.info(f"Queued command: {cmd}")
        return
        
    async def get_device_config(self):
        cmd = 211                           # Command for getting device details
        type = 1                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number
        data = [0, 0] 
        
        bytes = Utils.build_command(seq, cmd, type, data)
        await self.ble_manager.message_producer(bytes)
        
        self.increment_sequence()
        
        self.logger.info(f"Queued command: {cmd}")
        return

    async def get_device_details(self):
    
        if self.device.device_id:
            return
        
        cmd = 213                           # Command for getting device details
        type = 1                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number
        data = [0, 0]                       # No additional data for this command
        
        bytes = Utils.build_command(seq, cmd, type, data)
        await self.ble_manager.message_producer(bytes)
        
        self.increment_sequence()
        
        self.logger.info(f"Queued command: {cmd}")
        return
    
    # Not used -- maybe never
    async def set_light_setting(self):
        cmd = 215                           # Command for getting device details
        type = 1                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number
        data = [0]                          # 0 resets it

        return

    # Not used -- maybe never
    async def set_dnd_setting(self):
        cmd = 216                           # Command for getting device details
        type = 1                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number
        data = [0]                          # 0 resets it

        return

    async def set_device_mode(self, state, mode):
        cmd = 220                           # Command for getting device details
        type = 1                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number
        data = [state, mode]                # State 1 for on, 0 for off - Mode 1 for normal, 2 for smart
        
        bytes = Utils.build_command(seq, cmd, type, data)
        await self.ble_manager.message_producer(bytes)
        
        self.increment_sequence()
        
        self.logger.info(f"Queued command: {cmd}")
        return

    #async def set_device_config(self, smart_time_on, smart_time_off, led_switch, led_brightness, led_light_time_on_1, led_light_time_on_2, led_light_time_off_1, led_light_time_off_2, do_not_disturb_switch, do_not_disturb_time_start_1, do_not_disturb_time_start_2, do_not_disturb_time_end_1, do_not_disturb_time_end_2, is_locked):
    async def set_device_config(self, data):
        cmd = 221                           # Command for getting device details
        type = 1                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number       
        #data = [smart_time_on, smart_time_off, led_switch, led_brightness, led_light_time_on_1, led_light_time_on_2, led_light_time_off_1, led_light_time_off_2, do_not_disturb_switch, do_not_disturb_time_start_1, do_not_disturb_time_start_2, do_not_disturb_time_end_1, do_not_disturb_time_end_2, is_locked]                # State 1 for on, 0 for off - Mode 1 for normal, 2 for smart
        
        bytes = Utils.build_command(seq, cmd, type, data)
        await self.ble_manager.message_producer(bytes)
        
        self.increment_sequence()
        
        self.logger.info(f"Queued command: {cmd}")
        return

    async def set_reset_filter(self):
        cmd = 222                           # Command for getting device details
        type = 1                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number
        data = [0]                          # 0 resets it
        
        bytes = Utils.build_command(seq, cmd, type, data)
        await self.ble_manager.message_producer(bytes)
        
        self.increment_sequence()
        
        self.logger.info(f"Queued command: {cmd}")
        return

    # Not used -- maybe never
    async def set_updated_light(self):
        cmd = 225                           # Command for getting device details
        type = 1                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number
        data = [0]                          # 0 resets it

        return

    # Not used -- maybe never
    async def set_updated_dnd(self):
        cmd = 226                           # Command for getting device details
        type = 1                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number
        data = [0]                          # 0 resets it

        return
        
    async def get_device_update(self):
        cmd = 230                           # Command for getting device details
        type = 2                            # Type is 1 for sending - 2 for receiving
        seq = self.sequence                 # Example sequence number
        data = [1]                      # No additional data for this command
        
        bytes = Utils.build_command(seq, cmd, type, data)
        await self.ble_manager.message_producer(bytes)
        
        self.increment_sequence()
        
        self.logger.info(f"Queued command: {cmd}")
        return