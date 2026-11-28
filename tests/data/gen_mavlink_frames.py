"""Sinh khung đối chiếu bằng pymavlink (LGPL, chỉ chạy tay, không phải phụ thuộc của B1):
    pip install pymavlink==2.4.50 && python tests/data/gen_mavlink_frames.py > tests/data/mavlink_pymavlink_frames.json
"""
import json, sys, os
os.environ["MAVLINK20"]="1"
from pymavlink.dialects.v20 import common as m
class F: 
    def __init__(s): s.buf=b""
    def write(s,b): s.buf+=b
f=F(); mav=m.MAVLink(f, srcSystem=42, srcComponent=1)
out={}
def cap(name, msg):
    f.buf=b""; mav.send(msg); out[name]=f.buf.hex()
cap("hb", mav.heartbeat_encode(2,3,128|4|1,3,4))
cap("ss", mav.sys_status_encode(1,2,3,500,47900,-120,76,0,0,0,0,0,0))
cap("st", mav.system_time_encode(1764314401123000, 60000))
cap("gps", mav.gps_raw_int_encode(1764314401123000, 3, 111347056, 1064437417, 30500, 80, 120, 1520, 31800, 17))
cap("gpi", mav.global_position_int_encode(60000, 111347056, 1064437417, 150000, 120000, -1000, 1200, -10, 31800))
cap("vfr", mav.vfr_hud_encode(15.0,15.2,318,40,150.0,0.1))
cap("bat", mav.battery_status_encode(0,1,2,250,[47900]+[65535]*9,-120,300,400,76))
# signed
mav.signing.secret_key=bytes(range(32)); mav.signing.link_id=3; mav.signing.sign_outgoing=True; mav.signing.timestamp=123456789
cap("gpi_signed", mav.global_position_int_encode(60100, 111347056, 1064437417, 150000, 120000, -1000, 1200, -10, 31800))
print(json.dumps(out))
