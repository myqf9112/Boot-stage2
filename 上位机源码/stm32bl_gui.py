# stm32bl_gui.py - STM32F407 Bootloader GUI Host Tool
import os, sys, struct, time, threading, queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import serial, serial.tools.list_ports
from protocol import (
    open_serial, send_and_recv, crc32,
    OPCODE_INQUERY, OPCODE_ERASE, OPCODE_PROGRAM, OPCODE_VERIFY,
    OPCODE_BOOT, OPCODE_RESET,
    INQUERY_SUBCODE_VERSION, INQUERY_SUBCODE_MTU,
    ERR_OK, ERROR_NAMES,
    CHUNK_SIZE, DEFAULT_BAUDRATE, DEFAULT_TIMEOUT,
)
from flasher import (
    APP_BASE_ADDRESS, APP_MAX_SIZE,
    MAGIC_HEADER_ADDRESS, MAGIC_HEADER_SIZE,
    parse_xbin, generate_magic_header,
    _align4, program_stream,
)
MSG_LOG, MSG_PROGRESS, MSG_DONE = "LOG", "PROGRESS", "DONE"


class FlashWorker(threading.Thread):
    def __init__(self, q, port, baud, bin_path, base_addr, skip_erase, skip_verify):
        super().__init__(daemon=True)
        self.q = q; self.port = port; self.baud = baud
        self.bin_path = bin_path; self.base_addr = base_addr
        self.skip_erase = skip_erase; self.skip_verify = skip_verify
        self._cancel = False
    def cancel(self): self._cancel = True
    def _log(self, text): self.q.put((MSG_LOG, text))
    def _progress(self, c, t): self.q.put((MSG_PROGRESS, c, t))
    def _check_errcode(self, e, n):
        if e != ERR_OK:
            raise RuntimeError(n + " failed: " + ERROR_NAMES.get(e, "0x%02X" % e))
    def run(self):
        ser = None
        try:
            self._log("Open %s @ %d..." % (self.port, self.baud))
            ser = open_serial(self.port, self.baud)
            self._log("Connected: %s" % self.port)
            t0 = time.time()

            # ---- Read and parse file ----
            if not os.path.exists(self.bin_path):
                raise FileNotFoundError("File not found: " + self.bin_path)
            with open(self.bin_path, 'rb') as f:
                raw_data = f.read()

            ext = os.path.splitext(self.bin_path)[1].lower()
            if ext == '.xbin':
                header_bytes, firmware, data_address, data_length, data_crc32 = parse_xbin(raw_data)
                self._log("File: %s (.xbin with magic header)" % os.path.basename(self.bin_path))
                # 解析并显示 magic header 各字段
                hdr_magic      = struct.unpack_from('<I', header_bytes, 0)[0]
                hdr_type       = struct.unpack_from('<I', header_bytes, 32)[0]
                hdr_offset     = struct.unpack_from('<I', header_bytes, 36)[0]
                hdr_hdr_crc    = struct.unpack_from('<I', header_bytes, 252)[0]
                hdr_ver = header_bytes[96:224].split(b'\x00')[0].decode('ascii', errors='replace')
                self._log("  ── Magic Header ──")
                self._log("  magic:       0x%08X" % hdr_magic)
                self._log("  data_type:    %d" % hdr_type)
                self._log("  data_offset:  %d B" % hdr_offset)
                self._log("  data_address: 0x%08X" % data_address)
                self._log("  data_length:  %d B" % data_length)
                self._log("  data_crc32:   0x%08X" % data_crc32)
                self._log("  this_crc32:   0x%08X" % hdr_hdr_crc)
                self._log("  version:      %s" % hdr_ver)
                self._log("  Header: %d B" % len(header_bytes))
                self._log("  Firmware: %d B @ 0x%08X" % (data_length, data_address))
            else:
                firmware = raw_data
                data_address = self.base_addr
                data_length = len(firmware)
                self._log("File: %s (.bin, auto-generating magic header)" % os.path.basename(self.bin_path))
                self._log("  Firmware: %d B @ 0x%08X" % (len(firmware), data_address))
                header_bytes = generate_magic_header(firmware)
                self._log("  Header: %d B (auto-generated)" % len(header_bytes))

            if len(firmware) == 0:
                raise ValueError("Firmware is empty")

            firmware = _align4(firmware)
            data_length = len(firmware)
            fw_crc32 = crc32(firmware)
            self._log("  Firmware size: %d B (%.1f KB)" % (data_length, data_length / 1024))

            # ---- Query MTU ----
            try:
                payload = struct.pack('<B', INQUERY_SUBCODE_MTU)
                errcode, data = send_and_recv(ser, OPCODE_INQUERY, payload)
                self._check_errcode(errcode, "MTU")
                mtu = struct.unpack('<H', data)[0]
                self._log("MTU: %d bytes" % mtu)
                actual_chunk = min(CHUNK_SIZE, mtu - 8)
            except Exception:
                self._log("  (using default chunk size)")
                actual_chunk = CHUNK_SIZE

            # ---- ERASE ----
            if self.skip_erase:
                self._log("ERASE: SKIPPED")
            else:
                self._log("Erasing magic header 0x%08X +%d..." % (MAGIC_HEADER_ADDRESS, MAGIC_HEADER_SIZE))
                payload = struct.pack('<II', MAGIC_HEADER_ADDRESS, MAGIC_HEADER_SIZE)
                errcode, _ = send_and_recv(ser, OPCODE_ERASE, payload)
                self._check_errcode(errcode, "ERASE HEADER")
                self._log("  Header erase: OK")

                self._log("Erasing APP region 0x%08X +%d..." % (data_address, data_length))
                payload = struct.pack('<II', data_address, data_length)
                errcode, _ = send_and_recv(ser, OPCODE_ERASE, payload)
                self._check_errcode(errcode, "ERASE APP")
                self._log("  APP erase: OK (synchronous erase complete)")

            # ---- PROGRAM Magic Header ----
            self._log("Programming magic header to 0x%08X (%d B)..." % (MAGIC_HEADER_ADDRESS, len(header_bytes)))
            hdr_offset = 0
            while hdr_offset < len(header_bytes) and not self._cancel:
                chunk = header_bytes[hdr_offset:hdr_offset + actual_chunk]
                payload = struct.pack('<II', MAGIC_HEADER_ADDRESS + hdr_offset, len(chunk)) + chunk
                errcode, _ = send_and_recv(ser, OPCODE_PROGRAM, payload)
                self._check_errcode(errcode, "PROGRAM HEADER")
                hdr_offset += len(chunk)
            self._log("  Header: OK")

            if self._cancel:
                self._log("CANCELLED")
                self.q.put((MSG_DONE, False, "Cancelled"))
                return

            # ---- PROGRAM Firmware (pipelined) ----
            chunk_total = (data_length + actual_chunk - 1) // actual_chunk
            self._log("Programming %d chunks (%d B each, pipelined)..." % (chunk_total, actual_chunk))
            chunks = [
                (data_address + off, firmware[off:off + actual_chunk])
                for off in range(0, data_length, actual_chunk)
            ]

            def _on_program_ack(done):
                if self._cancel:
                    raise RuntimeError("Cancelled by user")
                self._progress(done, data_length)

            program_stream(ser, chunks, progress_cb=_on_program_ack)

            if self._cancel:
                self._log("CANCELLED")
                self.q.put((MSG_DONE, False, "Cancelled"))
                return

            # ---- VERIFY ----
            if self.skip_verify:
                self._log("VERIFY: SKIPPED")
            else:
                self._log("Verifying firmware 0x%08X +%d..." % (data_address, data_length))
                payload = struct.pack('<III', data_address, data_length, fw_crc32)
                errcode, _ = send_and_recv(ser, OPCODE_VERIFY, payload)
                self._check_errcode(errcode, "VERIFY")
                self._log("VERIFY: OK (CRC32=0x%08X)" % fw_crc32)

            # ---- BOOT ----
            self._log("Booting...")
            errcode, _ = send_and_recv(ser, OPCODE_BOOT, b'')
            self._check_errcode(errcode, "BOOT")
            self._log("BOOT: OK")
            self._log("=== UPGRADE SUCCESS (%.2f s) ===" % (time.time() - t0))
            self.q.put((MSG_DONE, True, "Success"))
        except Exception as e:
            self._log("ERROR: " + str(e))
            self.q.put((MSG_DONE, False, str(e)))
        finally:
            if ser and ser.is_open:
                ser.close()


class SimpleWorker(threading.Thread):
    def __init__(self, q, port, baud, cmd):
        super().__init__(daemon=True)
        self.q = q; self.port = port; self.baud = baud; self.cmd = cmd
    def _log(self, text): self.q.put((MSG_LOG, text))
    def _check(self, e, n):
        if e != ERR_OK:
            raise RuntimeError(n + " failed: " + ERROR_NAMES.get(e, "0x%02X" % e))
    def run(self):
        ser = None
        try:
            ser = open_serial(self.port, self.baud)
            self._log("Connected: %s" % self.port)
            if self.cmd == "inquery":
                p = struct.pack('<B', INQUERY_SUBCODE_VERSION)
                e, d = send_and_recv(ser, OPCODE_INQUERY, p)
                self._check(e, "VERSION")
                self._log("Version: " + d.decode('ascii', errors='replace'))
                p = struct.pack('<B', INQUERY_SUBCODE_MTU)
                e, d = send_and_recv(ser, OPCODE_INQUERY, p)
                self._check(e, "MTU")
                self._log("MTU: %d bytes" % struct.unpack('<H', d)[0])
            elif self.cmd == "boot":
                e, _ = send_and_recv(ser, OPCODE_BOOT, b'')
                self._check(e, "BOOT")
                self._log("BOOT: OK")
            elif self.cmd == "reset":
                e, _ = send_and_recv(ser, OPCODE_RESET, b'')
                self._check(e, "RESET")
                self._log("RESET: OK")
            self.q.put((MSG_DONE, True, "Done"))
        except Exception as e:
            self._log("ERROR: " + str(e))
            self.q.put((MSG_DONE, False, str(e)))
        finally:
            if ser and ser.is_open:
                ser.close()


class BootloaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("STM32F407 Bootloader - Firmware Upgrade Tool")
        self.root.geometry("720x620")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.worker = None
        self.msg_queue = queue.Queue()
        self._build_ui()
        self._refresh_ports()
        self._poll_queue()

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use('clam')

        # 修复配色：统一、清晰、高对比度
        BG = '#f5f5f5'               # 主背景
        CARD_BG = '#ffffff'          # 卡片背景
        ACCENT = '#0078D7'            # 主色调（Windows标准蓝）
        ACCENT_HOVER = '#005A9E'      # 按钮悬停色
        TEXT_NORMAL = '#000000'       # 正常文字（黑色）
        TEXT_SECONDARY = '#333333'    # 次要文字
        TEXT_DISABLED = '#666666'     # 禁用文字
        COLOR_SUCCESS = '#00B42A'     # 成功色
        COLOR_WARN = '#FF7D00'        # 警告色
        COLOR_ERROR = '#F53F3F'       # 错误色

        # 全局样式配置
        self.root.configure(bg=BG)

        # 进度条样式
        style.configure(
            'TProgressbar',
            background=ACCENT,
            troughcolor="#e0e0e0",
            borderwidth=0
        )

        # 卡片/标签框样式
        style.configure(
            'Card.TLabelframe',
            background=CARD_BG,
            relief='solid',
            borderwidth=1,
            bordercolor="#e0e0e0"
        )
        style.configure(
            'Card.TLabelframe.Label',
            font=('Segoe UI', 10, 'bold'),
            foreground=TEXT_NORMAL,
            background=CARD_BG
        )

        # 基础控件样式
        style.configure(
            'TLabel',
            font=('Segoe UI', 9),
            foreground=TEXT_NORMAL,
            background=CARD_BG
        )
        style.configure(
            'TButton',
            font=('Segoe UI', 9),
            padding=(12, 6)
        )
        style.configure(
            'TCheckbutton',
            font=('Segoe UI', 9),
            background=CARD_BG
        )
        style.configure(
            'TEntry',
            font=('Segoe UI', 9),
            padding=4
        )
        style.configure(
            'TCombobox',
            font=('Segoe UI', 9),
            padding=4
        )

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill=tk.BOTH, expand=True, padx=12, pady=(8, 12))

        # 标题栏
        banner = tk.Frame(main, bg=ACCENT, height=44)
        banner.pack(fill=tk.X, pady=(0, 10))
        banner.pack_propagate(False)
        tk.Label(
            banner, text='STM32F407 Bootloader 上位机',
            font=('Segoe UI', 13, 'bold'), fg='#ffffff', bg=ACCENT
        ).pack(side=tk.LEFT, padx=14, pady=9)
        tk.Label(
            banner, text='固件升级工具',
            font=('Segoe UI', 9), fg='#d0e8ff', bg=ACCENT
        ).pack(side=tk.LEFT, padx=(0, 14), pady=9)

        # 串口设置
        conn_frame = ttk.LabelFrame(main, text=' 串口设置 ', style='Card.TLabelframe', padding=10)
        conn_frame.pack(fill=tk.X, pady=(0, 8))
        cr = tk.Frame(conn_frame, bg=CARD_BG); cr.pack(fill=tk.X)
        ttk.Label(cr, text='端口:').pack(side=tk.LEFT)
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(cr, textvariable=self.port_var, width=10, state='readonly')
        self.port_combo.pack(side=tk.LEFT, padx=(6, 12))
        self.port_combo.bind('<Button-1>', lambda e: self._refresh_ports())
        ttk.Label(cr, text='波特率:').pack(side=tk.LEFT)
        self.baud_var = tk.StringVar(value=str(DEFAULT_BAUDRATE))
        ttk.Combobox(
            cr, textvariable=self.baud_var, width=8,
            values=['9600','19200','38400','57600','115200','230400','460800','921600','2000000']
        ).pack(side=tk.LEFT, padx=(6, 12))
        self.status_label = tk.Label(
            cr, text='就绪', font=('Segoe UI', 9, 'bold'),
            fg=TEXT_SECONDARY, bg=CARD_BG
        )
        self.status_label.pack(side=tk.RIGHT)

        # 固件设置
        fw_frame = ttk.LabelFrame(main, text=' 固件 ', style='Card.TLabelframe', padding=10)
        fw_frame.pack(fill=tk.X, pady=(0, 8))
        r1 = tk.Frame(fw_frame, bg=CARD_BG); r1.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(r1, text='文件:').pack(side=tk.LEFT)
        self.file_var = tk.StringVar()
        ttk.Entry(r1, textvariable=self.file_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(r1, text='浏览', command=self._browse_file).pack(side=tk.LEFT)
        r2 = tk.Frame(fw_frame, bg=CARD_BG); r2.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(r2, text='地址(.bin):').pack(side=tk.LEFT)
        self.addr_var = tk.StringVar(value='0x%08X' % APP_BASE_ADDRESS)
        ttk.Entry(r2, textvariable=self.addr_var, width=14, font=('Consolas', 9)).pack(side=tk.LEFT, padx=6)
        self.skip_erase_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r2, text='跳过擦除', variable=self.skip_erase_var).pack(side=tk.LEFT, padx=10)
        self.skip_verify_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r2, text='跳过校验', variable=self.skip_verify_var).pack(side=tk.LEFT, padx=10)
        self.flash_btn = tk.Button(
            fw_frame, text='烧录固件', command=self._start_flash,
            font=('Segoe UI', 10, 'bold'), bg=ACCENT, fg='#ffffff',
            activebackground=ACCENT_HOVER, activeforeground='white',
            relief='flat', padx=24, pady=8, cursor='hand2'
        )
        self.flash_btn.pack(pady=(6, 0))

        # 功能按钮
        cmd_frame = tk.Frame(main, bg=BG); cmd_frame.pack(fill=tk.X, pady=(0, 8))
        btn_style = {
            'font': ('Segoe UI', 9), 'relief': 'flat',
            'padx': 12, 'pady': 4, 'cursor': 'hand2',
            'bg': CARD_BG, 'fg': TEXT_NORMAL
        }
        self.inquery_btn = tk.Button(cmd_frame, text='查询信息', command=lambda: self._simple_cmd('inquery'), **btn_style)
        self.inquery_btn.pack(side=tk.LEFT, padx=(0, 6))

        # 启动APP按钮（绿色文字）
        boot_style = btn_style.copy()
        boot_style['fg'] = COLOR_SUCCESS
        self.boot_btn = tk.Button(cmd_frame, text='启动APP', command=lambda: self._simple_cmd('boot'), **boot_style)
        self.boot_btn.pack(side=tk.LEFT, padx=(0, 6))

        # 复位按钮（红色文字）
        reset_style = btn_style.copy()
        reset_style['fg'] = COLOR_ERROR
        self.reset_btn = tk.Button(cmd_frame, text='复位', command=lambda: self._simple_cmd('reset'), **reset_style)
        self.reset_btn.pack(side=tk.LEFT, padx=(0, 6))

        # 取消按钮
        cancel_style = btn_style.copy()
        cancel_style['bg'] = '#fff0f0'
        self.cancel_btn = tk.Button(cmd_frame, text='取消', command=self._cancel, state=tk.DISABLED, **cancel_style)
        self.cancel_btn.pack(side=tk.RIGHT)

        # 进度条
        prog_frame = tk.Frame(main, bg=BG)
        prog_frame.pack(fill=tk.X, pady=(0, 8))
        self.progress_var = tk.IntVar(value=0)
        self.progress_bar = ttk.Progressbar(prog_frame, variable=self.progress_var, maximum=100, mode='determinate')
        self.progress_bar.pack(fill=tk.X, ipady=2)
        self.progress_label = tk.Label(
            prog_frame, text='Ready', font=('Segoe UI', 9, 'bold'),
            fg=TEXT_NORMAL, bg=BG
        )
        self.progress_label.pack(anchor=tk.W, pady=(4, 0))

        # 日志区域
        log_frame = ttk.LabelFrame(main, text=' 日志 ', style='Card.TLabelframe', padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=10, state=tk.DISABLED, wrap=tk.WORD,
            font=('Consolas', 9), bg="#fafafa", fg=TEXT_NORMAL,
            relief='flat', borderwidth=0
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_config('ERROR', foreground=COLOR_ERROR)    # 错误日志
        self.log_text.tag_config('OK', foreground=COLOR_SUCCESS)      # 成功日志

        # 保存颜色变量供后续使用
        self.TEXT_NORMAL = TEXT_NORMAL
        self.TEXT_SECONDARY = TEXT_SECONDARY
        self.COLOR_WARN = COLOR_WARN
        self.COLOR_SUCCESS = COLOR_SUCCESS
        self.COLOR_ERROR = COLOR_ERROR

    def _refresh_ports(self, event=None):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo['values'] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title='选择固件文件',
            filetypes=[
                ('Firmware files', '*.xbin *.bin'),
                ('XBin with header', '*.xbin'),
                ('Binary files', '*.bin'),
                ('All files', '*.*'),
            ]
        )
        if path: self.file_var.set(path)

    def _log(self, text, tag=None):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + '\n', tag if tag else '')
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _set_ui_state(self, busy):
        state = tk.DISABLED if busy else tk.NORMAL
        for b in [self.flash_btn, self.inquery_btn, self.boot_btn, self.reset_btn]:
            b.configure(state=state)
        self.cancel_btn.configure(state=tk.NORMAL if busy else tk.DISABLED)
        self.port_combo.configure(state='readonly' if not busy else tk.DISABLED)

        # 修复状态文字颜色
        if busy:
            self.status_label.configure(text='忙碌中', fg=self.COLOR_WARN)
        else:
            self.status_label.configure(text='就绪', fg=self.TEXT_SECONDARY)

    def _start_flash(self):
        port = self.port_var.get().strip()
        if not port: messagebox.showwarning('警告', '请先选择串口'); return
        path = self.file_var.get().strip()
        if not path: messagebox.showwarning('警告', '请先选择固件文件'); return
        try: addr = int(self.addr_var.get(), 0)
        except ValueError: messagebox.showerror('错误', '地址格式无效'); return
        baud = int(self.baud_var.get())
        self._set_ui_state(True)
        self.progress_var.set(0)
        self.progress_label.configure(text='Preparing...')
        self.log_text.configure(state=tk.NORMAL); self.log_text.delete(1.0, tk.END); self.log_text.configure(state=tk.DISABLED)
        self._log('=' * 50)
        self._log('开始固件烧录...')
        self.worker = FlashWorker(self.msg_queue, port, baud, path, addr, self.skip_erase_var.get(), self.skip_verify_var.get())
        self.worker.start()

    def _simple_cmd(self, cmd):
        port = self.port_var.get().strip()
        if not port: messagebox.showwarning('警告', '请先选择串口'); return
        baud = int(self.baud_var.get())
        self._set_ui_state(True); self.progress_var.set(0)
        self._log('-' * 40)
        names = {'inquery': 'Query', 'boot': 'Boot', 'reset': 'Reset'}
        self._log('执行: %s...' % names.get(cmd, cmd))
        self.worker = SimpleWorker(self.msg_queue, port, baud, cmd)
        self.worker.start()

    def _cancel(self):
        if self.worker and self.worker.is_alive():
            self.worker.cancel()
            self._log('取消中...')

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            self.worker.cancel()
        self.root.destroy()

    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                t = msg[0]
                if t == MSG_LOG: self._log(msg[1])
                elif t == MSG_PROGRESS:
                    cur, tot = msg[1], msg[2]
                    pct = int(cur/tot*100) if tot > 0 else 0
                    self.progress_var.set(pct)
                    self.progress_label.configure(text='%d / %d B  (%d%%)' % (cur, tot, pct))
                elif t == MSG_DONE:
                    ok, m = msg[1], msg[2]
                    self._set_ui_state(False)
                    if ok:
                        self._log('完成', 'OK')
                        self.progress_label.configure(text='完成')
                    else:
                        self._log('失败: ' + m, 'ERROR')
                        self.progress_label.configure(text='失败')
                    self.worker = None
                self.msg_queue.task_done()
        except queue.Empty: pass
        self.root.after(100, self._poll_queue)


def main():
    root = tk.Tk()
    BootloaderGUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
