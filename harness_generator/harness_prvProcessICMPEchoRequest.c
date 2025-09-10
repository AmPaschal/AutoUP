#include <stdlib.h>
#include "FreeRTOS.h"
#include "ping.h"

void proof_harness(void){
    /* Declare function parameters */
    NetworkBufferDescriptor_t *pxNetworkBuffer;

    /* Allocate memory to the pointer parameters */
    pxNetworkBuffer = (NetworkBufferDescriptor_t *)malloc(sizeof(NetworkBufferDescriptor_t));

    /* Assume the pointers are not NULL */
    __CPROVER_assume(pxNetworkBuffer != NULL);

    /* Assume the size of buffer */
    uint16_t xDataLength = sizeof(ICMPPacket_t);
    __CPROVER_assume(pxNetworkBuffer->xDataLength == xDataLength);

    /* Allocate memory for the EthernetBuffer */
    pxNetworkBuffer->pucEthernetBuffer = (uint8_t *)malloc(xDataLength);
    __CPROVER_assume(pxNetworkBuffer->pucEthernetBuffer != NULL);

    /* Call the function under test */
    ProcessICMPPacket(pxNetworkBuffer);
}