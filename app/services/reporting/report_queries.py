"""
DB queries for the full financial report.

Fetches all data needed to build the PDF:
  - Trial Balance
  - General Ledger (per account, with running balances)
  - P&L + Balance Sheet  (via statement_generator)
  - VAT201 Summary + Detail
  - Vendor Statements
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

import asyncpg

from app.services.reporting.statement_generator import (
    BalanceSheet,
    ProfitAndLoss,
    get_statements,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TrialBalanceLine:
    code: str
    name: str
    account_type: str
    total_debits: Decimal
    total_credits: Decimal
    balance: Decimal


@dataclass
class GeneralLedgerLine:
    entry_date: date
    reference: str | None
    description: str
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    vendor_name: str | None


@dataclass
class GeneralLedgerAccount:
    code: str
    name: str
    account_type: str
    normal_balance: str
    opening_balance: Decimal
    lines: list[GeneralLedgerLine] = field(default_factory=list)

    @property
    def closing_balance(self) -> Decimal:
        if not self.lines:
            return self.opening_balance
        return self.lines[-1].running_balance


@dataclass
class Vat201Period:
    tax_period: date
    output_net: Decimal
    output_vat: Decimal
    input_net: Decimal
    input_vat: Decimal
    net_vat_payable: Decimal


@dataclass
class Vat201DetailLine:
    tax_period: date
    transaction_type: str
    vat_code: str
    counterparty_name: str | None
    counterparty_vat_number: str | None
    invoice_number: str | None
    entry_date: date
    net_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal


@dataclass
class VendorTransaction:
    entry_date: date
    description: str
    reference: str | None
    gross_amount: Decimal
    vat_amount: Decimal | None
    invoice_number: str | None


@dataclass
class VendorStatement:
    vendor_name: str
    transactions: list[VendorTransaction] = field(default_factory=list)

    @property
    def total_spend(self) -> Decimal:
        return sum((t.gross_amount for t in self.transactions), Decimal(0))


@dataclass
class FullReportData:
    user: dict                              # business_name, vat_number, income_tax_ref etc.
    from_date: date
    to_date: date
    trial_balance: list[TrialBalanceLine]
    general_ledger: list[GeneralLedgerAccount]
    profit_and_loss: ProfitAndLoss
    balance_sheet: BalanceSheet
    vat201_periods: list[Vat201Period]
    vat201_detail: list[Vat201DetailLine]
    vendor_statements: list[VendorStatement]


# ---------------------------------------------------------------------------
# Individual queries
# ---------------------------------------------------------------------------

async def _fetch_user(conn: asyncpg.Connection, user_id: UUID) -> dict:
    row = await conn.fetchrow(
        """
        SELECT business_name, trading_name, vat_number, income_tax_ref,
               cipc_reg_number, financial_year_end_month
        FROM users WHERE id = $1
        """,
        user_id,
    )
    return dict(row) if row else {}


async def _fetch_trial_balance(
    conn: asyncpg.Connection,
    user_id: UUID,
    from_date: date,
    to_date: date,
) -> list[TrialBalanceLine]:
    rows = await conn.fetch(
        """
        SELECT
            a.code,
            a.name,
            a.account_type::text        AS account_type,
            COALESCE(SUM(vab.total_debits),  0) AS total_debits,
            COALESCE(SUM(vab.total_credits), 0) AS total_credits,
            COALESCE(SUM(vab.balance),       0) AS balance
        FROM accounts a
        LEFT JOIN v_account_balances vab
            ON  vab.account_id = a.id
            AND vab.period_year * 12 + vab.period_month
                BETWEEN $2 * 12 + $3 AND $4 * 12 + $5
        WHERE a.user_id   = $1
          AND a.is_active = TRUE
        GROUP BY a.code, a.name, a.account_type
        HAVING COALESCE(SUM(vab.total_debits),  0) != 0
            OR COALESCE(SUM(vab.total_credits), 0) != 0
        ORDER BY a.code
        """,
        user_id,
        from_date.year, from_date.month,
        to_date.year,   to_date.month,
    )
    return [
        TrialBalanceLine(
            code=r["code"],
            name=r["name"],
            account_type=r["account_type"],
            total_debits=Decimal(str(r["total_debits"])),
            total_credits=Decimal(str(r["total_credits"])),
            balance=Decimal(str(r["balance"])),
        )
        for r in rows
    ]


async def _fetch_general_ledger(
    conn: asyncpg.Connection,
    user_id: UUID,
    from_date: date,
    to_date: date,
) -> list[GeneralLedgerAccount]:
    # All posted lines in period, ordered by account then date
    rows = await conn.fetch(
        """
        SELECT
            a.code,
            a.name,
            a.account_type::text        AS account_type,
            a.normal_balance::text      AS normal_balance,
            je.entry_date,
            je.reference_number,
            COALESCE(jel.description, je.description)   AS description,
            jel.debit_amount,
            jel.credit_amount,
            d.vendor_name
        FROM journal_entry_lines jel
        JOIN journal_entries je ON je.id  = jel.journal_entry_id
        JOIN accounts        a  ON a.id   = jel.account_id
        LEFT JOIN documents  d  ON d.id   = je.document_id
        WHERE a.user_id     = $1
          AND je.status     = 'POSTED'
          AND je.entry_date BETWEEN $2 AND $3
        ORDER BY a.code, je.entry_date, je.created_at, jel.line_order
        """,
        user_id, from_date, to_date,
    )

    # Fetch opening balances for balance-sheet accounts (cumulative before period)
    ob_rows = await conn.fetch(
        """
        SELECT
            a.code,
            a.normal_balance::text      AS normal_balance,
            COALESCE(SUM(vab.balance),  0) AS opening_balance
        FROM accounts a
        LEFT JOIN v_account_balances vab
            ON  vab.account_id = a.id
            AND vab.period_year * 12 + vab.period_month < $2 * 12 + $3
        WHERE a.user_id = $1
          AND a.account_type IN ('ASSET', 'LIABILITY', 'EQUITY')
          AND a.is_active = TRUE
        GROUP BY a.code, a.normal_balance
        """,
        user_id, from_date.year, from_date.month,
    )
    ob_map = {r["code"]: Decimal(str(r["opening_balance"])) for r in ob_rows}

    # Group rows by account, compute running balance
    accounts: dict[str, GeneralLedgerAccount] = {}
    for r in rows:
        code = r["code"]
        if code not in accounts:
            ob = ob_map.get(code, Decimal(0))
            accounts[code] = GeneralLedgerAccount(
                code=code,
                name=r["name"],
                account_type=r["account_type"],
                normal_balance=r["normal_balance"],
                opening_balance=ob,
            )

        acct = accounts[code]
        debit  = Decimal(str(r["debit_amount"]))
        credit = Decimal(str(r["credit_amount"]))

        if acct.lines:
            prev = acct.lines[-1].running_balance
        else:
            prev = acct.opening_balance

        if acct.normal_balance == "DEBIT":
            running = prev + debit - credit
        else:
            running = prev + credit - debit

        acct.lines.append(GeneralLedgerLine(
            entry_date=r["entry_date"],
            reference=r["reference_number"],
            description=r["description"] or "",
            debit=debit,
            credit=credit,
            running_balance=running,
            vendor_name=r["vendor_name"],
        ))

    return list(accounts.values())


async def _fetch_vat201(
    conn: asyncpg.Connection,
    user_id: UUID,
    from_date: date,
    to_date: date,
) -> tuple[list[Vat201Period], list[Vat201DetailLine]]:
    period_rows = await conn.fetch(
        """
        SELECT tax_period, output_net, output_vat, input_net, input_vat, net_vat_payable
        FROM v_vat201_summary
        WHERE user_id    = $1
          AND tax_period >= $2
          AND tax_period <= $3
        ORDER BY tax_period
        """,
        user_id, from_date, to_date,
    )
    periods = [
        Vat201Period(
            tax_period=r["tax_period"],
            output_net=Decimal(str(r["output_net"])),
            output_vat=Decimal(str(r["output_vat"])),
            input_net=Decimal(str(r["input_net"])),
            input_vat=Decimal(str(r["input_vat"])),
            net_vat_payable=Decimal(str(r["net_vat_payable"])),
        )
        for r in period_rows
    ]

    detail_rows = await conn.fetch(
        """
        SELECT
            ve.tax_period,
            ve.transaction_type::text   AS transaction_type,
            ve.vat_code::text           AS vat_code,
            ve.counterparty_name,
            ve.counterparty_vat_number,
            ve.invoice_number,
            je.entry_date,
            ve.net_amount,
            ve.vat_amount,
            ve.gross_amount
        FROM vat_entries ve
        JOIN journal_entries je ON je.id = ve.journal_entry_id
        WHERE ve.user_id    = $1
          AND ve.tax_period >= $2
          AND ve.tax_period <= $3
          AND je.status     = 'POSTED'
        ORDER BY ve.tax_period, ve.transaction_type, je.entry_date
        """,
        user_id, from_date, to_date,
    )
    detail = [
        Vat201DetailLine(
            tax_period=r["tax_period"],
            transaction_type=r["transaction_type"],
            vat_code=r["vat_code"],
            counterparty_name=r["counterparty_name"],
            counterparty_vat_number=r["counterparty_vat_number"],
            invoice_number=r["invoice_number"],
            entry_date=r["entry_date"],
            net_amount=Decimal(str(r["net_amount"])),
            vat_amount=Decimal(str(r["vat_amount"])),
            gross_amount=Decimal(str(r["gross_amount"])),
        )
        for r in detail_rows
    ]
    return periods, detail


async def _fetch_vendor_statements(
    conn: asyncpg.Connection,
    user_id: UUID,
    from_date: date,
    to_date: date,
) -> list[VendorStatement]:
    rows = await conn.fetch(
        """
        SELECT
            COALESCE(d.vendor_name, 'Unknown Vendor') AS vendor_name,
            je.entry_date,
            je.description,
            je.reference_number,
            d.gross_amount,
            d.vat_amount,
            d.document_ref  AS invoice_number
        FROM journal_entries je
        JOIN documents d ON d.id = je.document_id
        WHERE je.user_id    = $1
          AND je.status     = 'POSTED'
          AND je.entry_date BETWEEN $2 AND $3
        ORDER BY vendor_name, je.entry_date
        """,
        user_id, from_date, to_date,
    )

    vendors: dict[str, VendorStatement] = {}
    for r in rows:
        name = r["vendor_name"]
        if name not in vendors:
            vendors[name] = VendorStatement(vendor_name=name)
        vendors[name].transactions.append(VendorTransaction(
            entry_date=r["entry_date"],
            description=r["description"] or "",
            reference=r["reference_number"],
            gross_amount=Decimal(str(r["gross_amount"])) if r["gross_amount"] else Decimal(0),
            vat_amount=Decimal(str(r["vat_amount"])) if r["vat_amount"] else None,
            invoice_number=r["invoice_number"],
        ))

    return list(vendors.values())


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def fetch_report_data(
    conn: asyncpg.Connection,
    user_id: UUID,
    from_date: date,
    to_date: date,
) -> FullReportData:
    user = await _fetch_user(conn, user_id)
    trial_balance = await _fetch_trial_balance(conn, user_id, from_date, to_date)
    general_ledger = await _fetch_general_ledger(conn, user_id, from_date, to_date)
    pl, bs = await get_statements(conn, user_id, from_date, to_date)
    vat201_periods, vat201_detail = await _fetch_vat201(conn, user_id, from_date, to_date)
    vendor_statements = await _fetch_vendor_statements(conn, user_id, from_date, to_date)

    return FullReportData(
        user=user,
        from_date=from_date,
        to_date=to_date,
        trial_balance=trial_balance,
        general_ledger=general_ledger,
        profit_and_loss=pl,
        balance_sheet=bs,
        vat201_periods=vat201_periods,
        vat201_detail=vat201_detail,
        vendor_statements=vendor_statements,
    )


def has_any_data(data: FullReportData) -> bool:
    return bool(data.trial_balance or data.general_ledger or data.vat201_periods)
