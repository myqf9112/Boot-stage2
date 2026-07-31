#ifndef __LED_DESC_H__
#define __LED_DESC_H__

#include <stdbool.h>
#include <stdint.h>
#include "stm32f4xx_ll_gpio.h"

struct led_desc
{
    GPIO_TypeDef* Port;
    uint32_t Pin;
    uint32_t OnBit;
    uint32_t OffBit;
};

#endif /* __LED_DESC_H__ */
