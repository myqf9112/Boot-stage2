# flasher.py - STM32F407 Bootloader Flash Workflow
#   Supports .bin (raw firmware) and .xbin (with magic header)
#   ERASE -> PROGRAM (header + firmware, chunked) -> VERIFY -> BOOT / RESET

import os
import sys
import struct
import time
import json
import hashlib
from typing import Optional, Tuple

import serial

from protocol import (
    OPCODE_INQUERY, OPCODE_ERASE, OPCODE_PROGRAM, OPCODE_VERIFY,
    OPCODE_BOOT, OPCODE_RESET, OPCODE_SWITCH_SLOT,
    INQUERY_SUBCODE_VERSION, INQUERY_SUBCODE_MTU, INQUERY_SUBCODE_SLOT_STATUS,
    ERR_OK, ERR_PARAM, ERROR_NAMES,
    CHUNK_SIZE,
    send_and_recv, send_packet, recv_response, _default_debug, crc32,
)

# STM32F407 Flash layout (1MB, A/B dual-slot)
BL_ADDRESS         = 0x08000000
BL_SIZE            = 32 * 1024      # bootloader (sectors 0-1)
STATE_ADDRESS      = 0x08008000     # boot state (sector 2)

# 槽位配置
SLOTS = {
    'A': {
        'header_addr': 0x0800C000,   # A_Hdr (sector 3, 16KB)
        'app_addr':    0x08010000,   # A_App (sectors 4-7, 448KB)
        'app_size':    448 * 1024,
    },
    'B': {
        'header_addr': 0x08080000,   # B_Hdr (sector 8 起始, 4KB)
        'app_addr':    0x08081000,   # B_App (sector 8 尾 + 9-11, 508KB)
        'app_size':    508 * 1024,
    },
}

MAGIC_HEADER_ADDRESS = SLOTS['A']['header_addr']  # 兼容旧接口
APP_BASE_ADDRESS     = SLOTS['A']['app_addr']
APP_MAX_SIZE         = 512 * 1024                 # 兼容旧接口（历史值）
MAGIC_HEADER_SIZE    = 256                        # magic_header_t 结构体大小
MAGIC_HEADER_MAGIC   = 0x4D414749                 # "MAGI"


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


def inquery_slot_status(ser: serial.Serial) -> dict:
    """Query bootloader slot status: active/pending/boot_attempts/valid_mask"""
    payload = struct.pack('<B', INQUERY_SUBCODE_SLOT_STATUS)
    errcode, data = send_and_recv(ser, OPCODE_INQUERY, payload)
    _check_errcode(errcode, "INQUERY SLOT STATUS")
    if len(data) < 4:
        raise RuntimeError(f"Slot status response too short: {len(data)} bytes")
    return {
        'active': data[0],        # 0=A, 1=B
        'pending': data[1],       # 0xFF=无
        'boot_attempts': data[2], # 连续看门狗复位次数
        'valid_mask': data[3],    # bit0=A有效, bit1=B有效
    }


def switch_slot(ser: serial.Serial, slot: str) -> int:
    """
    请求切换启动槽（写 pending_slot，下次复位生效）。
    返回目标槽编号 (0=A, 1=B)。
    """
    s = slot.upper()
    if s not in SLOTS:
        raise ValueError(f"Invalid slot: {slot} (must be A or B)")
    target = 0 if s == 'A' else 1
    payload = struct.pack('<B', target)
    errcode, data = send_and_recv(ser, OPCODE_SWITCH_SLOT, payload)
    if errcode == ERR_OK:
        return target
    if errcode == ERR_PARAM:
        if data and data[0] == 2:
            raise RuntimeError("SWITCH_SLOT: target slot firmware invalid (not programmed?)")
        raise RuntimeError("SWITCH_SLOT: invalid slot parameter")
    _check_errcode(errcode, "SWITCH_SLOT")
    return target


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


def generate_magic_header(firmware: bytes, slot: str = 'A', version: str = None) -> bytes:
    """
    Auto-generate a magic header for raw .bin firmware.
    与 bootloader 的 magic_header_t (256B) 严格对应。
    slot: 'A' 或 'B'，决定 header 地址与 APP 加载地址。
    """
    s = slot.upper()
    if s not in SLOTS:
        raise ValueError(f"Invalid slot: {slot} (must be A or B)")
    header_addr = SLOTS[s]['header_addr']
    app_addr    = SLOTS[s]['app_addr']

    header = bytearray(MAGIC_HEADER_SIZE)  # 256

    # magic
    struct.pack_into('<I', header, 0, MAGIC_HEADER_MAGIC)
    # data_type = 0 (MAGIC_HEADER_TYPE_APP)
    struct.pack_into('<I', header, 32, 0)
    # data_offset（bootloader 不校验此字段，置 0）
    struct.pack_into('<I', header, 36, 0)
    # data_address
    struct.pack_into('<I', header, 40, app_addr)
    # data_length
    struct.pack_into('<I', header, 44, len(firmware))
    # data_crc32
    struct.pack_into('<I', header, 48, crc32(firmware))
    # version (128 bytes at offset 96)
    if version is None:
        version = time.strftime("v1.0.0-%y%m%d-%H%M-auto", time.localtime())
    ver_bytes = version.encode('ascii', errors='replace').ljust(128, b'\x00')
    header[96:224] = ver_bytes[:128]
    # this_address
    struct.pack_into('<I', header, 248, header_addr)
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


def _checkpoint_path(bin_path: str) -> str:
    """Checkpoint file path: firmware file + '.resume.json'"""
    return bin_path + ".resume.json"


def _save_checkpoint(bin_path, stage, offset, data_address, data_length, firmware):
    """Persist flash progress. Must be called AFTER the corresponding ACK."""
    cp = {
        "firmware_sha256": hashlib.sha256(firmware).hexdigest(),
        "stage": stage,               # "erased" | "header_done" | "firmware"
        "offset": offset,             # firmware bytes ACKed (firmware stage)
        "data_address": data_address,
        "data_length": data_length,
    }
    try:
        with open(_checkpoint_path(bin_path), "w", encoding="utf-8") as f:
            json.dump(cp, f)
    except OSError as e:
        print(f"  (checkpoint save failed: {e})")


def _load_checkpoint(bin_path, firmware, data_address, data_length):
    """Return (stage, offset) or None if no valid checkpoint for this firmware."""
    try:
        with open(_checkpoint_path(bin_path), "r", encoding="utf-8") as f:
            cp = json.load(f)
    except (OSError, ValueError):
        return None
    if cp.get("firmware_sha256") != hashlib.sha256(firmware).hexdigest():
        return None
    if cp.get("data_address") != data_address or cp.get("data_length") != data_length:
        return None
    stage = cp.get("stage")
    if stage not in ("erased", "header_done", "firmware"):
        return None
    try:
        offset = int(cp.get("offset", 0))
    except (TypeError, ValueError):
        return None
    if stage == "firmware" and not (0 <= offset <= data_length):
        return None
    return stage, offset


def _clear_checkpoint(bin_path):
    """Remove checkpoint after a fully successful flash."""
    try:
        os.remove(_checkpoint_path(bin_path))
    except OSError:
        pass


def flash_firmware(
    ser: serial.Serial,
    bin_path: str,
    slot: str = 'A',
    base_addr: int = None,
    skip_erase: bool = False,
    skip_verify: bool = False,
    resume: bool = False,
    switch_after: bool = True,
    dedup: bool = True,
) -> None:
    """
    Complete firmware flash workflow:
      1. Read file (.bin or .xbin), parse/auto-generate magic header
      2. ERASE header + APP region (combined, sector-safe)
      3. PROGRAM: magic header to header_addr, firmware to APP address
      4. VERIFY: firmware CRC32
      5. SWITCH_SLOT + RESET (default) or BOOT

    Args:
      ser:          Open serial port object
      bin_path:     Path to .bin or .xbin firmware file
      slot:         'A' or 'B', determines header/app addresses (for .bin)
      base_addr:    Override APP base address (only for .bin, ignored for .xbin)
      skip_erase:   Skip erase step (debug only)
      skip_verify:  Skip verify step (debug only)
      resume:       Resume from <bin>.resume.json checkpoint if valid
      switch_after: After flash, switch to this slot and reset (default True).
                    Set False to just send BOOT (legacy behavior).
      dedup:        If True (default), skip erase/program when target slot
                    already holds the same firmware (CRC match).
    """

    _t0 = time.time()

    s = slot.upper()
    if s not in SLOTS:
        raise ValueError(f"Invalid slot: {slot} (must be A or B)")
    slot_cfg = SLOTS[s]

    # ---- 1. Read and parse file ----
    if not os.path.exists(bin_path):
        raise FileNotFoundError(f"File not found: {bin_path}")

    with open(bin_path, 'rb') as f:
        raw_data = f.read()

    file_type = _detect_file_type(bin_path)

    if file_type == 'xbin':
        header_bytes, firmware, data_address, data_length, data_crc32 = parse_xbin(raw_data)
        header_addr = struct.unpack_from('<I', header_bytes, 248)[0]
        print(f"File: {bin_path} (.xbin with magic header)")
        print(f"  Header: {len(header_bytes)} B @ 0x{header_addr:08X}")
        print(f"  Firmware: {data_length} B @ 0x{data_address:08X}")
        print(f"  Firmware CRC32: 0x{data_crc32:08X}")
    else:
        # Raw .bin: auto-generate magic header for the target slot
        firmware = raw_data
        header_addr = slot_cfg['header_addr']
        data_address = base_addr if base_addr is not None else slot_cfg['app_addr']
        data_length = len(firmware)
        print(f"File: {bin_path} (.bin, slot {s}, auto-generating magic header)")
        print(f"  Header: 0x{header_addr:08X}, Firmware: {len(firmware)} B @ 0x{data_address:08X}")
        header_bytes = generate_magic_header(firmware, slot=s)
        print(f"  Header: {len(header_bytes)} B (auto-generated)")

    if len(firmware) == 0:
        raise ValueError("Firmware is empty")

    # 4-byte alignment
    firmware = _align4(firmware)
    data_length = len(firmware)
    print(f"  Firmware size: {data_length} B ({data_length / 1024:.1f} KB)")

    # Recalculate CRC32 on padded firmware
    fw_crc32 = crc32(firmware)

    # 边界检查：不能超出槽位大小
    if data_address >= slot_cfg['app_addr'] and data_length > slot_cfg['app_size']:
        raise ValueError(
            f"Firmware too large for slot {s}: {data_length} B > {slot_cfg['app_size']} B"
        )

    # ---- Resume checkpoint ----
    resume_stage = None
    resume_offset = 0
    if resume:
        _cp = _load_checkpoint(bin_path, firmware, data_address, data_length)
        if _cp:
            resume_stage, resume_offset = _cp
            print(f"  Resume checkpoint: stage={resume_stage}, offset={resume_offset}")
        else:
            print("  No valid checkpoint, starting from scratch")

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

    # ---- 3. Dedup probe: skip erase/program if target already holds this firmware ----
    dedup_hit = False
    if dedup and not skip_erase and resume_stage is None:
        try:
            probe = struct.pack('<III', data_address, data_length, fw_crc32)
            errcode, _ = send_and_recv(ser, OPCODE_VERIFY, probe, debug_cb=lambda _m: None)
            dedup_hit = (errcode == ERR_OK)
            if dedup_hit:
                print(f"  Firmware unchanged (CRC 0x{fw_crc32:08X} match), skip erase/program")
        except Exception:
            dedup_hit = False

    # ---- 4. ERASE (combined: header + APP, sector-safe) ----
    erase_start = min(header_addr, data_address)
    erase_end = max(header_addr + len(header_bytes), data_address + data_length)
    erase_size = erase_end - erase_start

    if skip_erase:
        print("ERASE: SKIPPED (--skip-erase)")
    elif resume_stage is not None:
        print("ERASE: SKIPPED (resuming, flash already erased)")
    elif dedup_hit:
        print("ERASE: SKIPPED (firmware unchanged)")
    else:
        print(f"Erasing 0x{erase_start:08X} +{erase_size} (header + APP)...")
        erase(ser, erase_start, erase_size)
        print("  Erase: ACK (synchronous erase complete)")
        # 擦除完成才落盘:之后续传绝不再擦除
        _save_checkpoint(bin_path, "erased", 0, data_address, data_length, firmware)

    # ---- 5. PROGRAM Magic Header ----
    if dedup_hit:
        print("Header: SKIPPED (firmware unchanged)")
    elif resume_stage in ("header_done", "firmware"):
        print("Header: SKIPPED (resuming, already programmed)")
    else:
        print(f"Programming magic header to 0x{header_addr:08X} ({len(header_bytes)} B)...")
        hdr_offset = 0
        while hdr_offset < len(header_bytes):
            chunk = header_bytes[hdr_offset:hdr_offset + actual_chunk]
            program(ser, header_addr + hdr_offset, chunk)
            hdr_offset += len(chunk)
        print("  Header: OK")
        _save_checkpoint(bin_path, "header_done", 0, data_address, data_length, firmware)

    # ---- 6. PROGRAM Firmware (chunked, pipelined) ----
    total = data_length
    if dedup_hit:
        print("Firmware: SKIPPED (unchanged)")
    else:
        start_offset = resume_offset if resume_stage == "firmware" else 0
        if start_offset:
            print(f"Resuming firmware from offset {start_offset} / {data_length}")
        print(f"Programming firmware ({actual_chunk} B/chunk, pipelined)...")
        chunks = [
            (data_address + off, firmware[off:off + actual_chunk])
            for off in range(start_offset, total, actual_chunk)
        ]

        saved_offset = start_offset

        def _on_ack(done):
            nonlocal saved_offset
            saved_offset = start_offset + done
            _progress_bar(saved_offset, total, prefix="  ")
            _save_checkpoint(bin_path, "firmware", saved_offset,
                             data_address, data_length, firmware)

        try:
            program_stream(ser, chunks, progress_cb=_on_ack)
        except Exception as e:
            print(f"\n  PROGRAM failed: {e}")
            print(f"  (progress saved at {saved_offset}, re-run with --resume to continue)")
            raise
        print()  # newline

    # ---- 7. VERIFY ----
    if skip_verify:
        print("VERIFY: SKIPPED (--skip-verify)")
    elif dedup_hit:
        print("VERIFY: SKIPPED (CRC already matched)")
    else:
        print(f"Verifying firmware 0x{data_address:08X} size={total}...")
        verify(ser, data_address, total, fw_crc32)
        print(f"VERIFY: OK (CRC32 = 0x{fw_crc32:08X})")

    # ---- 8. Switch & reboot / boot ----
    if switch_after:
        print(f"Switching to slot {s} (pending_slot={0 if s == 'A' else 1})...")
        switch_slot(ser, s)
        print("SWITCH_SLOT: OK")
        print("Rebooting...")
        reset(ser)
        print("RESET: OK (bootloader will boot slot %s)" % s)
    else:
        print("Booting application...")
        boot(ser)
        print("BOOT: OK")
    _clear_checkpoint(bin_path)
    print("\n=== Firmware upgrade completed successfully! (elapsed %.2f s) ===" % (time.time() - _t0))
