#!/usr/bin/env python3
"""Unit tests for scanner.is_breed_noise()."""
import sys

from scanner import is_breed_noise

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures = []


def check(breed: str, expected: bool, note: str = ""):
    result = is_breed_noise(breed)
    label = note or repr(breed)
    if result == expected:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}  (got {result}, expected {expected})")
        _failures.append(label)


print("=== is_breed_noise() tests ===")

# --- should be NOISE (filtered out) ---
print("\n-- noise cases (should return True) --")
check("Cavalier King Charles Spaniel", True)
check("Cavalier KC Spaniel", True)
check("Spaniel: Cavalier KC", True)
check("Cockapoo", True)
check("Cockapoo (Cocker Spaniel x Poodle)", True)
check("cockapoo", True)
check("CAVALIER", True)

# --- should NOT be noise (strong-keep overrides) ---
print("\n-- keep cases (should return False) --")
check("Cavalier x Springer Spaniel", False, "Cavalier x Springer — keep")
check("Border Collie x Cavalier", False, "BC x Cavalier — keep")
check("Collie x Cavalier", False, "Collie x Cavalier — keep")
check("Sprocker Spaniel", False, "Sprocker — keep")
check("Sprollie", False, "Sprollie — keep")
check("Springer Spaniel", False, "Springer — keep")
check("Working Cocker Spaniel", False, "Working Cocker — keep")
check("Cocker Spaniel", False, "Cocker Spaniel — keep")
check("Spaniel", False, "generic Spaniel — keep")
check("Border Collie", False, "Border Collie — keep")
check("Collie Cross", False, "Collie Cross — keep")
check("", False, "empty string — keep")

if _failures:
    print(f"\n{len(_failures)} test(s) FAILED: {_failures}")
    sys.exit(1)
else:
    print(f"\nAll {12 + 11} checks passed.")
