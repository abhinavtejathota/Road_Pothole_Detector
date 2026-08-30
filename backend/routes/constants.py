"""Shared workflow constants for work orders."""

VALID_TRANSITIONS = {
    "Created": ["Allocated"],
    "Allocated": ["WIP", "Created"],
    "WIP": ["Completed"],
    "Completed": ["Verified", "Failed"],
    "Verified": [],
    "Failed": ["Allocated"],
}
