# Invoice Assistant

A small helper tool for reading supplier invoices, so you don't have to
type every line by hand. It does **not** replace Bevlink - it just saves
you the data entry, and gives you a clean CSV / spreadsheet you can key
into Bevlink or send wherever it's needed.

**Before using this on real club invoices, the club should approve its
use.** It sends a photo/PDF of each invoice to Anthropic's Claude AI
service (over the internet) so it can read the text. Everything else -
the extracted numbers, your edits, the exports, and the original
invoice images - stays on this PC only. Nothing is sent anywhere else,
and nothing is ever automatically emailed, ordered, or entered into
Bevlink. You always check the numbers on screen before anything is
saved.

## How to start it

Double-click **start.bat**. The first time, it will:

1. Set itself up (takes a minute or two).
2. Ask you to add your API key to a file called `.env` - open it in
   Notepad, paste your key in after the `=`, save, and run
   **start.bat** again.

After that, it opens in your web browser automatically. Leave the black
window open while you're using it - closing it closes the app.

The app has four pages down the left side: **Invoices**, **Stocktake**,
**Reports**, and **API usage**.

### Invoices

1. **Upload** a photo or PDF of an invoice.
2. Click **Extract invoice details**. This takes a few seconds and
   costs a small amount (shown after it finishes, and tracked in the
   sidebar so you can see your spend for the month).
3. **Check the review screen.** Anything the tool wasn't confident about
   is marked **CHECK** with a plain-English reason - it will never make
   up a number it couldn't actually read. Click **View original invoice**
   to compare the photo against what was extracted. Fix anything wrong
   directly in the boxes and the table.
4. Read any **warnings** - missing fields, line items that need
   confirming, and price changes over 3% are grouped with a count and
   only expand when you click them. The two checks that matter most -
   line items not adding up to the total, and GST not looking like 10% -
   are always shown, not hidden behind a click.
5. Click **Save & export**. This:
   - Saves the invoice to this tool's price-history so future price
     jumps can be caught.
   - Writes a CSV file into the `csv_exports` folder (ready to key into
     Bevlink or send on).
   - Adds it to that month's running spreadsheet in the `exports`
     folder, with totals by department.

Further down the Invoices page, **Previously saved invoices** lets you
filter by supplier or month and pick one to view its photo and line
items again, and **Open invoices folder** opens the raw files in
Windows Explorer.

### Stocktake

1. Set the **stocktake date**, then upload or photograph each count
   sheet - you can add sheets one at a time or all at once.
2. Click **Extract** for each new sheet. Items land in one combined
   table you can edit, with a department per row.
3. Every counted item is automatically valued using the last known
   price from your invoice history (items with no price history yet are
   listed but not valued - extract an invoice for them first).
4. Check the **blank count box** / **needs confirming** sections (same
   click-to-expand pattern as Invoices), then click **Save stocktake**.

### Reports

Switch between **Weekly** and **Monthly**, pick a period, and see:

- Total purchases for that period
- Totals by supplier, with a dropdown to drill into any one supplier's
  invoices (or see all of them combined)
- Totals by department
- The **30%-of-purchases closing stock check** - pulled automatically
  from your latest saved stocktake, or type a figure from Bevlink by
  hand if you haven't done a stocktake in this tool yet.

### API usage

A live view of what this tool is spending: this month's total against the
cap, and a running log of every extraction (file, type, tokens, cost, and
whether it succeeded) - useful for keeping an eye on cost and for spotting
a run of failed extractions.

## Where things are kept

| Folder / file | What's in it |
|---|---|
| `csv_exports/` | One CSV per saved invoice |
| `exports/` | Monthly Excel workbooks (`invoices_2026-01.xlsx` etc.) with an Invoices sheet, a Line items sheet, and a Department totals sheet |
| `data/images/` | The original photo/PDF of every invoice and stocktake sheet you've extracted |
| `data/invoices.db` | The saved invoice, price-history, and stocktake records (not meant to be opened directly) |
| `sample_invoices/` | Just a scratch folder for testing - not used by the app |

## Monthly cost cap

To stop this from ever running up a surprise bill, it tracks what it's
spent each month and refuses to read more invoices once it hits a cap
(default **$15/month** - realistically each invoice costs a fraction of
a cent, so this is a very high ceiling). To change the cap, open `.env`
and add a line like:

```
MONTHLY_COST_CAP_USD=25
```

## If something goes wrong

- **"Couldn't read that invoice"** - usually means the photo is too
  blurry, dark, or the file didn't upload properly. Try a clearer photo.
- **The app won't start / a Python error appears** - close the window
  and run `start.bat` again. If it keeps happening, note down the exact
  message on screen so it can be looked into.
- **A number field shows $0.00** - the tool genuinely couldn't read that
  value on the invoice (rather than guessing). Check the source image
  and type it in yourself.

## What's not built yet

- Drafting a credit request when stock is missing, damaged, or
  over-delivered (always a draft you'd review and send yourself -
  never automatic).
- Importing figures directly from a Bevlink export file - for now, type
  Bevlink numbers into the Reports page by hand where needed.
