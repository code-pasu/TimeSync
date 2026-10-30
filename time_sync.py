#This controller acts as a standard learning switch (OpenFlow 1.3), but includes a dynamic policy rule: any UDP packet destined to or coming from port 5005 is placed into Queue 1 (High Priority) via OFPActionSetQueue(1), while all other traffic uses Queue 0 (Default/Low Priority).

import socket
import struct
import time
import argparse

# Pack 3 doubles (T1, T2, T3) = 24 bytes
PACKET_FORMAT = "!ddd"
SYNC_PORT = 5005

def run_server(bind_ip="0.0.0.0", port=SYNC_PORT):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind_ip, port))
    print(f"[Server] Listening for UDP Time Sync on {bind_ip}:{port}...")

    while True:
        data, client_addr = sock.recvfrom(1024)
        t2 = time.time()  # Reference clock arrival time
        if len(data) == 8:
            (t1,) = struct.unpack("!d", data)
            t3 = time.time()  # Reference clock transmit time
            reply = struct.pack(PACKET_FORMAT, t1, t2, t3)
            sock.sendto(reply, client_addr)

def run_client(server_ip, artificial_offset=2.5, count=20, interval=0.5, port=SYNC_PORT):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)

    def local_clock():
        # Simulate a local clock drifted by 'artificial_offset' seconds
        return time.time() + artificial_offset

    print(f"seq,rtt_ms,estimated_offset_s,sync_error_ms")
    for seq in range(1, count + 1):
        t1 = local_clock()
        sock.sendto(struct.pack("!d", t1), (server_ip, port))
        try:
            data, _ = sock.recvfrom(1024)
            t4 = local_clock()
            t1_orig, t2, t3 = struct.unpack(PACKET_FORMAT, data)

            # Calculate Round-Trip Delay (delta) and Offset (theta)
            rtt = (t4 - t1_orig) - (t3 - t2)
            # Offset of Reference Server relative to Client local clock
            est_offset = ((t2 - t1_orig) + (t3 - t4)) / 2.0

            # True offset needed to correct local_clock() is -artificial_offset
            true_offset = -artificial_offset
            sync_error = abs(est_offset - true_offset)

            print(f"{seq},{rtt*1000:.3f},{est_offset:.6f},{sync_error*1000:.3f}")
        except socket.timeout:
            print(f"{seq},TIMEOUT,TIMEOUT,TIMEOUT")

        time.sleep(interval)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["server", "client"], required=True)
    parser.add_argument("--server-ip", default="10.0.0.1")
    parser.add_argument("--port", type=int, default=SYNC_PORT)
    parser.add_argument("--offset", type=float, default=2.5)
    parser.add_argument("--count", type=int, default=30)
    args = parser.parse_args()

    if args.role == "server":
        run_server()
    else:
        run_client(args.server_ip, args.offset, args.count, port=args.port)