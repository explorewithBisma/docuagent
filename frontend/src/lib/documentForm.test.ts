import { describe, expect, it } from 'vitest'
import type { components } from '../api/schema'
import { diffForm, emptyRow, fieldLabel, parseNumber, toForm } from './documentForm'

type Invoice = components['schemas']['Invoice']

const DATA: Invoice = {
  doc_type: 'invoice',
  vendor_name: 'Acme',
  invoice_number: 'A-1',
  invoice_date: '2024-03-01',
  currency: 'USD',
  subtotal: 25,
  tax: 2.5,
  total: 27.5,
  line_items: [
    { description: 'x', quantity: 2, unit_price: 10, amount: 20 },
    { description: 'y', quantity: 1, unit_price: 5, amount: 5 },
  ],
}

describe('parseNumber', () => {
  it('reads plain numbers, commas and spaces', () => {
    expect(parseNumber('560')).toBe(560)
    expect(parseNumber(' 1,234.50 ')).toBe(1234.5)
    expect(parseNumber('-3')).toBe(-3)
    expect(parseNumber('.5')).toBe(0.5)
    expect(parseNumber('0')).toBe(0) // zero is a value, not "empty"
  })
  it('treats nothing as null and anything else as invalid (undefined)', () => {
    expect(parseNumber('')).toBeNull()
    expect(parseNumber('   ')).toBeNull()
    for (const bad of ['abc', '12abc', '1.2.3', 'NaN', 'Infinity', '1e3', '--1']) expect(parseNumber(bad)).toBeUndefined()
  })
})

describe('toForm', () => {
  it('turns the data into strings, with missing values as empty strings', () => {
    const form = toForm(DATA)
    expect(form).toMatchObject({ doc_type: 'invoice', vendor_name: 'Acme', vendor_tax_id: '', due_date: '', subtotal: '25', tax: '2.5', total: '27.5' })
    expect(form.line_items.map(({ key: _key, ...row }) => row)).toEqual([
      { description: 'x', quantity: '2', unit_price: '10', amount: '20' },
      { description: 'y', quantity: '1', unit_price: '5', amount: '5' },
    ])
  })
  it('gives every row its own key', () => {
    const keys = [...toForm(DATA).line_items, emptyRow()].map((row) => row.key)
    expect(new Set(keys).size).toBe(keys.length)
  })
})

describe('diffForm', () => {
  it('reports no change for an untouched form', () => {
    expect(diffForm(toForm(DATA), toForm(DATA))).toEqual({ patch: {}, errors: {}, changed: false })
  })

  it('is not fooled by spaces, trailing zeros, or a blank extra row', () => {
    const form = toForm(DATA)
    form.vendor_name = '  Acme  '
    form.total = '27.50'
    form.vendor_tax_id = '   '
    form.line_items = [...form.line_items, emptyRow()]
    expect(diffForm(form, toForm(DATA)).changed).toBe(false)
  })

  it('puts only the changed fields in the patch', () => {
    const form = toForm(DATA)
    form.invoice_number = 'A-2'
    form.total = '30'
    expect(diffForm(form, toForm(DATA))).toEqual({ patch: { invoice_number: 'A-2', total: 30 }, errors: {}, changed: true })
  })

  it('sends a cleared field as null, and a trimmed value without its spaces', () => {
    const form = toForm(DATA)
    form.vendor_name = ''
    form.currency = ' eur '
    form.doc_type = ''
    expect(diffForm(form, toForm(DATA)).patch).toEqual({ vendor_name: null, currency: 'eur', doc_type: null })
  })

  it('sends the whole line list when one cell changes, with blanks as null and empty rows dropped', () => {
    const form = toForm(DATA)
    form.line_items[1]!.amount = '6'
    form.line_items = [...form.line_items, { ...emptyRow(), description: 'z' }, emptyRow()]
    expect(diffForm(form, toForm(DATA)).patch).toEqual({
      line_items: [
        { description: 'x', quantity: 2, unit_price: 10, amount: 20 },
        { description: 'y', quantity: 1, unit_price: 5, amount: 6 },
        { description: 'z', quantity: null, unit_price: null, amount: null },
      ],
    })
  })

  it('a removed row is a change', () => {
    const form = toForm(DATA)
    form.line_items = form.line_items.slice(1)
    expect(diffForm(form, toForm(DATA)).patch).toEqual({ line_items: [{ description: 'y', quantity: 1, unit_price: 5, amount: 5 }] })
  })

  it('reports a number that is not a number as an error, and does not put it in the patch', () => {
    const form = toForm(DATA)
    form.total = 'lots'
    form.line_items[0]!.quantity = 'two'
    const result = diffForm(form, toForm(DATA))
    expect(result.changed).toBe(true)
    expect(result.patch).toEqual({})
    expect(result.errors.total).toBe('Total must be a number (you typed "lots").')
    expect(result.errors.line_items).toContain('line 1 quantity')
  })
})

describe('fieldLabel', () => {
  it('names the backend fields in words', () => {
    expect(fieldLabel('vendor_name')).toBe('Vendor name')
    expect(fieldLabel('line_items')).toBe('Line items')
    expect(fieldLabel('overall_confidence')).toBe('Overall confidence')
    expect(fieldLabel('some_new_field')).toBe('Some new field') // a field the front end does not know yet is still readable
  })
})
