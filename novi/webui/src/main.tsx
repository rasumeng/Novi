import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { ToastProvider } from './hooks/useToast'
import { NotificationCenterProvider } from './hooks/useNotificationCenter'
import './styles/globals.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ToastProvider>
      <NotificationCenterProvider>
        <App />
      </NotificationCenterProvider>
    </ToastProvider>
  </React.StrictMode>,
)
