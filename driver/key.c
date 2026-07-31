#include <stdbool.h>
#include <stddef.h>
#include "stm32f4xx_ll_gpio.h"
#include "key.h"
#include "key_desc.h"

// KEY1: PA0
// KEY2: PC4
// KEY3: PC5

void key_init(key_desc_t key)
{
    LL_GPIO_InitTypeDef GPIO_InitStruct;
    LL_GPIO_StructInit(&GPIO_InitStruct);
    GPIO_InitStruct.Mode = LL_GPIO_MODE_INPUT;
    GPIO_InitStruct.Pull = key->pupd;
    GPIO_InitStruct.Speed = LL_GPIO_SPEED_FREQ_MEDIUM;
    GPIO_InitStruct.Pin = key->pin;
    LL_GPIO_Init(key->port, &GPIO_InitStruct);
}

bool key_read(key_desc_t key)
{
    return (LL_GPIO_IsInputPinSet(key->port, key->pin) != 0) == (key->press_level != 0);
}

