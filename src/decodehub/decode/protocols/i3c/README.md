# I3C SDR

This module is a passive decoder for two-channel I3C Basic v1.1.1 Single Data
Rate captures. It accepts SCL and SDA digital channels and emits START,
address, data, CCC, DAA, transfer, warning, and unsupported events.

Controller-written SDR data uses the ninth clock as odd parity
1 XOR XOR(data[7:0]). Target-returned data uses the ninth clock as the
transition bit: 1 continues a read and 0 ends it. The address header keeps
I2C's eight-bit address-plus-R/W layout and its ninth ACK/NACK phase.

mode=sdr decodes I3C SDR, mode=legacy_i2c keeps I2C ACK semantics, and
mode=auto decodes recognizable I3C patterns while leaving private traffic
unknown/ambiguous (a passive two-wire capture cannot prove that it is I3C).
Only the reserved 7Eh/W broadcast header opens CCC context. ENTDAA is decoded
as 64 continuous PID/BCR/DCR bits followed by dynamic-address parity/ACK;
multiple arbitration rounds are emitted as separate DAA events. HDR-DDR/TSP/
TSL/BT and electrical drive ownership are not decodable from the current
logical DigitalWave model.

`bus_profile=auto` skips deterministic bus-free timing warnings; use `pure`
or `mixed` when the physical bus profile is known.

The encoder is deterministic and intended for tests; it does not model
push-pull/open-drain ownership or analog rise times.
