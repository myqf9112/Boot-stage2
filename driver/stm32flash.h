#ifndef __STM32__FLASH_H
#define __STM32__FLASH_H
#include <stdint.h>
#include <stdbool.h>
#include "stm32f4xx.h"
#include "stm32f4xx_hal_flash.h"
#define STM32_FLASH_BASE 0x08000000
#define STM32_FLASH_SIZE (512 * 1024)
void stm32_flash_unlock(void);
void stm32_flash_lock(void);
bool stm32_flash_erase(uint32_t address, uint32_t size);
bool stm32_flash_program(uint32_t address, const uint8_t *data, uint32_t size);
#endif /* __STM32__FLASH_H */
