#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "cbmc_proof/malloc_helpers.h"

void proof_harness() {
    // Memory allocation for pointer parameters
    BaseType_t xReset = nondet_BaseType_t();
    BaseType_t xDoCheck = nondet_BaseType_t();
    NetworkEndPoint_t *pxEndPoint = malloc(sizeof(NetworkEndPoint_t));
    ConstSocket_t xSocket = nondet_ConstSocket_t();

    // Assume pointers to be non null.
    __CPROVER_assume(pxEndPoint != NULL);

    // Call to under-test function.
    vDHCPProcessEndPoint(xReset, xDoCheck, pxEndPoint);
    xHandleWaitingOffer(pxEndPoint, xDoCheck);
    vHandleWaitingAcknowledge(pxEndPoint, xDoCheck);
    xHandleWaitingFirstDiscover(pxEndPoint);
    prvHandleWaitingeLeasedAddress(pxEndPoint);
    vProcessHandleOption(pxEndPoint, malloc(sizeof(ProcessSet_t)), xDoCheck);
    xProcessCheckOption(malloc(sizeof(ProcessSet_t)));
    xIsDHCPSocket(xSocket);
}