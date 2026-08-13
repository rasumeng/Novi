import { useEffect, useState } from 'react'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { platform } from '@tauri-apps/plugin-os'

import {
  CloseIcon,
  MacCloseIcon,
  MacMaximizeIcon,
  MacMinimizeIcon,
  MaximizeIcon,
  MinimizeIcon,
  RestoreIcon,
} from './WindowIcons'

export function WindowControls() {
  const appWindow = getCurrentWindow()

  const [isMac, setIsMac] = useState(false)
  const [isMaximized, setIsMaximized] = useState(false)

  useEffect(() => {
    setIsMac(platform() === 'macos')
  }, [])

  useEffect(() => {
    const updateMaximizedState = async () => {
      setIsMaximized(await appWindow.isMaximized())
    }

    updateMaximizedState()

    let unlisten: (() => void) | undefined

    appWindow.onResized(() => {
      updateMaximizedState()
    }).then((cleanup) => {
      unlisten = cleanup
    })

    return () => {
      unlisten?.()
    }
  }, [appWindow])

  if (isMac) {
    return (
      <div className="flex h-full shrink-0 items-center gap-2 px-3">
        {/* Close */}
        <button
          type="button"
          onClick={() => appWindow.close()}
          className="group flex h-3 w-3 items-center justify-center rounded-full bg-red-500"
          aria-label="Close"
          title="Close"
        >
          <span className="text-transparent group-hover:text-red-950">
            <MacCloseIcon />
          </span>
        </button>

        {/* Minimize */}
        <button
          type="button"
          onClick={() => appWindow.minimize()}
          className="group flex h-3 w-3 items-center justify-center rounded-full bg-yellow-500"
          aria-label="Minimize"
          title="Minimize"
        >
          <span className="text-transparent group-hover:text-yellow-950">
            <MacMinimizeIcon />
          </span>
        </button>

        {/* Maximize / Restore */}
        <button
          type="button"
          onClick={() => appWindow.toggleMaximize()}
          className="group flex h-3 w-3 items-center justify-center rounded-full bg-green-500"
          aria-label={isMaximized ? 'Restore' : 'Maximize'}
          title={isMaximized ? 'Restore' : 'Maximize'}
        >
          <span className="text-transparent group-hover:text-green-950">
            <MacMaximizeIcon />
          </span>
        </button>
      </div>
    )
  }

  return (
    <div className="flex h-full shrink-0">
      {/* Minimize */}
      <button
        type="button"
        onClick={() => appWindow.minimize()}
        className="flex h-full w-12 items-center justify-center text-base-400 transition-colors hover:bg-base-800 hover:text-base-100"
        aria-label="Minimize"
        title="Minimize"
      >
        <MinimizeIcon />
      </button>

      {/* Maximize / Restore */}
      <button
        type="button"
        onClick={() => appWindow.toggleMaximize()}
        className="flex h-full w-12 items-center justify-center text-base-400 transition-colors hover:bg-base-800 hover:text-base-100"
        aria-label={isMaximized ? 'Restore' : 'Maximize'}
        title={isMaximized ? 'Restore' : 'Maximize'}
      >
        {isMaximized ? <RestoreIcon /> : <MaximizeIcon />}
      </button>

      {/* Close */}
      <button
        type="button"
        onClick={() => appWindow.close()}
        className="flex h-full w-12 items-center justify-center text-base-400 transition-colors hover:bg-red-600 hover:text-white"
        aria-label="Close"
        title="Close"
      >
        <CloseIcon />
      </button>
    </div>
  )
}