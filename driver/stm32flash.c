#include "stm32flash.h"
#include "stdint.h"
#include "stdbool.h"
#include "stdio.h"
#include "string.h"

#define LOG_TAG "flash"
#define LOG_LVL ELOG_LVL_INFO
#include "elog.h"

#define FLASH_BASE_ADDR 0x08000000
typedef struct
{
    uint32_t sector;
    uint32_t size;
} sector_desc_t;

static const sector_desc_t sector_desc[] =
{
        {FLASH_SECTOR_0, 16 * 1024},   // sector 0, 16KB
        {FLASH_SECTOR_1, 16 * 1024},   // sector 1, 16KB
        {FLASH_SECTOR_2, 16 * 1024},   // sector 2, 16KB
        {FLASH_SECTOR_3, 16 * 1024},   // sector 3, 16KB
        {FLASH_SECTOR_4, 64 * 1024},   // sector 4, 64KB
        {FLASH_SECTOR_5, 128 * 1024},  // sector 5, 128KB
        {FLASH_SECTOR_6, 128 * 1024},  // sector 6, 128KB
        {FLASH_SECTOR_7, 128 * 1024},  // sector 7, 128KB
        {FLASH_SECTOR_8, 128 * 1024},  // sector 8, 128KB
        {FLASH_SECTOR_9, 128 * 1024},  // sector 9, 128KB
        {FLASH_SECTOR_10, 128 * 1024}, // sector 10, 128KB
        {FLASH_SECTOR_11, 128 * 1024}  // sector 11, 128KB
};

void stm32_flash_unlock(void)
{
    HAL_FLASH_Unlock();
}
void stm32_flash_lock(void)
{
    HAL_FLASH_Lock();
}

bool stm32_flash_erase(uint32_t address, uint32_t size)
{
    bool ok = true;
    uint32_t addr = FLASH_BASE_ADDR;
    for (uint32_t i = 0; i < sizeof(sector_desc) / sizeof(sector_desc_t); i++)
    {
        if (addr >= address && addr < address + size)
        {
            log_w("erasing sector %lu at address 0x%08lx size %lu", i, addr, sector_desc[i].size);
            FLASH_EraseInitTypeDef eraseInit;
            uint32_t sectorError;
            eraseInit.TypeErase = FLASH_TYPEERASE_SECTORS;
            eraseInit.Sector = sector_desc[i].sector;
            eraseInit.NbSectors = 1;
            eraseInit.VoltageRange = FLASH_VOLTAGE_RANGE_3;
            if (HAL_FLASHEx_Erase(&eraseInit, &sectorError) != HAL_OK)
            {
                log_e("flash erase error at sector %lu", i);
                ok = false;
            }
        }
        addr += sector_desc[i].size;
    }
    return ok;
}
bool stm32_flash_program(uint32_t address, const uint8_t *data, uint32_t size)
{

    bool ok = true;
    for (uint32_t i = 0; i < size; i += 4)
    {
        uint32_t word;
        memcpy(&word, data + i, 4);
        if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, address + i, word) != HAL_OK)
        {
            log_w("failed to program word at address 0x%08lx", address + i);
            ok = false;
        }
    }
    return ok;
}
