# stm32bl.py - STM32F407 Bootloader Host CLI Tool
#
# Usage:
#   python stm32bl.py COM3 flash firmware.xbin       # Flash .xbin (with magic header)
#   python stm32bl.py COM3 flash firmware.bin         # Flash .bin (auto-generate header)
#   python stm32bl.py COM3 flash firmware.bin --addr 0x08020000
#   python stm32bl.py COM3 flash firmware.bin --skip-erase --skip-verify
#   python stm32bl.py COM3 inquery                    # Query version/MTU
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
    flash_firmware, inquery_version, inquery_mtu,
    boot, reset, APP_BASE_ADDRESS, MAGIC_HEADER_ADDRESS,
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
  python stm32bl.py COM3 flash firmware.xbin
  python stm32bl.py COM3 flash firmware.bin
  python stm32bl.py COM3 flash firmware.bin --addr 0x08020000
  python stm32bl.py COM3 boot
  python stm32bl.py --list
        """.strip(),
    )

    parser.add_argument("port", nargs="?", help="Serial port (e.g. COM3, /dev/ttyUSB0)")
    parser.add_argument("action", nargs="?", default="flash",
                        choices=["flash", "inquery", "boot", "reset"],
                        help="Action to perform (default: flash)")

    parser.add_argument("file", nargs="?", help="Firmware file (.xbin or .bin, for flash)")
    parser.add_argument("--addr", type=lambda x: int(x, 0),
                        default=APP_BASE_ADDRESS,
                        help=f"APP base address for .bin files (default: 0x{APP_BASE_ADDRESS:08X})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUDRATE,
                        help=f"Baud rate (default: {DEFAULT_BAUDRATE})")
    parser.add_argument("--skip-erase", action="store_true",
                        help="Skip erase step")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Skip verify step")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from .resume.json checkpoint if available")
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
                base_addr=args.addr,
                skip_erase=args.skip_erase,
                skip_verify=args.skip_verify,
                resume=args.resume,
            )
        elif args.action == "inquery":
            cmd_inquery(ser)
        elif args.action == "boot":
            cmd_boot(ser)
        elif args.action == "reset":
            cmd_reset(ser)
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
