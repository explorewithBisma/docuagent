import { useId } from 'react'
import { DOC_TYPES, LINE_COLUMNS, MONEY_FIELDS, TEXT_FIELDS, emptyRow, type FormValues, type LineRow } from '../../lib/documentForm'

export type Marks = Record<string, 'error' | 'warning'>

interface Props {
  form: FormValues
  onChange: (next: FormValues) => void
  /** Problems with what was typed (a number that is not a number), by field name. */
  errors: Record<string, string>
  /** Fields the backend's checks flagged, by field name: shown with a coloured border. */
  marks: Marks
  disabled: boolean
}

const BASE = 'mt-1 block w-full rounded border bg-white px-2 py-1 text-slate-900 disabled:bg-slate-100'
function inputClass(name: string, marks: Marks, errors: Record<string, string>): string {
  if (errors[name] || marks[name] === 'error') return `${BASE} border-red-400 bg-red-50`
  if (marks[name] === 'warning') return `${BASE} border-amber-400 bg-amber-50`
  return `${BASE} border-slate-300`
}

function Field({ label, hint, name, marks, errors, children }: { label: string; hint?: string; name: string; marks: Marks; errors: Record<string, string>; children: React.ReactNode }) {
  const mark = errors[name] || marks[name] === 'error' ? ' ❌' : marks[name] === 'warning' ? ' ⚠' : ''
  return (
    <label className="block text-sm text-slate-700">
      <span>
        {label}
        {hint && <span className="text-slate-400"> ({hint})</span>}
        <span aria-hidden="true">{mark}</span>
      </span>
      {children}
      {errors[name] && (
        <span role="alert" className="mt-1 block text-xs text-red-700">
          {errors[name]}
        </span>
      )}
    </label>
  )
}

export default function DocumentForm({ form, onChange, errors, marks, disabled }: Props) {
  const set = <K extends keyof FormValues>(name: K, value: FormValues[K]) => onChange({ ...form, [name]: value })
  const setRow = (key: number, name: keyof LineRow, value: string) =>
    set('line_items', form.line_items.map((row) => (row.key === key ? { ...row, [name]: value } : row)))
  const tableId = useId()

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Document type" name="doc_type" marks={marks} errors={errors}>
          <select value={form.doc_type} onChange={(event) => set('doc_type', event.target.value)} disabled={disabled} className={inputClass('doc_type', marks, errors)}>
            <option value="">Not sure</option>
            {DOC_TYPES.map((type) => (
              <option key={type} value={type}>
                {type[0]?.toUpperCase()}
                {type.slice(1)}
              </option>
            ))}
          </select>
        </Field>
        {TEXT_FIELDS.map(({ name, label, hint }) => (
          <Field key={name} label={label} hint={hint} name={name} marks={marks} errors={errors}>
            <input type="text" value={form[name]} onChange={(event) => set(name, event.target.value)} disabled={disabled} aria-invalid={Boolean(errors[name]) || marks[name] === 'error'} className={inputClass(name, marks, errors)} />
          </Field>
        ))}
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        {MONEY_FIELDS.map(({ name, label }) => (
          <Field key={name} label={label} name={name} marks={marks} errors={errors}>
            <input type="text" inputMode="decimal" value={form[name]} onChange={(event) => set(name, event.target.value)} disabled={disabled} aria-invalid={Boolean(errors[name]) || marks[name] === 'error'} className={`${inputClass(name, marks, errors)} text-right tabular-nums`} />
          </Field>
        ))}
      </div>

      <div>
        <h3 id={tableId} className="text-sm font-medium text-slate-700">
          Line items
          <span aria-hidden="true">{errors.line_items || marks.line_items === 'error' ? ' ❌' : marks.line_items === 'warning' ? ' ⚠' : ''}</span>
        </h3>
        {errors.line_items && (
          <p role="alert" className="mt-1 text-xs text-red-700">
            {errors.line_items}
          </p>
        )}
        {/* scrolls sideways on a narrow screen */}
        <div className="mt-1 overflow-x-auto rounded border border-slate-200 bg-white">
          <table aria-labelledby={tableId} className="min-w-full text-sm">
            <thead className="bg-slate-100 text-left text-xs text-slate-600 uppercase">
              <tr>
                {LINE_COLUMNS.map((column) => (
                  <th key={column.name} scope="col" className={`px-2 py-1 font-medium ${column.numeric ? 'text-right' : ''}`}>
                    {column.label}
                  </th>
                ))}
                <th scope="col" className="px-2 py-1">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {form.line_items.length === 0 && (
                <tr>
                  <td colSpan={LINE_COLUMNS.length + 1} className="px-2 py-3 text-center text-slate-500">
                    No line items.
                  </td>
                </tr>
              )}
              {form.line_items.map((row, index) => (
                <tr key={row.key} className="border-t border-slate-100">
                  {LINE_COLUMNS.map((column) => (
                    <td key={column.name} className="px-1 py-1">
                      <input
                        type="text"
                        inputMode={column.numeric ? 'decimal' : undefined}
                        aria-label={`Line ${index + 1} ${column.label.toLowerCase()}`}
                        value={row[column.name]}
                        onChange={(event) => setRow(row.key, column.name, event.target.value)}
                        disabled={disabled}
                        className={`w-full min-w-24 rounded border border-slate-300 px-2 py-1 disabled:bg-slate-100 ${column.numeric ? 'text-right tabular-nums' : 'min-w-48'}`}
                      />
                    </td>
                  ))}
                  <td className="px-1 py-1 text-right">
                    <button type="button" onClick={() => set('line_items', form.line_items.filter((other) => other.key !== row.key))} disabled={disabled} aria-label={`Remove line ${index + 1}`} className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100 disabled:opacity-50">
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <button type="button" onClick={() => set('line_items', [...form.line_items, emptyRow()])} disabled={disabled} className="mt-2 rounded border border-slate-300 bg-white px-3 py-1 text-sm hover:bg-slate-100 disabled:opacity-50">
          Add line
        </button>
      </div>
    </div>
  )
}
