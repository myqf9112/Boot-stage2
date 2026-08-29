#include "boot_state.h"
#include <stdio.h>
#include <string.h>
#include "crc32.h"
#include <stdbool.h>
#include "utils.h"
#include "stm32flash.h"
#include "elog.h"

#define LOG_TAG "boot_state"
#define LOG_LVL ELOG_LVL_INFO
bool boot_state_validate(const boot_state_t *state)
{
    if (!(state->magic == 0x424F4F54))
    {
        log_e("Invalid magic header");
        return false; // 魔术头不合法
    }
    uint32_t ccrc32 = crc32((uint8_t *)state, offset_of(boot_state_t, crc32)); // 计算boot_state的CRC32校验值
    if (ccrc32 != state->crc32)
    {
        log_e("CRC32 mismatch");
        return false; // boot_state不合法
    }
    return true; // boot_state合法
}
boot_state_t boot_state_read(void)
{
    static const boot_state_t default_state =
        {
            .magic = 0,
            .active_slot = BOOT_SLOT_A,
            .pending_slot = BOOT_PENDING_NONE,
            .boot_attempts = 0,
            .crc32 = 0,
        };
    boot_state_t *state = (boot_state_t *)BOOT_STATE_ADDRESS;
    if (boot_state_validate(state))
        return *state;
    return default_state;
}

void boot_state_write(const boot_state_t *state)
{
    if (state == NULL)
        return;
    boot_state_t local_state = *state;
    local_state.crc32 = 0;
    local_state.crc32 = crc32((uint8_t *)(&local_state), offset_of(boot_state_t, crc32));
    stm32_flash_unlock();
    if(!stm32_flash_erase(BOOT_STATE_ADDRESS, 16 * 1024))// 擦除16KB的boot_state存储区域
    {
        log_e("Failed to erase boot state");
        stm32_flash_lock();
        return;
    }
    if (!stm32_flash_program(BOOT_STATE_ADDRESS, (const uint8_t *)(&local_state), sizeof(boot_state_t)))
    {
        log_e("Failed to program boot state");
        stm32_flash_lock();
        return;
    }
    stm32_flash_lock();
}
