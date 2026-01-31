#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

void *memcpy(void *dst, const void *src, size_t n) {

    __CPROVER_precondition(
        __CPROVER_POINTER_OBJECT(dst) != __CPROVER_POINTER_OBJECT(src) ||
            ((const char *)src >= (const char *)dst + n) || ((const char *)dst >= (const char *)src + n),
        "memcpy src/dst overlap");
    __CPROVER_precondition(src != NULL && __CPROVER_r_ok(src, n), "memcpy1 source region readable");
    __CPROVER_precondition(dst != NULL && __CPROVER_w_ok(dst, n), "memcpy2 destination region writeable");

    if (n > 0) {
        if (__builtin_constant_p(n)) {
            char src_n[n];
            __CPROVER_array_copy(src_n, (char *)src);
            __CPROVER_array_replace((char *)dst, src_n);
        } else {
            size_t index;
            __CPROVER_assume(index < n);
            ((uint8_t *)dst)[index] = nondet_uint8_t();
        }
        
    }
    
    return dst;
}

void *memmove(void *dest, const void *src, size_t n)
{
    __CPROVER_HIDE:;
    
    __CPROVER_precondition(__CPROVER_r_ok(src, n),
                            "memmove source region readable");
    __CPROVER_precondition(__CPROVER_w_ok(dest, n),
                            "memmove destination region writeable");

    if (n > 0) {
        if (__builtin_constant_p(n)) {
            char src_n[n];
            __CPROVER_array_copy(src_n, (char *)src);
            __CPROVER_array_replace((char *)dest, src_n);
        } else {
            size_t index;
            __CPROVER_assume(index < n);
            ((uint8_t *)dest)[index] = nondet_uint8_t();
        }
        
    }
    return dest;
}