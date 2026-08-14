# Python Guard Threat Model

This note defines the trust boundary for pure-Python construction tokens,
provenance bindings, immutable DTOs, and fail-closed validation guards used by
the project.

## In scope

### T1 — untrusted source data

Untrusted spreadsheet values, URLs, formulas, metadata, and other source
payloads are **in scope**. Guards must validate them, avoid reflecting payloads
in failures or supported debug surfaces, and fail closed where the contract
requires it.

### T2 — supported API and application misuse

Ordinary misuse through supported constructors, factories, serializers, copy
APIs, and application call paths is **in scope**. Pure-Python guards provide:

- supported construction discipline;
- application provenance and correctness;
- misuse resistance; and
- fail-closed ordinary APIs.

## Out of scope

### T3 — hostile code already executing in the same Python interpreter

Arbitrary hostile same-process execution is **out of scope as a standalone
correctness or freeze threat**. Examples include deliberate `__closure__`
introspection, `object.__new__`, `object.__setattr__`, monkeypatching, `ctypes`,
and debugger or runtime mutation.

Pure-Python guards do **not** provide cryptographic authenticity,
hostile-process isolation, or interpreter sandboxing. They are application
correctness boundaries, not same-interpreter security isolation.

If a future architecture permits untrusted code to execute in-process, the
system must reconsider a real process or service trust boundary.
