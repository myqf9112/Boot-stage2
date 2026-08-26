# stm32bl_debug_gui.py - STM32F407 Bootloader 调试工具 (GUI)
# 功能：
#   - 打开/关闭串口（保持连接）
#   - 快捷命令：查询版本 / 查询MTU / 启动APP / 复位
#   - 手动命令：任意 opcode + payload(hex) 发送
#   - 收发原始字节 hex 显示（调试 2M 波特率时观察字节是否正确）
#
# 用法：
#   python stm32bl_debug_gui.py

import queue
import struct
import threading

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

import serial
import serial.tools.list_ports

from protocol import (
    build_packet, parse_response,
    OPCODE_INQUERY, OPCODE_ERASE, OPCODE_PROGRAM, OPCODE_VERIFY,
    OPCODE_BOOT, OPCODE_RESET,
    INQUERY_SUBCODE_VERSION, INQUERY_SUBCODE_MTU,
    DEFAULT_BAUDRATE,
)

# 命令表：文本 -> (opcode, 是否快捷命令)
COMMANDS = [
    ("0x01 INQUERY-VERSION", OPCODE_INQUERY, b'\x00'),
    ("0x01 INQUERY-MTU",     OPCODE_INQUERY, b'\x01'),
    ("0x81 ERASE",           OPCODE_ERASE,   b''),
    ("0x82 PROGRAM",         OPCODE_PROGRAM, b''),
    ("0x33 VERIFY",          OPCODE_VERIFY,  b''),
    ("0x22 BOOT",            OPCODE_BOOT,    b''),
    ("0x23 RESET",           OPCODE_RESET,   b''),
]

MSG_TX   = "TX"     # 发送的原始字节
MSG_RX   = "RX"     # 接收的原始字节
MSG_INFO = "INFO"   # 解析信息
MSG_ERR  = "ERR"    # 错误
MSG_LOG  = "LOG"    # 普通日志
MSG_STATE = "STATE" # 连接状态


def _fmt_hex(data: bytes) -> str:
    return ' '.join('%02X' % b for b in data)


class DebugGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("STM32F407 Bootloader 调试工具")
        self.root.geometry("820x600")
        self.ser: serial.Serial = None
        self.msg_queue = queue.Queue()

        self._build_ui()
        self._refresh_ports()
        self._poll_queue()

    # ---------------- UI ----------------
    def _build_ui(self):
        main = tk.Frame(self.root, padx=10, pady=8)
        main.pack(fill=tk.BOTH, expand=True)

        # 串口设置
        conn = ttk.LabelFrame(main, text=" 串口 ", padding=8)
        conn.pack(fill=tk.X, pady=(0, 6))
        r1 = tk.Frame(conn); r1.pack(fill=tk.X)
        ttk.Label(r1, text="端口:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(r1, textvariable=self.port_var, width=12, state='readonly')
        self.port_combo.pack(side=tk.LEFT, padx=(4, 12))
        self.port_combo.bind('<Button-1>', lambda e: self._refresh_ports())
        ttk.Label(r1, text="波特率:").pack(side=tk.LEFT)
        self.baud_var = tk.StringVar(value=str(DEFAULT_BAUDRATE))
        ttk.Combobox(
            r1, textvariable=self.baud_var, width=8,
            values=['9600', '19200', '38400', '57600', '115200', '230400', '460800', '921600', '2000000']
        ).pack(side=tk.LEFT, padx=(4, 12))

        self.open_btn = ttk.Button(r1, text="打开串口", command=self._toggle_port)
        self.open_btn.pack(side=tk.LEFT)
        self.state_label = tk.Label(r1, text="未连接", fg="#cc0000", font=('Segoe UI', 9, 'bold'))
        self.state_label.pack(side=tk.RIGHT)

        # 快捷命令
        quick = ttk.LabelFrame(main, text=" 快捷命令 ", padding=8)
        quick.pack(fill=tk.X, pady=(0, 6))
        self._make_quick_btn(quick, "查询版本", b'\x00')
        self._make_quick_btn(quick, "查询MTU", b'\x01')
        self._make_quick_btn(quick, "启动APP", None, OPCODE_BOOT)
        self._make_quick_btn(quick, "复位", None, OPCODE_RESET)

        # 手动命令
        manual = ttk.LabelFrame(main, text=" 手动命令 ", padding=8)
        manual.pack(fill=tk.X, pady=(0, 6))
        m1 = tk.Frame(manual); m1.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(m1, text="Opcode:").pack(side=tk.LEFT)
        self.op_var = tk.StringVar(value="0x01 INQUERY")
        ttk.Combobox(
            m1, textvariable=self.op_var, width=20, state='readonly',
            values=['0x01 INQUERY', '0x81 ERASE', '0x82 PROGRAM', '0x33 VERIFY', '0x22 BOOT', '0x23 RESET']
        ).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(m1, text="Payload(hex):").pack(side=tk.LEFT)
        self.payload_var = tk.StringVar(value="00")
        ttk.Entry(m1, textvariable=self.payload_var, width=46, font=('Consolas', 9)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Button(m1, text="发送", command=self._send_manual).pack(side=tk.LEFT)

        # 收发日志
        logf = ttk.LabelFrame(main, text=" 收发日志 ", padding=4)
        logf.pack(fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(
            logf, height=18, state=tk.DISABLED, wrap=tk.WORD,
            font=('Consolas', 9), bg='#fafafa'
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_config('TX', foreground='#0055cc')
        self.log_text.tag_config('RX', foreground='#008000')
        self.log_text.tag_config('ERR', foreground='#cc0000')
        self.log_text.tag_config('INFO', foreground='#000000')
        self.log_text.tag_config('LOG', foreground='#666666')

    def _make_quick_btn(self, parent, text, inquery_sub=None, opcode=None):
        if inquery_sub is not None:
            cmd = lambda: self._send_worker(OPCODE_INQUERY, bytes([inquery_sub]))
        else:
            cmd = lambda: self._send_worker(opcode, b'')
        ttk.Button(parent, text=text, command=cmd).pack(side=tk.LEFT, padx=(0, 6))

    def _refresh_ports(self, event=None):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo['values'] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def _log(self, text, tag=MSG_LOG):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + '\n', tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ---------------- 串口 ----------------
    def _toggle_port(self):
        if self.ser and self.ser.is_open:
            self._close_port()
        else:
            self._open_port()

    def _open_port(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("警告", "请先选择串口")
            return
        try:
            baud = int(self.baud_var.get())
            self.ser = serial.Serial(
                port=port, baudrate=baud,
                bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=2.0, write_timeout=2.0,
            )
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        except Exception as e:
            messagebox.showerror("错误", "无法打开串口 %s:\n%s" % (port, e))
            self.ser = None
            return
        self.open_btn.configure(text="关闭串口")
        self.state_label.configure(text="已连接 %s @ %d" % (port, baud), fg="#008000")
        self._log("[LOG] 串口已打开: %s @ %d" % (port, baud))

    def _close_port(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None
        self.open_btn.configure(text="打开串口")
        self.state_label.configure(text="未连接", fg="#cc0000")
        self._log("[LOG] 串口已关闭")

    # ---------------- 发送 ----------------
    def _send_manual(self):
        if not (self.ser and self.ser.is_open):
            messagebox.showwarning("警告", "请先打开串口")
            return
        op_text = self.op_var.get().strip()
        try:
            opcode = int(op_text.split()[0], 16)
        except Exception:
            messagebox.showerror("错误", "Opcode 无效: %s" % op_text)
            return
        hex_str = self.payload_var.get().strip()
        try:
            payload = bytes.fromhex(hex_str.replace(' ', '').replace(',', ''))
        except Exception:
            messagebox.showerror("错误", "Payload hex 无效: %s" % hex_str)
            return
        threading.Thread(target=self._send_worker, args=(opcode, payload), daemon=True).start()

    def _send_worker(self, opcode: int, payload: bytes):
        try:
            packet = build_packet(opcode, payload)
            self.ser.write(packet)
            self.ser.flush()
            self.msg_queue.put((MSG_TX, "TX %s" % _fmt_hex(packet)))

            resp = self._read_response(self.ser, timeout=3.0)
            if not resp:
                self.msg_queue.put((MSG_ERR, "无响应（超时 3s）"))
                return
            if resp[0] != 0x55:
                self.msg_queue.put((MSG_RX, "RX %s  (头字节异常，期望 0x55)" % _fmt_hex(resp)))
                return
            self.msg_queue.put((MSG_RX, "RX %s" % _fmt_hex(resp)))
            try:
                errcode, data_len, data = parse_response(resp)
                self.msg_queue.put((MSG_INFO,
                    "响应解析 OK: errcode=0x%02X, data_len=%d, data=%s"
                    % (errcode, data_len, _fmt_hex(data) if data else "(空)")))
            except Exception as e:
                self.msg_queue.put((MSG_ERR, "响应解析失败: %s" % e))
        except Exception as e:
            self.msg_queue.put((MSG_ERR, "发送失败: %s" % e))

    @staticmethod
    def _read_response(ser: serial.Serial, timeout: float):
        """读取一个响应包：0x55 + opcode + errcode + len(2B) + data + crc16(2B)"""
        old_timeout = ser.timeout
        ser.timeout = timeout
        try:
            header = ser.read(1)
            if not header:
                return b''
            rest = ser.read(4)
            if len(rest) < 4:
                return header + rest
            data_len = rest[2] | (rest[3] << 8)
            tail = ser.read(data_len + 2)
            return header + rest + tail
        finally:
            ser.timeout = old_timeout

    # ---------------- 消息循环 ----------------
    def _poll_queue(self):
        try:
            while True:
                tag, text = self.msg_queue.get_nowait()
                self._log(text, tag)
                self.msg_queue.task_done()
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _on_close(self):
        self._close_port()
        self.root.destroy()


def main():
    root = tk.Tk()
    gui = DebugGUI(root)
    root.protocol("WM_DELETE_WINDOW", gui._on_close)
    root.mainloop()


if __name__ == '__main__':
    main()
