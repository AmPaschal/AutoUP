#include <stdint.h>
#include <stdlib.h>
#include "cbmc.h"

void proof_harness()
{
    NetworkBufferDescriptor_t * pxNetworkBuffer = malloc(sizeof(NetworkBufferDescriptor_t));
    __CPROVER_assume(pxNetworkBuffer != NULL);

    pxNetworkBuffer->pucEthernetBuffer = malloc(sizeof(uint8_t)*BUFFER_SIZE);
    __CPROVER_assume(pxNetworkBuffer->pucEthernetBuffer != NULL);

    pxNetworkBuffer->pxEndPoint = malloc(sizeof(NetworkEndPoint_t));
    __CPROVER_assume(pxNetworkBuffer->pxEndPoint != NULL);

    /* Function under verfication */
    eARPProcessPacket(pxNetworkBuffer);
}