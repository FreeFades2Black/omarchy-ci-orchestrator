# File: C:\Users\FreeF\agents\omarchy_watchdog.ps1
# Requires PowerShell 5.1+ or PowerShell Core (pwsh)

param(
    [string]$OmarchyHost = "omarchy",
    [int]$PollIntervalSeconds = 15
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Show-WindowsNotification {
    param(
        [string]$Title,
        [string]$Message
    )
    
    try {
        # Send native Windows balloon / toast alert
        $notify = New-Object System.Windows.Forms.NotifyIcon
        $notify.Icon = [System.Drawing.SystemIcons]::Error
        $notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Error
        $notify.BalloonTipTitle = $Title
        $notify.BalloonTipText = $Message
        $notify.Visible = $true
        $notify.ShowBalloonTip(10000)
        
        # Play system warning chime
        [System.Media.SystemSounds]::Hand.Play()
        
        Start-Sleep -Seconds 2
        $notify.Dispose()
    }
    catch {
        Write-Warning "Notification error: $_"
    }
}

Write-Host "[*] Starting Omarchy Remote Watchdog daemon polling $OmarchyHost every $PollIntervalSeconds s..."
$alertedUnits = @{}

while ($true) {
    try {
        # Check for failed user units on Omarchy
        $sshOutput = ssh -o BatchMode=yes -o ConnectTimeout=5 $OmarchyHost "systemctl --user --failed --plain --no-legend --no-pager" 2>$null
        $currentFailed = @()
        if ($sshOutput) {
            foreach ($line in ($sshOutput -split "`n")) {
                $trimmed = $line.Trim()
                if ($trimmed -match '^\S+\.service') {
                    $unitName = ($trimmed -split '\s+')[0]
                    $currentFailed += $unitName
                    
                    # Alert only once per failure until it recovers
                    if (-not $alertedUnits.ContainsKey($unitName)) {
                        Write-Host "[!] Detected failure: $unitName" -ForegroundColor Red
                        
                        # Grab the tail of the journal for this unit
                        $logSnippet = ssh -o BatchMode=yes -o ConnectTimeout=5 $OmarchyHost "journalctl --user-unit=$unitName -n 5 --no-pager" 2>$null
                        
                        $alertTitle = "Omarchy Alert: $unitName FAILED"
                        $alertBody = "Unit has crashed or failed.`n$logSnippet"
                        if ($alertBody.Length -gt 250) {
                            $alertBody = $alertBody.Substring(0, 247) + "..."
                        }
                        
                        Show-WindowsNotification -Title $alertTitle -Message $alertBody
                        $alertedUnits[$unitName] = [DateTime]::UtcNow
                    }
                }
            }
        }
        
        # Reset alerted units once they are cleared / recovered
        $cleared = @()
        foreach ($unit in $alertedUnits.Keys) {
            if ($unit -notin $currentFailed) {
                Write-Host "[+] Unit recovered or reset: $unit" -ForegroundColor Green
                $cleared += $unit
            }
        }
        foreach ($unit in $cleared) {
            $alertedUnits.Remove($unit)
        }
    }
    catch {
        Write-Host "[!] Watchdog loop error: $_" -ForegroundColor Yellow
    }

    Start-Sleep -Seconds $PollIntervalSeconds
}
