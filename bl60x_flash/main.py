from serial import Serial
from tqdm import tqdm
import binascii
import hashlib
import struct
import time
import sys
import os

def if_read(ser, data_len):
    data = bytearray(0)
    received = 0
    while received < data_len:
        tmp = ser.read(data_len - received)
        if len(tmp) == 0:
            break
        else:
            data += tmp
            received += len(tmp)

    if len(data) != data_len:
        return (0, data)
    return (1, data)

def reset(ser):
    ser.setRTS(0)
    time.sleep(0.2)
    reset_cnt = 2
    while reset_cnt > 0:
        ser.setRTS(1)
        time.sleep(0.005)
        ser.setRTS(0)
        time.sleep(0.1)
        ser.setRTS(1)
        time.sleep(0.005)
        ser.setRTS(0)
        time.sleep(0.005)
        reset_cnt -= 1

def handshake(ser):
    ser.setRTS(1)
    time.sleep(0.2)
    ser.setRTS(0)
    time.sleep(0.05)
    ser.setRTS(1)
    ser.setDTR(1)
    time.sleep(0.1)
    ser.setDTR(0)
    time.sleep(0.1)

def expect_ok(ser):
    data = ser.read(2)
    if data[0] != 0x4f or data[1] != 0x4b:
        err = ser.read(2)
        raise ValueError(binascii.hexlify(err))

def expect_data(ser):
    expect_ok(ser)
    len = ser.read(2)
    len = struct.unpack('<h', len)[0]
    data = ser.read(len)
    return data

def cmd_load_seg_header(ser, file):
    header = file.read(0x10)
    ser.write(b'\x17\x00\x10\x00' + header)
    data = expect_data(ser)
    seg_addr, seg_len = struct.unpack('<II', data[0:8])
    print(f'{seg_len} bytes @ {hex(seg_addr)}')
    return seg_len

def cmd_load_seg_data(ser, data):
    ser.write(b'\x18\x00' + struct.pack('<H', len(data)) + data)
    expect_ok(ser)

def cmd_load_boot_header(ser, file):
    header = file.read(0xb0)
    ser.write(b'\x11\x00\xb0\x00' + header)
    expect_ok(ser)

def cmd_check_image(ser):
    ser.write(b'\x19\x00\x00\x00')
    expect_ok(ser)

def cmd_run_image(ser):
    ser.write(b'\x1a\x00\x00\x00')
    expect_ok(ser)

def load_image(ser, file):
    image = open(file, 'rb')
    cmd_load_boot_header(ser, image)
    total = cmd_load_seg_header(ser, image)
    sent = 0
    with tqdm(total=total, unit='byte', unit_scale=True) as pbar:
        while sent != total:
            chunk = image.read(min(total-sent, 4080))
            cmd_load_seg_data(ser, chunk)
            sent = sent + len(chunk)
            pbar.update(len(chunk))
    cmd_check_image(ser)
    cmd_run_image(ser)

def empty_buffer(ser):
    timeout = ser.timeout
    ser.timeout = 0.1
    if_read(ser, 10000)
    ser.timeout = timeout

def send_sync(ser):
    empty_buffer(ser)
    ser.write(b'\x55' * int(0.006 * ser.baudrate / 10))
    expect_ok(ser)

def efl_write_cmd(ser, id, payload = b''):
    plen = len(payload)
    plen_data = struct.pack('<h', plen)
    checksum = struct.pack('<h', sum(plen_data + payload) & 0xff)[0:1]
    data = bytes([id]) + checksum + plen_data + payload
    ser.write(data)

def efl_cmd_read_memory(ser, addr):
    # there is a length parameter here but it doesn't seem to work correctly
    efl_write_cmd(ser, 0x51, struct.pack('<II', addr, 0x4))
    return expect_data(ser)

def efl_cmd_read_jid(ser):
    efl_write_cmd(ser, 0x36)
    return expect_data(ser)

def efl_cmd_flash_erase(ser, addr, len):
    end_addr = addr + len - 1
    efl_write_cmd(ser, 0x30, struct.pack('<II', addr, end_addr))
    timeout = ser.timeout
    ser.timeout = 10.0
    expect_ok(ser)
    ser.timeout = timeout
    print(f'Erased {len} bytes @ {hex(addr)}')

def efl_cmd_flash_write(ser, addr, data):
    efl_write_cmd(ser, 0x31, struct.pack('<I', addr) + data)
    expect_ok(ser)

def efl_cmd_flash_write_check(ser):
    efl_write_cmd(ser, 0x3a)
    expect_ok(ser)

def efl_cmd_flash_xip_read_start(ser):
    efl_write_cmd(ser, 0x60)
    expect_ok(ser)

def efl_cmd_flash_xip_read_sha(ser, addr, len):
    efl_write_cmd(ser, 0x3e, struct.pack('<II', addr, len))
    return expect_data(ser)

def efl_cmd_flash_xip_read_finish(ser):
    efl_write_cmd(ser, 0x61)
    expect_ok(ser)

def efl_program_img(ser, addr, data):
    data_len = len(data)
    efl_cmd_flash_erase(ser, addr, data_len)

    print(f'Programming {data_len} bytes @ {hex(addr)}')
    sent = 0
    with tqdm(total=data_len, unit='byte', unit_scale=True) as pbar:
        while sent != data_len:
            buf_len = min(2048, data_len - sent)
            buf = data[sent:sent + buf_len]
            efl_cmd_flash_write(ser, addr + sent, buf)
            sent = sent + buf_len
            pbar.update(buf_len)
    efl_cmd_flash_write_check(ser)

    sha256sum = hashlib.sha256(data).digest()
    efl_cmd_flash_xip_read_start(ser)
    device_sum = efl_cmd_flash_xip_read_sha(ser, addr, data_len)
    efl_cmd_flash_xip_read_finish(ser)
    if device_sum != sha256sum:
        print('Verification failed')
        print('Host SHA256:', binascii.hexlify(sha256sum))
        print('BL   SHA256:', binascii.hexlify(device_sum))
        return False
    print('Verified by XIP SHA256 hash')
    return True

def prepend_fw_header(img, header_file):
    if img[0:4] == 'BFNP':
        print('Image already has FW header')
        return
    with open(header_file, 'rb') as f:
        header = f.read()
    img = header + (b'\xFF' * (4096-len(header))) + img
    return img

def get_contrib_path(name):
    sep = os.path.sep
    return os.path.dirname(os.path.realpath(__file__)) + sep + 'contrib' + sep + name

def main():
    if len(sys.argv) < 3:
        print(f'Usage: {sys.argv[0]} <serial port> <firmware bin>')
        sys.exit(1)

    ser = Serial(sys.argv[1], baudrate=500000, timeout=2)
    handshake(ser)
    reset(ser)
    send_sync(ser)
    time.sleep(0.1)
    print('Loading helper binary')
    load_image(ser, get_contrib_path('eflash_loader_40m.bin'))
    time.sleep(0.2)
    print()

    # at this point, the eflash loader binary is running with efl_ commands
    # (which seems to work with a higher baudrate)
    ser.baudrate = 2000000
    send_sync(ser)
    with open(sys.argv[2], 'rb') as f:
        data = f.read()
    data = prepend_fw_header(data, get_contrib_path('bootheader.bin'))
    efl_program_img(ser, 0x10000, data)

if __name__ == "__main__":
    main()
