#!/usr/bin/env python3
"""One-shot script to insert A-01 ISA gate into main.py.
Run once, then delete this file."""

import sys

filepath = '/Users/rr/nzt48-signals/main.py'

with open(filepath, 'r') as f:
    content = f.read()

if 'A-01 ISA' in content:
    print('ISA gate already present in main.py')
    sys.exit(0)

# Find anchor: "Raw signals from all strategies"
anchor = 'Raw signals from all strategies'
anchor_idx = content.find(anchor)
if anchor_idx < 0:
    print('ERROR: anchor not found')
    sys.exit(1)

# Find end of anchor line
end_of_line = content.find('\n', anchor_idx)

# Find "# S15 PRIORITY PATH" after anchor
s15_marker = '# S15 PRIORITY PATH'
s15_idx = content.find(s15_marker, end_of_line)
if s15_idx < 0:
    print('ERROR: S15 PRIORITY PATH not found')
    sys.exit(1)

# Find the "# =====" line before S15
eq_idx = content.rfind('# =====', end_of_line, s15_idx)
if eq_idx < 0:
    print('ERROR: ===== line before S15 not found')
    sys.exit(1)

gate_block = """
        # =====================================================================
        # A-01: ISA ELIGIBILITY PRE-TRADE GATE (HARD VETO \u2014 FIRST GATE)
        # =====================================================================
        # HMRC ISA Regulations 1998 (SI 1998/1870, reg 4ZA): one non-ISA trade
        # voids the entire tax wrapper.  This gate runs BEFORE any other
        # processing (S15 priority path, S16 gauntlet, ML blend, qualification).
        # No override.  No bypass.  No exceptions.
        # =====================================================================
        _pre_isa_count = len(raw_signals)
        raw_signals = [
            s for s in raw_signals if is_isa_eligible(s.ticker)
        ]
        _isa_rejected = _pre_isa_count - len(raw_signals)
        if _isa_rejected > 0:
            logger.warning(
                "A-01 ISA GATE: %d of %d signal(s) REJECTED \u2014 ticker(s) not ISA-eligible",
                _isa_rejected, _pre_isa_count,
            )
        else:
            logger.debug("A-01 ISA GATE: all %d signal(s) passed ISA eligibility check", _pre_isa_count)

"""

new_content = content[:eq_idx] + gate_block + content[eq_idx:]

with open(filepath, 'w') as f:
    f.write(new_content)

print(f'ISA gate inserted at position {eq_idx}')
print('Success')
