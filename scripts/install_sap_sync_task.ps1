<#
.SYNOPSIS
    Registers the nightly SAP -> Postgres catalogue sync as a Windows scheduled task.
    Covers both catalogues: materials (SAP groups 101 + 106) and spare parts (103).

.DESCRIPTION
    Run ON QPLUS365SERVER, elevated. Creates (or replaces) a task that runs
    scripts/sap_sync_scheduled.cmd once a night.

    Why a scheduled task rather than an NSSM service like ebdental-api/ebdental-web:
    those two are long-running processes that should always be up. This is a job
    that runs, finishes and exits - a service wrapper around it would either
    restart it in a loop or sit "stopped" looking like a fault.

    Why nightly rather than hourly: SAP's item master changes when someone edits
    it, which is a handful of times a week, and every run rewrites ~8,000 rows and
    files change-log entries for whatever moved. Hourly would multiply that noise
    twenty-four-fold to catch a price change a few hours sooner. Move it up if
    pricing starts changing during the day - the schedule is one line below.

.PARAMETER At
    Time of day to run. Default 02:30, chosen to sit after SAP's own overnight
    activity and well before anyone opens the storefront.

.PARAMETER User
    Account to run as. Defaults to SYSTEM, which needs no password and already has
    everything a run touches on this machine: E:\Website, and the store Postgres
    (which authenticates by password over TCP, not by Windows identity).

    SYSTEM is only enough because SAP_DB_USER/SAP_DB_PASSWORD in store-api\.env give
    the sync a SQL login of its own. Without those, the sync reaches SAP through the
    task account's Windows identity - and SYSTEM is not a user of the SAP company
    database, so a run would fail on a login error. In that case pass an account SQL
    Server knows (-User Administrator) and this will prompt for its password.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_sap_sync_task.ps1
#>
[CmdletBinding()]
param(
    [string]$At = "02:30",
    [string]$User = "SYSTEM",
    [string]$TaskName = "EB Dental - SAP catalogue sync",
    [string]$Script = "E:\Website\store-api\scripts\sap_sync_scheduled.cmd"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Script)) {
    throw "Not found: $Script - deploy the store-api code to the server first."
}

$action  = New-ScheduledTaskAction -Execute $Script
$trigger = New-ScheduledTaskTrigger -Daily -At $At

# StartWhenAvailable: if the box is off or busy at 02:30, run at the next
# opportunity instead of skipping the night entirely and leaving the catalogue a
# day staler than anyone realises.
# ExecutionTimeLimit: a sync that has hung on a database connection should be
# killed rather than still be holding it when tomorrow's run starts.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

$description = "Syncs the SAP Business One materials and spare-parts catalogues into the store Postgres. See store-api/scripts/sap_sync.py."

# The built-in service accounts have no password to ask for - they are registered by
# name with LogonType ServiceAccount. Anything else is a real user, whose password is
# prompted for rather than stored in this file, and which is what you need if the sync
# is still reaching SAP through Windows authentication (see .PARAMETER User).
$builtin = @("SYSTEM", "LOCAL SERVICE", "NETWORK SERVICE")
if ($builtin -contains $User.ToUpper().Replace("NT AUTHORITY\", "")) {
    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description $description `
        -Force | Out-Null
    $ranAs = $User
} else {
    $credential = Get-Credential -UserName $User -Message "Password for $User (the account the sync runs as)"
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -User $credential.UserName `
        -Password $credential.GetNetworkCredential().Password `
        -RunLevel Highest `
        -Description $description `
        -Force | Out-Null
    $ranAs = $credential.UserName
}

Write-Host "Registered '$TaskName' - daily at $At as $ranAs."
Write-Host ""
Write-Host "Run it once now to check it works:"
Write-Host "    Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "    Get-ScheduledTaskInfo -TaskName '$TaskName' | Select LastRunTime, LastTaskResult"
Write-Host ""
Write-Host "LastTaskResult 0 = success. The run's own output is in E:\Website\logs\sap_sync_<date>.log."
Write-Host "To remove: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
