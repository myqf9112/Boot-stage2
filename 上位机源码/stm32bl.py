# stm32bl.py - STM32F407 Bootloader Host CLI Tool
#
# Usage:
#   python stm32bl.py COM3 flash firmware.bin --slot A    # Flash to Slot A (auto header)
#   python stm32bl.py COM3 flash firmware.bin --slot B    # Flash to Slot B
#   python stm32bl.py COM3 flash firmware.xbin            # Flash .xbin (with magic header)
#   python stm32bl.py COM3 flash firmware.bin --slot B --addr 0x08081000
#   python stm32bl.py COM3 inquery                    # Query version/MTU
#   python stm32bl.py COM3 status                     # Query A/B slot status
#   python stm32bl.py COM3 switch --slot B            # Switch boot slot (pending)
#   python stm32bl.py COM3 boot                       # Jump to APP only
#   python stm32bl.py COM3 reset                      # System reset
#   python stm32bl.py --list                          # List serial ports
#
# Install dependencies:
#   pip install -r requirements.txt

import argparse
import sys

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("ERROR: pyserial not installed. Run: pip install pyserial")
    sys.exit(1)

from protocol import (
    open_serial, DEFAULT_BAUDRATE,
    OPCODE_BOOT, OPCODE_RESET,
    send_and_recv, ERROR_NAMES,
)
from flasher import (
    flash_firmware, inquery_version, inquery_mtu, inquery_slot_status,
    boot, reset, switch_slot,
)


def list_ports() -> None:
    """List available serial ports"""
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("No serial ports found.")
        return
    print("Available ports:")
    for p in ports:
        print(f"  {p.device} - {p.description}")


def cmd_inquery(ser: serial.Serial) -> None:
    """Query bootloader info"""
    try:
        version = inquery_version(ser)
        print(f"Version: {version}")
    except Exception as e:
        print(f"  Version query failed: {e}")

    try:
        mtu = inquery_mtu(ser)
        print(f"MTU:     {mtu} bytes")
    except Exception as e:
        print(f"  MTU query failed: {e}")


def cmd_status(ser: serial.Serial) -> None:
    """Query A/B slot status"""
    st = inquery_slot_status(ser)
    slot_name = {0: 'A', 1: 'B', 0xFF: 'None'}
    print(f"Active slot : {slot_name.get(st['active'], st['active'])}")
    print(f"Pending slot: {slot_name.get(st['pending'], st['pending'])}")
    print(f"Boot attempts: {st['boot_attempts']}")
    a_ok = bool(st['valid_mask'] & 0x01)
    b_ok = bool(st['valid_mask'] & 0x02)
    print(f"Slot A valid: {'YES' if a_ok else 'NO'}")
    print(f"Slot B valid: {'YES' if b_ok else 'NO'}")


def cmd_switch(ser: serial.Serial, slot: str) -> None:
    """Switch boot slot (write pending_slot, takes effect on next reset)"""
    print(f"Sending SWITCH_SLOT to {slot.upper()}...")
    target = switch_slot(ser, slot)
    print(f"Done. pending_slot = {target} (0=A, 1=B). Reset the board to take effect.")


def cmd_boot(ser: serial.Serial) -> None:
    """Send BOOT command"""
    print("Sending BOOT command...")
    boot(ser)
    print("Done. Application started.")


def cmd_reset(ser: serial.Serial) -> None:
    """Send RESET command"""
    print("Sending RESET command...")
    reset(ser)
    print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="STM32F407 Bootloader Host Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python stm32bl.py COM3 flash firmware.bin --slot A
  python stm32bl.py COM3 flash firmware.bin --slot B
  python stm32bl.py COM3 status
  python stm32bl.py COM3 switch --slot B
  python stm32bl.py COM3 boot
  python stm32bl.py --list
        """.strip(),
    )

    parser.add_argument("port", nargs="?", help="Serial port (e.g. COM3, /dev/ttyUSB0)")
    parser.add_argument("action", nargs="?", default="flash",
                        choices=["flash", "inquery", "boot", "reset", "switch", "status"],
                        help="Action to perform (default: flash)")

    parser.add_argument("file", nargs="?", help="Firmware file (.xbin or .bin, for flash)")
    parser.add_argument("--slot", type=str, default='A', choices=['A', 'B'],
                        help="Target slot for flash (default: A). Also used by 'switch'.")
    parser.add_argument("--addr", type=lambda x: int(x, 0),
                        default=None,
                        help="Override APP base address for .bin files (default: slot base)")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUDRATE,
                        help=f"Baud rate (default: {DEFAULT_BAUDRATE})")
    parser.add_argument("--skip-erase", action="store_true",
                        help="Skip erase step")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Skip verify step")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from .resume.json checkpoint if available")
    parser.add_argument("--no-switch", action="store_true",
                        help="After flash, do NOT switch slot/reset (just send BOOT)")
    parser.add_argument("--list", action="store_true",
                        help="List available serial ports")

    args = parser.parse_args()

    # --list
    if args.list:
        list_ports()
        return

    # port is required for all actions except --list
    if not args.port:
        parser.error("the following arguments are required: port")

    # Flash requires a firmware file
    if args.action == "flash" and not args.file:
        parser.error("the following arguments are required for flash: file (.xbin or .bin)")

    # switch requires --slot
    if args.action == "switch" and args.slot not in ('A', 'B'):
        parser.error("switch requires --slot A|B")

    # Open serial port
    try:
        ser = open_serial(args.port, args.baud)
        print(f"Connected to {args.port} @ {args.baud}")
    except Exception as e:
        print(f"ERROR: Cannot open {args.port}: {e}")
        sys.exit(1)

    try:
        if args.action == "flash":
            flash_firmware(
                ser,
                bin_path=args.file,
                slot=args.slot,
                base_addr=args.addr,
                skip_erase=args.skip_erase,
                skip_verify=args.skip_verify,
                resume=args.resume,
                switch_after=not args.no_switch,
            )
        elif args.action == "inquery":
            cmd_inquery(ser)
        elif args.action == "boot":
            cmd_boot(ser)
        elif args.action == "reset":
            cmd_reset(ser)
        elif args.action == "switch":
            cmd_switch(ser, args.slot)
        elif args.action == "status":
            cmd_status(ser)
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
