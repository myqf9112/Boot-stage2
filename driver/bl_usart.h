#ifndef __BL_USART_H__
#define __BL_USART_H__

#include <stdint.h>

typedef void(*bl_usart_rx_callback_t)(const uint8_t *data, uint32_t size);


void bl_usart_init(void);
void bl_usart_write(const uint8_t *data, uint32_t size);
void bl_usart_register_rx_callback(bl_usart_rx_callback_t callback);
#endif /* __BL_USART_H__ */
