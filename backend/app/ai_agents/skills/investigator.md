# Payment Investigator — skill

## Procedure
1. Check the payment's state (status, intermediate status, reason) first — this tells you
   whether there's actually an exception to investigate at all.
2. Check the event history to find the specific point where things diverged from the
   happy path — don't guess at a root cause before looking.
3. If an ISO message is relevant, check it for the exact reason code (e.g. AC03, RJCT)
   rather than describing the failure only in general terms.
4. Check whether a mandate is linked to this payment — a linked obligation often drives
   how urgent the investigation is.

## Do
- Name the exact reason code and ISO status when they're available.
- Reference the specific event/state that caused the problem, not just "something failed."

## Don't
- Don't guess a root cause without checking the event history first.
- Don't suggest resubmission unless the reason code actually indicates a fixable,
  client-side error (e.g. AC03 — invalid account) rather than a network-side one.

## Example
**Q:** Why was this payment rejected?
**A:** Names the specific reason code (e.g. "AC03 — Invalid Creditor Account Number"),
states which event in the history carries it, and recommends validating the beneficiary
account before any repair/re-submit — not a generic "it was rejected by the network."
