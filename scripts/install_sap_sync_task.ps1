<#
.SYNOPSIS
    Registers the nightly SAP -> Postgres materials sync as a Windows scheduled task.

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
    Account to run as. It needs two things: read access to the SAP company
    database through Windows auth (the sync connects as this identity), and write
    access to the store Postgres. Administrator has both.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_sap_sync_task.ps1
#>
[CmdletBinding()]
param(
    [string]$At = "02:30",
    [string]$User = "Administrator",
    [string]$TaskName = "EB Dental - SAP materials sync",
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

# Password prompt rather than a stored credential in this file. Highest run level
# because the task reads the SAP database through the account's Windows identity.
$credential = Get-Credential -UserName $User -Message "Password for $User (the account the sync runs as)"

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -User $credential.UserName `
    -Password $credential.GetNetworkCredential().Password `
    -RunLevel Highest `
    -Description "Syncs the SAP Business One materials catalogue into the store Postgres. See store-api/scripts/sap_sync.py." `
    -Force | Out-Null

Write-Host "Registered '$TaskName' - daily at $At as $($credential.UserName)."
Write-Host ""
Write-Host "Run it once now to check it works:"
Write-Host "    Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "    Get-ScheduledTaskInfo -TaskName '$TaskName' | Select LastRunTime, LastTaskResult"
Write-Host ""
Write-Host "LastTaskResult 0 = success. The run's own output is in E:\Website\logs\sap_sync_<date>.log."
Write-Host "To remove: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
