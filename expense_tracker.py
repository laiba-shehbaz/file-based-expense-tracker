#!/usr/bin/env python3
"""
💰 Expense Tracker
==================
A file-based command-line tool to record and analyse personal or
small-business expenses.

Features
    * Add, edit, delete and list expenses
    * Data stored in a JSON file and loaded automatically at start-up
    * Strict validation of amounts, dates, categories and IDs
    * Damaged / missing data files are handled gracefully (never crashes)
    * Monthly totals and category-wise summaries
    * Export a report as .txt, .md or .csv

Run:  python expense_tracker.py [--file expenses.json] [--currency Rs]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import shutil
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

DATE_FMT = "%Y-%m-%d"
DEFAULT_FILE = "expenses.json"
MAX_AMOUNT = Decimal("10000000")
CURRENCY = "Rs"
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


# ──────────────────────────── helpers ────────────────────────────
def paint(text: str, code: str) -> str:
    """Wrap text in an ANSI colour code (only when the terminal supports it)."""
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def money(value: float) -> str:
    return f"{CURRENCY} {value:,.2f}"


class ValidationError(ValueError):
    """Raised when user input or a stored record is invalid."""


# ──────────────────────────── validation ─────────────────────────
def parse_amount(text: str) -> float:
    text = str(text).strip().replace(",", "")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValidationError("Amount must be a number, e.g. 1250.50")
    if not value.is_finite():
        raise ValidationError("Amount must be a real number")
    if value <= 0:
        raise ValidationError("Amount must be greater than zero")
    if value > MAX_AMOUNT:
        raise ValidationError(f"Amount is too large (max {MAX_AMOUNT:,})")
    if value != value.quantize(Decimal("0.01")):
        raise ValidationError("Use at most 2 decimal places")
    return float(value)


def parse_date(text: str, blank_means_today: bool = True) -> str:
    text = str(text).strip()
    if not text:
        if blank_means_today:
            return dt.date.today().strftime(DATE_FMT)
        raise ValidationError("Date is required (YYYY-MM-DD)")
    try:
        parsed = dt.datetime.strptime(text, DATE_FMT)
    except ValueError:
        raise ValidationError("Date must be a real date in YYYY-MM-DD format")
    if parsed.year < 2000:
        raise ValidationError("Year must be 2000 or later")
    return parsed.strftime(DATE_FMT)


def parse_category(text: str) -> str:
    text = " ".join(str(text).split())
    if not text:
        raise ValidationError("Category cannot be empty")
    if len(text) > 30:
        raise ValidationError("Category must be 30 characters or fewer")
    return " ".join(word.capitalize() for word in text.split(" "))


def parse_description(text: str) -> str:
    text = " ".join(str(text).split())
    if text == "-":
        return ""
    if len(text) > 100:
        raise ValidationError("Description must be 100 characters or fewer")
    return text


def parse_month(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    try:
        return dt.datetime.strptime(text, "%Y-%m").strftime("%Y-%m")
    except ValueError:
        raise ValidationError("Month must look like 2026-11")


# ──────────────────────────── data model ─────────────────────────
@dataclass
class Expense:
    id: int
    amount: float
    category: str
    date: str
    description: str = ""

    @classmethod
    def from_dict(cls, data) -> "Expense":
        if not isinstance(data, dict):
            raise ValidationError("record is not an object")
        missing = [k for k in ("id", "amount", "category", "date") if k not in data]
        if missing:
            raise ValidationError("missing field(s): " + ", ".join(missing))
        try:
            rec_id = int(data["id"])
        except (TypeError, ValueError):
            raise ValidationError("id must be a whole number")
        return cls(
            id=rec_id,
            amount=parse_amount(str(data["amount"])),
            category=parse_category(str(data["category"])),
            date=parse_date(str(data["date"]), blank_means_today=False),
            description=parse_description(str(data.get("description", ""))),
        )


class ExpenseStore:
    """Holds expenses in memory and persists them to a JSON file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.expenses: list[Expense] = []
        self.notes: list[str] = []

    # ---- loading -------------------------------------------------
    def load(self) -> None:
        self.expenses, self.notes = [], []
        if not self.path.exists():
            self.notes.append(f"No data file found. A new one will be created: {self.path}")
            return
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as err:
            return self._quarantine(f"could not be read ({err})")
        if not text.strip():
            self.notes.append("Data file was empty - starting fresh.")
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError as err:
            return self._quarantine(f"is not valid JSON ({err.msg}, line {err.lineno})")
        records = data.get("expenses") if isinstance(data, dict) else data
        if not isinstance(records, list):
            return self._quarantine("has an unexpected structure")

        seen: set[int] = set()
        skipped: list[str] = []
        for index, record in enumerate(records, start=1):
            try:
                expense = Expense.from_dict(record)
                if expense.id in seen:
                    raise ValidationError(f"duplicate id {expense.id}")
            except ValidationError as err:
                skipped.append(f"#{index}: {err}")
                continue
            seen.add(expense.id)
            self.expenses.append(expense)
        if skipped:
            backup = self.path.with_name(self.path.name + ".bak")
            try:
                shutil.copy2(self.path, backup)
                kept = f"A copy of the original was saved as {backup.name}."
            except OSError:
                kept = "The original could not be backed up."
            self.notes.append(
                f"Skipped {len(skipped)} damaged record(s) ({'; '.join(skipped[:3])}"
                f"{'...' if len(skipped) > 3 else ''}). {kept}"
            )

    def _quarantine(self, reason: str) -> None:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        try:
            self.path.rename(backup)
            where = f"moved to {backup.name}"
        except OSError:
            where = "could not be backed up"
        self.notes.append(f"Data file {reason}. Original {where}. Starting with an empty list.")

    # ---- saving --------------------------------------------------
    def save(self) -> None:
        payload = {"version": 1, "expenses": [asdict(e) for e in self.expenses]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic: never leaves a half-written file

    # ---- CRUD ----------------------------------------------------
    def next_id(self) -> int:
        return max((e.id for e in self.expenses), default=0) + 1

    def find(self, expense_id: int) -> Expense | None:
        return next((e for e in self.expenses if e.id == expense_id), None)

    def add(self, amount: float, category: str, date: str, description: str) -> Expense:
        expense = Expense(self.next_id(), amount, category, date, description)
        self.expenses.append(expense)
        self.save()
        return expense

    def update(self, expense: Expense, **changes) -> None:
        for key, value in changes.items():
            setattr(expense, key, value)
        self.save()

    def delete(self, expense: Expense) -> None:
        self.expenses.remove(expense)
        self.save()

    def sorted(self, month: str = "") -> list[Expense]:
        items = [e for e in self.expenses if e.date.startswith(month)]
        return sorted(items, key=lambda e: (e.date, e.id))


# ──────────────────────────── reporting ──────────────────────────
def render_table(headers, rows, aligns=None, markdown=False) -> str:
    aligns = aligns or ["<"] * len(headers)
    rows = [[str(c) for c in row] for row in rows]
    widths = [max([3, len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]

    def fmt(cells):
        return "| " + " | ".join(f"{c:{a}{w}}" for c, a, w in zip(cells, aligns, widths)) + " |"

    if markdown:
        sep = "| " + " | ".join(
            ("-" * (w - 1) + ":") if a == ">" else (":" + "-" * (w - 1))
            for a, w in zip(aligns, widths)
        ) + " |"
        return "\n".join([fmt(headers), sep] + [fmt(r) for r in rows])
    line = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    return "\n".join([line, fmt(headers), line] + [fmt(r) for r in rows] + [line])


def monthly_totals(expenses: list[Expense]) -> list[tuple[str, int, float]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for e in expenses:
        buckets[e.date[:7]].append(e.amount)
    return [(m, len(v), round(sum(v), 2)) for m, v in sorted(buckets.items())]


def category_summary(expenses: list[Expense]) -> list[tuple[str, int, float]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for e in expenses:
        buckets[e.category].append(e.amount)
    rows = [(c, len(v), round(sum(v), 2)) for c, v in buckets.items()]
    return sorted(rows, key=lambda r: (-r[2], r[0]))


def month_label(month: str) -> str:
    return dt.datetime.strptime(month, "%Y-%m").strftime("%b %Y")


def monthly_table(expenses, markdown=False) -> str:
    rows, previous = [], None
    for month, count, total in monthly_totals(expenses):
        change = "-" if not previous else f"{(total - previous) / previous * 100:+.1f}%"
        rows.append([month_label(month), count, money(total), money(total / count), change])
        previous = total
    return render_table(["Month", "Records", "Total", "Average", "vs Prev"], rows,
                        ["<", ">", ">", ">", ">"], markdown)


def category_table(expenses, markdown=False) -> str:
    grand = sum(e.amount for e in expenses)
    rows = []
    for category, count, total in category_summary(expenses):
        share = total / grand * 100
        bar = "█" * max(1, round(share / 5))
        rows.append([category, count, money(total), f"{share:.1f}%", bar])
    return render_table(["Category", "Records", "Total", "Share", "Chart"], rows,
                        ["<", ">", ">", ">", "<"], markdown)


def expense_table(expenses, markdown=False) -> str:
    rows = [[e.id, e.date, e.category, money(e.amount),
             (e.description[:34] + "…") if len(e.description) > 35 else e.description]
            for e in expenses]
    return render_table(["ID", "Date", "Category", "Amount", "Description"], rows,
                        [">", "<", "<", ">", "<"], markdown)


def build_report(expenses: list[Expense], markdown: bool = False) -> str:
    if not expenses:
        return "No expenses recorded yet.\n"
    total = round(sum(e.amount for e in expenses), 2)
    top = max(expenses, key=lambda e: e.amount)
    dates = sorted(e.date for e in expenses)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    overview = [
        ("Period", f"{dates[0]}  to  {dates[-1]}"),
        ("Total records", len(expenses)),
        ("Total spent", money(total)),
        ("Average per record", money(total / len(expenses))),
        ("Largest expense", f"{money(top.amount)} ({top.category}, {top.date})"),
    ]

    def heading(title: str) -> str:
        return f"\n## {title}\n" if markdown else f"\n{title}\n{'=' * len(title)}"

    out = ["# 💰 Expense Report" if markdown else "EXPENSE REPORT",
           f"_Generated: {stamp}_" if markdown else f"Generated: {stamp}", heading("Overview")]
    out += [f"- **{k}:** {v}" if markdown else f"  {k:<20}{v}" for k, v in overview]
    out += [heading("Monthly Totals"), monthly_table(expenses, markdown)]
    out += [heading("Category Summary"), category_table(expenses, markdown)]
    out += [heading("All Records"), expense_table(sorted(expenses, key=lambda e: (e.date, e.id)), markdown)]
    return "\n".join(out) + "\n"


def export_report(expenses: list[Expense], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "date", "category", "amount", "description"])
            for e in sorted(expenses, key=lambda x: (x.date, x.id)):
                writer.writerow([e.id, e.date, e.category, f"{e.amount:.2f}", e.description])
    else:
        path.write_text(build_report(expenses, markdown=(suffix == ".md")), encoding="utf-8")
    return path


# ──────────────────────────── user interface ─────────────────────
def ok(msg):   print(paint(f"  ✓ {msg}", "32"))
def warn(msg): print(paint(f"  ! {msg}", "33"))
def fail(msg): print(paint(f"  ✗ {msg}", "31"))


def prompt_field(label, parser, current=None, shown=None, hint=""):
    """Ask until the parser accepts the input. Blank keeps `current` when editing."""
    suffix = f" [{shown if shown is not None else current}]" if current is not None else hint
    while True:
        raw = input(f"  {label}{suffix}: ")
        if not raw.strip() and current is not None:
            return parser(current)
        try:
            return parser(raw)
        except ValidationError as err:
            fail(err)


def prompt_expense(store: ExpenseStore, action: str) -> Expense | None:
    if not store.expenses:
        warn("There are no expenses yet.")
        return None
    raw = input(f"  ID of the expense to {action}: ").strip()
    if not raw.isdigit():
        fail("ID must be a whole number")
        return None
    expense = store.find(int(raw))
    if expense is None:
        fail(f"No expense with ID {raw}")
    return expense


def ui_add(store):
    print(paint("\n➕ Add expense", "1;36"))
    if store.expenses:
        print("  Existing categories:", ", ".join(sorted({e.category for e in store.expenses})))
    amount = prompt_field("Amount", parse_amount)
    category = prompt_field("Category", parse_category)
    date = prompt_field("Date", parse_date, hint=" (YYYY-MM-DD, Enter = today)")
    description = prompt_field("Description", parse_description, hint=" (optional)")
    expense = store.add(amount, category, date, description)
    ok(f"Saved expense #{expense.id}: {money(expense.amount)} - {expense.category} on {expense.date}")


def ui_list(store):
    print(paint("\n📋 Expenses", "1;36"))
    month = prompt_field("Filter by month", parse_month, hint=" (YYYY-MM, Enter = all)")
    items = store.sorted(month)
    if not items:
        warn("No expenses to show.")
        return
    print(expense_table(items))
    print(f"  {len(items)} record(s), total {money(sum(e.amount for e in items))}")


def ui_edit(store):
    print(paint("\n✏️  Edit expense", "1;36"))
    expense = prompt_expense(store, "edit")
    if expense is None:
        return
    print("  Press Enter to keep a value. Type - to clear the description.")
    store.update(
        expense,
        amount=prompt_field("Amount", parse_amount, f"{expense.amount:.2f}"),
        category=prompt_field("Category", parse_category, expense.category),
        date=prompt_field("Date", parse_date, expense.date),
        description=prompt_field("Description", parse_description, expense.description,
                                 shown=expense.description or "none"),
    )
    ok(f"Expense #{expense.id} updated")


def ui_delete(store):
    print(paint("\n🗑️  Delete expense", "1;36"))
    expense = prompt_expense(store, "delete")
    if expense is None:
        return
    print(f"  {expense.date} | {expense.category} | {money(expense.amount)} | {expense.description}")
    if input("  Delete this record? (y/N): ").strip().lower() == "y":
        store.delete(expense)
        ok(f"Expense #{expense.id} deleted")
    else:
        warn("Cancelled - nothing was deleted")


def ui_monthly(store):
    print(paint("\n📅 Monthly totals", "1;36"))
    print(monthly_table(store.expenses) if store.expenses else "  No expenses yet.")


def ui_categories(store):
    print(paint("\n📊 Category summary", "1;36"))
    print(category_table(store.expenses) if store.expenses else "  No expenses yet.")


def ui_export(store):
    print(paint("\n📤 Export report", "1;36"))
    if not store.expenses:
        warn("Nothing to export yet.")
        return
    raw = input("  File name [report.txt] (.txt / .md / .csv): ").strip() or "report.txt"
    try:
        saved = export_report(store.expenses, raw)
    except OSError as err:
        fail(f"Could not write the report: {err}")
        return
    ok(f"Report saved to {saved.resolve()}")


MENU = [
    ("1", "Add expense", ui_add),
    ("2", "List expenses", ui_list),
    ("3", "Edit expense", ui_edit),
    ("4", "Delete expense", ui_delete),
    ("5", "Monthly totals", ui_monthly),
    ("6", "Category summary", ui_categories),
    ("7", "Export report", ui_export),
]


def print_banner(store):
    line = "═" * 46
    print(paint(f"\n╔{line}╗", "36"))
    print(paint(f"║{'💰  EXPENSE TRACKER':^45}║", "1;36"))
    print(paint(f"╚{line}╝", "36"))
    print(f"  Data file : {store.path}")
    print(f"  Records   : {len(store.expenses)}")
    for note in store.notes:
        warn(note)


def main(argv=None) -> int:
    global CURRENCY
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="File-based expense tracker")
    parser.add_argument("--file", default=DEFAULT_FILE, help="JSON data file (default: expenses.json)")
    parser.add_argument("--currency", default=CURRENCY, help="currency label (default: Rs)")
    args = parser.parse_args(argv)
    CURRENCY = args.currency

    store = ExpenseStore(args.file)
    store.load()
    print_banner(store)
    try:
        while True:
            print(paint("\nMAIN MENU", "1"))
            for key, label, _ in MENU:
                print(f"  {key}. {label}")
            print("  0. Exit")
            choice = input("Choose an option: ").strip()
            if choice == "0":
                break
            action = next((fn for key, _, fn in MENU if key == choice), None)
            if action is None:
                fail("Please choose a number from the menu")
                continue
            try:
                action(store)
            except OSError as err:
                fail(f"File error: {err}")
    except (EOFError, KeyboardInterrupt):
        print()
    print("Goodbye! Your data is saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
