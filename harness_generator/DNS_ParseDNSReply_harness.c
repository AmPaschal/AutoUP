#include "FreeRTOS.h"
/* FreeRTOS+TCP includes. */
#include "FreeRTOS_IP.h"
#include "FreeRTOS_IP_Private.h"

#include "FreeRTOS_DNS_Globals.h"
#include "FreeRTOS_DNS_Parser.h"
#include "FreeRTOS_DNS_Cache.h"
#include "FreeRTOS_DNS_Callback.h"

#include "NetworkBufferManagement.h"

#include <string.h>

void proof_harness() {
    uint16_t datalen;
    __CPROVER_assume(datalen > 0 && datalen < 65535);

    uint8_t *pucUDPPayloadBuffer = malloc(sizeof(uint8_t) * datalen);
    __CPROVER_assume(pucUDPPayloadBuffer != NULL);

    size_t uxBufferLength = datalen;

    struct freertos_addrinfo *pxAddressInfo = malloc(sizeof(struct freertos_addrinfo));
    __CPROVER_assume(pxAddressInfo != NULL);

    struct freertos_addrinfo **ppxAddressInfo = malloc(sizeof(struct freertos_addrinfo *));
    __CPROVER_assume(ppxAddressInfo != NULL);

    *ppxAddressInfo = pxAddressInfo;

    BaseType_t xExpected;
    uint16_t usPort;
    
    DNS_ParseDNSReply(pucUDPPayloadBuffer, uxBufferLength, ppxAddressInfo, xExpected, usPort);
}