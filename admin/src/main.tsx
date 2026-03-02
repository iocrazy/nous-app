import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from '@arco-design/web-react'
import enUS from '@arco-design/web-react/es/locale/en-US'
import { reportError } from './api/endpoints/error-reporting'
import App from './App'
import './index.css'

// Global error handlers for frontend error reporting
window.addEventListener('error', (event) => {
  reportError({
    error_type: 'runtime',
    message: event.message,
    stack: event.error?.stack,
    url: window.location.href,
    metadata: {
      filename: event.filename,
      lineno: event.lineno,
      colno: event.colno,
    },
  })
})

window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason
  reportError({
    error_type: 'unhandled_rejection',
    message: reason instanceof Error ? reason.message : String(reason),
    stack: reason instanceof Error ? reason.stack : undefined,
    url: window.location.href,
  })
})

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={enUS}>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ConfigProvider>
  </React.StrictMode>,
)
