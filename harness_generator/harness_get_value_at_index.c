#include <stdlib.h>
#include <stdint.h>
#include <assert.h>
#include <stdbool.h>

int get_value_at_index(int index);

void proof_harness() {
    // Declare all function parameters exactly as they appear in the function signature
    int index;

    // Call the function in the harness using the declared and initialized arguments
    int value = get_value_at_index(index);

    // Extra assumptions or verifications can be done here
    // For example, usually you would want to limit the range of 'index' to prevent possible out of bounds
    // Note that this simple function doesn't do any NULL checks or allocation, so those steps are not needed
}