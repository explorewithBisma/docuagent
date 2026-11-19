import { describe, expect, it } from 'vitest'
import { errorText } from './client'

describe('errorText', () => {
  it('uses a plain string as it is', () => {
    expect(errorText('Document 9 was not found.', 'x')).toBe('Document 9 was not found.')
  })
  it('lists the extra lines of a {message, errors} answer', () => {
    expect(errorText({ message: 'Too long.', errors: ['page 6', 'page 7'] }, 'x')).toBe('Too long.\n- page 6\n- page 7')
  })
  it('lists the issues of a refused confirm', () => {
    const detail = { message: 'The document still has errors.', issues: [{ message: 'The total is missing.' }, { message: 'The date is invalid.' }] }
    expect(errorText(detail, 'x')).toBe('The document still has errors.\n- The total is missing.\n- The date is invalid.')
  })
  it('joins validation errors with their field', () => {
    expect(errorText([{ loc: ['query', 'status'], msg: "Input should be 'draft' or 'confirmed'" }], 'x')).toBe("query.status: Input should be 'draft' or 'confirmed'")
  })
  it('falls back when there is nothing to show', () => {
    expect(errorText(undefined, 'The backend answered HTTP 500.')).toBe('The backend answered HTTP 500.')
    expect(errorText({}, 'fallback')).toBe('fallback')
  })
})
