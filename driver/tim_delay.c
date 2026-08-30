#include <stdint.h>
#include <string.h>
#include "stm32f4xx_ll_tim.h"
#include "stm32f4xx_ll_rcc.h"
#include "stm32f4xx_ll_bus.h"
#include "stm32f4xx_ll_iwdg.h"
#include "tim_delay.h"

static volatile uint64_t tim_tick_count;
static tim_periodic_callback_t periodic_callback;

void tim_delay_init(void)
{
    LL_RCC_ClocksTypeDef RCC_Clocks;
    LL_RCC_GetSystemClocksFreq(&RCC_Clocks);
    uint32_t apb1_tim_freq_mhz = RCC_Clocks.PCLK1_Frequency / 1000 / 1000 * 2;

    LL_TIM_InitTypeDef TIM_InitStruct;
    LL_TIM_StructInit(&TIM_InitStruct);
    TIM_InitStruct.Prescaler = apb1_tim_freq_mhz - 1;
    TIM_InitStruct.Autoreload = 999;
    TIM_InitStruct.ClockDivision = LL_TIM_CLOCKDIVISION_DIV1;
    TIM_InitStruct.CounterMode = LL_TIM_COUNTERMODE_UP;
    LL_TIM_Init(TIM6, &TIM_InitStruct);
    LL_TIM_EnableIT_UPDATE(TIM6);
    LL_TIM_EnableCounter(TIM6);

    NVIC_EnableIRQ(TIM6_DAC_IRQn);
    NVIC_SetPriority(TIM6_DAC_IRQn, 5);
}

uint64_t tim_now(void)
{
    uint64_t now, last_count;
    do {
        last_count = tim_tick_count;
        now = tim_tick_count + LL_TIM_GetCounter(TIM6);
    } while (last_count != tim_tick_count);
    return now;
}

uint64_t tim_get_us(void)
{
    return tim_now();
}

uint64_t tim_get_ms(void)
{
    return tim_now() /1000;
}

void tim_delay_us(uint32_t us)
{
    uint64_t now = tim_now();
    while (tim_now() - now < (uint64_t)us);
}

void tim_delay_ms(uint32_t ms)
{
    uint64_t now = tim_now();
    while (tim_now() - now < (uint64_t)ms * 1000);
}

void tim_register_periodic_callback(tim_periodic_callback_t callback)
{
    periodic_callback = callback;
}

void TIM6_DAC_IRQHandler(void)
{
    if (LL_TIM_IsActiveFlag_UPDATE(TIM6))
    {
        LL_TIM_ClearFlag_UPDATE(TIM6);
        tim_tick_count += 1000;
        if (periodic_callback)
            periodic_callback();
    }
}
