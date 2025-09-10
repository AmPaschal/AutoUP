#include <stdint.h>
#include <stdlib.h>
#include <stdbool.h>
#include "cbmc.h"

typedef struct
{
    uint8_t xHasError;
    size_t uxIndex;
    size_t uxSize;
    uint8_t ucContents[];
} BitConfig_t;

#define pdFALSE 0
#define pdTRUE 1

void vBitConfig_write_uc( BitConfig_t * pxConfig,
                          const uint8_t * pucData,
                          size_t uxSize );

void proof_harness()
{
    /* Declare all function parameters inside proof_harness
       exactly as they appear in the function signature. */
    BitConfig_t *pxConfig;
    const uint8_t *pucData;
    size_t uxSize;

    /* For any pointer to a struct, allocate memory with malloc and 
       use __CPROVER_assume(ptr != NULL) */
    pxConfig = malloc(sizeof(BitConfig_t) + sizeof(uint8_t) * uxSize);
    __CPROVER_assume(pxConfig != NULL);

    /* For pointers to primitive types, create a size variable to hold 
       the allocation size. Allocate memory using malloc. Use __CPROVER_assume(len == related_size_param)
       if the pointer and size parameter are related. */
    uint16_t len = uxSize;
    pucData = malloc(sizeof(uint8_t) * len);
    __CPROVER_assume(pucData != NULL);
    __CPROVER_assume(len == uxSize);

    /* For any pointer used in the function without a NULL check, 
       add a precondition using __CPROVER_assume(ptr != NULL). */
    __CPROVER_assume(pxConfig->ucContents != NULL);

    /* Set assumptions on the struct memory */
    __CPROVER_assume(pxConfig->uxSize >= uxSize);
    __CPROVER_assume(pxConfig->uxIndex <= pxConfig->uxSize);

    /* Call the function in the harness using the declared and initialized args. */
    vBitConfig_write_uc(pxConfig, pucData, uxSize);
}