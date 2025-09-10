#include "cbmc.h"
#include "FreeRTOS.h"
#include "task.h"
#include "ipconfig.h"
#include "IPTimer_t.h"

void proof_harness() {
    IPTimer_t * pxTimer;
    TickType_t xTime;

    pxTimer = malloc(sizeof(IPTimer_t));
    __CPROVER_assume(pxTimer != NULL);

    prvIPTimerReload(pxTimer, xTime);
}