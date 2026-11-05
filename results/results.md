| Scenario | SS | AIMD | TAHOE | RENO | DLA |
|---|---|---|---|---|---|
| Clean wired | 42% | 92% | 91% | 86% | **98%** |
| Lossy Wi-Fi (1%) | 32% | 22% | 22% | 26% | **77%** |
| Satellite (300 ms) | 26% | 8% | 14% | 17% | **37%** |
| Bufferbloat (4 BDP) | 57% | 96% | 94% | 79% | **98%** |
| Fast + lossy (50 Mbps) | 25% | 16% | 16% | 13% | **75%** |

| Scenario | Algorithm | Goodput (Mbps) | Mean RTT (ms) | p95 RTT (ms) | Timeouts |
|---|---|---|---|---|---|
| Bufferbloat (4 BDP) | SS | 5.66 ± 0.00 | 151 | 242 | 0.0 |
| Bufferbloat (4 BDP) | AIMD | 9.56 ± 0.01 | 163 | 243 | 0.0 |
| Bufferbloat (4 BDP) | TAHOE | 9.43 ± 0.18 | 175 | 243 | 0.2 |
| Bufferbloat (4 BDP) | RENO | 7.90 ± 0.02 | 153 | 226 | 1.0 |
| Bufferbloat (4 BDP) | DLA | 9.78 ± 0.00 | 66 | 77 | 0.0 |
| Lossy Wi-Fi (1%) | SS | 3.24 ± 0.15 | 64 | 90 | 13.4 |
| Lossy Wi-Fi (1%) | AIMD | 2.19 ± 0.19 | 52 | 54 | 5.6 |
| Lossy Wi-Fi (1%) | TAHOE | 2.22 ± 0.14 | 53 | 55 | 1.4 |
| Lossy Wi-Fi (1%) | RENO | 2.57 ± 0.13 | 52 | 54 | 2.4 |
| Lossy Wi-Fi (1%) | DLA | 7.67 ± 0.31 | 54 | 61 | 2.6 |
