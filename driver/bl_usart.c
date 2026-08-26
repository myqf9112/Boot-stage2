#include "stm32f4xx_ll_gpio.h"
#include "stm32f4xx_ll_usart.h"
#include "bl_usart.h"

// USE USART3
// RX: PB11
// TX: PB10
// MODE: 8-N-1
// BAUD: 115200
// DMA: TX/RX

static bl_usart_rx_callback_t rx_callback;

static void usart_io_init(void)
{
    LL_GPIO_InitTypeDef GPIO_InitStruct;
    LL_GPIO_StructInit(&GPIO_InitStruct);

    GPIO_InitStruct.Pin = LL_GPIO_PIN_10 | LL_GPIO_PIN_11;
    GPIO_InitStruct.Mode = LL_GPIO_MODE_ALTERNATE;
    GPIO_InitStruct.Speed = LL_GPIO_SPEED_FREQ_HIGH;
    GPIO_InitStruct.OutputType = LL_GPIO_OUTPUT_PUSHPULL;
    GPIO_InitStruct.Pull = LL_GPIO_PULL_UP;
    GPIO_InitStruct.Alternate = LL_GPIO_AF_7;
    LL_GPIO_Init(GPIOB, &GPIO_InitStruct);
}

// static void usart_dma_config(void)
// {
//     DMA_InitTypeDef DMA_InitStructure;
//     DMA_StructInit(&DMA_InitStructure);

//     // RX
//     DMA_InitStructure.DMA_Channel = DMA_Channel_4;
//     DMA_InitStructure.DMA_PeripheralBaseAddr = (uint32_t)&(USART3->DR);
//     DMA_InitStructure.DMA_Memory0BaseAddr = 0; // set later
//     DMA_InitStructure.DMA_DIR = DMA_DIR_PeripheralToMemory;
//     DMA_InitStructure.DMA_BufferSize = 0; // set later
//     DMA_InitStructure.DMA_PeripheralInc = DMA_PeripheralInc_Disable;
//     DMA_InitStructure.DMA_MemoryInc = DMA_MemoryInc_Enable;
//     DMA_InitStructure.DMA_PeripheralDataSize = DMA_MemoryDataSize_Byte;
//     DMA_InitStructure.DMA_MemoryDataSize = DMA_MemoryDataSize_Byte;
//     DMA_InitStructure.DMA_Mode = DMA_Mode_Normal;
//     DMA_InitStructure.DMA_Priority = DMA_Priority_Medium;
//     DMA_InitStructure.DMA_FIFOMode = DMA_FIFOMode_Enable;
//     DMA_InitStructure.DMA_FIFOThreshold = DMA_FIFOThreshold_Full;
//     DMA_InitStructure.DMA_MemoryBurst = DMA_MemoryBurst_INC16;
//     DMA_InitStructure.DMA_PeripheralBurst = DMA_PeripheralBurst_Single;
//     DMA_Init(DMA1_Stream1, &DMA_InitStructure);
//     // DMA_ITConfig(DMA1_Stream1, DMA_IT_TC, ENABLE);
//     DMA_Cmd(DMA1_Stream1, DISABLE);

//     // TX
//     DMA_InitStructure.DMA_Channel = DMA_Channel_4;
//     DMA_InitStructure.DMA_PeripheralBaseAddr = (uint32_t)&(USART3->DR);
//     DMA_InitStructure.DMA_Memory0BaseAddr = 0; // set later
//     DMA_InitStructure.DMA_DIR = DMA_DIR_MemoryToPeripheral;
//     DMA_InitStructure.DMA_BufferSize = 0; // set later
//     DMA_InitStructure.DMA_PeripheralInc = DMA_PeripheralInc_Disable;
//     DMA_InitStructure.DMA_MemoryInc = DMA_MemoryInc_Enable;
//     DMA_InitStructure.DMA_PeripheralDataSize = DMA_MemoryDataSize_Byte;
//     DMA_InitStructure.DMA_MemoryDataSize = DMA_MemoryDataSize_Byte;
//     DMA_InitStructure.DMA_Mode = DMA_Mode_Normal;
//     DMA_InitStructure.DMA_Priority = DMA_Priority_Medium;
//     DMA_InitStructure.DMA_FIFOMode = DMA_FIFOMode_Enable;
//     DMA_InitStructure.DMA_FIFOThreshold = DMA_FIFOThreshold_Full;
//     DMA_InitStructure.DMA_MemoryBurst = DMA_MemoryBurst_INC16;
//     DMA_InitStructure.DMA_PeripheralBurst = DMA_PeripheralBurst_Single;
//     DMA_Init(DMA1_Stream3, &DMA_InitStructure);
//     // DMA_ITConfig(DMA1_Stream3, DMA_IT_TC, ENABLE);
//     DMA_Cmd(DMA1_Stream3, DISABLE);
// }

static void usart_it_config(void)
{
    NVIC_EnableIRQ(USART3_IRQn);
    NVIC_SetPriority(USART3_IRQn, 5);

    // // DMA TX
    // NVIC_InitStructure.NVIC_IRQChannel = DMA1_Stream3_IRQn;
    // NVIC_InitStructure.NVIC_IRQChannelPreemptionPriority = 5;
    // NVIC_InitStructure.NVIC_IRQChannelSubPriority = 0;
    // NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;
    // NVIC_Init(&NVIC_InitStructure);
    // NVIC_SetPriority(DMA1_Stream3_IRQn, 5);

    // // DMA RX
    // NVIC_InitStructure.NVIC_IRQChannel = DMA1_Stream1_IRQn;
    // NVIC_InitStructure.NVIC_IRQChannelPreemptionPriority = 5;
    // NVIC_InitStructure.NVIC_IRQChannelSubPriority = 0;
    // NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;
    // NVIC_Init(&NVIC_InitStructure);
    // NVIC_SetPriority(DMA1_Stream1_IRQn, 5);
}

static void usart_lowlevel_init(void)
{
    LL_USART_InitTypeDef USART_InitStruct;
    LL_USART_StructInit(&USART_InitStruct);

    USART_InitStruct.BaudRate = 2000000u;
    USART_InitStruct.DataWidth = LL_USART_DATAWIDTH_8B;
    USART_InitStruct.StopBits = LL_USART_STOPBITS_1;
    USART_InitStruct.Parity = LL_USART_PARITY_NONE;
    USART_InitStruct.TransferDirection = LL_USART_DIRECTION_TX_RX;
    USART_InitStruct.HardwareFlowControl = LL_USART_HWCONTROL_NONE;
    LL_USART_Init(USART3, &USART_InitStruct);
    LL_USART_EnableIT_RXNE(USART3);
    LL_USART_EnableDirectionTx(USART3);
    LL_USART_EnableDirectionRx(USART3);
    LL_USART_Enable(USART3);
}

void bl_usart_init(void)
{
    // usart_dma_config();
    usart_it_config();
    usart_lowlevel_init();
    usart_io_init();
}

void bl_usart_write(const uint8_t *data, uint32_t size)
{
    while (size--)
    {
        LL_USART_TransmitData8(USART3, *data++);
        while (!LL_USART_IsActiveFlag_TC(USART3));
    }

    // // DMA Tranfer 65536 bytes at most
    // while (size > 0)
    // {
    //     uint32_t chunk = size > 65536 ? 65536 : size;
    //     DMA1_Stream3->M0AR = (uint32_t)data;
    //     DMA1_Stream3->NDTR = chunk;
    //     DMA_Cmd(DMA1_Stream3, ENABLE);
    //     while (DMA_GetCmdStatus(DMA1_Stream3) != DISABLE);
    //     data += chunk;
    //     size -= chunk;
    // }
}

void bl_usart_register_rx_callback(bl_usart_rx_callback_t callback)
{
    rx_callback = callback;
}

void USART3_IRQHandler(void)
{
    /* 先处理溢出错误：ORE 会标记数据丢失，必须清除 */
    if (LL_USART_IsActiveFlag_ORE(USART3))
    {
        LL_USART_ClearFlag_ORE(USART3);
    }

    if (LL_USART_IsActiveFlag_RXNE(USART3))
    {
        if (rx_callback)
        {
            uint8_t data = LL_USART_ReceiveData8(USART3);
            rx_callback(&data, 1);
        }
        LL_USART_ClearFlag_RXNE(USART3);
    }
}

// // DMA1 Stream3 for USART3 TX
// void DMA1_Stream3_IRQHandler(void)
// {
//     if (DMA_GetITStatus(DMA1_Stream3, DMA_IT_TC) != RESET)
//     {

//         DMA_ClearITPendingBit(DMA1_Stream3, DMA_IT_TC);
//     }
// }

// // DMA1 Stream1 for USART3 RX
// void DMA1_Stream1_IRQHandler(void)
// {
//     if (DMA_GetITStatus(DMA1_Stream1, DMA_IT_TC) != RESET)
//     {

//         DMA_ClearITPendingBit(DMA1_Stream1, DMA_IT_TC);
//     }
// }
