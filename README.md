# AP Invoice Agent: from PDF to decision

An automated process that reads a vendor invoice PDF, validates it against
purchase orders and rules, and returns a reasoned decision.

**Live app:** [your streamlit link]
**Demo video:** [your video link]

## Design principle
AI reads the messy document. Plain code makes the money decision, so every
decision is predictable and auditable.

## How it works
1. Ingest the PDF
2. Extract fields with an AI model (JSON out, null if unclear, never guess)
3. Run rule checks: completeness, vendor, duplicates, invoice age, PO match, amount
4. Decide: Approve / Approve with note / Needs review / Request info / Reject
5. Log every run with its reasoning to the dashboard

## Edge cases handled
- **Near-duplicate:** INV-0042 vs INV0042 caught after normalising
- **Split invoicing:** tracks cumulative billed amount against the PO
- **Tolerance:** up to 2% over is approved with a note; more goes to review
- **Missing fields:** requests info and drafts a vendor email, no guessing
- **No PO reference:** infers only if exactly one open PO fits
- **Stale invoice (my own addition):** over 90 days old goes to review

## Assumptions
- 2% tolerance, 90-day age limit, USD, English invoices [edit to match yours]
- Test data is synthetic: 4 approved vendors, 4 POs

## Limitations and what I'd build next
- Next: email inbox ingestion, ERP integration, learning from human overrides
- Today: a single currency, and handwritten invoices should go to review

## Run locally
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key
streamlit run app.py
