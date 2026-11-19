/** The edit form for one document, as pure functions (no React): turn the API's data into form strings, tell whether the form has unsaved edits,
 *  and build the PATCH body. Only the fields the user CHANGED are sent: the backend treats every patched field as "fully confident", so sending
 *  untouched fields would silently clear their low-confidence warnings. */

import type { components } from '../api/schema'

type Invoice = components['schemas']['Invoice']

export const TEXT_FIELDS = [
  { name: 'vendor_name', label: 'Vendor name', hint: '' },
  { name: 'vendor_tax_id', label: 'Vendor tax ID', hint: '' },
  { name: 'invoice_number', label: 'Invoice number', hint: '' },
  { name: 'invoice_date', label: 'Invoice date', hint: 'YYYY-MM-DD' },
  { name: 'due_date', label: 'Due date', hint: 'YYYY-MM-DD' },
  { name: 'currency', label: 'Currency', hint: 'e.g. USD' },
] as const
export const MONEY_FIELDS = [
  { name: 'subtotal', label: 'Subtotal' },
  { name: 'tax', label: 'Tax' },
  { name: 'total', label: 'Total' },
] as const
export const LINE_COLUMNS = [
  { name: 'description', label: 'Description', numeric: false },
  { name: 'quantity', label: 'Quantity', numeric: true },
  { name: 'unit_price', label: 'Unit price', numeric: true },
  { name: 'amount', label: 'Amount', numeric: true },
] as const
export const DOC_TYPES = ['invoice', 'receipt'] as const

type TextName = (typeof TEXT_FIELDS)[number]['name']
type MoneyName = (typeof MONEY_FIELDS)[number]['name']
type LineName = (typeof LINE_COLUMNS)[number]['name']

/** Names of fields as the backend's issues use them, in words. */
export const FIELD_LABELS: Record<string, string> = {
  doc_type: 'Document type',
  ...Object.fromEntries(TEXT_FIELDS.map((f) => [f.name, f.label])),
  ...Object.fromEntries(MONEY_FIELDS.map((f) => [f.name, f.label])),
  line_items: 'Line items',
  overall_confidence: 'Overall confidence',
}
export const fieldLabel = (field: string): string => FIELD_LABELS[field] ?? field.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

export type LineRow = { key: number } & Record<LineName, string>
export type FormValues = { doc_type: string; line_items: LineRow[] } & Record<TextName | MoneyName, string>

export type DocumentPatch = Partial<
  Pick<Invoice, 'doc_type' | 'vendor_name' | 'vendor_tax_id' | 'invoice_number' | 'invoice_date' | 'due_date' | 'currency' | 'subtotal' | 'tax' | 'total' | 'line_items'>
>

let lastKey = 0
/** A stable React key for a line row (rows can be added and removed, so the index will not do). */
export const newRowKey = (): number => ++lastKey

export const emptyRow = (): LineRow => ({ key: newRowKey(), description: '', quantity: '', unit_price: '', amount: '' })

const text = (value: string | null | undefined) => value ?? ''
const number = (value: number | null | undefined) => (value === null || value === undefined ? '' : String(value))

export function toForm(data: Invoice): FormValues {
  return {
    doc_type: text(data.doc_type),
    vendor_name: text(data.vendor_name),
    vendor_tax_id: text(data.vendor_tax_id),
    invoice_number: text(data.invoice_number),
    invoice_date: text(data.invoice_date),
    due_date: text(data.due_date),
    currency: text(data.currency),
    subtotal: number(data.subtotal),
    tax: number(data.tax),
    total: number(data.total),
    line_items: (data.line_items ?? []).map((item) => ({
      key: newRowKey(),
      description: text(item.description),
      quantity: number(item.quantity),
      unit_price: number(item.unit_price),
      amount: number(item.amount),
    })),
  }
}

const NUMBER = /^-?(\d+\.?\d*|\.\d+)$/

/** "1,234.50" -> 1234.5, "" -> null (nothing typed), "abc" -> undefined (not a number). */
export function parseNumber(input: string): number | null | undefined {
  const cleaned = input.trim().replace(/,/g, '')
  if (cleaned === '') return null
  return NUMBER.test(cleaned) ? Number(cleaned) : undefined
}

/** One value as the form means it: trimmed text (empty = null), a parsed number, or the raw text when it is not a number. */
type Cell = string | number | null | { invalid: string }
const textCell = (input: string): Cell => input.trim() || null
const numberCell = (input: string): Cell => {
  const parsed = parseNumber(input)
  return parsed === undefined ? { invalid: input.trim() } : parsed
}
const isInvalid = (cell: Cell): cell is { invalid: string } => typeof cell === 'object' && cell !== null

const cellsOf = (row: LineRow): Record<LineName, Cell> => ({
  description: textCell(row.description),
  quantity: numberCell(row.quantity),
  unit_price: numberCell(row.unit_price),
  amount: numberCell(row.amount),
})
const isEmptyRow = (cells: Record<LineName, Cell>) => Object.values(cells).every((cell) => cell === null)
/** The line rows that mean something: a row left completely blank is dropped (as the Streamlit page does). */
const meaningfulRows = (form: FormValues) => form.line_items.map(cellsOf).filter((cells) => !isEmptyRow(cells))

export interface FormChanges {
  /** What to PATCH: only the changed fields (empty when nothing changed). */
  patch: DocumentPatch
  /** Problems the user must fix before saving, by field name ("line_items" for the table). */
  errors: Record<string, string>
  /** True when the form differs from the saved data. */
  changed: boolean
}

export function diffForm(current: FormValues, saved: FormValues): FormChanges {
  const patch: Record<string, unknown> = {}
  const errors: Record<string, string> = {}
  let changed = false

  const compare = (name: string, label: string, now: Cell, before: Cell) => {
    if (JSON.stringify(now) === JSON.stringify(before)) return
    changed = true
    if (isInvalid(now)) errors[name] = `${label} must be a number (you typed "${now.invalid}").`
    else patch[name] = now
  }

  compare('doc_type', 'Document type', textCell(current.doc_type), textCell(saved.doc_type))
  for (const { name, label } of TEXT_FIELDS) compare(name, label, textCell(current[name]), textCell(saved[name]))
  for (const { name, label } of MONEY_FIELDS) compare(name, label, numberCell(current[name]), numberCell(saved[name]))

  const rows = meaningfulRows(current)
  if (JSON.stringify(rows) !== JSON.stringify(meaningfulRows(saved))) {
    changed = true
    const bad = rows.flatMap((cells, index) => LINE_COLUMNS.filter((c) => isInvalid(cells[c.name])).map((c) => `line ${index + 1} ${c.label.toLowerCase()}`))
    if (bad.length > 0) errors.line_items = `Line items must be numbers where numbers are expected (${bad.join(', ')}).`
    else patch.line_items = rows
  }
  return { patch: patch as DocumentPatch, errors, changed }
}
