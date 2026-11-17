/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the FastAPI backend is. Default http://localhost:8000 (see .env.example). */
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
