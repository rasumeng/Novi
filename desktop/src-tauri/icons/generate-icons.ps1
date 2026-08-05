Add-Type -AssemblyName System.Drawing

$out = $PSScriptRoot

function New-CozmoPng {
    param([int]$Size, [string]$Path)
    $bmp = New-Object System.Drawing.Bitmap $Size, $Size
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'
    $bg = [System.Drawing.Color]::FromArgb(10, 14, 28)
    $g.Clear($bg)
    $gold = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(247, 205, 91))
    $c = $Size / 2.0
    $outer = $Size * 0.42
    $inner = $Size * 0.16
    $ir = $inner * 0.7071
    $r1 = $outer * 0.7071
    $pts = @(
        (New-Object System.Drawing.PointF ($c), ($c - $outer)),
        (New-Object System.Drawing.PointF ($c + $ir), ($c - $ir)),
        (New-Object System.Drawing.PointF ($c + $r1), ($c - $r1)),
        (New-Object System.Drawing.PointF ($c + $outer), ($c)),
        (New-Object System.Drawing.PointF ($c + $r1), ($c + $r1)),
        (New-Object System.Drawing.PointF ($c + $ir), ($c + $ir)),
        (New-Object System.Drawing.PointF ($c), ($c + $outer)),
        (New-Object System.Drawing.PointF ($c - $ir), ($c + $ir)),
        (New-Object System.Drawing.PointF ($c - $r1), ($c + $r1)),
        (New-Object System.Drawing.PointF ($c - $outer), ($c)),
        (New-Object System.Drawing.PointF ($c - $r1), ($c - $r1)),
        (New-Object System.Drawing.PointF ($c - $ir), ($c - $ir))
    )
    $g.FillPolygon($gold, $pts)
    $g.Dispose()
    $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    Write-Host "generated $Path"
}

New-CozmoPng 32  "$out\32x32.png"
New-CozmoPng 128 "$out\128x128.png"
New-CozmoPng 256 "$out\128x128@2x.png"
New-CozmoPng 256 "$out\icon.png"

$pngBytes = [System.IO.File]::ReadAllBytes("$out\icon.png")
$fs = [System.IO.File]::Create("$out\icon.ico")
$bw = New-Object System.IO.BinaryWriter($fs)
$bw.Write([uint16]0)
$bw.Write([uint16]1)
$bw.Write([uint16]1)
$bw.Write([byte]0)
$bw.Write([byte]0)
$bw.Write([byte]0)
$bw.Write([byte]0)
$bw.Write([uint16]1)
$bw.Write([uint16]32)
$bw.Write([uint32]$pngBytes.Length)
$bw.Write([uint32]22)
$bw.Write($pngBytes)
$bw.Close()
$fs.Close()
Write-Host "generated $out\icon.ico"