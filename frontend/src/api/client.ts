/** A small typed fetch wrapper. Paths, parameters, bodies and responses are all derived from the generated schema.d.ts (npm run gen:types),
 *  so a wrong path or a wrong body is a compile error. No business logic here: this only talks to the FastAPI backend. */

import type { paths } from './schema'

export const API_URL: string = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/+$/, '')

type Method = 'get' | 'post' | 'patch'
type PathsFor<M extends Method> = { [P in keyof paths]: paths[P] extends Record<M, object> ? P : never }[keyof paths]
type OperationOf<P extends keyof paths, M extends Method> = paths[P][M]

/** The JSON body of the 200/201 response. */
export type ResponseOf<P extends keyof paths, M extends Method> =
  OperationOf<P, M> extends { responses: infer R }
    ? R[Extract<keyof R, 200 | 201>] extends { content: { 'application/json': infer J } }
      ? J
      : never
    : never

type JsonBodyOf<P extends keyof paths, M extends Method> =
  OperationOf<P, M> extends { requestBody: { content: { 'application/json': infer B } } } ? B : never
type PathParamsOf<P extends keyof paths, M extends Method> =
  OperationOf<P, M> extends { parameters: { path: infer PP } } ? PP : never
type QueryOf<P extends keyof paths, M extends Method> =
  OperationOf<P, M> extends { parameters: { query?: infer Q } } ? Q : never

export interface RequestOptions<P extends keyof paths, M extends Method> {
  /** Values for {placeholders} in the path, e.g. { document_id: 7 }. */
  params?: PathParamsOf<P, M>
  query?: QueryOf<P, M>
  /** A JSON body (typed from the schema) or a FormData for file uploads. */
  body?: JsonBodyOf<P, M> | FormData
  signal?: AbortSignal
}

/** What went wrong, in words a user can read. `status` is 0 when the backend could not be reached at all. */
export class ApiError extends Error {
  readonly status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** Turns the backend's error body (a string, {message, errors}, or a list of validation errors) into one readable message. */
export function errorText(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((e: { loc?: unknown[]; msg?: string }) => `${(e.loc ?? []).join('.')}: ${e.msg ?? ''}`).join('; ')
  }
  if (detail && typeof detail === 'object') {
    const { message, errors } = detail as { message?: string; errors?: string[] }
    return [message ?? fallback, ...(errors ?? []).map((line) => `- ${line}`)].join('\n')
  }
  return fallback
}

function buildUrl(path: string, params?: object, query?: object): string {
  const filled = path.replace(/\{(\w+)\}/g, (_, name: string) => encodeURIComponent(String((params as Record<string, unknown> | undefined)?.[name] ?? '')))
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query ?? {})) if (value !== undefined && value !== null) search.set(key, String(value))
  const text = search.toString()
  return `${API_URL}${filled}${text ? `?${text}` : ''}`
}

async function request<P extends PathsFor<M>, M extends Method>(method: M, path: P, options: RequestOptions<P, M> = {}): Promise<ResponseOf<P, M>> {
  const isForm = options.body instanceof FormData
  let response: Response
  try {
    // (the conditional types are unresolved inside this generic function, so they are cast here; the public signatures stay fully typed)
    response = await fetch(buildUrl(path, options.params as object | undefined, options.query as object | undefined), {
      method: method.toUpperCase(),
      headers: options.body === undefined || isForm ? undefined : { 'Content-Type': 'application/json' },
      body: options.body === undefined ? undefined : isForm ? (options.body as FormData) : JSON.stringify(options.body),
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(`Could not reach the backend at ${API_URL}. Is it running? (uvicorn backend.main:app --reload --timeout-graceful-shutdown 2) Does its CORS_ORIGINS include ${window.location.origin}?`, 0)
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => undefined)
    const detail = body && typeof body === 'object' && 'detail' in body ? (body as { detail: unknown }).detail : undefined
    throw new ApiError(errorText(detail, `The backend answered HTTP ${response.status}.`), response.status)
  }
  return (await response.json()) as ResponseOf<P, M>
}

export const apiGet = <P extends PathsFor<'get'>>(path: P, options?: RequestOptions<P, 'get'>) => request('get', path, options)
export const apiPost = <P extends PathsFor<'post'>>(path: P, options?: RequestOptions<P, 'post'>) => request('post', path, options)
export const apiPatch = <P extends PathsFor<'patch'>>(path: P, options?: RequestOptions<P, 'patch'>) => request('patch', path, options)
