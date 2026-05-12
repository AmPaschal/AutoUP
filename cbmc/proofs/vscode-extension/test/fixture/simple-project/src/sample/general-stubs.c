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

    if (n > 0 && __builtin_constant_p(n)) {
        char src_n[n];
        __CPROVER_array_copy(src_n, (char *)src);
        __CPROVER_array_replace((char *)dst, src_n);
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

    if (n > 0 && __builtin_constant_p(n)) {
        char src_n[n];
        __CPROVER_array_copy(src_n, (char *)src);
        __CPROVER_array_replace((char *)dest, src_n);
    }
    return dest;
}


int sprintf(char *dst, const char *fmt, ...)
{
    va_list ap;
    const char *src;
    size_t dst_size;
    size_t src_obj_size;
    size_t fmt_obj_size;
    size_t src_len;
    size_t literal_len;
    size_t out_len;

    __CPROVER_assume(dst != NULL);
    __CPROVER_assume(fmt != NULL);

    dst_size = __CPROVER_OBJECT_SIZE(dst);
    fmt_obj_size = __CPROVER_OBJECT_SIZE(fmt);

    __CPROVER_assume(dst_size > 0);
    __CPROVER_assume(fmt_obj_size >= 3);

    va_start(ap, fmt);
    src = va_arg(ap, const char *);
    va_end(ap);

    __CPROVER_assume(src != NULL);

    src_obj_size = __CPROVER_OBJECT_SIZE(src);
    __CPROVER_assume(src_obj_size > 0);

    /* Nondeterministically model actual lengths within object bounds. */
    src_len = nondet_size_t();
    literal_len = nondet_size_t();

    __CPROVER_assume(src_len < src_obj_size);
    __CPROVER_assume(literal_len <= fmt_obj_size - 3);

    out_len = literal_len + src_len;

    __CPROVER_assert(out_len < dst_size,
                     "sprintf destination overflow");

    if (out_len < dst_size) {
        dst[out_len] = '\0';
    }

    return (int)out_len;
}