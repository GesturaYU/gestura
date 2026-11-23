# Firmware

Firmware for the two ESP32-S3 boards of the CSI link. The receiver's serial output is defined in [`docs/csi-session-contract-v1.md`](../docs/csi-session-contract-v1.md).

| Folder | Board role | Based on |
|---|---|---|
| `sender/` | Sends numbered ESP-NOW broadcast packets | `csi_send_ch11_ht20`, from the Espressif esp-csi `csi_send` example (esp-csi commit `8633d67`) |
| `receiver/` | Captures CSI from the sender and prints one line per packet | `csi_recv_ab`, from the esp-csi `csi_recv` example (same commit) |
| `common/` | Empty; reserved for code shared by both | |

## Link configuration

| Setting | Value | Where it is set |
|---|---|---|
| Wi-Fi channel | 11, no secondary channel | `CONFIG_LESS_INTERFERENCE_CHANNEL` in both `main/app_main.c` |
| Bandwidth | 20 MHz (HT20) | `CONFIG_WIFI_BANDWIDTH` and `CONFIG_ESP_NOW_PHYMODE` in both `main/app_main.c` |
| ESP-NOW rate | MCS0, long guard interval | `CONFIG_ESP_NOW_RATE` in both `main/app_main.c` |
| Configured send rate | 100 packets/s (about 90/s measured) | `CONFIG_SEND_FREQUENCY` in `sender/main/app_main.c` |
| Sender MAC | `1a:00:00:00:00:00` | `CONFIG_CSI_SEND_MAC` in both; the receiver ignores other senders |
| Receiver gain | Automatic for the first 100 packets, then fixed at their baseline | `CONFIG_FORCE_GAIN 1` in `receiver/main/app_main.c` |
| Receiver output | UART console, 921600 baud, 8N1 | `receiver/sdkconfig.defaults` |

**Deviation from the feasibility test (17 Oct 2026).** The feasibility test used 40 MHz (HT40) on channel 11. At 40 MHz a CSI line is about 1.2 KB, so 921600 baud carries at most about 77 lines/s and about 20 % of packets were lost (GES-48). At 20 MHz loss was 0.4–1.1 % in the GES-68 check, so the link uses 20 MHz. Channel 11, fixed receiver gain and raw (uncompensated) CSI values are unchanged from the feasibility test.

The receiver can also send its lines over UDP (`receiver/sdkconfig.udp.defaults`, `idf.py menuconfig` → *Gestura CSI receiver*). That mode was used only for the link measurement in GES-48 and is not covered by the contract. Never commit an `sdkconfig` with a network name or password.

## Requirements

- ESP-IDF **v5.5.6** (`idf.py --version`). Other versions are untested.
- Two ESP32-S3 boards. Connect each through its USB-to-UART (CH343) port, not the native USB port.
- The `esp_csi_gain_ctrl` component is downloaded by the IDF Component Manager on the first build; the version is pinned in `receiver/dependencies.lock`.

## Build and flash

Open an ESP-IDF terminal (Windows: *ESP-IDF 5.5 PowerShell*, or run `export.ps1` / `export.sh` from the ESP-IDF folder). Replace `COMx` with the board's port (Device Manager → *Ports*; on Linux or macOS, `/dev/ttyACM0` or similar).

```
cd firmware/sender
idf.py set-target esp32s3
idf.py -p COMx flash

cd ../receiver
idf.py set-target esp32s3
idf.py -p COMy flash monitor
```

`set-target` is only needed once per folder. It creates `sdkconfig` and `build/`; both are ignored by git. Settings that should be kept go into `sdkconfig.defaults`.

After flashing, the sender only needs power (a USB charger is enough). Exit the monitor with `Ctrl+]`.

## Checking the link

With the sender powered and the receiver monitor open you should see, within a second:

1. The banner `================ CSI RECV ================`.
2. The header row from Section 1 of the contract, ending in `rx_state,len,first_word,data`.
3. Lines starting with `CSI_DATA,`, roughly 90 per second. In each line the second field (`id`) increases by one per packet, `bandwidth` (field 8) is `0`, `channel` (field 17) is `11` and `len` (field 23) is `256`.

If no `CSI_DATA` lines appear, check that the sender is powered and was flashed from `firmware/sender`, and that both boards use the same channel and bandwidth. Recordings are never stored in this repository; see the contract, Section 4.
