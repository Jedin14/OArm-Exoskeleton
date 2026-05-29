#!/usr/bin/env python3
import usb.core
import usb.util
import time
import struct
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

# Vendor / Product
VID = 0x04D8
PID = 0x0053

# Endpoints
EP_CMD_OUT = 0x01
EP_CMD_IN = 0x81
EP_CH1_TX = 0x02

# Magic and commands
PKG_MAGIC = 0xBEAD
CMD_OPEN = 0x8001
CMD_GET_INFO = 0x8005
CMD_INIT_CAN = 0x8002
CMD_SET_BAUD = 0x800B
CMD_START_CAN = 0x8003
CMD_TRANSMIT = 0x8004

def calc_checksum(datalen, cmd, data):
    pkgall = 1
    pkgcur = 1
    csum = datalen + 2 * cmd + (pkgall << 8) + (pkgcur << 8)
    for i in range(0, datalen - 1, 2):
        csum += (data[i] << 8) | data[i+1]
    csum += PKG_MAGIC
    return csum & 0xFFFF

def build_pkt(cmd, data):
    datalen = len(data)
    pkt = bytearray(8 + datalen + 4)
    pkt[0:2] = b'\xbe\xef'
    pkt[2:4] = struct.pack(">H", datalen)
    pkt[4] = 1
    pkt[5] = 1
    pkt[6:8] = struct.pack(">H", cmd)
    if datalen > 0:
        pkt[8:8+datalen] = data
    
    csum = calc_checksum(datalen, cmd, data)
    pkt[-4:-2] = struct.pack(">H", csum)
    pkt[-2:] = b'\xde\xad'
    return pkt

def send_cmd(dev, cmd, data, ep=EP_CMD_OUT):
    pkt = build_pkt(cmd, data)
    dev.write(ep, pkt, timeout=1000)

def recv_resp(dev):
    for _ in range(20):
        try:
            resp = dev.read(EP_CMD_IN, 512, timeout=100)
            if len(resp) >= 2 and resp[0] == 0xBE and resp[1] == 0xEF:
                return resp
        except usb.core.USBError:
            continue
    return None

def init_device(dev):
    print("[+] Performing AES handshake...")
    chal = bytearray(32)
    chal[0:2] = b'\xde\xff'
    chal[6:8] = b'\x43\x01'
    chal[8:12] = b'\xf2\x89\x82\xee'
    chal[16:22] = b'\xd0\x1f\x07\x11\x5f\x68'
    chal[26:32] = b'\xc2\x3e\xc8\x26\x52\x36'
    
    send_cmd(dev, CMD_OPEN, chal)
    resp = recv_resp(dev)
    if resp:
        print("    Handshake OK")
    else:
        print("    Handshake failed / no response (IGNORING and continuing)")

def init_can(dev, ch):
    print(f"[+] Initializing CH{ch}...")
    data = bytearray(32)
    data[0] = 0x55
    data[1] = 0x02
    data[2] = ch
    data[3] = 0x01
    data[9] = 0x01
    data[10:14] = b'\xff\xff\xff\xff'
    # 1Mbps
    data[18:22] = b'\x00\x02\x2e\x0b'
    data[22:26] = b'\x03\x01\x0a\x02'
    
    send_cmd(dev, CMD_INIT_CAN, data)
    recv_resp(dev)

def start_can(dev, ch):
    print(f"[+] Starting CH{ch}...")
    # baud 1M
    baud_data = bytearray([0x7E, ch, 0x00, 0x7F])
    send_cmd(dev, CMD_SET_BAUD, baud_data)
    recv_resp(dev)
    
    start_data = bytearray([0x55, 0x80, 0x03, ch])
    send_cmd(dev, CMD_START_CAN, start_data)
    recv_resp(dev)

def test_tx(dev, ep, label, mod_idx=None, mod_val=None):
    tx = bytearray(26)
    tx[0] = 0x55
    tx[1] = 0xF1  # classic CAN
    tx[8:10] = struct.pack(">H", 0x111)  # ID 0x111
    tx[10] = 4    # DLC = 4
    tx[11:15] = b'\xde\xad\xbe\xef'
    tx[21] = 0x00 # auto-retry
    
    if mod_idx is not None:
        tx[mod_idx] = mod_val
        
    print(f"\n--- Testing {label} ---")
    if mod_idx is not None:
        print(f"    Modifying byte [{mod_idx}] = 0x{mod_val:02X}")
    print(f"    Sending on Endpoint: 0x{ep:02X}")
    print("    WATCH THE LEDS NOW! (Sending 50 frames)")
    
    for _ in range(50):
        try:
            send_cmd(dev, CMD_TRANSMIT, tx, ep=ep)
        except Exception:
            pass
        time.sleep(0.01)
        
    ans = input("    Which LED blinked? (1 for CAN1, 2 for CAN2, 0 for None): ").strip()
    return ans

def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Device not found")
        return
        
    if dev.is_kernel_driver_active(0):
        print("Detaching kernel driver...")
        dev.detach_kernel_driver(0)
        
    dev.set_configuration()
    time.sleep(1.5)
    
    init_device(dev)
    init_can(dev, 0)
    init_can(dev, 1)
    start_can(dev, 0)
    start_can(dev, 1)
    print("\nDevice initialized successfully for both channels at 1Mbps.")
    print("Both arms/buses should be connected.")
    
    print("\n=== Baseline Test ===")
    test_tx(dev, EP_CMD_OUT, "Baseline EP1 (CH0 standard)")
    test_tx(dev, EP_CH1_TX, "Baseline EP2 (CH1 standard in v0.5.1)")
    
    print("\n=== Byte Modification Test ===")
    test_tx(dev, EP_CMD_OUT, "EP1 with byte[2] = 1", mod_idx=2, mod_val=1)
    test_tx(dev, EP_CH1_TX, "EP2 with byte[2] = 1", mod_idx=2, mod_val=1)
    test_tx(dev, EP_CH1_TX, "EP2 with byte[3] = 1", mod_idx=3, mod_val=1)
    test_tx(dev, EP_CH1_TX, "EP2 with tx_type (byte 21) = 1", mod_idx=21, mod_val=1)

if __name__ == "__main__":
    main()
