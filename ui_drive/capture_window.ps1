<#
.SYNOPSIS
    Captures one window, whole (including its title bar), to a PNG file.

.DESCRIPTION
    Uses PrintWindow with the PW_RENDERFULLCONTENT flag (2), which is what
    lets this grab a window that is not on top, minimized-but-restorable,
    or drawn by GPU-accelerated content such as QtWebEngine. Runs with
    per-monitor DPI awareness set on this process first, so the window's
    reported size and the bitmap it captures agree on any monitor scaling.

    Not meant to be run by hand day to day: drive.py calls this with the
    window handle it just found and the path to save to.

.PARAMETER Hwnd
    The window handle (as a plain integer) to capture.

.PARAMETER OutPath
    Where to save the PNG.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File capture_window.ps1 -Hwnd 132456 -OutPath C:\temp\shot.png
#>
param(
    [Parameter(Mandatory = $true)][long]$Hwnd,
    [Parameter(Mandatory = $true)][string]$OutPath
)

$ErrorActionPreference = "Stop"

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace UiDrive {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    public static class Native {
        [DllImport("user32.dll")]
        public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr dpiContext);

        [DllImport("user32.dll")]
        public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

        [DllImport("user32.dll")]
        public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdcBlt, uint nFlags);
    }
}
'@

# -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2. Without this, GetWindowRect
# on a high-DPI monitor can return a size PrintWindow does not agree with,
# and the bitmap ends up cropped or blank in a corner.
[UiDrive.Native]::SetThreadDpiAwarenessContext([IntPtr](-4)) | Out-Null

$hwnd = [IntPtr]$Hwnd
$rect = New-Object UiDrive.RECT
if (-not [UiDrive.Native]::GetWindowRect($hwnd, [ref]$rect)) {
    Write-Error "GetWindowRect failed for handle $Hwnd"
    exit 1
}

$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -le 0 -or $height -le 0) {
    Write-Error "window has no visible size ($width x $height)"
    exit 1
}

Add-Type -AssemblyName System.Drawing

$bitmap = New-Object System.Drawing.Bitmap($width, $height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$hdc = $graphics.GetHdc()
try {
    $PW_RENDERFULLCONTENT = 2
    $ok = [UiDrive.Native]::PrintWindow($hwnd, $hdc, $PW_RENDERFULLCONTENT)
} finally {
    $graphics.ReleaseHdc($hdc)
}

if (-not $ok) {
    Write-Error "PrintWindow failed for handle $Hwnd"
    exit 1
}

$bitmap.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()
Write-Output "wrote $OutPath ($width x $height)"
