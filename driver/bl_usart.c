#include "stm32f4xx_ll_gpio.h"
#include "stm32f4xx_ll_usart.h"
#include "stm32f4xx_ll_dma.h"
#include "bl_usart.h"

// USE USART3
// RX: PB11
// TX: PB10
// MODE: 8-N-1
// BAUD: 115200
// DMA: TX/RX

#define DMA_RX_BUF_SIZE 8192 // DMA 环形接收缓冲

static uint8_t dma_rx_buf[DMA_RX_BUF_SIZE] __attribute__((aligned(4)));
static uint32_t dma_rx_last; // 已搬进环形缓冲的 DMA 位置
static bl_usart_rx_callback_t rx_callback;

static void usart_dma_rx_config(void);

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



static void usart_it_config(void)
{
    NVIC_EnableIRQ(USART3_IRQn);
    NVIC_SetPriority(USART3_IRQn, 5);
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
    LL_USART_EnableDMAReq_RX(USART3);
    LL_USART_EnableDirectionTx(USART3);
    LL_USART_EnableDirectionRx(USART3);
    LL_USART_Enable(USART3);
}

void bl_usart_init(void)
{
    usart_dma_rx_config();
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


}

static void usart_dma_rx_config(void)
{
    /* DMA1 Stream1 Channel4 = USART3_RX (F407 固定映射),环形模式 */
    LL_DMA_SetChannelSelection(DMA1, LL_DMA_STREAM_1, LL_DMA_CHANNEL_4);
    LL_DMA_SetDataTransferDirection(DMA1, LL_DMA_STREAM_1, LL_DMA_DIRECTION_PERIPH_TO_MEMORY);
    LL_DMA_SetStreamPriorityLevel(DMA1, LL_DMA_STREAM_1, LL_DMA_PRIORITY_MEDIUM);
    LL_DMA_SetMode(DMA1, LL_DMA_STREAM_1, LL_DMA_MODE_CIRCULAR);
    LL_DMA_SetPeriphIncMode(DMA1, LL_DMA_STREAM_1, LL_DMA_PERIPH_NOINCREMENT);
    LL_DMA_SetMemoryIncMode(DMA1, LL_DMA_STREAM_1, LL_DMA_MEMORY_INCREMENT);
    LL_DMA_SetPeriphSize(DMA1, LL_DMA_STREAM_1, LL_DMA_PDATAALIGN_BYTE);
    LL_DMA_SetMemorySize(DMA1, LL_DMA_STREAM_1, LL_DMA_MDATAALIGN_BYTE);
    LL_DMA_SetDataLength(DMA1, LL_DMA_STREAM_1, DMA_RX_BUF_SIZE);
    LL_DMA_ConfigAddresses(DMA1, LL_DMA_STREAM_1,
                           (uint32_t)&USART3->DR,
                           (uint32_t)dma_rx_buf,
                           LL_DMA_DIRECTION_PERIPH_TO_MEMORY);
    LL_DMA_EnableStream(DMA1, LL_DMA_STREAM_1);
}

void bl_usart_flush_rx(void)
{
    /* 用 NDTR 算出 DMA 当前写入位置,把新收到的数据搬进上层环形缓冲 */
    uint32_t remain = LL_DMA_GetDataLength(DMA1, LL_DMA_STREAM_1);
    uint32_t pos = DMA_RX_BUF_SIZE - remain;
    if (pos == dma_rx_last)
        return;
    if (!rx_callback)
    {
        dma_rx_last = pos;
        return;
    }
    if (pos > dma_rx_last)
    {
        rx_callback(&dma_rx_buf[dma_rx_last], pos - dma_rx_last);
    }
    else
    {
        rx_callback(&dma_rx_buf[dma_rx_last], DMA_RX_BUF_SIZE - dma_rx_last);
        if (pos > 0)
            rx_callback(&dma_rx_buf[0], pos);
    }
    dma_rx_last = (pos == DMA_RX_BUF_SIZE) ? 0 : pos;
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

