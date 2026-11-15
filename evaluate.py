# evaluate.py
import csv
import statistics
import matplotlib.pyplot as plt

FILES = [
    ("results_baseline_no_qos.csv", "Static Baseline (No QoS)", "#e74c3c", "--"),
    ("results_dynamic_sdn.csv", "Dynamic Reactive SDN (Ours)", "#2980b9", "-"),
    ("results_proactive_qos.csv", "Proactive SDN QoS", "#27ae60", "-."),
]

def load_metrics(filename):
    seqs, rtts, errors = [], [], []
    drops = 0
    with open(filename, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            seq = int(row["seq"])
            if row["status"] == "DROPPED" or row["rtt_ms"] == "TIMEOUT":
                drops += 1
                continue
            # Skip seq 1 cold-start ARP packet for steady-state comparison
            if seq > 1:
                seqs.append(seq)
                rtts.append(float(row["rtt_ms"]))
                errors.append(float(row["sync_error_ms"]))
    return seqs, rtts, errors, drops

def main():
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    print(f"\n{'Regime':<30} | {'Mean RTT (ms)':<14} | {'Mean Error (ms)':<16} | {'Max Error (ms)':<15} | {'Drops'}")
    print("-" * 90)

    for fname, label, color, style in FILES:
        try:
            seqs, rtts, errors, drops = load_metrics(fname)
            mean_rtt = statistics.mean(rtts) if rtts else 0
            mean_err = statistics.mean(errors) if errors else 0
            max_err = max(errors) if errors else 0
            print(f"{label:<30} | {mean_rtt:<14.3f} | {mean_err:<16.3f} | {max_err:<15.3f} | {drops}")

            ax1.plot(seqs, rtts, label=label, color=color, linestyle=style, linewidth=2, marker="o", markersize=4)
            ax2.plot(seqs, errors, label=label, color=color, linestyle=style, linewidth=2, marker="s", markersize=4)
        except FileNotFoundError:
            print(f"[Warning] {fname} not found. Run: sudo .venv/bin/python topology.py --auto-eval first.")

    # Highlight congestion window (packets 8 to 26)
    for ax in (ax1, ax2):
        ax.axvspan(8, 26, color="orange", alpha=0.15, label="15 Mbps UDP Congestion Window")
        ax.grid(True, linestyle=":", alpha=0.6)
        ax.legend(loc="upper right")

    ax1.set_ylabel("Round-Trip Delay (ms)")
    ax1.set_title("Deliverable 2 Performance Evaluation: Static vs. Dynamic SDN Clock Synchronization")
    ax2.set_ylabel("Synchronization Error (ms)")
    ax2.set_xlabel("UDP Sync Packet Sequence Number")

    plt.tight_layout()
    plt.savefig("evaluation.png", dpi=300)
    print("\nSaved comparison plot to: evaluation.png")

if __name__ == "__main__":
    main()