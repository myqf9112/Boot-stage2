#include "stm32f4xx_ll_tim.h"
#include "stm32f4xx_ll_iwdg.h"
#include "stm32f4xx_ll_rcc.h"
#include "stm32f4xx_ll_usart.h"
#include "stm32f4xx_ll_dma.h"
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "bl_usart.h"
#include "ringbuffer.h"
#include "crc16.h"
#include "crc32.h"
#include "tim_delay.h"
#include "stm32flash.h"
#include "borad.h"
#include "key.h"
#include "key_desc.h"
#include "led.h"
#include "led_desc.h"
#include "magic_header.h"
#include "utils.h"
#include "boot_state.h"
#define LOG_TAG "boot"
#define LOG_LVL ELOG_LVL_INFO
#include "elog.h"

#define PACKET_SIZE_MAX (4 + PAYLOAD_SIZE_MAX + 2) // header(1) + opcode(1) + length(2) + payload + crc16(2)
#define RX_BUFFER_SIZE (8 * 1024)
#define RX_TIMEOUT_MS 20
#define BL_VERSION "1.0.0"
#define PAYLOAD_SIZE_MAX (4096 + 8) // 4096 program data + 8 bytes for address and size
#define BOOT_DELAY 3000             //  boot delay (上位机连接窗口)
#define KEY_HOLD_TRAP_MS 1500       // 按键进入 bootloader 需连续按住的时间(ms)

/*
Bootloader : 0x08000000  32KB  (sector 0~1)
Boot State : 0x08008000  16KB  (sector 2)
A Header   : 0x0800C000  16KB  (sector 3)
A App      : 0x08010000  448KB (sector 4~7)
B Header   : 0x08080000  4KB   (sector 8 起始)
B App      : 0x08081000  508KB (sector 8 尾~11)
*/
#define APP_BASE_ADDRESS 0x08010000
#define BL_ADDRESS 0x08000000
#define BL_SIZE (32 * 1024)              // 32KB bootloader size
#define A_MAGICHEADER_ADDRESS 0x0800C000 // A槽magic header存储地址
#define B_MAGICHEADER_ADDRESS 0x08080000 // B槽magic header存储地址
#define B_APP_ADDRESS 0x08081000         // B槽应用程序存储地址

typedef enum
{
    PACKET_STATE_HEADER,
    PACKET_STATE_OPCODE,
    PACKET_STATE_LENGTH,
    PACKET_STATE_PAYLOAD,
    PACKET_STATE_CRC16,
} packet_state_machine_t;

typedef enum
{
    PACKET_OPCODE_INQUERY = 0x01,
    PACKET_OPCODE_ERASE = 0x81,
    PACKET_OPCODE_PROGRAM = 0x82,
    PACKET_OPCODE_VERIFY = 0x33,
    PACKET_OPCODE_BOOT = 0x22,
    PACKET_OPCODE_RESET = 0x23,
    PACKET_OPCODE_SWITCH_SLOT = 0x24,
} packet_opcode_t;

typedef enum
{
    INQUERY_SUBCODE_VERSION = 0x00,
    INQUERY_SUBCODE_MTU = 0x01,
    INQUERY_SUBCODE_SLOT_STATUS = 0x02,
} packet_inquery_subcode_t;

typedef enum
{
    RESPONSE_ERRORCODE_OK = 0,
    RESPONSE_ERRORCODE_OPCODE,
    RESPONSE_ERRORCODE_OVERFLOW,
    RESPONSE_ERRORCODE_TIMEOUT,
    RESPONSE_ERRORCODE_FORMAT,
    RESPONSE_ERRORCODE_VERIFY,
    RESPONSE_ERRORCODE_PARAM,
    RESPONSE_ERRORCODE_FLASH,
    RESPONSE_ERRORCODE_UNKNOWN = 0xff,
} packet_errcode_t;

static uint8_t rb_buffer[RX_BUFFER_SIZE];
static rb_t rxrb;
static uint8_t packet_buffer[PACKET_SIZE_MAX];
static uint32_t packet_index;
static packet_opcode_t packet_opcode;
static uint16_t packet_payload_length;
static packet_state_machine_t packet_state = PACKET_STATE_HEADER;

static uint32_t slot_header_address(boot_slot_t slot)
{
    switch (slot)
    {
    case BOOT_SLOT_A:
        return A_MAGICHEADER_ADDRESS;
    case BOOT_SLOT_B:
        return B_MAGICHEADER_ADDRESS;
    default:
        return 0;
    }
}

static bool slot_validate(boot_slot_t slot)
{
    uint32_t header_address = slot_header_address(slot);
    if (header_address == 0)
    {
        log_e("Invalid slot %d", slot);
        return false;
    }

    if (!magic_header_validate(header_address))
    {
        log_w("Slot %d magic header invalid", slot);
        return false;
    }

    // 获取固件地址、大小和存储的 CRC
    uint32_t addr = magic_header_get_address(header_address);
    uint32_t size = magic_header_get_length(header_address);
    uint32_t stored_crc = magic_header_get_crc32(header_address);
    uint32_t calc_crc = crc32((const uint8_t *)addr, size);

    if (stored_crc != calc_crc)
    {
        log_w("Slot %d CRC32 mismatch: expected 0x%08X, got 0x%08X", slot, stored_crc, calc_crc);
        return false;
    }

    log_i("Slot %d validated OK", slot);
    return true;
}

static void boot_slot(boot_slot_t slot)
{
    uint32_t header_address = slot_header_address(slot);
    if (header_address == 0)
        return;
    uint32_t app_address = magic_header_get_address(header_address);
    log_i("Booting slot %d, header at 0x%08X, app at 0x%08X", slot, header_address, app_address);
    log_i("Booting  application...");
    tim_delay_ms(2);
    led_off(led1);

    LL_TIM_DisableCounter(TIM6);
    LL_TIM_DisableIT_UPDATE(TIM6);

    LL_USART_Disable(USART1);

    LL_USART_Disable(USART3);

    LL_USART_DisableDMAReq_RX(USART3);
    LL_DMA_DisableStream(DMA1, LL_DMA_STREAM_1);

    NVIC_DisableIRQ(TIM6_DAC_IRQn);
    NVIC_DisableIRQ(USART1_IRQn);
    NVIC_DisableIRQ(USART3_IRQn);
    SCB->VTOR = app_address;
    extern void JumpApp(uint32_t base);
    JumpApp(app_address);
}
static bool application_validate(void)
{
    if (!magic_header_validate(A_MAGICHEADER_ADDRESS))
    {
        log_e("Magic header invalid");
        return false;
    }

    uint32_t addr = magic_header_get_address(A_MAGICHEADER_ADDRESS);
    uint32_t size = magic_header_get_length(A_MAGICHEADER_ADDRESS);
    uint32_t crc = magic_header_get_crc32(A_MAGICHEADER_ADDRESS);
    uint32_t ccrc = crc32((const uint8_t *)addr, size);
    if (crc != ccrc)
    {
        log_w("Application CRC32 mismatch: expected %08X, got %08X", crc, ccrc);
        return false;
    }

    log_i("Application validated OK");
    return true;
}
static void boot_application(void)
{
    if (!application_validate())
    {
        log_e("Application validate failed,catnot boot");
        return;
    }

    SCB->VTOR = APP_BASE_ADDRESS;
    extern void JumpApp(uint32_t base);
    JumpApp(APP_BASE_ADDRESS);
}
static void bl_response(packet_opcode_t opcode, packet_errcode_t errcode,
                        const uint8_t *data, uint16_t length)
{
    uint8_t *response = packet_buffer;
    uint8_t *prsp = response;

    put_u8_inc(&prsp, 0x55);
    put_u8_inc(&prsp, (uint8_t)opcode);
    put_u8_inc(&prsp, (uint8_t)errcode);
    put_u16_inc(&prsp, length);
    put_bytes_inc(&prsp, data, length);
    uint16_t crc = crc16(response, prsp - response);
    put_u16_inc(&prsp, crc);
    bl_usart_write(response, prsp - response);
}
static void bl_opcode_inquery_handler(void)
{
    log_i("INQUERY handler");
    if (packet_payload_length != 1)
    {
        log_w("INQUERY should have no payload, but got %u bytes", packet_payload_length);
        return;
    }
    uint8_t subcode = packet_buffer[4];
    switch (subcode)
    {
    case INQUERY_SUBCODE_VERSION:
        bl_response(PACKET_OPCODE_INQUERY, RESPONSE_ERRORCODE_OK, (const uint8_t *)BL_VERSION, strlen(BL_VERSION));
        break;
    case INQUERY_SUBCODE_MTU:
    {
        // uint8_t bmtu[2] = {PAYLOAD_SIZE_MAX & 0xFF, (PAYLOAD_SIZE_MAX >> 8) & 0xFF};
        uint8_t bmtu[2];
        put_u16(bmtu, PAYLOAD_SIZE_MAX);
        bl_response(PACKET_OPCODE_INQUERY, RESPONSE_ERRORCODE_OK, (const uint8_t *)bmtu, sizeof(bmtu));
        break;
    }
    case INQUERY_SUBCODE_SLOT_STATUS:
    {
        boot_state_t st = boot_state_read();
        uint8_t resp[4];
        resp[0] = st.active_slot;                                                              // 当前启动槽
        resp[1] = st.pending_slot;                                                             // 待切换槽
        resp[2] = st.boot_attempts;                                                            // 当前槽连续看门狗复位次数
        resp[3] = (slot_validate(BOOT_SLOT_A) ? 1 : 0) | (slot_validate(BOOT_SLOT_B) ? 2 : 0); // 位掩码：bit0=A有效, bit1=B有效
        bl_response(PACKET_OPCODE_INQUERY, RESPONSE_ERRORCODE_OK, (const uint8_t *)resp, sizeof(resp));
        break;
    }
    default:
        log_w("Unknown INQUERY subcode: %02X", subcode);
        break;
    }
}

static void bl_opcode_reset_handler(void)
{
    log_i("reset handler...");
    bl_response(PACKET_OPCODE_RESET, RESPONSE_ERRORCODE_OK, NULL, 0);
    log_i("System resetting...");
    tim_delay_ms(2);
    NVIC_SystemReset();
}

static void bl_opcode_boot_handler(void)
{
    log_i("boot handler...");
    bl_response(PACKET_OPCODE_BOOT, RESPONSE_ERRORCODE_OK, NULL, 0);
}

static void bl_opcode_erase_handler(void)
{
    log_i("erase handler");
    uint32_t address = 0, size = 0;

    if (packet_payload_length != 8)
    {
        log_w("ERASE should have 8 bytes payload, but got %u bytes", packet_payload_length);
        bl_response(PACKET_OPCODE_ERASE, RESPONSE_ERRORCODE_FORMAT, NULL, 0);
        return;
    }
    // address = (packet_buffer[7] << 24) | (packet_buffer[6] << 16) | (packet_buffer[5] << 8) | packet_buffer[4];
    address = get_u32(&packet_buffer[4]);
    // size = (packet_buffer[11] << 24) | (packet_buffer[10] << 16) | (packet_buffer[9] << 8) | packet_buffer[8];
    size = get_u32(&packet_buffer[8]);
    uint32_t end_address = address + size;
    if ((address >= BL_ADDRESS && address < BL_ADDRESS + BL_SIZE) ||
        (address >= BOOT_STATE_ADDRESS && address < BOOT_STATE_ADDRESS + BOOT_STATE_SIZE) ||
        (end_address > BL_ADDRESS && end_address <= BL_ADDRESS + BL_SIZE) ||
        (end_address > BOOT_STATE_ADDRESS && end_address <= BOOT_STATE_ADDRESS + BOOT_STATE_SIZE))
    {
        log_w("address %08X is protected", address);
        bl_response(PACKET_OPCODE_ERASE, RESPONSE_ERRORCODE_PARAM, NULL, 0);
        return;
    }
    log_i("Erase request: address=0x%08X, size=%u", address, size);

    stm32_flash_unlock();
    bool erase_ok = stm32_flash_erase(address, size);
    stm32_flash_lock();
    bl_response(PACKET_OPCODE_ERASE,
                erase_ok ? RESPONSE_ERRORCODE_OK : RESPONSE_ERRORCODE_FLASH,
                NULL, 0);
}
static void bl_opcode_program_handler(void)
{
    log_i("program handler");
    uint32_t address = 0, size = 0;
    if (packet_payload_length < 8)
    {
        log_w("PROGRAM should have at least 8 bytes payload, but got %u bytes", packet_payload_length);
        bl_response(PACKET_OPCODE_PROGRAM, RESPONSE_ERRORCODE_FORMAT, NULL, 0);
        return;
    }

    // address = (packet_buffer[7] << 24) | (packet_buffer[6] << 16) | (packet_buffer[5] << 8) | packet_buffer[4];
    address = get_u32(&packet_buffer[4]);
    // size = (packet_buffer[11] << 24) | (packet_buffer[10] << 16) | (packet_buffer[9] << 8) | packet_buffer[8];
    size = get_u32(&packet_buffer[8]);
    uint8_t *data = &packet_buffer[12];
    uint32_t end_address = address + size;
    if ((address >= BL_ADDRESS && address < BL_ADDRESS + BL_SIZE) ||
        (address >= BOOT_STATE_ADDRESS && address < BOOT_STATE_ADDRESS + BOOT_STATE_SIZE) ||
        (end_address > BL_ADDRESS && end_address <= BL_ADDRESS + BL_SIZE) ||
        (end_address > BOOT_STATE_ADDRESS && end_address <= BOOT_STATE_ADDRESS + BOOT_STATE_SIZE))
    {
        log_w("address %08X is protected", address);
        bl_response(PACKET_OPCODE_PROGRAM, RESPONSE_ERRORCODE_PARAM, NULL, 0);
        return;
    }
    if (size != packet_payload_length - 8)
    {
        log_w("PROGRAM size mismatch: expected %u, got %u", size, packet_payload_length - 8);
        bl_response(PACKET_OPCODE_PROGRAM, RESPONSE_ERRORCODE_FORMAT, NULL, 0);
        return;
    }
    log_i("Program request: address=0x%08X, size=%u", address, size);
    stm32_flash_unlock();
    bool program_ok = stm32_flash_program(address, data, size);
    stm32_flash_lock();
    bl_response(PACKET_OPCODE_PROGRAM,
                program_ok ? RESPONSE_ERRORCODE_OK : RESPONSE_ERRORCODE_FLASH,
                NULL, 0);
}

static void bl_opcode_verify_handler(void)
{
    log_i("verify handler");
    uint32_t address = 0, size = 0;
    if (packet_payload_length != 12)
    {
        log_w("VERIFY should have 12 bytes payload, but got %u bytes", packet_payload_length);
        bl_response(PACKET_OPCODE_VERIFY, RESPONSE_ERRORCODE_PARAM, NULL, 0);
        return;
    }
    address = get_u32(&packet_buffer[4]);
    // address = (packet_buffer[7] << 24) | (packet_buffer[6] << 16) | (packet_buffer[5] << 8) | packet_buffer[4];
    // size = (packet_buffer[11] << 24) | (packet_buffer[10] << 16) | (packet_buffer[9] << 8) | packet_buffer[8];
    size = get_u32(&packet_buffer[8]);
    // uint32_t crc = (packet_buffer[15] << 24) | (packet_buffer[14] << 16) | (packet_buffer[13] << 8) | packet_buffer[12];
    uint32_t crc;
    crc = get_u32(&packet_buffer[12]);
    uint32_t end_address = address + size;
    if (address < STM32_FLASH_BASE || end_address > STM32_FLASH_BASE + STM32_FLASH_SIZE)
    {
        log_i("address %08X is protected", address);
        bl_response(PACKET_OPCODE_VERIFY, RESPONSE_ERRORCODE_PARAM, NULL, 0);
        return;
    }
    log_d("Verify request: address=0x%08X, size=%u, crc32=%08X", address, size, crc);
    uint32_t ccrc = crc32((const uint8_t *)address, size);
    if (ccrc != crc)
    {
        log_w("Verify failed: expected %08X, got %08X", crc, ccrc);
        bl_response(PACKET_OPCODE_VERIFY, RESPONSE_ERRORCODE_VERIFY, NULL, 0);
    }
    else
    {
        log_i("Verify OK");
        bl_response(PACKET_OPCODE_VERIFY, RESPONSE_ERRORCODE_OK, NULL, 0);
    }
}

static void bl_opcode_switch_slot_handler(void)
{
    log_i("switch slot handler");
    if (packet_payload_length != 1)
    {
        log_w("SWITCH_SLOT should have 1 byte payload, but got %u bytes", packet_payload_length);
        bl_response(PACKET_OPCODE_SWITCH_SLOT, RESPONSE_ERRORCODE_PARAM, NULL, 0);
        return;
    }
    boot_slot_t target = (boot_slot_t)packet_buffer[4];
    if (target > BOOT_SLOT_B)
    {
        log_w("Invalid slot: %d", target);
        bl_response(PACKET_OPCODE_SWITCH_SLOT, RESPONSE_ERRORCODE_PARAM, NULL, 0);
        return;
    }
    boot_slot_t slot = (boot_slot_t)target;
    if (!slot_validate(slot))
    {
        log_w("Slot %d is not valid, cannot switch", slot);
        uint8_t err = 2;
        bl_response(PACKET_OPCODE_SWITCH_SLOT, RESPONSE_ERRORCODE_PARAM, &err, 1);
        return;
    }
    boot_state_t state = boot_state_read();
    state.pending_slot = slot;
    state.crc32 = crc32((uint8_t *)&state, offset_of(boot_state_t, crc32));
    boot_state_write(&state);
    log_i("Switching to slot %d on next boot", slot);
    uint8_t resp = (uint8_t)slot;
    bl_response(PACKET_OPCODE_SWITCH_SLOT, RESPONSE_ERRORCODE_OK, &resp, 1);
}
static void bl_packet_handler(void)
{
    switch (packet_opcode)
    {
    case PACKET_OPCODE_INQUERY:
        bl_opcode_inquery_handler();
        log_d("Inquery received");
        break;

    case PACKET_OPCODE_ERASE:
        bl_opcode_erase_handler();
        log_d("Erase received");
        break;

    case PACKET_OPCODE_PROGRAM:
        bl_opcode_program_handler();
        log_d("Program received");
        break;

    case PACKET_OPCODE_VERIFY:
        bl_opcode_verify_handler();
        log_d("Verify received");
        break;
    case PACKET_OPCODE_BOOT:
        bl_opcode_boot_handler();
        log_d("Boot received");
        break;
    case PACKET_OPCODE_RESET:
        bl_opcode_reset_handler();
        log_d("Reset received");
        break;
    case PACKET_OPCODE_SWITCH_SLOT:
        bl_opcode_switch_slot_handler();
        log_d("Switch slot received");
        break;
    default:
        log_e("Unknown opcode received");
        break;
    }
}

static bool bl_byte_handler(uint8_t byte)
{
    bool full_packet = false;
    static uint64_t last_byte_ms;
    uint64_t now_ms = tim_get_ms();
    if (now_ms - last_byte_ms > RX_TIMEOUT_MS)
    {
        if (packet_state != PACKET_STATE_HEADER)
        {
            log_w("Packet timeout, reset state machine");
            packet_index = 0;
            packet_state = PACKET_STATE_HEADER;
        }
    }
    last_byte_ms = now_ms;
    log_v("Recv: %02X", byte);
    packet_buffer[packet_index++] = byte;
    switch (packet_state)
    {
    case PACKET_STATE_HEADER:
        if (packet_buffer[0] == 0xAA)
        {
            log_d("Header OK");
            packet_state = PACKET_STATE_OPCODE;
        }
        else
        {
            packet_index = 0;
            packet_state = PACKET_STATE_HEADER;
        }
        break;
    case PACKET_STATE_OPCODE:
        if (packet_buffer[1] == PACKET_OPCODE_INQUERY ||
            packet_buffer[1] == PACKET_OPCODE_ERASE ||
            packet_buffer[1] == PACKET_OPCODE_PROGRAM ||
            packet_buffer[1] == PACKET_OPCODE_VERIFY ||
            packet_buffer[1] == PACKET_OPCODE_BOOT ||
            packet_buffer[1] == PACKET_OPCODE_SWITCH_SLOT ||
            packet_buffer[1] == PACKET_OPCODE_RESET)
        {
            log_d("Opcode OK:%02X", packet_buffer[1]);
            packet_opcode = (packet_opcode_t)packet_buffer[1];
            packet_state = PACKET_STATE_LENGTH;
        }
        else
        {
            packet_index = 0;
            packet_state = PACKET_STATE_HEADER;
        }
        break;
    case PACKET_STATE_LENGTH:
        if (packet_index == 4)
        {
            // uint16_t payload_length = (packet_buffer[3] << 8) | packet_buffer[2];
            uint16_t payload_length = get_u16(&packet_buffer[2]);
            if (payload_length <= PAYLOAD_SIZE_MAX)
            {
                log_d("Length OK:%u", payload_length);
                packet_payload_length = payload_length;
                if (payload_length > 0)
                {
                    packet_state = PACKET_STATE_PAYLOAD;
                }
                else
                {
                    packet_state = PACKET_STATE_CRC16;
                }
            }
            else
            {
                packet_index = 0;
                packet_state = PACKET_STATE_HEADER;
            }
        }
        break;
    case PACKET_STATE_PAYLOAD:
        if (packet_index == 4 + packet_payload_length)
        {

            log_d("Payload Received OK");
            packet_state = PACKET_STATE_CRC16;
        }
        break;
    case PACKET_STATE_CRC16:
        if (packet_index == 4 + packet_payload_length + 2)
        {
            // uint16_t crc = (packet_buffer[4 + packet_payload_length + 1] << 8) |
            //              packet_buffer[4 + packet_payload_length];
            uint16_t crc = get_u16(&packet_buffer[4 + packet_payload_length]);
            uint16_t ccrc = crc16(packet_buffer, 4 + packet_payload_length);
            if (crc == ccrc)
            {
                full_packet = true;
                log_d("crc16 ok:%04X", ccrc);
                log_d("Packet complete: opcode=0x%2X, length=%u", packet_opcode, packet_payload_length);
                if (LOG_LVL >= ELOG_LVL_VERBOSE)
                    elog_hexdump("payload", 16, packet_buffer, 6 + packet_payload_length);
            }
            else
            {
                log_w("crc16 error: expected %04X, got %04X", crc, ccrc);
            }
            packet_index = 0;
            packet_state = PACKET_STATE_HEADER;
        }
        break;

    default:
        break;
    }
    return full_packet;
}

static void bl_usart_rx_handler(const uint8_t *data, uint32_t length)
{
    rb_puts(rxrb, data, length);
}

static void wait_key_release(void)
{
    while (key_read(key1))
        tim_delay_ms(10);
}

static bool key_press_check(void)
{
    if (!key_read(key1))
        return false;

    tim_delay_ms(10);
    if (!key_read(key1))
        return false;

    return true;
}

bool magic_header_trap_boot(void)
{

    if (!magic_header_validate(A_MAGICHEADER_ADDRESS))
    {
        log_e("Magic header invalid, skip trap");
        return true;
    }

    if (!application_validate())
    {
        log_e("Application invalid, trap into bootloader");
        return true;
    }

    return false;
}

static bool combined_trap_check(void)
{
    uint32_t held_ms = 0;
    for (uint32_t t = 0; t < BOOT_DELAY; t += 10)
    { // 3s 总窗口，10ms 采样
        tim_delay_ms(10);

        // 按键检测
        if (key_read(key1))
        {
            held_ms += 10;
            if (held_ms >= KEY_HOLD_TRAP_MS)
            {
                log_w("Key held %u ms, trap into boot", held_ms);
                return true;
            }
        }
        else
        {
            held_ms = 0;
        }

        //  UART数据检测
        bl_usart_flush_rx();
        if (!rb_empty(rxrb))
        {
            log_d("Data received, trap into boot");
            return true;
        }
    }
    return false;
}
/*
 *根据boot_state选择启动槽
 */
static boot_slot_t select_boot_slot(const boot_state_t *st)
{

    if (st->pending_slot != BOOT_PENDING_NONE)
    {
        return st->pending_slot;
    }

    if (st->active_slot != BOOT_PENDING_NONE)
    {
        return st->active_slot;
    }
    return BOOT_PENDING_NONE;
}

static bool try_boot_slot(boot_slot_t slot)
{
    // 固件有效性检查
    if (!(slot_validate(slot)))
    {
        log_w("Slot %d validation failed", slot);
        return false;
    }
    LL_IWDG_ReloadCounter(IWDG);
    log_i("Booting slot %d", slot);
    boot_slot(slot); // 跳转到应用程序，不返回
    return true;
}

static void iwdg_init(void)
{
    uint32_t timeout;

    LL_RCC_LSI_Enable();                              //  启用 LSI（IWDG 时钟源）
    timeout = 1000000;
    while (!LL_RCC_LSI_IsReady() && timeout-- > 0) {} //  等 LSI 就绪（约40us）
    if (timeout == 0)
        log_e("IWDG init: LSI not ready!");

    LL_IWDG_Enable(IWDG);                             //  启动 IWDG（写 0xCCCC），LSI 会被强制保持开
    LL_IWDG_EnableWriteAccess(IWDG);                  //  解锁
    LL_IWDG_SetPrescaler(IWDG, LL_IWDG_PRESCALER_256); // 分频 32kHz/256=125Hz
    LL_IWDG_SetReloadCounter(IWDG, 250);              //  重载值 250/125=2s

    timeout = 1000000;
    while (!LL_IWDG_IsReady(IWDG) && timeout-- > 0) {} // 等 PR/RLR 写入完成
    if (timeout == 0)
        log_e("IWDG init: PVU/RVU not cleared!");

    LL_IWDG_ReloadCounter(IWDG);                      //  刷新
    log_i("IWDG init OK");
}
static void feed_iwdg(void)
{
    LL_IWDG_ReloadCounter(IWDG);
}
void bootloader_main(void)
{
    log_i("Bootloader started.\r");
    tim_register_periodic_callback(feed_iwdg);
    iwdg_init();
    key_init(key1);
    rxrb = rb_new(rb_buffer, RX_BUFFER_SIZE);
    bl_usart_init();
    bl_usart_register_rx_callback(bl_usart_rx_handler);

    bool trapboot = false;

    if (!trapboot)
        trapboot = combined_trap_check();

    if (!trapboot)
    {
        boot_state_t st = boot_state_read();
        if (LL_RCC_IsActiveFlag_IWDGRST())
        {
            LL_RCC_ClearResetFlags();
            if (st.boot_attempts < 3)
            {
                st.boot_attempts++;
                log_w("IWDG reset detected, attempts=%u", st.boot_attempts);
            }
            else
            {
                st.active_slot = (st.active_slot == BOOT_SLOT_A) ? BOOT_SLOT_B : BOOT_SLOT_A;
                st.pending_slot = BOOT_PENDING_NONE;
                st.boot_attempts = 0;
                log_w("3rd IWDG reset, auto-switch to slot %d", st.active_slot);
            }
            st.crc32 = crc32((uint8_t *)&st, offset_of(boot_state_t, crc32));
            boot_state_write(&st);
        }
        boot_slot_t slot = select_boot_slot(&st);
        if (slot != BOOT_PENDING_NONE && try_boot_slot(slot))
        {
            // 不返回
        }
        boot_slot_t fallback = (slot == BOOT_SLOT_A) ? BOOT_SLOT_B : BOOT_SLOT_A;
        if (try_boot_slot(fallback))
        {
            // 不返回
        }
        log_i("No valid boot slot found, entering bootloader");
    }
    else
    {
        log_i("Trap into bootloader");
    }

    led_init(led1);
    led_on(led1);
    wait_key_release();

    /* 进入烧录服务循环后,日志级别降为 WARN,避免阻塞帧应答 */
    elog_set_filter_lvl(ELOG_LVL_WARN);

    while (1)
    {
        if (key_press_check())
        {
            log_i("key pressed,rebooting...");
            tim_delay_ms(2);
            NVIC_SystemReset();
        }
        bl_usart_flush_rx();
        if (!rb_empty(rxrb))
        {
            uint8_t byte;
            rb_get(rxrb, &byte);
            if (bl_byte_handler(byte))
            {
                bl_packet_handler();
            }
        }
    }
}
