#ifndef  __BOOT_STATE_H__
#define __BOOT_STATE_H__
#include <stdbool.h>
#include <stdint.h>
#define BOOT_STATE_ADDRESS    0X08008000 //boot_state存储地址
#define BOOT_STATE_SIZE    (16 * 1024)
#define BOOT_PENDING_NONE 0xFF // 无待切换槽
typedef enum
{
    BOOT_SLOT_A=0,
    BOOT_SLOT_B=1
}boot_slot_t;

typedef struct
{
    uint32_t magic;   //魔术
    uint32_t active_slot; //当前启动槽，0=A / 1=B
    uint32_t pending_slot; //待切换槽 0xFF=无
    uint32_t boot_attempts; //当前槽连续看门狗复位次数
    uint32_t crc32; //boot_state的CRC32校验值
}boot_state_t;

boot_state_t boot_state_read(void);
void boot_state_write(const boot_state_t *state);
bool boot_state_validate(const boot_state_t *state);
#endif /*__BOOT_STATE_H__*/

