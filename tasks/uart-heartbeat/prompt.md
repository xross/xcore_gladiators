# Two outputs on an XU316

I want an XU316 application with two outputs running at the same time:

- Send a repeating `0x55, 0xA5` byte pattern from one pin as 115200-baud, 8N1 serial data.
- Generate a 1 kHz square wave on a second pin.

The serial transmission must not disrupt the square-wave timing. Please get it working and show me how I can test it without connecting a board.
