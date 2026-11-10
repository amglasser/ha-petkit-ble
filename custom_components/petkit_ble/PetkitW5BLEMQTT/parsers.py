from .utils import Utils

def _pad(data, length):
    """Return `data` as a list padded with zeros to `length`.

    The CTW3 sends shorter payloads than the W5 (its config reply is 12 bytes,
    not 14). A short payload must degrade to zero defaults, not raise.
    """
    data = list(data)
    return data + [0] * (length - len(data))


def _is_ctw3(alias):
    """The CTW3 sends different payload layouts than the W5 family."""
    return alias == "CTW3"


class Parsers:
    # Get Battery Synchronization
    @staticmethod
    def device_battery(data, alias):
        return {
            "voltage": ((data[0] * 16 * 16) + (data[1] & 255)) / 1000.0,  # Magic borrowed from Petkit
            "battery": data[2]
        }

    # Init data
    @staticmethod
    def device_init(data, alias):
        return {"serial": Utils.bytes_to_long(data[7:11])}

    # Synchronize data
    @staticmethod
    def device_synchronization(data, alias):
        return {"device_initialized": data[0]}

    # Device Information
    @staticmethod
    def device_firmware(data, alias):
        # Extract the firmware version - supposedly
        # According to the com.petkit.oversea app, the Hardware is actually [0] while firmware is [1]
        # they are however presented as v[0].[1] in the actual app
        firmware = float(f"{data[0]}.{data[1]}")
        
        return {"firmware": firmware }
        
    # Get device state
    @staticmethod
    def device_state(data, alias):
        if _is_ctw3(alias):
            return Parsers._ctw3_state(data)
        data = _pad(data, 12)
        return {
            "power_status": data[0],
            "mode": data[1],
            "dnd_state": data[2],
            "warning_breakdown": data[3],
            "warning_water_missing": data[4],
            "warning_filter": data[5],
            "pump_runtime": Utils.bytes_to_integer(data[6:10]),
            "filter_percentage": Utils.byte_to_integer(data[10]) / 100,
            "running_status": Utils.byte_to_integer(data[11]),
        }

    # Get device configuration
    @staticmethod
    def device_configuration(data, alias):
        if _is_ctw3(alias):
            return Parsers._ctw3_config(data)
        data = _pad(data, 14)
        led_light_time_on = Utils.bytes_to_short(data[4:6])
        led_light_time_off = Utils.bytes_to_short(data[6:8])
        do_not_disturb_time_on = Utils.bytes_to_short(data[9:11])
        do_not_disturb_time_off = Utils.bytes_to_short(data[11:13])
    
        return {
            "smart_time_on": data[0],
            "smart_time_off": data[1],
            "led_switch": data[2],
            "led_brightness": data[3],
            "led_light_time_on": led_light_time_on,
            "led_light_time_on_readable": Utils.minutes_to_timestamp(led_light_time_on),
            "led_on_byte1": data[4],
            "led_on_byte2": data[5],
            "led_light_time_off": led_light_time_off,
            "led_light_time_off_readable": Utils.minutes_to_timestamp(led_light_time_off),
            "led_off_byte1": data[6],
            "led_off_byte2": data[7],
            "do_not_disturb_switch": data[8],
            "do_not_disturb_time_on": do_not_disturb_time_on,
            "do_not_disturb_time_on_readable": Utils.minutes_to_timestamp(do_not_disturb_time_on),
            "dnd_on_byte1": data[9],
            "dnd_on_byte2": data[10],
            "do_not_disturb_time_off": do_not_disturb_time_off,
            "do_not_disturb_time_off_readable": Utils.minutes_to_timestamp(do_not_disturb_time_off),
            "dnd_off_byte1": data[11],
            "dnd_off_byte2": data[12],
            "is_locked": data[13],
        }
        

    # ------------------------------------------------------------------
    # CTW3 layout. Verified against the raw frames of a real CTW3 (battery
    # 4.2 V, filter 100 %) and against the byte map used by the open source
    # aavdberg/ha-petkit integration.
    #
    # cmd 210 (30 bytes):
    #   0 power  1 suspend  2 mode (1 normal, 2 smart)  3 electric status
    #   4 dnd  5 breakdown  6 water missing  7 low battery  8 filter warning
    #   9-12 pump runtime (s)  13 filter %  14 running  15-18 runtime today (s)
    #   19 pet detected  20-21 supply mV  22-23 battery mV  24 battery %
    # cmd 211 (12 bytes):
    #   0 smart on  1 smart off  2-3 battery work  4-5 battery sleep
    #   6 led switch  7 led brightness  8 dnd  9 child lock  10-11 inductive
    # cmd 230 = the 30 byte state followed by the 12 byte config.
    # ------------------------------------------------------------------
    @staticmethod
    def _ctw3_state(data):
        data = _pad(data, 30)
        pump_runtime = Utils.bytes_to_integer(data[9:13])
        pump_runtime_today = Utils.bytes_to_integer(data[15:19])
        filter_percentage = Utils.byte_to_integer(data[13]) / 100
        battery_mv = int.from_bytes(bytes(data[22:24]), "big", signed=True)
        result = {
            "power_status": data[0],
            "dnd_state": data[4],
            "warning_breakdown": data[5],
            "warning_water_missing": data[6],
            "warning_filter": data[8],
            "pump_runtime": pump_runtime,
            "pump_runtime_today": pump_runtime_today,
            "filter_percentage": filter_percentage,
            "running_status": Utils.byte_to_integer(data[14]),
            "battery": data[24],
            "voltage": battery_mv / 1000.0,
            "pump_runtime_readable": Utils.get_timestamp_days(pump_runtime),
            "pump_runtime_today_readable": Utils.get_timestamp_hours(pump_runtime_today),
            "purified_water": Utils.calculate_water_purified("CTW3", pump_runtime),
            "purified_water_today": Utils.calculate_water_purified("CTW3", pump_runtime_today),
            "energy_consumed": format(Utils.calculate_energy_usage("CTW3", pump_runtime), "f"),
        }
        # Mode 0 is reported transiently (e.g. during the smart-mode sleep phase):
        # only accept the two real modes so the last known one is kept.
        if data[2] in (1, 2):
            result["mode"] = data[2]
        return result

    @staticmethod
    def _ctw3_config(data):
        data = _pad(data, 12)
        return {
            "smart_time_on": data[0],
            "smart_time_off": data[1],
            "led_switch": data[6],
            "led_brightness": data[7],
            "do_not_disturb_switch": data[8],
            "is_locked": data[9],
        }

    # Get device ID and serial
    @staticmethod
    def device_identifiers(data, alias):
        device_id_bytes, device_id = Utils.extract_device_id(data)
        serial = Utils.extract_serial_number(data)
        
        return {
            "device_id": device_id,
            "device_id_bytes": device_id_bytes,
            "serial": serial,
        }

    # Status
    @staticmethod
    def device_status(data, alias):
        if _is_ctw3(alias):
            result = Parsers._ctw3_state(data)
            if len(data) >= 40:
                result.update(Parsers._ctw3_config(data[30:]))
                result["filter_time_left"] = Utils.calculate_filter_days_ctw3(
                    result["filter_percentage"],
                    result.get("mode", 1),
                    result["smart_time_on"],
                    result["smart_time_off"],
                )
            return result
        data = _pad(data, 30)
        mode = data[1]
        filter_percentage = Utils.byte_to_integer(data[10]) / 100
        smart_time_on = data[16]
        smart_time_off = data[17]
        alias = alias
        pump_runtime_today = Utils.bytes_to_integer(data[12:16])
        pump_runtime = Utils.bytes_to_integer(data[6:10])
        
        filter_time_left, purified_water, purified_water_today, energy_consumed = Utils.calculate_values(mode, filter_percentage, smart_time_on, smart_time_off, alias, pump_runtime_today, pump_runtime)
        
        led_light_time_on = Utils.bytes_to_short(data[20:22])
        led_light_time_off = Utils.bytes_to_short(data[22:24])
        do_not_disturb_time_on = Utils.bytes_to_short(data[25:27])
        do_not_disturb_time_off = Utils.bytes_to_short(data[27:29])
        
        return {
            "power_status": data[0],
            "mode": mode,
            "dnd_state": data[2],
            "warning_breakdown": data[3],
            "warning_water_missing": data[4],
            "warning_filter": data[5],
            "pump_runtime": pump_runtime,
            "filter_percentage": filter_percentage,
            "running_status": Utils.byte_to_integer(data[11]),
            "pump_runtime_today": pump_runtime_today,
            "smart_time_on": smart_time_on,
            "smart_time_off": smart_time_off,
            "led_switch": data[18],
            "led_brightness": data[19],
            "led_light_time_on": led_light_time_on,
            "led_light_time_on_readable": Utils.minutes_to_timestamp(led_light_time_on),
            "led_on_byte1": data[20],
            "led_on_byte2": data[21],
            "led_light_time_off": led_light_time_off,
            "led_light_time_off_readable": Utils.minutes_to_timestamp(led_light_time_off),
            "led_off_byte1": data[22],
            "led_off_byte2": data[23],
            "do_not_disturb_switch": data[24],
            "do_not_disturb_time_on": do_not_disturb_time_on,
            "do_not_disturb_time_on_readable": Utils.minutes_to_timestamp(do_not_disturb_time_on),
            "dnd_on_byte1": data[25],
            "dnd_on_byte2": data[26],
            "do_not_disturb_time_off": do_not_disturb_time_off,
            "do_not_disturb_time_off_readable": Utils.minutes_to_timestamp(do_not_disturb_time_off),
            "dnd_off_byte1": data[27],
            "dnd_off_byte2": data[28],
            "pump_runtime_readable": Utils.get_timestamp_days(pump_runtime),
            "pump_runtime_today_readable": Utils.get_timestamp_hours(pump_runtime_today),
            "filter_time_left": filter_time_left,
            "purified_water": purified_water,
            "purified_water_today": purified_water_today,
            "energy_consumed": energy_consumed,
        }