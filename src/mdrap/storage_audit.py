"""Cryptographic audit trail, Merkle log, and external checkpoint verification.

Part of MDRAP Storage Layer decomposition (Spec §5 & §26).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

from .storage_schema import _compute_merkle_root, _synchronized

__stability__ = "stable"


class StoreAuditMixin:
    """Mixin class providing immutable cryptographic audit and Merkle tree capabilities."""

    @_synchronized
    def write_audit_entry(
        self,
        timestamp: float,
        actor: str,
        role: str,
        action: str,
        details: str,
        prev_hash: str,
        entry_hash: str,
        format_version: int = 2,
    ) -> None:
        self.conn.execute(
            """INSERT INTO audit_log (timestamp, actor, role, action, details, prev_hash, entry_hash, format_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp,
                actor,
                role,
                action,
                details,
                prev_hash,
                entry_hash,
                format_version,
            ),
        )

    @_synchronized
    def get_latest_audit_hash(self) -> str:
        cur = self.conn.execute(
            "SELECT entry_hash FROM audit_log ORDER BY entry_id DESC LIMIT 1"
        )
        row = cur.fetchone()
        return (
            row[0]
            if row
            else "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        )

    @_synchronized
    def query_audit_log(self, limit: int = 50) -> list[dict]:
        limit = min(max(1, limit), 10000)
        cur = self.conn.execute(
            "SELECT * FROM audit_log ORDER BY entry_id DESC LIMIT ?", (limit,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_synchronized
    def append_audit(
        self,
        actor: str,
        role: str,
        action: str,
        details: str,
        timestamp: float | None = None,
        format_version: int = 2,
    ) -> str:
        """Atomic audit log append: reads head, computes hash, inserts, and commits in one transaction."""
        from .audit_format import compute_audit_hash

        ts = timestamp if timestamp is not None else time.time()
        with self.transaction():
            cur = self.conn.execute(
                "SELECT entry_hash FROM audit_log ORDER BY entry_id DESC LIMIT 1"
            )
            row = cur.fetchone()
            prev_hash = (
                row[0]
                if row
                else "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
            )
            entry_hash = compute_audit_hash(
                prev_hash,
                ts,
                actor,
                role,
                action,
                details,
                format_version=format_version,
            )
            self.conn.execute(
                """INSERT INTO audit_log (timestamp, actor, role, action, details, prev_hash, entry_hash, format_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts,
                    actor,
                    role,
                    action,
                    details,
                    prev_hash,
                    entry_hash,
                    format_version,
                ),
            )
        return entry_hash

    def _append_quarantine_merkle_batch_in_tx(
        self, quarantine_rows: list[tuple], timestamp: float | None = None
    ) -> str | None:
        """Internal helper to insert a Merkle root for a quarantine batch within an existing transaction."""
        if not quarantine_rows:
            return None
        from .audit_format import compute_audit_hash

        ts = timestamp if timestamp is not None else time.time()
        leaves = [
            hashlib.sha256(
                json.dumps(row, default=str, sort_keys=True).encode("utf-8")
            ).digest()
            for row in quarantine_rows
        ]
        batch_root = _compute_merkle_root(leaves)
        cur = self.conn.execute(
            "SELECT entry_hash FROM quarantine_merkle_log ORDER BY entry_id DESC LIMIT 1"
        )
        row = cur.fetchone()
        prev_root = (
            row[0]
            if row
            else "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        )
        details = f"n={len(quarantine_rows)},root={batch_root}"
        entry_hash = compute_audit_hash(
            prev_root,
            ts,
            "system",
            "pipeline",
            "QUARANTINE_BATCH",
            details,
            format_version=3,
        )
        self.conn.execute(
            """INSERT INTO quarantine_merkle_log
               (timestamp, entry_hash, prev_root, batch_root, batch_size, format_version)
               VALUES (?, ?, ?, ?, ?, 3)""",
            (ts, entry_hash, prev_root, batch_root, len(quarantine_rows)),
        )
        return entry_hash

    @_synchronized
    def append_quarantine_merkle_batch(
        self, quarantine_rows: list[tuple], timestamp: float | None = None
    ) -> str | None:
        """Append a batched Merkle root record for quarantined records."""
        with self.transaction():
            return self._append_quarantine_merkle_batch_in_tx(
                quarantine_rows, timestamp
            )

    @_synchronized
    def verify_quarantine_merkle_integrity(
        self, since_entry_id: int | None = None
    ) -> tuple[bool, str, int]:
        """Verify the cryptographic hash-chain and batch Merkle roots of the quarantine log."""
        from .audit_format import compute_audit_hash

        if since_entry_id is not None and since_entry_id > 1:
            prev_row = self.conn.execute(
                "SELECT entry_hash FROM quarantine_merkle_log WHERE entry_id = ?",
                (since_entry_id - 1,),
            ).fetchone()
            expected_prev = (
                prev_row[0]
                if prev_row
                else "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
            )
            cur = self.conn.execute(
                "SELECT entry_id, timestamp, entry_hash, prev_root, batch_root, batch_size, format_version "
                "FROM quarantine_merkle_log WHERE entry_id >= ? ORDER BY entry_id ASC",
                (since_entry_id,),
            )
        else:
            expected_prev = "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
            cur = self.conn.execute(
                "SELECT entry_id, timestamp, entry_hash, prev_root, batch_root, batch_size, format_version "
                "FROM quarantine_merkle_log ORDER BY entry_id ASC"
            )
        rows = cur.fetchall()
        if not rows:
            return True, "Quarantine Merkle log is empty (valid)", 0

        for entry_id, ts, entry_hash, prev_root, batch_root, batch_size, f_ver in rows:
            if prev_root != expected_prev:
                return (
                    False,
                    f"Broken Merkle chain link at entry #{entry_id}: expected prev_root '{expected_prev[:12]}...', got '{prev_root[:12]}...'",
                    entry_id,
                )
            details = f"n={batch_size},root={batch_root}"
            recomputed = compute_audit_hash(
                prev_root,
                ts,
                "system",
                "pipeline",
                "QUARANTINE_BATCH",
                details,
                format_version=f_ver or 3,
            )
            if recomputed != entry_hash:
                return (
                    False,
                    f"Tampered quarantine Merkle entry #{entry_id}: hash mismatch",
                    entry_id,
                )
            expected_prev = entry_hash

        last_id = rows[-1][0] if rows else 0
        return (
            True,
            f"Quarantine Merkle log verified ({len(rows)} batches intact)",
            last_id,
        )

    @_synchronized
    def verify_audit_integrity(
        self,
        anchor: tuple[int, str] | None = None,
        since_entry_id: int | None = None,
    ) -> tuple[bool, str, int]:
        from .audit_format import compute_audit_hash

        if since_entry_id is not None and since_entry_id > 1:
            prev_row = self.conn.execute(
                "SELECT entry_hash FROM audit_log WHERE entry_id = ?",
                (since_entry_id - 1,),
            ).fetchone()
            expected_prev = (
                prev_row[0]
                if prev_row
                else "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
            )
            cur = self.conn.execute(
                "SELECT entry_id, timestamp, actor, role, action, details, prev_hash, entry_hash, "
                "COALESCE(format_version, 1) FROM audit_log WHERE entry_id >= ? ORDER BY entry_id ASC",
                (since_entry_id,),
            )
        else:
            expected_prev = "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
            cur = self.conn.execute(
                "SELECT entry_id, timestamp, actor, role, action, details, prev_hash, entry_hash, "
                "COALESCE(format_version, 1) FROM audit_log ORDER BY entry_id ASC"
            )
        rows = cur.fetchall()
        if anchor is not None and since_entry_id is None:
            expected_count, expected_head = anchor
            genesis = "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
            if expected_count < 0:
                return False, "Invalid checkpoint: entry count cannot be negative", 0
            if expected_count == 0 and expected_head != genesis:
                return False, "Checkpoint genesis hash mismatch", 0
            if len(rows) < expected_count:
                return (
                    False,
                    f"Tail truncation detected: expected {expected_count} entries, got {len(rows)}",
                    len(rows),
                )
            if expected_count and rows[expected_count - 1][7] != expected_head:
                return (
                    False,
                    f"Checkpoint prefix mismatch at entry #{expected_count}: expected '{expected_head[:12]}...', got '{rows[expected_count - 1][7][:12]}...'",
                    expected_count,
                )

        if not rows:
            return True, "Audit log is empty (valid)", 0

        for entry_id, ts, actor, role, action, details, prev_h, entry_h, f_ver in rows:
            if prev_h != expected_prev:
                return (
                    False,
                    f"Broken chain link at entry #{entry_id}: expected prev_hash '{expected_prev[:12]}...', got '{prev_h[:12]}...'",
                    entry_id,
                )
            recomputed_hash = compute_audit_hash(
                prev_h, ts, actor, role, action, details, format_version=f_ver
            )
            if recomputed_hash != entry_h:
                return (
                    False,
                    f"Tampered entry #{entry_id}: hash mismatch (stored '{entry_h[:12]}...', calculated '{recomputed_hash[:12]}...')",
                    entry_id,
                )
            expected_prev = entry_h

        last_id = rows[-1][0] if rows else 0
        return (
            True,
            (
                f"Unanchored audit chain integrity verified ({len(rows)} entries intact); "
                "historical authenticity is not established"
                if anchor is None
                else f"Anchored audit chain verified ({len(rows)} entries intact)"
            ),
            last_id,
        )

    @_synchronized
    def get_audit_anchor(self) -> tuple[int, str]:
        """Return the current audit log anchor: (entry_count, head_hash)."""
        cur = self.conn.execute(
            "SELECT count(*), COALESCE((SELECT entry_hash FROM audit_log ORDER BY entry_id DESC LIMIT 1), 'GENESIS_0000000000000000000000000000000000000000000000000000000000000000') FROM audit_log"
        )
        row = cur.fetchone()
        return (
            (row[0], row[1])
            if row
            else (
                0,
                "GENESIS_0000000000000000000000000000000000000000000000000000000000000000",
            )
        )

    @_synchronized
    def sign_audit_checkpoint(self, secret_key: bytes | str | None = None) -> dict:
        """Sign current audit log state with HMAC-SHA256."""
        if secret_key is None:
            secret_key = os.environ.get("MDRAP_AUDIT_KEY", "")
        if isinstance(secret_key, str):
            secret_key = secret_key.encode("utf-8")
        if not secret_key:
            raise ValueError(
                "MDRAP_AUDIT_KEY or secret_key required for audit checkpoint signing"
            )
        count, head = self.get_audit_anchor()
        msg = f"{count}:{head}".encode("utf-8")
        sig = hmac.new(secret_key, msg, hashlib.sha256).hexdigest()
        return {"count": count, "head": head, "signature": sig}

    @staticmethod
    def verify_audit_checkpoint(
        checkpoint: dict, secret_key: bytes | str | None = None
    ) -> bool:
        """Verify HMAC-SHA256 signature on an audit checkpoint."""
        if secret_key is None:
            secret_key = os.environ.get("MDRAP_AUDIT_KEY", "")
        if isinstance(secret_key, str):
            secret_key = secret_key.encode("utf-8")
        if not secret_key:
            return False
        count = checkpoint.get("count", 0)
        head = checkpoint.get("head", "")
        expected_sig = checkpoint.get("signature", "")
        msg = f"{count}:{head}".encode("utf-8")
        computed_sig = hmac.new(secret_key, msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed_sig, expected_sig)

    @_synchronized
    def export_audit_checkpoint(
        self, output_file: str, secret_key: bytes | str | None = None
    ) -> dict:
        """Export signed audit checkpoint to an external file for off-box tamper detection (Phase 4)."""
        cp = self.sign_audit_checkpoint(secret_key=secret_key)
        dirpath = os.path.dirname(os.path.abspath(output_file))
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(cp, f, indent=2)
        return cp

    @_synchronized
    def verify_external_checkpoint(
        self, checkpoint_source: str | dict, secret_key: bytes | str | None = None
    ) -> tuple[bool, str]:
        """Verify external signed checkpoint against the local audit log (Phase 4)."""
        if isinstance(checkpoint_source, str):
            if not os.path.exists(checkpoint_source):
                return False, f"Checkpoint file not found: {checkpoint_source}"
            with open(checkpoint_source, "r", encoding="utf-8") as f:
                cp = json.load(f)
        else:
            cp = checkpoint_source

        if not self.verify_audit_checkpoint(cp, secret_key=secret_key):
            return (
                False,
                "Checkpoint signature verification failed (forged or wrong secret)",
            )

        count = int(cp.get("count", 0))
        head = str(cp.get("head", ""))
        valid, msg, _ = self.verify_audit_integrity(anchor=(count, head))
        if not valid:
            return False, f"Audit trail diverges from external checkpoint: {msg}"
        return True, "Audit log verified against signed external checkpoint"

    @_synchronized
    def export_audit_proof(self, output_file: str | None = None) -> dict:
        """Export cryptographic audit trail as an independently verifiable JSON proof."""
        cur = self.conn.execute(
            "SELECT entry_id, timestamp, actor, role, action, details, prev_hash, entry_hash, "
            "COALESCE(format_version, 1) FROM audit_log ORDER BY entry_id ASC"
        )
        cols = [
            "entry_id",
            "timestamp",
            "actor",
            "role",
            "action",
            "details",
            "prev_hash",
            "entry_hash",
            "format_version",
        ]
        entries = [dict(zip(cols, r)) for r in cur.fetchall()]

        latest_hash = (
            entries[-1]["entry_hash"]
            if entries
            else "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        )
        proof = {
            "version": "2.0.0",
            "specification": "MDRAP-Spec-19.3",
            "algorithm": "sha256",
            "genesis_hash": "GENESIS_0000000000000000000000000000000000000000000000000000000000000000",
            "total_entries": len(entries),
            "latest_hash": latest_hash,
            "entries": entries,
        }
        if output_file:
            dir_name = os.path.dirname(os.path.abspath(output_file))
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            tmp_file = f"{output_file}.tmp.{os.getpid()}"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(proof, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, output_file)
        return proof

    @staticmethod
    def verify_standalone_proof(
        proof_data_or_path: Any, anchor: tuple[int, str] | None = None
    ) -> tuple[bool, str, int]:
        """Independently verify a JSON audit proof without database access."""
        from .audit_format import compute_audit_hash

        if isinstance(proof_data_or_path, str):
            with open(proof_data_or_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = proof_data_or_path

        entries = data.get("entries", [])
        genesis_hash = data.get(
            "genesis_hash",
            "GENESIS_0000000000000000000000000000000000000000000000000000000000000000",
        )
        expected_latest = entries[-1]["entry_hash"] if entries else genesis_hash
        declared_latest = data.get("latest_hash")
        declared_total = data.get("total_entries")

        if declared_latest is not None and declared_latest != expected_latest:
            return (
                False,
                f"Latest hash mismatch: declared '{declared_latest}', calculated '{expected_latest}'",
                0,
            )
        if declared_total is not None and declared_total != len(entries):
            return (
                False,
                f"Total entries mismatch: declared {declared_total}, actual {len(entries)}",
                0,
            )

        if not entries:
            if anchor is not None and anchor[0] == 0:
                return True, "Audit proof is empty (valid by anchor)", 0
            return False, "Audit proof is empty (invalid without confirmed anchor)", 0

        if anchor is not None:
            expected_count, expected_head = anchor
            if len(entries) != expected_count:
                return (
                    False,
                    f"Anchor count mismatch: expected {expected_count}, got {len(entries)}",
                    len(entries),
                )
            if entries[-1]["entry_hash"] != expected_head:
                return (
                    False,
                    f"Anchor head hash mismatch: expected '{expected_head[:12]}...', got '{entries[-1]['entry_hash'][:12]}...'",
                    len(entries),
                )

        expected_prev = genesis_hash
        for item in entries:
            entry_id = item["entry_id"]
            prev_h = item["prev_hash"]
            entry_h = item["entry_hash"]
            f_ver = item.get("format_version", 1)

            if prev_h != expected_prev:
                return (
                    False,
                    f"Broken chain link at entry #{entry_id}: expected prev '{expected_prev[:12]}...', got '{prev_h[:12]}...'",
                    entry_id,
                )

            calc_hash = compute_audit_hash(
                prev_h,
                item["timestamp"],
                item["actor"],
                item["role"],
                item["action"],
                item["details"],
                format_version=f_ver,
            )
            if calc_hash != entry_h:
                return (
                    False,
                    f"Tampered entry #{entry_id}: hash mismatch (stored '{entry_h[:12]}...', calculated '{calc_hash[:12]}...')",
                    entry_id,
                )

            expected_prev = entry_h

        return (
            True,
            f"Independent cryptographic audit proof verified ({len(entries)} entries intact)",
            len(entries),
        )
