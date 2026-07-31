#ifndef __BOARD__H__
#define __BOARD__H__

#include "led.h"
#include"key.h"
void board_lowlevel_init(void);
extern led_desc_t led1;
extern led_desc_t led2;
extern key_desc_t key1;
extern key_desc_t key2;
#endif /*__BOARD__H__*/
