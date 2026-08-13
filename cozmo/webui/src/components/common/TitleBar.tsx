import { WindowControls } from './WindowControls'

export function TitleBar() {
  return (
    <div className="flex h-auto w-full shrink-0 select-none border-b border-base-800 bg-base-950">
      {/* Drag region / Cozmo branding */}
      <div
        data-tauri-drag-region
        className="flex flex-1 items-center px-4"
      >
        <div className="flex items-center gap-2">
          <div className="flex h-5 w-5 items-center justify-center rounded-md bg-base-800 text-xs">
            ✦
          </div>

          <span className="text-sm font-medium text-base-100">
            Cozmo
          </span>
        </div>
      </div>

      <WindowControls />
    </div>
  )
}