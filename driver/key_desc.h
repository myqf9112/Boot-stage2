#ifndef __KEY_DESC_H__
#define __KEY_DESC_H__

#include <stdint.h>
#include "stm32f4xx_ll_gpio.h"
#include "key.h"

struct key_desc
{
    GPIO_TypeDef *port;
    uint32_t pin;
    uint32_t pupd;
    uint32_t press_level;
};

#endif /* __KEY_H__ */
