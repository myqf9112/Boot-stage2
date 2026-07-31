#include <stdio.h>
#include "stm32f4xx_ll_bus.h"
#include "stm32f4xx_ll_rcc.h"
#include "stm32f4xx_ll_pwr.h"
#include "stm32f4xx_ll_system.h"
#include "stm32f4xx_ll_utils.h"
#include "stm32f4xx_ll_usart.h"
#include "borad.h"
#include "led_desc.h"
#include "key_desc.h"

static void SystemClock_Config(void)
{
    /* Enable PWR clock and set voltage scaling to Scale 1 (for 168 MHz) */
    LL_APB1_GRP1_EnableClock(LL_APB1_GRP1_PERIPH_PWR);
    LL_PWR_SetRegulVoltageScaling(LL_PWR_REGU_VOLTAGE_SCALE1);

    /* Enable HSE and wait until ready */
    LL_RCC_HSE_Enable();
    while (!LL_RCC_HSE_IsReady())
        ;

    /* Set Flash latency to 5 wait states (required for 168 MHz, 3.3V) */
    LL_FLASH_SetLatency(LL_FLASH_LATENCY_5);

    /* Configure PLL: HSE/4 * 168 / 2 = 168 MHz, PLLQ=4 for 48 MHz */
    LL_RCC_PLL_ConfigDomain_SYS(LL_RCC_PLLSOURCE_HSE, LL_RCC_PLLM_DIV_4, 168, LL_RCC_PLLP_DIV_2);
    LL_RCC_PLL_ConfigDomain_48M(LL_RCC_PLLSOURCE_HSE, LL_RCC_PLLM_DIV_4, 168, 4);

    /* Enable PLL and wait until ready */
    LL_RCC_PLL_Enable();
    while (!LL_RCC_PLL_IsReady())
        ;

    /* Set AHB, APB1, APB2 prescalers */
    LL_RCC_SetAHBPrescaler(LL_RCC_SYSCLK_DIV_1);
    LL_RCC_SetAPB1Prescaler(LL_RCC_APB1_DIV_4);
    LL_RCC_SetAPB2Prescaler(LL_RCC_APB2_DIV_2);

    /* Switch SYSCLK to PLL */
    LL_RCC_SetSysClkSource(LL_RCC_SYS_CLKSOURCE_PLL);
    while (LL_RCC_GetSysClkSource() != LL_RCC_SYS_CLKSOURCE_STATUS_PLL)
        ;

    /* Update SystemCoreClock variable */
    SystemCoreClockUpdate();
}

void board_lowlevel_init(void)
{
    NVIC_SetPriorityGrouping(3);
    SystemClock_Config();
    LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_GPIOA);
    LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_GPIOB);
    LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_GPIOC);
    LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_GPIOD);
    LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_GPIOE);
    LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_GPIOF);
    LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_GPIOG);
    LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_DMA1);
    LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_DMA2);
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_USART1);
    LL_APB1_GRP1_EnableClock(LL_APB1_GRP1_PERIPH_USART3);
    LL_APB1_GRP1_EnableClock(LL_APB1_GRP1_PERIPH_TIM6);
}

int fputc(int ch, FILE *f)
{
    LL_USART_TransmitData8(USART1, (uint8_t)ch);
    while (!LL_USART_IsActiveFlag_TXE(USART1))
        ;
    return ch;
}
static struct led_desc _led1 = {GPIOG, LL_GPIO_PIN_13, 0, 1};
static struct led_desc _led2 = {GPIOG, LL_GPIO_PIN_14, 0, 1};
led_desc_t led1 = &_led1;
led_desc_t led2 = &_led2;

static struct key_desc _key1 = {GPIOF, LL_GPIO_PIN_6, LL_GPIO_PULL_UP, 0};
static struct key_desc _key2 = {GPIOF, LL_GPIO_PIN_7, LL_GPIO_PULL_UP, 0};

key_desc_t key1 = &_key1;
key_desc_t key2 = &_key2;
