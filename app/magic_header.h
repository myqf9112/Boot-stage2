#ifndef  __MAGIC__HEADER__H__
#define  __MAGIC__HEADER__H__

#include <stdbool.h>
#include <stdint.h>

typedef enum

{
    MAGIC_HEADER_TYPE_APP = 0,
} magic_header_type_t;

bool magic_header_validate(uint32_t magic_header_address);
magic_header_type_t magic_header_get_type(uint32_t magic_header_address);
uint32_t magic_header_get_offset(uint32_t magic_header_address);
uint32_t magic_header_get_address(uint32_t magic_header_address);
uint32_t magic_header_get_length(uint32_t magic_header_address);
uint32_t magic_header_get_crc32(uint32_t magic_header_address);
#endif /* __MAGIC__HEADER__H__*/
