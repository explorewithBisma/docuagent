import type { DocumentDetail } from '../../api/documents'
import { formatDate, formatMoney } from '../../lib/format'

const EMPTY = '—'

/** A confirmed document: the saved values as plain text. No inputs and no buttons, because it can no longer be edited. */
export default function DocumentReadOnly({ data }: { data: DocumentDetail['data'] }) {
  const rows: [string, string][] = [
    ['Document type', data.doc_type ? data.doc_type[0]?.toUpperCase() + data.doc_type.slice(1) : EMPTY],
    ['Vendor name', data.vendor_name || EMPTY],
    ['Vendor tax ID', data.vendor_tax_id || EMPTY],
    ['Invoice number', data.invoice_number || EMPTY],
    ['Invoice date', formatDate(data.invoice_date)],
    ['Due date', formatDate(data.due_date)],
    ['Currency', data.currency || EMPTY],
    ['Subtotal', formatMoney(data.subtotal, data.currency)],
    ['Tax', formatMoney(data.tax, data.currency)],
    ['Total', formatMoney(data.total, data.currency)],
  ]
  const items = data.line_items ?? []
  return (
    <div className="space-y-5">
      <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs text-slate-500">{label}</dt>
            <dd className="text-sm text-slate-900">{value}</dd>
          </div>
        ))}
      </dl>
      <div>
        <h3 className="text-sm font-medium text-slate-700">Line items</h3>
        <div className="mt-1 overflow-x-auto rounded border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-100 text-left text-xs text-slate-600 uppercase">
              <tr>
                <th scope="col" className="px-3 py-1 font-medium">Description</th>
                <th scope="col" className="px-3 py-1 text-right font-medium">Quantity</th>
                <th scope="col" className="px-3 py-1 text-right font-medium">Unit price</th>
                <th scope="col" className="px-3 py-1 text-right font-medium">Amount</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-3 text-center text-slate-500">
                    No line items.
                  </td>
                </tr>
              )}
              {items.map((item, index) => (
                <tr key={index} className="border-t border-slate-100">
                  <td className="px-3 py-1">{item.description || EMPTY}</td>
                  <td className="px-3 py-1 text-right tabular-nums">{item.quantity ?? EMPTY}</td>
                  <td className="px-3 py-1 text-right tabular-nums">{formatMoney(item.unit_price, data.currency)}</td>
                  <td className="px-3 py-1 text-right tabular-nums">{formatMoney(item.amount, data.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
