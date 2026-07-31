#include <stdint.h>
#include <string.h>
#include "stm32f4xx_ll_gpio.h"
#include "stm32f4xx_ll_usart.h"
#include "console.h"

static void console_io_init(void)
{
    LL_GPIO_SetAFPin_8_15(GPIOA, LL_GPIO_PIN_9, LL_GPIO_AF_7);
    LL_GPIO_SetAFPin_8_15(GPIOA, LL_GPIO_PIN_10, LL_GPIO_AF_7);

    LL_GPIO_InitTypeDef GPIO_InitStruct;
    LL_GPIO_StructInit(&GPIO_InitStruct);
    GPIO_InitStruct.Mode = LL_GPIO_MODE_ALTERNATE;
    GPIO_InitStruct.Speed = LL_GPIO_SPEED_FREQ_HIGH;
    GPIO_InitStruct.Pin = LL_GPIO_PIN_9 | LL_GPIO_PIN_10;
    LL_GPIO_Init(GPIOA, &GPIO_InitStruct);
}

static void console_usart_init(void)
{
    LL_USART_InitTypeDef USART_InitStruct;
    LL_USART_StructInit(&USART_InitStruct);

    USART_InitStruct.BaudRate = 115200u;
    USART_InitStruct.HardwareFlowControl = LL_USART_HWCONTROL_NONE;
    USART_InitStruct.TransferDirection = LL_USART_DIRECTION_TX_RX;
    USART_InitStruct.Parity = LL_USART_PARITY_NONE;
    USART_InitStruct.StopBits = LL_USART_STOPBITS_1;
    USART_InitStruct.DataWidth = LL_USART_DATAWIDTH_8B;

    LL_USART_Init(USART1, &USART_InitStruct);
    LL_USART_Enable(USART1);
}

void console_init(void)
{
    console_usart_init();
    console_io_init();
}

void console_write(const char str[], uint32_t length)
{
    for (uint32_t i = 0; i < length; i++)
    {
        while (!LL_USART_IsActiveFlag_TXE(USART1));
        LL_USART_TransmitData8(USART1, str[i]);
    }
}
