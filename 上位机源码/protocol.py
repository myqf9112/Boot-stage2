# protocol.py - STM32F407 Bootloader Protocol Layer

#   CRC16 XMODEM (poly=0x1021, init=0x0000, no reflect)

#   CRC32 IEEE 802.3 (poly=0xEDB88320, same as zlib.crc32)

#   Pack / Unpack / Serial TX/RX



import struct

import serial

import zlib

from typing import Tuple, Optional



# ============================================================

# Protocol Constants

# ============================================================

PACKET_HEADER_SEND = 0xAA

PACKET_HEADER_RECV = 0x55



# Opcodes

OPCODE_INQUERY = 0x01

OPCODE_ERASE   = 0x81

OPCODE_PROGRAM = 0x82

OPCODE_VERIFY  = 0x33

OPCODE_BOOT    = 0x22

OPCODE_RESET   = 0x23



# INQUERY subcodes

INQUERY_SUBCODE_VERSION = 0x00

INQUERY_SUBCODE_MTU     = 0x01



# Error codes

ERR_OK       = 0x00

ERR_OPCODE   = 0x01

ERR_OVERFLOW = 0x02

ERR_TIMEOUT  = 0x03

ERR_FORMAT   = 0x04

ERR_VERIFY   = 0x05

ERR_PARAM    = 0x06

ERR_UNKNOWN  = 0xFF



ERROR_NAMES = {

    0x00: "OK",

    0x01: "OPCODE",

    0x02: "OVERFLOW",

    0x03: "TIMEOUT",

    0x04: "FORMAT",

    0x05: "VERIFY",

    0x06: "PARAM",

    0xFF: "UNKNOWN",

}



# Default parameters

DEFAULT_BAUDRATE = 2000000

DEFAULT_TIMEOUT  = 30.0   # response timeout (s), large-area erase may take >10s

RETRY_COUNT      = 3

CHUNK_SIZE       = 4088  # MTU 4096 - 8 bytes (addr + size header)





# ============================================================

# CRC16 XMODEM

# ============================================================

CRC16_TAB = [

    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,

    0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,

    0x1231, 0x0210, 0x3273, 0x2252, 0x52B5, 0x4294, 0x72F7, 0x62D6,

    0x9339, 0x8318, 0xB37B, 0xA35A, 0xD3BD, 0xC39C, 0xF3FF, 0xE3DE,

    0x2462, 0x3443, 0x0420, 0x1401, 0x64E6, 0x74C7, 0x44A4, 0x5485,

    0xA56A, 0xB54B, 0x8528, 0x9509, 0xE5EE, 0xF5CF, 0xC5AC, 0xD58D,

    0x3653, 0x2672, 0x1611, 0x0630, 0x76D7, 0x66F6, 0x5695, 0x46B4,

    0xB75B, 0xA77A, 0x9719, 0x8738, 0xF7DF, 0xE7FE, 0xD79D, 0xC7BC,

    0x48C4, 0x58E5, 0x6886, 0x78A7, 0x0840, 0x1861, 0x2802, 0x3823,

    0xC9CC, 0xD9ED, 0xE98E, 0xF9AF, 0x8948, 0x9969, 0xA90A, 0xB92B,

    0x5AF5, 0x4AD4, 0x7AB7, 0x6A96, 0x1A71, 0x0A50, 0x3A33, 0x2A12,

    0xDBFD, 0xCBDC, 0xFBBF, 0xEB9E, 0x9B79, 0x8B58, 0xBB3B, 0xAB1A,

    0x6CA6, 0x7C87, 0x4CE4, 0x5CC5, 0x2C22, 0x3C03, 0x0C60, 0x1C41,

    0xEDAE, 0xFD8F, 0xCDEC, 0xDDCD, 0xAD2A, 0xBD0B, 0x8D68, 0x9D49,

    0x7E97, 0x6EB6, 0x5ED5, 0x4EF4, 0x3E13, 0x2E32, 0x1E51, 0x0E70,

    0xFF9F, 0xEFBE, 0xDFDD, 0xCFFC, 0xBF1B, 0xAF3A, 0x9F59, 0x8F78,

    0x9188, 0x81A9, 0xB1CA, 0xA1EB, 0xD10C, 0xC12D, 0xF14E, 0xE16F,

    0x1080, 0x00A1, 0x30C2, 0x20E3, 0x5004, 0x4025, 0x7046, 0x6067,

    0x83B9, 0x9398, 0xA3FB, 0xB3DA, 0xC33D, 0xD31C, 0xE37F, 0xF35E,

    0x02B1, 0x1290, 0x22F3, 0x32D2, 0x4235, 0x5214, 0x6277, 0x7256,

    0xB5EA, 0xA5CB, 0x95A8, 0x8589, 0xF56E, 0xE54F, 0xD52C, 0xC50D,

    0x34E2, 0x24C3, 0x14A0, 0x0481, 0x7466, 0x6447, 0x5424, 0x4405,

    0xA7DB, 0xB7FA, 0x8799, 0x97B8, 0xE75F, 0xF77E, 0xC71D, 0xD73C,

    0x26D3, 0x36F2, 0x0691, 0x16B0, 0x6657, 0x7676, 0x4615, 0x5634,

    0xD94C, 0xC96D, 0xF90E, 0xE92F, 0x99C8, 0x89E9, 0xB98A, 0xA9AB,

    0x5844, 0x4865, 0x7806, 0x6827, 0x18C0, 0x08E1, 0x3882, 0x28A3,

    0xCB7D, 0xDB5C, 0xEB3F, 0xFB1E, 0x8BF9, 0x9BD8, 0xABBB, 0xBB9A,

    0x4A75, 0x5A54, 0x6A37, 0x7A16, 0x0AF1, 0x1AD0, 0x2AB3, 0x3A92,

    0xFD2E, 0xED0F, 0xDD6C, 0xCD4D, 0xBDAA, 0xAD8B, 0x9DE8, 0x8DC9,

    0x7C26, 0x6C07, 0x5C64, 0x4C45, 0x3CA2, 0x2C83, 0x1CE0, 0x0CC1,

    0xEF1F, 0xFF3E, 0xCF5D, 0xDF7C, 0xAF9B, 0xBFBA, 0x8FD9, 0x9FF8,

    0x6E17, 0x7E36, 0x4E55, 0x5E74, 0x2E93, 0x3EB2, 0x0ED1, 0x1EF0,

]





def crc16(data: bytes, crc: int = 0) -> int:

    """XMODEM CRC16"""

    for byte in data:

        crc = ((crc << 8) ^ CRC16_TAB[((crc >> 8) ^ byte) & 0xFF]) & 0xFFFF

    return crc





def crc32(data: bytes) -> int:

    """IEEE 802.3 CRC32, matches STM32 crc32.c and zlib.crc32"""

    return zlib.crc32(data) & 0xFFFFFFFF





# ============================================================

# Packet Build / Parse

# ============================================================



def build_packet(opcode: int, payload: bytes) -> bytes:

    """Build send packet: [0xAA, opcode, len(2B LE), payload, crc16(2B LE)]"""

    length = len(payload)

    header = struct.pack('<BBH', PACKET_HEADER_SEND, opcode, length)

    body = header + payload

    csum = crc16(body)

    return body + struct.pack('<H', csum)





def parse_response(raw: bytes) -> Tuple[int, int, bytes]:

    """

    Parse bootloader response: [0x55, opcode, errcode, len(2B LE), data, crc16(2B LE)]

    Returns: (errcode, data_len, data)

    Raises ValueError on CRC mismatch or bad format.

    """

    if len(raw) < 7:

        raise ValueError(f"Response too short: {len(raw)} bytes")

    if raw[0] != PACKET_HEADER_RECV:

        raise ValueError(f"Bad response header: 0x{raw[0]:02X}")



    opcode = raw[1]

    errcode = raw[2]

    data_len = raw[3] | (raw[4] << 8)



    expected_len = 5 + data_len + 2

    if len(raw) != expected_len:

        raise ValueError(

            f"Response length mismatch: got {len(raw)}, expected {expected_len}"

        )



    data = raw[5:5 + data_len]



    # Verify CRC16

    received_crc = raw[5 + data_len] | (raw[5 + data_len + 1] << 8)

    calc_crc = crc16(raw[:5 + data_len])

    if received_crc != calc_crc:

        raise ValueError(

            f"Response CRC mismatch: got 0x{received_crc:04X}, "

            f"calc 0x{calc_crc:04X}"

        )



    return errcode, data_len, data





# ============================================================

# Serial TX/RX

# ============================================================



def open_serial(port: str, baudrate: int = DEFAULT_BAUDRATE) -> serial.Serial:

    """Open serial port"""

    ser = serial.Serial(

        port=port,

        baudrate=baudrate,

        bytesize=serial.EIGHTBITS,

        parity=serial.PARITY_NONE,

        stopbits=serial.STOPBITS_ONE,

        timeout=DEFAULT_TIMEOUT,

        write_timeout=2.0,

    )

    # Clear stale data in driver buffers (important at 2M after re-plug)

    ser.reset_input_buffer()

    ser.reset_output_buffer()

    return ser





def send_packet(ser: serial.Serial, opcode: int, payload: bytes) -> None:

    """Send a protocol packet"""

    packet = build_packet(opcode, payload)

    ser.write(packet)

    ser.flush()





def _default_debug(msg):

    """Default debug output to stdout"""

    print(msg)





def recv_response(ser: serial.Serial, timeout: float = DEFAULT_TIMEOUT,

                  debug_cb=_default_debug) -> Optional[Tuple[int, bytes]]:

    """

    Receive and parse a response packet.

    Returns (errcode, data), or None on failure.

    debug_cb is called with diagnostic messages (default: print to stdout).

    """

    # Look for 0x55 header

    header = ser.read(1)

    if not header:

        debug_cb("[recv] Timeout: no bytes received")

        return None

    if header[0] != PACKET_HEADER_RECV:

        debug_cb("[recv] Bad header: got 0x%02X, expected 0x55" % header[0])

        return None



    # Minimum remaining: opcode(1) + errcode(1) + len(2) = 4 bytes

    remaining = ser.read(4)

    if len(remaining) < 4:

        debug_cb("[recv] Too short: got %d bytes (need 4)" % len(remaining))

        return None



    data_len = remaining[2] | (remaining[3] << 8)

    # Read data + CRC16

    tail = ser.read(data_len + 2)

    if len(tail) < data_len + 2:

        debug_cb("[recv] Incomplete: data_len=%d, got %d bytes" % (data_len, len(tail)))

        return None



    raw = header + remaining + tail

    hex_str = ' '.join('%02X' % b for b in raw)

    debug_cb("[recv] Raw=%s" % hex_str)



    try:

        errcode, _, data = parse_response(raw)

        debug_cb("[recv] Parsed OK: errcode=%d, data_len=%d" % (errcode, len(data)))

        return errcode, data

    except ValueError as e:

        debug_cb("[recv] CRC mismatch: %s" % str(e))

        return None





def send_and_recv(ser: serial.Serial, opcode: int, payload: bytes,

                  retry: int = RETRY_COUNT, debug_cb=_default_debug) -> Tuple[int, bytes]:

    """

    Send command and wait for response, with retry.

    Returns (errcode, data). Raises ConnectionError on failure.

    debug_cb is called with diagnostic messages (default: print to stdout).

    """

    opcode_names = {

        0x01: "INQUERY", 0x81: "ERASE", 0x82: "PROGRAM",

        0x33: "VERIFY", 0x22: "BOOT", 0x23: "RESET",

    }

    op_name = opcode_names.get(opcode, "0x%02X" % opcode)



    # 按命令类型设置超时：快速命令快速失败，避免长时间"卡住"

    # ERASE/VERIFY 涉及大区域 Flash 操作，需要较长超时

    op_timeouts = {

        OPCODE_ERASE: 30.0,

        OPCODE_VERIFY: 30.0,

        OPCODE_PROGRAM: 10.0,

    }

    saved_timeout = ser.timeout

    ser.timeout = op_timeouts.get(opcode, 3.0)

    try:

        for attempt in range(retry):

            ser.reset_input_buffer()

            send_packet(ser, opcode, payload)



            debug_cb("[send] %s (attempt %d/%d), payload=%d bytes" % (op_name, attempt + 1, retry, len(payload)))



            result = recv_response(ser, debug_cb=debug_cb)

            if result is not None:

                return result



            if attempt < retry - 1:

                debug_cb("  Retry %d/%d..." % (attempt + 1, retry - 1))



        raise ConnectionError("No response after %d attempts" % retry)

    finally:

        ser.timeout = saved_timeout

