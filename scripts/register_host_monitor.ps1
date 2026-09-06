# Register the Rust monitor binary as a windowless current-user scheduled task.
param(
    [Parameter(Mandatory = $true)]
    [string]$BinarySource,
    [Parameter(Mandatory = $true)]
    [string]$TokenSource,
    # 원격 PC 에서 단독 실행되므로 수신 URL 은 호출자가 반드시 넘긴다.
    [Parameter(Mandatory = $true)]
    [string]$TargetUrl,
    [string]$TaskName = "rpi-calendar-host-monitor",
    [string]$InstallDirectory = (Join-Path $env:USERPROFILE "rpi-schedule-monitor"),
    [int]$DisplayOrder = 0
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $BinarySource -PathType Leaf)) {
    throw "monitor binary not found: $BinarySource"
}
if (-not (Test-Path -LiteralPath $TokenSource -PathType Leaf)) {
    throw "monitor token not found: $TokenSource"
}

$appDir = [IO.Path]::GetFullPath($InstallDirectory)
$binaryPath = Join-Path $appDir "rpi-schedule-monitor.exe"
$tokenPath = Join-Path $appDir "monitor-token"
$logPath = Join-Path $appDir "monitor.log"
New-Item -ItemType Directory -Path $appDir -Force | Out-Null

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.State -eq "Running") {
        Stop-ScheduledTask -TaskName $TaskName
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        do {
            Start-Sleep -Milliseconds 200
            $state = (Get-ScheduledTask -TaskName $TaskName).State
        } while ($state -eq "Running" -and [DateTime]::UtcNow -lt $deadline)
        if ($state -eq "Running") {
            throw "scheduled task did not stop: $TaskName"
        }
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Copy-Item -LiteralPath $BinarySource -Destination $binaryPath -Force
Copy-Item -LiteralPath $TokenSource -Destination $tokenPath -Force

# 토큰은 지표 푸시 서명 키다. 상속을 끊고 이 계정만 접근하도록 ACL 을 좁힌다.
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$tokenAcl = Get-Acl -LiteralPath $tokenPath
$tokenAcl.SetAccessRuleProtection($true, $false)
$tokenAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
    $currentUser, "FullControl", "Allow"
)))
Set-Acl -LiteralPath $tokenPath -AclObject $tokenAcl

$arguments = @(
    "--target", ('"{0}"' -f $TargetUrl),
    "--token-file", ('"{0}"' -f $tokenPath),
    "--interval-seconds", "2",
    "--display-order", $DisplayOrder,
    "--log-file", ('"{0}"' -f $logPath)
) -join " "

$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction `
    -Execute $binaryPath `
    -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -Hidden `
    -MultipleInstances IgnoreNew `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable
$description = "Push system metrics to the Raspberry Pi calendar kiosk without a console window"
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType S4U -RunLevel Limited
$task = New-ScheduledTask -Action $action -Description $description -Principal $principal -Settings $settings -Trigger $trigger
$registrationMode = "S4U"
try {
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
} catch {
    # Standard users cannot register S4U tasks. The Rust executable uses the
    # Windows GUI subsystem, so Interactive mode still creates no console.
    $principal = New-ScheduledTaskPrincipal `
        -UserId $identity `
        -LogonType Interactive `
        -RunLevel Limited
    $task = New-ScheduledTask `
        -Action $action `
        -Description $description `
        -Principal $principal `
        -Settings $settings `
        -Trigger $trigger
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
    $registrationMode = "InteractiveGui"
}
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 1

$registered = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
[pscustomobject]@{
    TaskName = $registered.TaskName
    State = $registered.State
    Execute = $registered.Actions.Execute
    LogonType = $registered.Principal.LogonType
    RegistrationMode = $registrationMode
    Hidden = $registered.Settings.Hidden
    LastTaskResult = $info.LastTaskResult
    Binary = $binaryPath
    Token = $tokenPath
} | Format-List
