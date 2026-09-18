# BOOT2026 面试复习（深度版）

> 目标：把这个项目的每一个实现细节都讲透，做到"面试官随便问都不虚"。
> 来源：逐文件精读 `app/ driver/ components/ 上位机源码/ platform/ Makefile` 后整理。

---

## 0. 项目定位（30 秒开场）

**STM32F407VGT6 的 A/B 双备份串口 Bootloader + Python 上位机烧录工具**，1MB Flash 分成 Bootloader / Boot State / A 槽 / B 槽四区，实现"升级失败自动回滚"的高可靠 OTA。

- 固件：GCC 10.3 + Makefile，**LL 库为主 + Flash 部分用 HAL**，无 RTOS，裸机
- 上位机：Python（`pyserial`），CLI + tkinter GUI，支持断点续传 / 固件去重 / 2Mbps 高速烧录
- 核心数据流：`上位机 → USB转串口(2Mbps) → USART3 + DMA → RingBuffer → 状态机解帧 → Flash 编程 → ACK`

---

## 1. 系统启动与时钟树（board.c）——能当场算

```
HSE 8MHz ──PLLM(/4)── 2MHz ──PLLN(×168)── 336MHz ──PLLP(/2)── SYSCLK=168MHz
                                                      └─PLLQ(/4)── 48MHz (USB/SDIO)
AHB = 168MHz (DIV1)
APB1 = 42MHz (DIV4)   → TIM6 定时器时钟 ×2 = 84MHz
APB2 = 84MHz (DIV2)
```

必须脱口而出的 4 个配置点：

| 配置 | 值 | 为什么 |
|------|-----|--------|
| Flash 等待周期 | `LL_FLASH_LATENCY_5`（5 WS） | 168MHz @ 3.3V 数据手册要求 5 WS |
| 电压调节 | `LL_PWR_REGU_VOLTAGE_SCALE1` | Scale1 才支持 168MHz |
| 中断分组 | `NVIC_SetPriorityGrouping(3)` | 4bit 抢占 / 0bit 子优先级，16 级抢占 |
| `printf` 重定向 | `fputc` → USART1（115200） | 日志走 USART1，烧录走 USART3，互不干扰 |

> 注意：定义同时开了 `USE_HAL_DRIVER` 和 `USE_FULL_LL_DRIVER`——**时钟/GPIO/USART/DMA/TIM/IWDG 用 LL，只有 Flash 擦写用了 HAL**（`HAL_FLASH_Program` / `HAL_FLASHEx_Erase`）。面试官问"为什么混用"：Flash 的 HAL 封装了擦写时序和错误标志处理，省去手写；其余外设 LL 更轻量、无句柄开销。

---

## 2. 时间戳系统（tim_delay.c）——藏着两个经典考点

**TIM6 配置推导**：
```c
apb1_tim_freq_mhz = PCLK1(42MHz) * 2 = 84        // APB1 分频>1 时定时器时钟倍频
Prescaler = 83                                   // 84MHz / 84 = 1MHz 计数
Autoreload = 999                                 // 1MHz / 1000 = 1kHz 溢出 → 1ms
TIM6_DAC_IRQHandler: tim_tick_count += 1000      // 单位是"微秒"
```

**考点 1：`tim_now()` 的 do-while 双重读**（解决读-中断竞争）：
```c
uint64_t tim_now(void) {
    uint64_t now, last_count;
    do {
        last_count = tim_tick_count;             // 第一次读快照
        now = tim_tick_count + LL_TIM_GetCounter(TIM6);
    } while (last_count != tim_tick_count);      // 若期间 tick 变了就重读
    return now;
}
```
> 原理：`tick_count`（毫秒）+ `CNT`（毫秒内微秒）分两次读，中间若发生 1ms 溢出中断，两个值就"撕裂"了。反复读直到两次 `tick_count` 一致，保证拼出的微秒数原子正确。本质是 **seqlock（顺序锁）** 思想。

**考点 2：擦写期间时间戳会"冻结"**：
> Flash 编程/擦除时 CPU 取指被阻塞（flash busy），TIM6 中断停摆，`tick_count` 不再增长。所以 bootloader 主循环里的 20ms 帧超时判断、IWDG 喂狗周期回调，在擦写期间全部失效——这是本项目**为什么喂狗不能只靠 TIM6 中断、IWDG 要放宽到 3.2s** 的根因。

---

## 3. 通信链路（bl_usart.c + ringbuffer.c）——最容易被深挖

**硬件**：USART3，TX=PB10，RX=PB11，AF7，**2Mbps 8-N-1**；DMA1 Stream1 Channel4（F407 固定映射 `USART3_RX`）。

**两级缓冲**：
```
USART3 RX ──DMA1_Stream1(环形 8KB)──> 主循环 bl_usart_flush_rx() ──> RingBuffer(8KB) ──> 状态机
```

**`bl_usart_flush_rx()` 的 NDTR 算法**（务必能讲）：
```c
uint32_t remain = LL_DMA_GetDataLength(DMA1, LL_DMA_STREAM_1); // 剩余未搬运字节数
uint32_t pos = DMA_RX_BUF_SIZE - remain;                       // 反推 DMA 写到哪里
// pos > last: 顺序搬 [last, pos)
// pos <= last: 环形回绕，分两段搬 [last, 8K) 和 [0, pos)
dma_rx_last = (pos == DMA_RX_BUF_SIZE) ? 0 : pos;              // 满一圈归 0
```
> 关键：用 **DMA 的 NDTR（剩余传输计数）做无中断的"写指针"**，不需要 DMA 传输完成中断，彻底绕开"Flash 编程时 CPU 进不了中断"的问题。

**为什么主循环轮询而不是中断**：
- 擦写 Flash 时 CPU 取指阻塞，任何中断都进不去
- USART 无 FIFO，此时到达的字节若没人取会丢（ORE 溢出）
- 但 DMA 是硬件搬运，**CPU 停摆期间照样把串口数据写进内存**，事后主循环一次性冲刷即可

**`USART3_IRQHandler` 里的 ORE 处理**（细节分）：
```c
if (LL_USART_IsActiveFlag_ORE(USART3))  LL_USART_ClearFlag_ORE(USART3);
```
> ORE = 溢出错误，DMA 模式下如果瞬时没跟上会置位，不清除会卡死后续接收。这是串口 DMA 的经典坑。

**环形缓冲（ringbuffer.c）**：
- 单生产者单消费者，head/tail + 柔性数组 `buffer[]`
- `rb_new`：`size = length - sizeof(struct ringbuffer)`（头结构吃掉一部分）
- 满判断：`new_head == tail`，**牺牲一个字节**区分空/满
- 单字节 `rb_put`，批量 `rb_puts` 逐字节调（够用但非最优）

> 深挖点：**DMA 缓冲区不能放 CCMRAM（0x10000000）**——F407 的 64KB CCMRAM 只能 CPU 访问，DMA 访问不到。本项目 `dma_rx_buf` 默认放普通 SRAM（0x20000000），这是链接脚本层面的隐含约束。

---

## 4. 通信协议（protocol.py + bootloader.c）——注意字节序！

**帧格式（全部小端 LE）**：
```
请求: 0xAA | opcode(1) | length(2,LE) | payload(length) | CRC16(2,LE)
响应: 0x55 | opcode(1) | errcode(1) | length(2,LE) | payload(length) | CRC16(2,LE)
```
> ⚠️ 字节序是**小端**：C 端 `get_u16`/`put_u16` 直接 `*(uint16_t*)ptr`（STM32 小端），Python 端 `struct.pack('<BBH', ...)`。面试被问"大端还是小端"直接答**小端**，并能说出两端代码依据。

**CRC 参数（必须背准）**：
| | CRC16（帧校验） | CRC32（固件/Header 校验） |
|---|---|---|
| 名称 | XMODEM | IEEE 802.3（同 zlib.crc32） |
| Poly | 0x1021 | 0xEDB88320（0x04C11DB7 的 LSB-first 反射） |
| Init | 0x0000 | 0xFFFFFFFF |
| 反射 | 无 | 有 |
| XorOut | 0x0000 | 0xFFFFFFFF |
| 校验值"123456789" | 0x31C3 | 0xCBF43926 |

**查表法核心循环**（能默写）：
```c
// CRC16:  crc = (crc << 8) ^ tab[((crc >> 8) ^ byte) & 0xFF];
// CRC32:  crc = tab[(crc ^ byte) & 0xFF] ^ (crc >> 8);   // init=~0, 结尾再 ^~0
```

**Opcode / 错误码表**：
| Opcode | 值 | | 错误码 | 值 |
|--------|----|-|--------|----|
| INQUERY | 0x01 | | OK | 0x00 |
| ERASE | 0x81 | | OPCODE | 0x01 |
| PROGRAM | 0x82 | | OVERFLOW | 0x02 |
| VERIFY | 0x33 | | TIMEOUT | 0x03 |
| BOOT | 0x22 | | FORMAT | 0x04 |
| RESET | 0x23 | | VERIFY | 0x05 |
| SWITCH_SLOT | 0x24 | | PARAM | 0x06 |
| | | | FLASH | 0x07 |

INQUERY 子码：0x00 版本 / 0x01 MTU / 0x02 槽位状态。

**MCU 解帧状态机**（`bl_byte_handler`）：
```
HEADER → OPCODE → LENGTH → PAYLOAD → CRC16 → 完整帧
```
- `PACKET_STATE_LENGTH`：`payload_length <= PAYLOAD_SIZE_MAX(4104)` 才接受，否则复位
- 字节间超时 `RX_TIMEOUT_MS = 20ms`：超时且不在 HEADER 态就复位状态机（防半包卡死）
- 注意 payload 与 `packet_buffer` 偏移：payload 从下标 4 开始（0=header, 1=opcode, 2~3=length）

---

## 5. Flash 编程底层（stm32flash.c）——硬件特性是加分项

**F407 扇区表（必须背）**：
```
Sector 0~3 : 16KB × 4
Sector 4   : 64KB
Sector 5~11: 128KB × 7
总计 1MB
```

**三个硬件特性**（面试亮点）：
1. **Flash 只能 1→0，不能 0→1**：所以写前必须擦除（擦除 = 整扇区置 0xFF）
2. **擦除粒度是扇区**：`FLASH_TYPEERASE_SECTORS`，哪怕写 4 字节也要擦整个扇区
3. **F405/407 不支持 64 位并行编程**：`FLASH_TYPEPROGRAM_DOUBLEWORD` 会报 `PGPERR`，只有 F427/429/437/439 支持，本项目只能用 `FLASH_TYPEPROGRAM_WORD`（32 位）

**program 的实现细节**：
```c
if (size % 4 != 0) return false;               // 4 字节对齐校验
for (i = 0; i < size; i += 4) {
    memcpy(&word, data + i, 4);
    HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, address + i, word);
    if (*(volatile uint32_t *)(address + i) != word) { ok = false; break; }  // 写后回读
}
```
> 写后回读：`volatile` 强制每次真读内存（不被优化），逐字确认数据真实落盘，任何一字失败立即 break。

**erase 的实现细节**：遍历 12 个扇区，凡与 `[address, address+size)` 有交集的扇区逐一擦除，返回是否有任一失败。

---

## 6. Magic Header（magic_header.c）——固件元数据

**结构体 256 字节**（与上位机 `generate_magic_header` 严格对应）：
```
offset 0   magic        = 0x4D414749 "MAGI"
offset 4   bitmask
offset 8   reserved1[6] (24B)
offset 32  data_type    = 0 (MAGIC_HEADER_TYPE_APP)
offset 36  data_offset
offset 40  data_address = 0x08010000 (A) / 0x08081000 (B)
offset 44  data_length
offset 48  data_crc32
offset 96  version[128]
offset 248 this_address = header 自身地址
offset 252 this_crc32   = CRC32(字节 0..251)
```

**双重 CRC 设计**：
1. `this_crc32` 校验 **Header 自身**完整性（`magic_header_validate` 里算）
2. `data_crc32` 校验 **固件**完整性（`slot_validate` 里算）

**validate 三条件**：`this_address == 参数地址` && `magic == 0x4D414749` && `头 CRC 匹配`。

---

## 7. A/B 回滚状态机（boot_state + bootloader_main）——项目灵魂

**boot_state_t（20 字节）持久化在 sector 2（0x08008000）**：
```c
magic(0x424F4F54 "BOOT") | active_slot | pending_slot | boot_attempts | crc32
```

**boot_state 的读写**：
- `read`：先 `validate`（magic + CRC32），失败回默认 `{magic, A, NONE, 0, 0}`
- `write`：**强制写 magic → 擦除整个 16KB 扇区 → 编程 20 字节**（每次写都整扇区擦！）

**启动决策链**（`bootloader_main`，顺序不能错）：
```
1. 读 boot_state
2. 若 IWDGRST(看门狗复位):  attempts<3 ? attempts++ : {active翻转, pending=NONE, attempts=0}
3. 若正常复位(上电/软复位/外部复位): attempts != 0 则清零
4. 若 pending != NONE: 乐观提交 active=pending, pending=NONE, attempts=0
5. select_boot_slot: pending 优先 → 否则 active
6. 校验跳转；失败回退另一槽；两槽都失败 → 留在 Bootloader
```

**"乐观提交"为什么是对的（深挖必考）**：
- 升级流程：上位机 `SWITCH_SLOT(B)` 只写 `pending=B`，并不马上切——这样万一还没复位就断电，当前 active 槽不受影响
- 下次启动时才"乐观提交" `active=B`，此时 B 已被完整烧录+校验过
- 若 B 连续 3 次看门狗崩溃，`active` 翻回 A——**因为乐观提交已经把 active 更新成了 B，翻转方向才正确指向旧槽 A**

**回滚场景完整推演**（能当场画）：
```
烧 B → SWITCH(pending=B) → RESET → 启动提交 active=B → B 崩溃(WDT) → attempts=1
→ 重启 → 崩溃 → attempts=2 → 重启 → 崩溃 → attempts≥3 → active 翻回 A → 启动 A ✓
```

**两个诚实的局限（体现思考深度）**：
1. **每次写 boot_state 都擦整个 16KB 扇区**：Flash 擦写寿命典型 ~10k 次，频繁更新 boot_attempts 会磨损 sector 2。更优方案是磨损均衡或仅在状态变化时写。
2. **写中断不安全**：`boot_state_write` 擦完扇区后若被断电/看门狗打断，区域处于"已擦未写"态，下次读 `validate` 失败 → 回默认（active=A），状态丢失但**不会变砖**——因为没有"双副本 + 事务提交"机制。

---

## 8. 看门狗（IWDG）——计算与"无法关闭"特性

```c
LL_RCC_LSI_Enable();                                  // LSI ~32kHz
LL_IWDG_Enable(IWDG);                                 // 写 0xCCCC 启动
LL_IWDG_EnableWriteAccess(IWDG);                      // 写 0x5555 解锁
LL_IWDG_SetPrescaler(IWDG, LL_IWDG_PRESCALER_256);    // 32k/256 = 125Hz
LL_IWDG_SetReloadCounter(IWDG, 400);                  // 400/125 = 3.2s
LL_IWDG_ReloadCounter(IWDG);                          // 写 0xAAAA 喂狗
```

**两个硬件特性**：
1. **IWDG 一旦使能无法关闭**（软件无法写 0xCCCC 关掉），LSI 被强制保持开——所以跳转前必须 reload，应用必须接管喂狗
2. 独立于 CPU 时钟（用 LSI），主时钟停了照样跑

**3.2s 的完整推导**：
> F407 128KB 扇区擦除 max 2.6s → 擦除期间 TIM6 喂狗中断停摆 → 若设 2s 会在擦除中途误复位 → 3.2s = 2.6s + 0.6s 余量。代价：应用崩溃后的回滚检测慢约 1.2s。

---

## 9. 跳转到 APP（jumpapp.s + boot_slot）——汇编必考

**JumpApp 只有两条指令**：
```asm
JumpApp:
    LDR SP, [R0, #0]   ; R0=app 地址，取向量表第 0 字 = 初始 MSP
    LDR PC, [R0, #4]   ; 取向量表第 1 字 = Reset_Handler，跳转
```
> 本质：把应用地址当作**向量表**，手动加载它的初始栈指针和复位入口。配合 `SCB->VTOR = app_address`，应用的中断向量才能正确指向自己的向量表。

**boot_slot 跳转前的完整清理清单（能背）**：
```c
LL_IWDG_ReloadCounter(IWDG);              // 1. 给应用留满 3.2s 窗口
LL_TIM_DisableCounter(TIM6);              // 2. 停 TIM6（否则应用没初始化时中断乱跳）
LL_TIM_DisableIT_UPDATE(TIM6);
LL_USART_Disable(USART1); LL_USART_Disable(USART3);   // 3. 停串口
LL_USART_DisableDMAReq_RX(USART3); LL_DMA_DisableStream(DMA1, LL_DMA_STREAM_1);
NVIC_DisableIRQ(TIM6_DAC_IRQn / USART1_IRQn / USART3_IRQn); // 4. 关中断
SCB->VTOR = app_address;                  // 5. 重定位向量表
JumpApp(app_address);                     // 6. 跳转
```

**能主动指出的 3 个局限（高级加分）**：
1. 没设置 `CONTROL` 寄存器（未退出特权线程模式，APP 若用 RTOS 需自行处理）
2. 没关 SysTick（本工程没用 SysTick，所以无影响）
3. 没复位全部外设时钟（只停了用到的几个），严格做法应 `__HAL_RCC_..._FORCE_RESET`
4. 未 `__set_MSP`（由 JumpApp 的 LDR SP 替代了，效果等价）

---

## 10. 上位机烧录流程（flasher.py）——8 步 + 两个杀手锏

**完整流程**：
```
1. 读文件(.bin/.xbin) → 解析或自动生成 magic header
2. 4 字节对齐(不足补 0xFF) + 重算 CRC32 + 槽位大小边界检查
3. 查询版本/MTU → actual_chunk = min(4096, MTU-8)
4. [去重] VERIFY(addr, len, crc) 探测 → 命中则跳过 5/6/7
5. ERASE: 合并 header+APP 区域，一次擦除
6. PROGRAM: 先 Header，再固件(分块流水线)
7. VERIFY: 固件 CRC32
8. SWITCH_SLOT + RESET（或 --no-switch 时 BOOT）
```

**杀手锏 1：流水线 `program_stream`**：
```
发送 chunk0 → [不等待] 发送 chunk1 → 收 chunk0 的 ACK → 发 chunk2 → 收 chunk1 的 ACK ...
```
> 本质：**同一时刻 UART TX 在发下一块，MCU 在编上一块 Flash**，串口传输与 Flash 编程重叠，吞吐接近极限。前提是 MCU 用 DMA 收（CPU 停摆期间 DMA 照常收数）。

**杀手锏 2：断点续传 `.resume.json`**：
```json
{ "firmware_sha256": "...", "stage": "erased|header_done|firmware",
  "offset": 0, "data_address": 0x08010000, "data_length": 0x10000 }
```
- 关键约束：**只在收到 ACK 之后才落盘**（`_save_checkpoint` 注释明确）
- 恢复时用 `sha256` 校验固件是否还是同一份，防止续传错文件
- `erased` 阶段续传绝不再擦除；`header_done` 跳过 Header；`firmware` 从 offset 续发

**去重 dedup**：见上一步 4，CRC 一致跳过擦/写/验，`--force` 关闭。

---

## 11. 构建系统（Makefile + 链接脚本）——嵌入式通用考点

**编译选项**：
```
-Og（debug 优化）  -Wall
-fdata-sections -ffunction-sections   ← 每个函数/数据独立段
-Wl,--gc-sections                     ← 链接时丢弃未用段（配合删死函数瘦身）
-specs=nano.specs                     ← newlib-nano 减小 C 库体积
-lc -lm -lnosys                       ← nosys 提供空系统调用桩
-DUSE_HAL_DRIVER -DUSE_FULL_LL_DRIVER -DSTM32F407xx -DHSE_VALUE=8000000U
```

**链接脚本内存**：
```
FLASH  0x08000000  1024K   (rx)
RAM    0x20000000  128K    (xrw)
CCMRAM 0x10000000  64K     (xrw, 仅 CPU 访问)
```

**能讲的知识点**：
- `.isr_vector` 放 Flash 最前（`ENTRY(Reset_Handler)`）
- `_sidata = LOADADDR(.data)`：`.data` 在 Flash 存初值、启动后拷贝到 RAM
- `_Min_Stack_Size=0x400`、`_Min_Heap_Size=0x200`，不够会在链接期报错
- `/DISCARD/` 丢弃 libc/libm/libgcc（nano 已够用，进一步瘦身）

---

## 12. 30 个深挖问答（按被问概率排序）

**必考区（A 级）**

**Q1 为什么用 A/B 双槽而不是单槽？** 单槽一旦升级中断/固件坏了就直接变砖；A/B 保证始终有一个可启动的旧版本，升级失败靠看门狗自动回滚，全程无需人工干预。

**Q2 回滚是怎么触发的？** 应用启动后必须在 3.2s 内接管 IWDG 喂狗；若新固件一跑就崩/卡死，IWDG 溢出 → IWDGRST 复位 → bootloader 检测到复位标志 → boot_attempts++ → 连续 3 次就把 active 翻回旧槽。

**Q3 为什么"连续 3 次"才回滚？** 防止单次偶发干扰（上电毛刺、意外复位）误判为固件故障；3 次连续崩溃才认定新固件确实起不来。

**Q4 正常复位为什么要清零 boot_attempts？** 如果用户手动复位/上电，说明设备在人工干预，不应把历史崩溃计数叠加到下一次判定上。

**Q5 `flash_range_check` 防的是什么攻击？** 防止上位机传 `address=0xFFFFFFFC, size=0x100` 这类参数，`address+size` 用 uint32 相加会溢出绕回 0，从而绕过"禁止写 Bootloader"的区间判断。用 `size <= FLASH_END - address` 的无符号减法，溢出时右值变小必失败。

**Q6 为什么 PROGRAM 要求 4 字节对齐？** Flash 是 32 位写，`HAL_FLASH_Program(WORD)` 一次写 4 字节；非对齐会导致地址错误（PGERR）。上层 flasher 用 `_align4` 补 0xFF 保证。

**高频区（B 级）**

**Q7 DMA 环形缓冲的 NDTR 是怎么算写指针的？** `pos = BUF_SIZE - NDTR`；NDTR 是剩余待搬字节数，DMA 每搬一字节递减，所以 `BUF_SIZE - NDTR` 就是已写到的位置。

**Q8 DMA 环形模式回绕怎么处理？** 当 `pos <= last` 说明写指针绕回开头，把 `[last, 8K)` 和 `[0, pos)` 两段分别搬进上层 RingBuffer。

**Q9 为什么串口不用中断而用轮询？** Flash 擦写时 CPU 取指阻塞，中断根本进不去；而 DMA 是硬件搬运不受影响。轮询 NDTR 恰好无需中断即可取数。

**Q10 波特率为什么能到 2Mbps？** USART3 挂在 APB1（42MHz），波特率分频精度足够支持 2M；且用了 DMA 接收 + 流水线，不会因为 CPU 处理不过来而丢字节。

**Q11 ORE 溢出错误是什么？** Overrun Error，接收数据寄存器未被及时读取导致覆盖；DMA 模式下瞬时超载也会置位，不清除会导致后续接收异常。中断里优先清 ORE 再处理 RXNE。

**Q12 CRC16 为什么选 XMODEM 而不是 CCITT？** XMODEM（poly 0x1021, init 0x0000）是嵌入式串口协议的常见约定，与上位机统一即可；两者差异只在 Init（CCITT 是 0xFFFF）和反射。

**Q13 `boot_state_write` 为什么每次整扇区擦？** Flash 只能 1→0，写之前必须擦；擦除最小单位是扇区（16KB），无法只擦 20 字节。

**Q14 Magic Header 的 this_crc32 校验范围为什么到 252 字节？** `this_crc32` 字段本身在 offset 252，计算它时只能覆盖它之前的字节（0..251），否则循环依赖。

**Q15 断点续传怎么保证不续错文件？** `.resume.json` 存 `firmware_sha256`，恢复时重算当前文件的 sha256，不一致就放弃续传从头开始。

**进阶区（C 级，答出即显功力）**

**Q16 `tim_now()` 为什么要 do-while 重读？** 见 §2，解决 32 位读计数与 16 位读 CNT 之间被 1ms 溢出中断打断的"撕裂读"问题，是 seqlock 思路。

**Q17 IWDG 使能后为什么关不掉？** 独立看门狗用 LSI 独立时钟，`LL_IWDG_Enable` 写 0xCCCC 后硬件锁死、软件无法关闭（复位前一直运行），这是硬件防篡改设计。

**Q18 跳转 APP 为什么必须重设 VTOR？** 默认 VTOR=0（Bootloader 自己的向量表）；应用的中断向量在其自己的基地址，不重设 VTOR 则应用的中断会跳到 Bootloader 的向量，完全错乱。

**Q19 JumpApp 为什么不直接用函数指针跳？** 直接跳只改了 PC，没加载应用的初始 SP；且要避免编译器把跳转优化成 BL（返回地址入栈）。用汇编精确控制 SP 和 PC。

**Q20 为什么不支持双字编程？** F407 的 Flash 控制器不支持 64 位并行编程（x64 是 F427/429/437/439 专属），用 DOUBLEWORD 会报 PGPERR。

**Q21 DMA 缓冲为什么不能放 CCMRAM？** CCMRAM（0x10000000）是 CPU 紧耦合内存，DMA 总线访问不到；DMA 目标必须放 0x20000000 的普通 SRAM。

**Q22 `bitops.h` 的 `*(uint32_t*)ptr` 为什么不担心非对齐？** Cortex-M4 硬件支持 32 位及以下的非对齐访问（LDR/STR 对非对齐地址是合法的），所以可直接强转读取；这是 ARM 架构特性，x86 也可，但某些 MCU（如部分 RISC-V 默认）会异常。

**Q23 流水线发送如果中途 ACK 丢失怎么办？** `program_stream` 里 `recv_response` 返回 None 时抛 `ConnectionError`，立即终止并把已 ACK 的进度写进检查点，用户 `--resume` 重跑续传。

**Q24 为什么进入烧录服务循环后把日志降到 WARN？** 日志走 USART1 是**同步阻塞**发送，每条日志占几百 us~ms；烧录要实时回 ACK，日志太频繁会拖慢帧应答甚至导致上位机超时，所以降级。

**Q25 Bootloader 自己怎么被烧进去的？** 本项目 Bootloader 本体（0x08000000）保护不可自写，首次需用 ST-Link/J-Link 或系统 Bootloader(串口 ISP) 写入；之后 OTA 只更新 A/B 应用区。

**Q26 如果两个槽的固件都坏了怎么办？** `select_boot_slot` 与 fallback 都校验失败 → 留在 Bootloader 服务循环，点灯等待上位机重新烧录（保证设备可恢复，不砖）。

**Q27 BOOT 命令为什么"先回 ACK 再跳转"？** 若先跳转再回 ACK，上位机会因串口已切到应用而收不到响应误报超时；先 ACK 保证上位机确认指令已收到，再跳转。

**Q28 擦除和编程中间断电会发生什么？** 该扇区处于"已擦除"状态（全 0xFF），magic header 失效 → `slot_validate` 失败 → 该槽不可启动，但另一槽仍正常，bootloader 会回退。

**Q29 MTU 为什么是 4104？** `PAYLOAD_SIZE_MAX = 4096 + 8`，其中 4096 是单块固件数据，8 字节是 PROGRAM 的 address(4)+size(4) 头。单帧总长 = 4(头) + 4104 + 2(CRC) = 4110 字节。

**Q30 固件去重为什么用 VERIFY 探测而不是读回整个固件？** VERIFY 只需 MCU 算一次 CRC32 返回单字节结果，O(1) 通信开销；读回整固件要传几百 KB，浪费带宽。

---

## 13. 面试官可能"挖坑"的 8 个点（诚实回应）

| 可能的追问 | 诚实且加分的话术 |
|-----------|-----------------|
| "boot_state 每次写都擦扇区，Flash 寿命怎么办？" | 承认这是当前实现的取舍，理想方案是磨损均衡/事务双副本，当前量级（10k 次擦写）够用 |
| "写 boot_state 到一半断电呢？" | 会回退到默认状态（active=A），不会变砖，但没有事务保护，是后续可优化点 |
| "IWDG 关不掉，那 Debug 时怎么办？" | 可以不在 debug 阶段使能 IWDG，或用 DHCSR 位冻结看门狗（硬件调试冻结） |
| "擦除 2.6s 期间如果来了串口数据？" | 靠 DMA 硬件缓冲，CPU 恢复后主循环冲刷，不会丢（前提 8KB 缓冲够装） |
| "2Mbps 下 8KB DMA 缓冲够吗？" | 2Mbps = 250KB/s，8KB 能缓冲 32ms 数据；单块编程远小于这个时间，够用 |
| "为什么 USART3 做烧录，USART1 做日志？" | 隔离数据面和控制面，日志不影响烧录帧时序 |
| "协议为什么自定义不用 YMODEM？" | 自定义协议轻量、可控、能扩展 SWITCH_SLOT 等命令，YMODEM 只做文件传输 |
| "A 槽 448KB、B 槽 508KB 为什么不对称？" | F407 扇区物理布局决定的——B 槽 Header 挤在 sector 8 起始 4KB，剩余空间给 B App |

---

## 14. 一页纸速查（面试前最后瞄一眼）

```
时钟: HSE8M → /4 ×168 /2 = 168MHz；APB1=42M(定时器×2=84M)；APB2=84M
分区: BL@0x08000000(32K) | State@0x08008000(16K) | A_Hdr@0x0800C000 | A_App@0x08010000(448K) | B_Hdr@0x08080000 | B_App@0x08081000(508K)
扇区: 16K×4 + 64K + 128K×7 = 1MB
协议: 0xAA/0x55 + op + len(LE) + payload + CRC16(XMODEM) ；CRC32=zlib(IEEE 802.3)
IWDG: LSI 32k / 256 = 125Hz × 400 = 3.2s（覆盖 128K 扇区擦除 2.6s）
回滚: attempts≥3 翻转 active；正常复位清零；pending 乐观提交
跳转: SCB->VTOR=app；JumpApp: LDR SP,[R0]; LDR PC,[R0,#4]
编程: 只能1→0、先擦后写、WORD(32位)、写后回读、F407 无 x64
去重: VERIFY 探测 CRC 一致 → 跳擦写验；--force 关闭
续传: .resume.json{sha256,stage,offset,addr,len}，ACK 后才落盘
```

---

## 15. 每日复习清单（打印勾选）

- [ ] 时钟树能口算（168/42/84、Flash 5WS、Scale1）
- [ ] tim_now 的 seqlock 双重读原理
- [ ] NDTR 反推写指针 + 回绕两段搬运
- [ ] ORE 溢出错误的处理
- [ ] 协议帧小端 + CRC16/CRC32 参数
- [ ] F407 扇区表 + 三硬件特性（1→0/扇区擦/无x64）
- [ ] boot_state 20 字节结构 + 决策链顺序
- [ ] 乐观提交 + 回滚方向推导
- [ ] IWDG 3.2s 推导 + 无法关闭特性
- [ ] JumpApp 两指令 + VTOR + 清理清单 + 3 局限
- [ ] 流水线/去重/断点续传三件套
- [ ] 30 问答里 A/B 级各能脱口而出
- [ ] 8 个"挖坑点"的诚实话术

---

> 打印建议：§12（问答）+ §14（一页纸）重点看，其余按章节逐天过一遍。把**加粗的结论句**背熟，配合"我可以现场打开 `xxx.c:行号`"的翻代码动作，基本可以应对任何深挖。