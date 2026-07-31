#include <stdbool.h>
#include <stdint.h>
#include "led_desc.h"
#include "led.h"

static void led_write_bit(led_desc_t led, uint32_t bit)
{
    if (bit)
        LL_GPIO_SetOutputPin(led->Port, led->Pin);
    else
        LL_GPIO_ResetOutputPin(led->Port, led->Pin);
}

void led_init(led_desc_t led)
{
    LL_GPIO_InitTypeDef GPIO_InitStruct;

    LL_GPIO_StructInit(&GPIO_InitStruct);
    GPIO_InitStruct.Pin = led->Pin;
    GPIO_InitStruct.Mode = LL_GPIO_MODE_OUTPUT;
    GPIO_InitStruct.OutputType = LL_GPIO_OUTPUT_PUSHPULL;
    GPIO_InitStruct.Speed = LL_GPIO_SPEED_FREQ_VERY_HIGH;
    GPIO_InitStruct.Pull = LL_GPIO_PULL_NO;
    LL_GPIO_Init(led->Port, &GPIO_InitStruct);

    led_write_bit(led, led->OffBit);
}

void led_set(led_desc_t led, bool onoff)
{
    led_write_bit(led, onoff ? led->OnBit : led->OffBit);
}

void led_on(led_desc_t led)
{
    led_write_bit(led, led->OnBit);
}

void led_off(led_desc_t led)
{
    led_write_bit(led, led->OffBit);
}
