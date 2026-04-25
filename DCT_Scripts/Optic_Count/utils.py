import re


def natural_key(s):
    """Sort key that orders embedded numbers numerically (swp1 < swp2 < swp10)."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', str(s))]
