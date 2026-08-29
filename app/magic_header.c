#include "magic_header.h"
#include <stdint.h>
#include <stdbool.h>
#include "crc32.h"
#include "utils.h"
#define MAGIC_HEADER_MAGIC 0x4D414749   // "MAGI"的ASCII码
typedef struct
{
    uint32_t magic;         // 魔术，用于表示这是一个有效的魔术头
    uint32_t bitmask;       // 位掩码，用于标识哪些字段是有效的
    uint32_t reserved1[6];  // 保留字段，预留给未来使用
    uint32_t data_type;          // 类型，根据type类型选择固件下载位置
    uint32_t data_offset;        // 固件文件相对于magic header的偏移地址，
    uint32_t data_address;       // 地址，表示固件在内存中的加载地址
    uint32_t data_length;        // 长度，表示固件的长度
    uint32_t data_crc32;         // 固件的CRC32校验值，用于验证固件的完整性
    uint32_t reserved2[11]; // 保留字段，预留给未来使用

    char version[128]; // 版本信息，表示固件的版本号

    uint32_t reserved3[6]; // 保留字段，预留给未来使用
    uint32_t this_address; // 魔术头在内存中的地址
    uint32_t this_crc32;   // 魔术头的CRC32校验值，用于验证魔术头的完整性
} magic_header_t;

bool magic_header_validate(uint32_t magic_header_address)
{
    magic_header_t *header = (magic_header_t *)magic_header_address;
    if(header->this_address != magic_header_address)
    {
        return false; // 魔术头地址不匹配
    }
    if (header->magic != MAGIC_HEADER_MAGIC)
    {
        return false; // 魔术头不合法
    }
    // 验证魔术头的完整性
    uint32_t ccrc32 = crc32((uint8_t *)header, offset_of(magic_header_t, this_crc32)); // 计算魔术头的CRC32校验值
    if (ccrc32 != header->this_crc32)
    {
        return false; // 魔术头不合法
    }
    return true; // 魔术头合法
}

magic_header_type_t magic_header_get_type(uint32_t magic_header_address)
{
    magic_header_t *header = (magic_header_t *)magic_header_address;
    return (magic_header_type_t)header->data_type; // 返回魔术头的类型
}
uint32_t magic_header_get_offset(uint32_t magic_header_address)
{
    magic_header_t *header = (magic_header_t *)magic_header_address;
    return header->data_offset; // 返回魔术头的偏移地址
}

uint32_t magic_header_get_address(uint32_t magic_header_address)
{
    magic_header_t *header = (magic_header_t *)magic_header_address;
    return header->data_address; // 返回魔术头的地址
}

uint32_t magic_header_get_length(uint32_t magic_header_address)
{

    magic_header_t *header = (magic_header_t *)magic_header_address;
    return header->data_length; // 返回魔术头的长度
}

uint32_t magic_header_get_crc32(uint32_t magic_header_address)
{
    magic_header_t *header = (magic_header_t *)magic_header_address;
    return header->data_crc32; // 返回魔术头的CRC32校验值
}
