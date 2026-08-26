# flasher.py - STM32F407 Bootloader Flash Workflow
#   Supports .bin (raw firmware) and .xbin (with magic header)
#   ERASE -> PROGRAM (header + firmware, chunked) -> VERIFY -> BOOT / RESET

import os
import sys
import struct
import time
from typing import Optional, Tuple

import serial

from protocol import (
    OPCODE_INQUERY, OPCODE_ERASE, OPCODE_PROGRAM, OPCODE_VERIFY,
    OPCODE_BOOT, OPCODE_RESET,
    INQUERY_SUBCODE_VERSION, INQUERY_SUBCODE_MTU,
    ERR_OK, ERROR_NAMES,
    CHUNK_SIZE,
    send_and_recv, send_packet, recv_response, _default_debug, crc32,
)

# STM32F407 Flash layout
BL_ADDRESS         = 0x08000000
BL_SIZE            = 48 * 1024      # 48KB bootloader
MAGIC_HEADER_ADDRESS = 0x0800C000   # Magic header address in Flash
MAGIC_HEADER_SIZE    = 4096         # Magic header size (padded)
APP_BASE_ADDRESS     = 0x08010000   # APP region base address
APP_MAX_SIZE         = 512 * 1024   # STM32F407VE total Flash 512KB

MAGIC_HEADER_MAGIC = 0x4D414749     # "MAGI"


def _check_errcode(errcode: int, op_name: str) -> None:
    """Check error code, raise RuntimeError if not OK"""
    if errcode != ERR_OK:
        name = ERROR_NAMES.get(errcode, f"0x{errcode:02X}")
        raise RuntimeError(f"{op_name} failed: {name}")


# ============================================================
# Single Command Functions
# ============================================================

def inquery_version(ser: serial.Serial) -> str:
    """Query bootloader version"""
    payload = struct.pack('<B', INQUERY_SUBCODE_VERSION)
    errcode, data = send_and_recv(ser, OPCODE_INQUERY, payload)
    _check_errcode(errcode, "INQUERY VERSION")
    version = data.decode('ascii', errors='replace')
    return version


def inquery_mtu(ser: serial.Serial) -> int:
    """Query MTU"""
    payload = struct.pack('<B', INQUERY_SUBCODE_MTU)
    errcode, data = send_and_recv(ser, OPCODE_INQUERY, payload)
    _check_errcode(errcode, "INQUERY MTU")
    mtu = struct.unpack('<H', data)[0]
    return mtu


def erase(ser: serial.Serial, address: int, size: int) -> None:
    """Erase Flash region"""
    payload = struct.pack('<II', address, size)
    errcode, _ = send_and_recv(ser, OPCODE_ERASE, payload)
    _check_errcode(errcode, f"ERASE 0x{address:08X} size={size}")


def program(ser: serial.Serial, address: int, data: bytes) -> None:
    """
    Write one chunk to Flash.
    Max CHUNK_SIZE bytes (4096) per call. Caller handles chunking.
    """
    payload = struct.pack('<II', address, len(data)) + data
    errcode, _ = send_and_recv(ser, OPCODE_PROGRAM, payload)
    _check_errcode(errcode, f"PROGRAM 0x{address:08X}")


def program_stream(ser: serial.Serial, chunks, progress_cb=None) -> None:
    """
    Pipelined PROGRAM: keep one frame in flight.

    Send the next chunk right after the previous one (without waiting for its
    ACK), so UART TX overlaps the MCU's Flash programming. Requires the MCU
    to receive via DMA: bytes arriving while Flash is busy are captured by
    DMA hardware (CPU is stalled, interrupts cannot run).

    chunks: iterable of (address, data) tuples.
    progress_cb: optional callback(done_bytes), called after each ACK.
    Raises ConnectionError on lost ACK, RuntimeError on non-OK errcode.
    """
    chunks = list(chunks)
    if not chunks:
        return

    def _payload(addr, data):
        return struct.pack('<II', addr, len(data)) + data

    saved_timeout = ser.timeout
    ser.timeout = 10.0
    done = 0
    try:
        send_packet(ser, OPCODE_PROGRAM, _payload(chunks[0][0], chunks[0][1]))
        prev = chunks[0]
        for chunk in chunks[1:]:
            send_packet(ser, OPCODE_PROGRAM, _payload(chunk[0], chunk[1]))
            resp = recv_response(ser, debug_cb=_default_debug)
            if resp is None:
                raise ConnectionError("PROGRAM ACK lost at 0x%08X" % prev[0])
            errcode, _ = resp
            _check_errcode(errcode, "PROGRAM 0x%08X" % prev[0])
            done += len(prev[1])
            if progress_cb:
                progress_cb(done)
            prev = chunk
        resp = recv_response(ser, debug_cb=_default_debug)
        if resp is None:
            raise ConnectionError("PROGRAM ACK lost at 0x%08X" % prev[0])
        errcode, _ = resp
        _check_errcode(errcode, "PROGRAM 0x%08X" % prev[0])
        done += len(prev[1])
        if progress_cb:
            progress_cb(done)
    finally:
        ser.timeout = saved_timeout


def verify(ser: serial.Serial, address: int, size: int, crc: int) -> None:
    """Verify Flash region CRC32"""
    payload = struct.pack('<III', address, size, crc)
    errcode, _ = send_and_recv(ser, OPCODE_VERIFY, payload)
    _check_errcode(errcode, f"VERIFY 0x{address:08X} size={size}")


def boot(ser: serial.Serial) -> None:
    """Jump to APP"""
    errcode, _ = send_and_recv(ser, OPCODE_BOOT, b'')
    _check_errcode(errcode, "BOOT")


def reset(ser: serial.Serial) -> None:
    """System reset"""
    errcode, _ = send_and_recv(ser, OPCODE_RESET, b'')
    _check_errcode(errcode, "RESET")


# ============================================================
# Magic Header Parsing / Generation
# ============================================================

# Magic header struct layout (256 bytes before padding):
#   offset  0: magic        (4B)  = 0x4D414749
#   offset  4: bitmask      (4B)
#   offset  8: reserved1    (24B)
#   offset 32: data_type    (4B)
#   offset 36: data_offset  (4B)  = 4096
#   offset 40: data_address (4B)  = 0x08010000
#   offset 44: data_length  (4B)  firmware size
#   offset 48: data_crc32   (4B)  firmware CRC32
#   offset 52: reserved2    (44B)
#   offset 96: version      (128B) version string
#   offset224: reserved3    (24B)
#   offset248: this_address (4B)  = 0x0800C000
#   offset252: this_crc32   (4B)  header self CRC32


def parse_xbin(data: bytes) -> Tuple[bytes, bytes, int, int, int]:
    """
    Parse .xbin file.
    Returns: (header_bytes, firmware_bytes, data_address, data_length, data_crc32)
    Raises ValueError if magic mismatch or data_offset out of range.
    """
    if len(data) < 256:
        raise ValueError(f"File too small for magic header: {len(data)} bytes")

    magic = struct.unpack_from('<I', data, 0)[0]
    if magic != MAGIC_HEADER_MAGIC:
        raise ValueError(
            f"Bad magic: 0x{magic:08X}, expected 0x{MAGIC_HEADER_MAGIC:08X}"
        )

    data_offset  = struct.unpack_from('<I', data, 36)[0]
    data_address = struct.unpack_from('<I', data, 40)[0]
    data_length  = struct.unpack_from('<I', data, 44)[0]
    data_crc32   = struct.unpack_from('<I', data, 48)[0]

    if data_offset > len(data):
        raise ValueError(
            f"data_offset ({data_offset}) exceeds file size ({len(data)})"
        )

    header_bytes = data[:data_offset]
    firmware_bytes = data[data_offset:data_offset + data_length]

    if len(firmware_bytes) != data_length:
        raise ValueError(
            f"Firmware truncated: expected {data_length} B, got {len(firmware_bytes)} B"
        )

    return header_bytes, firmware_bytes, data_address, data_length, data_crc32


def generate_magic_header(firmware: bytes) -> bytes:
    """
    Auto-generate a magic header for raw .bin firmware.
    Uses default addresses: header at 0x0800C000, firmware at 0x08010000.
    """
    header = bytearray(MAGIC_HEADER_SIZE)

    # magic
    struct.pack_into('<I', header, 0, MAGIC_HEADER_MAGIC)
    # data_type = 1 (firmware)
    struct.pack_into('<I', header, 32, 1)
    # data_offset
    struct.pack_into('<I', header, 36, MAGIC_HEADER_SIZE)
    # data_address
    struct.pack_into('<I', header, 40, APP_BASE_ADDRESS)
    # data_length
    struct.pack_into('<I', header, 44, len(firmware))
    # data_crc32
    struct.pack_into('<I', header, 48, crc32(firmware))
    # version (128 bytes at offset 96)
    ver = time.strftime("v1.0.0-%y%m%d-%H%M-auto", time.localtime())
    ver_bytes = ver.encode('ascii').ljust(128, b'\x00')
    header[96:224] = ver_bytes[:128]
    # this_address
    struct.pack_into('<I', header, 248, MAGIC_HEADER_ADDRESS)
    # this_crc32 (CRC of bytes 0..251)
    hdr_crc = crc32(bytes(header[:252]))
    struct.pack_into('<I', header, 252, hdr_crc)

    return bytes(header)

def _progress_bar(current: int, total: int, prefix: str = "", width: int = 40) -> None:
    """Print progress bar"""
    pct = current / total if total > 0 else 1.0
    filled = int(width * pct)
    bar = "#" * filled + "-" * (width - filled)
    sys.stdout.write(f"\r{prefix}[{bar}] {pct*100:.1f}% ({current}/{total} B)")
    sys.stdout.flush()


# ============================================================
# Progress Bar
# ============================================================

# ============================================================
# Main Flash Workflow
# ============================================================


def _detect_file_type(filepath: str) -> str:
    """Detect file type by extension: 'xbin' or 'bin'"""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.xbin':
        return 'xbin'
    return 'bin'


def _align4(data: bytes) -> bytes:
    """Pad to 4-byte alignment with 0xFF"""
    if len(data) % 4 != 0:
        pad = 4 - (len(data) % 4)
        return data + b'\xff' * pad
    return data


def flash_firmware(
    ser: serial.Serial,
    bin_path: str,
    base_addr: int = APP_BASE_ADDRESS,
    skip_erase: bool = False,
    skip_verify: bool = False,
) -> None:
    """
    Complete firmware flash workflow:
      1. Read file (.bin or .xbin), parse/auto-generate magic header
      2. ERASE magic header area + APP area
      3. PROGRAM: magic header to 0x0800C000, firmware to APP address
      4. VERIFY: firmware CRC32
      5. BOOT

    Args:
      ser:        Open serial port object
      bin_path:   Path to .bin or .xbin firmware file
      base_addr:  Override APP base address (only for .bin, ignored for .xbin)
      skip_erase: Skip erase step (debug only)
      skip_verify: Skip verify step (debug only)
    """

    _t0 = time.time()

    # ---- 1. Read and parse file ----
    if not os.path.exists(bin_path):
        raise FileNotFoundError(f"File not found: {bin_path}")

    with open(bin_path, 'rb') as f:
        raw_data = f.read()

    file_type = _detect_file_type(bin_path)

    if file_type == 'xbin':
        header_bytes, firmware, data_address, data_length, data_crc32 = parse_xbin(raw_data)
        print(f"File: {bin_path} (.xbin with magic header)")
        print(f"  Header: {len(header_bytes)} B")
        print(f"  Firmware: {data_length} B @ 0x{data_address:08X}")
        print(f"  Firmware CRC32: 0x{data_crc32:08X}")
    else:
        # Raw .bin: auto-generate magic header
        firmware = raw_data
        data_address = base_addr
        data_length = len(firmware)
        print(f"File: {bin_path} (.bin, auto-generating magic header)")
        print(f"  Firmware: {len(firmware)} B @ 0x{data_address:08X}")
        header_bytes = generate_magic_header(firmware)
        print(f"  Header: {len(header_bytes)} B (auto-generated)")

    if len(firmware) == 0:
        raise ValueError("Firmware is empty")

    # 4-byte alignment
    firmware = _align4(firmware)
    data_length = len(firmware)
    print(f"  Firmware size: {data_length} B ({data_length / 1024:.1f} KB)")

    # Recalculate CRC32 on padded firmware
    fw_crc32 = crc32(firmware)

    # ---- 2. Query Bootloader ----
    try:
        version = inquery_version(ser)
        print(f"Bootloader version: {version}")
        mtu = inquery_mtu(ser)
        print(f"MTU: {mtu} bytes")
        actual_chunk = min(CHUNK_SIZE, mtu - 8)
    except Exception:
        print("  (INQUERY not supported, using defaults)")
        actual_chunk = CHUNK_SIZE

    # ---- 3. ERASE ----
    if skip_erase:
        print("ERASE: SKIPPED (--skip-erase)")
    else:
        # Erase magic header area
        print(f"Erasing magic header 0x{MAGIC_HEADER_ADDRESS:08X} +{MAGIC_HEADER_SIZE}...")
        erase(ser, MAGIC_HEADER_ADDRESS, MAGIC_HEADER_SIZE)
        print("  Header erase: ACK")

        # Erase APP area
        print(f"Erasing APP region 0x{data_address:08X} +{data_length}...")
        erase(ser, data_address, data_length)
        print("  APP erase: ACK (synchronous erase complete)")

    # ---- 4. PROGRAM Magic Header ----
    print(f"Programming magic header to 0x{MAGIC_HEADER_ADDRESS:08X} ({len(header_bytes)} B)...")
    hdr_offset = 0
    while hdr_offset < len(header_bytes):
        chunk = header_bytes[hdr_offset:hdr_offset + actual_chunk]
        program(ser, MAGIC_HEADER_ADDRESS + hdr_offset, chunk)
        hdr_offset += len(chunk)
    print("  Header: OK")

    # ---- 5. PROGRAM Firmware (chunked, pipelined) ----
    print(f"Programming firmware ({actual_chunk} B/chunk, pipelined)...")
    total = data_length
    chunks = [
        (data_address + off, firmware[off:off + actual_chunk])
        for off in range(0, total, actual_chunk)
    ]
    try:
        program_stream(
            ser, chunks,
            progress_cb=lambda done: _progress_bar(done, total, prefix="  "),
        )
    except Exception as e:
        print(f"\n  PROGRAM failed: {e}")
        raise
    print()  # newline

    # ---- 6. VERIFY ----
    if skip_verify:
        print("VERIFY: SKIPPED (--skip-verify)")
    else:
        print(f"Verifying firmware 0x{data_address:08X} size={total}...")
        verify(ser, data_address, total, fw_crc32)
        print(f"VERIFY: OK (CRC32 = 0x{fw_crc32:08X})")

    # ---- 7. BOOT ----
    print("Booting application...")
    boot(ser)
    print("BOOT: OK")
    print("\n=== Firmware upgrade completed successfully! (elapsed %.2f s) ===" % (time.time() - _t0))
