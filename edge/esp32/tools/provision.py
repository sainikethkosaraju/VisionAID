#!/usr/bin/env python3
"""Generate the per-device NVS image holding Wi-Fi credentials and the device token.

Secrets are read from the environment or prompted — never passed on argv — and are
written only to the output image, which must be flashed and then deleted.

    export VISIONAID_WIFI_PASS=... VISIONAID_DEVICE_TOKEN=...
    python provision.py --ssid CareHome-Staff --api-base https://api.example.org/api/v1 \
        --out nvs_dev101.bin
    python -m esptool --chip esp32s3 write_flash 0x9000 nvs_dev101.bin && shred -u nvs_dev101.bin

Requires the ESP-IDF python env (esp-idf-nvs-partition-gen). Production: generate with
--encrypt (NVS encryption) and enable flash encryption; see docs/SECURITY.md.
"""

import argparse
import csv
import getpass
import os
import subprocess
import sys
import tempfile

NVS_SIZE = "0x6000"  # must match partitions.csv


def secret(env: str, prompt: str) -> str:
    return os.environ.get(env) or getpass.getpass(prompt)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ssid", required=True)
    ap.add_argument("--api-base", required=True)
    ap.add_argument("--buffer-seconds", type=int, default=60)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not args.api_base.startswith("https://"):
        sys.exit("api-base must be https://")
    token = secret("VISIONAID_DEVICE_TOKEN", "Device token (shown once at registration): ")
    if "." not in token:
        sys.exit("device token must look like '<device_uuid>.<secret>'")
    wifi_pass = secret("VISIONAID_WIFI_PASS", "Wi-Fi password: ")

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "type", "encoding", "value"])
        w.writerow(["visionaid", "namespace", "", ""])
        w.writerow(["wifi_ssid", "data", "string", args.ssid])
        w.writerow(["wifi_pass", "data", "string", wifi_pass])
        w.writerow(["api_base", "data", "string", args.api_base])
        w.writerow(["device_token", "data", "string", token])
        w.writerow(["buffer_s", "data", "u32", str(args.buffer_seconds)])
        csv_path = f.name
    try:
        subprocess.run([sys.executable, "-m", "esp_idf_nvs_partition_gen", "generate",
                        csv_path, args.out, NVS_SIZE], check=True)
    finally:
        os.remove(csv_path)  # plaintext secrets never persist
    print(f"wrote {args.out}; flash at 0x9000 then delete it")


if __name__ == "__main__":
    main()
