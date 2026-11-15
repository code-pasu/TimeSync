import socket
import struct
import time
import json
import argparse
import csv

PACKET_FORMAT = "!ddd"
SYNC_PORT = 5005
TELEMETRY_PORT = 9090

def run_server(bind_ip="0.0.0.0", port=SYNC_PORT):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind_ip, port))
    print(f"[Server] Reference Clock listening on UDP {bind_ip}:{port}...")

    while True:
        data, client_addr = sock.recvfrom(1024)
        t2 = time.time()  # Reference arrival timestamp
        if len(data) == 8:
            (t1,) = struct.unpack("!d", data)
            t3 = time.time()  # Reference transmit timestamp
            reply = struct.pack(PACKET_FORMAT, t1, t2, t3)
            sock.sendto(reply, client_addr)

def send_tcp_telemetry(controller_ip, payload):
    """Sends real-time monitoring metrics over TCP to the SDN Controller."""
    if not controller_ip:
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_sock:
            tcp_sock.settimeout(0.3)
            tcp_sock.connect((controller_ip, TELEMETRY_PORT))
            tcp_sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
    except Exception:
        pass  # Non-blocking if telemetry listener is disabled

def run_client(client_id, server_ip, artificial_offset=2.5, count=40, interval=0.5,
               telemetry_ip=None, csv_file=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.5)

    def local_clock():
        return time.time() + artificial_offset

    rows = []
    print("seq,client_id,rtt_ms,estimated_offset_s,sync_error_ms,corrected_drift_ms,status")

    for seq in range(1, count + 1):
        t1 = local_clock()
        sock.sendto(struct.pack("!d", t1), (server_ip, SYNC_PORT))
        try:
            data, _ = sock.recvfrom(1024)
            t4 = local_clock()
            t1_orig, t2, t3 = struct.unpack(PACKET_FORMAT, data)

            # 1. Round-Trip Delay (delta) and Estimated Offset (theta)
            rtt_s = (t4 - t1_orig) - (t3 - t2)
            est_offset_s = ((t2 - t1_orig) + (t3 - t4)) / 2.0

            # 2. Synchronization Error against ground-truth artificial offset
            true_offset_s = -artificial_offset
            sync_error_ms = abs(est_offset_s - true_offset_s) * 1000.0
            rtt_ms = rtt_s * 1000.0

            # 3. Adaptive Clock Correction: residual drift if client applies est_offset_s
            corrected_clock = local_clock() + est_offset_s
            residual_drift_ms = abs(corrected_clock - time.time()) * 1000.0

            status = "OK"
            print(f"{seq},{client_id},{rtt_ms:.3f},{est_offset_s:.6f},{sync_error_ms:.3f},{residual_drift_ms:.3f},{status}")
            rows.append([seq, client_id, f"{rtt_ms:.3f}", f"{est_offset_s:.6f}", f"{sync_error_ms:.3f}", f"{residual_drift_ms:.3f}", status])

            # 4. Report metrics via TCP to SDN Controller for Dynamic Policy Enforcement
            send_tcp_telemetry(telemetry_ip, {
                "seq": seq,
                "client_id": client_id,
                "rtt_ms": round(rtt_ms, 3),
                "sync_error_ms": round(sync_error_ms, 3)
            })

        except socket.timeout:
            print(f"{seq},{client_id},TIMEOUT,TIMEOUT,TIMEOUT,TIMEOUT,DROPPED")
            rows.append([seq, client_id, "TIMEOUT", "TIMEOUT", "TIMEOUT", "TIMEOUT", "DROPPED"])
            send_tcp_telemetry(telemetry_ip, {
                "seq": seq,
                "client_id": client_id,
                "rtt_ms": 999.0,
                "sync_error_ms": 999.0
            })

        time.sleep(interval)

    if csv_file:
        with open(csv_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["seq", "client_id", "rtt_ms", "estimated_offset_s", "sync_error_ms", "corrected_drift_ms", "status"])
            writer.writerows(rows)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["server", "client"], required=True)
    parser.add_argument("--client-id", default="h2")
    parser.add_argument("--server-ip", default="10.0.0.1")
    parser.add_argument("--offset", type=float, default=2.5)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--telemetry-ip", default=None, help="TCP Monitoring Server IP")
    parser.add_argument("--save-csv", default=None, help="Output CSV filename")
    args = parser.parse_args()

    if args.role == "server":
        run_server()
    else:
        run_client(args.client_id, args.server_ip, args.offset, args.count,
                   args.interval, args.telemetry_ip, args.save_csv)