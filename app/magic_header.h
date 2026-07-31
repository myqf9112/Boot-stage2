#ifndef  __MAGIC__HEADER__H__
#define  __MAGIC__HEADER__H__

#include <stdbool.h>
#include <stdint.h>

typedef enum

{
    MAGIC_HEADER_TYPE_APP = 0,
} magic_header_type_t;

bool magic_header_validate(void);
magic_header_type_t magic_header_get_type(void);
uint32_t magic_header_get_offset(void);
uint32_t magic_header_get_address(void);
uint32_t magic_header_get_length(void);
uint32_t magic_header_get_crc32(void);
#endif /* __MAGIC__HEADER__H__*/
