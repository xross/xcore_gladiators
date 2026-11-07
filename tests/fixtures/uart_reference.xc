/* Verifier validation fixture only. Never supplied to benchmark workers. */
#include <platform.h>
#include <xs1.h>

#ifndef UART_INCREMENT
#define UART_INCREMENT 868
#endif
#ifndef SQUARE_INCREMENT
#define SQUARE_INCREMENT 50000
#endif
#ifndef SECOND_BYTE
#define SECOND_BYTE 0xA5
#endif

on tile[0]: out port tx = XS1_PORT_1A;
on tile[0]: out port sq = XS1_PORT_1D;

void serial(out port p) {
    unsigned at, remainder = 0;
    unsigned bytes[2] = {0x55, SECOND_BYTE};
    p <: 1 @ at;
    at += 10000;
    for (unsigned frame = 0;; ++frame) {
        unsigned bits = (bytes[frame & 1] << 1) | 0x200;
        for (unsigned bit = 0; bit != 10; ++bit) {
            p @ at <: (bits & 1);
            bits >>= 1;
            at += UART_INCREMENT;
            if (++remainder == 18) {
                ++at;
                remainder = 0;
            }
        }
    }
}

void square(out port p) {
    unsigned at, value = 0;
    p <: 0 @ at;
    for (;;) {
        at += SQUARE_INCREMENT;
        value ^= 1;
        p @ at <: value;
    }
}

int main(void) {
    par {
        on tile[0]: serial(tx);
        on tile[0]: square(sq);
    }
    return 0;
}
