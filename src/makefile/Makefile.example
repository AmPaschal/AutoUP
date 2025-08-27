# Defines the root of the project.
# Be aware of how many directories deep you go!
ROOT = ../../..

# Defines file(s) to link to harness, MUST be a full path!
# (We recommend $(ROOT)/os/... for defining these paths)
# Each file is space seperated, and multiple can be defined
LINK = $(ROOT)/sys/net/gnrc/network_layer/sixlowpan/frag/rb/gnrc_sixlowpan_frag_rb.c 

# Harness to utilize, omit the extension!
H_ENTRY = _rbuf_add

# Extra CBMC flags to be passed during CBMC analysis
H_CBMCFLAGS = --nondet-static --unwind 1 --unwindset gnrc_sixlowpan_frag_rb_gc.2:2 \
       --unwindset _rbuf_int_get_free.0:2 \
       --unwindset _check_fragments.1:2 --unwindset gnrc_pkt_delete.0:2


H_DEF = -DGNRC_NETTYPE_SIXLOWPAN=1 -DMODULE_GNRC_SIXLOWPAN_FRAG=1 -DMODULE_GNRC_SIXLOWPAN_FRAG_SFR=1 \
            -DMODULE_GNRC_SIXLOWPAN_FRAG_MINFWD=1 -DCONFIG_GNRC_SIXLOWPAN_FRAG_RBUF_SIZE=1 \
            -DMODULE_GNRC_SIXLOWPAN_IPHC=1

H_INC = -I$(ROOT) -I$(ROOT)/sys/include/  \
            -I$(ROOT)/core/lib/include/  \
            -I$(ROOT)/core/include/  \
            -I$(ROOT)/cpu/native/include/  \
            -I$(ROOT)/drivers/include/  \
            -I$(ROOT)/boards/native/include/  \
            -I$(ROOT)/sys/net/gnrc/pktbuf/include/  \
            -I$(ROOT)/sys/net/gnrc/pktbuf_static/include/  \

# Include our special build file
MAKE_INCLUDE_PATH ?= ..
include $(MAKE_INCLUDE_PATH)/Makefile.include