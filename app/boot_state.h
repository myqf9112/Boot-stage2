#ifndef  __BOOT_STATE_H__
#define __BOOT_STATE_H__
#include <stdbool.h>
#include <stdint.h>

/*
 * A/B 双备份启动状态机语义
 *
 * 字段:
 *   active_slot   : 当前"已提交"的启动槽(默认启动)
 *   pending_slot  : 待激活槽(OTA升级目标);BOOT_PENDING_NONE 表示无升级
 *   boot_attempts : 当前槽连续"看门狗复位"次数
 *
 * 启动决策链(bootloader_main):
 *   1) 读 boot_state(Flash 无效则回退默认:active=A, pending=NONE, attempts=0)
 *   2) 看门狗复位(IWDGRST) -> boot_attempts++ ;连续 >=3 次则判定当前 active 为坏,
 *      active 在 A/B 间翻转回滚,attempts 清零
 *   3) 非看门狗复位(上电/软复位/外部复位) -> boot_attempts 清零(干净复位)
 *   4) 若 pending != NONE,乐观提交:active = pending, pending = NONE, attempts = 0
 *   5) 启动槽 = pending 优先,否则 active;失败则回退另一槽
 *
 * 升级/回滚流程:
 *   升级: SWITCH_SLOT(B) 写 pending=B -> RESET -> bootloader 提交 B 为 active 并启动
 *   回滚: B 连续 3 次看门狗崩溃 -> active 翻回 A
 *
 * 看门狗约定:
 *   IWDG 一旦使能无法关闭。bootloader 跳转前 reload 一次,应用须在 3.2s 超时前
 *   接管喂狗,否则被判定崩溃并累计 boot_attempts。
 *   (超时取 3.2s:覆盖 F407 128KB 扇区擦除 max 2.6s,擦除期间喂狗中断停摆)
 */
#define BOOT_STATE_ADDRESS    0X08008000 //boot_state存储地址
#define BOOT_STATE_SIZE    (16 * 1024)
#define BOOT_PENDING_NONE 0xFF // 无待切换槽
#define BOOT_STATE_MAGIC 0x424F4F54UL // "BOOT"的ASCII码
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

