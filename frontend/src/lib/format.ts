/** Display formatting only (no business logic). Missing values show as an em dash. */

const EMPTY = '—'
const MONEY_LOCALE = 'en-US'
const DATE_LOCALE = 'en-GB' // day first and a month name, so 03/04 can never be misread

/** 1234.5, "USD" -> "$1,234.50". An unknown currency code still shows the number: "1,234.50 XYZ". */
export function formatMoney(amount: number | null | undefined, currency?: string | null): string {
  if (amount === null || amount === undefined) return EMPTY
  if (currency) {
    try {
      return new Intl.NumberFormat(MONEY_LOCALE, { style: 'currency', currency }).format(amount)
    } catch {
      // Intl throws RangeError for something that is not an ISO 4217 code (the extraction can be wrong); fall through.
    }
  }
  const number = new Intl.NumberFormat(MONEY_LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount)
  return currency ? `${number} ${currency}` : number
}

/** "2024-03-10" -> "10 Mar 2024". Not a real date (the validator reports those) -> shown as it is. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return EMPTY
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) return value
  const [, year, month, day] = match
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)))
  if (date.getUTCFullYear() !== Number(year) || date.getUTCMonth() !== Number(month) - 1 || date.getUTCDate() !== Number(day)) return value
  return new Intl.DateTimeFormat(DATE_LOCALE, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(date)
}

/** A timestamp from the API in the viewer's own time zone: "17 Nov 2026, 18:32".
 *  The backend sends UTC with a "Z". As a safety net (an older backend, or a hand-made value), a timestamp without an offset is also read as UTC, not as local time. */
export function formatDateTime(value: string | null | undefined, timeZone?: string): string {
  if (!value) return EMPTY
  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)
  const date = new Date(hasOffset ? value : `${value}Z`)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(DATE_LOCALE, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone }).format(date)
}
