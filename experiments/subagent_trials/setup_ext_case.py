"""One-time setup for the escrow_trade_ext case: extend the base escrow
protocol with the SettlementAudit child via the REAL incremental pipeline
(stjp_core/compiler/incremental.py) and commit the resulting artifacts —
composed protocol, per-role contracts, standalone monitors — so the trials
run against exactly what the pipeline produced.

    python experiments/subagent_trials/setup_ext_case.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from stjp_core.compiler.incremental import add_subprotocol   # noqa: E402
from cases import BASE_PROTOCOL, AUDIT_CHILD                 # noqa: E402


def main() -> int:
    out = HERE / "protocols"
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "escrow_trade.scr").write_text(BASE_PROTOCOL, encoding="utf-8")
        (d / "settlement_audit.scr").write_text(AUDIT_CHILD, encoding="utf-8")
        result = add_subprotocol(
            d / "escrow_trade.scr", d / "settlement_audit.scr",
            ["Escrow", "Auditor"], anchor="after:ConfirmReceipt",
            output_dir=d)
        print(result.summary())
        if not result.success:
            print(result.error)
            return 1
        shutil.copy(result.composed_path,
                    out / "escrow_trade_ext_composed.scr")
        gen = d / "generated"
        if gen.exists():
            dst = out / "generated"
            dst.mkdir(exist_ok=True)
            for f in gen.iterdir():
                shutil.copy(f, dst / f.name)
                print(f"  artifact: {f.name}")
        print(f"wrote {out / 'escrow_trade_ext_composed.scr'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
