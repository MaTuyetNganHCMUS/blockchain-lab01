"""Generate the two-page TV5 safety and testing report.

The report is intentionally generated from source so the committed PDF can be
reproduced after test results or references change.
"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = ROOT / "logs" / "t8" / "results.json"
OUTPUT_PATH = ROOT / "output" / "pdf" / "tv5-locking-byzantine-report.pdf"

NAVY = colors.HexColor("#17324D")
BLUE = colors.HexColor("#23699B")
TEXT = colors.HexColor("#263544")
MUTED = colors.HexColor("#617283")
GRID = colors.HexColor("#B8C9D8")
ALT = colors.HexColor("#F2F6F9")
PASS = colors.HexColor("#E2F2E8")
PARTIAL = colors.HexColor("#FFF2CC")
NOT_RUN = colors.HexColor("#F2F2F2")

# Emit compressed binary streams so Git treats the generated PDF as an artifact
# rather than attempting line-oriented diffs against its internal objects.
rl_config.useA85 = 0


def _paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def _table(
    rows: list[list[object]],
    widths: list[float],
    *,
    header: bool = True,
    font_size: float = 7.2,
    row_backgrounds: dict[int, colors.Color] | None = None,
) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands: list[tuple] = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.45, GRID),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("TEXTCOLOR", (0, 0), (-1, -1), TEXT),
    ]
    if header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
        for row in range(1, len(rows)):
            if row % 2 == 0:
                commands.append(("BACKGROUND", (0, row), (-1, row), ALT))
    if row_backgrounds:
        for row, background in row_backgrounds.items():
            commands.append(("BACKGROUND", (-1, row), (-1, row), background))
            commands.append(("FONTNAME", (-1, row), (-1, row), "Helvetica-Bold"))
    table.setStyle(TableStyle(commands))
    return table


def _draw_page(canvas, doc) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 16 * mm, width, 16 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 8.5)
    canvas.drawString(18 * mm, height - 10.2 * mm, "BLOCKCHAIN LAB 01  |  TV5 TECHNICAL REPORT")

    canvas.setStrokeColor(GRID)
    canvas.setLineWidth(0.4)
    canvas.line(17 * mm, 14 * mm, width - 17 * mm, 14 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(17 * mm, 9.2 * mm, "Minimal Layer 1 Blockchain & BFT Consensus Simulator")
    page_label = f"Page {doc.page}"
    canvas.drawString(
        width - 17 * mm - stringWidth(page_label, "Helvetica", 7.5),
        9.2 * mm,
        page_label,
    )
    canvas.restoreState()


def build_report(output_path: Path = OUTPUT_PATH) -> Path:
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "TV5Title",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=19,
        leading=21,
        textColor=NAVY,
        alignment=TA_LEFT,
        spaceAfter=3 * mm,
    )
    subtitle = ParagraphStyle(
        "TV5Subtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.2,
        leading=10,
        textColor=MUTED,
        spaceAfter=3.5 * mm,
    )
    heading = ParagraphStyle(
        "TV5Heading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11.5,
        leading=13,
        textColor=BLUE,
        spaceBefore=2.2 * mm,
        spaceAfter=1.5 * mm,
        keepWithNext=True,
    )
    body = ParagraphStyle(
        "TV5Body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.1,
        leading=10.1,
        textColor=TEXT,
        spaceAfter=1.5 * mm,
    )
    small = ParagraphStyle(
        "TV5Small",
        parent=body,
        fontSize=6.7,
        leading=8.1,
        spaceAfter=0,
    )
    small_bold = ParagraphStyle(
        "TV5SmallBold",
        parent=small,
        fontName="Helvetica-Bold",
    )
    small_header = ParagraphStyle(
        "TV5SmallHeader",
        parent=small_bold,
        textColor=colors.white,
    )
    code = ParagraphStyle(
        "TV5Code",
        parent=body,
        fontName="Courier",
        fontSize=6.6,
        leading=8.2,
        backColor=ALT,
        borderColor=GRID,
        borderWidth=0.4,
        borderPadding=5,
        spaceBefore=1 * mm,
        spaceAfter=1.5 * mm,
    )

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=23 * mm,
        bottomMargin=18 * mm,
        title="TV5 - Locking, Byzantine Handling and T1-T8 Results",
        author="TV5 - Blockchain Lab 01",
    )

    story: list[object] = [
        _paragraph("TV5: LOCKING, BYZANTINE HANDLING<br/>AND TEST EVIDENCE", title),
        _paragraph(
            "Implementation notes | Tendermint-style BFT | n = 3f + 1 | Reproducible Python tests",
            subtitle,
        ),
        _paragraph("1. Scope and safety objective", heading),
        _paragraph(
            "TV5 guards the boundary between untrusted consensus messages and validator state. "
            "<b>VoteTracker</b> validates, de-duplicates and tallies votes; <b>LockManager</b> "
            "enforces local signing and PoLC-based lock changes; the Byzantine simulator creates "
            "bounded faults; and the deterministic logger emits byte-stable evidence. The safety "
            "invariant is that honest nodes never finalize two blocks at the same height.",
            body,
        ),
        _paragraph("2. Seven voting and locking rules", heading),
    ]

    rule_rows = [
        [_paragraph("Rule", small_header), _paragraph("Enforced condition", small_header), _paragraph("Implementation", small_header)],
        [_paragraph("1", small_bold), _paragraph("One PREVOTE per validator and (H, R).", small), _paragraph("Local slot keyed by height, round and phase.", small)],
        [_paragraph("2", small_bold), _paragraph("One PRECOMMIT per validator and (H, R).", small), _paragraph("PRECOMMIT uses an independent phase slot.", small)],
        [_paragraph("3", small_bold), _paragraph("Non-NIL PRECOMMIT requires matching PREVOTE quorum.", small), _paragraph("can_precommit() checks a stored PoLC.", small)],
        [_paragraph("4", small_bold), _paragraph("2f + 1 PREVOTE(B), B != NIL, locks B at round R.", small), _paragraph("observe_quorum() validates and records lock.", small)],
        [_paragraph("5", small_bold), _paragraph("Changing block requires locked_round &lt; polka_round &lt; proposal_round.", small), _paragraph("can_prevote() rejects stale/same-round PoLC.", small)],
        [_paragraph("6", small_bold), _paragraph("Reject invalid voter, coordinates, hash, chain or signature domain.", small), _paragraph("Optional verification uses VOTE:&lt;chain_id&gt;.", small)],
        [_paragraph("7", small_bold), _paragraph("Duplicates count once; conflicts are equivocation.", small), _paragraph("First vote stays; conflicting pair is evidence.", small)],
    ]
    story.extend(
        [
            _table(rule_rows, [11 * mm, 80 * mm, 78 * mm], font_size=6.8),
            _paragraph("3. Quorum, locking and PoLC", heading),
            _paragraph(
                "Votes are indexed by (height, round, phase) and tallied per block hash. A quorum "
                "contains distinct validators for one block. PoLC votes must agree on height, round, "
                "PREVOTE phase and block hash. A new height clears the lock while retaining the local "
                "signing history for audit.",
                body,
            ),
            _paragraph(
                "Safety-critical ordering: locked_round &lt; polka_round &lt; proposal_round",
                code,
            ),
            _paragraph("4. Implementation notes", heading),
            _paragraph(
                "Exact retransmissions are idempotent; conflicting duplicates never replace the first "
                "admitted vote. Signature payloads use stable sorted JSON and the VOTE:&lt;chain_id&gt; "
                "domain. Logger timestamps use simulated time, sorted compact JSON, UTF-8, LF and one "
                "final newline. These choices prevent packet order, locale and platform formatting from "
                "changing the tally or evidence hash.",
                body,
            ),
            PageBreak(),
            _paragraph("5. Byzantine handling", heading),
        ]
    )

    attack_rows = [
        [_paragraph("Attack", small_header), _paragraph("Injected behavior", small_header), _paragraph("Expected defense", small_header)],
        [_paragraph("Equivocation", small_bold), _paragraph("Vote A for one network view and B for another.", small), _paragraph("Count one vote; retain conflict evidence.", small)],
        [_paragraph("Invalid state", small_bold), _paragraph("Propose a deterministic wrong state commitment.", small), _paragraph("Execution/ledger validation rejects it.", small)],
        [_paragraph("Replay", small_bold), _paragraph("Repeat an old packet or vote unchanged.", small), _paragraph("Vote/transaction handling stays idempotent.", small)],
        [_paragraph("Silence", small_bold), _paragraph("Withhold all outbound messages.", small), _paragraph("Timeout and round change preserve liveness.", small)],
    ]
    story.extend(
        [
            _table(attack_rows, [31 * mm, 67 * mm, 71 * mm], font_size=6.7),
            _paragraph("6. Consolidated T1-T8 results", heading),
            _paragraph(
                "Status is limited to evidence present on this branch. PARTIAL means a supporting unit "
                "or component test passes but the named full-stack E2E scenario still requires TV1-TV4 integration.",
                small,
            ),
        ]
    )

    test_rows = [
        [_paragraph("Test", small_header), _paragraph("Required scenario", small_header), _paragraph("Evidence on TV5 branch", small_header), _paragraph("Status", small_header)],
        [_paragraph("T1", small_bold), _paragraph(">=8 nodes: same finalized chain/state", small), _paragraph("Needs integrated node/state stack.", small), _paragraph("NOT RUN", small_bold)],
        [_paragraph("T2", small_bold), _paragraph("Duplicate/reordered packets", small), _paragraph("Vote de-dup PASS; execution/reorder E2E pending.", small), _paragraph("PARTIAL", small_bold)],
        [_paragraph("T3", small_bold), _paragraph("Invalid signature/domain", small), _paragraph("Vote-domain rejection PASS; transaction E2E pending.", small), _paragraph("PARTIAL", small_bold)],
        [_paragraph("T4", small_bold), _paragraph("Transaction replay", small), _paragraph("Replay primitive PASS; ledger E2E pending.", small), _paragraph("PARTIAL", small_bold)],
        [_paragraph("T5", small_bold), _paragraph("Drop/delay: safety and liveness", small), _paragraph("Needs integrated network scenarios.", small), _paragraph("NOT RUN", small_bold)],
        [_paragraph("T6", small_bold), _paragraph("Proposer failure and recovery", small), _paragraph("Needs integrated consensus FSM.", small), _paragraph("NOT RUN", small_bold)],
        [_paragraph("T7", small_bold), _paragraph("Byzantine equivocation", small), _paragraph("One common block; no conflicting finalize.", small), _paragraph("PASS", small_bold)],
        [_paragraph("T8", small_bold), _paragraph("Deterministic execution", small), _paragraph("Identical logs, block set and state hash.", small), _paragraph("PASS", small_bold)],
    ]
    story.extend(
        [
            _table(
                test_rows,
                [10 * mm, 53 * mm, 78 * mm, 28 * mm],
                font_size=6.3,
                row_backgrounds={
                    1: NOT_RUN,
                    2: PARTIAL,
                    3: PARTIAL,
                    4: PARTIAL,
                    5: NOT_RUN,
                    6: NOT_RUN,
                    7: PASS,
                    8: PASS,
                },
            ),
            _paragraph("7. Reproducibility evidence", heading),
            KeepTogether(
                [
                    _paragraph(
                        f"Log SHA-256 (run 1 = run 2): {results['log_hashes']['run_1']}<br/>"
                        f"Final state hash: {results['final_state_hash']}<br/>"
                        "Repository suite at publication: 34/34 PASS; Ruff: no findings.",
                        code,
                    )
                ]
            ),
            _paragraph("8. References", heading),
            _paragraph(
                "[1] L. Lamport, R. Shostak, M. Pease, <i>The Byzantine Generals Problem</i>, "
                "ACM TOPLAS 4(3), 1982. doi:10.1145/357172.357176<br/>"
                "[2] M. Castro, B. Liskov, <i>Practical Byzantine Fault Tolerance</i>, OSDI, 1999. "
                "usenix.org/conference/osdi-99/practical-byzantine-fault-tolerance<br/>"
                "[3] E. Buchman, J. Kwon, Z. Milosevic, <i>The latest gossip on BFT consensus</i>, "
                "arXiv:1807.04938, 2018.",
                small,
            ),
        ]
    )

    doc.build(story, onFirstPage=_draw_page, onLaterPages=_draw_page)
    return output_path


def main() -> int:
    output = build_report()
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
