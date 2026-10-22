# Items Excluded from MDRAP Demo Video (Unverified or Non-Production)

Per the strict credibility rules of the MDRAP project, the following items were intentionally excluded from the video presentation:

1. **Regulatory Compliance Certification (SEC 613 CAT / MiFID II RTS 25)**:
   - *Status*: The codebase produces timestamped audit trails and TCA metrics shaped around these regulatory concepts, but MDRAP is open-source software and does not confer legal or regulatory certification.
   - *Action*: Excluded from claims.

2. **Distributed Multi-Datacenter High Availability / Raft Consensus**:
   - *Status*: The engine currently operates as a high-throughput Single-Node Sidecar (Mode A). Distributed multi-host failover is on the roadmap (Mode B).
   - *Action*: Excluded from production claims; presented strictly as single-node IPC sidecar.

3. **AF_XDP Kernel Bypass & Hardware PTP NIC Timestamping**:
   - *Status*: AF_XDP experimental receiver exists in `src/mdrap/af_xdp.py`, but requires Linux kernel $\ge 5.4$ with XDP-capable NIC drivers (e.g. Intel i40e/ixgbe) not present on standard workstation environments.
   - *Action*: Excluded from demo video; documented under `docs/roadmap.md`.

4. **FPGA Acceleration**:
   - *Status*: No production Verilog/VHDL bitstreams exist in the repository.
   - *Action*: Completely omitted.

5. **Colocation & Ultra-Low-Latency Fiber Routes**:
   - *Status*: MDRAP is a software sidecar. Physical network topologies and cross-connect latency belong to the exchange participant's hosting infrastructure.
   - *Action*: Excluded.
