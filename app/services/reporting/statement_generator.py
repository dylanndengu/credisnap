"""
Financial statement generator.

Queries v_account_balances to produce a P&L and Balance Sheet for a given
date range and formats them as a WhatsApp-friendly text message.

P&L: REVENUE and EXPENSE accounts summed over the reporting period.
Balance Sheet: ASSET, LIABILITY, EQUITY accounts cumulative to period end.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

import asyncpg

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IFRS line item → statement section mapping
# ---------------------------------------------------------------------------

_CURRENT_ASSET_ITEMS = frozenset({
    "cash_and_equivalents", "trade_receivables", "inventories",
    "other_current_assets", "current_assets",
})
_CURRENT_LIABILITY_ITEMS = frozenset({
    "trade_payables", "tax_payable", "short_term_borrowings",
    "accrued_liabilities", "current_liabilities",
})
_COST_OF_SALES_ITEMS = frozenset({"cost_of_sales"})
_FINANCE_COST_ITEMS = frozenset({"finance_costs"})
_OTHER_INCOME_ITEMS = frozenset({"other_income", "finance_income"})


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AccountLine:
    code: str
    name: str
    balance: Decimal


@dataclass
class ProfitAndLoss:
    period_start: date
    period_end: date
    revenue:             list[AccountLine] = field(default_factory=list)
    other_income:        list[AccountLine] = field(default_factory=list)
    cost_of_sales:       list[AccountLine] = field(default_factory=list)
    operating_expenses:  list[AccountLine] = field(default_factory=list)
    finance_costs:       list[AccountLine] = field(default_factory=list)

    @property
    def total_revenue(self) -> Decimal:
        return sum((l.balance for l in self.revenue + self.other_income), Decimal(0))

    @property
    def total_cost_of_sales(self) -> Decimal:
        return sum((l.balance for l in self.cost_of_sales), Decimal(0))

    @property
    def gross_profit(self) -> Decimal:
        return self.total_revenue - self.total_cost_of_sales

    @property
    def total_operating_expenses(self) -> Decimal:
        return sum((l.balance for l in self.operating_expenses), Decimal(0))

    @property
    def operating_profit(self) -> Decimal:
        return self.gross_profit - self.total_operating_expenses

    @property
    def total_finance_costs(self) -> Decimal:
        return sum((l.balance for l in self.finance_costs), Decimal(0))

    @property
    def net_profit(self) -> Decimal:
        return self.operating_profit - self.total_finance_costs

    def has_data(self) -> bool:
        return bool(self.revenue or self.other_income or self.cost_of_sales
                    or self.operating_expenses or self.finance_costs)


@dataclass
class BalanceSheet:
    as_at: date
    current_assets:          list[AccountLine] = field(default_factory=list)
    non_current_assets:      list[AccountLine] = field(default_factory=list)
    current_liabilities:     list[AccountLine] = field(default_factory=list)
    non_current_liabilities: list[AccountLine] = field(default_factory=list)
    equity:                  list[AccountLine] = field(default_factory=list)

    @property
    def total_assets(self) -> Decimal:
        return sum((l.balance for l in self.current_assets + self.non_current_assets), Decimal(0))

    @property
    def total_liabilities(self) -> Decimal:
        return sum((l.balance for l in self.current_liabilities + self.non_current_liabilities), Decimal(0))

    @property
    def total_equity(self) -> Decimal:
        return sum((l.balance for l in self.equity), Decimal(0))

    @property
    def net_assets(self) -> Decimal:
        return self.total_assets - self.total_liabilities

    def has_data(self) -> bool:
        return bool(self.current_assets or self.non_current_assets
                    or self.current_liabilities or self.non_current_liabilities
                    or self.equity)


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

_PL_QUERY = """
SELECT
    a.code,
    a.name,
    COALESCE(a.ifrs_line_item, 'other')  AS ifrs_line_item,
    a.account_type::text                 AS account_type,
    COALESCE(SUM(vab.balance), 0)        AS balance
FROM accounts a
LEFT JOIN v_account_balances vab
    ON  vab.account_id = a.id
    AND vab.period_year  * 12 + vab.period_month
        BETWEEN $2 * 12 + $3 AND $4 * 12 + $5
WHERE a.user_id    = $1
  AND a.account_type IN ('REVENUE', 'EXPENSE')
GROUP BY a.code, a.name, a.ifrs_line_item, a.account_type
HAVING COALESCE(SUM(vab.balance), 0) != 0
ORDER BY a.code
"""

_BS_QUERY = """
SELECT
    a.code,
    a.name,
    COALESCE(a.ifrs_line_item, 'other')  AS ifrs_line_item,
    a.account_type::text                 AS account_type,
    COALESCE(SUM(vab.balance), 0)        AS balance
FROM accounts a
LEFT JOIN v_account_balances vab
    ON  vab.account_id = a.id
    AND vab.period_year * 12 + vab.period_month <= $2 * 12 + $3
WHERE a.user_id    = $1
  AND a.account_type IN ('ASSET', 'LIABILITY', 'EQUITY')
GROUP BY a.code, a.name, a.ifrs_line_item, a.account_type
HAVING COALESCE(SUM(vab.balance), 0) != 0
ORDER BY a.code
"""


async def get_statements(
    conn: asyncpg.Connection,
    user_id: UUID,
    from_date: date,
    to_date: date,
) -> tuple[ProfitAndLoss, BalanceSheet]:
    """Return structured P&L and Balance Sheet for the given period."""

    pl = ProfitAndLoss(period_start=from_date, period_end=to_date)
    bs = BalanceSheet(as_at=to_date)

    # --- P&L ---
    pl_rows = await conn.fetch(
        _PL_QUERY,
        user_id,
        from_date.year, from_date.month,
        to_date.year,   to_date.month,
    )
    for row in pl_rows:
        line = AccountLine(
            code=row["code"],
            name=row["name"],
            balance=Decimal(str(row["balance"])),
        )
        item = row["ifrs_line_item"]
        atype = row["account_type"]

        if atype == "REVENUE":
            (pl.other_income if item in _OTHER_INCOME_ITEMS else pl.revenue).append(line)
        else:  # EXPENSE
            if item in _COST_OF_SALES_ITEMS:
                pl.cost_of_sales.append(line)
            elif item in _FINANCE_COST_ITEMS:
                pl.finance_costs.append(line)
            else:
                pl.operating_expenses.append(line)

    # --- Balance Sheet ---
    bs_rows = await conn.fetch(
        _BS_QUERY,
        user_id,
        to_date.year, to_date.month,
    )
    for row in bs_rows:
        line = AccountLine(
            code=row["code"],
            name=row["name"],
            balance=Decimal(str(row["balance"])),
        )
        item  = row["ifrs_line_item"]
        atype = row["account_type"]

        if atype == "ASSET":
            (bs.current_assets if item in _CURRENT_ASSET_ITEMS else bs.non_current_assets).append(line)
        elif atype == "LIABILITY":
            (bs.current_liabilities if item in _CURRENT_LIABILITY_ITEMS else bs.non_current_liabilities).append(line)
        else:  # EQUITY
            bs.equity.append(line)

    return pl, bs


# ---------------------------------------------------------------------------
# Financial year helpers
# ---------------------------------------------------------------------------

import calendar as _calendar


def financial_year(fy_end_month: int, fy_year: int) -> tuple[date, date]:
    """
    Return (from_date, to_date) for a specific financial year.

    fy_year is the calendar year in which the FY ends.

    Example: fy_end_month=2 (Feb), fy_year=2025
      → from_date = 1 Mar 2024, to_date = 28 Feb 2025
    """
    fy_start_month = (fy_end_month % 12) + 1
    # If start month > end month (e.g. Mar > Feb), the FY spans two calendar years
    fy_start_year = fy_year - 1 if fy_start_month > fy_end_month else fy_year
    last_day = _calendar.monthrange(fy_year, fy_end_month)[1]
    from_date = date(fy_start_year, fy_start_month, 1)
    to_date   = date(fy_year, fy_end_month, last_day)
    # Cap to_date at today for the current (in-progress) FY
    return from_date, min(to_date, date.today())


def current_financial_year(fy_end_month: int) -> tuple[date, date]:
    """Return (from_date, to_date) for the current financial year to date."""
    today = date.today()
    fy_start_month = (fy_end_month % 12) + 1
    if today.month > fy_end_month:
        current_fy_year = today.year + (1 if fy_start_month <= fy_end_month else 0)
    else:
        current_fy_year = today.year
    return financial_year(fy_end_month, current_fy_year)


# ---------------------------------------------------------------------------
# WhatsApp message formatter
# ---------------------------------------------------------------------------

def _r(amount: Decimal) -> str:
    """Format a ZAR amount, using brackets for negatives."""
    if amount < 0:
        return f"(R {abs(amount):,.0f})"
    return f"R {amount:,.0f}"


def _month(d: date) -> str:
    return d.strftime("%-d %b %Y") if hasattr(d, "strftime") else str(d)


def format_whatsapp_report(pl: ProfitAndLoss, bs: BalanceSheet) -> str:
    if not pl.has_data() and not bs.has_data():
        return (
            "No posted transactions found for this period yet.\n\n"
            "Upload a receipt and confirm it to see your financial statements."
        )

    lines = [
        "📊 *CrediSnap Financial Report*",
        f"_{_month(pl.period_start)} – {_month(pl.period_end)}_",
        "",
    ]

    # --- P&L ---
    lines.append("*PROFIT & LOSS*")
    if pl.has_data():
        lines.append(f"Revenue:              {_r(pl.total_revenue)}")
        if pl.cost_of_sales:
            lines.append(f"Cost of Sales:        {_r(pl.total_cost_of_sales)}")
        lines.append(f"*Gross Profit:         {_r(pl.gross_profit)}*")
        if pl.operating_expenses:
            lines.append(f"Operating Expenses:   {_r(pl.total_operating_expenses)}")
        if pl.finance_costs:
            lines.append(f"Finance Costs:        {_r(pl.total_finance_costs)}")
        lines.append(f"*Net Profit:           {_r(pl.net_profit)}*")
    else:
        lines.append("_No P&L activity in this period._")

    lines.append("")

    # --- Balance Sheet ---
    lines.append(f"*BALANCE SHEET* _(as at {_month(bs.as_at)})_")
    if bs.has_data():
        lines.append(f"Total Assets:         {_r(bs.total_assets)}")
        lines.append(f"Total Liabilities:    {_r(bs.total_liabilities)}")
        lines.append(f"*Net Assets:          {_r(bs.net_assets)}*")
        if bs.equity:
            lines.append(f"Equity:               {_r(bs.total_equity)}")
    else:
        lines.append("_No balance sheet activity yet._")

    return "\n".join(lines)
