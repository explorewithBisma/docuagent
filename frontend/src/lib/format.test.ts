import { describe, expect, it } from 'vitest'
import { formatDate, formatDateTime, formatMoney } from './format'

describe('formatMoney', () => {
  it('formats with the currency', () => {
    expect(formatMoney(1234.5, 'USD')).toBe('$1,234.50')
    expect(formatMoney(560, 'EUR')).toBe('€560.00')
    expect(formatMoney(1234.5, 'AED')).toBe('AED 1,234.50') // Intl uses a non-breaking space, so the amount never wraps onto two lines
    expect(formatMoney(0, 'USD')).toBe('$0.00') // zero is a real amount, not "missing"
  })
  it('keeps the number when the currency code is not valid', () => {
    expect(formatMoney(10.5, 'XYZ1')).toBe('10.50 XYZ1')
    expect(formatMoney(10.5, 'dollars')).toBe('10.50 dollars')
  })
  it('shows the number alone without a currency, and a dash when there is no amount', () => {
    expect(formatMoney(10.5, null)).toBe('10.50')
    expect(formatMoney(null, 'USD')).toBe('—')
    expect(formatMoney(undefined)).toBe('—')
  })
})

describe('formatDate', () => {
  it('writes the month as a name so day and month cannot be confused', () => {
    expect(formatDate('2024-03-10')).toBe('10 Mar 2024')
    expect(formatDate('2024-12-01')).toBe('1 Dec 2024')
  })
  it('does not shift the day with the time zone', () => {
    expect(formatDate('2024-01-01')).toBe('1 Jan 2024')
  })
  it('shows a dash for nothing, and shows anything that is not a real date as it is', () => {
    expect(formatDate(null)).toBe('—')
    expect(formatDate('')).toBe('—')
    expect(formatDate('2024-02-30')).toBe('2024-02-30') // the backend's validator reports it; the list must not hide it
    expect(formatDate('10/03/2024')).toBe('10/03/2024')
  })
})

describe('formatDateTime', () => {
  it('reads a timestamp without an offset as UTC (SQLite drops the Z)', () => {
    expect(formatDateTime('2026-11-17T18:32:43.779644', 'UTC')).toBe('17 Nov 2026, 18:32')
    expect(formatDateTime('2026-11-17T18:32:43.779644', 'Asia/Dubai')).toBe('17 Nov 2026, 22:32') // UTC+4, so it is shown in the viewer's own time
  })
  it('respects an explicit offset or Z', () => {
    expect(formatDateTime('2026-11-17T18:32:43Z', 'UTC')).toBe('17 Nov 2026, 18:32')
    expect(formatDateTime('2026-11-17T18:32:43+05:00', 'UTC')).toBe('17 Nov 2026, 13:32')
  })
  it('shows midnight as 00:xx, never 24:xx', () => {
    expect(formatDateTime('2026-11-18T00:05:00Z', 'UTC')).toBe('18 Nov 2026, 00:05')
  })
  it('shows a dash for nothing and the raw text for garbage', () => {
    expect(formatDateTime(null)).toBe('—')
    expect(formatDateTime('not a date')).toBe('not a date')
  })
})
