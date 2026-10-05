import React from 'react'
import ReactDOM from 'react-dom/client'
import { MotionConfig } from 'framer-motion'
import App from './App'
import { ToastProvider } from './hooks/useToast'
import { NotificationCenterProvider } from './hooks/useNotificationCenter'
import './styles/globals.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <MotionConfig reducedMotion="user">
      <ToastProvider>
        <NotificationCenterProvider>
          <App />
        </NotificationCenterProvider>
      </ToastProvider>
    </MotionConfig>
  </React.StrictMode>,
)
